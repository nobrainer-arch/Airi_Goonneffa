# airi/setup.py — GUI-driven setup wizard, re-entrant (can continue configuring)
import discord
from discord.ext import commands
import asyncio
import db
from utils import _err, C_INFO, C_SUCCESS, C_WARN
from airi.guild_config import (
    set_value, K_BOT, K_NSFW, K_XP, K_LEVELUP, K_MARKET,
    K_COURT, K_TXN, K_LOG, K_GACHA, K_PROFILE, K_JUDGE,
    get_channel, get_channels, CHANNEL_TYPES
)

# Each step: (key, label, emoji, hint, is_multi, is_role)
SETUP_STEPS = [
    (K_BOT,     "Bot Commands Channel",     "🤖", "Where commands like !daily, !profile, !ah work.",              True,  False),
    (K_NSFW,    "NSFW Channel",             "🔞", "Where NSFW GIF commands are allowed. SFW also works here.",    True,  False),
    (K_XP,      "XP Gain Channel",          "⬆️", "Only messages here earn XP. Skip to allow XP everywhere.",    True,  False),
    (K_LEVELUP, "Level-up Announcements",   "📣", "Where level-up messages post. Skip to use message channel.",  False, False),
    (K_PROFILE, "Profile Channel",          "👤", "Where !profile output is redirected.",                        False, False),
    (K_MARKET,  "Auction House Channel",    "🏪", "Where !ah listings post and the buy buttons live.",           False, False),
    (K_GACHA,   "Gacha Channel",            "🎰", "Where the gacha board lives. Use !gachaboard after setup.",   False, False),
    (K_COURT,   "Divorce Court Channel",    "⚖️", "Where divorce cases are posted for judges.",                  False, False),
    (K_TXN,     "Transaction Log Channel",  "💸", "Where completed sales and payments are recorded.",            False, False),
    (K_LOG,     "Mod Log Channel",          "📋", "Where moderation actions are logged.",                        False, False),
    (K_JUDGE,   "Judge Role",               "⚖️", "Role that can rule on divorce cases. Skip = admins only.",   False, True),
]


def _cfg_embed(guild_name: str, step_idx: int, total: int, label: str, emoji: str, hint: str) -> discord.Embed:
    e = discord.Embed(
        title=f"{emoji} Step {step_idx+1}/{total} — {label}",
        description=f"*{hint}*\n\nSelect below or press **Skip** to keep default.",
        color=C_WARN,
    )
    e.set_footer(text=f"Configuring: {guild_name} · You can re-run !setup any time")
    return e


def _summary_embed(results: dict, guild_name: str) -> discord.Embed:
    e = discord.Embed(title="✅ Setup Complete!", color=C_SUCCESS)
    lines = []
    for label, val in results.items():
        lines.append(f"**{label}:** {val}")
    if not lines:
        lines.append("All channels using defaults.")
    e.description = "\n".join(lines)
    e.add_field(
        name="Next steps",
        value=(
            "`!config show` — view all settings\n"
            "`!config set <type> #channel` — change anything\n"
            "`!gachaboard` — post the gacha machine\n"
            "`!help` — see all commands"
        ),
        inline=False,
    )
    e.set_footer(text=f"{guild_name} · Run !setup again to reconfigure")
    return e


