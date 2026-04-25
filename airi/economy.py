# airi/economy.py — Economy commands with full UI
import discord
from discord.ext import commands
from datetime import datetime, timedelta, timezone, timezone
import random
import db
import config
from utils import _err, C_ECONOMY, C_INFO, C_SUCCESS, log_txn

# ── Constants ─────────────────────────────────────────────────────
DAILY_MIN     = 3500
DAILY_MAX     = 8000
STREAK_BONUS  = 300
STREAK_CAP    = 5000
DAILY_COOLDOWN = 22   # hours
PAY_TAX        = 0.02
GIVE_LIMIT     = 5000

SHOP_ITEMS: dict[str, dict] = {
    "xpboost":   {"name": "⚡ XP Boost (1h)",   "price": 1000,  "desc": "Double XP for 1 hour",         "type": "xp_boost"},
    "xpboost24": {"name": "⚡ XP Boost (24h)",  "price": 8000,  "desc": "Double XP for 24 hours",        "type": "xp_boost24"},
    "shield":    {"name": "🛡️ Waifu Shield",    "price": 2000,  "desc": "Protect your waifu from claims","type": "shield"},
    "prenup":    {"name": "📜 Prenup",           "price": 5000,  "desc": "Protects assets on divorce",    "type": "prenup"},
    "title_rich":{"name": "💰 Title: Rich",      "price": 3000,  "desc": "Show off wealth",               "type": "title"},
    "title_chad":{"name": "🔥 Title: Chad",      "price": 3000,  "desc": "Peak confidence",               "type": "title"},
    "title_cutie":{"name":"🌸 Title: Cutie",     "price": 3000,  "desc": "Adorable vibes",                "type": "title"},
    "title_toxic":{"name":"☠️ Title: Toxic",     "price": 5000,  "desc": "Villain arc",                   "type": "title"},
}

def _make_tz_aware(ts):
    """Coerce datetime to naive UTC for safe arithmetic with asyncpg results."""
    if ts is None: return None
    if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
        return ts.replace(tzinfo=None)  # make naive
    return ts

async def ensure_user(conn, gid: int, uid: int):
    await conn.execute("""
        INSERT INTO economy (guild_id, user_id) VALUES ($1,$2) ON CONFLICT DO NOTHING
    """, gid, uid)

async def add_coins(gid: int, uid: int, amount: int):
    await db.pool.execute("""
        INSERT INTO economy (guild_id, user_id, balance) VALUES ($1,$2,GREATEST(0,$3))
        ON CONFLICT (guild_id,user_id) DO UPDATE
        SET balance = GREATEST(0, economy.balance + $3)
    """, gid, uid, amount)

async def get_balance(gid: int, uid: int) -> int:
    row = await db.pool.fetchrow("SELECT balance FROM economy WHERE guild_id=$1 AND user_id=$2", gid, uid)
    return row["balance"] if row else 0

async def is_xp_boosted(gid: int, uid: int) -> bool:
    row = await db.pool.fetchrow("SELECT xp_boost_until FROM economy WHERE guild_id=$1 AND user_id=$2", gid, uid)
    if not row or not row["xp_boost_until"]: return False
    return datetime.now(timezone.utc) < row["xp_boost_until"]


# ── Balance UI ─────────────────────────────────────────────────────
def _bal_embed(member: discord.Member, bal: int, kak: int, title: str | None) -> discord.Embed:
    e = discord.Embed(
        title=f"{'✨ '+title+'  ·  ' if title else ''}💰 {member.display_name}'s Wallet",
        color=C_ECONOMY,
    )
    e.set_thumbnail(url=member.display_avatar.url)
    e.add_field(name="Coins",  value=f"**{bal:,}** 🪙", inline=True)
    e.add_field(name="Kakera", value=f"**{kak:,}** 💎", inline=True)
    return e


def _make_fc(inter):
    """Create a minimal fake-ctx from an interaction for _static_pay/_static_give."""
    class FC:
        guild  = inter.guild
        author = inter.user
        bot    = inter.client
        async def send(s, msg=None, **kw):
            try:
                if msg is not None:
                    await inter.followup.send(msg, ephemeral=True)
                else:
                    await inter.followup.send(ephemeral=True, **kw)
            except Exception:
                pass
    return FC()


