# airi/economy.py
import discord
from discord.ext import commands
from datetime import datetime, timedelta
import random
import db
from utils import _err, C_ECONOMY, C_INFO, C_SUCCESS, log_txn
from airi.guild_config import check_channel, get_txn_channel

DAILY_MIN = 150; DAILY_MAX = 350; DAILY_COOLDOWN = 22
STREAK_BONUS = 10; STREAK_CAP = 200

SHOP_ITEMS = {
    "weeb":    {"price": 200,  "desc": "Title: *Weeb*",                  "type": "title"},
    "senpai":  {"price": 500,  "desc": "Title: *Senpai*",                "type": "title"},
    "kouhai":  {"price": 300,  "desc": "Title: *Kouhai*",                "type": "title"},
    "master":  {"price": 800,  "desc": "Title: *Master*",                "type": "title"},
    "slave":   {"price": 100,  "desc": "Title: *Slave*",                 "type": "title"},
    "goddess": {"price": 1200, "desc": "Title: *Goddess*",               "type": "title"},
    "otaku":   {"price": 400,  "desc": "Title: *Otaku*",                 "type": "title"},
    "yandere": {"price": 700,  "desc": "Title: *Yandere*",               "type": "title"},
    "xpboost": {"price": 1000, "desc": "2x XP for 1 hour",               "type": "xp_boost"},
    "dailyx2": {"price": 600,  "desc": "2x your next daily",             "type": "daily_boost"},
    "shield":  {"price": 1500, "desc": "Shield - 7 days protection",     "type": "shield"},
    "prenup":  {"price": 5000, "desc": "Prenup - protects assets on divorce", "type": "prenup"},
}


async def ensure_user(conn, gid, uid):
    await conn.execute(
        "INSERT INTO economy (guild_id,user_id) VALUES ($1,$2) ON CONFLICT DO NOTHING", gid, uid
    )


async def get_balance(guild_id, user_id):
    row = await db.pool.fetchrow(
        "SELECT balance FROM economy WHERE guild_id=$1 AND user_id=$2", guild_id, user_id
    )
    return row["balance"] if row else 0


async def add_coins(guild_id, user_id, amount):
    row = await db.pool.fetchrow("""
        INSERT INTO economy (guild_id,user_id,balance) VALUES ($1,$2,GREATEST(0,$3))
        ON CONFLICT (guild_id,user_id) DO UPDATE SET balance=GREATEST(0,economy.balance+$3)
        RETURNING balance
    """, guild_id, user_id, amount)
    return row["balance"] if row else 0


async def get_title(guild_id, user_id):
    row = await db.pool.fetchrow(
        "SELECT active_title FROM economy WHERE guild_id=$1 AND user_id=$2", guild_id, user_id
    )
    return row["active_title"] if row else None


async def is_xp_boosted(guild_id, user_id):
    row = await db.pool.fetchrow(
        "SELECT xp_boost_until FROM economy WHERE guild_id=$1 AND user_id=$2", guild_id, user_id
    )
    return bool(row and row["xp_boost_until"] and datetime.utcnow() < row["xp_boost_until"])


async def _recent_txns(gid: int, uid: int, limit: int = 10) -> list[dict]:
    rows = await db.pool.fetch("""
        SELECT action, detail, amount, created_at
        FROM audit_log
        WHERE guild_id=$1 AND user_id=$2
        ORDER BY created_at DESC LIMIT $3
    """, gid, uid, limit)
    return [dict(r) for r in rows]


