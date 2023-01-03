import discord
from redbot.core import Config, commands

# Create a new Config instance for storing team information
team_config = Config.get_conf(None, identifier=1234567890, force_registration=True)

# Define the team Config subgroup
team_config.register_guild(
    name='',
    general_manager=None,
    players=[]
)

class TeamModule(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.team_config = team_config

    @commands.command()
    @commands.has_role(1025216358117544037)
    async def create_team(self, ctx, general_manager: discord.Member, *, name: str):
        """Creates a new team with the given name and the specified user as the general manager."""
        # Check if the general manager is already on a team
        if await team_config.guild(general_manager.guild).general_manager() is not None or general_manager.id in await team_config.guild(general_manager.guild).players():
            await ctx.send(f'{general_manager.mention} is already on a team.')
            return

        # Create the new team
        await team_config.guild(ctx.guild).name.set(name)
        await team_config.guild(ctx.guild).general_manager.set(general_manager.id)
        await team_config.guild(ctx.guild).players.set([])

        await ctx.send(f'{ctx.author.mention} has created the team "{name}" and has set {general_manager.mention} as the general manager.')

    @commands.command()
    @commands.has_role(1028690403022606377)
    async def gminvite(self, ctx, player: discord.Member):
        """Invites a player to join the team."""
        # Check if the player is already on a team
        if player.team is not None:
            await ctx.send(f'{player.mention} is already on a team.')
            return

        # Check if the player is a free agent
        if not hasattr(player, 'free_agent'):
            await ctx.send(f'{player.mention} is not a registered free agent.')
            return

        # Create the embed message
        embed = discord.Embed(title=f'{ctx.author.team.name} offers {player.mention} a roster spot',
                              description='React with the tick to accept the invite or the cross to decline.')
        message = await player.send(embed=embed)

        # Add the reactions
        await message.add_reaction('✅')
        await message.add_reaction('❌')

        # Wait for the player to react
        def check(reaction, user):
            return user == player and str(reaction.emoji) in ('✅', '❌')
        reaction, _ = await self.bot.wait_for('reaction_add', check=check)

        # Handle the reaction
        if str(reaction.emoji) == '✅':
            # Add the player to the team
            player.team = ctx.author.team
            player.save()
            await message.edit(embed=discord.Embed(title='Invite accepted', description=f'{player.mention} has joined {ctx.author.team.name}.'))
        else:
            # Decline the invite
            await message.edit(embed=discord.Embed(title='Invite declined', description=f'{player.mention} has declined the invite.'))

    @commands.command()
    @commands.has_any_role(1025216358117544037, 1028690403022606377)
    async def kick(self, ctx, player: discord.Member):
        """Kicks a player from the team."""
        # Check if the player is on the same team as the general manager or has the 1025216358117544037 role
        if player.team != ctx.author.team and not ctx.author.has_role(1025216358117544037):
            await ctx.send(f'{player.mention} is not on your team.')
            return

        # Remove the player from the team
        player.team = None
        player.save()

        await ctx.send(f'{player.mention} has been kicked from the team.')

    @commands.command()
    async def register(self, ctx, mmr: int, tracker: str):
        """Registers as a free agent with the given MMR and tracker link."""
        # Check if the user is already on a team
        if ctx.author.team is not None:
            await ctx.send(f'{ctx.author.mention} is already on a team.')
            return

        # Create a new free agent
        free_agent = FreeAgent(user=ctx.author, mmr=mmr, tracker=tracker)
        free_agent.save()

        # Send a message in the current channel
        await ctx.send(f'{ctx.author.mention} has been registered as a free agent with an MMR of {free_agent.mmr} and tracker link {free_agent.tracker}.')

        # Send the user's information to the specified channel
        channel = self.bot.get_channel(1059726875527762012)
        embed = discord.Embed(title=f'New free agent: {ctx.author.name}',
                              description=f'MMR: {free_agent.mmr}\nTracker: {free_agent.tracker}')
        await channel.send(embed=embed)

    @commands.command()
    async def team_info(self, ctx, *, name: str):
        """Displays the general manager and players of the given team, along with each player's MMR."""
        # Get the team
        try:
            team = Team.objects.get(name=name)
        except Team.DoesNotExist:
            await ctx.send(f'Team "{name}" does not exist.')
            return

        # Create the embed message
        embed = discord.Embed(title=f'{team.name} Team Information',
                              description=f'General Manager: {team.general_manager.mention}')
        for player in team.players:
            embed.add_field(name=player.name, value=f'MMR: {player.free_agent.mmr / 100}')

        await ctx.send(embed=embed)

class Team:
    def __init__(self, name, general_manager, players=[]):
        self.name = name
        self.general_manager = general_manager
        self.players = players