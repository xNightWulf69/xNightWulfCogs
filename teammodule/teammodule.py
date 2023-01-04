import discord
import asyncio
from redbot.core import Config, commands

# Create a new Config instance for storing team information
team_config = Config.get_conf(None, identifier=1234567890, force_registration=True)
team_config.register_guild(
    teams={}
)
free_agents_config = Config.get_conf(None, identifier=9876543210, force_registration=True)
free_agents_config.register_guild(
    free_agents={}
)
class TeamModule(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.team_config = team_config
        self.free_agents_config = free_agents_config

    @commands.command()
    @commands.has_role(1025216358117544037)
    async def create_team(self, ctx, general_manager: discord.Member, *, team_name: str):
        # Retrieve the list of teams from the Config
        teams = await team_config.guild(ctx.guild).teams()
        if team_name in teams:
            await ctx.send("That team name already exists.")
            return
        else:
            teams[team_name] = {"GM": general_manager.id, "players": {}, "subplayers": {}}
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
            subplayers = teams[team_name]["subplayers"]
            embed.add_field(name="General Manager", value=gmid.mention, inline=False)
            for player in players:
                playerid = self.bot.get_user(int(player))
                embed.add_field(name="Player", value=playerid.mention + " " + f'Salary: {teams[team_name]["players"][player]["mmr"] / 100}', inline=False)
            for player in subplayers:
                playerid = self.bot.get_user(int(player))
                embed.add_field(name="Sub", value=playerid.mention, inline=False)
            current_mmr = sum([p["mmr"] for p in players.values()]) // 100
            remaining_mmr = 46 - current_mmr
            embed.add_field(name="Remaining Salary", value=remaining_mmr)
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
        free_agents = await free_agents_config.guild(ctx.guild).free_agents()

        # Check if the player being invited is the general manager of the team
        if player.id == team["GM"]:
            return await ctx.send("The general manager can't be invited to their own team.")

        # Check if the inviter is the general manager of the team
        if ctx.author.id != team["GM"]:
            return await ctx.send("Only the general manager can invite players to the team.")

        # Check if the player being invited is a registered free agent
        if f"{player.id}" not in free_agents:
            return await ctx.send("That player is not a registered free agent.")
        # Check if the combined MMR of the current players and the invited player is less than 1000
        team = None
        for t_name, t in teams.items():
            if ctx.author.id == t["GM"]:
                team = t
                break
        if team is not None:
            current_mmr = 0
            for p_id, p in team["players"].items():
                current_mmr += p["mmr"]
            if current_mmr + free_agents[f"{player.id}"]["mmr"] > 4600:
                return await ctx.send("The combined MMR of the current players and the invited player is more than 1000.")
        else:
            return await ctx.send("You are not the general manager of a team.")
        # Create an embed with the invitation message and tick and cross reactions
        embed = discord.Embed(
            title=f'Invitation to join team **{team_name}**',
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
            # Remove the player from the free agents Config
            team["players"][player.id] = {"mmr": free_agents[f"{player.id}"]["mmr"]}
            await team_config.guild(ctx.guild).teams.set(teams)
            del free_agents[f"{player.id}"]
            await free_agents_config.guild(ctx.guild).free_agents.set(free_agents)
            # Add the player to the team
            try:
                remrole = discord.utils.get(ctx.guild.roles, name="Free Agent")
                await player.remove_roles(remrole)
            except:
                await ctx.send("Can't remove the Free Agent role... (You probably didn't have it for some reason)")
            try:
                role = discord.utils.get(ctx.guild.roles, name=team_name)
                await ctx.author.add_roles(role)
            except:
                await ctx.send("Couldn't give you your team role... (Probably becasue it hasn't been made yet)")
            await ctx.send(f'{player.mention} has joined team **{team_name}**.')
        else:
            await ctx.send(f'{player.mention} declined the invitation to join team **{team_name}**.')

    @commands.command()
    async def register(self, ctx, mmr: int, tracker: str):
        # Check if the player is already on a team
        teams = await team_config.guild(ctx.guild).teams()
        for team in teams.values():
            if f"{ctx.author.id}" in team["players"]:
                return await ctx.send("You are already on a team.")
        free_agents = await free_agents_config.guild(ctx.guild).free_agents()
        if f"{ctx.author.id}" in free_agents:
            return await ctx.send("You are already registered as a Free Agent")

        # Add the player to the free agents Config
        free_agents = await free_agents_config.guild(ctx.guild).free_agents()
        free_agents[ctx.author.id] = {"mmr": mmr, "tracker": tracker}
        await free_agents_config.guild(ctx.guild).free_agents.set(free_agents)
        await ctx.send(f'{ctx.author.mention} has been registered as a free agent with MMR {mmr} and tracker {tracker}.')
        channel = self.bot.get_channel(1059726875527762012)
        embed = discord.Embed(
                    title=f'{ctx.author.display_name} has registered!',
                    description=f'{ctx.author.mention} has registered as a free agent with MMR {mmr} and tracker {tracker}',
                    color=discord.Color.green()
                )
        role = discord.utils.get(ctx.guild.roles, name="Free Agent")
        await ctx.author.add_roles(role)
        await channel.send(embed=embed)

    @commands.command()
    @commands.has_permissions(manage_guild=True)
    async def clear_configs(self, ctx):
        # Clear the team Config
        await self.team_config.clear_all_guilds()
        await ctx.send("Team Config cleared.")

        # Clear the free agents Config
        await self.free_agents_config.clear_all_guilds()
        await ctx.send("Free agents Config cleared.")

    @commands.command()
    @commands.has_role(1028435771528581130)
    async def update_mmr(self, ctx, player: discord.Member, mmr: int):
        # Retrieve the list of teams and free agents from the Config
        teams = await team_config.guild(ctx.guild).teams()
        free_agents = await free_agents_config.guild(ctx.guild).free_agents()

        # Check if the player is a registered free agent
        if f"{player.id}" in free_agents:
            # Update the player's MMR in the free agents Config
            free_agents[f"{player.id}"]["mmr"] = mmr
            await free_agents_config.guild(ctx.guild).free_agents.set(free_agents)
            await ctx.send(f'{player.mention} has had their MMR updated to {mmr}.')
            return

        # Check if the player is on a team
        for team in teams.values():
            if f"{player.id}" in team["players"]:
                # Update the player's MMR in the team
                team["players"][f"{player.id}"]["mmr"] = mmr
                await team_config.guild(ctx.guild).teams.set(teams)
                await ctx.send(f'{player.mention} has had their MMR updated to {mmr}.')
                return
        await ctx.send("That player is not a registered free agent or on a team.")

    @commands.command()
    async def leave_team(self, ctx, *, team_name: str):
        # Retrieve the list of teams from the Config
        teams = await team_config.guild(ctx.guild).teams()
        team = teams[team_name]
        player_kicked = False
        if f"{ctx.author.id}" in team["subplayers"]:
            del team["subplayers"][f"{ctx.author.id}"]
            await team_config.guild(ctx.guild).teams.set(teams)
            player_kicked = True
            break
        if f"{ctx.author.id}" in team["players"]:
            del team["players"][f"{ctx.author.id}"]
            await team_config.guild(ctx.guild).teams.set(teams)
            player_kicked = True
            break
        if player_kicked:
            await ctx.send(f'{player.mention} has left their team.')
        else:
            await ctx.send("You are not on a team.")

    @commands.command()
    async def gmkick(self, ctx, player: discord.Member):
        # Retrieve the list of teams from the Config
        teams = await team_config.guild(ctx.guild).teams()
        player_kicked = False
        for team_name, team in teams.items():
            if ctx.author.id == team["GM"]:
                if f"{player.id}" in team["players"]:
                    # Remove the player from the team
                    del team["players"][f"{player.id}"]
                    await team_config.guild(ctx.guild).teams.set(teams)
                    player_kicked = True
                    break
                if f"{player.id}" in team["subplayers"]:
                    # Remove the player from the team
                    del team["subplayers"][f"{player.id}"]
                    await team_config.guild(ctx.guild).teams.set(teams)
                    player_kicked = True
                    break
        if player_kicked:
            await ctx.send(f'{player.mention} has been kicked from their team.')
        else:
            await ctx.send("You are not the general manager of a team or the player is not on a team.")

    @commands.command()
    async def transfer_gm(self, ctx, new_gm: discord.Member):
        # Retrieve the list of teams from the Config
        teams = await team_config.guild(ctx.guild).teams()
        for team_name, team in teams.items():
            if ctx.author.id == team["GM"]:
                # Transfer the GM role and remove the original GM from the team
                team["GM"] = new_gm.id
                if f"{new_gm.id}" in team["players"]:
                    del team["players"][f"{new_gm.id}"]
                await team_config.guild(ctx.guild).teams.set(teams)
                await ctx.send(f'{new_gm.mention} is now the general manager of "{team_name}".')
                return
        await ctx.send("You are not the general manager of a team.")

    @commands.command()
    async def gmsubinvite(self, ctx, player: discord.Member, *, team_name: str):
        # Retrieve the list of teams from the Config
        teams = await team_config.guild(ctx.guild).teams()
        if team_name not in teams:
            return await ctx.send("That team doesn't exist.")
        team = teams[team_name]
        free_agents = await free_agents_config.guild(ctx.guild).free_agents()

        # Check if the player being invited is the general manager of the team
        if player.id == team["GM"]:
            return await ctx.send("The general manager can't be invited to their own team.")

        # Check if the inviter is the general manager of the team
        if ctx.author.id != team["GM"]:
            return await ctx.send("Only the general manager can invite players to the team.")

        # Check if the player being invited is a registered free agent
        if f"{player.id}" not in free_agents:
            return await ctx.send("That player is not a registered free agent.")

        # Check if the invited player's MMR is lower than the lowest MMR player on the team
        team = None
        for t_name, t in teams.items():
            if ctx.author.id == t["GM"]:
                team = t
                break
        if team is not None:
            lowest_mmr = float("inf")
            for p in team["players"].values():
                if p["mmr"] < lowest_mmr:
                    lowest_mmr = p["mmr"]
            if free_agents[f"{player.id}"]["mmr"] > lowest_mmr:
                return await ctx.send("The invited player's MMR is not lower than the lowest MMR player on the team.")
        # Create an embed with the invitation message and tick and cross reactions
        embed = discord.Embed(
            title=f'Invitation to join team **{team_name}** as a sub',
            description=f'{player.mention}, you have been invited to join team **{team_name}** as a sub by {ctx.author.mention}.\n\n'
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
            # Remove the player from the free agents Config
            team["subplayers"][player.id] = {"mmr": free_agents[f"{player.id}"]["mmr"]}
            await team_config.guild(ctx.guild).teams.set(teams)
            del free_agents[f"{player.id}"]
            await free_agents_config.guild(ctx.guild).free_agents.set(free_agents)
            # Add the player to the team
            try:
                remrole = discord.utils.get(ctx.guild.roles, name="Free Agent")
                await player.remove_roles(remrole)
            except:
                await ctx.send("Can't remove the Free Agent role... (You probably didn't have it for some reason)")
            try:
                role = discord.utils.get(ctx.guild.roles, name=team_name)
                await ctx.author.add_roles(role)
            except:
                await ctx.send("Couldn't give you your team role... (Probably becasue it hasn't been made yet)")
            await ctx.send(f'{player.mention} has joined team **{team_name}** as a sub.')
        else:
            await ctx.send(f'{player.mention} declined the invitation to join team **{team_name}** as a sub.')