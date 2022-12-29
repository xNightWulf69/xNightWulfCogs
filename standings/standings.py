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
            teams.items(), key=lambda x: (-x[1]["gw"], x[1]["gp"]), reverse=False
        )
        embed = discord.Embed(title="League Standings", color=discord.Color.blue())
        for team, stats in sorted_teams:
            win_percentage = stats["gw"] / stats["gp"] if stats["gp"] > 0 else 0
            embed.add_field(
                name=team,
                value=f"GamesPlayed: {stats['gp']} | GamesWon: {stats['gw']} | GamesLost: {stats['gl']} | WinPercentage: {win_percentage:.2f}",
                inline=False,
            )
        await ctx.send(embed=embed)
    
    @commands.command(name="updatestandings")
    @commands.has_permissions(manage_guild=True)
    async def update_standings(self, ctx, result: str, *, team: str):
        teams = await self.config.guild(ctx.guild).teams()
        if team not in teams:
            return await ctx.send("Invalid team name.")

        if result.lower() == "win":
            teams[team]["gw"] += 1
        elif result.lower() == "loss":
            teams[team]["gl"] += 1
        else:
            return await ctx.send("Invalid result. Must be 'win' or 'loss'.")

        teams[team]["gp"] += 1
        win_percentage = teams[team]["gw"] / teams[team]["gp"] if teams[team]["gp"] > 0 else 0
        teams[team]["wp"] = win_percentage

        await self.config.guild(ctx.guild).teams.set(teams)
        await ctx.send("Standings updated successfully.")