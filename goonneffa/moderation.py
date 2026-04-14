# goonneffa/moderation.py — Server-wide moderation via DB (not in-memory dicts)
import discord
from discord.ext import commands
import re
from datetime import timedelta, datetime, timezone
import config, bad_words, db
from utils import is_mod

IGNORED_USERS             = config.IGNORED_USERS
TIMEOUT_DURATION          = config.TIMEOUT_DURATION
BADWORD_TIMEOUT_THRESHOLD = config.BADWORD_TIMEOUT_THRESHOLD
GOONNEFFA_COOLDOWN        = config.GOONNEFFA_COOLDOWN

BAD_WORDS  = bad_words.BAD_WORDS
BAD_EMOJIS = bad_words.BAD_EMOJIS

def _obfuscated(w: str) -> str:
    return r'\s*'.join(re.escape(c) for c in w)

_bad = []
for _w in BAD_WORDS:
    if ' ' in _w:
        _bad.append(r'\s+'.join(_obfuscated(p) for p in _w.split()))
    else:
        _bad.append(_obfuscated(_w))

BAD_WORD_REGEX  = re.compile('|'.join(_bad), re.IGNORECASE) if _bad else re.compile(r'(?!)')
GOONNEFFA_REGEX = re.compile(r'g\s*o\s*o\s*n+\s*e?\s*f+\s*a?', re.IGNORECASE)

# Discord invites only — NOT regular URLs / CDN / attachments
INVITE_PATTERN = re.compile(
    r"(https?://)?(?:www\.)?(discord\.gg|discord\.com/invite)/[a-zA-Z0-9]+",
    re.IGNORECASE,
)

# ── DB helpers ─────────────────────────────────────────────────────
async def _get_offense_count(gid: int, uid: int) -> int:
    """Active offenses in the last 7 days, server-wide."""
    return await db.pool.fetchval(
        """SELECT COUNT(*) FROM mod_cases
           WHERE guild_id=$1 AND target_id=$2
           AND action='AutoWarn'
           AND created_at > NOW() - INTERVAL '7 days'""",
        gid, uid,
    ) or 0

async def _add_offense(gid: int, uid: int, reason: str) -> int:
    await db.pool.execute(
        "INSERT INTO mod_cases (guild_id,mod_id,target_id,action,reason) VALUES ($1,0,$2,'AutoWarn',$3)",
        gid, uid, reason,
    )
    return await _get_offense_count(gid, uid)

async def _get_log_channel(bot, gid: int):
    try:
        from airi.guild_config import get_log_channel
        ch_id = await get_log_channel(gid)
        if ch_id:
            return bot.get_channel(ch_id)
    except Exception:
        pass
    return bot.get_channel(config.LOG_CHANNEL_ID)

async def _get_media_channels(gid: int) -> set[int]:
    try:
        from airi.guild_config import get_channels, K_MEDIA
        return await get_channels(gid, K_MEDIA)
    except Exception:
        return set()

def _is_discord_forward(message: discord.Message) -> bool:
    """
    Allow Discord native forward messages through media channels.
    Forwards: have message_reference AND carry attachments/embeds from origin.
    Also allows channel crossposts (auto-publish).
    """
    if message.reference and (message.attachments or message.embeds):
        return True
    if hasattr(message.flags, "crossposted") and message.flags.crossposted:
        return True
    return False