class EconomyCog(commands.Cog, name="Economy"):
    def __init__(self, bot): self.bot = bot

    @commands.command(aliases=["bal", "coins", "money"])
    async def balance(self, ctx, member: discord.Member = None):
        if not await check_channel(ctx, "economy"): return
        target = member or ctx.author
        gid, uid = ctx.guild.id, target.id

        row = await db.pool.fetchrow(
            "SELECT balance,active_title,streak FROM economy WHERE guild_id=$1 AND user_id=$2",
            gid, uid
        )
        bal    = row["balance"]       if row else 0
        title  = row["active_title"]  if row else None
        streak = row["streak"]        if row else 0

        # XP
        xpr = await db.pool.fetchrow(
            "SELECT xp, level FROM xp WHERE guild_id=$1 AND user_id=$2", gid, uid
        )
        level = xpr["level"] if xpr else 0

        # Recent transactions
        txns = await _recent_txns(gid, uid, 10)

        e = discord.Embed(
            title=f"{'*'+title+'*  ·  ' if title else ''}💰 {target.display_name}'s Wallet",
            color=C_ECONOMY,
        )
        e.set_thumbnail(url=target.display_avatar.url)
        e.add_field(name="Balance",  value=f"**{bal:,}** coins", inline=True)
        e.add_field(name="Level",    value=str(level),           inline=True)
        if streak > 1:
            e.add_field(name="🔥 Streak", value=f"{streak} days", inline=True)

        if txns:
            def fmt(t):
                amt = t["amount"]
                sign = ("+" if amt >= 0 else "") if amt is not None else ""
                amt_str = f"  `{sign}{amt:,}`" if amt is not None else ""
                dt = t["created_at"].strftime("%m/%d %H:%M") if t["created_at"] else ""
                detail = (t["detail"] or "")[:30]
                return f"`{dt}` {t['action']}{amt_str} {detail}"
            txn_lines = "\n".join(fmt(t) for t in txns)
            e.add_field(name="📋 Recent Transactions (last 10)", value=txn_lines, inline=False)
        else:
            e.add_field(name="📋 Recent Transactions", value="*No transactions yet.*", inline=False)

        await ctx.send(embed=e)

    @commands.command(aliases=["dp", "earn"])
    async def daily(self, ctx):
        """Open the Economy Panel — claim daily, work, or commit crime from one place."""
        if not await check_channel(ctx, "economy"): return
        from airi.daily_panel import open_daily_panel
        await open_daily_panel(ctx)

    async def _do_daily(self, ctx):
        """Internal daily claim logic, called by the panel button."""
        gid, uid, now = ctx.guild.id, ctx.author.id, datetime.utcnow()
        async with db.pool.acquire() as conn:
            await ensure_user(conn, gid, uid)
            row = await conn.fetchrow(
                "SELECT balance,last_daily,streak,daily_boost FROM economy WHERE guild_id=$1 AND user_id=$2",
                gid, uid
            )
        last = row["last_daily"]; streak = row["streak"] or 0
        if last:
            elapsed = now - last
            if elapsed < timedelta(hours=DAILY_COOLDOWN):
                remaining = timedelta(hours=DAILY_COOLDOWN) - elapsed
                h, rem = divmod(int(remaining.total_seconds()), 3600)
                return await _err(ctx, f"Come back in **{h}h {rem//60}m**.")
            streak = streak + 1 if elapsed < timedelta(hours=48) else 1
        else:
            streak = 1
        amount = random.randint(DAILY_MIN, DAILY_MAX)
        if row["daily_boost"]: amount *= 2
        bonus = min(streak * STREAK_BONUS, STREAK_CAP)
        total = amount + bonus
        await db.pool.execute(
            "UPDATE economy SET balance=balance+$1,last_daily=$2,streak=$3,daily_boost=FALSE WHERE guild_id=$4 AND user_id=$5",
            total, now, streak, gid, uid
        )
        from airi.audit_log import log as audit
        await audit(gid, uid, "daily", f"streak {streak}", total)
        await log_txn(self.bot, gid, "Daily", "System", ctx.author, total, f"Streak {streak}")
        from airi.milestones import update_achievement
        await update_achievement(self.bot, gid, uid, "daily_7", streak if streak <= 7 else 1, ctx.channel)
        if streak >= 7:
            await update_achievement(self.bot, gid, uid, "daily_30", streak if streak <= 30 else 1, ctx.channel)
        e = discord.Embed(title="💰 Daily Claimed!", color=C_ECONOMY)
        e.add_field(name="Base",                     value=f"{amount:,} coins", inline=True)
        e.add_field(name=f"🔥 Day {streak} Streak",  value=f"+{bonus:,} coins", inline=True)
        e.add_field(name="Total",                    value=f"**{total:,} coins**", inline=False)
        e.set_footer(text="Come back tomorrow to keep your streak!")
        await ctx.send(embed=e)

    @commands.command()
    async def pay(self, ctx, member: discord.Member, amount: int):
        if not await check_channel(ctx, "economy"): return
        if member.bot or member == ctx.author: return await _err(ctx, "Invalid target.")
        if amount <= 0 or amount > 10000: return await _err(ctx, "Amount must be 1–10,000.")
        gid, uid = ctx.guild.id, ctx.author.id
        tax    = max(1, int(amount * 0.05))
        net    = amount - tax
        bal    = await get_balance(gid, uid)
        if bal < amount: return await _err(ctx, f"Need **{amount:,}** but have **{bal:,}**.")
        await add_coins(gid, uid, -amount)
        await add_coins(gid, member.id, net)
        from airi.audit_log import log as audit
        await audit(gid, uid, "pay", f"to {member.display_name}", -amount)
        await audit(gid, member.id, "receive", f"from {ctx.author.display_name}", net)
        await log_txn(self.bot, gid, "Pay", ctx.author, member, net, f"Tax {tax}")
        e = discord.Embed(
            description=(
                f"💸 {ctx.author.mention} → {member.mention}\n"
                f"**{net:,}** coins *(5% tax: {tax:,})*"
            ),
            color=C_ECONOMY,
        )
        # Post to txn channel if configured
        txn_ch_id = await get_txn_channel(gid)
        if txn_ch_id:
            txn_ch = self.bot.get_channel(txn_ch_id)
            if txn_ch: await txn_ch.send(embed=e)
        await ctx.send(embed=e)

    @commands.command()
    async def give(self, ctx, member: discord.Member, amount: int):
        """Tax-free gift. Max 1,000 coins per transaction."""
        if not await check_channel(ctx, "economy"): return
        if member.bot or member == ctx.author: return await _err(ctx, "Invalid target.")
        if amount <= 0 or amount > 1000: return await _err(ctx, "Amount must be 1–1,000 coins.")
        gid, uid = ctx.guild.id, ctx.author.id
        bal = await get_balance(gid, uid)
        if bal < amount: return await _err(ctx, f"Need **{amount:,}** but have **{bal:,}**.")
        await add_coins(gid, uid, -amount)
        await add_coins(gid, member.id, amount)
        await log_txn(self.bot, gid, "Gift", ctx.author, member, amount)
        await ctx.send(embed=discord.Embed(
            description=f"🎁 {ctx.author.mention} gifted **{amount:,} coins** to {member.mention}.",
            color=C_SUCCESS
        ))

    @commands.command()
    async def shop(self, ctx):
        if not await check_channel(ctx, "economy"): return

        class ShopSelect(discord.ui.Select):
            def __init__(self_):
                options = [
                    discord.SelectOption(
                        label=f"{k} — {v['desc'][:50]}",
                        value=k,
                        description=f"{v['price']:,} coins",
                    )
                    for k, v in SHOP_ITEMS.items()
                ]
                super().__init__(placeholder="Browse items...", options=options[:25])

            async def callback(self_, inter: discord.Interaction):
                if inter.user.id != ctx.author.id:
                    await inter.response.send_message("Not for you.", ephemeral=True)
                    return
                key = self_.values[0]
                item = SHOP_ITEMS[key]
                e = discord.Embed(
                    title=f"🛍️ {key.title()}",
                    description=f"{item['desc']}\n\n**Price:** {item['price']:,} coins",
                    color=C_ECONOMY
                )

                class BuyView(discord.ui.View):
                    @discord.ui.button(label="Buy", style=discord.ButtonStyle.success)
                    async def buy(self__, inter2, btn):
                        for i in self__.children: i.disabled = True
                        await inter2.response.edit_message(view=self__)
                        await _do_buy(inter2, ctx.guild.id, inter2.user.id, key)

                await inter.response.send_message(embed=e, view=BuyView(), ephemeral=True)

        class ShopView(discord.ui.View):
            def __init__(self_): super().__init__(timeout=120); self_.add_item(ShopSelect())

        e = discord.Embed(title="🛍️ Shop", description="Select an item to preview and buy.", color=C_ECONOMY)
        for k, v in SHOP_ITEMS.items():
            e.add_field(name=f"`{k}` — {v['price']:,} coins", value=v["desc"], inline=True)
        await ctx.send(embed=e, view=ShopView())

    @commands.command()
    async def buy(self, ctx, item: str):
        if not await check_channel(ctx, "economy"): return
        await _do_buy(ctx, ctx.guild.id, ctx.author.id, item.lower().strip())

    @commands.command()
    async def title(self, ctx, *, name: str = None):
        if not await check_channel(ctx, "economy"): return
        gid, uid = ctx.guild.id, ctx.author.id
        row = await db.pool.fetchrow("SELECT titles, active_title FROM economy WHERE guild_id=$1 AND user_id=$2", gid, uid)
        owned = list(row["titles"] or []) if row else []
        if not owned:
            return await _err(ctx, "You don't own any titles. Buy them from `!shop`.")
        if name is None:
            await ctx.send(
                f"Your titles: {', '.join(f'`{t}`' for t in owned)}\n"
                f"Equip with `!title <name>`"
            )
            return
        name = name.lower()
        if name not in owned:
            return await _err(ctx, f"You don't own `{name}`.")
        await db.pool.execute(
            "UPDATE economy SET active_title=$1 WHERE guild_id=$2 AND user_id=$3", name, gid, uid
        )
        await ctx.send(f"✅ Title **{name}** equipped!", delete_after=8)


