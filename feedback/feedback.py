import discord
from redbot.core import commands, checks, Config

class Feedback(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "feedback_channel": None
        }
        self.config.register_guild(**default_guild)
    
    @commands.command()
    async def feedback(self, ctx, *, message: str):
        """Sends a feedback message to the specified channel"""
        feedback_channel = await self.config.guild(ctx.guild).feedback_channel()
        if feedback_channel is None:
            await ctx.send("The feedback channel has not been set. Please use the `setfeedbackchannel` command to set the channel.")
            return
        channel = self.bot.get_channel(feedback_channel)
        if channel is None:
            await ctx.send("The feedback channel could not be found. Please use the `setfeedbackchannel` command to set the channel.")
            return
        
        # Create the embed with the user's message, a field showing the message sender's mention, and the specified color and footer
        embed = discord.Embed(description=message, color=16773632)
        embed.set_author(name=str(ctx.author), icon_url=ctx.author.avatar_url)
        embed.set_footer(text="The Swarm", icon_url="https://cdn.discordapp.com/attachments/1058010972138254348/1058012219390046288/IMG_0038.png")
        
        # Send the embed to the feedback channel and add a tick and cross reaction
        try:
            msg = await channel.send(embed=embed)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            await ctx.send("Thanks for the feedback!")
        except:
            await ctx.send("Something went wrong, please try again.")
    
    @commands.command()
    @checks.admin_or_permissions(manage_channels=True)
    async def setfeedbackchannel(self, ctx, channel: discord.TextChannel):
        """Sets the channel for feedback messages"""
        await self.config.guild(ctx.guild).feedback_channel.set(channel.id)
        await ctx.send(f"Feedback channel set to {channel.mention}")