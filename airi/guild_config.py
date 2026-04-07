# airi/guild_config.py
import discord
from discord.ext import commands
import db
from utils import _err, C_INFO

# ── Category channel rules ────────────────────────────────────────
# "everywhere"  = no restriction
# "bot|nsfw"    = bot or nsfw channels (or all if none configured)
# "nsfw_only"   = only nsfw channels
# "xp"          = xp channels only (or all if none configured)
# "setup_lock"  = locked until !setup is done (only xp + bot work by default)
CAT_RULES = {
    "mod":          "everywhere",
    "sfw_gif":      "bot|nsfw",
    "nsfw_gif":     "nsfw_only",
    "economy":      "setup_lock",
    "social":       "setup_lock",
    "relationship": "setup_lock",
    "market":       "setup_lock",
    "gacha":        "setup_lock",
    "business":     "setup_lock",
    "xp":           "xp",
}

K_BOT     = "bot_channels"
K_NSFW    = "nsfw_channels"
K_XP      = "xp_channels"
K_LEVELUP = "levelup_channel"
K_PROFILE = "profile_channel"
K_MARKET  = "market_channel"
K_COURT   = "court_channel"
K_LOG     = "log_channel"
K_MEDIA   = "media_only_channels"
K_JUDGE   = "judge_role"
K_TXN     = "transaction_channel"
K_GACHA   = "gacha_channel"

_cache: dict[int, dict] = {}

async def _load(gid: int) -> dict:
    if gid in _cache: return _cache[gid]
    rows = await db.pool.fetch("SELECT key, value FROM guild_config WHERE guild_id=$1", gid)
    cfg = {r["key"]: r["value"] for r in rows}
    _cache[gid] = cfg
    return cfg

def _inv(gid): _cache.pop(gid, None)

async def get(gid, key): return (await _load(gid)).get(key)

async def set_value(gid, key, value):
    _inv(gid)
    await db.pool.execute("""
        INSERT INTO guild_config (guild_id,key,value) VALUES ($1,$2,$3)
        ON CONFLICT (guild_id,key) DO UPDATE SET value=EXCLUDED.value
    """, gid, key, value)

async def get_channel(gid, key):
    v = await get(gid, key); return int(v) if v else None

async def get_channels(gid, key) -> set[int]:
    v = await get(gid, key)
    if not v: return set()
    return {int(x) for x in v.split(",") if x.strip()}

async def add_channel(gid, key, ch_id):
    s = await get_channels(gid, key); s.add(ch_id)
    await set_value(gid, key, ",".join(str(c) for c in s))

async def remove_channel(gid, key, ch_id):
    s = await get_channels(gid, key); s.discard(ch_id)
    await set_value(gid, key, ",".join(str(c) for c in s))

async def is_setup_done(gid: int) -> bool:
    row = await db.pool.fetchrow(
        "SELECT setup_done FROM guild_setup WHERE guild_id=$1", gid
    )
    return bool(row and row["setup_done"])

async def check_channel(ctx, category: str) -> bool:
    if not ctx.guild: return True
    gid, cid = ctx.guild.id, ctx.channel.id
    rule = CAT_RULES.get(category, "bot|nsfw")

    if rule == "everywhere": return True

    # setup_lock: locked until setup is done; treats channel as "bot|nsfw" after
    if rule == "setup_lock":
        if not await is_setup_done(gid):
            await _err(ctx,
                "⚙️ This server hasn't been configured yet. "
                "An admin needs to run `!setup` first."
            )
            return False
        rule = "bot|nsfw"  # fall through to normal check

    bot_chs  = await get_channels(gid, K_BOT)
    nsfw_chs = await get_channels(gid, K_NSFW)
    xp_chs   = await get_channels(gid, K_XP)

    if rule == "xp":
        return not xp_chs or cid in xp_chs

    if rule == "nsfw_only":
        if not nsfw_chs or cid in nsfw_chs: return True
        nsfw_list = " ".join(f"<#{c}>" for c in nsfw_chs) if nsfw_chs else "a NSFW channel"
        await _err(ctx, f"🔞 NSFW commands are only allowed in {nsfw_list}")
        return False

    allowed = bot_chs | nsfw_chs
    if not allowed: return True
    if cid in allowed: return True
    ch_list = " ".join(f"<#{c}>" for c in allowed)
    await _err(ctx, f"Use this command in {ch_list}")
    return False

