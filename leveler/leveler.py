import discord
from redbot.core import commands, Config
import random
import aiohttp
import io
import math
import asyncio
import time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def xp_for_level(level: int) -> int:
    return int(100 * (level ** 1.2))


COG_DIR = Path(__file__).parent
FONT_PATH = COG_DIR / "fonts" / "Roboto-Black.ttf"

_font_cache = {}

TEXT_STROKE_WIDTH = 2
BAR_OUTLINE_WIDTH = 4


def load_font(size):
    if size in _font_cache:
        return _font_cache[size]
    try:
        font = ImageFont.truetype(str(FONT_PATH), size)
    except (OSError, IOError):
        try:
            font = ImageFont.load_default(size=size)
        except TypeError:
            font = ImageFont.load_default()
    _font_cache[size] = font
    return font


def validate_rgb(r, g, b):
    return all(0 <= v <= 255 for v in (r, g, b))


class Leveling(commands.Cog):
    """Advanced leveling system with caching + image cards (per-guild XP/levels)"""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)

        default_guild = {
            "min_xp": 5,
            "max_xp": 5,
            "channel": None,
            "default_bg": "https://images2.imgbox.com/6a/73/y1Q0NJPU_o.png",
            "mention": True,
            "xp_color": [54, 132, 181],
            "outline_color": [54, 132, 181],
            "text_color": [255, 255, 255],
            "level_color": [104, 235, 250],
            "rank_color": [242, 190, 96],
            "username_font_size": 56,
            "stats_font_size": 40,
            "labels_font_size": 30,
            "level_roles": {},  # { "level": role_id }
            "enabled": True
        }

        default_user = {
            "bg": None
        }

        default_member = {
            "xp": 0,
            "level": 0
        }

        self.config.register_guild(**default_guild)
        self.config.register_user(**default_user)
        self.config.register_member(**default_member)

        self.xp_cache = {}
        self.rank_cache = {}
        self.image_cache = {}
        self.level_cache = {}

        self.cache_ttl = 30
        self._autosave_task = None

        if not FONT_PATH.exists():
            print(
                f"[Leveling] WARNING: {FONT_PATH} not found. "
                f"Download Roboto-Black.ttf from https://fonts.google.com/specimen/Roboto "
                f"and place it there, or profile cards will fall back to the default font."
            )

    async def cog_load(self):
        self._autosave_task = self.bot.loop.create_task(self.auto_save_loop())

    async def cog_unload(self):
        if self._autosave_task:
            self._autosave_task.cancel()
        await self.save_all_dirty()

    async def get_level_roles(self, guild: discord.Guild):
        return await self.config.guild(guild).level_roles()

    async def set_level_role(self, guild: discord.Guild, level: int, role_id: int):
        async with self.config.guild(guild).level_roles() as roles:
            roles[str(level)] = role_id

    async def remove_level_role(self, guild: discord.Guild, level: int):
        async with self.config.guild(guild).level_roles() as roles:
            roles.pop(str(level), None)

    async def apply_level_roles(
        self,
        member: discord.Member,
        new_level: int,
        old_level: int
    ):
        roles_map = await self.get_level_roles(member.guild)

        if not roles_map:
            return []

        gained_roles = []

        for lvl_str, role_id in roles_map.items():
            try:
                lvl = int(lvl_str)
            except ValueError:
                continue

            role = member.guild.get_role(role_id)
            if not role:
                continue

            # GIVE roles
            if old_level < lvl <= new_level:
                if role not in member.roles:
                    try:
                        await member.add_roles(
                            role,
                            reason="Level role reward"
                        )
                        gained_roles.append(role.name)
                    except discord.Forbidden:
                        pass

            # REMOVE roles
            elif new_level < lvl:
                if role in member.roles:
                    try:
                        await member.remove_roles(
                            role,
                            reason="Level role removed (level down)"
                        )
                    except discord.Forbidden:
                        pass

        return gained_roles

    # ---------------- XP SYSTEM ---------------- #

    @commands.Cog.listener()
    async def on_message(self, message):
        if not message.guild or message.author.bot:
            return

        guild = message.guild
        user = message.author
        key = (guild.id, user.id)

        # Check whether leveling is enabled in this server.
        # This only affects automatic XP from messages.
        if not await self.config.guild(guild).enabled():
            return

        guild_conf = self.config.guild(guild)

        min_xp = await guild_conf.min_xp()
        max_xp = await guild_conf.max_xp()

        gain = random.randint(min_xp, max_xp)

        if key not in self.xp_cache:
            data = await self.config.member(user).all()
            self.xp_cache[key] = {
                "xp": data["xp"],
                "level": data["level"],
                "dirty": False
            }

        cache = self.xp_cache[key]

        cache["xp"] += gain

        old_level = cache["level"]
        old_xp = cache["xp"]

        leveled_up = self.check_level_ups(cache)

        gained_roles = []
        new_level = cache["level"]
        new_xp = cache["xp"]

        if leveled_up:
            cache["dirty"] = True

            gained_roles = await self.apply_level_roles(
                user,
                new_level,
                old_level
            )

            await self.level_up(
                message,
                user,
                new_level,
                gained_roles,
                xp_override=new_xp,
                old_level=old_level
            )

        cache["dirty"] = True
        self.rank_cache.pop(guild.id, None)

    # ---------------- CACHE ---------------- #

    def get_cached_xp(self, level: int) -> int:
        if level not in self.level_cache:
            self.level_cache[level] = xp_for_level(level)
        return self.level_cache[level]

    def check_level_ups(self, cache) -> bool:
        leveled_up = False

        while cache["xp"] >= self.get_cached_xp(cache["level"] + 1):
            cache["level"] += 1
            leveled_up = True

        return leveled_up

    def check_level_downs(self, cache) -> bool:
        leveled_down = False

        while (
            cache["level"] > 0
            and cache["xp"] < self.get_cached_xp(cache["level"])
        ):
            cache["level"] -= 1
            leveled_down = True

        return leveled_down

    # ---------------- SAVE SYSTEM ---------------- #

    async def save_all_dirty(self):
        for (gid, uid), data in list(self.xp_cache.items()):
            if not data.get("dirty"):
                continue

            guild = self.bot.get_guild(gid)

            if not guild:
                continue

            member = guild.get_member(uid)

            if not member:
                continue

            await self.config.member(member).xp.set(data["xp"])
            await self.config.member(member).level.set(data["level"])

            data["dirty"] = False

    async def auto_save_loop(self):
        await self.bot.wait_until_ready()

        while True:
            await asyncio.sleep(15)
            await self.save_all_dirty()

    # ---------------- IMAGE CACHE ---------------- #

    async def fetch_image(self, url):
        if url in self.image_cache:
            return self.image_cache[url]

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    self.image_cache[url] = data
                    return data

        return None

    # ---------------- RANK SYSTEM ---------------- #

    async def get_rank(self, guild, user_id):
        cache = self.rank_cache.get(guild.id)

        if cache and time.time() - cache["ts"] < self.cache_ttl:
            data = cache["data"]

        else:
            all_members = await self.config.all_members(guild)

            xp_map = {
                uid: mdata["xp"]
                for uid, mdata in all_members.items()
            }

            for (gid, uid), cdata in self.xp_cache.items():
                if gid == guild.id:
                    xp_map[uid] = cdata["xp"]

            data = []

            for uid, xp in xp_map.items():
                member = guild.get_member(uid)

                if member:
                    data.append((uid, xp))

            data.sort(
                key=lambda x: x[1],
                reverse=True
            )

            self.rank_cache[guild.id] = {
                "data": data,
                "ts": time.time()
            }

        for i, (uid, _) in enumerate(data, start=1):
            if uid == user_id:
                return i

        return "Unranked"

    # ---------------- LEVEL UP ---------------- #

    async def level_up(
        self,
        message,
        member,
        level,
        gained_roles=None,
        xp_override=None,
        old_level=None
    ):
        guild = message.guild
        guild_conf = self.config.guild(guild)

        cache = self.xp_cache.get(
            (guild.id, member.id)
        )

        xp = (
            xp_override
            if xp_override is not None
            else (
                cache["xp"]
                if cache
                else await self.config.member(member).xp()
            )
        )

        needed = self.get_cached_xp(level + 1)

        rank = await self.get_rank(
            guild,
            member.id
        )

        img = await self.make_image(
            member,
            level,
            xp,
            needed,
            rank,
            guild
        )

        channel_id = await guild_conf.channel()

        channel = (
            guild.get_channel(channel_id)
            if channel_id
            else message.channel
        )

        mention = await guild_conf.mention()

        role_text = ""

        if gained_roles:
            role_text = (
                f" and gained the "
                f"**{', '.join(gained_roles)}** role"
            )

        text = (
            f"🎉 {member.mention if mention else member.display_name} "
            f"reached **Level {level}**{role_text}!"
        )

        await channel.send(
            content=text,
            file=discord.File(
                img,
                "levelup.png"
            )
        )

    # ---------------- IMAGE CREATION ---------------- #

    async def make_image(
        self,
        member,
        level,
        xp,
        needed,
        rank,
        guild
    ):
        w, h = 900, 300

        img = Image.new(
            "RGB",
            (w, h),
            (25, 25, 25)
        )

        draw = ImageDraw.Draw(img)

        user_conf = self.config.user(member)
        guild_conf = self.config.guild(guild)

        bg_url = (
            await user_conf.bg()
            or await guild_conf.default_bg()
        )

        if bg_url:
            bg_bytes = await self.fetch_image(bg_url)

            if bg_bytes:
                bg = Image.open(
                    io.BytesIO(bg_bytes)
                ).convert("RGB")

                bg = bg.resize((w, h))

                img.paste(bg)

        avatar_size = 220
        avatar_x, avatar_y = 30, 40

        avatar_bytes = await self.fetch_image(
            str(member.display_avatar.url)
        )

        avatar = Image.open(
            io.BytesIO(avatar_bytes)
        ).convert("RGB")

        avatar = avatar.resize(
            (avatar_size, avatar_size)
        )

        mask = Image.new(
            "L",
            (avatar_size * 4, avatar_size * 4),
            0
        )

        ImageDraw.Draw(mask).ellipse(
            (0, 0, avatar_size * 4, avatar_size * 4),
            fill=255
        )

        mask = mask.resize(
            (avatar_size, avatar_size),
            Image.LANCZOS
        )

        img.paste(
            avatar,
            (avatar_x, avatar_y),
            mask
        )

        xp_color = tuple(
            await guild_conf.xp_color()
        )

        outline_color = tuple(
            await guild_conf.outline_color()
        )

        text_color = tuple(
            await guild_conf.text_color()
        )

        level_color = tuple(
            await guild_conf.level_color()
        )

        rank_color = tuple(
            await guild_conf.rank_color()
        )

        username_size = await guild_conf.username_font_size()
        stats_size = await guild_conf.stats_font_size()
        labels_size = await guild_conf.labels_font_size()

        name_font = load_font(username_size)
        stat_font = load_font(stats_size)
        label_font = load_font(labels_size)

        text_x = avatar_x + avatar_size + 30

        top_margin = 15
        name_y = top_margin

        line_spacing = stats_size + 14

        stats_y = name_y + username_size + 5

        label_gap = stats_size - 15

        level_label_y = stats_y
        level_value_y = level_label_y + label_gap

        rank_x = text_x + 170
        rank_label_y = stats_y
        rank_value_y = rank_label_y + label_gap

        xp_label_y = (
            level_value_y
            + stats_size
            + 10
        )

        xp_value_y = (
            xp_label_y
            + label_gap
        )

        bar_y = (
            xp_value_y
            + stats_size
            + 10
        )

        # Username
        draw.text(
            (text_x, name_y),
            member.display_name,
            font=name_font,
            fill=text_color,
            stroke_width=TEXT_STROKE_WIDTH,
            stroke_fill="black",
        )

        # Level
        draw.text(
            (text_x, level_label_y),
            "Level",
            font=label_font,
            fill=level_color,
            stroke_width=TEXT_STROKE_WIDTH,
            stroke_fill="black",
        )

        draw.text(
            (text_x, level_value_y),
            str(level),
            font=stat_font,
            fill=level_color,
            stroke_width=TEXT_STROKE_WIDTH,
            stroke_fill="black",
        )

        # Rank
        draw.text(
            (rank_x, rank_label_y),
            "Rank",
            font=label_font,
            fill=rank_color,
            stroke_width=TEXT_STROKE_WIDTH,
            stroke_fill="black",
        )

        draw.text(
            (rank_x, rank_value_y),
            f"#{rank}",
            font=stat_font,
            fill=rank_color,
            stroke_width=TEXT_STROKE_WIDTH,
            stroke_fill="black",
        )

        # XP
        draw.text(
            (text_x, xp_label_y),
            "XP",
            font=label_font,
            fill=text_color,
            stroke_width=TEXT_STROKE_WIDTH,
            stroke_fill="black",
        )

        draw.text(
            (text_x, xp_value_y),
            f"{xp}/{needed}",
            font=stat_font,
            fill=text_color,
            stroke_width=TEXT_STROKE_WIDTH,
            stroke_fill="black",
        )

        bar_x = text_x
        bar_w = w - bar_x - 250
        bar_h = 32

        radius = bar_h // 2

        bar_y = min(
            bar_y,
            h - bar_h - 12
        )

        progress = (
            min(xp / needed, 1)
            if needed
            else 0
        )

        fill_w = max(
            int(bar_w * progress),
            bar_h
        )

        draw.rounded_rectangle(
            [
                bar_x,
                bar_y,
                bar_x + bar_w,
                bar_y + bar_h
            ],
            radius=radius,
            outline=outline_color,
            width=BAR_OUTLINE_WIDTH,
        )

        if progress > 0:
            draw.rounded_rectangle(
                [
                    bar_x,
                    bar_y,
                    bar_x + fill_w,
                    bar_y + bar_h
                ],
                radius=radius,
                fill=xp_color,
            )

        buffer = io.BytesIO()

        img.save(
            buffer,
            format="PNG"
        )

        buffer.seek(0)

        return buffer

    # ---------------- LEVEL SETTINGS ---------------- #

    @commands.group()
    @commands.mod_or_permissions(manage_messages=True)
    async def levelset(self, ctx):
        """Level system settings"""

    @levelset.command()
    async def enabled(self, ctx, toggle: bool):
        """Enable or disable automatic leveling in this server."""

        await self.config.guild(ctx.guild).enabled.set(toggle)

        if toggle:
            await ctx.send(
                "✅ Leveling system enabled in this server."
            )
        else:
            await ctx.send(
                "⛔ Leveling system disabled in this server. "
                "Existing XP and level data has been preserved."
            )

    @levelset.command()
    async def xp(self, ctx, min_xp: int, max_xp: int):
        await self.config.guild(ctx.guild).min_xp.set(min_xp)
        await self.config.guild(ctx.guild).max_xp.set(max_xp)

        await ctx.send(
            "XP range updated."
        )

    @levelset.command()
    async def channel(self, ctx, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).channel.set(
            channel.id
        )

        await ctx.send(
            f"Level-up channel set to {channel.mention}"
        )

    @levelset.command()
    async def mention(self, ctx, toggle: bool):
        await self.config.guild(ctx.guild).mention.set(
            toggle
        )

        await ctx.send(
            f"Mentions set to {toggle}"
        )

    @levelset.command()
    async def defaultbg(self, ctx, url: str):
        await self.config.guild(ctx.guild).default_bg.set(
            url
        )

        await ctx.send(
            "Default background updated."
        )

    # ---------------- COLOURS ---------------- #

    @levelset.group(
        name="colour",
        invoke_without_command=True
    )
    async def levelset_colour(self, ctx):
        """Change profile card colours. Use: xp / outline / text"""

        await ctx.send_help(ctx.command)

    @levelset_colour.command(name="xp")
    async def colour_xp(
        self,
        ctx,
        r: int,
        g: int,
        b: int
    ):
        """Set the XP bar fill colour. Usage: ]levelset colour xp <r> <g> <b>"""

        if not validate_rgb(r, g, b):
            await ctx.send(
                "RGB values must each be between 0 and 255."
            )
            return

        await self.config.guild(ctx.guild).xp_color.set(
            [r, g, b]
        )

        await ctx.send(
            f"XP bar colour set to ({r}, {g}, {b})."
        )

    @levelset_colour.command(name="outline")
    async def colour_outline(
        self,
        ctx,
        r: int,
        g: int,
        b: int
    ):
        """Set the XP bar outline colour. Usage: ]levelset colour outline <r> <g> <b>"""

        if not validate_rgb(r, g, b):
            await ctx.send(
                "RGB values must each be between 0 and 255."
            )
            return

        await self.config.guild(ctx.guild).outline_color.set(
            [r, g, b]
        )

        await ctx.send(
            f"XP bar outline colour set to ({r}, {g}, {b})."
        )

    @levelset_colour.command(name="text")
    async def colour_text(
        self,
        ctx,
        r: int,
        g: int,
        b: int
    ):
        """Set the profile card text colour. Usage: ]levelset colour text <r> <g> <b>"""

        if not validate_rgb(r, g, b):
            await ctx.send(
                "RGB values must each be between 0 and 255."
            )
            return

        await self.config.guild(ctx.guild).text_color.set(
            [r, g, b]
        )

        await ctx.send(
            f"Text colour set to ({r}, {g}, {b})."
        )

    @levelset_colour.command(name="level")
    async def colour_level(
        self,
        ctx,
        r: int,
        g: int,
        b: int
    ):
        """Set the level text colour. Usage: ]levelset colour level <r> <g> <b>"""

        if not validate_rgb(r, g, b):
            await ctx.send(
                "RGB values must each be between 0 and 255."
            )
            return

        await self.config.guild(ctx.guild).level_color.set(
            [r, g, b]
        )

        await ctx.send(
            f"Level colour set to ({r}, {g}, {b})."
        )

    @levelset_colour.command(name="rank")
    async def colour_rank(
        self,
        ctx,
        r: int,
        g: int,
        b: int
    ):
        """Set the rank text colour. Usage: ]levelset colour rank <r> <g> <b>"""

        if not validate_rgb(r, g, b):
            await ctx.send(
                "RGB values must each be between 0 and 255."
            )
            return

        await self.config.guild(ctx.guild).rank_color.set(
            [r, g, b]
        )

        await ctx.send(
            f"Rank text colour set to ({r}, {g}, {b})."
        )

    # ---------------- FONT SIZES ---------------- #

    @levelset.group(
        name="size",
        invoke_without_command=True
    )
    async def levelset_size(self, ctx):
        """Change profile card font sizes. Use: username / stats"""

        await ctx.send_help(ctx.command)

    @levelset_size.command(name="username")
    async def size_username(
        self,
        ctx,
        size: int
    ):
        """Set the username font size. Usage: ]levelset size username <size>"""

        if not 10 <= size <= 200:
            await ctx.send(
                "Font size must be between 10 and 200."
            )
            return

        await self.config.guild(
            ctx.guild
        ).username_font_size.set(size)

        await ctx.send(
            f"Username font size set to {size}."
        )

    @levelset_size.command(name="stats")
    async def size_stats(
        self,
        ctx,
        size: int
    ):
        """Set the rank/level/XP font size. Usage: ]levelset size stats <size>"""

        if not 10 <= size <= 200:
            await ctx.send(
                "Font size must be between 10 and 200."
            )
            return

        await self.config.guild(
            ctx.guild
        ).stats_font_size.set(size)

        await ctx.send(
            f"Stats font size set to {size}."
        )

    @levelset_size.command(name="labels")
    async def size_labels(
        self,
        ctx,
        size: int
    ):
        """Set the rank/level/XP label font size. Usage: ]levelset size label <size>"""

        if not 10 <= size <= 200:
            await ctx.send(
                "Font size must be between 10 and 200."
            )
            return

        await self.config.guild(
            ctx.guild
        ).labels_font_size.set(size)

        await ctx.send(
            f"Label font size set to {size}."
        )

    # ---------------- XP MANUAL COMMANDS ---------------- #

    @levelset.command()
    async def addxp(
        self,
        ctx,
        member: discord.Member,
        amount: int
    ):
        """Add XP to a user in this server. Usage: ]addxp @user <xp>"""

        if amount < 0:
            await ctx.send(
                "Use `]removexp` to remove XP instead of a negative amount."
            )
            return

        key = (
            ctx.guild.id,
            member.id
        )

        if key not in self.xp_cache:
            data = await self.config.member(member).all()

            self.xp_cache[key] = {
                "xp": data["xp"],
                "level": data["level"],
                "dirty": False
            }

        cache = self.xp_cache[key]

        cache["xp"] += amount
        cache["dirty"] = True

        old_level = cache["level"]

        leveled_up = self.check_level_ups(cache)

        self.rank_cache.pop(
            ctx.guild.id,
            None
        )

        gained_roles = await self.apply_level_roles(
            member,
            cache["level"],
            old_level
        )

        if leveled_up:
            await self.level_up(
                ctx.message,
                member,
                cache["level"],
                gained_roles
            )

        await ctx.send(
            f"Added {amount} XP to {member.display_name}. "
            f"New total: {cache['xp']} XP "
            f"(Level {cache['level']})."
        )

    @levelset.command()
    async def removexp(
        self,
        ctx,
        member: discord.Member,
        amount: int
    ):
        """Remove XP from a user in this server. Usage: ]removexp @user <xp>"""

        if amount < 0:
            await ctx.send(
                "Please provide a positive amount to remove."
            )
            return

        key = (
            ctx.guild.id,
            member.id
        )

        if key not in self.xp_cache:
            data = await self.config.member(member).all()

            self.xp_cache[key] = {
                "xp": data["xp"],
                "level": data["level"],
                "dirty": False
            }

        cache = self.xp_cache[key]

        cache["xp"] = max(
            0,
            cache["xp"] - amount
        )

        cache["dirty"] = True

        old_level = cache["level"]

        self.check_level_downs(cache)

        await self.apply_level_roles(
            member,
            cache["level"],
            old_level
        )

        self.rank_cache.pop(
            ctx.guild.id,
            None
        )

        await ctx.send(
            f"Removed {amount} XP from {member.display_name}. "
            f"New total: {cache['xp']} XP "
            f"(Level {cache['level']})."
        )

    # ---------------- LEVEL ROLES ---------------- #

    @levelset.command()
    async def role(
        self,
        ctx,
        level: int,
        role: discord.Role
    ):
        """Set a role reward for reaching a level"""

        if level < 1:
            await ctx.send(
                "Level must be 1 or higher."
            )
            return

        if role.is_default():
            await ctx.send(
                "You can't assign @everyone."
            )
            return

        async with self.config.guild(
            ctx.guild
        ).level_roles() as roles:
            roles[str(level)] = role.id

        await ctx.send(
            f"Level {level} role set to {role.mention}"
        )

    # ---------------- USER SETTINGS ---------------- #

    @commands.group()
    async def levelerset(self, ctx):
        """Level user settings"""

    @levelerset.command()
    async def setbg(
        self,
        ctx,
        url: str
    ):
        await self.config.user(
            ctx.author
        ).bg.set(url)

        await ctx.send(
            "Background updated."
        )

    # ---------------- PROFILE ---------------- #

    @commands.command()
    async def profile(
        self,
        ctx,
        member: discord.Member = None
    ):
        member = member or ctx.author

        key = (
            ctx.guild.id,
            member.id
        )

        data = self.xp_cache.get(key)

        if not data:
            m = await self.config.member(
                member
            ).all()

            data = {
                "xp": m["xp"],
                "level": m["level"]
            }

        level = data["level"]
        xp = data["xp"]

        needed = self.get_cached_xp(
            level + 1
        )

        rank = await self.get_rank(
            ctx.guild,
            member.id
        )

        img = await self.make_image(
            member,
            level,
            xp,
            needed,
            rank,
            ctx.guild
        )

        await ctx.send(
            file=discord.File(
                img,
                "profile.png"
            )
        )

    # ---------------- LEADERBOARD ---------------- #

    @commands.command()
    async def lvlleaderboard(self, ctx):
        all_members = await self.config.all_members(
            ctx.guild
        )

        xp_map = {
            uid: mdata["xp"]
            for uid, mdata in all_members.items()
        }

        for (gid, uid), cdata in self.xp_cache.items():
            if gid == ctx.guild.id:
                xp_map[uid] = cdata["xp"]

        data = []

        for uid, xp in xp_map.items():
            member = ctx.guild.get_member(uid)

            if member:
                data.append(
                    (
                        member.display_name,
                        xp
                    )
                )

        data.sort(
            key=lambda x: x[1],
            reverse=True
        )

        desc = "\n".join(
            f"**{i+1}.** {name} — {xp} XP"
            for i, (name, xp)
            in enumerate(data[:10])
        )

        embed = discord.Embed(
            title="Leaderboard",
            description=desc
        )

        await ctx.send(
            embed=embed
        )


async def setup(bot):
    await bot.add_cog(Leveling(bot))
