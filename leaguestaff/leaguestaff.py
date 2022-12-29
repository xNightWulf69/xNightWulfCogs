import discord
from redbot.core import commands

class LeagueStaff(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def leaguestaff(self, ctx):
        role = ctx.guild.get_role(1025216358117544037)
        if role is None:
            await ctx.send("That role does not exist.")
            return

        members = role.members
        if not members:
            await ctx.send("There are no members with that role.")
            return

        embed = discord.Embed(title="The Swarm Staff")
        for member in members:
            embed.add_field(name=":swarm: Staff Member", value=member.mention, inline=True)
        await ctx.send(embed=embed)