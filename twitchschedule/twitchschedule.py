import asyncio
import datetime
import hashlib
import json
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


class TwitchSchedule(commands.Cog):
    """
    Watch a Twitch channel's stream schedule and post it to a Discord channel
    as a formatted embed whenever it changes.

    Checks every 5 minutes for edits. If nothing has changed all week, it
    force-posts a fresh copy of the schedule every Monday around 4:00 UTC
    so the channel never goes stale.
    """

    __version__ = "1.0.0"
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
            "last_schedule_hash": None,
            "last_forced_iso_week": None,
        }
        self.config.register_guild(**default_guild)

        self._app_token: Optional[str] = None
        self._app_token_expiry: float = 0.0
        self._lock = asyncio.Lock()

        self.schedule_check_loop.start()

    def cog_unload(self):
        self.schedule_check_loop.cancel()
        asyncio.create_task(self.session.close())

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

    @staticmethod
    def _hash_schedule(schedule: dict) -> str:
        segments = schedule.get("segments") or []
        canonical = []
        for seg in sorted(segments, key=lambda s: s.get("start_time", "")):
            category = seg.get("category") or {}
            canonical.append(
                {
                    "id": seg.get("id"),
                    "start_time": seg.get("start_time"),
                    "end_time": seg.get("end_time"),
                    "title": seg.get("title"),
                    "category_id": category.get("id"),
                    "is_recurring": seg.get("is_recurring"),
                    "canceled_until": seg.get("canceled_until"),
                }
            )
        vacation = schedule.get("vacation")
        payload = json.dumps({"segments": canonical, "vacation": vacation}, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # ---------------------------------------------------------------------
    # Embed building
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

    def _build_embed(self, conf: dict, schedule: dict) -> discord.Embed:
        display_name = conf.get("display_name") or conf.get("twitch_username")
        login = conf.get("twitch_username")
        profile_image = conf.get("profile_image_url")

        embed = discord.Embed(
            title="Weekly Stream Schedule",
            url=f"https://twitch.tv/{login}/schedule" if login else None,
            color=TWITCH_PURPLE,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_author(
            name=display_name,
            icon_url=profile_image,
            url=f"https://twitch.tv/{login}" if login else None,
        )
        if profile_image:
            embed.set_thumbnail(url=profile_image)

        vacation = schedule.get("vacation")
        if vacation:
            start_unix = self._parse_unix(vacation.get("start_time"))
            end_unix = self._parse_unix(vacation.get("end_time"))
            if start_unix and end_unix:
                embed.description = (
                    f"On a break from <t:{start_unix}:D> until <t:{end_unix}:D>."
                )
            else:
                embed.description = "This channel is currently on a scheduled break."

        segments = schedule.get("segments") or []
        segments = sorted(segments, key=lambda s: s.get("start_time", ""))

        if not segments:
            if not embed.description:
                embed.description = "No upcoming streams are scheduled right now."
        else:
            for seg in segments[:MAX_SEGMENTS_SHOWN]:
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

            if len(segments) > MAX_SEGMENTS_SHOWN:
                remaining = len(segments) - MAX_SEGMENTS_SHOWN
                embed.add_field(
                    name="\u200b",
                    value=f"...and {remaining} more upcoming stream(s). Full schedule linked above.",
                    inline=False,
                )

        embed.set_footer(text="Times shown above adjust automatically to your local timezone")
        return embed

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

        new_hash = self._hash_schedule(schedule)
        old_hash = conf.get("last_schedule_hash")

        now = datetime.datetime.now(datetime.timezone.utc)
        iso_year, iso_week, _ = now.isocalendar()
        current_iso_week = f"{iso_year}-W{iso_week}"
        last_forced_iso_week = conf.get("last_forced_iso_week")

        should_post = False

        if force:
            should_post = True
        elif new_hash != old_hash:
            should_post = True
        elif (
            now.weekday() == FORCE_POST_WEEKDAY
            and now.hour == FORCE_POST_HOUR_UTC
            and last_forced_iso_week != current_iso_week
        ):
            should_post = True

        if not should_post:
            return

        embed = self._build_embed(conf, schedule)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            log.exception("Failed to post schedule in guild %s", guild_id)
            return

        await guild_group.last_schedule_hash.set(new_hash)
        if now.weekday() == FORCE_POST_WEEKDAY and now.hour == FORCE_POST_HOUR_UTC:
            await guild_group.last_forced_iso_week.set(current_iso_week)

    # ---------------------------------------------------------------------
    # Commands
    # ---------------------------------------------------------------------

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def scheduletwitch(self, ctx: commands.Context, username: str):
        """
        Set the Twitch username to watch for schedule updates.

        Example:
        [p]scheduletwitch spookymochii
        """
        username = username.lstrip("@").strip()
        if not username:
            await ctx.send("Please provide a valid Twitch username.")
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

        guild_group = self.config.guild(ctx.guild)
        await guild_group.twitch_username.set(info["login"])
        await guild_group.broadcaster_id.set(info["id"])
        await guild_group.display_name.set(info["display_name"])
        await guild_group.profile_image_url.set(info["profile_image_url"])
        await guild_group.last_schedule_hash.set(None)
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
    async def scheduleforce(self, ctx: commands.Context):
        """Force an immediate repost of the current schedule."""
        conf = await self.config.guild(ctx.guild).all()
        if not conf.get("twitch_username") or not conf.get("channel_id"):
            await ctx.send("Please set both `scheduletwitch` and `schedulechannel` first.")
            return

        async with ctx.typing():
            await self._check_guild(ctx.guild.id, conf, force=True)
        await ctx.send("Schedule reposted.")

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

        embed = discord.Embed(title="Twitch Schedule Settings", color=await ctx.embed_color())
        embed.add_field(name="Twitch Channel", value=display_name, inline=False)
        embed.add_field(name="Post Channel", value=channel_text, inline=False)
        embed.set_footer(
            text=(
                f"Checking for changes every {CHECK_INTERVAL_MINUTES} minutes, "
                "forced weekly refresh Monday ~04:00 UTC"
            )
        )
        await ctx.send(embed=embed)
