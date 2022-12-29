import discord
from redbot.core import commands

class Ballchasing(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def ballchasinglink(self, ctx, *, player_name: str):
        # Replace spaces in the player name with %20
        player_name = player_name.replace(" ", "%20")
    
        # If the player name is not specified, send a message asking for a player name
        if not player_name:
            await ctx.send("Please specify a player name")
            return
    
        # Otherwise, send a message with the link to the Ballchasing website
        link = f"https://ballchasing.com/?player-name={player_name}"
        await ctx.send(link)
