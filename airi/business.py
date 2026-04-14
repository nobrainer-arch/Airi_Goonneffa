# airi/business.py
import discord
from discord.ext import commands
from datetime import datetime, timedelta, timezone
import random
import db
from utils import _err, C_BUSINESS, C_WARN, C_SUCCESS
from airi.guild_config import check_channel
from airi.economy import add_coins

TAX_RATE    = 0.15
MANAGER_CUT = 0.20

BIZ_TYPES = {
    "cafe":   {"emoji": "☕", "cost": 15000, "min_level": 5,  "income": (200,  500), "desc": "Serves coffee and treats."},
    "shop":   {"emoji": "🛍️", "cost": 25000, "min_level": 10, "income": (350,  700), "desc": "Sells random goods."},
    "arcade": {"emoji": "🎮", "cost": 40000, "min_level": 20, "income": (500, 1000), "desc": "Entertainment for all."},
    "studio": {"emoji": "🎬", "cost": 60000, "min_level": 30, "income": (800, 1500), "desc": "Content production house."},
}

COLLECT_INTERVAL = timedelta(hours=1)

EVENTS = [
    ("🔥 Rush Hour",    1.5,  "Business is booming! Income ×1.5."),
    ("🎉 Viral Post",   1.3,  "Went viral! +30% this collection."),
    ("🏥 Inspection",  -200,  "Health inspection fine: −200 coins."),
    ("🔧 Maintenance", -150,  "Equipment broke. Paid −150 to fix."),
    ("😴 Slow Day",     0.7,  "Slow day — income at 70%."),
]


async def _get_level(guild_id, user_id):
    row = await db.pool.fetchrow("SELECT level FROM xp WHERE guild_id=$1 AND user_id=$2", guild_id, user_id)
    return row["level"] if row else 0

async def _biz_count(guild_id):
    return await db.pool.fetchval("SELECT COUNT(*) FROM businesses WHERE guild_id=$1 ", guild_id) or 0

