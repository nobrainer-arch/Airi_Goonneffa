# airi/social.py
import discord
from discord.ext import commands
from datetime import datetime, timedelta
import asyncio
import db
from utils import _err, C_SOCIAL, C_ECONOMY, C_WARN, C_SUCCESS, C_ERROR
from airi.guild_config import check_channel, get_profile_channel

REP_COOLDOWN = 12
CLAIM_COST   = 500
NEW_USER_PROTECTION = timedelta(hours=6)
PAGE_SIZE = 10


async def _is_protected(guild_id, user_id, member):
    if member.joined_at and (datetime.utcnow() - member.joined_at.replace(tzinfo=None)) < NEW_USER_PROTECTION:
        return True
    row = await db.pool.fetchrow(
        "SELECT expires_at FROM protection WHERE guild_id=$1 AND user_id=$2", guild_id, user_id
    )
    return bool(row and datetime.utcnow() < row["expires_at"])


# ── Paginated harem view (buttons) ───────────────────────────────
class HaremView(discord.ui.View):
    """Paginated harem — uses unique custom_ids per author to avoid collisions."""

    def __init__(self, rows: list, target, author_id: int):
        super().__init__(timeout=180)
        self._rows    = rows
        self._current = 0
        self._target  = target
        self._author  = author_id
        self._pages   = [rows[i:i+PAGE_SIZE] for i in range(0, len(rows), PAGE_SIZE)]
        # Unique custom_ids per user to prevent cross-user button routing
        self._prev_id = f"harem_prev_{author_id}"
        self._next_id = f"harem_next_{author_id}"
        prev_btn = discord.ui.Button(
            label="◀ Prev", style=discord.ButtonStyle.secondary,
            custom_id=self._prev_id, disabled=True
        )
        next_btn = discord.ui.Button(
            label="Next ▶", style=discord.ButtonStyle.secondary,
            custom_id=self._next_id, disabled=(len(self._pages) <= 1)
        )
        prev_btn.callback = self._on_prev
        next_btn.callback = self._on_next
        self.add_item(prev_btn)
        self.add_item(next_btn)

    def _upd(self):
        for item in self.children:
            if item.custom_id == self._prev_id:
                item.disabled = (self._current == 0)
            elif item.custom_id == self._next_id:
                item.disabled = (self._current == len(self._pages) - 1)

    def build(self, idx: int) -> discord.Embed:
        chunk = self._pages[idx]
        total = len(self._rows)
        e = discord.Embed(
            title=f"💕 {self._target.display_name}'s Harem ({total} waifus)",
            color=C_SOCIAL
        )
        e.set_thumbnail(url=self._target.display_avatar.url)
        start = idx * PAGE_SIZE
        lines = []
        for i, r in enumerate(chunk):
            m = self._target.guild.get_member(r["claimed_id"])
            name = m.display_name if m else f"<@{r['claimed_id']}>"
            lines.append(f"`{start+i+1}.` {name}")
        e.description = "\n".join(lines) if lines else "*Empty page*"
        e.set_footer(text=f"Page {idx+1}/{len(self._pages)} · Use !claim @user to grow your harem")
        return e

    async def _on_prev(self, interaction: discord.Interaction):
        if interaction.user.id != self._author:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        self._current -= 1
        self._upd()
        await interaction.response.edit_message(embed=self.build(self._current), view=self)

    async def _on_next(self, interaction: discord.Interaction):
        if interaction.user.id != self._author:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        self._current += 1
        self._upd()
        await interaction.response.edit_message(embed=self.build(self._current), view=self)


# ── Leaderboard view with category dropdown ───────────────────────
class LeaderboardView(discord.ui.View):
    def __init__(self, guild, initial_cat: str):
        super().__init__(timeout=180)
        self._guild = guild
        self._cat   = initial_cat

    @discord.ui.select(
        placeholder="Change category...",
        options=[
            discord.SelectOption(label="XP",    value="xp",    emoji="⬆️", default=True),
            discord.SelectOption(label="Coins",  value="coins",  emoji="💰"),
            discord.SelectOption(label="Rep",    value="rep",    emoji="⭐"),
        ]
    )
    async def select_cb(self, interaction: discord.Interaction, select: discord.ui.Select):
        cat = select.values[0]
        for opt in select.options: opt.default = (opt.value == cat)
        e = await _build_lb(self._guild, cat)
        await interaction.response.edit_message(embed=e, view=self)


