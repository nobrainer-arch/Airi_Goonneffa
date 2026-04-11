# airi/orders.py — Buy order board
# Flow: !order new → item SELECT → price modal → quantity confirm → post
import discord
from discord.ext import commands
from datetime import datetime
import db
from utils import _err, C_ECONOMY, C_SUCCESS, C_WARN
from airi.guild_config import check_channel, get_market_channel
from airi.economy import add_coins, get_balance
from airi.inventory import ITEMS, RARITY_STAR, get_quantity, remove_item, add_item

PAGE_SIZE = 6
ORDER_MAX = 10


async def _post_order(inter: discord.Interaction, item_key: str, price: int, qty: int):
    """Internal: validate, hold coins, insert order, send to channel."""
    gid = inter.guild_id; uid = inter.user.id
    total = price * qty
    bal = await get_balance(gid, uid)
    if bal < total:
        return await inter.followup.send(f"❌ Need **{total:,}** coins but have **{bal:,}**.", ephemeral=True)
    open_count = await db.pool.fetchval(
        "SELECT COUNT(*) FROM orders WHERE guild_id=$1 AND buyer_id=$2 AND status='open'", gid, uid
    ) or 0
    if open_count >= ORDER_MAX:
        return await inter.followup.send(f"❌ Max **{ORDER_MAX}** open orders.", ephemeral=True)
    await add_coins(gid, uid, -total)
    item_info = ITEMS.get(item_key, {"name": item_key, "rarity": "common"})
    row = await db.pool.fetchrow("""
        INSERT INTO orders (guild_id,buyer_id,item_key,item_name,max_price,quantity,status)
        VALUES ($1,$2,$3,$4,$5,$6,'open') RETURNING id
    """, gid, uid, item_key, item_info["name"], price, qty)
    star = RARITY_STAR.get(item_info.get("rarity","common"), "⬜")
    e = discord.Embed(
        title="📦 Buy Order Posted",
        description=(
            f"{star} **{item_info['name']}** × {qty}\n"
            f"💰 Up to **{price:,}** coins each · Buyer: {inter.user.mention}\n\n"
            f"*Order #{row['id']} — Sellers: use `!orderbook` to fulfill*"
        ),
        color=C_ECONOMY, timestamp=datetime.utcnow(),
    )
    market_ch_id = await get_market_channel(gid)
    ch = inter.client.get_channel(market_ch_id) if market_ch_id else inter.channel
    if ch and ch.id != inter.channel_id:
        await ch.send(embed=e)
        await inter.followup.send(f"✅ Order posted in {ch.mention}!", ephemeral=True)
    else:
        await inter.followup.send(embed=e)


