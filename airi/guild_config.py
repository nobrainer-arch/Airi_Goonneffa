# airi/guild_config.py
import discord
from discord.ext import commands
import db
from utils import _err, C_INFO

# ── Channel type registry ─────────────────────────────────────────
# key → (db_key, is_multi, display_label)
CHANNEL_TYPES: dict[str, tuple[str, bool, str]] = {
    "bot":      ("bot_channels",     True,  "🤖 Bot channels"),
    "nsfw":     ("nsfw_channels",    True,  "🔞 NSFW channels"),
    "xp":       ("xp_channels",      True,  "⬆️ XP channels"),
    "levelup":  ("levelup_channel",  False, "🎉 Level-up channel"),
    "profile":  ("profile_channel",  False, "🖼️ Profile channel"),
    "market":   ("market_channel",   False, "🏪 Market channel"),
    "court":    ("court_channel",    False, "⚖️ Court channel"),
    "log":      ("log_channel",      False, "📋 Audit log channel"),
    "txn":      ("txn_channel",      False, "💸 Transaction channel"),
    "gacha":    ("gacha_channel",    False, "🎰 Gacha channel"),
    "social":   ("social_channels",  True,  "💕 Social channels"),
    "economy":  ("eco_channels",     True,  "💰 Economy channels"),
    "relationship": ("rel_channels", True,  "💍 Relationship channels"),
    "mediaonly":("media_only",       True,  "📸 Media-only channels"),
    "cards":    ("cards_channel",  False, "🎴 Card market channel"),
}

K_BOT   = "bot_channels"
K_NSFW  = "nsfw_channels"
K_XP    = "xp_channels"
K_LEVELUP = "levelup_channel"
K_PROFILE = "profile_channel"
K_MARKET  = "market_channel"
K_COURT   = "court_channel"
K_LOG     = "log_channel"
K_TXN     = "txn_channel"
K_GACHA   = "gacha_channel"
K_SOCIAL  = "social_channels"
K_ECO     = "eco_channels"
K_REL     = "rel_channels"
K_MEDIA   = "media_only"
K_CARDS   = "cards_channel"
K_JUDGE   = "judge_role"

async def get(gid: int, key: str) -> str:
    row = await db.pool.fetchrow(
        "SELECT value FROM guild_config WHERE guild_id=$1 AND key=$2", gid, key
    )
    return row["value"] if row else ""

async def set_value(gid: int, key: str, value: str):
    await db.pool.execute("""
        INSERT INTO guild_config (guild_id, key, value) VALUES ($1,$2,$3)
        ON CONFLICT (guild_id, key) DO UPDATE SET value=$3
    """, gid, key, value)

async def get_channel(gid: int, key: str) -> int | None:
    v = await get(gid, key)
    try: return int(v) if v else None
    except ValueError: return None

async def get_channels(gid: int, key: str) -> set[int]:
    v = await get(gid, key)
    if not v: return set()
    result = set()
    for p in v.split(","):
        p = p.strip()
        if p.isdigit(): result.add(int(p))
    return result

async def add_channel(gid: int, key: str, ch_id: int):
    chs = await get_channels(gid, key)
    chs.add(ch_id)
    await set_value(gid, key, ",".join(str(c) for c in chs))

async def remove_channel(gid: int, key: str, ch_id: int):
    chs = await get_channels(gid, key)
    chs.discard(ch_id)
    await set_value(gid, key, ",".join(str(c) for c in chs))

async def get_txn_channel(gid: int) -> int | None:
    return await get_channel(gid, K_TXN)

async def get_court_channel(gid: int) -> int | None:
    return await get_channel(gid, K_COURT)

async def get_market_channel(gid: int) -> int | None:
    return await get_channel(gid, K_MARKET)

async def get_gacha_channel(gid: int) -> int | None:
    return await get_channel(gid, K_GACHA)

async def get_log_channel(gid: int) -> int | None:
    return await get_channel(gid, K_LOG)

async def get_cards_channel(gid: int) -> int | None:
    return await get_channel(gid, K_CARDS)

# FIX: was missing — used by goonneffa/moderation.py
async def get_media_channels(gid: int) -> set[int]:
    return await get_channels(gid, K_MEDIA)

async def get_bot_channels(gid: int) -> set[int]:
    return await get_channels(gid, K_BOT)

async def get_nsfw_channels(gid: int) -> set[int]:
    return await get_channels(gid, K_NSFW)


