import asyncio
import datetime
import logging
from typing import Optional

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import commands, Config
from redbot.core.bot import Red

log = logging.getLogger("red.twitchschedule")

TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
TWITCH_API_BASE = "https://api.twitch.tv/helix"

CHECK_INTERVAL_MINUTES = 5
FORCE_POST_WEEKDAY = 0  # Monday (datetime.weekday(): Monday = 0)
FORCE_POST_HOUR_UTC = 4
MAX_SEGMENTS_SHOWN = 8
TWITCH_PURPLE = 0x9146FF
DEFAULT_EVENT_DURATION_SECONDS = 3600


class TwitchSchedule(commands.Cog):
    """
    Watch a Twitch channel's stream schedule and post it to a Discord channel
    as a formatted embed showing the current Monday to Sunday week.

    A repost only happens when the streamer makes a genuine edit (title,
    time, category, added/removed/canceled stream). The normal day to day
    rolling forward of the schedule (a stream airs and drops off the list)
    is not treated as an edit. If nothing changes all week, a fresh copy is
    still force-posted every Monday around 04:00 UTC so the channel does
    not go stale.

    Optionally, a Discord Scheduled Event is created for each stream in the
    current week and kept in sync with the Twitch schedule.
    """

    __version__ = "2.1.0"
    __author__ = "Custom"

    def __init__(self, bot: Red):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        self.config = Config.get_conf(self, identifier=471029385, force_registration=True)

        default_guild = {
            "twitch_username": None,
            "broadcaster_id": None,
            "display_name": None,
            "profile_image_url": None,
            "channel_id": None,
            "last_fingerprint": {},
            "last_forced_iso_week": None,
            "create_events": True,
            "event_ids": {},
        }
        self.config.register_guild(**default_guild)

        self._app_token: Optional[str] = None
        self._app_token_expiry: float = 0.0
        self._lock = asyncio.Lock()
        self._guild_locks: dict = {}

        self.schedule_check_loop.start()

    def cog_unload(self):
        self.schedule_check_loop.cancel()
        asyncio.create_task(self.session.close())

    def _get_guild_lock(self, guild_id: int) -> asyncio.Lock:
        lock = self._guild_locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._guild_locks[guild_id] = lock
        return lock

    # ---------------------------------------------------------------------
    # Twitch API helpers
    # ---------------------------------------------------------------------

    async def _get_credentials(self):
        tokens = await self.bot.get_shared_api_tokens("twitch")
        return tokens.get("client_id"), tokens.get("client_secret")

    async def _get_app_token(self) -> Optional[str]:
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        if self._app_token and now < self._app_token_expiry - 60:
            return self._app_token

        async with self._lock:
            now = datetime.datetime.now(datetime.timezone.utc).timestamp()
            if self._app_token and now < self._app_token_expiry - 60:
                return self._app_token

            client_id, client_secret = await self._get_credentials()
            if not client_id or not client_secret:
                return None

            params = {
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            }
            try:
                async with self.session.post(TWITCH_TOKEN_URL, params=params) as resp:
                    if resp.status != 200:
                        log.error("Failed to get Twitch app token: %s", await resp.text())
                        return None
                    data = await resp.json()
            except aiohttp.ClientError:
                log.exception("Network error while fetching Twitch app token")
                return None

            self._app_token = data.get("access_token")
            self._app_token_expiry = now + float(data.get("expires_in", 3600))
            return self._app_token

    async def _headers(self):
        token = await self._get_app_token()
        if not token:
            return None
        client_id, _ = await self._get_credentials()
        if not client_id:
            return None
        return {
            "Client-ID": client_id,
            "Authorization": f"Bearer {token}",
        }

    async def _get_broadcaster_info(self, username: str) -> Optional[dict]:
        headers = await self._headers()
        if not headers:
            return None
        params = {"login": username.lower()}
        try:
            async with self.session.get(
                f"{TWITCH_API_BASE}/users", headers=headers, params=params
            ) as resp:
                if resp.status != 200:
                    log.error("Failed to fetch Twitch user %s: %s", username, await resp.text())
                    return None
                data = await resp.json()
        except aiohttp.ClientError:
            log.exception("Network error while fetching Twitch user %s", username)
            return None

        results = data.get("data", [])
        if not results:
            return None
        user = results[0]
        return {
            "id": user.get("id"),
            "login": user.get("login"),
            "display_name": user.get("display_name"),
            "profile_image_url": user.get("profile_image_url"),
        }

    async def _get_schedule(self, broadcaster_id: str) -> Optional[dict]:
        """Returns the schedule data dict, an empty dict if none is set, or None on failure."""
        headers = await self._headers()
        if not headers:
            return None
        params = {"broadcaster_id": broadcaster_id, "first": "25"}
        try:
            async with self.session.get(
                f"{TWITCH_API_BASE}/schedule", headers=headers, params=params
            ) as resp:
                if resp.status == 404:
                    # Broadcaster has not set up a schedule at all.
                    return {"segments": [], "vacation": None}
                if resp.status != 200:
                    log.error(
                        "Failed to fetch schedule for %s: %s",
                        broadcaster_id,
                        await resp.text(),
                    )
                    return None
                data = await resp.json()
        except aiohttp.ClientError:
            log.exception("Network error while fetching schedule for %s", broadcaster_id)
            return None

        return data.get("data", {"segments": [], "vacation": None})

    # ---------------------------------------------------------------------
    # Edit detection
    #
    # Twitch's schedule endpoint only ever returns future occurrences. Once
    # a stream airs it disappears from the list, and a recurring stream's
    # "next" occurrence quietly rolls forward to next week with the same
    # id. Naively comparing raw API responses treats that normal rolling
    # forward as an edit. To avoid that, recurring segments are fingerprinted
    # by weekday and time of day (which stay constant week to week) rather
    # than by absolute date, so only a genuine change registers as an edit.
    # ---------------------------------------------------------------------

    @staticmethod
    def _parse_unix(timestamp_str: str) -> Optional[int]:
        if not timestamp_str:
            return None
        try:
            dt = datetime.datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%SZ")
            dt = dt.replace(tzinfo=datetime.timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            return None

    def _build_fingerprint(self, schedule: dict) -> dict:
        segments = schedule.get("segments") or []
        fingerprint = {}

        for seg in segments:
            seg_id = seg.get("id")
            if not seg_id:
                continue
            start_unix = self._parse_unix(seg.get("start_time"))
            if start_unix is None:
                continue
            end_unix = self._parse_unix(seg.get("end_time"))

            category = seg.get("category") or {}
            title = seg.get("title")
            category_id = category.get("id")
            is_recurring = bool(seg.get("is_recurring"))
            canceled_until = seg.get("canceled_until")

            if is_recurring:
                start_dt = datetime.datetime.fromtimestamp(start_unix, tz=datetime.timezone.utc)
                duration = (end_unix - start_unix) if end_unix else None
                data = {
                    "title": title,
                    "category_id": category_id,
                    "weekday": start_dt.weekday(),
                    "time_of_day": start_dt.strftime("%H:%M"),
                    "duration": duration,
                    "canceled_until": canceled_until,
                    "is_one_off": False,
                }
            else:
                # One off streams are unique instances. Once their start
                # time has passed they naturally vanish from the API; that
                # is expected and is handled in _fingerprints_differ, not
                # treated as an edit.
                data = {
                    "title": title,
                    "category_id": category_id,
                    "start_time": start_unix,
                    "end_time": end_unix,
                    "canceled_until": canceled_until,
                    "is_one_off": True,
                    "start_time_when_seen": start_unix,
                }

            # If the same id appears more than once (multiple future
            # occurrences of a recurring segment), keep the earliest one as
            # the canonical definition.
            existing = fingerprint.get(seg_id)
            if existing is None or start_unix < existing.get("_start_unix", start_unix):
                data["_start_unix"] = start_unix
                fingerprint[seg_id] = data

        return fingerprint

    @staticmethod
    def _fingerprints_differ(old_fp: dict, new_fp: dict) -> bool:
        now_unix = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

        def _clean(data: dict) -> dict:
            return {k: v for k, v in data.items() if k != "_start_unix"}

        filtered_old = {}
        for seg_id, data in old_fp.items():
            if data.get("is_one_off") and data.get("start_time_when_seen", 0) <= now_unix:
                # Expected to have already aired and rolled off the list.
                continue
            filtered_old[seg_id] = _clean(data)

        new_clean = {seg_id: _clean(data) for seg_id, data in new_fp.items()}

        return filtered_old != new_clean

    # ---------------------------------------------------------------------
    # Week windowing
    # ---------------------------------------------------------------------

    @staticmethod
    def _get_week_bounds(now: datetime.datetime):
        monday_start = (now - datetime.timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        next_monday = monday_start + datetime.timedelta(days=7)
        return monday_start, next_monday

    def _get_week_segments(self, schedule: dict, now: datetime.datetime):
        monday_start, next_monday = self._get_week_bounds(now)
        monday_unix = int(monday_start.timestamp())
        next_monday_unix = int(next_monday.timestamp())

        segments = schedule.get("segments") or []
        week_segments = []
        for seg in segments:
            start_unix = self._parse_unix(seg.get("start_time"))
            if start_unix is None:
                continue
            if monday_unix <= start_unix < next_monday_unix:
                week_segments.append(seg)

        week_segments.sort(key=lambda s: s.get("start_time", ""))
        return week_segments, monday_unix, next_monday_unix

    # ---------------------------------------------------------------------
    # Embed building
    # ---------------------------------------------------------------------

    def _build_embed(self, conf: dict, schedule: dict, now: datetime.datetime) -> discord.Embed:
        display_name = conf.get("display_name") or conf.get("twitch_username")
        login = conf.get("twitch_username")
        profile_image = conf.get("profile_image_url")

        week_segments, monday_unix, next_monday_unix = self._get_week_segments(schedule, now)
        sunday_unix = next_monday_unix - 1

        embed = discord.Embed(
            title="This Week's Stream Schedule",
            url=f"https://twitch.tv/{login}/schedule" if login else None,
            color=TWITCH_PURPLE,
            timestamp=now,
        )
        embed.set_author(
            name=display_name,
            icon_url=profile_image,
            url=f"https://twitch.tv/{login}" if login else None,
        )
        if profile_image:
            embed.set_thumbnail(url=profile_image)

        description_lines = [f"<t:{monday_unix}:D> - <t:{sunday_unix}:D>"]

        vacation = schedule.get("vacation")
        if vacation:
            start_unix = self._parse_unix(vacation.get("start_time"))
            end_unix = self._parse_unix(vacation.get("end_time"))
            if start_unix and end_unix:
                description_lines.append(
                    f"On a break from <t:{start_unix}:D> until <t:{end_unix}:D>."
                )
            else:
                description_lines.append("This channel is currently on a scheduled break.")

        embed.description = "\n".join(description_lines)

        if not week_segments:
            embed.add_field(
                name="\u200b",
                value="No streams scheduled for the rest of this week.",
                inline=False,
            )
        else:
            for seg in week_segments[:MAX_SEGMENTS_SHOWN]:
                title = seg.get("title") or "Untitled Stream"
                start_unix = self._parse_unix(seg.get("start_time"))
                category = seg.get("category") or {}
                category_name = category.get("name") or "Not set"

                lines = []
                if start_unix:
                    lines.append(f"**When:** <t:{start_unix}:F> (<t:{start_unix}:R>)")
                lines.append(f"**Category:** {category_name}")
                if seg.get("is_recurring"):
                    lines.append("*Recurring weekly*")
                if seg.get("canceled_until"):
                    lines.append("**This occurrence is canceled**")

                emoji = "\U0001F534"  # red circle
                field_name = f"{emoji} {title}"[:256]
                embed.add_field(name=field_name, value="\n".join(lines)[:1024], inline=False)

            if len(week_segments) > MAX_SEGMENTS_SHOWN:
                remaining = len(week_segments) - MAX_SEGMENTS_SHOWN
                embed.add_field(
                    name="\u200b",
                    value=f"...and {remaining} more stream(s) this week. Full schedule linked above.",
                    inline=False,
                )

        embed.set_footer(text="Times shown above adjust automatically to your local timezone")
        return embed

    # ---------------------------------------------------------------------
    # Discord Scheduled Events
    # ---------------------------------------------------------------------

    async def _dedupe_existing_events(self, guild: discord.Guild, event_ids: dict):
        """
        Find and merge duplicate scheduled events (same name and start time)
        that may exist from a past bug, a concurrent run, or the cog having
        briefly been loaded twice. Keeps the event already referenced in our
        mapping when possible, otherwise keeps the oldest (lowest id) one.
        Returns the corrected event_ids mapping and how many were removed.
        """
        try:
            events = await guild.fetch_scheduled_events()
        except discord.HTTPException:
            log.exception("Failed to fetch scheduled events for dedupe in guild %s", guild.id)
            return event_ids, 0

        active_events = [
            e
            for e in events
            if e.status in (discord.EventStatus.scheduled, discord.EventStatus.active)
        ]

        groups: dict = {}
        for e in active_events:
            key = (e.name, e.start_time.replace(second=0, microsecond=0))
            groups.setdefault(key, []).append(e)

        tracked_ids = set(event_ids.values())
        id_remap = {}
        removed_count = 0

        for group in groups.values():
            if len(group) <= 1:
                continue

            survivor = None
            for e in group:
                if e.id in tracked_ids:
                    survivor = e
                    break
            if survivor is None:
                survivor = min(group, key=lambda e: e.id)

            for e in group:
                if e.id == survivor.id:
                    continue
                try:
                    await e.delete()
                    removed_count += 1
                except discord.HTTPException:
                    log.exception("Failed to delete duplicate scheduled event %s", e.id)
                    continue
                id_remap[e.id] = survivor.id

        if id_remap:
            for seg_id, discord_id in list(event_ids.items()):
                if discord_id in id_remap:
                    event_ids[seg_id] = id_remap[discord_id]

        if removed_count:
            log.info(
                "Removed %s duplicate scheduled event(s) in guild %s", removed_count, guild.id
            )

        return event_ids, removed_count

    async def _sync_events(self, guild: discord.Guild, conf: dict, week_segments: list, now: datetime.datetime):
        event_ids = dict(conf.get("event_ids") or {})
        login = conf.get("twitch_username")
        active_ids = set()

        # Self heal first: merge away any duplicates that already exist
        # before deciding what still needs to be created or updated.
        event_ids, _ = await self._dedupe_existing_events(guild, event_ids)

        # Guard against the same Twitch segment id appearing more than once
        # in this week's window (for example a daily recurring segment can
        # produce several occurrences that share one id).
        seen_ids = set()
        deduped_segments = []
        for seg in week_segments:
            seg_id = seg.get("id")
            if seg_id and seg_id in seen_ids:
                continue
            if seg_id:
                seen_ids.add(seg_id)
            deduped_segments.append(seg)
        week_segments = deduped_segments

        for seg in week_segments:
            seg_id = seg.get("id")
            if not seg_id:
                continue

            start_unix = self._parse_unix(seg.get("start_time"))
            if not start_unix or start_unix <= int(now.timestamp()):
                # Discord will not allow scheduling events in the past.
                continue

            end_unix = self._parse_unix(seg.get("end_time"))
            if not end_unix or end_unix <= start_unix:
                end_unix = start_unix + DEFAULT_EVENT_DURATION_SECONDS

            start_dt = datetime.datetime.fromtimestamp(start_unix, tz=datetime.timezone.utc)
            end_dt = datetime.datetime.fromtimestamp(end_unix, tz=datetime.timezone.utc)

            title = (seg.get("title") or "Stream")[:100]
            category_name = (seg.get("category") or {}).get("name") or "Not set"
            twitch_url = f"https://twitch.tv/{login}" if login else "https://twitch.tv"
            description = f"Category: {category_name}\nWatch live at {twitch_url}"[:1000]
            location = (f"twitch.tv/{login}" if login else "Twitch")[:100]

            event_obj = None
            existing_id = event_ids.get(seg_id)
            if existing_id:
                try:
                    event_obj = await guild.fetch_scheduled_event(existing_id)
                except discord.NotFound:
                    event_obj = None
                except discord.HTTPException:
                    log.exception("Failed to fetch scheduled event %s", existing_id)
                    event_obj = None

                if event_obj is not None and event_obj.status not in (
                    discord.EventStatus.scheduled,
                    discord.EventStatus.active,
                ):
                    # Already completed or canceled on Discord's side; a
                    # fresh event needs to be created for the new occurrence.
                    event_obj = None

            if event_obj is not None:
                needs_update = (
                    event_obj.name != title
                    or event_obj.start_time != start_dt
                    or event_obj.end_time != end_dt
                )
                if needs_update:
                    try:
                        await event_obj.edit(
                            name=title,
                            description=description,
                            start_time=start_dt,
                            end_time=end_dt,
                            location=location,
                        )
                    except discord.HTTPException:
                        log.exception("Failed to update scheduled event for segment %s", seg_id)
                event_ids[seg_id] = event_obj.id
                active_ids.add(seg_id)
                continue

            try:
                new_event = await guild.create_scheduled_event(
                    name=title,
                    description=description,
                    start_time=start_dt,
                    end_time=end_dt,
                    entity_type=discord.EntityType.external,
                    privacy_level=discord.PrivacyLevel.guild_only,
                    location=location,
                )
            except discord.HTTPException:
                log.exception("Failed to create scheduled event for segment %s", seg_id)
                continue

            event_ids[seg_id] = new_event.id
            active_ids.add(seg_id)

        # Clean up events for segments that were canceled or removed before
        # they aired. Segments that simply already aired are left alone;
        # Discord marks those completed on its own.
        stale_ids = [seg_id for seg_id in event_ids if seg_id not in active_ids]
        for seg_id in stale_ids:
            discord_event_id = event_ids.pop(seg_id, None)
            if not discord_event_id:
                continue
            try:
                event_obj = await guild.fetch_scheduled_event(discord_event_id)
            except discord.NotFound:
                continue
            except discord.HTTPException:
                log.exception("Failed to fetch scheduled event %s for cleanup", discord_event_id)
                continue

            if (
                event_obj.status in (discord.EventStatus.scheduled, discord.EventStatus.active)
                and event_obj.start_time > now
            ):
                try:
                    await event_obj.delete()
                except discord.HTTPException:
                    log.exception("Failed to delete scheduled event %s", discord_event_id)

        await self.config.guild(guild).event_ids.set(event_ids)

    # ---------------------------------------------------------------------
    # Background loop
    # ---------------------------------------------------------------------

    @tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
    async def schedule_check_loop(self):
        all_guilds = await self.config.all_guilds()
        for guild_id, conf in all_guilds.items():
            try:
                await self._check_guild(guild_id, conf)
            except Exception:
                log.exception("Error while checking schedule for guild %s", guild_id)

    @schedule_check_loop.before_loop
    async def before_schedule_check_loop(self):
        await self.bot.wait_until_red_ready()

    async def _check_guild(self, guild_id: int, conf: dict, force: bool = False):
        # Serialize per guild so a manual command (scheduleforce,
        # scheduletwitch) can never run at the same time as the automatic
        # loop tick for the same server. Running twice at once was the
        # cause of duplicate Discord Scheduled Events being created.
        lock = self._get_guild_lock(guild_id)
        async with lock:
            await self._check_guild_locked(guild_id, conf, force=force)

    async def _check_guild_locked(self, guild_id: int, conf: dict, force: bool = False):
        username = conf.get("twitch_username")
        channel_id = conf.get("channel_id")
        broadcaster_id = conf.get("broadcaster_id")
        if not username or not channel_id or not broadcaster_id:
            return

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = guild.get_channel(channel_id)
        if channel is None:
            return

        guild_group = self.config.guild(guild)

        schedule = await self._get_schedule(broadcaster_id)
        if schedule is None:
            return

        now = datetime.datetime.now(datetime.timezone.utc)

        new_fp = self._build_fingerprint(schedule)
        old_fp = conf.get("last_fingerprint") or {}
        edited = self._fingerprints_differ(old_fp, new_fp)

        iso_year, iso_week, _ = now.isocalendar()
        current_iso_week = f"{iso_year}-W{iso_week}"
        last_forced_iso_week = conf.get("last_forced_iso_week")

        should_post = False
        if force:
            should_post = True
        elif edited:
            should_post = True
        elif (
            now.weekday() == FORCE_POST_WEEKDAY
            and now.hour == FORCE_POST_HOUR_UTC
            and last_forced_iso_week != current_iso_week
        ):
            should_post = True

        # Keep the stored fingerprint fresh regardless of whether a post
        # happens, so expected natural changes (like a one off stream
        # airing and rolling off the list) do not linger.
        await guild_group.last_fingerprint.set(new_fp)

        if not should_post:
            return

        embed = self._build_embed(conf, schedule, now)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            log.exception("Failed to post schedule in guild %s", guild_id)

        if conf.get("create_events", True):
            week_segments, _, _ = self._get_week_segments(schedule, now)
            try:
                await self._sync_events(guild, conf, week_segments, now)
            except Exception:
                log.exception("Failed to sync scheduled events for guild %s", guild_id)

        if now.weekday() == FORCE_POST_WEEKDAY and now.hour == FORCE_POST_HOUR_UTC:
            await guild_group.last_forced_iso_week.set(current_iso_week)

    # ---------------------------------------------------------------------
    # Commands
    # ---------------------------------------------------------------------

    async def _clear_guild_watch(self, guild: discord.Guild):
        """Stop watching whatever Twitch channel is set and remove any
        Discord Scheduled Events that were created for it."""
        guild_group = self.config.guild(guild)
        conf = await guild_group.all()
        event_ids = conf.get("event_ids") or {}

        if event_ids:
            lock = self._get_guild_lock(guild.id)
            async with lock:
                for discord_event_id in list(event_ids.values()):
                    try:
                        event_obj = await guild.fetch_scheduled_event(discord_event_id)
                    except discord.NotFound:
                        continue
                    except discord.HTTPException:
                        log.exception(
                            "Failed to fetch scheduled event %s while clearing", discord_event_id
                        )
                        continue
                    if event_obj.status in (discord.EventStatus.scheduled, discord.EventStatus.active):
                        try:
                            await event_obj.delete()
                        except discord.HTTPException:
                            log.exception(
                                "Failed to delete scheduled event %s while clearing",
                                discord_event_id,
                            )

        await guild_group.twitch_username.set(None)
        await guild_group.broadcaster_id.set(None)
        await guild_group.display_name.set(None)
        await guild_group.profile_image_url.set(None)
        await guild_group.last_fingerprint.set({})
        await guild_group.last_forced_iso_week.set(None)
        await guild_group.event_ids.set({})

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def scheduletwitch(self, ctx: commands.Context, username: str):
        """
        Set the Twitch username to watch for schedule updates.

        Running this again with the username currently being watched stops
        watching it (and removes any Discord Scheduled Events created for
        it). Use `[p]scheduleremove` instead if you are not sure of the
        exact username currently set.

        Example:
        [p]scheduletwitch spookymochii
        """
        username = username.lstrip("@").strip()
        if not username:
            await ctx.send("Please provide a valid Twitch username.")
            return

        guild_group = self.config.guild(ctx.guild)
        current_username = await guild_group.twitch_username()

        if current_username and current_username.lower() == username.lower():
            async with ctx.typing():
                await self._clear_guild_watch(ctx.guild)
            await ctx.send(f"Stopped watching **{current_username}**'s schedule.")
            return

        async with ctx.typing():
            info = await self._get_broadcaster_info(username)

        if not info:
            await ctx.send(
                "I could not find that Twitch username, or the Twitch API credentials "
                "have not been set yet.\n\n"
                "A bot owner needs to set API credentials once with:\n"
                "`[p]set api twitch client_id,<your_client_id> client_secret,<your_client_secret>`\n\n"
                "You can get a client ID and secret by registering an application at "
                "https://dev.twitch.tv/console/apps"
            )
            return

        if current_username:
            # Switching to a different channel: clear out the old one's
            # events first so nothing gets left orphaned.
            await self._clear_guild_watch(ctx.guild)

        await guild_group.twitch_username.set(info["login"])
        await guild_group.broadcaster_id.set(info["id"])
        await guild_group.display_name.set(info["display_name"])
        await guild_group.profile_image_url.set(info["profile_image_url"])
        await guild_group.last_fingerprint.set({})
        await guild_group.last_forced_iso_week.set(None)

        await ctx.send(
            f"Now watching **{info['display_name']}**'s schedule. "
            "If a post channel is already set, I will post the current schedule shortly."
        )

        # Trigger an immediate check so setup feels responsive instead of
        # waiting for the next 5 minute loop tick.
        conf = await guild_group.all()
        await self._check_guild(ctx.guild.id, conf, force=True)

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def scheduleremove(self, ctx: commands.Context):
        """Stop watching the current Twitch schedule and remove any Discord Scheduled Events for it."""
        current_username = await self.config.guild(ctx.guild).twitch_username()
        if not current_username:
            await ctx.send("I am not currently watching any Twitch schedule for this server.")
            return

        async with ctx.typing():
            await self._clear_guild_watch(ctx.guild)
        await ctx.send(f"Stopped watching **{current_username}**'s schedule.")

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def schedulechannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Set the channel where the schedule embed will be posted.

        Example:
        [p]schedulechannel #twitchschedule
        """
        perms = channel.permissions_for(ctx.guild.me)
        if not perms.send_messages or not perms.embed_links:
            await ctx.send(
                f"I need both Send Messages and Embed Links permissions in {channel.mention}."
            )
            return

        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"Schedule updates will be posted in {channel.mention}.")

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def scheduleevents(self, ctx: commands.Context, enabled: bool):
        """
        Toggle automatic Discord Scheduled Events for this week's streams.

        I need the Manage Events permission for this to work.

        Example:
        [p]scheduleevents true
        [p]scheduleevents false
        """
        await self.config.guild(ctx.guild).create_events.set(enabled)
        state = "enabled" if enabled else "disabled"
        await ctx.send(f"Automatic Discord scheduled events are now {state}.")

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def scheduleforce(self, ctx: commands.Context):
        """Force an immediate repost of the current week's schedule."""
        conf = await self.config.guild(ctx.guild).all()
        if not conf.get("twitch_username") or not conf.get("channel_id"):
            await ctx.send("Please set both `scheduletwitch` and `schedulechannel` first.")
            return

        async with ctx.typing():
            await self._check_guild(ctx.guild.id, conf, force=True)
        await ctx.send("Schedule reposted.")

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def scheduleeventscleanup(self, ctx: commands.Context):
        """
        Find and merge any duplicate Discord Scheduled Events (same name and
        start time) right now, without waiting for the next scheduled check.
        """
        conf = await self.config.guild(ctx.guild).all()
        lock = self._get_guild_lock(ctx.guild.id)
        async with lock:
            async with ctx.typing():
                event_ids = dict(conf.get("event_ids") or {})
                event_ids, removed_count = await self._dedupe_existing_events(ctx.guild, event_ids)
                await self.config.guild(ctx.guild).event_ids.set(event_ids)

        if removed_count:
            await ctx.send(f"Cleaned up {removed_count} duplicate scheduled event(s).")
        else:
            await ctx.send("No duplicate scheduled events found.")

    @commands.command()
    @commands.guild_only()
    async def schedulesettings(self, ctx: commands.Context):
        """Show the current Twitch schedule settings for this server."""
        conf = await self.config.guild(ctx.guild).all()
        display_name = conf.get("display_name") or conf.get("twitch_username") or "Not set"

        channel = None
        if conf.get("channel_id"):
            channel = ctx.guild.get_channel(conf["channel_id"])
        channel_text = channel.mention if channel else "Not set"

        events_state = "Enabled" if conf.get("create_events", True) else "Disabled"

        embed = discord.Embed(title="Twitch Schedule Settings", color=await ctx.embed_color())
        embed.add_field(name="Twitch Channel", value=display_name, inline=False)
        embed.add_field(name="Post Channel", value=channel_text, inline=False)
        embed.add_field(name="Discord Scheduled Events", value=events_state, inline=False)
        embed.set_footer(
            text=(
                f"Checking for changes every {CHECK_INTERVAL_MINUTES} minutes, "
                "forced weekly refresh Monday ~04:00 UTC"
            )
        )
        await ctx.send(embed=embed)
