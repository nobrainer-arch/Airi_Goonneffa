# airi/social.py — Social commands: profile, rep, claim, release, mywaifu, waifu, lb
import discord
from discord.ext import commands
from datetime import datetime, timedelta, timezone
import db
from utils import _err, C_SOCIAL, C_ECONOMY, C_SUCCESS, C_WARN
from airi.guild_config import check_channel, get_channel, K_PROFILE

CLAIM_COST  = 2500

def _make_tz_aware(ts):
    if ts is None: return None
    from datetime import timezone as _tz
    if hasattr(ts, "tzinfo") and ts.tzinfo is not None: return ts
    return ts.replace(tzinfo=_tz.utc)
REP_COOLDOWN = 24  # hours
PAGE_SIZE    = 1   # one card per page in mywaifu

# ── Shared leaderboard builder ────────────────────────────────────
from airi.leaderboard import _build_lb, LB_CATEGORIES

# ── HaremView ─────────────────────────────────────────────────────
import discord

class HaremView(discord.ui.View):
    def __init__(self, rows: list[dict], target: discord.Member, author_id: int):
        super().__init__(timeout=300)
        self.rows = rows
        self.target = target
        self.author_id = author_id
        self.cur = 0

        self._update_buttons()

    # ========================= EMBED =========================
    def build(self, idx: int) -> discord.Embed:
        r = self.rows[idx]
        uid = r["claimed_id"]

        m = self.target.guild.get_member(uid)
        name = m.display_name if m else f"<@{uid}>"

        e = discord.Embed(
            title=f"💕 {self.target.display_name}'s Harem ({len(self.rows)})",
            description=f"**#{idx+1}** — {m.mention if m else name}",
            color=0xFF66AA
        )

        if m:
            e.set_image(url=m.display_avatar.url)

        e.set_thumbnail(url=self.target.display_avatar.url)

        claimed = r.get("claimed_at")
        e.set_footer(
            text=f"Card {idx+1}/{len(self.rows)} · Claimed {claimed.strftime('%b %d %Y') if claimed else '?'}"
        )

        return e

    # ========================= BUTTON STATE =========================
    def _update_buttons(self):
        self.prev_button.disabled = self.cur == 0
        self.next_button.disabled = self.cur == len(self.rows) - 1

    # ========================= PERMISSION =========================
    async def _check(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Not for you.", ephemeral=True)
            return False
        return True

    # ========================= BUTTONS =========================
    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check(interaction):
            return

        self.cur -= 1
        self._update_buttons()

        await interaction.response.edit_message(
            embed=self.build(self.cur),
            view=self
        )

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check(interaction):
            return

        self.cur += 1
        self._update_buttons()

        await interaction.response.edit_message(
            embed=self.build(self.cur),
            view=self
        )

    @discord.ui.button(label="🎁 Give", style=discord.ButtonStyle.primary)
    async def give_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id:
            return await interaction.response.send_message(
                "Only the owner can give.",
                ephemeral=True
            )

        r = self.rows[self.cur]
        uid = r["claimed_id"]

        select = discord.ui.UserSelect(placeholder="Give waifu to...")

        async def select_callback(i2: discord.Interaction):
            recipient = select.values[0]

            if recipient.bot or recipient.id == self.target.id:
                return await i2.response.send_message(
                    "Invalid recipient.",
                    ephemeral=True
                )

            try:
                await db.pool.execute(
                    "UPDATE claims SET claimer_id=$1 WHERE guild_id=$2 AND claimed_id=$3",
                    recipient.id,
                    interaction.guild_id,
                    uid
                )

                await i2.response.edit_message(
                    content=f"✅ Waifu given to {recipient.mention}!",
                    view=None
                )

            except Exception as e:
                await i2.response.send_message(
                    "❌ Failed to transfer.",
                    ephemeral=True
                )

        select.callback = select_callback

        give_view = discord.ui.View(timeout=60)
        give_view.add_item(select)

        await interaction.response.send_message(
            "Give this waifu to:",
            view=give_view,
            ephemeral=True
        )

class SocialCog(commands.Cog, name="Social"):
    def __init__(self, bot): self.bot = bot

    # ── Profile ───────────────────────────────────────────────────
    @commands.hybrid_command(name="profile", aliases=["pf"], description="View your profile")
    async def profile(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        gid, uid = ctx.guild.id, target.id
        eco  = await db.pool.fetchrow("SELECT balance,kakera,active_title,level FROM economy LEFT JOIN xp USING (guild_id,user_id) WHERE economy.guild_id=$1 AND economy.user_id=$2", gid, uid)
        soc  = await db.pool.fetchrow("SELECT rep,hugs_received,kisses_received,pats_received FROM social WHERE guild_id=$1 AND user_id=$2", gid, uid)
        rel  = await db.pool.fetchrow("SELECT type,user1_id,user2_id FROM relationships WHERE guild_id=$1 AND (user1_id=$2 OR user2_id=$2) AND status='active' ORDER BY CASE type WHEN 'married' THEN 0 WHEN 'dating' THEN 1 ELSE 2 END LIMIT 1", gid, uid)
        claim_count = await db.pool.fetchval("SELECT COUNT(*) FROM claims WHERE guild_id=$1 AND claimer_id=$2", gid, uid) or 0
        is_claimed  = await db.pool.fetchval("SELECT claimer_id FROM claims WHERE guild_id=$1 AND claimed_id=$2", gid, uid)

        title = (eco or {}).get("active_title","")
        bal   = (eco or {}).get("balance",0) or 0
        kak   = (eco or {}).get("kakera",0) or 0
        rep   = (soc or {}).get("rep",0) or 0
        hugs  = (soc or {}).get("hugs_received",0) or 0
        kisses= (soc or {}).get("kisses_received",0) or 0
        pats  = (soc or {}).get("pats_received",0) or 0

        rel_txt = "Single 💔"
        if rel:
            partner_id = rel["user2_id"] if rel["user1_id"] == uid else rel["user1_id"]
            pm = ctx.guild.get_member(partner_id)
            icons = {"married":"💍","dating":"💘","hookup":"💋"}
            rel_txt = f"{icons.get(rel['type'],'❤️')} {rel['type'].title()} with {pm.mention if pm else f'<@{partner_id}>'}"

        e = discord.Embed(color=C_SOCIAL, timestamp=datetime.now(timezone.utc))
        e.set_author(name=f"{'✨ '+title+'  ·  ' if title else ''}{target.display_name}", icon_url=target.display_avatar.url)
        e.set_thumbnail(url=target.display_avatar.url)
        e.add_field(name="💰 Coins",   value=f"{bal:,}", inline=True)
        e.add_field(name="💎 Kakera",  value=f"{kak:,}", inline=True)
        e.add_field(name="⭐ Rep",      value=f"{rep:,}", inline=True)
        e.add_field(name="💕 Relationship", value=rel_txt, inline=False)
        e.add_field(name="👑 Waifus", value=f"{claim_count} claimed"+(f" · Owned by <@{is_claimed}>" if is_claimed else ""), inline=True)
        e.add_field(name="🤗 Hugs",   value=str(hugs),   inline=True)
        e.add_field(name="💋 Kisses", value=str(kisses), inline=True)
        e.add_field(name="🤚 Pats",   value=str(pats),   inline=True)

        is_own = target == ctx.author
        class ProfileView(discord.ui.View):
            def __init__(self_): super().__init__(timeout=180)
            @discord.ui.button(label="⭐ Give Rep", style=discord.ButtonStyle.primary)
            async def rep_btn(self_, inter, btn):
                if inter.user.id == uid: return await inter.response.send_message("Can't rep yourself.", ephemeral=True)
                await inter.response.defer(ephemeral=True)
                cog = inter.client.cogs.get("Social")
                if cog:
                    class FC:
                        guild=inter.guild; author=inter.user; channel=inter.channel
                        async def send(self__,*a,**kw): return await inter.followup.send(*a,**kw)
                        message=None
                    await cog._do_rep(FC(), target)
            @discord.ui.button(label="👑 Claim", style=discord.ButtonStyle.success)
            async def claim_btn(self_, inter, btn):
                if not is_own: return await inter.response.send_message("Only your own profile.", ephemeral=True)
                await inter.response.send_message("Use `!claim @user` to claim.", ephemeral=True)
            @discord.ui.button(label="🎴 Waifus", style=discord.ButtonStyle.secondary)
            async def waifu_btn(self_, inter, btn):
                cog = inter.client.cogs.get("Social")
                if cog:
                    class FC:
                        guild=inter.guild; author=inter.user; channel=inter.channel
                        async def send(self__,*a,**kw): return await inter.followup.send(*a,**kw)
                        message=None
                    await inter.response.defer()
                    await cog.mywaifu(FC(), target)

        await ctx.send(embed=e, view=ProfileView())

    # ── Rep ───────────────────────────────────────────────────────
    @commands.hybrid_command(name="rep", aliases=["reputation"], description="Give someone reputation")
    async def rep(self, ctx, member: discord.Member = None):
        if not await check_channel(ctx, "social"): return
        if member is None:
            sel = discord.ui.UserSelect(placeholder="Give rep to…")
            async def cb(inter):
                if inter.user.id != ctx.author.id: return await inter.response.send_message("Not for you.", ephemeral=True)
                for i in v.children: i.disabled = True
                await inter.response.edit_message(view=v)
                class FC:
                    guild=inter.guild; author=inter.user; channel=inter.channel
                    async def send(self_,*a,**kw): return await inter.followup.send(*a,**kw)
                    message=None
                await self._do_rep(FC(), sel.values[0])
            sel.callback = cb
            class v(discord.ui.View):
                def __init__(self_): super().__init__(timeout=120); self_.add_item(sel)
            return await ctx.send(embed=discord.Embed(description="Give rep to:", color=C_SOCIAL), view=v())
        await self._do_rep(ctx, member)

    async def _do_rep(self, ctx, member: discord.Member):
        from datetime import timezone as _tz
        if member == ctx.author or member.bot: return await _err(ctx, "Invalid target.")
        gid, now = ctx.guild.id, datetime.now(_tz.utc)
        row = await db.pool.fetchrow("SELECT last_rep_given FROM social WHERE guild_id=$1 AND user_id=$2", gid, ctx.author.id)
        if row and row["last_rep_given"]:
            lr = row["last_rep_given"]
            if lr and (not hasattr(lr,'tzinfo') or lr.tzinfo is None): lr = lr.replace(tzinfo=_tz.utc)
            elapsed = now - lr
            if elapsed < timedelta(hours=REP_COOLDOWN):
                rem = timedelta(hours=REP_COOLDOWN) - elapsed
                h, s = divmod(int(rem.total_seconds()), 3600)
                return await _err(ctx, f"Give rep again in **{h}h {s//60}m**.")
        await db.pool.execute("INSERT INTO social (guild_id,user_id,last_rep_given) VALUES ($1,$2,$3) ON CONFLICT (guild_id,user_id) DO UPDATE SET last_rep_given=$3", gid, ctx.author.id, now)
        new_rep = await db.pool.fetchval("INSERT INTO social (guild_id,user_id,rep) VALUES ($1,$2,1) ON CONFLICT (guild_id,user_id) DO UPDATE SET rep=social.rep+1 RETURNING rep", gid, member.id)
        await ctx.send(embed=discord.Embed(description=f"⭐ {ctx.author.mention} gave rep to {member.mention}! They now have **{new_rep}** rep.", color=C_SOCIAL))

    # ── Claim ─────────────────────────────────────────────────────
    @commands.hybrid_command(name="claim", description="Claim someone as your waifu")
    async def claim(self, ctx, member: discord.Member = None):
        if not await check_channel(ctx, "social"): return
        if member is None:
            sel = discord.ui.UserSelect(placeholder="Claim someone as your waifu…")
            async def cb(inter):
                if inter.user.id != ctx.author.id: return await inter.response.send_message("Not for you.", ephemeral=True)
                for i in v.children: i.disabled = True
                await inter.response.edit_message(view=v)
                class FC:
                    guild=inter.guild; author=inter.user; channel=inter.channel
                    async def send(self_,*a,**kw): return await inter.followup.send(*a,**kw)
                    message=None
                await self._do_claim(FC(), sel.values[0])
            sel.callback = cb
            class v(discord.ui.View):
                def __init__(self_): super().__init__(timeout=120); self_.add_item(sel)
            return await ctx.send(embed=discord.Embed(description="Claim who as your waifu?", color=C_SOCIAL), view=v())
        await self._do_claim(ctx, member)

    async def _do_claim(self, ctx, member: discord.Member):
        if member == ctx.author or member.bot: return await _err(ctx, "Invalid target.")
        gid, uid = ctx.guild.id, ctx.author.id
        if await db.pool.fetchval("SELECT 1 FROM nsfw_optout WHERE guild_id=$1 AND user_id=$2", gid, member.id):
            return await _err(ctx, f"{member.display_name} has opted out of claims.")
        existing = await db.pool.fetchval("SELECT claimer_id FROM claims WHERE guild_id=$1 AND claimed_id=$2", gid, member.id)
        if existing:
            owner = ctx.guild.get_member(existing)
            return await _err(ctx, f"{member.display_name} is already claimed by {owner.mention if owner else f'<@{existing}>'}.")
        bal = await db.pool.fetchval("SELECT balance FROM economy WHERE guild_id=$1 AND user_id=$2", gid, uid) or 0
        if bal < CLAIM_COST:
            return await _err(ctx, f"Need **{CLAIM_COST:,}** coins to claim. You have **{bal:,}**.")

        class ConfirmView(discord.ui.View):
            def __init__(self_): super().__init__(timeout=30)
            @discord.ui.button(label=f"✅ Pay {CLAIM_COST:,} coins", style=discord.ButtonStyle.success)
            async def yes(self_, inter, btn):
                if inter.user.id != uid: return await inter.response.send_message("Not for you.", ephemeral=True)
                for i in self_.children: i.disabled = True
                await inter.response.edit_message(view=self_)
                row = await db.pool.fetchrow("UPDATE economy SET balance=balance-$1 WHERE guild_id=$2 AND user_id=$3 AND balance>=$1 RETURNING balance", CLAIM_COST, gid, uid)
                if not row: return await inter.followup.send("❌ Not enough coins.", ephemeral=True)
                try:
                    await db.pool.execute("INSERT INTO claims (guild_id,claimer_id,claimed_id) VALUES ($1,$2,$3)", gid, uid, member.id)
                except Exception:
                    await db.pool.execute("UPDATE economy SET balance=balance+$1 WHERE guild_id=$2 AND user_id=$3", CLAIM_COST, gid, uid)
                    return await inter.followup.send("❌ Already claimed.", ephemeral=True)
                await inter.followup.send(embed=discord.Embed(description=f"💕 {inter.user.mention} claimed **{member.display_name}** as their waifu!", color=C_SOCIAL))
                self_.stop()
            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def no(self_, inter, btn):
                if inter.user.id != uid: return await inter.response.send_message("Not for you.", ephemeral=True)
                for i in self_.children: i.disabled = True
                await inter.response.edit_message(content="Cancelled.", view=self_)
                self_.stop()

        await ctx.send(embed=discord.Embed(description=f"Claim **{member.display_name}** for **{CLAIM_COST:,}** coins?", color=C_SOCIAL), view=ConfirmView())

    # ── Release ───────────────────────────────────────────────────
    @commands.hybrid_command(name="release", description="Release a claimed waifu")
    async def release(self, ctx, member: discord.Member = None):
        if not await check_channel(ctx, "social"): return
        gid, uid = ctx.guild.id, ctx.author.id
        if member is None:
            rows = await db.pool.fetch("SELECT claimed_id FROM claims WHERE guild_id=$1 AND claimer_id=$2", gid, uid)
            if not rows: return await _err(ctx, "You haven't claimed anyone.")
            opts = []
            for r in rows:
                m = ctx.guild.get_member(r["claimed_id"])
                if m: opts.append(discord.SelectOption(label=m.display_name, value=str(m.id)))
            if not opts: return await _err(ctx, "None of your waifus are in this server.")
            sel = discord.ui.Select(placeholder="Release who?", options=opts[:25])
            async def cb(inter):
                if inter.user.id != uid: return await inter.response.send_message("Not for you.", ephemeral=True)
                tid = int(sel.values[0])
                await db.pool.execute("DELETE FROM claims WHERE guild_id=$1 AND claimer_id=$2 AND claimed_id=$3", gid, uid, tid)
                tm = inter.guild.get_member(tid)
                for i in v.children: i.disabled = True
                await inter.response.edit_message(content=f"✅ Released **{tm.display_name if tm else tid}**.", view=v)
            sel.callback = cb
            class v(discord.ui.View):
                def __init__(self_): super().__init__(timeout=120); self_.add_item(sel)
            return await ctx.send("Release which waifu?", view=v())
        await db.pool.execute("DELETE FROM claims WHERE guild_id=$1 AND claimer_id=$2 AND claimed_id=$3", gid, uid, member.id)
        await ctx.send(embed=discord.Embed(description=f"💔 {ctx.author.mention} released **{member.display_name}**.", color=C_WARN))

    # ── My Waifu ──────────────────────────────────────────────────
    @commands.hybrid_command(name="mywaifu", aliases=["harem","mywaifus"], description="View your waifu harem")
    async def mywaifu(self, ctx, member: discord.Member = None):
        if not await check_channel(ctx, "social"): return
        target = member or ctx.author
        gid, uid = ctx.guild.id, target.id
        rows = await db.pool.fetch("SELECT claimed_id, claimed_at FROM claims WHERE guild_id=$1 AND claimer_id=$2 ORDER BY claimed_at DESC", gid, uid)
        if not rows:
            whose = "You have" if target == ctx.author else f"{target.display_name} has"
            return await ctx.send(embed=discord.Embed(description=f"{whose} no waifus. Use `!claim @user`!", color=C_WARN))
        rows_list = [dict(r) for r in rows]
        view = HaremView(rows_list, target, ctx.author.id)
        await ctx.send(embed=view.build(0), view=view)

    # ── Waifu info ────────────────────────────────────────────────
    @commands.hybrid_command(name="waifu", description="Check someone's waifu status")
    async def waifu(self, ctx, member: discord.Member = None):
        if not await check_channel(ctx, "social"): return
        if member is None:
            sel = discord.ui.UserSelect(placeholder="Check waifu status of…")
            async def cb(inter):
                if inter.user.id != ctx.author.id: return await inter.response.send_message("Not for you.", ephemeral=True)
                for i in v.children: i.disabled = True
                await inter.response.edit_message(view=v)
                await inter.followup.send(embed=await self._waifu_embed(ctx.guild, sel.values[0], inter.user))
            sel.callback = cb
            class v(discord.ui.View):
                def __init__(self_): super().__init__(timeout=120); self_.add_item(sel)
            return await ctx.send("Check waifu status of:", view=v())
        e = await self._waifu_embed(ctx.guild, member, ctx.author)
        await ctx.send(embed=e)

    async def _waifu_embed(self, guild, target, viewer):
        gid, tid = guild.id, target.id
        owner_id    = await db.pool.fetchval("SELECT claimer_id FROM claims WHERE guild_id=$1 AND claimed_id=$2", gid, tid)
        owned_count = await db.pool.fetchval("SELECT COUNT(*) FROM claims WHERE guild_id=$1 AND claimer_id=$2", gid, tid) or 0
        owner_m  = guild.get_member(owner_id) if owner_id else None
        shielded = await db.pool.fetchval("SELECT quantity FROM inventory WHERE guild_id=$1 AND user_id=$2 AND item_key='shield' AND quantity>0", gid, tid) or 0
        e = discord.Embed(title=f"💕 {target.display_name}", color=C_SOCIAL)
        e.set_thumbnail(url=target.display_avatar.url)
        e.add_field(name="Owner",    value=owner_m.mention if owner_m else "Unclaimed 💔", inline=True)
        e.add_field(name="Waifus",   value=f"{owned_count} claimed", inline=True)
        e.add_field(name="Shield",   value="🛡️ Protected" if shielded else "Unprotected", inline=True)
        return e

    # ── NSFW Opt-out ──────────────────────────────────────────────
    @commands.hybrid_command(name="nsfwoptout", description="Toggle NSFW command opt-out")
    async def nsfwoptout(self, ctx):
        gid, uid = ctx.guild.id, ctx.author.id
        exists = await db.pool.fetchval("SELECT 1 FROM nsfw_optout WHERE guild_id=$1 AND user_id=$2", gid, uid)
        class ToggleView(discord.ui.View):
            def __init__(self_): super().__init__(timeout=60)
            @discord.ui.button(label="🔞 Opt OUT (block NSFW)", style=discord.ButtonStyle.danger if not exists else discord.ButtonStyle.secondary, disabled=bool(exists))
            async def opt_out(self_, inter, btn):
                if inter.user.id != uid: return await inter.response.send_message("Not for you.", ephemeral=True)
                await db.pool.execute("INSERT INTO nsfw_optout (guild_id,user_id) VALUES ($1,$2) ON CONFLICT DO NOTHING", gid, uid)
                for i in self_.children: i.disabled = True
                await inter.response.edit_message(content="✅ NSFW opt-out enabled. Others cannot NSFW-action you.", view=self_)
            @discord.ui.button(label="✅ Opt IN (allow NSFW)", style=discord.ButtonStyle.success if exists else discord.ButtonStyle.secondary, disabled=not bool(exists))
            async def opt_in(self_, inter, btn):
                if inter.user.id != uid: return await inter.response.send_message("Not for you.", ephemeral=True)
                await db.pool.execute("DELETE FROM nsfw_optout WHERE guild_id=$1 AND user_id=$2", gid, uid)
                for i in self_.children: i.disabled = True
                await inter.response.edit_message(content="✅ NSFW opt-out removed. You can receive NSFW actions.", view=self_)
        status = "🔞 Currently opted **OUT** of NSFW" if exists else "✅ Currently opted **IN** to NSFW"
        await ctx.send(embed=discord.Embed(description=status, color=C_SOCIAL), view=ToggleView(), ephemeral=True)

    # ── Leaderboard ───────────────────────────────────────────────
    @commands.hybrid_command(name="leaderboard", aliases=["lb","top"], description="View server leaderboards")
    async def leaderboard(self, ctx, category: str = "xp"):
        cat = category.lower()
        if cat not in LB_CATEGORIES: cat = "xp"
        e = await _build_lb(ctx.guild, cat)
        opts = [discord.SelectOption(label=v[0][:50], value=k, default=(k==cat)) for k,v in LB_CATEGORIES.items()]
        sel = discord.ui.Select(placeholder="Change category…", options=opts)
        async def sel_cb(inter):
            new_e = await _build_lb(inter.guild, sel.values[0])
            for opt in sel.options: opt.default = (opt.value == sel.values[0])
            await inter.response.edit_message(embed=new_e, view=view)
        sel.callback = sel_cb
        class LBView(discord.ui.View):
            def __init__(self_): super().__init__(timeout=300); self_.add_item(sel)
        view = LBView()
        await ctx.send(embed=e, view=view)

    # ── Rel opt-out ───────────────────────────────────────────────
    @commands.hybrid_command(name="reloptout", description="Toggle relationship command opt-out")
    async def reloptout(self, ctx):
        gid, uid = ctx.guild.id, ctx.author.id
        exists = await db.pool.fetchval("SELECT 1 FROM rel_optout WHERE guild_id=$1 AND user_id=$2", gid, uid)
        class TV(discord.ui.View):
            def __init__(self_): super().__init__(timeout=60)
            @discord.ui.button(label="Opt OUT of relationships", style=discord.ButtonStyle.danger if not exists else discord.ButtonStyle.secondary, disabled=bool(exists))
            async def out(self_, inter, btn):
                if inter.user.id != uid: return await inter.response.send_message("Not for you.", ephemeral=True)
                await db.pool.execute("INSERT INTO rel_optout (guild_id,user_id) VALUES ($1,$2) ON CONFLICT DO NOTHING", gid, uid)
                for i in self_.children: i.disabled = True
                await inter.response.edit_message(content="✅ Opted out of relationship commands.", view=self_)
            @discord.ui.button(label="Opt IN to relationships", style=discord.ButtonStyle.success if exists else discord.ButtonStyle.secondary, disabled=not bool(exists))
            async def inn(self_, inter, btn):
                if inter.user.id != uid: return await inter.response.send_message("Not for you.", ephemeral=True)
                await db.pool.execute("DELETE FROM rel_optout WHERE guild_id=$1 AND user_id=$2", gid, uid)
                for i in self_.children: i.disabled = True
                await inter.response.edit_message(content="✅ Opted in to relationship commands.", view=self_)
        status = "Currently opted **OUT**" if exists else "Currently opted **IN**"
        await ctx.send(embed=discord.Embed(description=f"💍 Relationship commands: {status}", color=C_SOCIAL), view=TV(), ephemeral=True)
