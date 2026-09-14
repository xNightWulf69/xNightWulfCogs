import discord
from redbot.core import commands
from redbot.core.bot import Red

from deep_translator import GoogleTranslator
from langdetect import detect, LangDetectException


class Translate(commands.Cog):
    """Translate messages to English."""

    def __init__(self, bot: Red):
        self.bot = bot

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

        # ---------------------------------------------------------
        # Find the previous message if no text was supplied
        # ---------------------------------------------------------

        if not text:
            message = None

            try:
                async for previous in ctx.channel.history(limit=20):
                    # Don't translate the command itself
                    if previous.id == ctx.message.id:
                        continue

                    # Ignore bot messages
                    if previous.author.bot:
                        continue

                    # Ignore empty messages
                    if not previous.content.strip():
                        continue

                    message = previous
                    break

            except discord.Forbidden:
                await ctx.send(
                    "❌ I don't have permission to read message history "
                    "in this channel."
                )
                return

            except discord.HTTPException as e:
                await ctx.send(
                    f"❌ I couldn't read the message history.\n"
                    f"```{type(e).__name__}: {e}```"
                )
                return

            if message is None:
                await ctx.send(
                    "❌ I couldn't find a previous message to translate."
                )
                return

            text = message.content
            source_display = message.author.display_name

        else:
            source_display = None

        # ---------------------------------------------------------
        # Character limit
        # ---------------------------------------------------------

        if len(text) > 5000:
            await ctx.send(
                "❌ That text is too long to translate. "
                "Please keep it under 5,000 characters."
            )
            return

        # ---------------------------------------------------------
        # Detect language
        # ---------------------------------------------------------

        try:
            detected_language = detect(text)

        except LangDetectException:
            await ctx.send(
                "❌ I couldn't detect the language of that text."
            )
            return

        except Exception as e:
            await ctx.send(
                f"❌ Language detection failed.\n"
                f"```{type(e).__name__}: {e}```"
            )
            return

        # ---------------------------------------------------------
        # Language names
        # ---------------------------------------------------------

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
            detected_language.upper(),
        )

        # ---------------------------------------------------------
        # Already English
        # ---------------------------------------------------------

        if detected_language == "en":
            embed = discord.Embed(
                title="🇬🇧 Translation",
                description=(
                    f"**Detected Language:** English\n\n"
                    f"> {text[:4000]}"
                ),
                color=discord.Color.green(),
            )

            if source_display:
                embed.set_footer(
                    text=f"Message from {source_display}"
                )

            await ctx.send(embed=embed)
            return

        # ---------------------------------------------------------
        # Translate using GoogleTranslator
        # ---------------------------------------------------------

        try:
            translator = GoogleTranslator(
                source="auto",
                target="en",
            )

            translated = translator.translate(text)

        except Exception as e:
            await ctx.send(
                "❌ **Translation failed.**\n"
                "The translation service returned an error:\n"
                f"```{type(e).__name__}: {e}```"
            )
            return

        # ---------------------------------------------------------
        # Make sure we actually received a translation
        # ---------------------------------------------------------

        if not translated:
            await ctx.send(
                "❌ The translation service returned an empty response."
            )
            return

        # ---------------------------------------------------------
        # Create response embed
        # ---------------------------------------------------------

        embed = discord.Embed(
            title="🌐 Translation",
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name="Detected Language",
            value=language_name,
            inline=True,
        )

        embed.add_field(
            name="Translated To",
            value="🇬🇧 English",
            inline=True,
        )

        # Discord embeds have a 4096 character description limit.
        original_text = text[:1500]
        translated_text = translated[:2000]

        if source_display:
            embed.description = (
                f"**Original message from {source_display}:**\n"
                f"> {original_text}\n\n"
                f"**English:**\n"
                f"> {translated_text}"
            )
        else:
            embed.description = (
                f"**Original:**\n"
                f"> {original_text}\n\n"
                f"**English:**\n"
                f"> {translated_text}"
            )

        embed.set_footer(
            text=f"Language detected: {language_name}"
        )

        await ctx.send(embed=embed)