async def is_judge(member: discord.Member) -> bool:
    if member.guild_permissions.administrator: return True
    rid = await get(member.guild.id, K_JUDGE)
    if not rid: return member.guild_permissions.administrator
    try:
        return any(r.id == int(rid) for r in member.roles)
    except Exception:
        return False

async def check_channel(ctx_or_inter, category: str) -> bool:
    """Returns True if command is allowed in this channel, False (+ sends error) if not."""
    if hasattr(ctx_or_inter, "guild"):
        guild = ctx_or_inter.guild
        channel = ctx_or_inter.channel
    else:
        return True

    gid = guild.id
    ch_id = channel.id

    # Map category to config key
    cat_map = {
        "economy":      K_ECO,
        "social":       K_SOCIAL,
        "gacha":        K_GACHA,
        "relationship": K_REL,
        "nsfw":         K_NSFW,
        "bot":          K_BOT,
    }
    key = cat_map.get(category)
    if not key:
        return True

    # Single-channel types
    single_types = {K_GACHA}
    if key in single_types:
        config_ch = await get_channel(gid, key)
        if config_ch and ch_id != config_ch:
            ch = guild.get_channel(config_ch)
            mention = ch.mention if ch else f"<#{config_ch}>"
            await _err(ctx_or_inter, f"This command must be used in {mention}.")
            return False
        return True

    # Multi-channel types
    allowed = await get_channels(gid, key)
    if not allowed:
        return True  # unrestricted if not configured
    if ch_id not in allowed:
        await _err(ctx_or_inter, f"This command isn't allowed here. Check your configured {category} channels.")
        return False
    return True


# ── Config UI ─────────────────────────────────────────────────────
class _TypeActionView(discord.ui.View):
    def __init__(self, guild, author_id, type_key, db_key, is_multi, label):
        super().__init__(timeout=300)
        self._guild    = guild
        self._author   = author_id
        self._type_key = type_key
        self._db_key   = db_key
        self._is_multi = is_multi
        self._label    = label

    def _ok(self, inter): return inter.user.id == self._author and (
        inter.user.guild_permissions.administrator or inter.user.guild_permissions.manage_guild)

    @discord.ui.button(label="➕ Add channel(s)", style=discord.ButtonStyle.success)
    async def add_btn(self, inter: discord.Interaction, btn):
        if not self._ok(inter): return await inter.response.send_message("Not for you.", ephemeral=True)
        sel = discord.ui.ChannelSelect(
            placeholder=f"Pick channels for {self._label}…",
            channel_types=[discord.ChannelType.text],
            min_values=1, max_values=10 if self._is_multi else 1,
        )
        async def cb(i2: discord.Interaction):
            for ch in i2.data["values"]:
                cid = int(ch)
                if self._is_multi: await add_channel(i2.guild_id, self._db_key, cid)
                else: await set_value(i2.guild_id, self._db_key, str(cid))
            for item in v.children: item.disabled = True
            await i2.response.edit_message(
                content=f"✅ Added {len(i2.data['values'])} channel(s) to **{self._label}**.", view=v)
        sel.callback = cb
        class v(discord.ui.View):
            def __init__(self_): super().__init__(timeout=60); self_.add_item(sel)
        await inter.response.send_message(f"Select channels for **{self._label}**:", view=v(), ephemeral=True)

    @discord.ui.button(label="➖ Remove", style=discord.ButtonStyle.danger)
    async def remove_btn(self, inter: discord.Interaction, btn):
        if not self._ok(inter): return await inter.response.send_message("Not for you.", ephemeral=True)
        gid = inter.guild_id
        if self._is_multi:
            chs = await get_channels(gid, self._db_key)
        else:
            c = await get_channel(gid, self._db_key)
            chs = {c} if c else set()
        if not chs:
            return await inter.response.send_message(f"Nothing configured for **{self._label}**.", ephemeral=True)
        opts = [discord.SelectOption(
            label=f"#{inter.guild.get_channel(c).name if inter.guild.get_channel(c) else c}",
            value=str(c)) for c in chs]
        sel2 = discord.ui.Select(placeholder="Select to remove…", options=opts[:25], min_values=1, max_values=min(len(opts),25))
        async def cb2(i2: discord.Interaction):
            for v2 in sel2.values: await remove_channel(gid, self._db_key, int(v2))
            for item in v2cls.children: item.disabled = True
            await i2.response.edit_message(content=f"✅ Removed {len(sel2.values)} channel(s).", view=v2cls)
        sel2.callback = cb2
        class v2cls(discord.ui.View):
            def __init__(self_): super().__init__(timeout=60); self_.add_item(sel2)
        await inter.response.send_message("Select to remove:", view=v2cls(), ephemeral=True)

    @discord.ui.button(label="👁️ Show", style=discord.ButtonStyle.secondary)
    async def show_btn(self, inter: discord.Interaction, btn):
        if not self._ok(inter): return await inter.response.send_message("Not for you.", ephemeral=True)
        if self._is_multi:
            chs = await get_channels(inter.guild_id, self._db_key)
            val = " ".join(f"<#{c}>" for c in chs) if chs else "*all channels (unrestricted)*"
        else:
            c = await get_channel(inter.guild_id, self._db_key)
            val = f"<#{c}>" if c else "*not set*"
        await inter.response.send_message(f"**{self._label}:** {val}", ephemeral=True)

    @discord.ui.button(label="🗑️ Clear", style=discord.ButtonStyle.secondary)
    async def clear_btn(self, inter: discord.Interaction, btn):
        if not self._ok(inter): return await inter.response.send_message("Not for you.", ephemeral=True)
        await set_value(inter.guild_id, self._db_key, "")
        await inter.response.send_message(f"✅ Cleared **{self._label}**.", ephemeral=True)


