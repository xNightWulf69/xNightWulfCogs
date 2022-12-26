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
        # Create a dictionary mapping timezone names to user-friendly names
        timezone_names = {
            "Europe/London": "UK",
            "America/New_York": "US Eastern",
            "America/Chicago": "US Central",
            "America/Denver": "US Mountain",
            "America/Los_Angeles": "US Pacific",
        }

        # Create a list of the default timezones (Europe/London, America/New_York, America/Chicago, America/Denver, and America/Los_Angeles)
        timezones = ["Europe/London", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"]

        if timezone is not None:
            # If a timezone is specified, add it to the list
            timezones.append(timezone)

        # Create an empty embed object
        embed = discord.Embed()

        # Use asyncio to run multiple tasks concurrently
        results = await asyncio.gather(*[self.get_time(tz) for tz in timezones])

        # Add each timezone and its corresponding time as a field to the embed
        for timezone, time in zip(timezones, results):
            # Look up the user-friendly name for the timezone using the timezone_names dictionary
            name = timezone_names.get(timezone, timezone)
            embed.add_field(name=name, value=time, inline=False)

        # Send the embed to the Discord channel
        await ctx.send(embed=embed)