async def get_levelup_channel(gid):  return await get_channel(gid, K_LEVELUP)
async def get_profile_channel(gid):  return await get_channel(gid, K_PROFILE)
async def get_market_channel(gid):   return await get_channel(gid, K_MARKET)
async def get_court_channel(gid):    return await get_channel(gid, K_COURT)
async def get_log_channel(gid):      return await get_channel(gid, K_LOG)
async def get_txn_channel(gid):      return await get_channel(gid, K_TXN)
async def get_gacha_channel(gid):    return await get_channel(gid, K_GACHA)
async def get_media_channels(gid):   return await get_channels(gid, K_MEDIA)

async def is_media_only(gid, cid):
    chs = await get_media_channels(gid); return cid in chs

async def is_judge(member: discord.Member) -> bool:
    v = await get(member.guild.id, K_JUDGE)
    if not v: return member.guild_permissions.administrator
    return any(r.id == int(v) for r in member.roles)

CHANNEL_TYPES = {
    "bot":      (K_BOT,     True,  "🤖 Bot commands"),
    "nsfw":     (K_NSFW,    True,  "🔞 NSFW commands"),
    "xp":       (K_XP,      True,  "⬆️ XP gain"),
    "levelup":  (K_LEVELUP, False, "📣 Level-up announcements"),
    "profile":  (K_PROFILE, False, "👤 Profile/rank output"),
    "market":   (K_MARKET,  False, "🏪 Auction House"),
    "court":    (K_COURT,   False, "⚖️ Divorce court"),
    "log":      (K_LOG,     False, "📋 Mod/bot logs"),
    "txn":      (K_TXN,     False, "💸 Transaction log"),
    "gacha":    (K_GACHA,   False, "🎰 Gacha channel"),
    "mediaonly":(K_MEDIA,   True,  "📸 Media-only channels"),
}


class _ConfigView(discord.ui.View):
    """Main !config panel — dropdown to choose type, then action buttons."""

    def __init__(self, guild: discord.Guild, author_id: int):
        super().__init__(timeout=300)
        self._guild    = guild
        self._author   = author_id
        self._type_key: str | None = None   # currently selected CHANNEL_TYPES key

        # Build type select
        opts = [
            discord.SelectOption(label=f"{label}", value=key, description=key)
            for key, (_, _, label) in CHANNEL_TYPES.items()
        ]
        sel = discord.ui.Select(placeholder="Select channel type to configure…", options=opts)
        sel.callback = self._on_type_select
        self.add_item(sel)

    async def _check_admin(self, inter: discord.Interaction) -> bool:
        ok = inter.user.guild_permissions.administrator or inter.user.guild_permissions.manage_guild
        if not ok:
            await inter.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
        return ok

    async def _on_type_select(self, interaction: discord.Interaction):
        if not await self._check_admin(interaction): return
        self._type_key = interaction.data["values"][0]
        key, is_multi, label = CHANNEL_TYPES[self._type_key]

        # Show current value
        gid = interaction.guild_id
        if is_multi:
            chs = await get_channels(gid, key)
            cur = " ".join(f"<#{c}>" for c in chs) if chs else "*all channels (unrestricted)*"
        else:
            ch = await get_channel(gid, key)
            cur = f"<#{ch}>" if ch else "*not set*"

        embed = discord.Embed(
            title=f"⚙️ Config — {label}",
            description=f"**Current:** {cur}\n\nUse the buttons below to change it.",
            color=0x3498db,
        )
        # Build action view
        view = _TypeActionView(self._guild, self._author, self._type_key, key, is_multi, label)
        await interaction.response.edit_message(embed=embed, view=view)


