# airi/auction_house.py
# Full button-driven AH. AH channel stays clean — only listing embeds.
# Bids/confirmations are ephemeral. Only completed sales post to txn channel.
import discord
from discord.ext import commands
from datetime import datetime, timedelta, timezone, timezone
import asyncio
import db
from utils import _err, C_GACHA, C_ECONOMY, C_WARN, C_SUCCESS, C_ERROR
from airi.guild_config import check_channel, get_market_channel, get_txn_channel
from airi.economy import add_coins, get_balance
from airi.inventory import ITEMS, RARITY_STAR, add_item, remove_item, get_quantity, get_inventory

AH_FEE       = 0.05
AH_EXPIRE_H  = 48
AH_MAX_SLOTS = 5
PAGE_SIZE    = 6   # listings per page

def _utc_naive(ts):
    if ts is None:
        return None
    if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
        return ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts


async def _count_active(guild_id, user_id):
    return await db.pool.fetchval(
        "SELECT COUNT(*) FROM auction_house WHERE guild_id=$1 AND seller_id=$2 AND status='active'",
        guild_id, user_id
    ) or 0


def _listing_embed(row, guild) -> discord.Embed:
    """Build the canonical embed for one active listing."""
    star   = RARITY_STAR.get(row["rarity"], "⬜")
    seller = guild.get_member(row["seller_id"])
    sname  = seller.display_name if seller else f"ID {row['seller_id']}"
    expires_txt = ""
    if row["expires_at"]:
        h = max(0, int((row["expires_at"] - datetime.now(timezone.utc)).total_seconds() // 3600))
        expires_txt = f"\n⏰ Expires in **{h}h**"

    has_bid = row.get("min_bid") is not None
    if has_bid:
        cur  = row.get("current_bid") or row["min_bid"]
        bidder_id = row.get("current_bidder_id")
        bidder = guild.get_member(bidder_id) if bidder_id else None
        bid_line = (
            f"\n💸 **Current bid:** {cur:,} coins"
            + (f" by {bidder.display_name}" if bidder else "")
        )
        price_line = f"💰 **Buyout:** {row['price']:,} coins"
    else:
        bid_line   = ""
        price_line = f"💰 **Price:** {row['price']:,} coins"

    e = discord.Embed(
        title=f"🏪 #{row['id']} — {star} {row['item_name']} ×{row['quantity']}",
        description=(
            f"{price_line}{bid_line}\n"
            f"👤 Seller: **{sname}**{expires_txt}"
        ),
        color=C_GACHA,
    )
    e.set_footer(text=f"ID #{row['id']} · Rarity: {row['rarity'].title()}")
    return e


# ── Bid Modal ─────────────────────────────────────────────────────
class BidModal(discord.ui.Modal, title="Place a Bid"):
    amount_input = discord.ui.TextInput(
        label="Your bid amount (coins)",
        placeholder="Must be higher than current bid",
        min_length=1,
        max_length=10,
        required=True,
    )

    def __init__(self, listing_id: int, guild_id: int):
        super().__init__()
        self._lid  = listing_id
        self._gid  = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.amount_input.value.strip().replace(",", "")
        if not raw.isdigit():
            await interaction.response.send_message("❌ Enter a valid number.", ephemeral=True)
            return

        amount = int(raw)
        uid    = interaction.user.id
        gid    = self._gid

        async with db.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT * FROM auction_house WHERE id=$1 AND guild_id=$2 AND status='active' FOR UPDATE",
                    self._lid, gid
                )
                if not row:
                    await interaction.response.send_message("❌ Listing no longer active.", ephemeral=True)
                    return
                if row["seller_id"] == uid:
                    await interaction.response.send_message("❌ You can't bid on your own listing.", ephemeral=True)
                    return
                min_next = (row.get("current_bid") or row.get("min_bid") or row["price"]) + 1
                if amount < min_next:
                    await interaction.response.send_message(
                        f"❌ Minimum bid is **{min_next:,} coins**.", ephemeral=True
                    )
                    return
                bal = await conn.fetchval(
                    "SELECT balance FROM economy WHERE guild_id=$1 AND user_id=$2", gid, uid
                ) or 0
                if bal < amount:
                    await interaction.response.send_message(
                        f"❌ You need **{amount:,}** but have **{bal:,}** coins.", ephemeral=True
                    )
                    return

                # Refund previous bidder if any
                prev_bidder = row.get("current_bidder_id")
                prev_bid    = row.get("current_bid") or 0
                if prev_bidder and prev_bid:
                    await conn.execute(
                        "UPDATE economy SET balance=balance+$1 WHERE guild_id=$2 AND user_id=$3",
                        prev_bid, gid, prev_bidder
                    )

                # Hold new bid
                await conn.execute(
                    "UPDATE economy SET balance=balance-$1 WHERE guild_id=$2 AND user_id=$3",
                    amount, gid, uid
                )
                await conn.execute(
                    "UPDATE auction_house SET current_bid=$1, current_bidder_id=$2 WHERE id=$3",
                    amount, uid, self._lid
                )
                await conn.execute(
                    "INSERT INTO ah_bids (listing_id, guild_id, bidder_id, amount) VALUES ($1,$2,$3,$4)",
                    self._lid, gid, uid, amount
                )

        # Update the listing embed in the AH channel
        await _refresh_listing_msg(interaction.client, gid, self._lid, interaction.guild)
        await interaction.response.send_message(
            f"✅ Bid of **{amount:,} coins** placed! You'll be refunded if outbid.", ephemeral=True
        )


# ── Buyout Confirm View ───────────────────────────────────────────
class BuyoutConfirmView(discord.ui.View):
    def __init__(self, listing_id: int, guild_id: int, price: int):
        super().__init__(timeout=60)
        self._lid   = listing_id
        self._gid   = guild_id
        self._price = price

    @discord.ui.button(label="✅ Confirm Buyout", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        gid = self._gid
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(content="Processing...", view=self)
        await _execute_buyout(interaction, gid, self._lid, uid)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(content="Cancelled.", view=self)


# ── Per-listing Action View ───────────────────────────────────────
class ListingActionView(discord.ui.View):
    def __init__(self, listing_id: int, guild_id: int, seller_id: int,
                 has_bidding: bool, price: int, min_bid: int | None):
        super().__init__(timeout=None)  # Persistent
        self._lid         = listing_id
        self._gid         = guild_id
        self._seller_id   = seller_id
        self._has_bidding = has_bidding
        self._price       = price
        self._min_bid     = min_bid
        # Set stable custom_ids so views survive restarts
        self.bid_btn.custom_id = f"ah_bid_{listing_id}"
        self.buyout_btn.custom_id = f"ah_buy_{listing_id}"
        self.stop_btn.custom_id = f"ah_stop_{listing_id}"

    @discord.ui.button(label="💸 Bid", style=discord.ButtonStyle.primary)
    async def bid_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._has_bidding:
            await interaction.response.send_message(
                "This listing is buyout only.", ephemeral=True
            )
            return
        if interaction.user.id == self._seller_id:
            await interaction.response.send_message("You can't bid on your own listing.", ephemeral=True)
            return
        modal = BidModal(self._lid, self._gid)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="💰 Buyout", style=discord.ButtonStyle.success)
    async def buyout_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id == self._seller_id:
            await interaction.response.send_message(
                "Use the 🔨 Stop button to remove your listing.", ephemeral=True
            )
            return
        view = BuyoutConfirmView(self._lid, self._gid, self._price)
        await interaction.response.send_message(
            f"Confirm buyout for **{self._price:,} coins**?",
            view=view, ephemeral=True
        )

    @discord.ui.button(label="🔨 Stop", style=discord.ButtonStyle.danger)
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self._seller_id:
            await interaction.response.send_message(
                "Only the seller can stop a listing.", ephemeral=True
            )
            return
        # Confirm
        class StopConfirmView(discord.ui.View):
            def __init__(self_, lid, gid, sid):
                super().__init__(timeout=30)
                self_._lid = lid; self_._gid = gid; self_._sid = sid

            @discord.ui.button(label="Yes, cancel listing", style=discord.ButtonStyle.danger)
            async def yes(self_, inter, btn):
                for i in self_.children: i.disabled = True
                await inter.response.edit_message(view=self_)
                await _cancel_listing(inter, self_._gid, self_._lid, self_._sid)

            @discord.ui.button(label="Keep listing", style=discord.ButtonStyle.secondary)
            async def no(self_, inter, btn):
                for i in self_.children: i.disabled = True
                await inter.response.edit_message(content="Cancelled.", view=self_)

        view = StopConfirmView(self._lid, self._gid, self._seller_id)
        await interaction.response.send_message("Cancel your listing and get the item back?", view=view, ephemeral=True)


# ── Helpers ───────────────────────────────────────────────────────
async def _refresh_listing_msg(bot, gid: int, lid: int, guild):
    """Reload listing from DB and edit the pinned message."""
    row = await db.pool.fetchrow("SELECT * FROM auction_house WHERE id=$1", lid)
    if not row: return
    ch_id  = row.get("channel_id")
    msg_id = row.get("message_id")
    if not ch_id or not msg_id: return
    ch = bot.get_channel(ch_id)
    if not ch: return
    try:
        msg = await ch.fetch_message(msg_id)
        if row["status"] == "active":
            has_bid = row.get("min_bid") is not None
            view = ListingActionView(lid, gid, row["seller_id"], has_bid,
                                     row["price"], row.get("min_bid"))
            await msg.edit(embed=_listing_embed(row, guild), view=view)
        else:
            status_map = {"sold": "✅ Sold", "expired": "⏰ Expired", "cancelled": "❌ Cancelled"}
            e = _listing_embed(row, guild)
            e.colour = C_WARN
            e.title  = status_map.get(row["status"], row["status"]) + " — " + e.title
            await msg.edit(embed=e, view=None)
    except Exception:
        pass


async def _post_txn(bot, gid: int, guild, row, buyer: discord.Member | None,
                    paid: int, seller_got: int, mode: str):
    """Post a final sale notice to the txn channel."""
    ch_id = await get_txn_channel(gid)
    if not ch_id: return
    ch = bot.get_channel(ch_id)
    if not ch: return
    seller = guild.get_member(row["seller_id"])
    sname  = seller.display_name if seller else f"<@{row['seller_id']}>"
    bname  = buyer.display_name  if buyer  else "Unknown"
    e = discord.Embed(
        title=f"{'🔨 Sold' if mode == 'buyout' else '⚡ Bid Won'} — #{row['id']} {row['item_name']}",
        description=(
            f"**Buyer:** {bname}  |  **Seller:** {sname}\n"
            f"**Paid:** {paid:,} coins  |  **Seller received:** {seller_got:,} coins\n"
            f"*(5% fee deducted)*"
        ),
        color=C_ECONOMY,
        timestamp=datetime.now(timezone.utc),
    )
    await ch.send(embed=e)


async def _execute_buyout(interaction: discord.Interaction, gid: int, lid: int, uid: int):
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM auction_house WHERE id=$1 AND guild_id=$2 AND status='active' FOR UPDATE",
                lid, gid
            )
            if not row:
                await interaction.edit_original_response(content="❌ Listing no longer active.", view=None)
                return
            if row["seller_id"] == uid:
                await interaction.edit_original_response(content="❌ Can't buy your own listing.", view=None)
                return
            bal = await conn.fetchval(
                "SELECT balance FROM economy WHERE guild_id=$1 AND user_id=$2", gid, uid
            ) or 0
            if bal < row["price"]:
                await interaction.edit_original_response(
                    content=f"❌ Need **{row['price']:,}** but have **{bal:,}** coins.", view=None
                )
                return

            fee    = max(1, int(row["price"] * AH_FEE))
            payout = row["price"] - fee

            # Refund outstanding bidder if any
            prev_bidder = row.get("current_bidder_id")
            prev_bid    = row.get("current_bid") or 0
            if prev_bidder and prev_bid and prev_bidder != uid:
                await conn.execute(
                    "UPDATE economy SET balance=balance+$1 WHERE guild_id=$2 AND user_id=$3",
                    prev_bid, gid, prev_bidder
                )

            await conn.execute(
                "UPDATE economy SET balance=balance-$1 WHERE guild_id=$2 AND user_id=$3",
                row["price"], gid, uid
            )
            await conn.execute("""
                INSERT INTO economy (guild_id,user_id,balance) VALUES ($1,$2,$3)
                ON CONFLICT (guild_id,user_id) DO UPDATE SET balance=economy.balance+$3
            """, gid, row["seller_id"], payout)
            await conn.execute("UPDATE auction_house SET status='sold' WHERE id=$1", lid)

    await add_item(gid, uid, row["item_key"], row["quantity"])
    buyer = interaction.guild.get_member(uid)
    seller_m = interaction.guild.get_member(row["seller_id"])
    from utils import log_txn
    await log_txn(interaction.client, gid, "AH Buyout", buyer or f"<@{uid}>", seller_m or f"<@{row['seller_id']}>", payout, row["item_name"])
    await interaction.edit_original_response(
        content=(
            f"✅ Bought **{row['item_name']} ×{row['quantity']}** for **{row['price']:,} coins**!\n"
            f"Check `!inventory` to use it."
        ),
        view=None
    )
    # Notify seller
    seller = interaction.guild.get_member(row["seller_id"])
    if seller:
        try:
            await seller.send(embed=discord.Embed(
                description=f"💰 Your listing **{row['item_name']}** sold for **{payout:,} coins** (after fee).",
                color=C_ECONOMY
            ))
        except Exception:
            pass

    await _refresh_listing_msg(interaction.client, gid, lid, interaction.guild)
    await _post_txn(interaction.client, gid, interaction.guild, row, buyer,
                    row["price"], payout, "buyout")


