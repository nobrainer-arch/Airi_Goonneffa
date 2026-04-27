# airi/hub.py — Unified command hubs
# /economy  → full panel: balance + daily + work + crime + pay + give + lb + shop shortcut
# /social   → full panel: profile + rep + waifu + relationships + lb social
# /lb       → all leaderboard categories in one dropdown
# /server   → server info + setup/config buttons
# All functionality embedded — no redirecting to other commands.

import discord
from discord.ext import commands
from datetime import datetime, timezone
import db
from utils import _err, C_INFO, C_SUCCESS, C_WARN, C_ECONOMY, C_SOCIAL


# ═══════════════════════════════════════════════════════════════════
#  /economy  — Balance · Daily · Work · Crime · Pay · Give · Shop
# ═══════════════════════════════════════════════════════════════════
class EconomyHub(commands.Cog, name="EconomyHub"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_command(
        name="economy",
        aliases=["eco"],   # /economy is the ONE hub entry; old !daily !balance etc still work via prefix
        description="Economy hub — balance, daily, work, crime, pay, give",
    )
    async def economy(self, ctx, member: discord.Member = None, amount: int = None):
        """Full economy hub in one message. Buttons do the actual work."""
        # Quick-pay shortcut: !economy @user 500
        if member and amount:
            eco_cog = ctx.bot.cogs.get("Economy")
            if eco_cog:
                return await eco_cog.pay(ctx, member=member, amount=amount)

        await ctx.defer()
        view = EconomyPanelView(ctx)
        await view._load()
        msg = await ctx.send(embed=view._embed(), view=view)
        view._msg = msg


class EconomyPanelView(discord.ui.View):
    """All economy actions in one live-updating message."""

    def __init__(self, ctx):
        super().__init__(timeout=300)
        self._ctx  = ctx
        self._gid  = ctx.guild.id
        self._uid  = ctx.author.id
        self._state: dict = {}
        # Page: "home" | "pay" | "give" | "lb" | "shop_redirect"
        self._page = "home"

    async def _load(self):
        from airi.daily_panel import _get_state
        self._state = await _get_state(self._gid, self._uid)
        self._build()

    def _build(self):
        self.clear_items()
        s = self._state
        if self._page == "home":
            self._build_home(s)

    def _build_home(self, s):
        # Row 0: core earn buttons
        daily = discord.ui.Button(
            label="💰 Daily" + (" ✅" if not s.get("daily_rem") else f" {s['daily_rem']}"),
            style=discord.ButtonStyle.success if not s.get("daily_rem") else discord.ButtonStyle.secondary,
            disabled=bool(s.get("daily_rem")), row=0,
        )
        work = discord.ui.Button(
            label="💼 Work" + (" ✅" if not s.get("work_rem") else f" {s['work_rem']}"),
            style=discord.ButtonStyle.primary if not s.get("work_rem") else discord.ButtonStyle.secondary,
            disabled=bool(s.get("work_rem")), row=0,
        )
        crime = discord.ui.Button(
            label="⚠️ Crime" + (" ✅" if not s.get("crime_rem") else f" {s['crime_rem']}"),
            style=discord.ButtonStyle.danger if not s.get("crime_rem") else discord.ButtonStyle.secondary,
            disabled=bool(s.get("crime_rem")), row=0,
        )
        daily.callback = self._do_daily
        work.callback  = self._do_work
        crime.callback = self._do_crime
        self.add_item(daily); self.add_item(work); self.add_item(crime)

        # Row 1: transfer + leaderboard
        pay_btn = discord.ui.Button(label="💸 Pay", style=discord.ButtonStyle.secondary, row=1)
        give_btn= discord.ui.Button(label="🎁 Give",style=discord.ButtonStyle.secondary, row=1)
        lb_btn  = discord.ui.Button(label="📊 Leaderboard", style=discord.ButtonStyle.secondary, row=1)
        shop_btn= discord.ui.Button(label="🛒 Shop", style=discord.ButtonStyle.secondary, row=1)
        pay_btn.callback  = self._open_pay
        give_btn.callback = self._open_give
        lb_btn.callback   = self._open_lb
        shop_btn.callback = self._open_shop
        self.add_item(pay_btn); self.add_item(give_btn)
        self.add_item(lb_btn);  self.add_item(shop_btn)

    def _embed(self) -> discord.Embed:
        s = self._state
        bal     = s.get("balance", 0) or 0
        kak     = s.get("kakera",  0) or 0
        streak  = s.get("streak",  0) or 0
        streak_txt = f"\n🔥 **{streak}-day streak!**" if streak > 0 else ""
        e = discord.Embed(
            title="💰 Economy Panel",
            description=(
                f"**Balance:** {bal:,} 🪙  ·  **Kakera:** {kak:,} 💎"
                f"{streak_txt}"
            ),
            color=0xf1c40f,
            timestamp=datetime.now(timezone.utc),
        )
        e.set_author(name=self._ctx.author.display_name,
                     icon_url=self._ctx.author.display_avatar.url)
        e.set_footer(text="Buttons unlock when cooldowns reset · /economy @user <amount> to pay quickly")
        return e

    async def _panel_refresh(self, inter):
        from airi.daily_panel import _get_state
        self._state = await _get_state(self._gid, self._uid)
        self._build()
        try:
            await inter.edit_original_response(embed=self._embed(), view=self)
        except Exception:
            pass

    async def _run(self, inter, cog_name, method):
        """Run a cog method, capture result, show inline."""
        cog = inter.client.cogs.get(cog_name)
        if not cog: return
        result_embeds = []
        result_text   = []
        class FC:
            guild=inter.guild; author=inter.user; channel=inter.channel; bot=inter.client
            async def send(self_, *a, embed=None, **kw):
                if embed:   result_embeds.append(embed)
                elif a:     result_text.append(str(a[0])[:200])
                class M:
                    @staticmethod
                    async def delete(): pass
                return M()
        fn = getattr(cog, method, None)
        if fn:
            try:
                await fn(FC())
            except Exception as e:
                print(f"EconomyHub._run error ({cog_name}.{method}): {e}")
        return result_embeds, result_text

    async def _do_daily(self, inter: discord.Interaction):
        if inter.user.id != self._uid:
            return await inter.response.send_message("Not for you.", ephemeral=True)
        await inter.response.defer()
        embeds, texts = await self._run(inter, "Economy", "_do_daily") or ([],[])
        await self._panel_refresh(inter)
        # Show result inline for 2 seconds, then return to main panel
        import asyncio
        if embeds:
            try:
                result_e = embeds[0]
                result_e.set_footer(text="Returning to panel...")
                await inter.edit_original_response(embed=result_e, view=None)
                await asyncio.sleep(2.5)
            except: pass
        await self._panel_refresh(inter)

    async def _do_work(self, inter: discord.Interaction):
        if inter.user.id != self._uid:
            return await inter.response.send_message("Not for you.", ephemeral=True)
        await inter.response.defer()
        embeds, texts = await self._run(inter, "Jobs", "_do_work") or ([],[])
        await self._panel_refresh(inter)
        import asyncio
        if embeds:
            try:
                result_e = embeds[0]
                result_e.set_footer(text="Returning to panel...")
                await inter.edit_original_response(embed=result_e, view=None)
                await asyncio.sleep(2.5)
            except: pass
        await self._panel_refresh(inter)

    async def _do_crime(self, inter: discord.Interaction):
        if inter.user.id != self._uid:
            return await inter.response.send_message("Not for you.", ephemeral=True)
        await inter.response.defer()
        embeds, texts = await self._run(inter, "Jobs", "_do_crime") or ([],[])
        await self._panel_refresh(inter)
        import asyncio
        if embeds:
            try:
                result_e = embeds[0]
                result_e.set_footer(text="Returning to panel...")
                await inter.edit_original_response(embed=result_e, view=None)
                await asyncio.sleep(2.5)
            except: pass
        await self._panel_refresh(inter)

    async def _open_pay(self, inter: discord.Interaction):
        if inter.user.id != self._uid:
            return await inter.response.send_message("Not for you.", ephemeral=True)
        # Inline pay: user select → modal → send coins → refresh
        sel = discord.ui.UserSelect(placeholder="Who to pay…", min_values=1, max_values=1)
        back= discord.ui.Button(label="◀ Back", style=discord.ButtonStyle.secondary)

        async def sel_cb(i2: discord.Interaction):
            if i2.user.id != self._uid: return await i2.response.send_message("Not for you.", ephemeral=True)
            rec = sel.values[0]
            class PayModal(discord.ui.Modal, title=f"Pay {rec.display_name}"):
                amount_in = discord.ui.TextInput(label="Amount (coins)", placeholder="e.g. 500", required=True)
                async def on_submit(m, i3):
                    await i3.response.defer()
                    raw = m.amount_in.value.strip().replace(",","")
                    if not raw.isdigit():
                        return await i3.followup.send("❌ Invalid amount.", ephemeral=True)
                    eco_cog = i3.client.cogs.get("Economy")
                    if eco_cog:
                        class FC:
                            guild=i3.guild; author=i3.user; channel=i3.channel; bot=i3.client
                            async def send(self_,*a,**kw):
                                e=kw.get("embed"); txt=a[0] if a else None
                                if e:   await i3.followup.send(embed=e, ephemeral=True)
                                elif txt: await i3.followup.send(str(txt)[:200], ephemeral=True)
                        from airi.economy import get_balance, add_coins
                        bal = await get_balance(self._gid, i3.user.id)
                        amt = int(raw)
                        tax = int(amt * 0.05)
                        net = amt + tax
                        if bal < net:
                            pay_e = discord.Embed(description=f"❌ Need **{net:,}** (incl. 5% tax), have **{bal:,}**.",color=0xe74c3c)
                        else:
                            await add_coins(self._gid, i3.user.id, -net)
                            await add_coins(self._gid, rec.id, amt)
                            pay_e = discord.Embed(
                                title="💸 Payment Sent!",
                                description=f"Sent **{amt:,}** 🪙 to {rec.mention} (tax: {tax:,} 🪙).",
                                color=0x2ecc71,
                            )
                        pay_e.set_footer(text="Returning to panel...")
                        await i3.edit_original_response(embed=pay_e, view=None)
                        import asyncio; await asyncio.sleep(2)
                    from airi.daily_panel import _get_state
                    self._state = await _get_state(self._gid, self._uid)
                    self._build()
                    await i3.edit_original_response(embed=self._embed(), view=self)
            await i2.response.send_modal(PayModal())

        async def back_cb(i2: discord.Interaction):
            if i2.user.id != self._uid: return await i2.response.send_message("Not for you.", ephemeral=True)
            await self._refresh(i2)

        sel.callback  = sel_cb
        back.callback = back_cb
        pay_view = discord.ui.View(timeout=120)
        pay_view.add_item(sel); pay_view.add_item(back)

        e = discord.Embed(title="💸 Pay Someone",
                          description="Select a member to pay coins to (5% tax).",
                          color=C_ECONOMY)
        await inter.response.edit_message(embed=e, view=pay_view)

    async def _open_give(self, inter: discord.Interaction):
        if inter.user.id != self._uid:
            return await inter.response.send_message("Not for you.", ephemeral=True)
        sel  = discord.ui.UserSelect(placeholder="Give coins to…", min_values=1, max_values=1)
        back = discord.ui.Button(label="◀ Back", style=discord.ButtonStyle.secondary)

        async def sel_cb(i2):
            if i2.user.id != self._uid: return await i2.response.send_message("Not for you.", ephemeral=True)
            rec = sel.values[0]
            class GiveModal(discord.ui.Modal, title=f"Give {rec.display_name}"):
                amount_in = discord.ui.TextInput(label="Amount (coins, no tax)", placeholder="e.g. 200", required=True)
                async def on_submit(m, i3):
                    await i3.response.defer()
                    raw = m.amount_in.value.strip().replace(",","")
                    if not raw.isdigit(): return await i3.followup.send("❌ Invalid amount.", ephemeral=True)
                    eco_cog = i3.client.cogs.get("Economy")
                    if eco_cog:
                        class FC:
                            guild=i3.guild; author=i3.user; channel=i3.channel; bot=i3.client
                            async def send(self_,*a,**kw):
                                e=kw.get("embed"); t=a[0] if a else None
                                if e:   await i3.followup.send(embed=e, ephemeral=True)
                                elif t: await i3.followup.send(str(t)[:200], ephemeral=True)
                        from airi.economy import get_balance, add_coins
                        bal = await get_balance(self._gid, i3.user.id)
                        amt = int(raw)
                        if bal < amt:
                            give_e = discord.Embed(description=f"❌ Need **{amt:,}** 🪙, have **{bal:,}**.",color=0xe74c3c)
                        else:
                            await add_coins(self._gid, i3.user.id, -amt)
                            await add_coins(self._gid, rec.id, amt)
                            give_e = discord.Embed(
                                title="🎁 Gift Sent!",
                                description=f"Gave **{amt:,}** 🪙 to {rec.mention} (no tax).",
                                color=0x2ecc71,
                            )
                        give_e.set_footer(text="Returning to panel...")
                        await i3.edit_original_response(embed=give_e, view=None)
                        import asyncio; await asyncio.sleep(2)
                    from airi.daily_panel import _get_state
                    self._state = await _get_state(self._gid, self._uid)
                    self._build()
                    await i3.edit_original_response(embed=self._embed(), view=self)
            await i2.response.send_modal(GiveModal())

        async def back_cb(i2):
            if i2.user.id != self._uid: return await i2.response.send_message("Not for you.", ephemeral=True)
            await self._refresh(i2)

        sel.callback = sel_cb; back.callback = back_cb
        gv = discord.ui.View(timeout=120); gv.add_item(sel); gv.add_item(back)
        e = discord.Embed(title="🎁 Give Coins",
                          description="Select a member to give coins to (no tax).", color=C_ECONOMY)
        await inter.response.edit_message(embed=e, view=gv)

    async def _open_lb(self, inter: discord.Interaction):
        if inter.user.id != self._uid:
            return await inter.response.send_message("Not for you.", ephemeral=True)
        await inter.response.defer()
        from airi.leaderboard import _build_lb, LB_CATEGORIES
        lb_e = await _build_lb(inter.guild, "coins")
        opts = [
            discord.SelectOption(label=v[0][:50], value=k, default=(k=="coins"),
                emoji={"xp":"📈","coins":"💰","rep":"⭐","hugs":"🤗","kisses":"💋",
                       "pats":"🤚","rpg":"⚔️","marriage":"💍","waifuscore":"👑","proposals":"💌"}.get(k,"📊"))
            for k, v in LB_CATEGORIES.items()
        ]
        cat_sel = discord.ui.Select(placeholder="📊 Category…", options=opts)
        back_btn= discord.ui.Button(label="◀ Back", style=discord.ButtonStyle.secondary)

        async def cat_cb(i2):
            new_e = await _build_lb(i2.guild, cat_sel.values[0])
            for o in cat_sel.options: o.default = (o.value == cat_sel.values[0])
            await i2.response.edit_message(embed=new_e, view=lb_view)

        async def back_cb(i2):
            if i2.user.id != self._uid: return await i2.response.send_message("Not for you.", ephemeral=True)
            await self._refresh(i2)

        cat_sel.callback = cat_cb; back_btn.callback = back_cb
        lb_view = discord.ui.View(timeout=300)
        lb_view.add_item(cat_sel); lb_view.add_item(back_btn)
        await inter.edit_original_response(embed=lb_e, view=lb_view)

    async def _open_shop(self, inter: discord.Interaction):
        """Launch the unified shop inside this panel."""
        if inter.user.id != self._uid:
            return await inter.response.send_message("Not for you.", ephemeral=True)
        await inter.response.defer()
        from airi.rpg.shop import ShopView
        from airi.rpg.char import get_char
        char = await get_char(self._gid, self._uid)
        player_class = char.get("class") if char else None
        shop_view = ShopView(self._ctx, player_class)

        load_e = discord.Embed(title="🔄 Loading Shop…", description="Fetching from D&D 5e API…", color=C_INFO)
        await inter.edit_original_response(embed=load_e, view=None)
        await shop_view._load()
        shop_view._rebuild()

        # Add a Back button to shop view
        back_btn = discord.ui.Button(label="◀ Economy", style=discord.ButtonStyle.secondary, row=4)
        async def back_cb(i2):
            if i2.user.id != self._uid: return await i2.response.send_message("Not for you.", ephemeral=True)
            await i2.response.defer()
            await self._refresh(i2)
        back_btn.callback = back_cb
        shop_view.add_item(back_btn)

        await inter.edit_original_response(embed=shop_view._embed(), view=shop_view)


# ═══════════════════════════════════════════════════════════════════
#  /social  — Profile · Rep · Waifu · Relationships · Social LB
# ═══════════════════════════════════════════════════════════════════
class SocialHub(commands.Cog, name="SocialHub"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_command(
        name="social",
        aliases=["soc"],    # /social is the hub; !profile !rep still work via prefix
        description="Social hub — profile, rep, waifu, relationships",
    )
    async def social(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        gid, uid = ctx.guild.id, target.id
        await ctx.defer()

        eco  = await db.pool.fetchrow(
            "SELECT balance,kakera,active_title FROM economy WHERE guild_id=$1 AND user_id=$2", gid, uid)
        soc  = await db.pool.fetchrow(
            "SELECT rep,hugs_received,kisses_received,pats_received FROM social WHERE guild_id=$1 AND user_id=$2", gid, uid)
        rel  = await db.pool.fetchrow(
            "SELECT type,user1_id,user2_id FROM relationships WHERE guild_id=$1 AND (user1_id=$2 OR user2_id=$2) AND status='active' ORDER BY CASE type WHEN 'married' THEN 0 WHEN 'dating' THEN 1 ELSE 2 END LIMIT 1",
            gid, uid)
        claim_count = await db.pool.fetchval("SELECT COUNT(*) FROM claims WHERE guild_id=$1 AND claimer_id=$2", gid, uid) or 0
        is_claimed  = await db.pool.fetchval("SELECT claimer_id FROM claims WHERE guild_id=$1 AND claimed_id=$2", gid, uid)

        title  = (eco or {}).get("active_title","")
        bal    = (eco or {}).get("balance",0) or 0
        kak    = (eco or {}).get("kakera",0) or 0
        rep    = (soc or {}).get("rep",0) or 0
        hugs   = (soc or {}).get("hugs_received",0) or 0
        kisses = (soc or {}).get("kisses_received",0) or 0
        pats   = (soc or {}).get("pats_received",0) or 0

        rel_txt = "Single 💔"
        if rel:
            pid = rel["user2_id"] if rel["user1_id"]==uid else rel["user1_id"]
            pm  = ctx.guild.get_member(pid)
            icons = {"married":"💍","dating":"💘","hookup":"💋"}
            rel_txt = f"{icons.get(rel['type'],'❤️')} {rel['type'].title()} with {pm.mention if pm else f'<@{pid}>'}"

        e = discord.Embed(color=C_SOCIAL, timestamp=datetime.now(timezone.utc))
        e.set_author(name=f"{'✨ '+title+'  ·  ' if title else ''}{target.display_name}",
                     icon_url=target.display_avatar.url)
        e.set_thumbnail(url=target.display_avatar.url)
        e.add_field(name="💰 Coins",   value=f"{bal:,}",  inline=True)
        e.add_field(name="💎 Kakera",  value=f"{kak:,}",  inline=True)
        e.add_field(name="⭐ Rep",      value=f"{rep:,}",  inline=True)
        e.add_field(name="💕 Relationship", value=rel_txt, inline=False)
        e.add_field(name="👑 Waifus",  value=f"{claim_count} claimed"+(f" · Owned by <@{is_claimed}>" if is_claimed else ""), inline=True)
        e.add_field(name="🤗 Hugs",   value=str(hugs),   inline=True)
        e.add_field(name="💋 Kisses", value=str(kisses), inline=True)
        e.add_field(name="🤚 Pats",   value=str(pats),   inline=True)

        view = SocialPanelView(ctx, target, uid, e)
        await ctx.send(embed=e, view=view)


class SocialPanelView(discord.ui.View):
    def __init__(self, ctx, target, target_uid, profile_embed):
        super().__init__(timeout=300)
        self._ctx    = ctx
        self._target = target
        self._tuid   = target_uid
        self._home   = profile_embed
        self._is_own = (ctx.author.id == target_uid)

    @discord.ui.button(label="⭐ Give Rep", style=discord.ButtonStyle.primary, row=0)
    async def rep_btn(self, inter, btn):
        if inter.user.id == self._tuid:
            return await inter.response.send_message("Can't rep yourself.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        cog = inter.client.cogs.get("Social")
        if cog:
            class FC:
                guild=inter.guild; author=inter.user; channel=inter.channel
                async def send(self_,*a,**kw): return await inter.followup.send(*a, ephemeral=True, **kw)
                message=None
            await cog._do_rep(FC(), self._target)

    @discord.ui.button(label="🎴 Waifus", style=discord.ButtonStyle.secondary, row=0)
    async def waifus_btn(self, inter, btn):
        await inter.response.defer()
        cog = inter.client.cogs.get("Social")
        if cog:
            class FC:
                guild=inter.guild; author=inter.user; channel=inter.channel
                async def send(self_,*a,**kw):
                    kw.pop("delete_after",None)
                    return await inter.followup.send(*a,**kw)
                message=None
            await cog.mywaifu(FC(), self._target)

    @discord.ui.button(label="💕 Relationship", style=discord.ButtonStyle.secondary, row=0)
    async def rel_btn(self, inter, btn):
        await inter.response.defer()
        cog = inter.client.cogs.get("Relationship")
        if cog:
            class FC:
                guild=inter.guild; author=inter.user; channel=inter.channel
                async def send(self_,*a,**kw):
                    kw.pop("delete_after",None)
                    return await inter.followup.send(*a,**kw)
                message=None
            await cog.myrel(FC())
        else:
            await inter.followup.send("Relationship module unavailable.", ephemeral=True)

    @discord.ui.button(label="📊 Social LB", style=discord.ButtonStyle.secondary, row=1)
    async def lb_btn(self, inter, btn):
        await inter.response.defer()
        from airi.leaderboard import _build_lb, LB_CATEGORIES
        e = await _build_lb(inter.guild, "hugs")
        opts = [
            discord.SelectOption(label=v[0][:50], value=k, default=(k=="hugs"),
                emoji={"xp":"📈","coins":"💰","rep":"⭐","hugs":"🤗","kisses":"💋",
                       "pats":"🤚","rpg":"⚔️","marriage":"💍","waifuscore":"👑","proposals":"💌"}.get(k,"📊"))
            for k, v in LB_CATEGORIES.items()
        ]
        cat_sel = discord.ui.Select(placeholder="📊 Category…", options=opts)
        back_btn= discord.ui.Button(label="◀ Profile", style=discord.ButtonStyle.secondary)
        async def cat_cb(i2):
            new_e = await _build_lb(i2.guild, cat_sel.values[0])
            for o in cat_sel.options: o.default=(o.value==cat_sel.values[0])
            await i2.response.edit_message(embed=new_e, view=lbv)
        async def back_cb(i2):
            await i2.response.edit_message(embed=self._home, view=self)
        cat_sel.callback=cat_cb; back_btn.callback=back_cb
        lbv=discord.ui.View(timeout=300); lbv.add_item(cat_sel); lbv.add_item(back_btn)
        await inter.edit_original_response(embed=e, view=lbv)

    @discord.ui.button(label="👤 View Profile", style=discord.ButtonStyle.secondary, row=1)
    async def refresh_btn(self, inter, btn):
        """Reload profile with fresh data."""
        await inter.response.defer()
        gid, uid = inter.guild_id, self._tuid
        eco  = await db.pool.fetchrow("SELECT balance,kakera,active_title FROM economy WHERE guild_id=$1 AND user_id=$2",gid,uid)
        soc  = await db.pool.fetchrow("SELECT rep,hugs_received,kisses_received,pats_received FROM social WHERE guild_id=$1 AND user_id=$2",gid,uid)
        rel  = await db.pool.fetchrow("SELECT type,user1_id,user2_id FROM relationships WHERE guild_id=$1 AND (user1_id=$2 OR user2_id=$2) AND status='active' ORDER BY CASE type WHEN 'married' THEN 0 WHEN 'dating' THEN 1 ELSE 2 END LIMIT 1",gid,uid)
        title=(eco or {}).get("active_title",""); bal=(eco or {}).get("balance",0) or 0
        kak=(eco or {}).get("kakera",0) or 0; rep=(soc or {}).get("rep",0) or 0
        hugs=(soc or {}).get("hugs_received",0) or 0
        kisses=(soc or {}).get("kisses_received",0) or 0
        pats=(soc or {}).get("pats_received",0) or 0
        claim_count=await db.pool.fetchval("SELECT COUNT(*) FROM claims WHERE guild_id=$1 AND claimer_id=$2",gid,uid) or 0
        is_claimed=await db.pool.fetchval("SELECT claimer_id FROM claims WHERE guild_id=$1 AND claimed_id=$2",gid,uid)
        rel_txt="Single 💔"
        if rel:
            pid=rel["user2_id"] if rel["user1_id"]==uid else rel["user1_id"]
            pm=inter.guild.get_member(pid)
            icons={"married":"💍","dating":"💘","hookup":"💋"}
            rel_txt=f"{icons.get(rel['type'],'❤️')} {rel['type'].title()} with {pm.mention if pm else f'<@{pid}>'}"
        e=discord.Embed(color=C_SOCIAL,timestamp=datetime.now(timezone.utc))
        e.set_author(name=f"{'✨ '+title+'  ·  ' if title else ''}{self._target.display_name}",icon_url=self._target.display_avatar.url)
        e.set_thumbnail(url=self._target.display_avatar.url)
        e.add_field(name="💰 Coins",value=f"{bal:,}",inline=True)
        e.add_field(name="💎 Kakera",value=f"{kak:,}",inline=True)
        e.add_field(name="⭐ Rep",value=f"{rep:,}",inline=True)
        e.add_field(name="💕 Relationship",value=rel_txt,inline=False)
        e.add_field(name="👑 Waifus",value=f"{claim_count} claimed"+(f" · Owned by <@{is_claimed}>" if is_claimed else ""),inline=True)
        e.add_field(name="🤗 Hugs",value=str(hugs),inline=True)
        e.add_field(name="💋 Kisses",value=str(kisses),inline=True)
        e.add_field(name="🤚 Pats",value=str(pats),inline=True)
        self._home=e
        await inter.edit_original_response(embed=e, view=self)


# ═══════════════════════════════════════════════════════════════════
#  /lb  — All leaderboard categories in one dropdown
# ═══════════════════════════════════════════════════════════════════
class LeaderboardHub(commands.Cog, name="LeaderboardHub"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_command(
        name="lb",
        aliases=["top"],   # /lb is the hub entry
        description="Server leaderboards — XP, coins, hugs, RPG power, and more",
    )
    async def lb(self, ctx, category: str = "xp"):
        from airi.leaderboard import _build_lb, LB_CATEGORIES
        cat = category.lower()
        if cat not in LB_CATEGORIES: cat = "xp"
        e = await _build_lb(ctx.guild, cat)
        opts = [
            discord.SelectOption(
                label=v[0][:50], value=k, default=(k==cat),
                emoji={"xp":"📈","coins":"💰","rep":"⭐","hugs":"🤗","kisses":"💋",
                       "pats":"🤚","rpg":"⚔️","marriage":"💍","waifuscore":"👑","proposals":"💌"}.get(k,"📊"),
            )
            for k, v in LB_CATEGORIES.items()
        ]
        sel = discord.ui.Select(placeholder="📊 Change category…", options=opts)
        async def sel_cb(inter):
            new_e = await _build_lb(inter.guild, sel.values[0])
            for o in sel.options: o.default=(o.value==sel.values[0])
            await inter.response.edit_message(embed=new_e, view=view)
        sel.callback = sel_cb
        class LBView(discord.ui.View):
            def __init__(lv): super().__init__(timeout=300); lv.add_item(sel)
        view = LBView()
        await ctx.send(embed=e, view=view)


# ═══════════════════════════════════════════════════════════════════
#  /server  — Server info + setup / config buttons
# ═══════════════════════════════════════════════════════════════════
class ServerHub(commands.Cog, name="ServerHub"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_command(
        name="server",
        aliases=["serverinfo","si"],
        description="Server info and configuration hub",
    )
    async def server(self, ctx):
        g = ctx.guild
        e = discord.Embed(title=f"🌐 {g.name}", color=C_INFO, timestamp=datetime.now(timezone.utc))
        e.set_thumbnail(url=g.icon.url if g.icon else None)
        e.add_field(name="👥 Members", value=f"{g.member_count:,}",  inline=True)
        e.add_field(name="📅 Created",  value=discord.utils.format_dt(g.created_at,"R"), inline=True)
        e.add_field(name="👑 Owner",    value=g.owner.mention if g.owner else "?", inline=True)
        e.add_field(name="📝 Channels", value=f"{len(g.text_channels)} text · {len(g.voice_channels)} voice", inline=True)
        e.add_field(name="🎭 Roles",    value=str(len(g.roles)), inline=True)

        setup_btn  = discord.ui.Button(label="⚙️ Run Setup",    style=discord.ButtonStyle.primary)
        config_btn = discord.ui.Button(label="📋 View Config",  style=discord.ButtonStyle.secondary)

        async def setup_cb(inter):
            if not inter.user.guild_permissions.manage_guild:
                return await inter.response.send_message("Need **Manage Server** permission.", ephemeral=True)
            cog = inter.client.cogs.get("Setup")
            if cog:
                await inter.response.defer()
                class FC:
                    guild=inter.guild; author=inter.user; bot=inter.client
                    async def send(s,*a,**kw): return await inter.followup.send(*a,**kw)
                await cog.setup(FC())
            else:
                await inter.response.send_message("Setup module unavailable.", ephemeral=True)

        async def config_cb(inter):
            cog = inter.client.cogs.get("Config")
            if cog:
                await inter.response.defer(ephemeral=True)
                class FC:
                    guild=inter.guild; author=inter.user; bot=inter.client
                    async def send(s,*a,**kw): return await inter.followup.send(*a, ephemeral=True, **kw)
                await cog.cfg_show(FC())
            else:
                await inter.response.send_message("Config unavailable.", ephemeral=True)

        setup_btn.callback  = setup_cb
        config_btn.callback = config_cb
        v = discord.ui.View(timeout=120)
        v.add_item(setup_btn); v.add_item(config_btn)
        await ctx.send(embed=e, view=v)
