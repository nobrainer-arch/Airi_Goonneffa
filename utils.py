# utils.py
import json, re, os, discord
import config

DATA_DIR = os.path.dirname(os.path.abspath(__file__))

def data_path(filename: str) -> str:
    return os.path.join(DATA_DIR, filename)

def obfuscated_pattern(word: str) -> str:
    return r'\s*'.join(re.escape(ch) for ch in word)

def load_json(file: str, default):
    try:
        with open(file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def save_json(file: str, data) -> None:
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# ---------- UX Colours ----------
C_SUCCESS  = 0x2ecc71
C_ERROR    = 0xe74c3c
C_INFO     = 0x3498db
C_WARN     = 0xf39c12
C_ECONOMY  = 0xf1c40f
C_SOCIAL   = 0xe91e63
C_MARKET   = 0x1a1a2e
C_REL      = 0xff69b4
C_BUSINESS = 0x27ae60
C_GACHA    = 0x9b59b6
C_DARK     = 0x0d0d0d

# ---------- UX Helpers ----------
async def _err(ctx, msg: str, delete_cmd: bool = True):
    """Short error message that auto-deletes after 10 seconds."""
    if delete_cmd:
        try: await ctx.message.delete()
        except: pass
    await ctx.send(f"❌ {msg}", delete_after=10)

async def _ok(ctx, msg: str, embed: discord.Embed = None):
    """Success response."""
    if embed:
        await ctx.send(embed=embed)
    else:
        await ctx.send(f"✅ {msg}")

async def _info(ctx, msg: str):
    await ctx.send(msg, delete_after=10)

def _embed(title: str, desc: str = None, color: int = C_INFO) -> discord.Embed:
    e = discord.Embed(title=title, color=color)
    if desc: e.description = desc
    return e

# ---------- Shared DB helpers ----------
async def log_action(bot, action, user, reason, channel=None, message_link=None):
    log_channel = bot.get_channel(config.LOG_CHANNEL_ID)
    if not log_channel: return
    is_severe = any(w in action for w in ("Kick", "Ban"))
    e = discord.Embed(
        title=action,
        description=f"**User:** {user.mention}\n**ID:** {user.id}\n**Reason:** {reason}",
        color=C_ERROR if is_severe else C_WARN,
        timestamp=discord.utils.utcnow(),
    )
    if channel: e.add_field(name="Channel", value=channel.mention)
    if message_link: e.add_field(name="Message", value=f"[Jump]({message_link})")
    await log_channel.send(embed=e)

def is_mod(member: discord.Member) -> bool:
    return member.guild_permissions.administrator or member.guild_permissions.manage_messages


# ── Transaction channel log ───────────────────────────────────────
async def log_txn(bot, guild_id: int, action: str,
                  sender, receiver, amount: int, note: str = ""):
    """Post a coin-movement embed to the configured transaction channel.

    sender / receiver: discord.Member, discord.User, or a plain string like 'System'.
    amount: positive = coins gained by receiver, negative = coins lost by sender.
    """
    try:
        from airi.guild_config import get_txn_channel
        txn_ch_id = await get_txn_channel(guild_id)
        if not txn_ch_id:
            return
        ch = bot.get_channel(txn_ch_id)
        if not ch:
            return

        from datetime import datetime
        import discord as _d

        def _name(x):
            if isinstance(x, (_d.Member, _d.User)): return x.mention
            return str(x)

        sign = "+" if amount >= 0 else ""
        color = 0x2ecc71 if amount >= 0 else 0xe74c3c

        e = _d.Embed(
            title=f"💸 {action}",
            color=color,
            timestamp=datetime.utcnow(),
        )
        e.add_field(name="From",   value=_name(sender),              inline=True)
        e.add_field(name="To",     value=_name(receiver),            inline=True)
        e.add_field(name="Amount", value=f"**{sign}{amount:,}** 🪙", inline=True)
        if note:
            e.add_field(name="Note", value=note, inline=False)
        await ch.send(embed=e)
    except Exception as err:
        print(f"log_txn error: {err}")
