import discord
from redbot.core import commands
import requests

class Ballchasing(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def latestgame(self, ctx, *, player_name: str):
        # Make a request to the Ballchasing API to get the latest game for the specified player
        url = f"https://www.ballchasing.com/api/v1/player/{player_name}/latest"
        response = requests.get(url)
        if response.status_code != 200:
            return await ctx.send("Failed to retrieve player stats. Please check the player name and try again.")
        data = response.json()

        # Extract the relevant information from the API response and format it for the Discord message
        game_mode = data["game_mode"]
        goals = data["stats"]["goals"]
        assists = data["stats"]["assists"]
        saves = data["stats"]["saves"]
        shots = data["stats"]["shots"]
        message = f"In their latest {game_mode} game, {player_name} scored {goals} goals, had {assists} assists, made {saves} saves, and took {shots} shots."

        # Create an embed to send the stats in
        embed = discord.Embed(title=f"{player_name}'s latest game stats", description=message, color=discord.Color.blue())
        await ctx.send(embed=embed)