class SetupCog(commands.Cog, name="Setup"):
    def __init__(self, bot):
        self.bot     = bot
        self._active: dict[int, bool] = {}

    @commands.command(name="setup")
    @commands.has_permissions(manage_guild=True)
    async def setup(self, ctx):
        """GUI-driven server setup. Re-runnable any time."""
        gid = ctx.guild.id
        if self._active.get(gid):
            return await _err(ctx, "Setup is already running in this server.")
        self._active[gid] = True
        try:
            await self._run(ctx)
        finally:
            self._active.pop(gid, None)

    async def _run(self, ctx):
        gid = ctx.guild.id
        results: dict[str, str] = {}

        # Intro
        intro = discord.Embed(
            title="🚀 Airi Setup Wizard",
            description=(
                "Welcome! This wizard configures your server.\n\n"
                "For each step, **select a channel** from the dropdown or press **Skip**.\n"
                "You can always re-run `!setup` or use `!config` to change individual settings.\n\n"
                "⏱️ Each step times out in **3 minutes**."
            ),
            color=C_INFO,
        )
        intro.set_footer(text="Only admins can interact with this wizard")
        await ctx.send(embed=intro)

        for i, (key, label, emoji, hint, is_multi, is_role) in enumerate(SETUP_STEPS):
            done_event = discord.utils.MISSING
            chosen_val = None

            class StepView(discord.ui.View):
                def __init__(self_):
                    super().__init__(timeout=180)
                    self_._done = False

                    if is_role:
                        sel = discord.ui.RoleSelect(
                            placeholder=f"Select the {label}...",
                            min_values=1, max_values=1,
                        )
                    elif is_multi:
                        sel = discord.ui.ChannelSelect(
                            placeholder=f"Select {label}...",
                            channel_types=[discord.ChannelType.text],
                            min_values=1, max_values=1,
                        )
                    else:
                        sel = discord.ui.ChannelSelect(
                            placeholder=f"Select {label}...",
                            channel_types=[discord.ChannelType.text],
                            min_values=1, max_values=1,
                        )
                    sel.callback = self_._selected
                    self_.add_item(sel)

                async def _selected(self_, interaction: discord.Interaction):
                    if interaction.user.id != ctx.author.id:
                        return await interaction.response.send_message("Not for you.", ephemeral=True)
                    if self_._done: return
                    self_._done = True
                    nonlocal chosen_val
                    chosen_val = interaction.data["values"][0]
                    for item in self_.children: item.disabled = True
                    await interaction.response.edit_message(view=self_)
                    self_.stop()

                @discord.ui.button(label="⏭️ Skip", style=discord.ButtonStyle.secondary)
                async def skip_btn(self_, interaction: discord.Interaction, button):
                    if interaction.user.id != ctx.author.id:
                        return await interaction.response.send_message("Not for you.", ephemeral=True)
                    if self_._done: return
                    self_._done = True
                    nonlocal chosen_val
                    chosen_val = None
                    for item in self_.children: item.disabled = True
                    await interaction.response.edit_message(view=self_)
                    self_.stop()

                @discord.ui.button(label="❌ Cancel Setup", style=discord.ButtonStyle.danger)
                async def cancel_btn(self_, interaction: discord.Interaction, button):
                    if interaction.user.id != ctx.author.id:
                        return await interaction.response.send_message("Not for you.", ephemeral=True)
                    self_._done = True
                    nonlocal chosen_val
                    chosen_val = "CANCEL"
                    for item in self_.children: item.disabled = True
                    await interaction.response.edit_message(view=self_)
                    await ctx.send("❌ Setup cancelled.", delete_after=8)
                    self_.stop()

            view = StepView()
            step_msg = await ctx.send(embed=_cfg_embed(ctx.guild.name, i, len(SETUP_STEPS), label, emoji, hint), view=view)
            await view.wait()

            if chosen_val == "CANCEL":
                return

            # Apply value
            if chosen_val:
                await set_value(gid, key, str(chosen_val))
                if is_role:
                    role = ctx.guild.get_role(int(chosen_val))
                    display = role.mention if role else chosen_val
                else:
                    display = f"<#{chosen_val}>"
                results[f"{emoji} {label}"] = display
                await step_msg.edit(embed=discord.Embed(
                    title=f"✅ {label}", description=f"Set to {display}", color=C_SUCCESS
                ))
            else:
                await step_msg.edit(embed=discord.Embed(
                    title=f"⏭️ {label}", description="Skipped — using default.", color=C_WARN
                ))

        # Mark setup done
        await db.pool.execute("""
            INSERT INTO guild_setup (guild_id, setup_done, setup_by, setup_at)
            VALUES ($1,TRUE,$2,NOW())
            ON CONFLICT (guild_id) DO UPDATE SET setup_done=TRUE, setup_by=$2, setup_at=NOW()
        """, gid, ctx.author.id)

        await ctx.send(embed=_summary_embed(results, ctx.guild.name))

    @setup.error
    async def setup_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await _err(ctx, "You need Manage Server permission to run setup.")
