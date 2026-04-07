# goonneffa/moderation.py
import discord
from discord.ext import commands
import re
import json
import random
from datetime import timedelta, datetime
import config
import bad_words
from utils import is_mod, log_action, data_path
import db

# Constants from config
IGNORED_USERS             = config.IGNORED_USERS
TIMEOUT_DURATION          = config.TIMEOUT_DURATION
BADWORD_TIMEOUT_THRESHOLD = config.BADWORD_TIMEOUT_THRESHOLD
GOONEFFA_COOLDOWN         = config.GOONNEFFA_COOLDOWN
GIFS_FILE                 = data_path("gifs.json")
BAD_WORDS                 = bad_words.BAD_WORDS
BAD_EMOJIS                = bad_words.BAD_EMOJIS

def _obfuscated(w): return r'\s*'.join(re.escape(c) for c in w)

_bad = []
for _w in BAD_WORDS:
    if ' ' in _w:
        _bad.append(r'\s+'.join(_obfuscated(p) for p in _w.split()))
    else:
        _bad.append(_obfuscated(_w))

BAD_WORD_REGEX = re.compile('|'.join(_bad), re.IGNORECASE)
GOONEFFA_REGEX = re.compile(r'g\s*o\s*o\s*n+\s*e?\s*f+\s*a?', re.IGNORECASE)
# Only Discord invites — NOT regular URLs, CDN links, or attachments
INVITE_PATTERN = re.compile(
    r"(https?://)?(?:www\.)?(discord\.gg|discord\.com/invite)/[a-zA-Z0-9]+",
    re.IGNORECASE,
)

def _load_watch_gifs():
    try:
        with open(GIFS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        wm = data.get("watch_male", []); wf = data.get("watch_female", [])
        wn = wm + wf
        if not wn: raise ValueError("No watch GIFs")
        return wm, wf, wn
    except Exception as e:
        print(f"⚠️ GIF load failed: {e}")
        return [], [], ["https://media.giphy.com/media/3o7abB06u9bNzA8LC8/giphy.gif"]


async def _get_log_channel(bot, guild_id: int):
    """Get per-guild log channel, fall back to global config."""
    try:
        from airi.guild_config import get_log_channel
        ch_id = await get_log_channel(guild_id)
        if ch_id:
            return bot.get_channel(ch_id)
    except Exception:
        pass
    return bot.get_channel(config.LOG_CHANNEL_ID)

async def _get_media_channels(guild_id: int) -> set[int]:
    """Get guild-configurable media-only channels."""
    try:
        from airi.guild_config import get_media_channels
        return await get_media_channels(guild_id)
    except Exception:
        return set()


class ModerationCog(commands.Cog, name="ModerationCog"):
    def __init__(self, bot):
        self.bot = bot
        self.badword_offenses:   dict[int, int]   = {}
        self.gooneffa_last_used: dict[int, float] = {}
        self.watch_male, self.watch_female, self.watch_neutral = _load_watch_gifs()

    async def _handle_gooneffa(self, message):
        uid = message.author.id
        now = datetime.utcnow().timestamp()
        if now - self.gooneffa_last_used.get(uid, 0) < GOONEFFA_COOLDOWN:
            try: await message.delete()
            except: pass
            await message.channel.send(f"⏳ {message.author.mention} goonneffa is cooling down...", delete_after=5)
            return
        self.gooneffa_last_used[uid] = now
        from airi.gender import get_gender
        gender = await get_gender(str(uid))
        if gender == "g":    pool = self.watch_female or self.watch_neutral
        elif gender == "b":  pool = self.watch_male   or self.watch_neutral
        else:                pool = self.watch_neutral or ["https://media.giphy.com/media/3o7abB06u9bNzA8LC8/giphy.gif"]
        await message.channel.send(f"{message.author.mention} I'm watching you 👀")
        await message.channel.send(random.choice(pool))
        if gender not in ("g","b"):
            await message.channel.send(f"💡 {message.author.mention} use `!gender` with Airi to set your gender!")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot: return
        content = message.content
        uid     = message.author.id

        if uid not in IGNORED_USERS and not is_mod(message.author):
            # Bad words first — prevents "goonneffa + bad word" getting a GIF response
            if BAD_WORD_REGEX.search(content) or any(e in content for e in BAD_EMOJIS):
                try: await message.delete()
                except: return
                offenses = self.badword_offenses.get(uid, 0) + 1
                self.badword_offenses[uid] = offenses
                if offenses >= BADWORD_TIMEOUT_THRESHOLD:
                    try:
                        end = discord.utils.utcnow() + timedelta(seconds=TIMEOUT_DURATION)
                        await message.author.timeout(end, reason="Repeated violations")
                        log_ch = await _get_log_channel(self.bot, message.guild.id)
                        if log_ch:
                            e = discord.Embed(title="Timeout", color=0xe74c3c)
                            e.description = f"**User:** {message.author.mention}\n**Reason:** {offenses} offenses"
                            await log_ch.send(embed=e)
                        self.badword_offenses[uid] = 0
                    except Exception as ex:
                        print(f"Timeout failed: {ex}")
                else:
                    await message.channel.send(
                        f"{message.author.mention} warning ({offenses}/{BADWORD_TIMEOUT_THRESHOLD}) 🚫",
                        delete_after=8
                    )
                return

            # Discord invites ONLY — not regular URLs or attachments
            if INVITE_PATTERN.search(content):
                try: await message.delete()
                except: return
                try:
                    await message.author.kick(reason="Discord invite link")
                    log_ch = await _get_log_channel(self.bot, message.guild.id)
                    if log_ch:
                        e = discord.Embed(title="Kicked", color=0xe74c3c,
                            description=f"**User:** {message.author.mention}\n**Reason:** Discord invite link")
                        await log_ch.send(embed=e)
                except Exception as ex:
                    await message.channel.send("Missing kick permissions", delete_after=5)
                return

            # Guild-configurable media-only channels
            media_chs = await _get_media_channels(message.guild.id)
            if message.channel.id in media_chs:
                if not message.attachments:
                    # Pure text with no attachment — delete
                    try:
                        await message.delete()
                        await message.channel.send(
                            f"{message.author.mention} media only here 🚫", delete_after=5
                        )
                    except: pass
                return

        # Gooneffa trigger — only reached if message passed moderation
        if GOONEFFA_REGEX.search(content) and not content.lower().startswith("!goonneffa"):
            await self._handle_gooneffa(message)
