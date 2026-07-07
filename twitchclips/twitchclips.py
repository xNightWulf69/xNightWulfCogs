import asyncio
import datetime
import logging
from typing import Optional

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import commands, Config
from redbot.core.bot import Red

log = logging.getLogger("red.twitchclips")

TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
TWITCH_API_BASE = "https://api.twitch.tv/helix"

CHECK_INTERVAL_MINUTES = 2
BACKFILL_MINUTES_ON_FIRST_RUN = 10
MAX_TRACKED_CLIP_IDS = 150


class TwitchClips(commands.Cog):
    """
    Watch a Twitch channel and automatically post new clips to a Discord channel.

    Only clips are posted (never full broadcasts/VODs), because this cog uses
    Twitch's "Get Clips" endpoint, which is a separate data type from videos/streams.
    """

    __version__ = "1.1.0"
    __author__ = "Custom"

    def __init__(self, bot: Red):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        self.config = Config.get_conf(self, identifier=890234571, force_registration=True)

        default_guild = {
            "twitch_username": None,
            "broadcaster_id": None,
            "channel_id": None,
            "message": "{streamer} just posted a new clip!",
            "last_check": None,
            "posted_clip_ids": [],
        }
        self.config.register_guild(**default_guild)

        self._app_token: Optional[str] = None
        self._app_token_expiry: float = 0.0
        self._lock = asyncio.Lock()

        self.clip_check_loop.start()

    def cog_unload(self):
        self.clip_check_loop.cancel()
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
            # Re-check after acquiring the lock in case another task refreshed it
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

    async def _get_broadcaster_id(self, username: str) -> Optional[str]:
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
        return results[0]["id"]

    async def _get_new_clips(self, broadcaster_id: str, started_at: str):
        headers = await self._headers()
        if not headers:
            return []
        params = {
            "broadcaster_id": broadcaster_id,
            "started_at": started_at,
            "first": "20",
        }
        try:
            async with self.session.get(
                f"{TWITCH_API_BASE}/clips", headers=headers, params=params
            ) as resp:
                if resp.status != 200:
                    log.error(
                        "Failed to fetch clips for broadcaster %s: %s",
                        broadcaster_id,
                        await resp.text(),
                    )
                    return []
                data = await resp.json()
        except aiohttp.ClientError:
            log.exception("Network error while fetching clips for %s", broadcaster_id)
            return []

        return data.get("data", [])

    # ---------------------------------------------------------------------
    # Background loop
    # ---------------------------------------------------------------------

    @tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
    async def clip_check_loop(self):
        all_guilds = await self.config.all_guilds()
        for guild_id, conf in all_guilds.items():
            try:
                await self._check_guild(guild_id, conf)
            except Exception:
                log.exception("Error while checking clips for guild %s", guild_id)

    @clip_check_loop.before_loop
    async def before_clip_check_loop(self):
        await self.bot.wait_until_red_ready()

    async def _check_guild(self, guild_id: int, conf: dict):
        username = conf.get("twitch_username")
        channel_id = conf.get("channel_id")
        if not username or not channel_id:
            return

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = guild.get_channel(channel_id)
        if channel is None:
            return

        guild_group = self.config.guild(guild)

        broadcaster_id = conf.get("broadcaster_id")
        if not broadcaster_id:
            broadcaster_id = await self._get_broadcaster_id(username)
            if not broadcaster_id:
                return
            await guild_group.broadcaster_id.set(broadcaster_id)

        now = datetime.datetime.now(datetime.timezone.utc)
        last_check = conf.get("last_check")
        if last_check:
            started_at = last_check
        else:
            started_at = (
                now - datetime.timedelta(minutes=BACKFILL_MINUTES_ON_FIRST_RUN)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")

        clips = await self._get_new_clips(broadcaster_id, started_at)
        if clips is None:
            return

        posted_ids = list(conf.get("posted_clip_ids") or [])
        posted_ids_set = set(posted_ids)

        new_clips = [c for c in clips if c.get("id") not in posted_ids_set]
        new_clips.sort(key=lambda c: c.get("created_at", ""))

        if new_clips:
            message_template = conf.get("message") or "{streamer} just posted a new clip!"
            for clip in new_clips:
                text = message_template.replace("{streamer}", username)
                url = clip.get("url")
                if not url:
                    continue
                try:
                    await channel.send(f"{text}\n{url}")
                except discord.HTTPException:
                    log.exception("Failed to post clip in guild %s", guild_id)
                    continue
                posted_ids.append(clip["id"])

            if len(posted_ids) > MAX_TRACKED_CLIP_IDS:
                posted_ids = posted_ids[-MAX_TRACKED_CLIP_IDS:]

            await guild_group.posted_clip_ids.set(posted_ids)

        await guild_group.last_check.set(now.strftime("%Y-%m-%dT%H:%M:%SZ"))

    # ---------------------------------------------------------------------
    # Commands
    # ---------------------------------------------------------------------

    async def _clear_guild_watch(self, guild: discord.Guild):
        """Stop watching whatever Twitch channel is set for this server."""
        guild_group = self.config.guild(guild)
        await guild_group.twitch_username.set(None)
        await guild_group.broadcaster_id.set(None)
        await guild_group.last_check.set(None)
        await guild_group.posted_clip_ids.set([])

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def cliptwitch(self, ctx: commands.Context, username: str):
        """
        Set the Twitch username to watch for new clips.

        Running this again with the username currently being watched stops
        watching it. Use `[p]clipremove` instead if you are not sure of the
        exact username currently set.

        Example:
        [p]cliptwitch spookymochii
        """
        username = username.lstrip("@").strip()
        if not username:
            await ctx.send("Please provide a valid Twitch username.")
            return

        guild_group = self.config.guild(ctx.guild)
        current_username = await guild_group.twitch_username()

        if current_username and current_username.lower() == username.lower():
            await self._clear_guild_watch(ctx.guild)
            await ctx.send(f"Stopped watching **{current_username}** for new Twitch clips.")
            return

        async with ctx.typing():
            broadcaster_id = await self._get_broadcaster_id(username)

        if not broadcaster_id:
            await ctx.send(
                "I could not find that Twitch username, or the Twitch API credentials "
                "have not been set yet.\n\n"
                "A bot owner needs to set API credentials once with:\n"
                "`[p]set api twitch client_id,<your_client_id> client_secret,<your_client_secret>`\n\n"
                "You can get a client ID and secret by registering an application at "
                "https://dev.twitch.tv/console/apps"
            )
            return

        await guild_group.twitch_username.set(username)
        await guild_group.broadcaster_id.set(broadcaster_id)

        # Reset tracking so we start watching fresh from now, without dumping
        # a backlog of old clips into the channel.
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        await guild_group.last_check.set(now)
        await guild_group.posted_clip_ids.set([])

        await ctx.send(f"Now watching **{username}** for new Twitch clips.")

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def clipremove(self, ctx: commands.Context):
        """Stop watching the current Twitch channel for new clips."""
        current_username = await self.config.guild(ctx.guild).twitch_username()
        if not current_username:
            await ctx.send("I am not currently watching any Twitch channel for this server.")
            return

        await self._clear_guild_watch(ctx.guild)
        await ctx.send(f"Stopped watching **{current_username}** for new Twitch clips.")

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def clipchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Set the channel where new Twitch clips will be posted.

        Example:
        [p]clipchannel #clips
        """
        perms = channel.permissions_for(ctx.guild.me)
        if not perms.send_messages:
            await ctx.send(f"I do not have permission to send messages in {channel.mention}.")
            return

        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"New Twitch clips will be posted in {channel.mention}.")

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def clipmessage(self, ctx: commands.Context, *, message: str):
        """
        Set the message posted above the clip link.

        Use {streamer} as a placeholder for the Twitch username.

        Example:
        [p]clipmessage {streamer} just posted a clip!
        """
        await self.config.guild(ctx.guild).message.set(message)
        await ctx.send(f"Clip message set to:\n{message}")

    @commands.command()
    @commands.guild_only()
    async def clipsettings(self, ctx: commands.Context):
        """Show the current Twitch clip settings for this server."""
        conf = await self.config.guild(ctx.guild).all()
        username = conf.get("twitch_username") or "Not set"

        channel = None
        if conf.get("channel_id"):
            channel = ctx.guild.get_channel(conf["channel_id"])
        channel_text = channel.mention if channel else "Not set"

        message = conf.get("message") or "Not set"

        embed = discord.Embed(title="Twitch Clip Settings", color=await ctx.embed_color())
        embed.add_field(name="Twitch Username", value=username, inline=False)
        embed.add_field(name="Post Channel", value=channel_text, inline=False)
        embed.add_field(name="Message", value=message, inline=False)
        embed.set_footer(text=f"Checking for new clips every {CHECK_INTERVAL_MINUTES} minutes")
        await ctx.send(embed=embed)