class ModerationCog(commands.Cog, name="ModerationCog"):
    def __init__(self, bot):
        self.bot = bot
        self._goon_cooldown: dict[int, float] = {}  # uid → last gif timestamp

    # ── Goonneffa fun response ─────────────────────────────────────
    async def _handle_gooneffa(self, message: discord.Message):
        uid = message.author.id
        now = datetime.now(timezone.utc).timestamp()
        if now - self._goon_cooldown.get(uid, 0) < GOONNEFFA_COOLDOWN:
            try: await message.delete()
            except Exception: pass
            await message.channel.send(
                f"⏳ {message.author.mention} goonneffa is on cooldown...", delete_after=5
            )
            return
        self._goon_cooldown[uid] = now

        from airi.gif_provider import klipy_search
        try:
            from airi.gender import get_gender
            gender = await get_gender(str(uid))
        except Exception:
            gender = "u"

        query = (
            "anime girl watching"  if gender == "g" else
            "anime boy watching"   if gender == "b" else
            "anime watching stare"
        )
        urls = await klipy_search(query, 5)
        await message.channel.send(f"{message.author.mention} I'm watching you 👀")
        if urls:
            await message.channel.send(urls[0])
        if gender not in ("g", "b"):
            await message.channel.send(
                f"💡 {message.author.mention} use `!gender` with Airi to personalise!", delete_after=12
            )

    # ── Auto-timeout helper ────────────────────────────────────────
    async def _auto_timeout(self, member: discord.Member, reason: str):
        gid = member.guild.id
        try:
            end = discord.utils.utcnow() + timedelta(seconds=TIMEOUT_DURATION)
            await member.timeout(end, reason=reason)
            log_ch = await _get_log_channel(self.bot, gid)
            if log_ch:
                e = discord.Embed(
                    title="🔇 Auto Timeout",
                    description=f"**User:** {member.mention} (`{member.id}`)\n**Reason:** {reason}",
                    color=0xe74c3c,
                    timestamp=datetime.now(timezone.utc),
                )
                await log_ch.send(embed=e)
            await db.pool.execute(
                "INSERT INTO mod_cases (guild_id,mod_id,target_id,action,reason,duration) VALUES ($1,0,$2,'AutoTimeout',$3,$4)",
                gid, member.id, reason, f"{TIMEOUT_DURATION}s",
            )
        except Exception as ex:
            print(f"Auto-timeout failed {member}: {ex}")

    # ── Main listener ──────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        content = message.content
        uid     = message.author.id
        gid     = message.guild.id

        # Exempt ignored users (bot IDs) and mods — but still run goonneffa fun
        is_exempt = uid in IGNORED_USERS or is_mod(message.author)
        if is_exempt:
            if GOONNEFFA_REGEX.search(content) and not content.lower().startswith("g!"):
                await self._handle_gooneffa(message)
            return

        # ── 1. Bad words (server-wide, persistent offense counter) ──
        if BAD_WORD_REGEX.search(content) or any(e in content for e in BAD_EMOJIS):
            try: await message.delete()
            except Exception: return
            count = await _add_offense(gid, uid, "Bad word / emoji")
            if count >= BADWORD_TIMEOUT_THRESHOLD:
                await self._auto_timeout(
                    message.author,
                    f"Auto-timeout: {count} bad-word offenses (server-wide)",
                )
            else:
                left = BADWORD_TIMEOUT_THRESHOLD - count
                await message.channel.send(
                    f"⚠️ {message.author.mention} Watch your language! "
                    f"Offense **{count}/{BADWORD_TIMEOUT_THRESHOLD}** "
                    f"— {left} more before auto-timeout.",
                    delete_after=8,
                )
            return

        # ── 2. Discord invite links → kick ──────────────────────────
        if INVITE_PATTERN.search(content):
            try: await message.delete()
            except Exception: return
            log_ch = await _get_log_channel(self.bot, gid)
            try:
                await message.author.kick(reason="Posted Discord invite link")
                if log_ch:
                    e = discord.Embed(
                        title="👢 Auto-Kicked",
                        description=f"**User:** {message.author.mention}\n**Reason:** Discord invite link",
                        color=0xe74c3c,
                        timestamp=datetime.now(timezone.utc),
                    )
                    await log_ch.send(embed=e)
                await db.pool.execute(
                    "INSERT INTO mod_cases (guild_id,mod_id,target_id,action,reason) VALUES ($1,0,$2,'AutoKick',$3)",
                    gid, uid, "Discord invite link",
                )
            except discord.Forbidden:
                await message.channel.send("⚠️ Missing kick permission.", delete_after=5)
            return

        # ── 3. Media-only channels ───────────────────────────────────
        media_chs = await _get_media_channels(gid)
        if message.channel.id in media_chs:
            # ALLOW: has file attachments
            if message.attachments:
                return
            # ALLOW: Discord forward/crosspost (reference with embeds/attachments)
            if _is_discord_forward(message):
                return
            # ALLOW: links that Discord auto-embeds as image/video
            for emb in message.embeds:
                if emb.image or emb.video or emb.thumbnail:
                    return
            # Block pure text
            try:
                await message.delete()
                await message.channel.send(
                    f"{message.author.mention} 📸 This channel is **media-only** — "
                    "please post an image, video, or file!",
                    delete_after=6,
                )
            except Exception:
                pass
            return

        # ── 4. Goonneffa trigger ─────────────────────────────────────
        if GOONNEFFA_REGEX.search(content) and not content.lower().startswith("g!"):
            await self._handle_gooneffa(message)