class _TypeActionView(discord.ui.View):
    """Action buttons shown after a type is chosen in _ConfigView."""

    def __init__(self, guild, author_id, type_key, db_key, is_multi, label):
        super().__init__(timeout=300)
        self._guild    = guild
        self._author   = author_id
        self._type_key = type_key
        self._db_key   = db_key
        self._is_multi = is_multi
        self._label    = label

    def _check_author(self, inter: discord.Interaction) -> bool:
        return inter.user.id == self._author and (
            inter.user.guild_permissions.administrator or
            inter.user.guild_permissions.manage_guild
        )

    # ── Add ──────────────────────────────────────────────────────
    @discord.ui.button(label="➕ Add channel(s)", style=discord.ButtonStyle.success)
    async def add_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_author(interaction):
            return await interaction.response.send_message("Not for you.", ephemeral=True)

        if self._type_key == "log" or not self._is_multi:
            # Single-channel types or judge: use a channel select for ONE channel
            view = _ChannelPickView(self._guild, self._author, self._db_key, self._label,
                                    self._is_multi, "add", back_view=self)
            await interaction.response.send_message(
                f"Select the channel to set as **{self._label}**:", view=view, ephemeral=True
            )
        else:
            # Multi-channel type: pick several at once
            view = _BulkChannelPickView(self._guild, self._author, self._db_key, self._label,
                                         "add", back_view=self)
            await interaction.response.send_message(
                f"Select channels to **add** to **{self._label}** (pick up to 10):",
                view=view, ephemeral=True
            )

    # ── Remove ───────────────────────────────────────────────────
    @discord.ui.button(label="➖ Remove channel(s)", style=discord.ButtonStyle.danger)
    async def remove_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_author(interaction):
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        gid = interaction.guild_id

        if self._is_multi:
            chs = await get_channels(gid, self._db_key)
        else:
            ch = await get_channel(gid, self._db_key)
            chs = {ch} if ch else set()

        if not chs:
            return await interaction.response.send_message(
                f"No channels configured for **{self._label}** to remove.", ephemeral=True
            )

        # Build options from currently configured channels
        options = []
        for cid in chs:
            ch_obj = interaction.guild.get_channel(cid)
            name = f"#{ch_obj.name}" if ch_obj else f"<#{cid}> (deleted)"
            options.append(discord.SelectOption(label=name, value=str(cid)))

        class RemoveSelect(discord.ui.Select):
            def __init__(self_):
                super().__init__(
                    placeholder="Select channel(s) to remove…",
                    options=options[:25],
                    min_values=1,
                    max_values=min(len(options), 25),
                )
            async def callback(self_, inter: discord.Interaction):
                for cid_str in self_.values:
                    await remove_channel(gid, self._db_key, int(cid_str))
                for item in self_.view.children: item.disabled = True
                await inter.response.edit_message(
                    content=f"✅ Removed {len(self_.values)} channel(s) from **{self._label}**.",
                    view=self_.view
                )

        class RemoveView(discord.ui.View):
            def __init__(self_): super().__init__(timeout=120); self_.add_item(RemoveSelect())

        await interaction.response.send_message(
            f"Which channels to remove from **{self._label}**?",
            view=RemoveView(), ephemeral=True
        )

    # ── Clear all ─────────────────────────────────────────────────
    @discord.ui.button(label="🗑️ Clear all", style=discord.ButtonStyle.secondary)
    async def clear_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_author(interaction):
            return await interaction.response.send_message("Not for you.", ephemeral=True)

        class ConfirmView(discord.ui.View):
            def __init__(self_): super().__init__(timeout=30)
            @discord.ui.button(label="Yes, clear", style=discord.ButtonStyle.danger)
            async def yes(self_, inter, btn):
                await set_value(inter.guild_id, self._db_key, "")
                for item in self_.children: item.disabled = True
                await inter.response.edit_message(
                    content=f"✅ Cleared **{self._label}** — using defaults.", view=self_
                )
            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def no(self_, inter, btn):
                for item in self_.children: item.disabled = True
                await inter.response.edit_message(content="Cancelled.", view=self_)

        await interaction.response.send_message(
            f"Clear all configured channels for **{self._label}**?",
            view=ConfirmView(), ephemeral=True
        )

    # ── Show current ──────────────────────────────────────────────
    @discord.ui.button(label="👁️ Show current", style=discord.ButtonStyle.secondary)
    async def show_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_author(interaction):
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        gid = interaction.guild_id
        if self._is_multi:
            chs = await get_channels(gid, self._db_key)
            val = " ".join(f"<#{c}>" for c in chs) if chs else "*all channels (unrestricted)*"
        else:
            ch = await get_channel(gid, self._db_key)
            val = f"<#{ch}>" if ch else "*not set*"
        await interaction.response.send_message(
            f"**{self._label}:** {val}", ephemeral=True
        )

    # ── Back ──────────────────────────────────────────────────────
    @discord.ui.button(label="↩ Back", style=discord.ButtonStyle.secondary)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_author(interaction):
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        e = discord.Embed(
            title="⚙️ Server Config",
            description="Choose a channel type to configure:",
            color=0x3498db,
        )
        await interaction.response.edit_message(embed=e, view=_ConfigView(interaction.guild, self._author))


