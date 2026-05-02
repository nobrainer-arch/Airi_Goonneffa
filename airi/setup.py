# airi/setup.py — Single-message setup wizard with back navigation
# Everything happens in ONE message that gets edited in-place.
import discord
from discord.ext import commands
import db
from utils import _err, C_INFO, C_SUCCESS, C_WARN, C_ERROR
from airi.guild_config import (
    set_value, K_BOT, K_NSFW, K_XP, K_LEVELUP, K_MARKET,
    K_COURT, K_TXN, K_LOG, K_GACHA, K_PROFILE, K_JUDGE,
    get_channel, get_channels, CHANNEL_TYPES
)

# ── Step definitions ────────────────────────────────────────────────
# (key, label, emoji, hint, is_multi, is_role)
STEPS = [
    (K_BOT,     "Bot Commands",       "🤖", "Channels where bot commands (!daily, !profile, !ah) work. Pick multiple.",   True,  False),
    (K_NSFW,    "NSFW Channels",      "🔞", "Channels where NSFW GIF commands are allowed. SFW also works here.",          True,  False),
    (K_XP,      "XP Gain Channels",   "⬆️", "Only messages in these channels earn XP. Skip = XP everywhere.",             True,  False),
    (K_LEVELUP, "Level-up Channel",   "📣", "Where level-up announcements post. Skip = same channel as message.",          False, False),
    (K_PROFILE, "Profile Channel",    "👤", "Where !profile output is redirected.",                                         False, False),
    (K_MARKET,  "Auction Channel",    "🏪", "Where !ah listings post with buy/bid buttons.",                               False, False),
    (K_GACHA,   "Gacha Channel",      "🎰", "Where the gacha board lives. Post !gachaboard here after setup.",             False, False),
    (K_COURT,   "Divorce Court",      "⚖️", "Where divorce cases are posted for judges.",                                  False, False),
    (K_TXN,     "Transaction Log",    "💸", "Where completed sales and payments are recorded.",                            False, False),
    (K_LOG,     "Mod Log",            "📋", "Where moderation actions are logged.",                                         False, False),
    (K_JUDGE,   "Judge Role",         "⚖️", "Role that can rule on divorce cases. Skip = admins only.",                   False, True),
]

# ── Embed builders ──────────────────────────────────────────────────
def _home_embed(guild: discord.Guild, results: dict) -> discord.Embed:
    e = discord.Embed(
        title="🚀 Airi Setup Wizard",
        description=(
            "Configure your server by selecting each category.\n"
            "All changes save immediately. You can come back any time.\n\n"
            "**Status:**"
        ),
        color=C_INFO,
    )
    for key, label, emoji, hint, is_multi, is_role in STEPS:
        val = results.get(key)
        e.add_field(
            name=f"{emoji} {label}",
            value=f"{'✅ ' + val if val else '⬜ Not set'}" ,
            inline=True,
        )
    e.set_footer(text=f"{guild.name} · Select a category below · All settings save instantly")
    return e

def _step_embed(guild: discord.Guild, key: str, label: str, emoji: str, hint: str, current_val: str | None) -> discord.Embed:
    e = discord.Embed(
        title=f"{emoji} Configure: {label}",
        description=(
            f"*{hint}*\n\n"
            + (f"**Current:** {current_val}\n\n" if current_val else "")
            + "Select below, or press **◀ Back** to return to the menu."
        ),
        color=C_WARN,
    )
    e.set_footer(text=f"{guild.name} · Changes save immediately")
    return e

def _done_embed(guild: discord.Guild) -> discord.Embed:
    e = discord.Embed(
        title="✅ Setup Complete!",
        description=(
            "Your server is configured! You can re-run `/setup` any time.\n\n"
            "**Next steps:**\n"
            "• `!gachaboard` — post the gacha machine\n"
            "• `!ah` — open the auction house\n"
            "• `!help` — see all commands\n"
            "• `!config show` — view all settings"
        ),
        color=C_SUCCESS,
    )
    e.set_footer(text=f"{guild.name} · Setup wizard closed")
    return e

