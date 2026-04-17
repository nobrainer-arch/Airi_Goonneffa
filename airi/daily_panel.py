# airi/daily_panel.py — Economy Panel (Daily/Work/Crime in one place)
import discord
from datetime import datetime, timedelta, timezone
import db

async def _get_state(gid: int, uid: int) -> dict:
    from datetime import timezone as _tz
    now = datetime.now(_tz.utc)
    eco  = await db.pool.fetchrow(
        "SELECT last_daily,streak,balance FROM economy WHERE guild_id=$1 AND user_id=$2", gid, uid
    )
    work = await db.pool.fetchrow(
        "SELECT last_work,last_crime FROM work_log WHERE guild_id=$1 AND user_id=$2", gid, uid
    )
    def _make_aware(ts):
        """Ensure datetime is timezone-aware for safe arithmetic."""
        if ts is None: return None
        if hasattr(ts, 'tzinfo') and ts.tzinfo is not None:
            return ts
        return ts.replace(tzinfo=_tz.utc)
    def _rem(ts, hours):
        ts = _make_aware(ts)
        if not ts: return None
        d = timedelta(hours=hours) - (now - ts)
        return d if d.total_seconds() > 0 else None
    def _fmt(d):
        if not d: return None
        h, s = divmod(int(d.total_seconds()), 3600)
        return f"{h}h {s//60}m" if h else f"{s//60}m"
    return {
        "balance":   eco["balance"]  if eco  else 0,
        "streak":    eco["streak"]   if eco  else 0,
        "daily_rem": _fmt(_rem(eco["last_daily"]  if eco  else None, 22)),
        "work_rem":  _fmt(_rem(work["last_work"]   if work else None,  1)),
        "crime_rem": _fmt(_rem(work["last_crime"]  if work else None,  2)),
    }


class DailyPanelView(discord.ui.View):
    def __init__(self, ctx, state: dict):
        super().__init__(timeout=300)
        self._ctx   = ctx
        self._state = state
        self._gid   = ctx.guild.id
        self._uid   = ctx.author.id
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        s = self._state
        daily_btn = discord.ui.Button(
            label="💰 Daily"  + (" ✅" if not s["daily_rem"] else f" — {s['daily_rem']}"),
            style=discord.ButtonStyle.success if not s["daily_rem"] else discord.ButtonStyle.secondary,
            disabled=bool(s["daily_rem"])
        )
        work_btn = discord.ui.Button(
            label="💼 Work"   + (" ✅" if not s["work_rem"]  else f" — {s['work_rem']}"),
            style=discord.ButtonStyle.primary  if not s["work_rem"]  else discord.ButtonStyle.secondary,
            disabled=bool(s["work_rem"])
        )
        crime_btn = discord.ui.Button(
            label="⚠️ Crime"  + (" ✅" if not s["crime_rem"] else f" — {s['crime_rem']}"),
            style=discord.ButtonStyle.danger   if not s["crime_rem"] else discord.ButtonStyle.secondary,
            disabled=bool(s["crime_rem"])
        )
        daily_btn.callback = self._daily
        work_btn.callback  = self._work
        crime_btn.callback = self._crime
        self.add_item(daily_btn)
        self.add_item(work_btn)
        self.add_item(crime_btn)

    def _embed(self):
        s = self._state
        streak_txt = f"\n🔥 **{s['streak']}-day streak!**" if s.get("streak",0) > 0 else ""
        e = discord.Embed(
            title="💰 Economy Panel",
            description=f"**Balance:** {s['balance']:,} 🪙{streak_txt}",
            color=0xf1c40f,
        )
        e.set_author(name=self._ctx.author.display_name, icon_url=self._ctx.author.display_avatar.url)
        e.set_footer(text="Buttons unlock when cooldowns reset")
        return e

    async def _refresh(self, interaction: discord.Interaction):
        self._state = await _get_state(self._gid, self._uid)
        self._rebuild()
        try:
            await interaction.response.edit_message(embed=self._embed(), view=self)
        except Exception:
            await interaction.edit_original_response(embed=self._embed(), view=self)

    async def _run_cog_cmd(self, interaction: discord.Interaction, cog_name: str, method: str):
        """Run a cog method via a fake ctx. Returns True on success, False on handled error."""
        cog = interaction.client.cogs.get(cog_name)
        if not cog: return False
        sent_messages = []
        class FCtx:
            guild   = interaction.guild
            author  = interaction.user
            channel = interaction.channel
            bot     = interaction.client
            async def send(self_, *a, **kw):
                kw.pop("delete_after", None)
                # Store result in panel state instead of sending separate message
                if "embed" in kw:
                    sent_messages.append(kw["embed"])
                elif a:
                    sent_messages.append(a[0])
                # Send as ephemeral followup so it's visible but doesn't clutter channel
                try:
                    return await interaction.followup.send(*a, ephemeral=True, **{k:v for k,v in kw.items()})
                except Exception:
                    pass
            class message:
                @staticmethod
                async def delete(): pass
        fn = getattr(cog, method, None)
        if fn:
            try:
                await fn(FCtx())
                return True
            except Exception as e:
                print(f"DailyPanel _run_cog_cmd error: {e}")
                import traceback; traceback.print_exc()
                try:
                    await interaction.followup.send(f"❌ An error occurred: {str(e)[:100]}", ephemeral=True)
                except Exception:
                    pass
                return False
        return False

    async def _daily(self, interaction: discord.Interaction):
        if interaction.user.id != self._uid:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        try:
            await interaction.response.defer_update()
        except AttributeError:
            await interaction.response.defer()
        await self._run_cog_cmd(interaction, "Economy", "_do_daily")
        await self._refresh(interaction)

    async def _work(self, interaction: discord.Interaction):
        if interaction.user.id != self._uid:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        try:
            await interaction.response.defer_update()
        except AttributeError:
            await interaction.response.defer()
        await self._run_cog_cmd(interaction, "Jobs", "_do_work")
        await self._refresh(interaction)

    async def _crime(self, interaction: discord.Interaction):
        if interaction.user.id != self._uid:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        try:
            await interaction.response.defer_update()
        except AttributeError:
            await interaction.response.defer()
        await self._run_cog_cmd(interaction, "Jobs", "_do_crime")
        await self._refresh(interaction)


async def open_daily_panel(ctx):
    state = await _get_state(ctx.guild.id, ctx.author.id)
    view  = DailyPanelView(ctx, state)
    await ctx.send(embed=view._embed(), view=view)