class _ChannelPickView(discord.ui.View):
    """Single channel select for non-multi types."""
    def __init__(self, guild, author_id, db_key, label, is_multi, action, back_view=None):
        super().__init__(timeout=120)
        self._author   = author_id
        self._db_key   = db_key
        self._label    = label
        self._is_multi = is_multi
        self._action   = action
        sel = discord.ui.ChannelSelect(
            placeholder=f"Pick a channel for {label}…",
            channel_types=[discord.ChannelType.text],
            min_values=1, max_values=1,
        )
        sel.callback = self._cb
        self.add_item(sel)

    async def _cb(self, interaction: discord.Interaction):
        if interaction.user.id != self._author:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        ch_id = int(interaction.data["values"][0])
        gid   = interaction.guild_id
        if self._is_multi:
            await add_channel(gid, self._db_key, ch_id)
        else:
            await set_value(gid, self._db_key, str(ch_id))
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(
            content=f"✅ **{self._label}** set to <#{ch_id}>.", view=self
        )


class _BulkChannelPickView(discord.ui.View):
    """Multi channel select — add up to 10 at once."""
    def __init__(self, guild, author_id, db_key, label, action, back_view=None):
        super().__init__(timeout=120)
        self._author  = author_id
        self._db_key  = db_key
        self._label   = label
        self._action  = action
        sel = discord.ui.ChannelSelect(
            placeholder=f"Pick channels for {label} (up to 10)…",
            channel_types=[discord.ChannelType.text],
            min_values=1, max_values=10,
        )
        sel.callback = self._cb
        self.add_item(sel)

    async def _cb(self, interaction: discord.Interaction):
        if interaction.user.id != self._author:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        gid = interaction.guild_id
        added = []
        for cid_str in interaction.data["values"]:
            cid = int(cid_str)
            await add_channel(gid, self._db_key, cid)
            added.append(f"<#{cid}>")
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(
            content=f"✅ Added {len(added)} channel(s) to **{self._label}**: {' '.join(added)}.",
            view=self
        )


