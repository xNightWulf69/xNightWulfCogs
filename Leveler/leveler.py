import discord
from redbot.core import commands, Config
import random
import aiohttp
import io
import math
import asyncio
import time
from PIL import Image, ImageDraw


def xp_for_level(level: int) -> int:
    return int(100 * (level ** 1.5))


class Leveling(commands.Cog):
    """Advanced leveling system with caching + image cards"""

    def __init__(self, bot):
        self.bot = bot

        self.config = Config.get_conf(self, identifier=1234567890)

        default_guild = {
            "min_xp": 5,
            "max_xp": 15,
            "channel": None,
            "default_bg": None,
            "mention": True
        }

        default_user = {
            "xp": 0,
            "level": 0,
            "bg": None
        }

        self.config.register_guild(**default_guild)
        self.config.register_user(**default_user)

        # ---------------- CACHE ---------------- #
        self.xp_cache = {}       # uid -> {xp, level, dirty}
        self.rank_cache = {}     # gid -> {data, ts}
        self.image_cache = {}    # url -> bytes
        self.level_cache = {}    # level -> xp needed

        self.cache_ttl = 30

    # ---------------- READY ---------------- #

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.loop.create_task(self.auto_save_loop())

    # ---------------- XP SYSTEM ---------------- #

    @commands.Cog.listener()
    async def on_message(self, message):
        if not message.guild or message.author.bot:
            return

        guild = message.guild
        user = message.author
        uid = user.id

        guild_conf = self.config.guild(guild)

        min_xp = await guild_conf.min_xp()
        max_xp = await guild_conf.max_xp()

        gain = random.randint(min_xp, max_xp)

        # load cache
        if uid not in self.xp_cache:
            data = await self.config.user(user).all()
            self.xp_cache[uid] = {
                "xp": data["xp"],
                "level": data["level"],
                "dirty": False
            }

        cache = self.xp_cache[uid]

        cache["xp"] += gain

        needed = self.get_cached_xp(cache["level"] + 1)

        if cache["xp"] >= needed:
            cache["level"] += 1
            cache["dirty"] = True

            await self.level_up(message, user, cache["level"])

        cache["dirty"] = True

    # ---------------- CACHE HELPERS ---------------- #

    def get_cached_xp(self, level: int) -> int:
        if level not in self.level_cache:
            self.level_cache[level] = xp_for_level(level)
        return self.level_cache[level]

    # ---------------- AUTO SAVE ---------------- #

    async def auto_save_loop(self):
        await self.bot.wait_until_ready()

        while True:
            await asyncio.sleep(15)

            for uid, data in list(self.xp_cache.items()):
                if not data.get("dirty"):
                    continue

                user = self.bot.get_user(uid)
                if not user:
                    continue

                await self.config.user(user).xp.set(data["xp"])
                await self.config.user(user).level.set(data["level"])

                data["dirty"] = False

    # ---------------- IMAGE FETCH CACHE ---------------- #

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

    # ---------------- RANK CACHE ---------------- #

    async def get_rank(self, guild, user_id):
        cache = self.rank_cache.get(guild.id)

        if cache and time.time() - cache["ts"] < self.cache_ttl:
            data = cache["data"]
        else:
            all_users = await self.config.all_users()

            data = []
            for uid, udata in all_users.items():
                member = guild.get_member(uid)
                if member:
                    data.append((uid, udata["xp"]))

            data.sort(key=lambda x: x[1], reverse=True)

            self.rank_cache[guild.id] = {
                "data": data,
                "ts": time.time()
            }

        for i, (uid, _) in enumerate(data, start=1):
            if uid == user_id:
                return i

        return "Unranked"

    # ---------------- LEVEL UP ---------------- #

    async def level_up(self, message, member, level):
        guild = message.guild
        guild_conf = self.config.guild(guild)
        user_conf = self.config.user(member)

        data = self.xp_cache.get(member.id)
        xp = data["xp"] if data else (await user_conf.xp())
        needed = self.get_cached_xp(level)

        rank = await self.get_rank(guild, member.id)

        img = await self.make_image(member, level, xp, needed, rank, guild)

        channel_id = await guild_conf.channel()
        channel = guild.get_channel(channel_id) if channel_id else message.channel

        mention = await guild_conf.mention()

        text = f"🎉 {member.mention if mention else member.display_name} reached **Level {level}**!"

        await channel.send(content=text, file=discord.File(img, "levelup.png"))

    # ---------------- IMAGE CREATION ---------------- #

    async def make_image(self, member, level, xp, needed, rank, guild):
        w, h = 900, 300
        img = Image.new("RGB", (w, h), (25, 25, 25))
        draw = ImageDraw.Draw(img)

        user_conf = self.config.user(member)
        guild_conf = self.config.guild(guild)

        bg_url = await user_conf.bg() or await guild_conf.default_bg()

        if bg_url:
            bg_bytes = await self.fetch_image(bg_url)
            if bg_bytes:
                bg = Image.open(io.BytesIO(bg_bytes)).convert("RGB")
                bg = bg.resize((w, h))
                img.paste(bg)

        # avatar
        avatar_bytes = await self.fetch_image(str(member.display_avatar.url))
        avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGB")
        avatar = avatar.resize((180, 180))

        mask = Image.new("L", (180, 180), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 180, 180), fill=255)

        img.paste(avatar, (30, 60), mask)

        # text
        draw.text((240, 60), member.display_name, fill="white")
        draw.text((240, 110), f"Level: {level}", fill="white")
        draw.text((240, 150), f"XP: {xp}/{needed}", fill="white")
        draw.text((240, 190), f"Rank: #{rank}", fill="white")

        # progress bar
        bar_x, bar_y = 240, 240
        bar_w, bar_h = 500, 25

        progress = min(xp / needed, 1)

        draw.rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], outline="white", width=2)
        draw.rectangle([bar_x, bar_y, bar_x + int(bar_w * progress), bar_y + bar_h], fill=(0, 200, 255))

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)

        return buffer

    # ---------------- COMMANDS ---------------- #

    @commands.group()
    @commands.has_permissions(administrator=True)
    async def levelset(self, ctx):
        """Level system settings"""

    @levelset.command()
    async def xp(self, ctx, min_xp: int, max_xp: int):
        await self.config.guild(ctx.guild).min_xp.set(min_xp)
        await self.config.guild(ctx.guild).max_xp.set(max_xp)
        await ctx.send("XP range updated.")

    @levelset.command()
    async def channel(self, ctx, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).channel.set(channel.id)
        await ctx.send(f"Level-up channel set to {channel.mention}")

    @levelset.command()
    async def mention(self, ctx, toggle: bool):
        await self.config.guild(ctx.guild).mention.set(toggle)
        await ctx.send(f"Mentions set to {toggle}")

    @levelset.command()
    async def defaultbg(self, ctx, url: str):
        await self.config.guild(ctx.guild).default_bg.set(url)
        await ctx.send("Default background updated.")

    # ---------------- USER COMMANDS ---------------- #

    @commands.command()
    async def setbg(self, ctx, url: str):
        await self.config.user(ctx.author).bg.set(url)
        await ctx.send("Background updated.")

    @commands.command()
    async def profile(self, ctx, member: discord.Member = None):
        member = member or ctx.author

        data = self.xp_cache.get(member.id)
        if not data:
            u = await self.config.user(member).all()
            data = {"xp": u["xp"], "level": u["level"]}

        level = data["level"]
        xp = data["xp"]
        needed = self.get_cached_xp(level + 1)

        rank = await self.get_rank(ctx.guild, member.id)

        img = await self.make_image(member, level, xp, needed, rank, ctx.guild)

        await ctx.send(file=discord.File(img, "profile.png"))

    @commands.command()
    async def leaderboard(self, ctx):
        all_users = await self.config.all_users()

        data = []
        for uid, udata in all_users.items():
            member = ctx.guild.get_member(uid)
            if member:
                data.append((member.display_name, udata["xp"]))

        data.sort(key=lambda x: x[1], reverse=True)

        desc = "\n".join(
            f"**{i+1}.** {name} — {xp} XP"
            for i, (name, xp) in enumerate(data[:10])
        )

        embed = discord.Embed(title="Leaderboard", description=desc)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Leveling(bot))
