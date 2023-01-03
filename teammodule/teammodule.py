import discord
import asyncio
from redbot.core import Config, commands

# Create a new Config instance for storing team information
team_config = Config.get_conf(None, identifier=1234567890, force_registration=True)
team_config.register_guild(
    teams={}
)

class TeamModule(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.team_config = team_config

    @commands.command()
    @commands.has_role(1025216358117544037)
    async def create_team(self, ctx, general_manager: discord.Member, *, team_name: str):
        # Retrieve the list of teams from the Config
        teams = await team_config.guild(ctx.guild).teams()
        if team_name in teams:
            await ctx.send("That team name already exists.")
            return
        else:
            teams[team_name] = {"GM": general_manager.id, "players": {}}
        await team_config.guild(ctx.guild).teams.set(teams)
        # Give the general manager the role
        role = discord.utils.get(ctx.guild.roles, id=1028690403022606377)
        await general_manager.add_roles(role)

        await ctx.send(f'Team "{team_name}" has been created with {general_manager.mention} as the general manager.')

    @commands.command()
    async def team(self, ctx, *, team_name: str):
        # Retrieve the team's general manager and players from the Config
        teams = await team_config.guild(ctx.guild).teams()
        if team_name in teams:
            embed = discord.Embed(title=f"{team_name}")
            gm = teams[team_name]["GM"]
            gmid = self.bot.get_user(int(gm))
            players = teams[team_name]["players"]
            embed.add_field(name="General Manager", value=gmid.mention, inline=False)
            for player in players:
                playerid = self.bot.get_user(int(player))
                embed.add_field(name="Player", value=playerid.mention + " " + f'MMR: {teams[team_name]["players"][player]["mmr"] / 100}', inline=False)
            await ctx.send(embed=embed)
        else:
            return await ctx.send("That team doesn't exist")
    @commands.command()
    async def gminvite(self, ctx, player: discord.Member, *, team_name: str):
        # Retrieve the list of teams from the Config
        teams = await team_config.guild(ctx.guild).teams()
        if team_name not in teams:
            return await ctx.send("That team doesn't exist.")
        team = teams[team_name]

        # Check if the player being invited is the general manager of the team
        if player.id == team["GM"]:
            return await ctx.send("The general manager can't be invited to their own team.")

        # Check if the inviter is the general manager of the team
        if ctx.author.id != team["GM"]:
            return await ctx.send("Only the general manager can invite players to the team.")

        # Create an embed with the invitation message and tick and cross reactions
        embed = discord.Embed(
            title=f'Invitation to join team "{team_name}"',
            description=f'{player.mention}, you have been invited to join team **{team_name}** by {ctx.author.mention}.\n\n'
                        f'React with 🟢 to accept the invitation or 🔴 to decline.',
            color=discord.Color.green()
        )
        message = await ctx.send(embed=embed)
        await message.add_reaction("🟢")
        await message.add_reaction("🔴")

        # Wait for the player to react
        def check(reaction, user):
            return user == player and str(reaction.emoji) in ["🟢", "🔴"]
        try:
            reaction, user = await self.bot.wait_for("reaction_add", check=check, timeout=300.0)
        except asyncio.TimeoutError:
            return await ctx.send("Invitation expired.")

        # Add the player to the team if they reacted with the tick emoji, or send a message if they declined
        if str(reaction.emoji) == "🟢":
            team["players"][player.id] = {"mmr": 0}
            await team_config.guild(ctx.guild).teams.set(teams)
            await ctx.send(f'{player.mention} has joined team **{team_name}**.')
        else:
            await ctx.send(f'{player.mention} declined the invitation to join team **{team_name}**.')