class GuildConfigCog(commands.Cog, name="GuildConfig"):
    def __init__(self, bot): self.bot = bot

    def _admin(self, ctx):
        return ctx.author.guild_permissions.administrator or ctx.author.guild_permissions.manage_guild

    # ── !config — opens the GUI panel ────────────────────────────
    @commands.group(name="config", invoke_without_command=True)
    async def cfg(self, ctx):
        if not self._admin(ctx):
            return await _err(ctx, "You need Manage Server permission.")
        e = discord.Embed(
            title="⚙️ Server Config",
            description="Choose a channel type from the dropdown to add, remove, or view its configuration.",
            color=0x3498db,
        )
        e.set_footer(text="You can also use !config show to see all settings at once.")
        await ctx.send(embed=e, view=_ConfigView(ctx.guild, ctx.author.id))

    # ── Subcommands kept for power users / scripts ────────────────
    @cfg.command(name="set")
    async def cfg_set(self, ctx, t: str, channel: discord.TextChannel = None):
        if not self._admin(ctx): return await _err(ctx, "You need Manage Server permission.")
        if channel is None:
            # No channel provided — open picker
            if t.lower() not in CHANNEL_TYPES:
                return await _err(ctx, f"Unknown type `{t}`. Valid: {', '.join(CHANNEL_TYPES)}")
            key, is_multi, label = CHANNEL_TYPES[t.lower()]
            view = _ChannelPickView(ctx.guild, ctx.author.id, key, label, is_multi, "set")
            return await ctx.send(f"Pick a channel for **{label}**:", view=view)
        t = t.lower()
        if t not in CHANNEL_TYPES: return await _err(ctx, "Unknown type.")
        key, is_multi, label = CHANNEL_TYPES[t]
        if is_multi: await add_channel(ctx.guild.id, key, channel.id)
        else: await set_value(ctx.guild.id, key, str(channel.id))
        await ctx.send(f"✅ {label} → {channel.mention}", delete_after=8)

    @cfg.command(name="add")
    async def cfg_add(self, ctx, t: str, channel: discord.TextChannel = None):
        if not self._admin(ctx): return await _err(ctx, "You need Manage Server permission.")
        if channel is None:
            if t.lower() not in CHANNEL_TYPES:
                return await _err(ctx, f"Unknown type `{t}`. Valid: {', '.join(CHANNEL_TYPES)}")
            key, is_multi, label = CHANNEL_TYPES[t.lower()]
            view = _BulkChannelPickView(ctx.guild, ctx.author.id, key, label, "add")
            return await ctx.send(f"Pick channels to add to **{label}**:", view=view)
        t = t.lower()
        if t not in CHANNEL_TYPES: return await _err(ctx, "Unknown type.")
        key, _, label = CHANNEL_TYPES[t]
        await add_channel(ctx.guild.id, key, channel.id)
        await ctx.send(f"✅ Added {channel.mention} to {label}", delete_after=8)

    @cfg.command(name="remove")
    async def cfg_remove(self, ctx, t: str, channel: discord.TextChannel = None):
        if not self._admin(ctx): return await _err(ctx, "You need Manage Server permission.")
        t = t.lower()
        if t not in CHANNEL_TYPES: return await _err(ctx, "Unknown type.")
        key, _, label = CHANNEL_TYPES[t]
        if channel is None:
            chs = await get_channels(ctx.guild.id, key) if CHANNEL_TYPES[t][1] else ({await get_channel(ctx.guild.id, key)} if await get_channel(ctx.guild.id, key) else set())
            if not chs:
                return await _err(ctx, f"Nothing configured for {label}.")
            options = [discord.SelectOption(
                label=f"#{ctx.guild.get_channel(c).name}" if ctx.guild.get_channel(c) else str(c),
                value=str(c)
            ) for c in chs]

            class RS(discord.ui.Select):
                def __init__(self_): super().__init__(placeholder="Pick to remove…", options=options[:25], min_values=1, max_values=min(len(options),25))
                async def callback(self_, inter):
                    for v in self_.values: await remove_channel(inter.guild_id, key, int(v))
                    for i in self_.view.children: i.disabled = True
                    await inter.response.edit_message(content=f"✅ Removed {len(self_.values)} from {label}.", view=self_.view)

            class RV(discord.ui.View):
                def __init__(self_): super().__init__(timeout=60); self_.add_item(RS())
            return await ctx.send(f"Which channels to remove from {label}?", view=RV())
        await remove_channel(ctx.guild.id, key, channel.id)
        await ctx.send(f"✅ Removed {channel.mention} from {label}", delete_after=8)

    @cfg.command(name="judge")
    async def cfg_judge(self, ctx, role: discord.Role = None):
        if not self._admin(ctx): return await _err(ctx, "You need Manage Server permission.")
        if role is None:
            class JudgeView(discord.ui.View):
                def __init__(self_): super().__init__(timeout=60)
                @discord.ui.role_select(placeholder="Pick the judge role…")
                async def pick(self_, inter, sel):
                    if inter.user.id != ctx.author.id: return await inter.response.send_message("Not for you.", ephemeral=True)
                    await set_value(inter.guild_id, K_JUDGE, str(sel.values[0].id))
                    for i in self_.children: i.disabled = True
                    await inter.response.edit_message(content=f"✅ Judge role set to {sel.values[0].mention}.", view=self_)
            return await ctx.send("Pick the judge role:", view=JudgeView())
        await set_value(ctx.guild.id, K_JUDGE, str(role.id))
        await ctx.send(f"✅ Judge role set to {role.mention}", delete_after=8)

    @cfg.command(name="show")
    async def cfg_show(self, ctx):
        gid = ctx.guild.id
        e = discord.Embed(title=f"⚙️ Config — {ctx.guild.name}", color=0x3498db)
        for t, (key, is_multi, label) in CHANNEL_TYPES.items():
            if is_multi:
                chs = await get_channels(gid, key)
                v = " ".join(f"<#{c}>" for c in chs) if chs else "*all channels*"
            else:
                ch = await get_channel(gid, key)
                v = f"<#{ch}>" if ch else "*not set*"
            e.add_field(name=label, value=v, inline=True)
        rid = await get(gid, K_JUDGE)
        e.add_field(name="⚖️ Judge role", value=f"<@&{rid}>" if rid else "*admins only*", inline=True)
        done = await is_setup_done(gid)
        e.add_field(name="🔧 Setup", value="✅ Done" if done else "❌ Not done — run `!setup`", inline=True)
        await ctx.send(embed=e)