class BalanceView(discord.ui.View):
    def __init__(self, ctx, target: discord.Member):
        super().__init__(timeout=180)
        self._ctx    = ctx
        self._target = target
        self._is_own = target.id == ctx.author.id

    @discord.ui.button(label="💸 Pay", style=discord.ButtonStyle.primary)
    async def pay_btn(self, inter: discord.Interaction, btn):
        if inter.user.id != self._ctx.author.id:
            return await inter.response.send_message("Not for you.", ephemeral=True)

        if not self._is_own:
            # Paying the person whose balance we're viewing — skip user select
            tgt = self._target
            class PayM(discord.ui.Modal, title=f"Pay {tgt.display_name}"):
                amt_in = discord.ui.TextInput(label="Amount (coins)", placeholder="e.g. 500", required=True)
                async def on_submit(m, i2):
                    await i2.response.defer(ephemeral=True)
                    raw = m.amt_in.value.strip().replace(",","")
                    if not raw.isdigit():
                        return await i2.followup.send("❌ Enter a valid number.", ephemeral=True)
                    await EconomyCog._static_pay(_make_fc(i2), tgt, int(raw), i2)
            return await inter.response.send_modal(PayM())

        # Own balance — pick a recipient first
        sel = discord.ui.UserSelect(placeholder="Who do you want to pay?")
        async def sel_cb(i2: discord.Interaction):
            if i2.user.id != self._ctx.author.id:
                return await i2.response.send_message("Not for you.", ephemeral=True)
            rec = sel.values[0]
            if rec.bot or rec.id == i2.user.id:
                return await i2.response.send_message("❌ Invalid recipient.", ephemeral=True)
            class PayM2(discord.ui.Modal, title=f"Pay {rec.display_name}"):
                amt_in = discord.ui.TextInput(label="Amount (coins)", placeholder="e.g. 500", required=True)
                async def on_submit(m, i3):
                    await i3.response.defer(ephemeral=True)
                    raw = m.amt_in.value.strip().replace(",","")
                    if not raw.isdigit():
                        return await i3.followup.send("❌ Enter a valid number.", ephemeral=True)
                    await EconomyCog._static_pay(_make_fc(i3), rec, int(raw), i3)
            await i2.response.send_modal(PayM2())
        sel.callback = sel_cb
        pv = discord.ui.View(timeout=60)
        pv.add_item(sel)
        await inter.response.send_message("Select recipient:", view=pv, ephemeral=True)

    @discord.ui.button(label="🎁 Give", style=discord.ButtonStyle.secondary)
    async def give_btn(self, inter: discord.Interaction, btn):
        if inter.user.id != self._ctx.author.id:
            return await inter.response.send_message("Not for you.", ephemeral=True)

        if not self._is_own:
            tgt = self._target
            class GiveM(discord.ui.Modal, title=f"Give {tgt.display_name} (max {GIVE_LIMIT:,})"):
                amt_in = discord.ui.TextInput(label=f"Amount (max {GIVE_LIMIT:,})", placeholder="e.g. 100", required=True)
                async def on_submit(m, i2):
                    await i2.response.defer(ephemeral=True)
                    raw = m.amt_in.value.strip().replace(",","")
                    if not raw.isdigit():
                        return await i2.followup.send("❌ Enter a valid number.", ephemeral=True)
                    await EconomyCog._static_give(_make_fc(i2), tgt, int(raw), i2)
            return await inter.response.send_modal(GiveM())

        sel = discord.ui.UserSelect(placeholder="Who do you want to give coins to?")
        async def give_cb(i2: discord.Interaction):
            if i2.user.id != self._ctx.author.id:
                return await i2.response.send_message("Not for you.", ephemeral=True)
            rec = sel.values[0]
            if rec.bot or rec.id == i2.user.id:
                return await i2.response.send_message("❌ Invalid recipient.", ephemeral=True)
            class GiveM2(discord.ui.Modal, title=f"Give {rec.display_name} (max {GIVE_LIMIT:,})"):
                amt_in = discord.ui.TextInput(label=f"Amount (max {GIVE_LIMIT:,})", placeholder="e.g. 100", required=True)
                async def on_submit(m, i3):
                    await i3.response.defer(ephemeral=True)
                    raw = m.amt_in.value.strip().replace(",","")
                    if not raw.isdigit():
                        return await i3.followup.send("❌ Enter a valid number.", ephemeral=True)
                    await EconomyCog._static_give(_make_fc(i3), rec, int(raw), i3)
            await i2.response.send_modal(GiveM2())
        sel.callback = give_cb
        gv = discord.ui.View(timeout=60)
        gv.add_item(sel)
        await inter.response.send_message("Select recipient:", view=gv, ephemeral=True)


