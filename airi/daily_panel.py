# airi/daily_panel.py — Combined Daily/Work/Crime panel (#8 UI pattern)
# !daily opens one panel with all three economy actions as buttons.
# Cooldowns shown live. No need to remember separate commands.
import discord
from datetime import datetime, timedelta
import db
from utils import _err, C_ECONOMY
from airi.guild_config import check_channel


async def _get_panel_state(gid: int, uid: int) -> dict:
    """Returns cooldown state for daily / work / crime."""
    now = datetime.utcnow()
    eco = await db.pool.fetchrow(
        "SELECT last_daily, streak, daily_boost, balance FROM economy WHERE guild_id=$1 AND user_id=$2",
        gid, uid
    )
    work = await db.pool.fetchrow(
        "SELECT last_work, last_crime FROM work_log WHERE guild_id=$1 AND user_id=$2", gid, uid
    )

    def _cd(ts, hours):
        if not ts: return None
        rem = timedelta(hours=hours) - (now - ts)
        return rem if rem.total_seconds() > 0 else None

    daily_rem  = _cd(eco["last_daily"] if eco else None,  22)
    work_rem   = _cd(work["last_work"] if work else None,  1)
    crime_rem  = _cd(work["last_crime"] if work else None, 2)

    def fmt(rem):
        if not rem: return None
        h, s = divmod(int(rem.total_seconds()), 3600)
        m = s // 60
        return f"{h}h {m}m" if h else f"{m}m"

    return {
        "balance":    eco["balance"]  if eco  else 0,
        "streak":     eco["streak"]   if eco  else 0,
        "daily_rem":  fmt(daily_rem),
        "work_rem":   fmt(work_rem),
        "crime_rem":  fmt(crime_rem),
    }


class DailyPanelView(discord.ui.View):
    """Panel with Daily / Work / Crime buttons. Refreshes cooldowns after each action."""

    def __init__(self, ctx, state: dict):
        super().__init__(timeout=120)
        self._ctx   = ctx
        self._state = state
        self._gid   = ctx.guild.id
        self._uid   = ctx.author.id
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()
        s = self._state

        daily_label = f"💰 Daily" + (f" — ready!" if not s["daily_rem"] else f" — {s['daily_rem']}")
        work_label  = f"💼 Work"  + (f" — ready!" if not s["work_rem"]  else f" — {s['work_rem']}")
        crime_label = f"⚠️ Crime" + (f" — ready!" if not s["crime_rem"] else f" — {s['crime_rem']}")

        daily_btn = discord.ui.Button(
            label=daily_label,
            style=discord.ButtonStyle.success if not s["daily_rem"] else discord.ButtonStyle.secondary,
            disabled=bool(s["daily_rem"])
        )
        work_btn = discord.ui.Button(
            label=work_label,
            style=discord.ButtonStyle.primary if not s["work_rem"] else discord.ButtonStyle.secondary,
            disabled=bool(s["work_rem"])
        )
        crime_btn = discord.ui.Button(
            label=crime_label,
            style=discord.ButtonStyle.danger if not s["crime_rem"] else discord.ButtonStyle.secondary,
            disabled=bool(s["crime_rem"])
        )

        daily_btn.callback = self._daily
        work_btn.callback  = self._work
        crime_btn.callback = self._crime

        self.add_item(daily_btn)
        self.add_item(work_btn)
        self.add_item(crime_btn)

    def _build_embed(self) -> discord.Embed:
        s = self._state
        streak_txt = f"\n🔥 {s['streak']}-day streak!" if s.get("streak", 0) > 0 else ""
        e = discord.Embed(
            title="💰 Economy Panel",
            description=f"**Balance:** {s['balance']:,} coins{streak_txt}",
            color=C_ECONOMY,
        )
        e.set_author(name=self._ctx.author.display_name, icon_url=self._ctx.author.display_avatar.url)
        e.set_footer(text="Buttons refresh after each action.")
        return e

    async def _refresh(self, interaction: discord.Interaction):
        self._state = await _get_panel_state(self._gid, self._uid)
        self._build_buttons()
        await interaction.edit_original_response(embed=self._build_embed(), view=self)

    async def _daily(self, interaction: discord.Interaction):
        if interaction.user.id != self._uid:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        await interaction.response.defer()
        eco_cog = interaction.client.cogs.get("Economy")
        if eco_cog:
            class _FCtx:
                guild   = interaction.guild
                author  = interaction.user
                channel = interaction.channel
                bot     = interaction.client
                async def send(self_, *a, **kw):
                    kw.pop("delete_after", None)  # ignore delete_after on followup
                    return await interaction.followup.send(*a, **kw, ephemeral=True)
                class _Msg:
                    async def delete(self_): pass
                message = _Msg()
            await eco_cog._do_daily(_FCtx())
        await self._refresh(interaction)

    async def _work(self, interaction: discord.Interaction):
        if interaction.user.id != self._uid:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        await interaction.response.defer()
        jobs_cog = interaction.client.cogs.get("Jobs")
        if jobs_cog:
            class _FCtx:
                guild   = interaction.guild
                author  = interaction.user
                channel = interaction.channel
                bot     = interaction.client
                async def send(self_, *a, **kw):
                    kw.pop("delete_after", None)
                    return await interaction.followup.send(*a, **kw, ephemeral=True)
                class _Msg:
                    async def delete(self_): pass
                message = _Msg()
            await jobs_cog.work.callback(jobs_cog, _FCtx())
        await self._refresh(interaction)

    async def _crime(self, interaction: discord.Interaction):
        if interaction.user.id != self._uid:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        await interaction.response.defer()
        jobs_cog = interaction.client.cogs.get("Jobs")
        if jobs_cog:
            class _FCtx:
                guild   = interaction.guild
                author  = interaction.user
                channel = interaction.channel
                bot     = interaction.client
                async def send(self_, *a, **kw):
                    kw.pop("delete_after", None)
                    return await interaction.followup.send(*a, **kw, ephemeral=True)
                class _Msg:
                    async def delete(self_): pass
                message = _Msg()
            await jobs_cog.crime.callback(jobs_cog, _FCtx())
        await self._refresh(interaction)


async def open_daily_panel(ctx):
    """Helper called by economy.py daily command to open the panel."""
    state = await _get_panel_state(ctx.guild.id, ctx.author.id)
    view  = DailyPanelView(ctx, state)
    await ctx.send(embed=view._build_embed(), view=view)