class _PriceQtyModal(discord.ui.Modal):
    price_in = discord.ui.TextInput(label="Max price per unit (coins)", placeholder="e.g. 1500", required=True)
    qty_in   = discord.ui.TextInput(label="Quantity wanted", placeholder="1", default="1", required=False)
    def __init__(self, item_key: str):
        super().__init__(title=f"Order: {ITEMS.get(item_key,{}).get('name',item_key)}")
        self._item_key = item_key
    async def on_submit(self, inter: discord.Interaction):
        raw_p = self.price_in.value.strip().replace(",","")
        raw_q = (self.qty_in.value.strip() or "1").replace(",","")
        if not raw_p.isdigit() or not raw_q.isdigit():
            return await inter.response.send_message("❌ Invalid numbers.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        await _post_order(inter, self._item_key, int(raw_p), max(1, int(raw_q)))


class OrdersCog(commands.Cog, name="Orders"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_group(name="order", invoke_without_command=True)
    async def order(self, ctx):
        """Order system — buy items from other players."""
        await self.orderbook(ctx)

    @order.command(name="new", description="Post a buy order")
    async def order_new(self, ctx):
        """Post a new buy order — select item → enter price → confirm."""
        if not await check_channel(ctx, "economy"): return
        opts = [
            discord.SelectOption(
                label=v["name"][:50], value=k,
                description=f"Rarity: {v.get('rarity','common').title()}"
            )
            for k, v in ITEMS.items()
        ][:25]
        sel = discord.ui.Select(placeholder="Select the item you want to buy…", options=opts)
        async def cb(inter: discord.Interaction):
            if inter.user.id != ctx.author.id: return await inter.response.send_message("Not for you.", ephemeral=True)
            await inter.response.send_modal(_PriceQtyModal(sel.values[0]))
        sel.callback = cb
        class V(discord.ui.View):
            def __init__(self_): super().__init__(timeout=180); self_.add_item(sel)
        e = discord.Embed(
            title="📦 New Buy Order",
            description="Select the item you want to buy, then set your max price and quantity.",
            color=C_ECONOMY
        )
        await ctx.send(embed=e, view=V())

    @commands.hybrid_command(name="orderbook", aliases=["orders"], description="Browse buy orders")
    async def orderbook(self, ctx):
        """Browse open buy orders and fulfill them."""
        if not await check_channel(ctx, "economy"): return
        gid = ctx.guild.id
        uid = ctx.author.id

        rows = await db.pool.fetch("""
            SELECT o.*, e.active_title
            FROM orders o
            LEFT JOIN economy e ON e.guild_id=o.guild_id AND e.user_id=o.buyer_id
            WHERE o.guild_id=$1 AND o.status='open' AND o.buyer_id!=$2
            ORDER BY o.max_price DESC LIMIT 50
        """, gid, uid)

        if not rows:
            return await ctx.send(embed=discord.Embed(
                description="No open buy orders right now.", color=C_ECONOMY
            ))

        pages  = [rows[i:i+PAGE_SIZE] for i in range(0, len(rows), PAGE_SIZE)]
        current = [0]

        def build(idx):
            chunk = pages[idx]
            e = discord.Embed(title="📦 Order Book", color=C_ECONOMY)
            for r in chunk:
                star = RARITY_STAR.get(ITEMS.get(r["item_key"],{}).get("rarity","common"), "⬜")
                buyer = ctx.guild.get_member(r["buyer_id"])
                bname = buyer.display_name if buyer else f"<@{r['buyer_id']}>"
                e.add_field(
                    name=f"#{r['id']} {star} {r['item_name']} ×{r['quantity']}",
                    value=f"💰 Up to **{r['max_price']:,}** each\n👤 {bname}",
                    inline=True,
                )
            e.set_footer(text=f"Page {idx+1}/{len(pages)} · !order new to post")
            return e

        # Build fulfill options
        def _fulfill_opts(chunk):
            options = [
                discord.SelectOption(
                    label=f"#{r['id']} {r['item_name']} ×{r['quantity']} — {r['max_price']:,}c",
                    value=str(r["id"])
                )
                for r in chunk
            ]
            return options

        class OView(discord.ui.View):
            def __init__(self_):
                super().__init__(timeout=300)
                self_._chunk = pages[current[0]]
                self_._upd()

            def _upd(self_):
                self_.prev.disabled = current[0] == 0
                self_.nxt.disabled  = current[0] == len(pages) - 1
                # Rebuild fulfill select
                for i in list(self_.children):
                    if isinstance(i, discord.ui.Select): self_.remove_item(i)
                if self_._chunk:
                    sel = discord.ui.Select(
                        placeholder="Fulfill an order…",
                        options=_fulfill_opts(self_._chunk)[:25]
                    )
                    sel.callback = self_._fulfill_cb
                    self_.add_item(sel)

            @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
            async def prev(self_, i, b):
                if i.user.id != ctx.author.id: return await i.response.send_message("Not for you.", ephemeral=True)
                current[0] -= 1; self_._chunk = pages[current[0]]; self_._upd()
                await i.response.edit_message(embed=build(current[0]), view=self_)

            @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
            async def nxt(self_, i, b):
                if i.user.id != ctx.author.id: return await i.response.send_message("Not for you.", ephemeral=True)
                current[0] += 1; self_._chunk = pages[current[0]]; self_._upd()
                await i.response.edit_message(embed=build(current[0]), view=self_)

            async def _fulfill_cb(self_, inter: discord.Interaction):
                if inter.user.id == ctx.author.id and inter.user.id in {r["buyer_id"] for r in rows}:
                    # Check if they're trying to fulfill their own
                    pass
                order_id = int(self_.view.children[-1].values[0] if hasattr(self_, 'view') else inter.data["values"][0])
                # Re-fetch safely
                order_id = int(inter.data["values"][0])
                order = await db.pool.fetchrow("SELECT * FROM orders WHERE id=$1 AND status='open'", order_id)
                if not order:
                    return await inter.response.send_message("❌ Order no longer available.", ephemeral=True)
                if order["buyer_id"] == inter.user.id:
                    return await inter.response.send_message("❌ Can't fulfill your own order.", ephemeral=True)
                gid2 = inter.guild_id; uid2 = inter.user.id
                qty   = order["quantity"] - order["filled"]
                owned = await get_quantity(gid2, uid2, order["item_key"])
                if owned < qty:
                    return await inter.response.send_message(
                        f"❌ You need **{qty}×** {order['item_name']} but only have **{owned}×**.", ephemeral=True
                    )
                payment = order["max_price"] * qty
                await remove_item(gid2, uid2, order["item_key"], qty)
                await add_item(gid2, order["buyer_id"], order["item_key"], qty)
                await add_coins(gid2, uid2, payment)
                await db.pool.execute(
                    "UPDATE orders SET status='filled',filled=filled+$1 WHERE id=$2", qty, order_id
                )
                e2 = discord.Embed(
                    description=f"✅ {inter.user.mention} fulfilled order #{order_id}!\n"
                                f"💰 +**{payment:,}** coins for **{qty}×** {order['item_name']}",
                    color=C_SUCCESS
                )
                await inter.response.edit_message(embed=build(current[0]), view=self_)
                await inter.followup.send(embed=e2)

        await ctx.send(embed=build(0), view=OView())
