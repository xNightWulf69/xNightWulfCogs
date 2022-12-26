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

        # Set the thumbnail of the embed to an image of a clock
        embed.set_thumbnail(url="https://purepng.com/public/uploads/large/purepng.com-wall-clockclockbelltimewall-clockwhiteblacksquareround-1421526462960il1m2.png")

        # Use asyncio to run multiple tasks concurrently
        results = await asyncio.gather(*[self.get_time(tz) for tz in timezones])

        # Get the current time in the UK
        uk_time = self.get_time("Europe/London")

        # Get the hour of the current time in the UK
        uk_hour = int(uk_time[:2])

        # Set the color of the embed based on the current time in the UK
        if 3 <= uk_hour < 11:
            embed.color = discord.Color.red()
        elif 11 <= uk_hour < 21:
            embed.color = discord.Color.yellow()
        else:
            embed.color = discord.Color.green()

        # Add each timezone and its corresponding time as a field to the embed
        for timezone, time in zip(timezones, results):
            # Look up the user-friendly name for the timezone using the timezone_names dictionary
            name = timezone_names.get(timezone, timezone)
            embed.add_field(name=name, value=time, inline=False)

        # Send the embed to the Discord channel
        await ctx.send(embed=embed)

    async def get_time(self, timezone):
        """Gets the current time in the specified timezone."""
        # Use the pytz module to get the timezone object for the specified timezone
        tz = pytz.timezone(timezone)

        # Get the current time in the specified timezone
        now = datetime.datetime.now(tz)

        # Format the time as a string
        time_str = now.strftime("%I:%M %p %Z")

        return time_str
