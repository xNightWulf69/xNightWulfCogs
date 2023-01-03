from .teammodule import TeamModule


async def setup(bot):
    cog = TeamModule(bot)
    bot.add_cog(cog)