from .translate import Translate


async def setup(bot):
    await bot.add_cog(Translate(bot))