class EconomyCog(commands.Cog, name="Economy"):
    def __init__(self, bot): self.bot = bot

    # ── Daily ──────────────────────────────────────────────────────
    @commands.hybrid_command(name="daily", aliases=["dp","earn"], description="Open Economy Panel (Daily/Work/Crime)")
    async def daily(self, ctx):
        from airi.daily_panel import open_daily_panel
        await open_daily_panel(ctx)

    async def _do_daily(self, ctx):
        from datetime import timezone as _tz
        gid, uid, now = ctx.guild.id, ctx.author.id, datetime.utcnow()
        async with db.pool.acquire() as conn:
            await ensure_user(conn, gid, uid)
        row = await db.pool.fetchrow(
            "SELECT balance,last_daily,streak,daily_boost FROM economy WHERE guild_id=$1 AND user_id=$2", gid, uid
        )
        last   = _make_tz_aware(row["last_daily"]); streak = row["streak"] or 0
        if last:
            elapsed = now - last
            if elapsed < timedelta(hours=DAILY_COOLDOWN):
                rem = timedelta(hours=DAILY_COOLDOWN) - elapsed
                h, s = divmod(int(rem.total_seconds()), 3600)
                return await _err(ctx, f"Come back in **{h}h {s//60}m**.")
            streak = streak + 1 if elapsed < timedelta(hours=48) else 1
        else:
            streak = 1
        amount = random.randint(DAILY_MIN, DAILY_MAX)
        if row["daily_boost"]: amount *= 2
        bonus  = min(streak * STREAK_BONUS, STREAK_CAP)
        total  = amount + bonus
        await db.pool.execute(
            "UPDATE economy SET balance=balance+$1,last_daily=$2,streak=$3,daily_boost=FALSE WHERE guild_id=$4 AND user_id=$5",
            total, now, streak, gid, uid
        )
        await log_txn(self.bot, gid, "Daily", "System", ctx.author, total, f"Streak {streak}")
        from airi.milestones import update_achievement
        if hasattr(ctx, "channel"):
            await update_achievement(self.bot, gid, uid, "daily_7", min(streak, 7), ctx.channel)
            if streak >= 7:
                await update_achievement(self.bot, gid, uid, "daily_30", min(streak, 30), ctx.channel)
        e = discord.Embed(title="💰 Daily Claimed!", color=C_ECONOMY)
        e.add_field(name="Base",           value=f"{amount:,} 🪙", inline=True)
        e.add_field(name=f"🔥 Day {streak}", value=f"+{bonus:,} 🪙", inline=True)
        e.add_field(name="Total",          value=f"**{total:,} 🪙**", inline=False)
        e.set_footer(text="Come back tomorrow!")
        await ctx.send(embed=e)

    # ── Balance ────────────────────────────────────────────────────
    @commands.hybrid_command(name="balance", aliases=["bal","coins","wallet"], description="Check wallet balance")
    async def balance(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        gid, uid = ctx.guild.id, target.id
        async with db.pool.acquire() as conn:
            await ensure_user(conn, gid, uid)
        row = await db.pool.fetchrow(
            "SELECT balance, kakera, active_title FROM economy WHERE guild_id=$1 AND user_id=$2", gid, uid
        )
        bal = row["balance"] if row else 0
        kak = row["kakera"]  if row else 0
        ttl = row["active_title"] if row else None
        e = _bal_embed(target, bal, kak, ttl)
        view = BalanceView(ctx, target)
        await ctx.send(embed=e, view=view)

    # ── Pay ────────────────────────────────────────────────────────
    @commands.hybrid_command(name="pay", description="Send coins to someone (5% tax)")
    async def pay(self, ctx, member: discord.Member = None, amount: int = None):
        if member is None:
            # Full UI: user select then amount
            sel = discord.ui.UserSelect(placeholder="Select recipient…")
            async def sel_cb(inter: discord.Interaction):
                if inter.user.id != ctx.author.id: return await inter.response.send_message("Not for you.", ephemeral=True)
                rec = sel.values[0]
                class AmtM(discord.ui.Modal, title=f"Pay {rec.display_name}"):
                    amount_in = discord.ui.TextInput(label="Amount (coins)", placeholder="e.g. 500", required=True)
                    async def on_submit(m_self, i2):
                        await i2.response.defer(ephemeral=True)
                        raw = m_self.amount_in.value.strip().replace(",","")
                        if not raw.isdigit():
                            return await i2.followup.send("❌ Invalid amount. Enter a number.", ephemeral=True)
                        amt = int(raw)
                        class FC:
                            guild = i2.guild; author = i2.user; bot = i2.client
                            async def send(s, msg=None, **kw):
                                if msg: await i2.followup.send(msg, ephemeral=True)
                                else: await i2.followup.send(ephemeral=True, **kw)
                        await EconomyCog._static_pay(FC(), rec, amt, i2)
                await inter.response.send_modal(AmtM())
            sel.callback = sel_cb
            class V(discord.ui.View):
                def __init__(self_): super().__init__(timeout=180); self_.add_item(sel)
            return await ctx.send("Who do you want to pay?", view=V())
        if amount is None:
            class AmtM(discord.ui.Modal, title=f"Pay {member.display_name}"):
                amount_in = discord.ui.TextInput(label="Amount (coins)", placeholder="e.g. 500", required=True)
                async def on_submit(m_self, i2):
                    await i2.response.defer(ephemeral=True)
                    raw = m_self.amount_in.value.strip().replace(",","")
                    if not raw.isdigit():
                        return await i2.followup.send("❌ Invalid amount.", ephemeral=True)
                    class FC:
                        guild = i2.guild; author = i2.user; bot = i2.client
                        async def send(s, msg=None, **kw):
                            if msg: await i2.followup.send(msg, ephemeral=True)
                            else: await i2.followup.send(ephemeral=True, **kw)
                    await EconomyCog._static_pay(FC(), member, int(raw), i2)
            return await ctx.send_modal(AmtM()) if hasattr(ctx, "send_modal") else await ctx.send(f"Usage: `!pay @{member.display_name} <amount>`")
        await EconomyCog._static_pay(ctx, member, amount, None)

    @staticmethod
    async def _static_pay(ctx, member: discord.Member, amount: int, inter=None):
        async def send(msg=None, **kw):
            # Safely send whether we have an interaction followup or a plain ctx
            if inter:
                try:
                    if msg is not None:
                        await inter.followup.send(msg, ephemeral=True, **kw)
                    else:
                        await inter.followup.send(ephemeral=True, **kw)
                except Exception:
                    pass
            else:
                if msg is not None:
                    await ctx.send(msg, **kw)
                else:
                    await ctx.send(**kw)
        if member == ctx.author or member.bot: return await send("❌ Invalid target.")
        if amount <= 0 or amount > 10000: return await send("❌ Amount must be 1–10,000 coins.")
        gid, uid = ctx.guild.id, ctx.author.id
        async with db.pool.acquire() as conn:
            await ensure_user(conn, gid, uid)
            await ensure_user(conn, gid, member.id)
        tax = int(amount * PAY_TAX)
        net = amount - tax
        row = await db.pool.fetchrow(
            "UPDATE economy SET balance=balance-$1 WHERE guild_id=$2 AND user_id=$3 AND balance>=$1 RETURNING balance",
            amount, gid, uid
        )
        if not row:
            bal = await get_balance(gid, uid)
            return await send(f"❌ You need **{amount:,}** coins but only have **{bal:,}**.")
        await db.pool.execute("UPDATE economy SET balance=balance+$1 WHERE guild_id=$2 AND user_id=$3", net, gid, member.id)
        e = discord.Embed(
            description=f"💸 {ctx.author.mention} paid **{net:,}** 🪙 to {member.mention} (tax: {tax:,})",
            color=C_SUCCESS
        )
        await send(embed=e)
        from utils import log_txn as _lt
        # We need the bot — try to get from ctx
        bot = getattr(ctx, "bot", None)
        if bot: await _lt(bot, gid, "Pay", ctx.author, member, net, f"Tax: {tax}")

    # ── Give ───────────────────────────────────────────────────────
    @commands.hybrid_command(name="give", description=f"Tax-free gift (max {GIVE_LIMIT:,} coins)")
    async def give(self, ctx, member: discord.Member = None, amount: int = None):
        if member is None:
            sel = discord.ui.UserSelect(placeholder="Give coins to…")
            async def sel_cb(inter: discord.Interaction):
                if inter.user.id != ctx.author.id: return await inter.response.send_message("Not for you.", ephemeral=True)
                rec = sel.values[0]
                class GM(discord.ui.Modal, title=f"Give {rec.display_name} (max {GIVE_LIMIT:,})"):
                    amount_in = discord.ui.TextInput(label="Amount", placeholder=f"1-{GIVE_LIMIT}", required=True)
                    async def on_submit(m_self, i2):
                        await i2.response.defer(ephemeral=True)
                        raw = m_self.amount_in.value.strip().replace(",","")
                        if not raw.isdigit():
                            return await i2.followup.send("❌ Invalid amount.", ephemeral=True)
                        class FC:
                            guild = i2.guild; author = i2.user; bot = i2.client
                            async def send(s, msg=None, **kw):
                                if msg: await i2.followup.send(msg, ephemeral=True)
                                else: await i2.followup.send(ephemeral=True, **kw)
                        await EconomyCog._static_give(FC(), rec, int(raw), i2)
                await inter.response.send_modal(GM())
            sel.callback = sel_cb
            class V(discord.ui.View):
                def __init__(self_): super().__init__(timeout=180); self_.add_item(sel)
            return await ctx.send("Give coins to:", view=V())
        if amount is None:
            return await _err(ctx, f"Usage: `!give @user <amount>` (max {GIVE_LIMIT:,})")
        await EconomyCog._static_give(ctx, member, amount, None)

    @staticmethod
    async def _static_give(ctx, member: discord.Member, amount: int, inter=None):
        async def send(msg=None, **kw):
            if inter:
                try:
                    if msg is not None:
                        await inter.followup.send(msg, ephemeral=True, **kw)
                    else:
                        await inter.followup.send(ephemeral=True, **kw)
                except Exception:
                    pass
            else:
                if msg is not None:
                    await ctx.send(msg, **kw)
                else:
                    await ctx.send(**kw)
        if member == ctx.author or member.bot: return await send("❌ Invalid target.")
        if amount <= 0 or amount > GIVE_LIMIT: return await send(f"❌ Amount must be 1–{GIVE_LIMIT:,} coins.")
        gid, uid = ctx.guild.id, ctx.author.id
        row = await db.pool.fetchrow(
            "UPDATE economy SET balance=balance-$1 WHERE guild_id=$2 AND user_id=$3 AND balance>=$1 RETURNING balance",
            amount, gid, uid
        )
        if not row: return await send(f"❌ You don't have **{amount:,}** coins.")
        await db.pool.execute("UPDATE economy SET balance=balance+$1 WHERE guild_id=$2 AND user_id=$3", amount, gid, member.id)
        e = discord.Embed(description=f"🎁 {ctx.author.mention} gifted **{amount:,}** 🪙 to {member.mention}!", color=C_SUCCESS)
        await send(embed=e)


    # ── Buy (direct) ───────────────────────────────────────────────
    @commands.hybrid_command(name="buy", description="Buy a shop item")
    async def buy(self, ctx, *, item: str = None):
        if item is None:
            return await self.shop(ctx)
        key = item.lower().replace(" ","_")
        if key not in SHOP_ITEMS:
            return await _err(ctx, f"Unknown item `{key}`. Check `!shop`.")
        await _do_buy(ctx, key)

    # ── Title ──────────────────────────────────────────────────────
    @commands.hybrid_command(name="title", description="Equip one of your titles")
    async def title(self, ctx, *, title_name: str = None):
        gid, uid = ctx.guild.id, ctx.author.id
        row = await db.pool.fetchrow("SELECT titles, active_title FROM economy WHERE guild_id=$1 AND user_id=$2", gid, uid)
        owned = list(row["titles"] or []) if row else []
        if not owned:
            return await _err(ctx, "You don't own any titles yet. Buy some from `!shop`!")
        if title_name:
            if title_name not in owned:
                return await _err(ctx, f"You don't own title `{title_name}`.")
            await db.pool.execute("UPDATE economy SET active_title=$1 WHERE guild_id=$2 AND user_id=$3", title_name, gid, uid)
            return await ctx.send(embed=discord.Embed(description=f"✅ Title **{title_name}** equipped!", color=C_SUCCESS))
        # Show dropdown
        opts = [discord.SelectOption(label=t, value=t) for t in owned[:25]]
        sel  = discord.ui.Select(placeholder="Choose a title to equip…", options=opts)
        async def cb(inter: discord.Interaction):
            if inter.user.id != uid: return await inter.response.send_message("Not for you.", ephemeral=True)
            chosen = sel.values[0]
            await db.pool.execute("UPDATE economy SET active_title=$1 WHERE guild_id=$2 AND user_id=$3", chosen, gid, uid)
            for i in v.children: i.disabled = True
            await inter.response.edit_message(content=f"✅ Title **{chosen}** equipped!", view=v)
        sel.callback = cb
        class v(discord.ui.View):
            def __init__(self_): super().__init__(timeout=120); self_.add_item(sel)
        await ctx.send("Choose a title to equip:", view=v())


async def _do_buy(ctx_or_inter, key: str):
    item = SHOP_ITEMS.get(key)
    if not item: return
    if hasattr(ctx_or_inter, "guild_id"):
        gid = ctx_or_inter.guild_id
        uid = ctx_or_inter.user.id
        async def send(msg=None, **kw):
            try: await ctx_or_inter.followup.send(msg, **kw, ephemeral=True) if msg else await ctx_or_inter.followup.send(**kw, ephemeral=True)
            except Exception: pass
        user = ctx_or_inter.user
    else:
        gid = ctx_or_inter.guild.id
        uid = ctx_or_inter.author.id
        send = ctx_or_inter.send
        user = ctx_or_inter.author
    price = item["price"]
    row = await db.pool.fetchrow(
        "UPDATE economy SET balance=balance-$1 WHERE guild_id=$2 AND user_id=$3 AND balance>=$1 RETURNING balance",
        price, gid, uid
    )
    if not row:
        bal = await get_balance(gid, uid)
        return await send(f"❌ Need **{price:,}** 🪙 but you have **{bal:,}** 🪙.")
    itype = item["type"]
    if itype == "title":
        tname = key.replace("title_","")
        await db.pool.execute("""
            UPDATE economy SET titles=CASE WHEN $1=ANY(COALESCE(titles,'{}')) THEN titles
            ELSE ARRAY_APPEND(COALESCE(titles,'{}'), $1) END WHERE guild_id=$2 AND user_id=$3
        """, tname, gid, uid)
        await send(embed=discord.Embed(description=f"✅ Title **{tname}** added! Equip with `!title {tname}`.", color=C_SUCCESS))
    elif itype in ("xp_boost","xp_boost24"):
        hours = 1 if itype == "xp_boost" else 24
        from datetime import timedelta
        until = datetime.now(timezone.utc) + timedelta(hours=hours)
        await db.pool.execute("UPDATE economy SET xp_boost_until=$1 WHERE guild_id=$2 AND user_id=$3", until, gid, uid)
        await send(embed=discord.Embed(description=f"✅ **XP Boost** active for **{hours}h**!", color=C_SUCCESS))
    elif itype == "shield":
        from airi.inventory import add_item as _ai
        await _ai(gid, uid, "shield", 1)
        await send(embed=discord.Embed(description="✅ **Waifu Shield** added to inventory!", color=C_SUCCESS))
    elif itype == "prenup":
        from airi.inventory import add_item as _ai
        await _ai(gid, uid, "prenup", 1)
        await send(embed=discord.Embed(description="✅ **Prenup** added to inventory!", color=C_SUCCESS))
    else:
        await send(embed=discord.Embed(description=f"✅ Purchased **{item['name']}**!", color=C_SUCCESS))
