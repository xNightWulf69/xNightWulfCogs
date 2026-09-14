import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

from deep_translator import GoogleTranslator
from langdetect import detect, LangDetectException


class Translate(commands.Cog):
    """Translate messages to English."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=5832147392, force_registration=True)

    @commands.command(name="translate", aliases=["trans"])
    @commands.guild_only()
    async def translate(self, ctx: commands.Context, *, text: str = None):
        """
        Translate text to English.

        Without text, translates the previous message in the channel.

        Examples:
            [p]translate
            [p]translate Bonjour tout le monde
        """

        # If no text was supplied, find the previous message.
        if not text:
            messages = []

            async for message in ctx.channel.history(limit=10):
                # Skip the command message itself and bot messages.
                if message.id == ctx.message.id:
                    continue

                if message.author.bot:
                    continue

                # Ignore empty messages.
                if not message.content.strip():
                    continue

                messages.append(message)

            if not messages:
                await ctx.send("❌ I couldn't find a previous message to translate.")
                return

            message = messages[0]
            text = message.content

            source_display = message.author.display_name

        else:
            source_display = None

        # Don't try to translate ridiculously large messages.
        if len(text) > 5000:
            await ctx.send("❌ That text is too long to translate. Please keep it under 5,000 characters.")
            return

        # Detect language.
        try:
            detected_language = detect(text)
        except LangDetectException:
            await ctx.send("❌ I couldn't detect the language of that text.")
            return
        except Exception:
            await ctx.send("❌ Something went wrong while detecting the language.")
            return

        # Convert common language codes to readable names.
        language_names = {
            "af": "Afrikaans",
            "ar": "Arabic",
            "bg": "Bulgarian",
            "bn": "Bengali",
            "ca": "Catalan",
            "cs": "Czech",
            "cy": "Welsh",
            "da": "Danish",
            "de": "German",
            "el": "Greek",
            "en": "English",
            "es": "Spanish",
            "et": "Estonian",
            "fa": "Persian",
            "fi": "Finnish",
            "fr": "French",
            "ga": "Irish",
            "gu": "Gujarati",
            "he": "Hebrew",
            "hi": "Hindi",
            "hr": "Croatian",
            "hu": "Hungarian",
            "id": "Indonesian",
            "is": "Icelandic",
            "it": "Italian",
            "ja": "Japanese",
            "ka": "Georgian",
            "kn": "Kannada",
            "ko": "Korean",
            "lt": "Lithuanian",
            "lv": "Latvian",
            "mk": "Macedonian",
            "ml": "Malayalam",
            "mr": "Marathi",
            "ne": "Nepali",
            "nl": "Dutch",
            "no": "Norwegian",
            "pa": "Punjabi",
            "pl": "Polish",
            "pt": "Portuguese",
            "ro": "Romanian",
            "ru": "Russian",
            "sk": "Slovak",
            "sl": "Slovenian",
            "so": "Somali",
            "sq": "Albanian",
            "sr": "Serbian",
            "sv": "Swedish",
            "sw": "Swahili",
            "ta": "Tamil",
            "te": "Telugu",
            "th": "Thai",
            "tl": "Tagalog",
            "tr": "Turkish",
            "uk": "Ukrainian",
            "ur": "Urdu",
            "vi": "Vietnamese",
            "zh-cn": "Chinese",
            "zh-tw": "Chinese",
        }

        language_name = language_names.get(
            detected_language,
            detected_language.upper()
        )

        # Already English.
        if detected_language == "en":
            await ctx.send(
                f"🇬🇧 **Detected language:** English\n\n"
                f"> {text}"
            )
            return

        # Translate.
        try:
            translator = GoogleTranslator(
                source="auto",
                target="en"
            )

            translated = translator.translate(text)

        except Exception as e:
            await ctx.send(
                "❌ I couldn't translate that message right now. "
                "The translation service may be unavailable."
            )
            return

        # Build the response.
        embed = discord.Embed(
            title="🌐 Translation",
            color=discord.Color.blurple()
        )

        embed.add_field(
            name="Detected Language",
            value=language_name,
            inline=True
        )

        embed.add_field(
            name="Translated To",
            value="🇬🇧 English",
            inline=True
        )

        if source_display:
            embed.description = (
                f"**Original message from {source_display}:**\n"
                f"> {text[:1000]}\n\n"
                f"**English:**\n"
                f"> {translated[:2000]}"
            )
        else:
            embed.description = (
                f"**Original:**\n"
                f"> {text[:1000]}\n\n"
                f"**English:**\n"
                f"> {translated[:2000]}"
            )

        embed.set_footer(
            text=f"Language detected: {language_name}"
        )

        await ctx.send(embed=embed)
