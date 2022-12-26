from .timezone import TIMEZONE


async def setup(bot):
    cog = TIMEZONE(bot)
    bot.add_cog(cog)
