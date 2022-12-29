import discord
from redbot.core import commands, Config

class Standings(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "teams": {
                "EZ Esports": {"gp": 0, "gw": 0, "gl": 0},
                "Future Esports": {"gp": 0, "gw": 0, "gl": 0},
                "Hawaiian Punch": {"gp": 0, "gw": 0, "gl": 0},
                "Hybrid Esports": {"gp": 0, "gw": 0, "gl": 0},
                "NV": {"gp": 0, "gw": 0, "gl": 0},
                "N0 Sw3at Esports": {"gp": 0, "gw": 0, "gl": 0},
                "Blossom Esports": {"gp": 0, "gw": 0, "gl": 0},
                "Iris Esports": {"gp": 0, "gw": 0, "gl": 0},
                "Assassin Squad": {"gp": 0, "gw": 0, "gl": 0},
                "Rice Farming": {"gp": 0, "gw": 0, "gl": 0},
                "Sting Esports": {"gp": 0, "gw": 0, "gl": 0},
                "Evil Inside": {"gp": 0, "gw": 0, "gl": 0},
                "Rocket Rizzlers": {"gp": 0, "gw": 0, "gl": 0},
                "Blizzard Sneak": {"gp": 0, "gw": 0, "gl": 0},
            }
        }
        self.config.register_guild(**default_guild)
    
    @commands.command(name="standings")
    async def standings(self, ctx):
        teams = await self.config.guild(ctx.guild).teams()
        sorted_teams = sorted(
            teams.items(), key=lambda x: (-x[1]["gw"], x[1]["gp"]), reverse=True
        )
        embed = discord.Embed(title="League Standings", color=discord.Color.blue())
        for team, stats in sorted_teams:
            win_percentage = stats["gw"] / stats["gp"] if stats["gp"] > 0 else 0
            embed.add_field(
                name=team,
                value=f"GP: {stats['gp']} | GW: {stats['gw']} | GL: {stats['gl']} | WP: {win_percentage:.2f}",
                inline=False,
            )
        await ctx.send(embed=embed)
    
    @commands.command(name="updatestandings")
    @commands.has_permissions(manage_guild=True)
    async def update_standings(self, ctx, team: str, gp: int, gw: int, gl: int):
        teams = await self.config.guild(ctx.guild).teams()
        if team not in teams:
            return await ctx.send("Invalid team name.")
        teams[team]["gp"] = gp
        teams[team]["gw"] = gw
        teams[team]["gl"] = gl
        await self.config.guild(ctx.guild).teams.set(teams)
        await ctx.send("Standings updated successfully.")