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

        embed = discord.Embed(title="The Swarm Staff", color=16772096, thumbnail_url="https://cdn.discordapp.com/attachments/1058010972138254348/1058012219390046288/IMG_0038.png")
        for member in members:
            embed.add_field(name="<:swarm:1035174263147208805> Staff Member", value=member.mention, inline=True)
        embed.set_footer(text=f"<:swarm:1035174263147208805> Total staff members: {len(members)}")
        await ctx.send(embed=embed)