class GuildConfigCog(commands.Cog, name="GuildConfig"):
    def __init__(self, bot): self.bot = bot

    def _admin(self, ctx):
        return ctx.author.guild_permissions.administrator or ctx.author.guild_permissions.manage_guild

    @commands.hybrid_group(name="config", invoke_without_command=True)
    async def cfg(self, ctx):
        """Open server configuration panel."""
        if not self._admin(ctx): return await _err(ctx, "Need Manage Server permission.")
        opts = [discord.SelectOption(label=v[2], value=k) for k, v in CHANNEL_TYPES.items()]
        sel = discord.ui.Select(placeholder="Select channel type to configure…", options=opts)
        async def sel_cb(inter: discord.Interaction):
            if inter.user.id != ctx.author.id: return await inter.response.send_message("Not for you.", ephemeral=True)
            t_key = inter.data["values"][0]
            db_key, is_multi, label = CHANNEL_TYPES[t_key]
            view = _TypeActionView(inter.guild, ctx.author.id, t_key, db_key, is_multi, label)
            e = discord.Embed(title=f"⚙️ Config — {label}", description="Use the buttons to manage this channel setting.", color=C_INFO)
            await inter.response.edit_message(embed=e, view=view)
        sel.callback = sel_cb
        class V(discord.ui.View):
            def __init__(self_): super().__init__(timeout=300); self_.add_item(sel)
        e = discord.Embed(title="⚙️ Server Config", description="Choose a channel type to configure:", color=C_INFO)
        await ctx.send(embed=e, view=V())

    @cfg.command(name="show")
    async def cfg_show(self, ctx):
        """Show all configured channels."""
        gid = ctx.guild.id
        e = discord.Embed(title=f"⚙️ Config — {ctx.guild.name}", color=C_INFO)
        for t, (key, is_multi, label) in CHANNEL_TYPES.items():
            if is_multi:
                chs = await get_channels(gid, key)
                v = " ".join(f"<#{c}>" for c in chs) if chs else "*all channels*"
            else:
                c = await get_channel(gid, key)
                v = f"<#{c}>" if c else "*not set*"
            e.add_field(name=label, value=v, inline=True)
        rid = await get(gid, K_JUDGE)
        e.add_field(name="⚖️ Judge role", value=f"<@&{rid}>" if rid else "*admins only*", inline=True)
        await ctx.send(embed=e)

    @cfg.command(name="judge")
    async def cfg_judge(self, ctx, role: discord.Role = None):
        """Set the judge role for divorce court."""
        if not self._admin(ctx): return await _err(ctx, "Need Manage Server permission.")
        if role is None:
            class JView(discord.ui.View):
                def __init__(self_): super().__init__(timeout=60)
                @discord.ui.role_select(placeholder="Pick the judge role…")
                async def pick(self_, inter, sel):
                    if inter.user.id != ctx.author.id: return await inter.response.send_message("Not for you.", ephemeral=True)
                    await set_value(inter.guild_id, K_JUDGE, str(sel.values[0].id))
                    for i in self_.children: i.disabled = True
                    await inter.response.edit_message(content=f"✅ Judge role set to {sel.values[0].mention}.", view=self_)
            return await ctx.send("Pick the judge role:", view=JView())
        await set_value(ctx.guild.id, K_JUDGE, str(role.id))
        await ctx.send(f"✅ Judge role → {role.mention}", delete_after=8)