# ── Main wizard view (home screen) ─────────────────────────────────
class SetupHomeView(discord.ui.View):
    def __init__(self, ctx, results: dict):
        super().__init__(timeout=600)   # 10 min timeout
        self._ctx     = ctx
        self._results = results
        self._msg: discord.Message | None = None
        self._build_select()

    def _build_select(self):
        self.clear_items()
        opts = [
            discord.SelectOption(
                label=label[:100],
                value=key,
                description=hint[:100],
                emoji=emoji,
                default=False,  # only one default allowed per select; track state in embed instead
            )
            for key, label, emoji, hint, _, _ in STEPS
        ]
        sel = discord.ui.Select(
            placeholder="Choose a category to configure…",
            options=opts,
            min_values=1, max_values=1,
        )
        sel.callback = self._on_select
        self.add_item(sel)

        done_btn = discord.ui.Button(label="✅ Done", style=discord.ButtonStyle.success, row=1)
        done_btn.callback = self._on_done
        self.add_item(done_btn)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self._ctx.author.id:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        key = interaction.data["values"][0]
        step = next((s for s in STEPS if s[0] == key), None)
        if not step: return
        _, label, emoji, hint, is_multi, is_role = step

        # Load current value display
        cur_val = self._results.get(key)

        # Build the step-specific view and switch to it
        step_view = SetupStepView(
            self._ctx, key, label, emoji, hint, is_multi, is_role,
            cur_val, parent=self
        )
        await interaction.response.edit_message(
            embed=_step_embed(self._ctx.guild, key, label, emoji, hint, cur_val),
            view=step_view,
        )

    async def _on_done(self, interaction: discord.Interaction):
        if interaction.user.id != self._ctx.author.id:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        for child in self.children:
            child.disabled = True
        # Mark setup done in DB
        await db.pool.execute("""
            INSERT INTO guild_setup (guild_id, setup_done, setup_by, setup_at)
            VALUES ($1,TRUE,$2,NOW())
            ON CONFLICT (guild_id) DO UPDATE SET setup_done=TRUE,setup_by=$2,setup_at=NOW()
        """, self._ctx.guild.id, self._ctx.author.id)
        await interaction.response.edit_message(embed=_done_embed(self._ctx.guild), view=self)
        self.stop()

    async def return_home(self, interaction: discord.Interaction):
        """Called by step view when Back is pressed."""
        # Reload current settings
        self._results = await _load_results(self._ctx.guild.id)
        self._build_select()
        await interaction.response.edit_message(
            embed=_home_embed(self._ctx.guild, self._results),
            view=self,
        )