async def _build_lb(guild, cat: str) -> discord.Embed:
    gid    = guild.id
    medals = ["🥇", "🥈", "🥉"]
    lines  = []
    if cat == "xp":
        rows  = await db.pool.fetch(
            "SELECT user_id,xp,level FROM xp WHERE guild_id=$1 ORDER BY xp DESC LIMIT 10", gid
        )
        title = "⬆️ XP Leaderboard"
        for i, r in enumerate(rows):
            m = guild.get_member(r["user_id"])
            if not m: continue
            lines.append(f"{medals[i] if i<3 else f'`{i+1}`.'}  **{m.display_name}** — Lv.**{r['level']}** · {r['xp']:,} XP")
    elif cat == "coins":
        rows  = await db.pool.fetch(
            "SELECT user_id,balance FROM economy WHERE guild_id=$1 ORDER BY balance DESC LIMIT 10", gid
        )
        title = "💰 Coins Leaderboard"
        for i, r in enumerate(rows):
            m = guild.get_member(r["user_id"])
            if not m: continue
            lines.append(f"{medals[i] if i<3 else f'`{i+1}`.'}  **{m.display_name}** — **{r['balance']:,}** coins")
    else:
        rows  = await db.pool.fetch(
            "SELECT user_id,rep FROM social WHERE guild_id=$1 ORDER BY rep DESC LIMIT 10", gid
        )
        title = "⭐ Rep Leaderboard"
        for i, r in enumerate(rows):
            m = guild.get_member(r["user_id"])
            if not m: continue
            lines.append(f"{medals[i] if i<3 else f'`{i+1}`.'}  **{m.display_name}** — **{r['rep']}** rep")
    e = discord.Embed(title=title, description="\n".join(lines) or "No data yet.", color=C_ECONOMY)
    e.set_footer(text="Updates in real-time · Use dropdown to switch")
    return e


