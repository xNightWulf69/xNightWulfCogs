from .leveler import Leveler


async def setup(bot):
    await bot.add_cog(Leveler(bot))
