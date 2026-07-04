from .twitchclips import TwitchClips


async def setup(bot):
    await bot.add_cog(TwitchClips(bot))