# ── Per-step view ────────────────────────────────────────────────────
class SetupStepView(discord.ui.View):
    def __init__(self, ctx, key: str, label: str, emoji: str, hint: str,
                 is_multi: bool, is_role: bool, cur_val: str | None, parent: SetupHomeView):
        super().__init__(timeout=300)   # 5 min per step — enough to pick channels
        self._ctx     = ctx
        self._key     = key
        self._label   = label
        self._emoji   = emoji
        self._is_role = is_role
        self._parent  = parent
        self._saved   = False

        if is_role:
            sel = discord.ui.RoleSelect(
                placeholder=f"Select the {label}…",
                min_values=1, max_values=1,
            )
        else:
            sel = discord.ui.ChannelSelect(
                placeholder=f"Select {label}…",
                channel_types=[discord.ChannelType.text],
                min_values=1,
                # Multi-channel steps allow up to 5 at once
                max_values=5 if is_multi else 1,
            )
        sel.callback = self._on_select
        self.add_item(sel)

        skip_btn = discord.ui.Button(label="⏭ Skip / Clear", style=discord.ButtonStyle.secondary, row=1)
        skip_btn.callback = self._on_skip
        self.add_item(skip_btn)

        back_btn = discord.ui.Button(label="◀ Back", style=discord.ButtonStyle.secondary, row=1)
        back_btn.callback = self._on_back
        self.add_item(back_btn)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self._ctx.author.id:
            return await interaction.response.send_message("Not for you.", ephemeral=True)

        values = interaction.data.get("values", [])
        if not values:
            return await interaction.response.send_message("Nothing selected.", ephemeral=True)

        gid = self._ctx.guild.id

        if self._is_role:
            role_id = values[0]
            await set_value(gid, self._key, role_id)
            role = self._ctx.guild.get_role(int(role_id))
            display = role.mention if role else f"<@&{role_id}>"
            confirm_txt = f"✅ {self._label} set to {display}"
        else:
            # Save all selected channels (join with comma for multi)
            joined = ",".join(values)
            await set_value(gid, self._key, joined)
            mentions = " ".join(f"<#{v}>" for v in values)
            confirm_txt = f"✅ {self._label} set to {mentions}"

        self._saved = True

        # Show brief confirmation then return home
        for child in self.children:
            child.disabled = True

        e = discord.Embed(description=confirm_txt, color=C_SUCCESS)
        e.set_footer(text="Returning to menu…")
        msg = interaction.message
        await interaction.response.edit_message(embed=e, view=self)

        # Auto-return to home after brief pause — use message.edit() for reliability
        import asyncio
        await asyncio.sleep(1.5)
        self._parent._results = await _load_results(gid)
        self._parent._build_select()
        try:
            if msg:
                await msg.edit(
                    embed=_home_embed(self._ctx.guild, self._parent._results),
                    view=self._parent,
                )
            else:
                await interaction.edit_original_response(
                    embed=_home_embed(self._ctx.guild, self._parent._results),
                    view=self._parent,
                )
        except Exception:
            pass

    async def _on_skip(self, interaction: discord.Interaction):
        if interaction.user.id != self._ctx.author.id:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        # Clear the key
        await set_value(self._ctx.guild.id, self._key, "")
        e = discord.Embed(description=f"⏭ {self._label} cleared — using default.", color=C_WARN)
        e.set_footer(text="Returning to menu…")
        for child in self.children:
            child.disabled = True
        msg = interaction.message
        await interaction.response.edit_message(embed=e, view=self)
        import asyncio
        await asyncio.sleep(1.0)
        self._parent._results = await _load_results(self._ctx.guild.id)
        self._parent._build_select()
        try:
            if msg:
                await msg.edit(
                    embed=_home_embed(self._ctx.guild, self._parent._results),
                    view=self._parent,
                )
            else:
                await interaction.edit_original_response(
                    embed=_home_embed(self._ctx.guild, self._parent._results),
                    view=self._parent,
                )
        except Exception:
            pass

    async def _on_back(self, interaction: discord.Interaction):
        if interaction.user.id != self._ctx.author.id:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        await self._parent.return_home(interaction)

    async def on_timeout(self):
        """When step times out, disable all controls (message stays, user can click Back)."""
        for child in self.children:
            child.disabled = True
        # Can't edit without interaction here, but view becomes inactive gracefully


# ── DB helper: load all current settings ────────────────────────────
async def _load_results(guild_id: int) -> dict:
    results = {}
    for key, label, emoji, hint, is_multi, is_role in STEPS:
        try:
            row = await db.pool.fetchrow(
                "SELECT value FROM guild_config WHERE guild_id=$1 AND key=$2",
                guild_id, key
            )
            val = row["value"] if row else None
            if val:
                if is_role:
                    results[key] = f"<@&{val}>"
                else:
                    # Multi: show as #channel mentions
                    ids = val.split(",")
                    results[key] = " ".join(f"<#{i}>" for i in ids if i)
        except Exception:
            pass
    return results


# ── Cog ───────────────────────────────────────────────────────────────
class SetupCog(commands.Cog, name="Setup"):
    def __init__(self, bot):
        self.bot = bot
        self._active: dict[int, bool] = {}

    @commands.hybrid_command(name="setup")
    @commands.has_permissions(manage_guild=True)
    async def setup(self, ctx):
        """Configure your server in one place. Re-runnable any time."""
        gid = ctx.guild.id
        if self._active.get(gid):
            # Allow re-run by clearing stale lock (previous session may have timed out)
            self._active.pop(gid, None)
        self._active[gid] = True
        try:
            results = await _load_results(gid)
            view    = SetupHomeView(ctx, results)
            msg     = await ctx.send(embed=_home_embed(ctx.guild, results), view=view)
            view._msg = msg
            await view.wait()
        except Exception as e:
            print(f"Setup error for guild {gid}: {e}")
        finally:
            self._active.pop(gid, None)

    @setup.error
    async def setup_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await _err(ctx, "You need **Manage Server** permission to run setup.")
