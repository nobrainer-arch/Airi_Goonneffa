# goonneffa/mod_panel.py — Full /mod slash command panel for Goonneffa
import discord
from discord.ext import commands
from datetime import datetime, timedelta, timezone
import db
from utils import C_ERROR, C_WARN, C_SUCCESS, C_INFO

# ── Helpers ────────────────────────────────────────────────────────
def _is_mod(member: discord.Member) -> bool:
    """Server owner, admin, or manage_messages = mod."""
    return (
        member.guild.owner_id == member.id
        or member.guild_permissions.administrator
        or member.guild_permissions.manage_guild
        or member.guild_permissions.manage_messages
    )

async def _log_case(guild_id: int, mod_id: int, target_id: int,
                    action: str, reason: str, duration: str = None) -> int:
    """Insert a mod case and return its ID."""
    row = await db.pool.fetchrow("""
        INSERT INTO mod_cases (guild_id, mod_id, target_id, action, reason, duration)
        VALUES ($1,$2,$3,$4,$5,$6) RETURNING id
    """, guild_id, mod_id, target_id, action, reason, duration)
    return row["id"]

async def _get_log_channel(guild: discord.Guild, bot):
    """Fetch configured mod-log channel or fall back to global LOG_CHANNEL_ID."""
    try:
        from airi.guild_config import get_log_channel
        ch_id = await get_log_channel(guild.id)
        if ch_id:
            return bot.get_channel(ch_id)
    except Exception:
        pass
    import config
    return bot.get_channel(getattr(config, "LOG_CHANNEL_ID", 0))

