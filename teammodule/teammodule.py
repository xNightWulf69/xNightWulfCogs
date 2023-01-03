import discord
from redbot.core import Config, commands

# Create a new Config instance for storing team information
team_config = Config.get_conf(None, identifier=1234567890, force_registration=True)
free_agents_config = Config.get_conf(None, identifier=12345678910, force_registration=True)
# Define the team Config subgroup
team_config.register_guild(
    name='',
    general_manager=None,
    players=[]
)
free_agents_config.register_guild(**{'free_agents': {}})
class TeamModule(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.team_config = team_config
        self.free_agents_config = free_agents_config

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
        role = discord.utils.get(general_manager.guild.roles, id=1028690403022606377)
        await general_manager.add_roles(role)
        await ctx.send(f'{ctx.author.mention} has created the team "{name}" and has set {general_manager.mention} as the general manager.')

    @commands.command()
    async def leave_team(self, ctx):
        """Leaves the current team."""
        # Check if the user is the general manager
        if ctx.author.id == await self.team_config.guild(ctx.guild).general_manager():
            # Remove the general manager role from the general manager
            role = discord.utils.get(ctx.author.guild.roles, id=1028690403022606377)
            await ctx.author.remove_roles(role)

            # Clear the team's general manager
            await self.team_config.guild(ctx.guild).general_manager.set(None)

            await ctx.send(f'{ctx.author.mention} has left the team.')
        else:
            # Check if the user is a player
            players = await self.team_config.guild(ctx.guild).players()
            if ctx.author.id in players:
                # Remove the player from the player list
                players.remove(ctx.author.id)
                await self.team_config.guild(ctx.guild).players.set(players)

                await ctx.send(f'{ctx.author.mention} has left the team.')
            else:
                await ctx.send(f'{ctx.author.mention} is not on a team.')

    @commands.command()
    async def register(self, ctx, mmr: int, tracker_link: str):
        """Registers the user as a free agent with the given MMR and tracker link."""
        # Check if the user is already on a team
        if await self.team_config.guild(ctx.guild).general_manager() == ctx.author.id or ctx.author.id in await self.team_config.guild(ctx.guild).players():
            await ctx.send(f'{ctx.author.mention} is already on a team.')
            return

        # Register the user as a free agent
        await self.free_agents_config.guild(ctx.guild).free_agents.set_raw(str(ctx.author.id), value={'mmr': mmr, 'tracker_link': tracker_link})

        # Send a message in the channel and send the user's name, MMR, and tracker link to the designated channel
        await ctx.send(f'{ctx.author.mention} has registered as a free agent with MMR {mmr} and tracker link {tracker_link}.')
        channel = self.bot.get_channel(1059726875527762012)
        await channel.send(f'{ctx.author.mention} (MMR: {mmr}, Tracker: {tracker_link})')