async def _cancel_listing(interaction: discord.Interaction, gid: int, lid: int, uid: int):
    row = await db.pool.fetchrow(
        "SELECT * FROM auction_house WHERE id=$1 AND guild_id=$2 AND seller_id=$3 AND status='active'",
        lid, gid, uid
    )
    if not row:
        await interaction.followup.send("❌ Listing not found or already closed.", ephemeral=True)
        return

    # Refund bidder if any
    prev_bidder = row.get("current_bidder_id")
    prev_bid    = row.get("current_bid") or 0
    if prev_bidder and prev_bid:
        await db.pool.execute(
            "UPDATE economy SET balance=balance+$1 WHERE guild_id=$2 AND user_id=$3",
            prev_bid, gid, prev_bidder
        )

    await db.pool.execute("UPDATE auction_house SET status='cancelled' WHERE id=$1", lid)
    await add_item(gid, uid, row["item_key"], row["quantity"])
    await interaction.followup.send(
        f"✅ Listing `#{lid}` cancelled — **{row['item_name']}** returned to inventory.",
        ephemeral=True
    )
    await _refresh_listing_msg(interaction.client, gid, lid, interaction.guild)


# ── Sell flow (called from inventory button or !ah sell) ──────────
async def _do_sell(interaction: discord.Interaction, gid: int, uid: int, item_key: str, price: int, quantity: int, min_bid: int | None):
    """Actual sell logic – works from any channel, posts to market channel."""
    try:
        from airi.guild_config import get_market_channel
        from airi.inventory import get_quantity, remove_item, ITEMS
        from datetime import datetime, timedelta, timezone

        market_ch_id = await get_market_channel(gid)
        target_ch = interaction.client.get_channel(market_ch_id) if market_ch_id else interaction.channel
        if not target_ch:
            target_ch = interaction.channel

        owned = await get_quantity(gid, uid, item_key)
        if owned < quantity:
            return await interaction.response.send_message(f"❌ You only have ×{owned}.", ephemeral=True)
        if await _count_active(gid, uid) >= AH_MAX_SLOTS:
            return await interaction.response.send_message(f"❌ Max {AH_MAX_SLOTS} active listings.", ephemeral=True)
        ok = await remove_item(gid, uid, item_key, quantity)
        if not ok:
            return await interaction.response.send_message("❌ Failed to remove item.", ephemeral=True)

        expires = _utc_naive(datetime.now(timezone.utc) + timedelta(hours=AH_EXPIRE_H))
        item_info = ITEMS[item_key]
        row = await db.pool.fetchrow("""
            INSERT INTO auction_house
                (guild_id, seller_id, item_key, item_name, rarity, quantity, price, min_bid, expires_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            RETURNING id
        """, gid, uid, item_key, item_info["name"], item_info["rarity"], quantity, price, min_bid, expires)
        lid = row["id"]
        full_row = await db.pool.fetchrow("SELECT * FROM auction_house WHERE id=$1", lid)
        has_bid = min_bid is not None
        view = ListingActionView(lid, gid, uid, has_bid, price, min_bid)
        e = _listing_embed(full_row, interaction.guild)
        msg = await target_ch.send(embed=e, view=view)
        # Register the view persistently so it survives restarts
        interaction.client.add_view(view, message_id=msg.id)
        await db.pool.execute(
            "UPDATE auction_house SET listing_message_id=$1, listing_channel_id=$2 WHERE id=$3",
            msg.id, target_ch.id, lid
        )
        await interaction.response.send_message(
            f"✅ Listed **{item_info['name']} ×{quantity}** for **{price:,}** coins in {target_ch.mention}!",
            ephemeral=True
        )
    except Exception as e:
        print(f"_do_sell error: {e}")
        await interaction.response.send_message(f"❌ Failed to list item: {str(e)[:200]}", ephemeral=True)