class SocialCog(commands.Cog, name="Social"):
    def __init__(self, bot): self.bot = bot

    # ── Profile ──────────────────────────────────────────────────
    @commands.command(aliases=["prof", "me"])
    async def profile(self, ctx, member: discord.Member = None):
        if not await check_channel(ctx, "social"): return
        target = member or ctx.author
        gid = ctx.guild.id

        profile_ch_id = await get_profile_channel(gid)
        dest = self.bot.get_channel(profile_ch_id) if profile_ch_id and ctx.channel.id != profile_ch_id else ctx.channel
        if dest != ctx.channel:
            await _err(ctx, f"Profiles are shown in {dest.mention}.", delete_cmd=False)
        await self._send_profile(dest, ctx.guild, target)

    async def _send_profile(self, channel, guild, target):
        gid, uid = guild.id, target.id
        eco  = await db.pool.fetchrow("SELECT balance,active_title,streak FROM economy WHERE guild_id=$1 AND user_id=$2", gid, uid)
        xpr  = await db.pool.fetchrow("SELECT xp,level FROM xp WHERE guild_id=$1 AND user_id=$2", gid, uid)
        soc  = await db.pool.fetchrow("SELECT rep FROM social WHERE guild_id=$1 AND user_id=$2", gid, uid)
        waifus_owned  = await db.pool.fetchval("SELECT COUNT(*) FROM claims WHERE guild_id=$1 AND claimer_id=$2", gid, uid) or 0
        claimed_count = await db.pool.fetchval("SELECT COUNT(*) FROM claims WHERE guild_id=$1 AND claimed_id=$2", gid, uid) or 0

        from airi.xp import xp_for_level, get_rank
        balance  = eco["balance"]       if eco else 0
        title    = eco["active_title"]  if eco else None
        total_xp = xpr["xp"]            if xpr else 0
        level    = xpr["level"]         if xpr else 0
        rep      = soc["rep"]           if soc else 0
        rank     = await get_rank(gid, uid)

        cf   = xp_for_level(level)
        nxt  = xp_for_level(level + 1)
        prog = total_xp - cf
        need = nxt - cf
        pct  = min(100, int(prog / need * 100)) if need else 100
        bar  = "█" * int(pct / 100 * 10) + "░" * (10 - int(pct / 100 * 10))

        member = guild.get_member(uid)
        shielded = await _is_protected(gid, uid, member) if member else False

        color = target.color if target.color.value else 0xff69b4
        e = discord.Embed(
            title=f"{'*'+title+'*  ·  ' if title else ''}{target.display_name}",
            color=color,
        )
        e.set_thumbnail(url=target.display_avatar.url)
        e.add_field(name="⬆️ Level",  value=str(level),      inline=True)
        e.add_field(name="🏆 Rank",   value=f"#{rank}",       inline=True)
        e.add_field(name="💰 Coins",  value=f"{balance:,}",   inline=True)
        e.add_field(name="⭐ Rep",    value=str(rep),         inline=True)
        e.add_field(name="💘 Waifus", value=str(waifus_owned), inline=True)
        e.add_field(name="👑 Owned",  value=str(claimed_count), inline=True)
        e.add_field(name=f"XP Lv{level}→{level+1}", value=f"`{bar}` {pct}%", inline=False)
        if shielded:
            e.add_field(name="🛡️ Protected", value="Cannot be claimed", inline=True)
        e.set_footer(text=f"ID: {target.id}")
        await channel.send(embed=e)

    # ── Rep ──────────────────────────────────────────────────────
    @commands.command(aliases=["rep"])
    async def reputation(self, ctx, member: discord.Member = None):
        if not await check_channel(ctx, "social"): return
        if member is None:
            uid = ctx.author.id
            cog_self = self
            class RepPickView(discord.ui.View):
                def __init__(self_): super().__init__(timeout=60)
                @discord.ui.user_select(placeholder="Select someone to give rep to...")
                async def pick(self_, inter, sel):
                    if inter.user.id != uid: return await inter.response.send_message("Not for you.", ephemeral=True)
                    for i in self_.children: i.disabled = True
                    await inter.response.edit_message(view=self_)
                    target = sel.values[0]
                    # Fake a ctx-like object
                    class FakeCtx:
                        author = inter.user; guild = inter.guild; channel = inter.channel
                        async def send(self__, *a, **kw): return await inter.followup.send(*a, **kw)
                        message = None
                    fake = FakeCtx()
                    await cog_self._do_rep(fake, target)
                    self_.stop()
            await ctx.send(embed=discord.Embed(description=f"**{ctx.author.display_name}** wants to give rep to...", color=C_SOCIAL), view=RepPickView())
            return
        await self._do_rep(ctx, member)

    async def _do_rep(self, ctx, member):
        if member == ctx.author or member.bot: return await _err(ctx, "Invalid target.")
        gid, now = ctx.guild.id, datetime.utcnow()
        row = await db.pool.fetchrow(
            "SELECT last_rep_given FROM social WHERE guild_id=$1 AND user_id=$2", gid, ctx.author.id
        )
        if row and row["last_rep_given"]:
            elapsed = now - row["last_rep_given"]
            if elapsed < timedelta(hours=REP_COOLDOWN):
                remaining = timedelta(hours=REP_COOLDOWN) - elapsed
                h, rem = divmod(int(remaining.total_seconds()), 3600)
                return await _err(ctx, f"Rep again in **{h}h {rem//60}m**.")
        await db.pool.execute("""
            INSERT INTO social (guild_id,user_id,last_rep_given) VALUES ($1,$2,$3)
            ON CONFLICT (guild_id,user_id) DO UPDATE SET last_rep_given=$3
        """, gid, ctx.author.id, now)
        new_rep = await db.pool.fetchval("""
            INSERT INTO social (guild_id,user_id,rep) VALUES ($1,$2,1)
            ON CONFLICT (guild_id,user_id) DO UPDATE SET rep=social.rep+1 RETURNING rep
        """, gid, member.id)
        await ctx.send(embed=discord.Embed(
            description=f"⭐ {ctx.author.mention} gave rep to {member.mention}! They now have **{new_rep}** rep.",
            color=C_SOCIAL
        ))

    # ── Claim ────────────────────────────────────────────────────
    @commands.command()
    async def claim(self, ctx, member: discord.Member = None):
        if not await check_channel(ctx, "social"): return
        if member is None:
            uid = ctx.author.id
            cog_self = self
            class ClaimPickView(discord.ui.View):
                def __init__(self_): super().__init__(timeout=60)
                @discord.ui.user_select(placeholder="Select someone to claim...")
                async def pick(self_, inter, sel):
                    if inter.user.id != uid: return await inter.response.send_message("Not for you.", ephemeral=True)
                    for i in self_.children: i.disabled = True
                    await inter.response.edit_message(view=self_)
                    target = sel.values[0]
                    class FakeCtx:
                        author = inter.user; guild = inter.guild; channel = inter.channel
                        async def send(self__, *a, **kw): return await inter.followup.send(*a, **kw)
                        message = None
                    fake = FakeCtx()
                    await cog_self._do_claim(fake, target)
                    self_.stop()
            await ctx.send(embed=discord.Embed(description=f"**{ctx.author.display_name}** wants to claim...", color=C_SOCIAL), view=ClaimPickView())
            return
        await self._do_claim(ctx, member)

    async def _do_claim(self, ctx, member):
        if member.bot or member == ctx.author: return await _err(ctx, "Invalid target.")
        gid, uid, tid = ctx.guild.id, ctx.author.id, member.id

        if await _is_protected(gid, tid, member):
            t = member.joined_at
            if t and (datetime.utcnow() - t.replace(tzinfo=None)) < NEW_USER_PROTECTION:
                h = int((NEW_USER_PROTECTION - (datetime.utcnow() - t.replace(tzinfo=None))).total_seconds() // 3600)
                return await _err(ctx, f"{member.display_name} is a new member — protected for **{h}h** more.")
            return await _err(ctx, f"{member.display_name} has an active **Claim Shield** 🛡️.")

        existing_owner = await db.pool.fetchval(
            "SELECT claimer_id FROM claims WHERE guild_id=$1 AND claimed_id=$2", gid, tid
        )
        if existing_owner == uid:
            return await _err(ctx, f"You already own {member.mention}!")
        if existing_owner:
            owner = ctx.guild.get_member(existing_owner)
            return await _err(ctx, f"{member.mention} is already owned by {owner.mention if owner else f'<@{existing_owner}>'}.")

        from airi.economy import get_balance, add_coins
        bal = await get_balance(gid, uid)
        if bal < CLAIM_COST: return await _err(ctx, f"Claiming costs **{CLAIM_COST:,} coins**. You have **{bal:,}**.")
        await add_coins(gid, uid, -CLAIM_COST)
        from utils import log_txn
        await log_txn(ctx.bot, gid, "Claim", ctx.author, "System", CLAIM_COST, f"Claimed {member.display_name}")
        await db.pool.execute("""
            INSERT INTO claims (guild_id,claimer_id,claimed_id) VALUES ($1,$2,$3)
            ON CONFLICT (guild_id,claimed_id) DO UPDATE SET claimer_id=EXCLUDED.claimer_id
        """, gid, uid, tid)
        e = discord.Embed(title="💘 Claimed!", description=f"{ctx.author.mention} claimed {member.mention}!", color=C_SOCIAL)
        e.set_thumbnail(url=member.display_avatar.url)
        e.set_footer(text=f"−{CLAIM_COST} coins")
        await ctx.send(embed=e)

    # ── Release ──────────────────────────────────────────────────
    @commands.command()
    async def release(self, ctx, member: discord.Member = None):
        if not await check_channel(ctx, "social"): return
        gid, uid = ctx.guild.id, ctx.author.id
        if member is None:
            rows = await db.pool.fetch(
                "SELECT claimed_id FROM claims WHERE guild_id=$1 AND claimer_id=$2", gid, uid
            )
            if not rows: return await _err(ctx, "You have no waifus to release.")
            options = []
            for r in rows[:25]:
                m = ctx.guild.get_member(r["claimed_id"])
                if m: options.append(discord.SelectOption(label=m.display_name, value=str(r["claimed_id"])))
            if not options: return await _err(ctx, "No members found to release.")

            class ReleaseSelect(discord.ui.Select):
                def __init__(self_):
                    super().__init__(placeholder="Select who to release...", options=options)
                async def callback(self_, inter):
                    if inter.user.id != uid:
                        return await inter.response.send_message("Not for you.", ephemeral=True)
                    tid = int(self_.values[0])
                    await db.pool.execute(
                        "DELETE FROM claims WHERE guild_id=$1 AND claimer_id=$2 AND claimed_id=$3", gid, uid, tid
                    )
                    m = inter.guild.get_member(tid)
                    for item in self.view.children: item.disabled = True
                    await inter.response.edit_message(
                        embed=discord.Embed(
                            description=f"💔 Released {m.mention if m else f'<@{tid}>'}.",
                            color=C_WARN
                        ),
                        view=self.view
                    )

            class ReleaseView(discord.ui.View):
                def __init__(self_): super().__init__(timeout=60); self_.add_item(ReleaseSelect())

            await ctx.send(embed=discord.Embed(description="Who do you want to release?", color=C_WARN),
                           view=ReleaseView())
            return

        row = await db.pool.fetchrow(
            "SELECT 1 FROM claims WHERE guild_id=$1 AND claimer_id=$2 AND claimed_id=$3", gid, uid, member.id
        )
        if not row: return await _err(ctx, f"You don't own {member.mention}.")
        await db.pool.execute("DELETE FROM claims WHERE guild_id=$1 AND claimer_id=$2 AND claimed_id=$3", gid, uid, member.id)
        await ctx.send(embed=discord.Embed(description=f"💔 {ctx.author.mention} released {member.mention}.", color=C_WARN))

    # ── My Waifu (paginated, button nav) ─────────────────────────
    @commands.command(aliases=["harem", "mywaifus"])
    async def mywaifu(self, ctx, member: discord.Member = None):
        if not await check_channel(ctx, "social"): return
        target = member or ctx.author
        gid, uid = ctx.guild.id, target.id

        rows = await db.pool.fetch(
            "SELECT claimed_id, claimed_at FROM claims WHERE guild_id=$1 AND claimer_id=$2 ORDER BY claimed_at DESC",
            gid, uid
        )
        if not rows:
            whose = "You have" if target == ctx.author else f"{target.display_name} has"
            return await ctx.send(embed=discord.Embed(
                description=f"{whose} no waifus yet. Use `!claim @user` to claim someone!",
                color=C_WARN
            ))

        # Convert asyncpg records to plain dicts so they're safe to use later
        rows_list = [dict(r) for r in rows]
        view = HaremView(rows_list, target, ctx.author.id)
        await ctx.send(embed=view.build(0), view=view if len(view._pages) > 1 else None)

    # ── Waifu info ───────────────────────────────────────────────
    @commands.command()
    async def waifu(self, ctx, member: discord.Member = None):
        if not await check_channel(ctx, "social"): return
        target = member or ctx.author
        gid, tid = ctx.guild.id, target.id
        owner_id    = await db.pool.fetchval("SELECT claimer_id FROM claims WHERE guild_id=$1 AND claimed_id=$2", gid, tid)
        owned_count = await db.pool.fetchval("SELECT COUNT(*) FROM claims WHERE guild_id=$1 AND claimer_id=$2", gid, tid) or 0
        owner_text  = (lambda o: o.mention if o else f"<@{owner_id}>")(ctx.guild.get_member(owner_id)) if owner_id else "Nobody"
        shielded    = await _is_protected(gid, tid, target)
        e = discord.Embed(title=f"💕 {target.display_name}", color=C_SOCIAL)
        e.set_thumbnail(url=target.display_avatar.url)
        e.add_field(name="Owned by",     value=owner_text,                      inline=True)
        e.add_field(name="Waifus owned", value=str(owned_count),                inline=True)
        e.add_field(name="🛡️ Shield",    value="Active" if shielded else "None", inline=True)
        await ctx.send(embed=e)

    # ── Leaderboard ──────────────────────────────────────────────
    @commands.command(aliases=["lb", "top"])
    async def leaderboard(self, ctx, category: str = "xp"):
        if not await check_channel(ctx, "social"): return
        cat = category.lower()
        if cat not in ("xp", "coins", "rep"): cat = "xp"
        e    = await _build_lb(ctx.guild, cat)
        view = LeaderboardView(ctx.guild, cat)
        await ctx.send(embed=e, view=view)

    # ── NSFW opt-out ─────────────────────────────────────────────
    @commands.command(aliases=["nsfwopt"])
    async def nsfwoptout(self, ctx, action: str = "out"):
        gid, uid = ctx.guild.id, ctx.author.id
        action = action.lower()
        if action == "out":
            await db.pool.execute(
                "INSERT INTO nsfw_optout (guild_id,user_id) VALUES ($1,$2) ON CONFLICT DO NOTHING", gid, uid
            )
            await ctx.send("✅ You've opted **out** of NSFW commands.", delete_after=10)
        elif action == "in":
            await db.pool.execute("DELETE FROM nsfw_optout WHERE guild_id=$1 AND user_id=$2", gid, uid)
            await ctx.send("✅ You've opted **in** to NSFW commands.", delete_after=10)
        else:
            await _err(ctx, "Use `!nsfwoptout out` or `!nsfwoptout in`")

    # ── Delete bot history ────────────────────────────────────────
    @commands.command(aliases=["cdh", "cleanbotmsgs"])
    @commands.has_permissions(manage_messages=True)
    async def chatdelhist(self, ctx, count: int = 10):
        count = min(count, 100)
        msgs  = []
        async for msg in ctx.channel.history(limit=500):
            if msg.author == ctx.guild.me: msgs.append(msg)
            if len(msgs) >= count: break
        if not msgs: return await ctx.send("No bot messages found.", delete_after=4)
        try: await ctx.channel.delete_messages(msgs)
        except Exception:
            for m in msgs:
                try: await m.delete()
                except Exception: pass
        await ctx.send(f"🗑️ Deleted **{len(msgs)}** messages.", delete_after=4)

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def shutdown(self, ctx):
        await ctx.send("🔴 Airi shutting down... *uwu goodbye* 👋")
        await self.bot.close()
