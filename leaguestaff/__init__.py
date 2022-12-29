from .leaguestaff import LeagueStaff

def setup(bot):
    bot.add_cog(LeagueStaff(bot))