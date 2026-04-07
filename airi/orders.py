# airi/orders.py — DonutSMP-style order board
# Buyers post "I want X for Y coins" — sellers browse and fulfil
import discord
from discord.ext import commands
from datetime import datetime
import db
from utils import _err, C_ECONOMY, C_SUCCESS, C_WARN, C_ERROR
from airi.guild_config import check_channel, get_market_channel
from airi.economy import add_coins, get_balance
from airi.inventory import ITEMS, RARITY_STAR, get_quantity, remove_item, add_item

PAGE_SIZE = 6
ORDER_MAX = 10  # max open orders per user


class CreateOrderModal(discord.ui.Modal, title="Post a Buy Order"):
    item_in  = discord.ui.TextInput(label="Item key (from !inventory)",  placeholder="e.g. xp_boost_1h", required=True)
    price_in = discord.ui.TextInput(label="Max price per unit (coins)", placeholder="e.g. 1500", required=True)
    qty_in   = discord.ui.TextInput(label="Quantity wanted",            placeholder="1", default="1", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        item_key = self.item_in.value.strip().lower()
        raw_price = self.price_in.value.strip().replace(",", "")
        raw_qty   = (self.qty_in.value.strip() or "1").replace(",", "")

        if item_key not in ITEMS:
            await interaction.response.send_message(
                f"❌ Unknown item `{item_key}`. Check `!inventory` for valid keys.", ephemeral=True
            )
            return
        if not raw_price.isdigit() or not raw_qty.isdigit():
            await interaction.response.send_message("❌ Enter valid numbers.", ephemeral=True)
            return

        price = int(raw_price)
        qty   = max(1, int(raw_qty))
        gid   = interaction.guild_id
        uid   = interaction.user.id
        total = price * qty

        # Check balance
        bal = await get_balance(gid, uid)
        if bal < total:
            await interaction.response.send_message(
                f"❌ You need **{total:,}** coins (price × qty) but have **{bal:,}**.", ephemeral=True
            )
            return

        # Max orders
        open_count = await db.pool.fetchval(
            "SELECT COUNT(*) FROM orders WHERE guild_id=$1 AND buyer_id=$2 AND status='open'", gid, uid
        ) or 0
        if open_count >= ORDER_MAX:
            await interaction.response.send_message(
                f"❌ Max **{ORDER_MAX}** open orders at once. Cancel one first.", ephemeral=True
            )
            return

        # Hold coins
        await add_coins(gid, uid, -total)

        item_info = ITEMS[item_key]
        row = await db.pool.fetchrow("""
            INSERT INTO orders (guild_id, buyer_id, item_key, item_name, max_price, quantity)
            VALUES ($1,$2,$3,$4,$5,$6) RETURNING id
        """, gid, uid, item_key, item_info["name"], price, qty)

        await interaction.response.send_message(
            f"✅ Order `#{row['id']}` posted — **{item_info['name']} ×{qty}** for up to **{price:,}** coins each.\n"
            f"**{total:,}** coins held in escrow.",
            ephemeral=True
        )

        # Refresh the order board
        interaction.client.dispatch("orders_refresh", interaction.guild)


class OrdersView(discord.ui.View):
    def __init__(self, pages, current: int, author_id: int):
        super().__init__(timeout=180)
        self._pages   = pages
        self._current = current
        self._author  = author_id
        self._update()

    def _update(self):
        self.prev_btn.disabled = self._current == 0
        self.next_btn.disabled = self._current == len(self._pages) - 1

    def _build(self, idx, guild) -> discord.Embed:
        e = discord.Embed(
            title=f"📋 Open Buy Orders ({sum(len(p) for p in self._pages)} total)",
            color=C_ECONOMY,
        )
        for r in self._pages[idx]:
            buyer = guild.get_member(r["buyer_id"])
            bname = buyer.display_name if buyer else f"<@{r['buyer_id']}>"
            star  = RARITY_STAR.get(ITEMS.get(r["item_key"], {}).get("rarity", "common"), "⬜")
            e.add_field(
                name=f"#{r['id']} {star} {r['item_name']} ×{r['quantity']}",
                value=f"💰 Up to **{r['max_price']:,}** coins each · Buyer: {bname}",
                inline=False,
            )
        e.set_footer(text=f"Page {idx+1}/{len(self._pages)} · Use 'Fulfil' to sell · 'Post Order' to buy")
        return e

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary, custom_id="ord_prev")
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._current -= 1; self._update()
        await interaction.response.edit_message(embed=self._build(self._current, interaction.guild), view=self)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary, custom_id="ord_next")
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._current += 1; self._update()
        await interaction.response.edit_message(embed=self._build(self._current, interaction.guild), view=self)

    @discord.ui.button(label="📦 Fulfil an Order", style=discord.ButtonStyle.success, custom_id="ord_fulfil")
    async def fulfil_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Seller picks an order to fulfil."""
        rows = await db.pool.fetch(
            "SELECT * FROM orders WHERE guild_id=$1 AND status='open' AND buyer_id!=$2 ORDER BY max_price DESC LIMIT 25",
            interaction.guild_id, interaction.user.id
        )
        if not rows:
            await interaction.response.send_message("No open orders to fulfil right now.", ephemeral=True)
            return

        options = []
        for r in rows:
            star = RARITY_STAR.get(ITEMS.get(r["item_key"], {}).get("rarity", "common"), "⬜")
            options.append(discord.SelectOption(
                label=f"#{r['id']} {r['item_name']} ×{r['quantity']}",
                value=str(r["id"]),
                description=f"Up to {r['max_price']:,} coins each",
                emoji=star,
            ))

        class FulfilSelect(discord.ui.Select):
            def __init__(self_):
                super().__init__(placeholder="Select an order to fulfil...", options=options)

            async def callback(self_, inter: discord.Interaction):
                order_id = int(self_.values[0])
                uid      = inter.user.id
                gid      = inter.guild_id
                order    = await db.pool.fetchrow("SELECT * FROM orders WHERE id=$1 AND status='open'", order_id)
                if not order:
                    await inter.response.send_message("❌ Order already gone.", ephemeral=True)
                    return
                owned = await get_quantity(gid, uid, order["item_key"])
                if owned < order["quantity"]:
                    await inter.response.send_message(
                        f"❌ You have ×{owned} but order needs ×{order['quantity']}.", ephemeral=True
                    )
                    return

                # Remove items from seller, send payment
                await remove_item(gid, uid, order["item_key"], order["quantity"])
                total_payment = order["max_price"] * order["quantity"]
                await add_coins(gid, uid, total_payment)

                # Give items to buyer
                await add_item(gid, order["buyer_id"], order["item_key"], order["quantity"])

                # Mark order filled
                await db.pool.execute(
                    "UPDATE orders SET status='filled', filled_by=$1 WHERE id=$2",
                    uid, order_id
                )

                # Notify buyer
                buyer = inter.guild.get_member(order["buyer_id"])
                if buyer:
                    try:
                        await buyer.send(embed=discord.Embed(
                            description=(
                                f"📦 Your order `#{order_id}` for **{order['item_name']} ×{order['quantity']}** "
                                f"has been filled! Check `!inventory`."
                            ),
                            color=C_SUCCESS
                        ))
                    except Exception:
                        pass

                for item in self.view.children: item.disabled = True
                await inter.response.edit_message(view=self.view)
                await inter.followup.send(
                    f"✅ Order `#{order_id}` filled! You received **{total_payment:,}** coins.",
                    ephemeral=True
                )
                inter.client.dispatch("orders_refresh", inter.guild)

        class FulfilView(discord.ui.View):
            def __init__(self_): super().__init__(timeout=60); self_.add_item(FulfilSelect())

        await interaction.response.send_message("Which order do you want to fulfil?", view=FulfilView(), ephemeral=True)

    @discord.ui.button(label="📝 Post Buy Order", style=discord.ButtonStyle.primary, custom_id="ord_post")
    async def post_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CreateOrderModal())

    @discord.ui.button(label="❌ Cancel My Order", style=discord.ButtonStyle.danger, custom_id="ord_cancel")
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        gid = interaction.guild_id
        rows = await db.pool.fetch(
            "SELECT * FROM orders WHERE guild_id=$1 AND buyer_id=$2 AND status='open'", gid, uid
        )
        if not rows:
            await interaction.response.send_message("You have no open orders.", ephemeral=True)
            return

        options = [
            discord.SelectOption(
                label=f"#{r['id']} {r['item_name']} ×{r['quantity']}",
                value=str(r["id"]),
                description=f"Refund: {r['max_price']*r['quantity']:,} coins",
            )
            for r in rows[:25]
        ]

        class CancelSelect(discord.ui.Select):
            def __init__(self_): super().__init__(placeholder="Select order to cancel...", options=options)

            async def callback(self_, inter: discord.Interaction):
                order_id = int(self_.values[0])
                order = await db.pool.fetchrow("SELECT * FROM orders WHERE id=$1 AND buyer_id=$2 AND status='open'", order_id, inter.user.id)
                if not order:
                    await inter.response.send_message("❌ Not found.", ephemeral=True); return
                refund = order["max_price"] * order["quantity"]
                await add_coins(gid, inter.user.id, refund)
                await db.pool.execute("UPDATE orders SET status='cancelled' WHERE id=$1", order_id)
                for item in self.view.children: item.disabled = True
                await inter.response.edit_message(view=self.view)
                await inter.followup.send(
                    f"✅ Order `#{order_id}` cancelled — **{refund:,}** coins refunded.", ephemeral=True
                )
                inter.client.dispatch("orders_refresh", inter.guild)

        class CancelView(discord.ui.View):
            def __init__(self_): super().__init__(timeout=60); self_.add_item(CancelSelect())

        await interaction.response.send_message("Which order to cancel?", view=CancelView(), ephemeral=True)


class OrdersCog(commands.Cog, name="Orders"):
    def __init__(self, bot): self.bot = bot

    @commands.command(aliases=["orders", "orderboard"])
    async def orderbook(self, ctx):
        """Show the open buy-order board."""
        if not await check_channel(ctx, "market"): return
        await self._show_orders(ctx)

    async def _show_orders(self, ctx_or_channel, guild=None):
        if guild is None:
            guild = ctx_or_channel.guild
        gid = guild.id
        rows = await db.pool.fetch(
            "SELECT * FROM orders WHERE guild_id=$1 AND status='open' ORDER BY max_price DESC",
            gid
        )
        pages = [rows[i:i+PAGE_SIZE] for i in range(0, max(len(rows),1), PAGE_SIZE)] if rows else [[]]
        e = discord.Embed(
            title="📋 Open Buy Orders",
            description="No open orders yet. Press **Post Buy Order** to request an item!" if not rows else "",
            color=C_ECONOMY
        )
        if rows:
            for r in pages[0]:
                star = RARITY_STAR.get(ITEMS.get(r["item_key"], {}).get("rarity", "common"), "⬜")
                buyer = guild.get_member(r["buyer_id"])
                bname = buyer.display_name if buyer else f"<@{r['buyer_id']}>"
                e.add_field(
                    name=f"#{r['id']} {star} {r['item_name']} ×{r['quantity']}",
                    value=f"💰 Up to **{r['max_price']:,}** coins each · {bname}",
                    inline=False,
                )
        e.set_footer(text=f"Page 1/{len(pages)} · Coins are held in escrow until filled or cancelled")

        aid = ctx_or_channel.author.id if hasattr(ctx_or_channel, "author") else 0
        view = OrdersView(pages, 0, aid)
        if hasattr(ctx_or_channel, "send"):
            await ctx_or_channel.send(embed=e, view=view)

    @commands.Cog.listener()
    async def on_orders_refresh(self, guild):
        """Called after any order state change — nothing to do here unless we keep a persistent message."""
        pass
