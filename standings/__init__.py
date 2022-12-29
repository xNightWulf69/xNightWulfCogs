from .standings import Standings

def setup(bot):
    bot.add_cog(Standings(bot))