# ── Interactive Sell Select (for !ah sell) ────────────────────────
class SellItemSelect(discord.ui.Select):
    def __init__(self, tradable_items: list[dict], guild_id: int, user_id: int, bot):
        self._gid = guild_id
        self._uid = user_id
        self._bot = bot
        options = [
            discord.SelectOption(
                label=f"{it['name']} ×{it['qty']}",
                value=it["key"],
                description=f"Rarity: {it['rarity'].title()}",
                emoji=RARITY_STAR.get(it['rarity'], '⬜')
            )
            for it in tradable_items[:25]
        ]
        super().__init__(placeholder="Select an item to sell...", options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self._uid:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        selected_key = self.values[0]
        item_info = ITEMS[selected_key]

        class SellModal(discord.ui.Modal, title=f"Sell {item_info['name']}"):
            price_in = discord.ui.TextInput(label="Buyout price (coins)", placeholder="e.g. 2000", required=True)
            qty_in = discord.ui.TextInput(label="Quantity", placeholder="1", default="1", required=False)
            bid_in = discord.ui.TextInput(label="Starting bid (optional)", placeholder="Leave blank for buyout only", required=False)

            async def on_submit(self_, inter2):
                raw_price = self_.price_in.value.strip().replace(",","")
                raw_qty = (self_.qty_in.value.strip() or "1").replace(",","")
                raw_bid = self_.bid_in.value.strip().replace(",","")
                if not raw_price.isdigit() or not raw_qty.isdigit():
                    return await inter2.response.send_message("❌ Invalid numbers.", ephemeral=True)
                price_val = int(raw_price)
                qty_val = int(raw_qty)
                bid_val = int(raw_bid) if raw_bid.isdigit() else None
                if bid_val and bid_val >= price_val:
                    return await inter2.response.send_message("❌ Starting bid must be less than buyout.", ephemeral=True)
                await _do_sell(inter2, self._gid, self._uid, selected_key, price_val, qty_val, bid_val)

        await interaction.response.send_modal(SellModal())


# ── Cog ───────────────────────────────────────────────────────────
class AuctionHouseCog(commands.Cog, name="AuctionHouse"):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        """Re-attach persistent views to all active listings after bot is ready."""
        async def restore():
            await self.bot.wait_until_ready()
            rows = await db.pool.fetch("""
                SELECT id, guild_id, seller_id, item_key, item_name, quantity,
                       rarity, price, min_bid, current_bid, current_bidder_id, status,
                       expires_at,
                       COALESCE(channel_id, listing_channel_id)   AS channel_id,
                       COALESCE(message_id, listing_message_id)   AS message_id
                FROM auction_house
                WHERE status='active'
                  AND (listing_message_id IS NOT NULL OR message_id IS NOT NULL)
            """)
            count = 0
            for row in rows:
                guild = self.bot.get_guild(row["guild_id"])
                if not guild:
                    continue
                ch_id = row["channel_id"] if "channel_id" in row.keys() else row.get("listing_channel_id")
                channel = self.bot.get_channel(ch_id) if ch_id else None
                if not channel:
                    continue
                try:
                    msg_id = row["message_id"] if "message_id" in row.keys() else row.get("listing_message_id")
                    message = await channel.fetch_message(msg_id)
                    has_bid = row.get("min_bid") is not None
                    view = ListingActionView(
                        row["id"], row["guild_id"], row["seller_id"],
                        has_bid, row["price"], row.get("min_bid")
                    )
                    self.bot.add_view(view, message_id=message.id)
                    await message.edit(view=view)
                    count += 1
                except Exception as e:
                    print(f"Failed to restore listing {row['id']}: {e}")
            print(f"Restored {count} auction house listings")
        
        # Schedule the restoration task to run after the bot is ready
        asyncio.create_task(restore())

    @commands.group(name="ah", invoke_without_command=True, aliases=["auctionhouse"])
    async def ah(self, ctx):
        if ctx.invoked_subcommand is not None:
            return
        await self.ah_list(ctx)

    @ah.command(name="list", aliases=["browse"])
    async def ah_list(self, ctx, sort: str = "new"):
        """Browse the AH. Buttons handle all bidding/buying."""
        if not await check_channel(ctx, "market"):
            return
        gid = ctx.guild.id

        # If there's a market channel and we're not in it, redirect
        market_ch_id = await get_market_channel(gid)
        if market_ch_id and ctx.channel.id != market_ch_id:
            market_ch = self.bot.get_channel(market_ch_id)
            if market_ch:
                await _err(ctx, f"Browse the Auction House in {market_ch.mention}.")
                return

        order_map = {
            "price":   "price ASC",
            "priceh":  "price DESC",
            "rarity":  "CASE rarity WHEN 'mythic' THEN 5 WHEN 'legendary' THEN 4 WHEN 'epic' THEN 3 WHEN 'rare' THEN 2 ELSE 1 END DESC",
            "new":     "id DESC",
        }
        order = order_map.get(sort.lower(), "id DESC")

        rows = await db.pool.fetch(
            f"SELECT * FROM auction_house WHERE guild_id=$1 AND status='active' ORDER BY {order} LIMIT 80",
            gid
        )
        if not rows:
            return await ctx.send(embed=discord.Embed(
                title="🏪 Auction House",
                description="No active listings.\nSell gacha items via your `!inventory` → Sell button!",
                color=C_GACHA
            ))

        # Paginated overview embed
        pages = [rows[i:i+PAGE_SIZE] for i in range(0, len(rows), PAGE_SIZE)]
        current = [0]

        def build_overview(idx):
            chunk = pages[idx]
            e = discord.Embed(title=f"🏪 Auction House — {len(rows)} listings", color=C_GACHA)
            for r in chunk:
                star   = RARITY_STAR.get(r["rarity"], "⬜")
                seller = ctx.guild.get_member(r["seller_id"])
                sname  = seller.display_name if seller else f"<@{r['seller_id']}>"
                has_bid = r.get("min_bid") is not None
                bid_txt = " · 💸 Bidding" if has_bid else ""
                e.add_field(
                    name=f"#{r['id']} {star} {r['item_name']} ×{r['quantity']}",
                    value=f"**{r['price']:,}** coins · {sname}{bid_txt}",
                    inline=False,
                )
            e.set_footer(text=f"Page {idx+1}/{len(pages)} · Use listing buttons to bid/buy")
            return e

        class PageView(discord.ui.View):
            def __init__(self_):
                super().__init__(timeout=180)
                self_._update()
            def _update(self_):
                self_.prev.disabled = current[0] == 0
                self_.next.disabled = current[0] == len(pages) - 1
            @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
            async def prev(self_, inter, btn):
                if inter.user.id != ctx.author.id:
                    return await inter.response.send_message("Not for you.", ephemeral=True)
                current[0] -= 1; self_._update()
                await inter.response.edit_message(embed=build_overview(current[0]), view=self_)
            @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
            async def next(self_, inter, btn):
                if inter.user.id != ctx.author.id:
                    return await inter.response.send_message("Not for you.", ephemeral=True)
                current[0] += 1; self_._update()
                await inter.response.edit_message(embed=build_overview(current[0]), view=self_)

        v = PageView() if len(pages) > 1 else None
        await ctx.send(embed=build_overview(0), view=v)

    @ah.command(name="sell")
    async def ah_sell(self, ctx, item_key: str = None, price: int = None, quantity: int = 1, min_bid: int = None):
        """
        List an item for sale.
        If no arguments, shows interactive item selector.
        """
        if not await check_channel(ctx, "market"):
            return
        gid, uid = ctx.guild.id, ctx.author.id

        # If item_key not provided, show dropdown of tradable items
        if item_key is None:
            inv = await get_inventory(gid, uid)
            tradable = [it for it in inv if ITEMS.get(it["key"], {}).get("tradable", False) and it["qty"] > 0]
            if not tradable:
                return await _err(ctx, "You have no tradable items. Get some from `!gacha` or `!shop`!")
            view = discord.ui.View(timeout=120)
            view.add_item(SellItemSelect(tradable, gid, uid, self.bot))
            await ctx.send("Select an item to sell:", view=view)
            return

        # Direct arguments mode
        if price is None:
            return await _err(ctx, "Usage: `!ah sell <item_key> <price> [quantity] [min_bid]` or just `!ah sell` for interactive.")
        # Need a context-like object for direct command; we'll create a fake interaction wrapper or just call _do_sell directly with ctx.
        # But _do_sell expects an interaction, so we'll adapt by sending a followup.
        await _do_sell(ctx, gid, uid, item_key, price, quantity, min_bid)  # ctx will work because we use interaction.client? Actually ctx is not interaction.
        # Better: we'll call a helper that handles both. For simplicity, we'll just use a temporary interaction shim.
        # Since this is a text command, we'll create a wrapper.
        class FakeInteraction:
            def __init__(self, ctx_):
                self.user = ctx_.author
                self.guild = ctx_.guild
                self.channel = ctx_.channel
                self.client = ctx_.bot
                self._ctx = ctx_
                self.response = type('Response', (), {
                    'send_message': lambda _, content, ephemeral=False: ctx_.send(content),
                    'defer': lambda: None
                })()
                self.followup = type('Followup', (), {
                    'send': lambda _, content, ephemeral=False: ctx_.send(content)
                })()
            async def response_send_message(self, content, ephemeral=False):
                await self._ctx.send(content)
        fake_inter = FakeInteraction(ctx)
        await _do_sell(fake_inter, gid, uid, item_key, price, quantity, min_bid)

    @ah.command(name="my")
    async def ah_my(self, ctx):
        """Show your active listings."""
        gid = ctx.guild.id
        uid = ctx.author.id
        rows = await db.pool.fetch(
            "SELECT id, item_name, quantity, price, min_bid, status, expires_at FROM auction_house WHERE guild_id=$1 AND seller_id=$2 AND status='active'",
            gid, uid
        )
        if not rows:
            return await ctx.send(embed=discord.Embed(description="You have no active listings.", color=C_WARN))
        e = discord.Embed(title="Your Active Listings", color=C_GACHA)
        for r in rows:
            e.add_field(
                name=f"#{r['id']} {r['item_name']} ×{r['quantity']}",
                value=f"💰 {r['price']:,} coins | Bidding: {'Yes' if r['min_bid'] else 'No'} | Expires: {discord.utils.format_dt(r['expires_at'], 'R')}",
                inline=False
            )
        await ctx.send(embed=e)

    @ah.command(name="info")
    async def ah_info(self, ctx, listing_id: int):
        """Get info and jump to a listing."""
        gid = ctx.guild.id
        row = await db.pool.fetchrow("SELECT * FROM auction_house WHERE id=$1 AND guild_id=$2", listing_id, gid)
        if not row:
            return await _err(ctx, f"Listing `#{listing_id}` not found.")
        msg_id = row.get("message_id")
        ch_id  = row.get("channel_id")
        if msg_id and ch_id and row["status"] == "active":
            jump = f"https://discord.com/channels/{gid}/{ch_id}/{msg_id}"
            await ctx.send(f"[Jump to listing #{listing_id}]({jump})", delete_after=15)
        else:
            await ctx.send(embed=_listing_embed(row, ctx.guild), delete_after=30)

    async def expire_listings(self):
        """Background task: expire old listings."""
        rows = await db.pool.fetch(
            "SELECT * FROM auction_house WHERE status='active' AND expires_at < NOW()"
        )
        for row in rows:
            prev_bidder = row.get("current_bidder_id")
            prev_bid    = row.get("current_bid") or 0
            if prev_bidder and prev_bid:
                await db.pool.execute(
                    "UPDATE economy SET balance=balance+$1 WHERE guild_id=$2 AND user_id=$3",
                    prev_bid, row["guild_id"], prev_bidder
                )
            await db.pool.execute("UPDATE auction_house SET status='expired' WHERE id=$1", row["id"])
            await add_item(row["guild_id"], row["seller_id"], row["item_key"], row["quantity"])
            guild = self.bot.get_guild(row["guild_id"])
            if guild:
                await _refresh_listing_msg(self.bot, row["guild_id"], row["id"], guild)