import asyncio
import datetime
import discord
import pytz
from redbot.core import commands

class TIMEZONE(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    @commands.cooldown(1, 300, commands.BucketType.user)
    async def time(self, ctx, *, timezone: str = None):
        """Shows the current time in the specified timezone, or in the UK and all the USA timezones if no timezone is specified."""
        # Create a list of the default timezones (UK and all the USA timezones)
        timezones = ["Europe/London", "US-Eastern", "US-Central", "US-Mountain", "US-Pacific"]

        if timezone is not None:
            # If a timezone is specified, add it to the list
            timezones.append(timezone)

        # Create an empty embed object
        embed = discord.Embed()

        # Use asyncio to run multiple tasks concurrently
        results = await asyncio.gather(*[self.get_time(tz) for tz in timezones])

        # Add each timezone and its corresponding time as a field to the embed
        for timezone, time in zip(timezones, results):
            embed.add_field(name=timezone, value=time, inline=False)

        # Send the embed to the Discord channel
        await ctx.send(embed=embed)

    async def get_time(self, timezone):
        """Gets the current time in the specified timezone."""
        # Use the pytz module to get the timezone object for the specified timezone
        tz = pytz.timezone(timezone)
        # Use the datetime module to get the current time in the specified timezone
        current_time = datetime.datetime.now(tz=tz)
        # Format the time as a string and return it
        return current_time.strftime('%H:%M:%S')