async def _post_log(bot, guild: discord.Guild, action: str, mod: discord.Member,
                    target: discord.Member, reason: str, case_id: int,
                    duration: str = None, color: int = C_ERROR):
    ch = await _get_log_channel(guild, bot)
    if not ch:
        return
    e = discord.Embed(
        title=f"🔨 Case #{case_id} — {action}",
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    e.add_field(name="Target",      value=f"{target.mention} (`{target.id}`)", inline=True)
    e.add_field(name="Moderator",   value=f"{mod.mention}",                    inline=True)
    e.add_field(name="Reason",      value=reason,                              inline=False)
    if duration:
        e.add_field(name="Duration", value=duration, inline=True)
    e.set_thumbnail(url=target.display_avatar.url)
    await ch.send(embed=e)

# ── Action modals ──────────────────────────────────────────────────
class ReasonModal(discord.ui.Modal):
    reason_in = discord.ui.TextInput(
        label="Reason", placeholder="State the reason…",
        required=True, max_length=512,
    )

    def __init__(self, title: str, action_fn, target: discord.Member):
        super().__init__(title=title)
        self._fn     = action_fn
        self._target = target

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self._fn(interaction, self._target, self.reason_in.value)


class TimeoutModal(discord.ui.Modal):
    duration_in = discord.ui.TextInput(
        label="Duration (e.g. 10m, 2h, 1d)",
        placeholder="5m / 30m / 2h / 1d",
        required=True, max_length=10,
    )
    reason_in = discord.ui.TextInput(
        label="Reason", placeholder="State the reason…",
        required=True, max_length=512,
    )

    def __init__(self, target: discord.Member, action_fn):
        super().__init__(title=f"Timeout {target.display_name}")
        self._target = target
        self._fn     = action_fn

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        raw = self.duration_in.value.strip().lower()
        delta = _parse_duration(raw)
        if not delta:
            await interaction.followup.send(
                "❌ Invalid duration. Use: `5m`, `2h`, `1d`, `30m`", ephemeral=True
            )
            return
        await self._fn(interaction, self._target, self.reason_in.value, delta, raw)


def _parse_duration(raw: str) -> timedelta | None:
    """Parse '5m', '2h', '1d', '40320m' etc. into timedelta."""
    import re
    m = re.match(r'^(\d+)([smhd])$', raw)
    if not m:
        return None
    val, unit = int(m.group(1)), m.group(2)
    return {
        "s": timedelta(seconds=val),
        "m": timedelta(minutes=val),
        "h": timedelta(hours=val),
        "d": timedelta(days=val),
    }.get(unit)

# ── Target picker view ─────────────────────────────────────────────
class TargetPickerView(discord.ui.View):
    """Shows a UserSelect then triggers the requested action."""

    def __init__(self, action: str, executor: discord.Member, bot):
        super().__init__(timeout=120)
        self._action   = action
        self._executor = executor
        self._bot      = bot
        self._done     = False

        sel = discord.ui.UserSelect(
            placeholder=f"Select member to {action}…",
            min_values=1, max_values=1,
        )
        sel.callback = self._selected
        self.add_item(sel)

        back = discord.ui.Button(label="◀ Back", style=discord.ButtonStyle.secondary)
        back.callback = self._back
        self.add_item(back)

    async def _back(self, interaction: discord.Interaction):
        if interaction.user.id != self._executor.id:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        panel = ModPanelView(self._executor, self._bot)
        await interaction.response.edit_message(embed=panel._embed(), view=panel)

    async def _selected(self, interaction: discord.Interaction):
        if interaction.user.id != self._executor.id:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        if self._done:
            return
        self._done = True
        target = interaction.data["resolved"]["members"]
        # Get the actual Member object
        member_id = list(target.keys())[0]
        member = interaction.guild.get_member(int(member_id))
        if not member:
            return await interaction.response.send_message("❌ Member not found.", ephemeral=True)

        action = self._action
        if action == "warn":
            await interaction.response.send_modal(
                ReasonModal(f"Warn {member.display_name}", _do_warn, member)
            )
        elif action == "timeout":
            await interaction.response.send_modal(
                TimeoutModal(member, _do_timeout)
            )
        elif action == "kick":
            await interaction.response.send_modal(
                ReasonModal(f"Kick {member.display_name}", _do_kick, member)
            )
        elif action == "ban":
            view = BanConfirmView(member, self._executor, self._bot)
            e = discord.Embed(
                title=f"🔨 Confirm Ban — {member.display_name}",
                description=f"Are you sure you want to ban {member.mention}?",
                color=C_ERROR,
            )
            e.set_thumbnail(url=member.display_avatar.url)
            await interaction.response.edit_message(embed=e, view=view)
        elif action == "warnings":
            await interaction.response.defer(ephemeral=False)
            await _show_warnings(interaction, member)
        elif action == "userinfo":
            await interaction.response.defer(ephemeral=False)
            await _show_userinfo(interaction, member)
        elif action == "unmute":
            await interaction.response.defer(ephemeral=True)
            await _do_unmute(interaction, member)


class BanConfirmView(discord.ui.View):
    def __init__(self, target: discord.Member, executor: discord.Member, bot):
        super().__init__(timeout=60)
        self._target   = target
        self._executor = executor
        self._bot      = bot

    @discord.ui.button(label="✅ Confirm Ban", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._executor.id:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        await interaction.response.send_modal(
            ReasonModal(f"Ban {self._target.display_name}", _do_ban, self._target)
        )

    @discord.ui.button(label="◀ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._executor.id:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        panel = ModPanelView(self._executor, self._bot)
        await interaction.response.edit_message(embed=panel._embed(), view=panel)


# ── Action executors ───────────────────────────────────────────────
async def _do_warn(interaction: discord.Interaction, target: discord.Member, reason: str):
    executor = interaction.user
    if not _is_mod(executor):
        return await interaction.followup.send("❌ No permission.", ephemeral=True)
    if target.top_role >= executor.top_role and executor.guild.owner_id != executor.id:
        return await interaction.followup.send("❌ Can't moderate someone with equal/higher role.", ephemeral=True)

    case_id = await _log_case(interaction.guild_id, executor.id, target.id, "Warn", reason)
    # Count warnings for this user in this server
    count = await db.pool.fetchval(
        "SELECT COUNT(*) FROM mod_cases WHERE guild_id=$1 AND target_id=$2 AND action='Warn'",
        interaction.guild_id, target.id
    )
    e = discord.Embed(
        description=f"⚠️ {target.mention} warned. (Warning #{count})\n**Reason:** {reason}",
        color=C_WARN,
    )
    await interaction.followup.send(embed=e, ephemeral=False)
    try:
        await target.send(embed=discord.Embed(
            title=f"⚠️ Warning in {interaction.guild.name}",
            description=f"You have been warned.\n**Reason:** {reason}\n**Total warnings:** {count}",
            color=C_WARN,
        ))
    except Exception:
        pass
    await _post_log(interaction.client, interaction.guild, "Warn", executor, target,
                    reason, case_id, color=C_WARN)


async def _do_timeout(interaction: discord.Interaction, target: discord.Member,
                       reason: str, delta: timedelta, raw: str):
    executor = interaction.user
    if not _is_mod(executor):
        return await interaction.followup.send("❌ No permission.", ephemeral=True)
    if target.top_role >= executor.top_role and executor.guild.owner_id != executor.id:
        return await interaction.followup.send("❌ Can't moderate someone with equal/higher role.", ephemeral=True)
    try:
        await target.timeout(delta, reason=f"[Mod] {reason}")
    except discord.Forbidden:
        return await interaction.followup.send("❌ Missing `Moderate Members` permission.", ephemeral=True)
    case_id = await _log_case(interaction.guild_id, executor.id, target.id, "Timeout", reason, raw)
    e = discord.Embed(
        description=f"🔇 {target.mention} timed out for **{raw}**.\n**Reason:** {reason}",
        color=C_ERROR,
    )
    await interaction.followup.send(embed=e, ephemeral=False)
    try:
        await target.send(embed=discord.Embed(
            title=f"🔇 Timeout in {interaction.guild.name}",
            description=f"You have been timed out for **{raw}**.\n**Reason:** {reason}",
            color=C_ERROR,
        ))
    except Exception:
        pass
    await _post_log(interaction.client, interaction.guild, "Timeout", executor, target,
                    reason, case_id, duration=raw)


async def _do_kick(interaction: discord.Interaction, target: discord.Member, reason: str):
    executor = interaction.user
    if not _is_mod(executor):
        return await interaction.followup.send("❌ No permission.", ephemeral=True)
    if target.top_role >= executor.top_role and executor.guild.owner_id != executor.id:
        return await interaction.followup.send("❌ Can't moderate someone with equal/higher role.", ephemeral=True)
    try:
        await target.kick(reason=f"[Mod] {reason}")
    except discord.Forbidden:
        return await interaction.followup.send("❌ Missing `Kick Members` permission.", ephemeral=True)
    case_id = await _log_case(interaction.guild_id, executor.id, target.id, "Kick", reason)
    e = discord.Embed(
        description=f"👢 {target.mention} was kicked.\n**Reason:** {reason}",
        color=C_ERROR,
    )
    await interaction.followup.send(embed=e, ephemeral=False)
    await _post_log(interaction.client, interaction.guild, "Kick", executor, target,
                    reason, case_id)


async def _do_ban(interaction: discord.Interaction, target: discord.Member, reason: str):
    executor = interaction.user
    if not _is_mod(executor):
        return await interaction.followup.send("❌ No permission.", ephemeral=True)
    if target.top_role >= executor.top_role and executor.guild.owner_id != executor.id:
        return await interaction.followup.send("❌ Can't moderate someone with equal/higher role.", ephemeral=True)
    try:
        await target.ban(reason=f"[Mod] {reason}", delete_message_days=0)
    except discord.Forbidden:
        return await interaction.followup.send("❌ Missing `Ban Members` permission.", ephemeral=True)
    case_id = await _log_case(interaction.guild_id, executor.id, target.id, "Ban", reason)
    e = discord.Embed(
        description=f"🔨 {target.mention} was banned.\n**Reason:** {reason}",
        color=C_ERROR,
    )
    await interaction.followup.send(embed=e, ephemeral=False)
    await _post_log(interaction.client, interaction.guild, "Ban", executor, target,
                    reason, case_id)


async def _do_unmute(interaction: discord.Interaction, target: discord.Member):
    executor = interaction.user
    if not _is_mod(executor):
        return await interaction.followup.send("❌ No permission.", ephemeral=True)
    try:
        await target.timeout(None, reason="[Mod] Removed timeout")
    except discord.Forbidden:
        return await interaction.followup.send("❌ Missing permission.", ephemeral=True)
    case_id = await _log_case(interaction.guild_id, executor.id, target.id, "Unmute", "Timeout removed")
    await interaction.followup.send(
        embed=discord.Embed(
            description=f"🔊 {target.mention} timeout removed.", color=C_SUCCESS
        ),
        ephemeral=False,
    )
    await _post_log(interaction.client, interaction.guild, "Unmute", executor, target,
                    "Timeout removed", case_id, color=C_SUCCESS)


async def _show_warnings(interaction: discord.Interaction, target: discord.Member):
    rows = await db.pool.fetch("""
        SELECT action, reason, duration, created_at
        FROM mod_cases
        WHERE guild_id=$1 AND target_id=$2
        ORDER BY created_at DESC LIMIT 20
    """, interaction.guild_id, target.id)

    e = discord.Embed(
        title=f"📋 Mod History — {target.display_name}",
        color=C_WARN,
    )
    e.set_thumbnail(url=target.display_avatar.url)
    if not rows:
        e.description = "✅ No mod actions on record."
    else:
        lines = []
        for r in rows:
            ts = discord.utils.format_dt(r["created_at"], "R")
            dur = f" ({r['duration']})" if r["duration"] else ""
            lines.append(f"**{r['action']}**{dur} — {r['reason']} · {ts}")
        e.description = "\n".join(lines[:15])
        e.set_footer(text=f"Showing {min(len(rows), 15)} most recent")

    back = discord.ui.Button(label="◀ Back to Panel", style=discord.ButtonStyle.secondary)
    async def _back_cb(i):
        if i.user.id != interaction.user.id: return await i.response.send_message("Not for you.", ephemeral=True)
        panel = ModPanelView(i.user, i.client)
        await i.response.edit_message(embed=panel._embed(), view=panel)
    back.callback = _back_cb
    v = discord.ui.View(timeout=120)
    v.add_item(back)
    await interaction.followup.send(embed=e, view=v)


async def _show_userinfo(interaction: discord.Interaction, target: discord.Member):
    warn_count = await db.pool.fetchval(
        "SELECT COUNT(*) FROM mod_cases WHERE guild_id=$1 AND target_id=$2 AND action='Warn'",
        interaction.guild_id, target.id,
    ) or 0
    case_count = await db.pool.fetchval(
        "SELECT COUNT(*) FROM mod_cases WHERE guild_id=$1 AND target_id=$2",
        interaction.guild_id, target.id,
    ) or 0

    e = discord.Embed(title=f"🔍 User Info — {target}", color=C_INFO)
    e.set_thumbnail(url=target.display_avatar.url)
    e.add_field(name="ID",        value=str(target.id),                           inline=True)
    e.add_field(name="Joined",    value=discord.utils.format_dt(target.joined_at, "R"), inline=True)
    e.add_field(name="Created",   value=discord.utils.format_dt(target.created_at, "R"), inline=True)
    e.add_field(name="Warnings",  value=str(warn_count),                          inline=True)
    e.add_field(name="Mod cases", value=str(case_count),                          inline=True)
    e.add_field(name="Roles",     value=" ".join(r.mention for r in target.roles[1:6]) or "None", inline=False)
    if target.timed_out_until and target.timed_out_until > discord.utils.utcnow():
        e.add_field(name="⏱️ Timed out until",
                    value=discord.utils.format_dt(target.timed_out_until, "R"),
                    inline=False)

    back = discord.ui.Button(label="◀ Back to Panel", style=discord.ButtonStyle.secondary)
    async def _back_cb(i):
        if i.user.id != interaction.user.id: return await i.response.send_message("Not for you.", ephemeral=True)
        panel = ModPanelView(i.user, i.client)
        await i.response.edit_message(embed=panel._embed(), view=panel)
    back.callback = _back_cb
    v = discord.ui.View(timeout=120)
    v.add_item(back)
    await interaction.followup.send(embed=e, view=v)


# ── Main panel view ────────────────────────────────────────────────
class ModPanelView(discord.ui.View):
    def __init__(self, executor: discord.Member, bot):
        super().__init__(timeout=300)
        self._executor = executor
        self._bot      = bot

    def _embed(self) -> discord.Embed:
        e = discord.Embed(
            title="🛡️ Moderation Panel",
            description=(
                "Select an action below. You'll be guided through each step.\n\n"
                "**Actions available:**\n"
                "⚠️ Warn · 🔇 Timeout · 🔊 Unmute · 👢 Kick · 🔨 Ban\n"
                "📋 View History · 🔍 User Info"
            ),
            color=C_ERROR,
        )
        e.set_footer(text=f"Used by {self._executor.display_name} · Panel expires in 5 min")
        return e

    def _picker(self, action: str) -> TargetPickerView:
        return TargetPickerView(action, self._executor, self._bot)

    @discord.ui.button(label="⚠️ Warn", style=discord.ButtonStyle.secondary, row=0)
    async def warn_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._executor.id:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        if not _is_mod(interaction.user):
            return await interaction.response.send_message("❌ You don't have mod permissions.", ephemeral=True)
        e = discord.Embed(title="⚠️ Warn — Select Target",
                          description="Pick the member you want to warn.", color=C_WARN)
        await interaction.response.edit_message(embed=e, view=self._picker("warn"))

    @discord.ui.button(label="🔇 Timeout", style=discord.ButtonStyle.primary, row=0)
    async def timeout_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._executor.id:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        if not _is_mod(interaction.user):
            return await interaction.response.send_message("❌ No mod permissions.", ephemeral=True)
        e = discord.Embed(title="🔇 Timeout — Select Target",
                          description="Pick the member to timeout.", color=C_ERROR)
        await interaction.response.edit_message(embed=e, view=self._picker("timeout"))

    @discord.ui.button(label="🔊 Unmute", style=discord.ButtonStyle.secondary, row=0)
    async def unmute_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._executor.id:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        if not _is_mod(interaction.user):
            return await interaction.response.send_message("❌ No mod permissions.", ephemeral=True)
        e = discord.Embed(title="🔊 Unmute — Select Target",
                          description="Pick the member to remove timeout from.", color=C_SUCCESS)
        await interaction.response.edit_message(embed=e, view=self._picker("unmute"))

    @discord.ui.button(label="👢 Kick", style=discord.ButtonStyle.danger, row=1)
    async def kick_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._executor.id:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        if not _is_mod(interaction.user):
            return await interaction.response.send_message("❌ No mod permissions.", ephemeral=True)
        e = discord.Embed(title="👢 Kick — Select Target",
                          description="Pick the member to kick.", color=C_ERROR)
        await interaction.response.edit_message(embed=e, view=self._picker("kick"))

    @discord.ui.button(label="🔨 Ban", style=discord.ButtonStyle.danger, row=1)
    async def ban_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._executor.id:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        if not _is_mod(interaction.user):
            return await interaction.response.send_message("❌ No mod permissions.", ephemeral=True)
        e = discord.Embed(title="🔨 Ban — Select Target",
                          description="Pick the member to ban.", color=C_ERROR)
        await interaction.response.edit_message(embed=e, view=self._picker("ban"))

    @discord.ui.button(label="📋 View History", style=discord.ButtonStyle.secondary, row=2)
    async def history_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._executor.id:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        if not _is_mod(interaction.user):
            return await interaction.response.send_message("❌ No mod permissions.", ephemeral=True)
        e = discord.Embed(title="📋 View History — Select User",
                          description="Pick whose history to view.", color=C_WARN)
        await interaction.response.edit_message(embed=e, view=self._picker("warnings"))

    @discord.ui.button(label="🔍 User Info", style=discord.ButtonStyle.secondary, row=2)
    async def info_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._executor.id:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        e = discord.Embed(title="🔍 User Info — Select User",
                          description="Pick who to look up.", color=C_INFO)
        await interaction.response.edit_message(embed=e, view=self._picker("userinfo"))


# ── Cog ────────────────────────────────────────────────────────────
class ModPanelCog(commands.Cog, name="ModPanel"):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="mod", description="Open the moderation panel")
    @commands.has_permissions(manage_messages=True)
    async def mod(self, ctx):
        """Open the full moderation panel with guided UI."""
        panel = ModPanelView(ctx.author, self.bot)
        await ctx.send(embed=panel._embed(), view=panel)

    # ── Quick standalone commands (all with no-arg UI) ─────────────
    @commands.hybrid_command(name="warn", description="Warn a member")
    @commands.has_permissions(manage_messages=True)
    async def warn(self, ctx, member: discord.Member = None, *, reason: str = None):
        if member is None:
            # Guide: show picker
            e = discord.Embed(title="⚠️ Warn — Select Target",
                              description="Pick the member to warn.", color=C_WARN)
            view = TargetPickerView("warn", ctx.author, self.bot)
            return await ctx.send(embed=e, view=view)
        if reason is None:
            class RModal(discord.ui.Modal, title=f"Warn {member.display_name}"):
                reason_in = discord.ui.TextInput(label="Reason", required=True, max_length=512)
                async def on_submit(m_self, inter):
                    await inter.response.defer(ephemeral=True)
                    await _do_warn(inter, member, m_self.reason_in.value)
            return await ctx.send_modal(RModal()) if hasattr(ctx, "send_modal") else \
                   await _do_warn(ctx, member, "No reason given")  # prefix fallback
        await _do_warn(ctx, member, reason)

    @commands.hybrid_command(name="timeout", description="Timeout a member")
    @commands.has_permissions(moderate_members=True)
    async def timeout_cmd(self, ctx, member: discord.Member = None,
                          duration: str = None, *, reason: str = "No reason given"):
        if member is None:
            e = discord.Embed(title="🔇 Timeout — Select Target",
                              description="Pick the member to timeout.", color=C_ERROR)
            view = TargetPickerView("timeout", ctx.author, self.bot)
            return await ctx.send(embed=e, view=view)
        if duration is None:
            await ctx.send(
                "⏱️ **Usage:** `!timeout @user <duration> [reason]`\n"
                "Duration formats: `5m` `2h` `1d` (up to `28d`)\n\n"
                "Or use `/mod` for the guided panel.",
                delete_after=15
            )
            return
        delta = _parse_duration(duration.lower())
        if not delta:
            return await ctx.send("❌ Invalid duration. Try `5m`, `2h`, `1d`.", delete_after=8)
        # build fake interaction-like object for prefix commands
        case_id = await _log_case(ctx.guild.id, ctx.author.id, member.id, "Timeout", reason, duration)
        try:
            await member.timeout(delta, reason=f"[Mod] {reason}")
        except discord.Forbidden:
            return await ctx.send("❌ Missing `Moderate Members` permission.", delete_after=8)
        e = discord.Embed(
            description=f"🔇 {member.mention} timed out for **{duration}**.\n**Reason:** {reason}",
            color=C_ERROR,
        )
        await ctx.send(embed=e)
        await _post_log(self.bot, ctx.guild, "Timeout", ctx.author, member, reason, case_id, duration=duration)

    @commands.hybrid_command(name="kick", description="Kick a member")
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx, member: discord.Member = None, *, reason: str = None):
        if member is None:
            e = discord.Embed(title="👢 Kick — Select Target",
                              description="Pick the member to kick.", color=C_ERROR)
            view = TargetPickerView("kick", ctx.author, self.bot)
            return await ctx.send(embed=e, view=view)
        if reason is None:
            reason = "No reason given"
        if member.top_role >= ctx.author.top_role and ctx.guild.owner_id != ctx.author.id:
            return await ctx.send("❌ Can't kick someone with equal/higher role.", delete_after=8)
        case_id = await _log_case(ctx.guild.id, ctx.author.id, member.id, "Kick", reason)
        try:
            await member.kick(reason=f"[Mod] {reason}")
        except discord.Forbidden:
            return await ctx.send("❌ Missing `Kick Members` permission.", delete_after=8)
        e = discord.Embed(
            description=f"👢 {member.mention} kicked.\n**Reason:** {reason}",
            color=C_ERROR,
        )
        await ctx.send(embed=e)
        await _post_log(self.bot, ctx.guild, "Kick", ctx.author, member, reason, case_id)

    @commands.hybrid_command(name="ban", description="Ban a member")
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx, member: discord.Member = None, *, reason: str = None):
        if member is None:
            e = discord.Embed(title="🔨 Ban — Select Target",
                              description="Pick the member to ban.", color=C_ERROR)
            view = TargetPickerView("ban", ctx.author, self.bot)
            return await ctx.send(embed=e, view=view)
        if reason is None:
            reason = "No reason given"
        if member.top_role >= ctx.author.top_role and ctx.guild.owner_id != ctx.author.id:
            return await ctx.send("❌ Can't ban someone with equal/higher role.", delete_after=8)

        # Confirm view for prefix command
        class ConfirmBan(discord.ui.View):
            def __init__(self_): super().__init__(timeout=30)
            @discord.ui.button(label="✅ Confirm Ban", style=discord.ButtonStyle.danger)
            async def yes(self_, inter, btn):
                if inter.user.id != ctx.author.id: return await inter.response.send_message("Not for you.", ephemeral=True)
                for b in self_.children: b.disabled = True
                await inter.response.edit_message(view=self_)
                case_id = await _log_case(ctx.guild.id, ctx.author.id, member.id, "Ban", reason)
                try:
                    await member.ban(reason=f"[Mod] {reason}", delete_message_days=0)
                except discord.Forbidden:
                    return await inter.followup.send("❌ Missing `Ban Members` permission.", ephemeral=True)
                e = discord.Embed(
                    description=f"🔨 {member.mention} banned.\n**Reason:** {reason}",
                    color=C_ERROR,
                )
                await inter.followup.send(embed=e)
                from goonneffa.mod_panel import _post_log
                await _post_log(ctx.bot, ctx.guild, "Ban", ctx.author, member, reason, case_id)
            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def no(self_, inter, btn):
                for b in self_.children: b.disabled = True
                await inter.response.edit_message(content="Cancelled.", view=self_)

        e = discord.Embed(
            title=f"🔨 Confirm Ban — {member.display_name}",
            description=f"Ban {member.mention}?\n**Reason:** {reason}",
            color=C_ERROR,
        )
        await ctx.send(embed=e, view=ConfirmBan())

    @commands.hybrid_command(name="warnings", aliases=["warnlist", "modhistory"],
                              description="View a member's mod history")
    @commands.has_permissions(manage_messages=True)
    async def warnings(self, ctx, member: discord.Member = None):
        if member is None:
            e = discord.Embed(title="📋 History — Select User",
                              description="Pick whose history to view.", color=C_WARN)
            view = TargetPickerView("warnings", ctx.author, self.bot)
            return await ctx.send(embed=e, view=view)
        rows = await db.pool.fetch("""
            SELECT action, reason, duration, created_at FROM mod_cases
            WHERE guild_id=$1 AND target_id=$2
            ORDER BY created_at DESC LIMIT 20
        """, ctx.guild.id, member.id)
        e = discord.Embed(title=f"📋 Mod History — {member.display_name}", color=C_WARN)
        e.set_thumbnail(url=member.display_avatar.url)
        if not rows:
            e.description = "✅ No mod actions on record."
        else:
            lines = []
            for r in rows:
                ts  = discord.utils.format_dt(r["created_at"], "R")
                dur = f" ({r['duration']})" if r["duration"] else ""
                lines.append(f"**{r['action']}**{dur} — {r['reason']} · {ts}")
            e.description = "\n".join(lines[:15])
        await ctx.send(embed=e)

    @commands.hybrid_command(name="clearwarnings", description="Clear all warnings for a member")
    @commands.has_permissions(administrator=True)
    async def clearwarnings(self, ctx, member: discord.Member = None):
        if member is None:
            sel = discord.ui.UserSelect(placeholder="Select member to clear warnings…")
            async def cb(inter: discord.Interaction):
                if inter.user.id != ctx.author.id:
                    return await inter.response.send_message("Not for you.", ephemeral=True)
                m = sel.values[0]
                await db.pool.execute(
                    "DELETE FROM mod_cases WHERE guild_id=$1 AND target_id=$2 AND action='Warn'",
                    ctx.guild.id, m.id,
                )
                for i in v.children: i.disabled = True
                await inter.response.edit_message(
                    content=f"✅ Cleared warnings for {m.display_name}.", view=v
                )
            sel.callback = cb
            class v(discord.ui.View):
                def __init__(self_): super().__init__(timeout=60); self_.add_item(sel)
            return await ctx.send("Select member:", view=v())
        await db.pool.execute(
            "DELETE FROM mod_cases WHERE guild_id=$1 AND target_id=$2 AND action='Warn'",
            ctx.guild.id, member.id,
        )
        await ctx.send(f"✅ Warnings cleared for {member.mention}.", delete_after=8)

    @commands.hybrid_command(name="chatdelhist", description="Delete recent chat history, including bot commands")
    @commands.has_permissions(manage_messages=True)
    async def chatdelhist(self, ctx, amount: int = 10):
        if amount > 100:
            amount = 100
        if amount < 1:
            amount = 1
        deleted = 0
        to_delete = []
        async for message in ctx.channel.history(limit=amount):
            # Delete bot messages, commands (starting with ! or /), or mentions to the bot
            if (message.author == self.bot.user or
                message.content.startswith('!') or
                message.content.startswith('/') or
                self.bot.user.mentioned_in(message)):
                to_delete.append(message)
        if to_delete:
            try:
                await ctx.channel.delete_messages(to_delete)
                deleted = len(to_delete)
            except discord.Forbidden:
                # Fallback to individual deletes if bulk fails
                for msg in to_delete:
                    try:
                        await msg.delete()
                        deleted += 1
                    except:
                        pass
        await ctx.send(f"🗑️ Deleted {deleted} messages from history.", delete_after=5)
