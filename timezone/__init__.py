from .timezone import TimeZoneModule


async def setup(bot):
    cog = TimeZoneModule(bot)
    bot.add_cog(cog)