async def _do_buy(ctx_or_inter, guild_id: int, user_id: int, item_key: str):
    """Shared buy logic for both !buy command and shop button."""
    if item_key not in SHOP_ITEMS:
        msg = f"Unknown item `{item_key}`. See `!shop`."
        if isinstance(ctx_or_inter, discord.Interaction):
            await ctx_or_inter.followup.send("❌ " + msg, ephemeral=True)
        else:
            await _err(ctx_or_inter, msg)
        return

    item  = SHOP_ITEMS[item_key]
    price = item["price"]
    itype = item["type"]
    gid   = guild_id
    uid   = user_id

    bal = await get_balance(gid, uid)
    if bal < price:
        msg = f"Need **{price:,}** but have **{bal:,}** coins."
        if isinstance(ctx_or_inter, discord.Interaction):
            await ctx_or_inter.followup.send("❌ " + msg, ephemeral=True)
        else:
            await _err(ctx_or_inter, msg)
        return

    await add_coins(gid, uid, -price)

    if itype == "title":
        await db.pool.execute("""
            UPDATE economy SET
                titles = CASE WHEN titles IS NULL THEN ARRAY[$1]::TEXT[]
                              ELSE ARRAY_APPEND(titles,$1) END
            WHERE guild_id=$2 AND user_id=$3
        """, item_key, gid, uid)
        await db.pool.execute(
            "INSERT INTO economy (guild_id,user_id) VALUES ($1,$2) ON CONFLICT DO NOTHING", gid, uid
        )
        msg = f"✅ Bought title **{item_key}**! Equip with `!title {item_key}`."
    elif itype == "xp_boost":
        until = datetime.utcnow() + timedelta(hours=1)
        await db.pool.execute(
            "UPDATE economy SET xp_boost_until=$1 WHERE guild_id=$2 AND user_id=$3", until, gid, uid
        )
        msg = "✅ XP Boost active for **1 hour**!"
    elif itype == "daily_boost":
        await db.pool.execute(
            "UPDATE economy SET daily_boost=TRUE WHERE guild_id=$1 AND user_id=$2", gid, uid
        )
        msg = "✅ Next `!daily` will be **doubled**!"
    elif itype == "shield":
        until = datetime.utcnow() + timedelta(days=7)
        await db.pool.execute("""
            INSERT INTO protection (guild_id,user_id,expires_at) VALUES ($1,$2,$3)
            ON CONFLICT (guild_id,user_id) DO UPDATE SET expires_at=$3
        """, gid, uid, until)
        msg = "✅ **Claim Shield** active for **7 days**!"
    elif itype == "prenup":
        await db.pool.execute("""
            UPDATE economy SET
                titles = CASE WHEN titles IS NULL THEN ARRAY['prenup']::TEXT[]
                              ELSE ARRAY_APPEND(titles,'prenup') END
            WHERE guild_id=$1 AND user_id=$2
        """, gid, uid)
        msg = "✅ **Prenup Doc** added to your inventory. Attach it when proposing marriage."
    else:
        msg = "✅ Item purchased!"

    from airi.audit_log import log as audit
    await audit(gid, uid, f"buy_{item_key}", item_key, -price)

    if isinstance(ctx_or_inter, discord.Interaction):
        await ctx_or_inter.followup.send(msg, ephemeral=True)
    else:
        await ctx_or_inter.send(msg, delete_after=15)