async def _max_biz(guild_id, guild):
    members = guild.member_count or 1
    return max(3, members // 20)


# ── Business type picker view ─────────────────────────────────────
class BizTypeView(discord.ui.View):
    def __init__(self, author_id: int, level: int):
        super().__init__(timeout=60)
        self._author = author_id
        options = []
        for key, info in BIZ_TYPES.items():
            eligible = level >= info["min_level"]
            label = f"{info['emoji']} {key.title()} — {info['cost']:,} coins"
            desc  = f"Lv.{info['min_level']}+ · {info['income'][0]}–{info['income'][1]}/h" + ("" if eligible else " 🔒")
            options.append(discord.SelectOption(
                label=label[:100], value=key, description=desc[:100],
                emoji=info["emoji"], default=False,
            ))
        sel = discord.ui.Select(placeholder="Choose your business type...", options=options)
        sel.callback = self._selected
        self.add_item(sel)
        self._chosen = None

    async def _selected(self, interaction: discord.Interaction):
        if interaction.user.id != self._author:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        self._chosen = interaction.data["values"][0]
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


class BusinessCog(commands.Cog, name="Business"):
    def __init__(self, bot): self.bot = bot

    @commands.command(aliases=["openbiz"])
    async def startbiz(self, ctx, biz_type: str = None, *, name: str = None):
        """Start a business. !startbiz — shows a picker."""
        if not await check_channel(ctx, "business"): return
        gid, uid = ctx.guild.id, ctx.author.id
        level = await _get_level(gid, uid)

        # Check already has one
        existing = await db.pool.fetchrow(
            "SELECT id FROM businesses WHERE guild_id=$1 AND owner_id=$2 ", gid, uid
        )
        if existing:
            return await _err(ctx, "You already own a business. Use `!mybiz` to manage it.")

        # Server capacity
        if await _biz_count(gid) >= await _max_biz(gid, ctx.guild):
            return await _err(ctx, "The server has reached its business limit.")

        # Show picker if no type given
        if biz_type is None or biz_type.lower() not in BIZ_TYPES:
            view = BizTypeView(uid, level)
            e = discord.Embed(
                title="🏭 Open a Business",
                description="Select the type of business you want to open.\n🔒 = level requirement not met yet.",
                color=C_BUSINESS
            )
            for key, info in BIZ_TYPES.items():
                e.add_field(
                    name=f"{info['emoji']} {key.title()}",
                    value=f"**{info['cost']:,}** coins · Lv.{info['min_level']}+ · {info['income'][0]}–{info['income'][1]}/h\n*{info['desc']}*",
                    inline=True
                )
            msg = await ctx.send(embed=e, view=view)
            timed_out = await view.wait()
            if timed_out or not view._chosen:
                return
            biz_type = view._chosen
            name = None  # will prompt below

        biz_type = biz_type.lower()
        if biz_type not in BIZ_TYPES:
            return await _err(ctx, f"Unknown type. Choose: {', '.join(BIZ_TYPES)}")

        info = BIZ_TYPES[biz_type]
        if level < info["min_level"]:
            return await _err(ctx, f"You need **Level {info['min_level']}** to open a {biz_type}.")

        from airi.economy import get_balance
        bal = await get_balance(gid, uid)
        if bal < info["cost"]:
            return await _err(ctx, f"Need **{info['cost']:,} coins** to open this. You have **{bal:,}**.")

        # Name prompt if not provided
        if not name:
            name_prompt = await ctx.send(
                embed=discord.Embed(description="What's the name of your business? (type it now, or say `skip`)", color=C_BUSINESS)
            )
            def check(m): return m.author == ctx.author and m.channel == ctx.channel
            try:
                import asyncio
                reply = await self.bot.wait_for("message", check=check, timeout=30)
                name = reply.content.strip() if reply.content.strip().lower() != "skip" else f"{ctx.author.display_name}'s {biz_type.title()}"
                try: await reply.delete()
                except Exception: pass
            except Exception:
                name = f"{ctx.author.display_name}'s {biz_type.title()}"
            try: await name_prompt.delete()
            except Exception: pass

        name = name[:50]
        await add_coins(gid, uid, -info["cost"])
        await db.pool.execute("""
            INSERT INTO businesses (guild_id, owner_id, type, name, level, status, last_collected)
            VALUES ($1,$2,$3,$4,1,'running',NOW())
        """, gid, uid, biz_type, name)

        e = discord.Embed(
            title=f"{info['emoji']} {name} — Open!",
            description=(
                f"Your **{biz_type}** is now open!\n\n"
                f"💰 Cost: **{info['cost']:,}** coins\n"
                f"💸 Income: **{info['income'][0]}–{info['income'][1]}** coins/hour\n"
                f"Use `!collect` every hour to collect income."
            ),
            color=C_BUSINESS,
        )
        await ctx.send(embed=e)

    @commands.command(aliases=["mystore"])
    async def mybiz(self, ctx):
        """View your business stats."""
        if not await check_channel(ctx, "business"): return
        gid, uid = ctx.guild.id, ctx.author.id
        biz = await db.pool.fetchrow("SELECT * FROM businesses WHERE guild_id=$1 AND owner_id=$2 ", gid, uid)
        if not biz:
            return await ctx.send(embed=discord.Embed(
                description="You don't own a business. Use `!startbiz` to open one!",
                color=C_WARN
            ))
        info = BIZ_TYPES[biz["type"]]
        now  = datetime.now(timezone.utc)
        last = biz.get("last_collected")
        hours_since = (now - last).total_seconds() / 3600 if last else 0
        ready = hours_since >= 1.0

        manager = ctx.guild.get_member(biz["manager_id"]) if biz["manager_id"] else None
        e = discord.Embed(title=f"{info['emoji']} {biz['name']}", color=C_BUSINESS)
        e.add_field(name="Type",     value=biz["type"].title(),              inline=True)
        e.add_field(name="Level",    value=str(biz["level"]),                inline=True)
        e.add_field(name="Income",   value=f"{info['income'][0]}–{info['income'][1]}/h", inline=True)
        e.add_field(name="Manager",  value=manager.mention if manager else "None", inline=True)
        e.add_field(name="Collect",  value="✅ Ready!" if ready else f"⏰ {int(1-hours_since*60//60)}h left", inline=True)
        e.set_footer(text="!collect to collect income · !upgrade to level up · !hire @user to add manager")
        await ctx.send(embed=e)

    @commands.hybrid_command()
    async def collect(self, ctx):
        """Collect your business income."""
        if not await check_channel(ctx, "business"): return
        gid, uid = ctx.guild.id, ctx.author.id
        biz = await db.pool.fetchrow("SELECT * FROM businesses WHERE guild_id=$1 AND owner_id=$2 ", gid, uid)
        if not biz: return await _err(ctx, "You don't own a business.")

        now   = datetime.now(timezone.utc)
        last  = biz["last_collected"]
        if last and (now - last) < COLLECT_INTERVAL:
            rem = COLLECT_INTERVAL - (now - last)
            m   = int(rem.total_seconds() // 60)
            return await _err(ctx, f"Income not ready yet. Come back in **{m}m**.")

        info   = BIZ_TYPES[biz["type"]]
        earned = random.randint(*info["income"]) * biz["level"]

        # Random event
        event_txt = ""
        if random.random() < 0.25:
            event = random.choice(EVENTS)
            label, modifier, desc = event
            if isinstance(modifier, float):
                earned = int(earned * modifier)
            else:
                earned = max(0, earned + modifier)
            event_txt = f"\n\n{label} {desc}"

        # Tax
        tax    = max(1, int(earned * TAX_RATE))
        net    = earned - tax
        await add_coins(gid, uid, net)

        # Manager cut
        if biz["manager_id"]:
            mgr_cut = int(earned * MANAGER_CUT)
            await add_coins(gid, biz["manager_id"], mgr_cut)
            mgr = ctx.guild.get_member(biz["manager_id"])
            if mgr:
                try: await mgr.send(embed=discord.Embed(description=f"💼 You earned **{mgr_cut:,}** coins managing **{biz['name']}**!", color=C_BUSINESS))
                except Exception: pass

        await db.pool.execute("UPDATE businesses SET last_collected=$1 WHERE id=$2", now, biz["id"])

        e = discord.Embed(
            title=f"{info['emoji']} {biz['name']} — Collection",
            description=f"Collected **{net:,} coins** *(tax: {tax:,})*{event_txt}",
            color=C_SUCCESS,
        )
        e.set_footer(text=f"Level {biz['level']} · Next collection in 1h")
        await ctx.send(embed=e)

    @commands.command(aliases=["upgradestore"])
    async def upgrade(self, ctx):
        """Upgrade your business for higher income."""
        if not await check_channel(ctx, "business"): return
        gid, uid = ctx.guild.id, ctx.author.id
        biz = await db.pool.fetchrow("SELECT * FROM businesses WHERE guild_id=$1 AND owner_id=$2 ", gid, uid)
        if not biz: return await _err(ctx, "You don't own a business.")
        info      = BIZ_TYPES[biz["type"]]
        cost      = info["cost"] * biz["level"]
        new_level = biz["level"] + 1

        from airi.economy import get_balance
        bal = await get_balance(gid, uid)
        if bal < cost:
            return await _err(ctx, f"Upgrading to Level {new_level} costs **{cost:,}** coins. You have **{bal:,}**.")

        class UpgradeView(discord.ui.View):
            def __init__(self_): super().__init__(timeout=30)
            @discord.ui.button(label=f"Upgrade to Lv.{new_level} — {cost:,} coins", style=discord.ButtonStyle.success)
            async def yes(self_, inter, btn):
                if inter.user.id != uid: return await inter.response.send_message("Not for you.", ephemeral=True)
                for i in self_.children: i.disabled = True
                await inter.response.edit_message(view=self_)
                await add_coins(gid, uid, -cost)
                await db.pool.execute("UPDATE businesses SET level=$1 WHERE id=$2", new_level, biz["id"])
                await inter.followup.send(
                    embed=discord.Embed(description=f"✅ **{biz['name']}** upgraded to **Level {new_level}**!", color=C_SUCCESS),
                    ephemeral=True
                )
                self_.stop()
            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def no(self_, inter, btn):
                for i in self_.children: i.disabled = True
                await inter.response.edit_message(content="Cancelled.", view=self_)
                self_.stop()

        e = discord.Embed(
            title=f"Upgrade {biz['name']} to Level {new_level}?",
            description=f"Cost: **{cost:,}** coins\nNew income: **{int(info['income'][0]*new_level)}–{int(info['income'][1]*new_level)}**/h",
            color=C_BUSINESS
        )
        await ctx.send(embed=e, view=UpgradeView())

    @commands.hybrid_command()
    async def hire(self, ctx, member: discord.Member = None):
        """Hire a manager. Shows picker if no @ given."""
        if not await check_channel(ctx, "business"): return
        gid, uid = ctx.guild.id, ctx.author.id
        biz = await db.pool.fetchrow("SELECT * FROM businesses WHERE guild_id=$1 AND owner_id=$2 ", gid, uid)
        if not biz: return await _err(ctx, "You don't own a business.")

        if member is None:
            class HireView(discord.ui.View):
                def __init__(self_): super().__init__(timeout=60)
                @discord.ui.user_select(placeholder="Select a manager...")
                async def pick(self_, inter, sel):
                    if inter.user.id != uid: return await inter.response.send_message("Not for you.", ephemeral=True)
                    m = sel.values[0]
                    if m.bot or m.id == uid: return await inter.response.send_message("❌ Invalid choice.", ephemeral=True)
                    for i in self_.children: i.disabled = True
                    await inter.response.edit_message(view=self_)
                    await db.pool.execute("UPDATE businesses SET manager_id=$1 WHERE id=$2", m.id, biz["id"])
                    await inter.followup.send(
                        embed=discord.Embed(
                            description=f"👔 {m.mention} is now managing **{biz['name']}**. They earn **{int(MANAGER_CUT*100)}%** per collection.",
                            color=C_BUSINESS
                        )
                    )
                    self_.stop()
            await ctx.send("Who do you want to hire as manager?", view=HireView())
            return

        if member.bot or member == ctx.author: return await _err(ctx, "Invalid target.")
        await db.pool.execute("UPDATE businesses SET manager_id=$1 WHERE id=$2", member.id, biz["id"])
        await ctx.send(embed=discord.Embed(
            description=f"👔 {member.mention} is now managing **{biz['name']}**. They earn **{int(MANAGER_CUT*100)}%** per collection.",
            color=C_BUSINESS
        ))

    @commands.hybrid_command()
    async def sellbiz(self, ctx):
        """Sell your business for 60% of startup cost."""
        if not await check_channel(ctx, "business"): return
        gid, uid = ctx.guild.id, ctx.author.id
        biz = await db.pool.fetchrow("SELECT * FROM businesses WHERE guild_id=$1 AND owner_id=$2 ", gid, uid)
        if not biz: return await _err(ctx, "You don't own a business.")
        info   = BIZ_TYPES[biz["type"]]
        refund = int(info["cost"] * 0.60)

        class SellView(discord.ui.View):
            def __init__(self_): super().__init__(timeout=30)
            @discord.ui.button(label=f"Sell for {refund:,} coins", style=discord.ButtonStyle.danger)
            async def yes(self_, inter, btn):
                if inter.user.id != uid: return await inter.response.send_message("Not for you.", ephemeral=True)
                for i in self_.children: i.disabled = True
                await inter.response.edit_message(view=self_)
                await add_coins(gid, uid, refund)
                await db.pool.execute("UPDATE businesses SET status='sold' WHERE id=$1", biz["id"])
                await inter.followup.send(
                    embed=discord.Embed(description=f"💸 Sold **{biz['name']}** for **{refund:,} coins**.", color=C_WARN),
                    ephemeral=False
                )
                self_.stop()
            @discord.ui.button(label="Keep it", style=discord.ButtonStyle.secondary)
            async def no(self_, inter, btn):
                for i in self_.children: i.disabled = True
                await inter.response.edit_message(content="Cancelled.", view=self_)
                self_.stop()

        await ctx.send(
            embed=discord.Embed(
                title=f"Sell {biz['name']}?",
                description=f"You'll receive **{refund:,} coins** (60% of {info['cost']:,}).",
                color=C_WARN
            ),
            view=SellView()
        )

    @commands.hybrid_command()
    async def listbiz(self, ctx):
        """See all businesses in this server."""
        if not await check_channel(ctx, "business"): return
        gid  = ctx.guild.id
        rows = await db.pool.fetch(
            "SELECT * FROM businesses WHERE guild_id=$1  ORDER BY level DESC",
            gid
        )
        if not rows:
            return await ctx.send(embed=discord.Embed(description="No businesses open yet. Be the first!", color=C_BUSINESS))
        e = discord.Embed(title="🏭 Server Businesses", color=C_BUSINESS)
        for biz in rows[:20]:
            owner = ctx.guild.get_member(biz["owner_id"])
            info  = BIZ_TYPES.get(biz["type"], {})
            e.add_field(
                name=f"{info.get('emoji','🏭')} {biz['name']} (Lv.{biz['level']})",
                value=f"Owner: {owner.display_name if owner else '<left>'}",
                inline=True,
            )
        await ctx.send(embed=e)
