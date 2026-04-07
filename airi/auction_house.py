# airi/auction_house.py
# Full button-driven AH. AH channel stays clean — only listing embeds.
# Bids/confirmations are ephemeral. Only completed sales post to txn channel.
import discord
from discord.ext import commands
from datetime import datetime, timedelta
import asyncio
import db
from utils import _err, C_GACHA, C_ECONOMY, C_WARN, C_SUCCESS, C_ERROR
from airi.guild_config import check_channel, get_market_channel, get_txn_channel
from airi.economy import add_coins, get_balance
from airi.inventory import ITEMS, RARITY_STAR, add_item, remove_item, get_quantity

AH_FEE       = 0.05
AH_EXPIRE_H  = 48
AH_MAX_SLOTS = 5
PAGE_SIZE    = 6   # listings per page


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
        h = max(0, int((row["expires_at"] - datetime.utcnow()).total_seconds() // 3600))
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
        super().__init__(timeout=None)
        self._lid         = listing_id
        self._gid         = guild_id
        self._seller_id   = seller_id
        self._has_bidding = has_bidding
        self._price       = price
        self._min_bid     = min_bid
        # Set stable custom_ids per listing so views survive restarts
        for child in self.children:
            if hasattr(child, 'label'):
                if child.label == "💸 Bid":
                    child.custom_id = f"ah_bid_{listing_id}"
                elif child.label == "💰 Buyout":
                    child.custom_id = f"ah_buy_{listing_id}"
                elif child.label == "🔨 Stop":
                    child.custom_id = f"ah_stop_{listing_id}"

    @discord.ui.button(label="💸 Bid", style=discord.ButtonStyle.primary)
    async def bid_button(self, interaction: discord.Interaction, button: discord.ui.Button):
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
    async def buyout_button(self, interaction: discord.Interaction, button: discord.ui.Button):
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
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
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
    ch_id  = row.get("listing_channel_id")
    msg_id = row.get("listing_message_id")
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
        timestamp=datetime.utcnow(),
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


# ── Cog ───────────────────────────────────────────────────────────
class AuctionHouseCog(commands.Cog, name="AuctionHouse"):
    def __init__(self, bot): self.bot = bot

    @commands.group(name="ah", invoke_without_command=True, aliases=["auctionhouse"])
    async def ah(self, ctx):
        if ctx.invoked_subcommand is not None:
            return  # ← CRITICAL: prevent ah_list running before subcommand
        await self.ah_list(ctx)

    @ah.command(name="list", aliases=["browse"])
    async def ah_list(self, ctx, sort: str = "new"):
        """Browse the AH. Buttons handle all bidding/buying."""
        if not await check_channel(ctx, "market"): return
        gid = ctx.guild.id

        # If there's a market channel and we're not in it, redirect
        market_ch_id = await get_market_channel(gid)
        if market_ch_id and ctx.channel.id != market_ch_id:
            market_ch = self.bot.get_channel(market_ch_id)
            if market_ch:
                await _err(ctx, f"Browse the Auction House in {market_ch.mention}.")
                return

        order = {
            "price":  "price ASC",
            "priceh": "price DESC",
            "rarity": "CASE rarity WHEN 'mythic' THEN 5 WHEN 'legendary' THEN 4 WHEN 'epic' THEN 3 WHEN 'rare' THEN 2 ELSE 1 END DESC",
        }.get(sort.lower(), "listed_at DESC")

        rows = await db.pool.fetch(
            f"SELECT * FROM auction_house WHERE guild_id=$1 AND status='active' ORDER BY {order} LIMIT 80",
            gid
        )
        if not rows:
            return await ctx.send(embed=discord.Embed(
                title="🏪 Auction House",
                description="No active listings.\nSell gacha items via your `!inventory` → List button!",
                color=C_GACHA
            ))

        # Paginated overview embed (no action buttons — actions are on the listing posts)
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
                current[0] -= 1; self_._update()
                await inter.response.edit_message(embed=build_overview(current[0]), view=self_)
            @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
            async def next(self_, inter, btn):
                current[0] += 1; self_._update()
                await inter.response.edit_message(embed=build_overview(current[0]), view=self_)

        v = PageView() if len(pages) > 1 else None
        await ctx.send(embed=build_overview(0), view=v)

    @ah.command(name="sell")
    async def ah_sell(self, ctx, item_key: str, price: int,
                      quantity: int = 1, min_bid: int = None):
        """
        List an item for sale.
        !ah sell xp_boost_1h 2000          — fixed price only
        !ah sell xp_boost_1h 2000 1 500    — buyout 2000, bidding starts at 500
        """
        if not await check_channel(ctx, "market"): return
        item_key = item_key.lower().strip()

        if item_key not in ITEMS:
            return await _err(ctx, f"Unknown item `{item_key}`. Check `!inventory`.")
        item_info = ITEMS[item_key]
        if not item_info.get("tradable"):
            return await _err(ctx, f"**{item_info['name']}** cannot be traded.")
        if price < 1 or quantity < 1:
            return await _err(ctx, "Price and quantity must be ≥ 1.")
        if min_bid is not None and min_bid >= price:
            return await _err(ctx, "Min bid must be less than the buyout price.")

        gid, uid = ctx.guild.id, ctx.author.id

        # Redirect if not in AH channel
        market_ch_id = await get_market_channel(gid)
        target_ch = self.bot.get_channel(market_ch_id) if market_ch_id else ctx.channel
        if target_ch != ctx.channel:
            # Check if user already has an active listing of this item
            existing = await db.pool.fetchrow(
                "SELECT id, listing_message_id, listing_channel_id FROM auction_house "
                "WHERE guild_id=$1 AND seller_id=$2 AND item_key=$3 AND status='active'",
                gid, uid, item_key
            )
            if existing:
                msg_id = existing.get("listing_message_id")
                ch_id  = existing.get("listing_channel_id")
                if msg_id and ch_id:
                    jump = f"https://discord.com/channels/{gid}/{ch_id}/{msg_id}"
                    await _err(ctx, f"You already have this item listed! [View listing]({jump})")
                else:
                    await _err(ctx, f"You already have this listed. Head to {target_ch.mention} to manage it.")
                return
            await _err(ctx, f"Listings go in {target_ch.mention}. Run this command there.")
            return

        if await _count_active(gid, uid) >= AH_MAX_SLOTS:
            return await _err(ctx, f"Max **{AH_MAX_SLOTS}** active listings at once.")

        owned = await get_quantity(gid, uid, item_key)
        if owned < quantity:
            return await _err(ctx, f"You only have **{owned}×** {item_info['name']}.")

        await remove_item(gid, uid, item_key, quantity)
        expires = datetime.utcnow() + timedelta(hours=AH_EXPIRE_H)

        row = await db.pool.fetchrow("""
            INSERT INTO auction_house
                (guild_id, seller_id, item_key, item_name, rarity, quantity, price, min_bid, expires_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            RETURNING id
        """, gid, uid, item_key, item_info["name"], item_info["rarity"],
            quantity, price, min_bid, expires)
        lid = row["id"]

        # Re-fetch full row for embed
        full_row = await db.pool.fetchrow("SELECT * FROM auction_house WHERE id=$1", lid)
        has_bid  = min_bid is not None
        view     = ListingActionView(lid, gid, uid, has_bid, price, min_bid)
        e        = _listing_embed(full_row, ctx.guild)
        msg      = await ctx.channel.send(embed=e, view=view)

        # Store message ref so we can edit it later
        await db.pool.execute(
            "UPDATE auction_house SET listing_message_id=$1, listing_channel_id=$2 WHERE id=$3",
            msg.id, ctx.channel.id, lid
        )
        await ctx.message.delete()

    @ah.command(name="info")
    async def ah_info(self, ctx, listing_id: int):
        gid = ctx.guild.id
        row = await db.pool.fetchrow("SELECT * FROM auction_house WHERE id=$1 AND guild_id=$2", listing_id, gid)
        if not row:
            return await _err(ctx, f"Listing `#{listing_id}` not found.")

        # If listing message exists in AH channel, jump there
        msg_id = row.get("listing_message_id")
        ch_id  = row.get("listing_channel_id")
        if msg_id and ch_id and row["status"] == "active":
            jump = f"https://discord.com/channels/{gid}/{ch_id}/{msg_id}"
            await ctx.send(f"[Jump to listing #{listing_id}]({jump})", delete_after=15)
            return

        await ctx.send(embed=_listing_embed(row, ctx.guild), delete_after=30)

    async def expire_listings(self):
        rows = await db.pool.fetch(
            "SELECT * FROM auction_house WHERE status='active' AND expires_at < NOW()"
        )
        for row in rows:
            # Refund bidder
            prev_bidder = row.get("current_bidder_id")
            prev_bid    = row.get("current_bid") or 0
            if prev_bidder and prev_bid:
                await db.pool.execute(
                    "UPDATE economy SET balance=balance+$1 WHERE guild_id=$2 AND user_id=$3",
                    prev_bid, row["guild_id"], prev_bidder
                )
            await db.pool.execute("UPDATE auction_house SET status='expired' WHERE id=$1", row["id"])
            await add_item(row["guild_id"], row["seller_id"], row["item_key"], row["quantity"])
            # Update listing embed
            guild = self.bot.get_guild(row["guild_id"])
            if guild:
                await _refresh_listing_msg(self.bot, row["guild_id"], row["id"], guild)

    async def restore_views(self):
        """Re-attach ListingActionView to all active listings on restart."""
        rows = await db.pool.fetch(
            "SELECT * FROM auction_house WHERE status='active' AND listing_message_id IS NOT NULL"
        )
        for row in rows:
            ch = self.bot.get_channel(row["listing_channel_id"])
            if not ch: continue
            try:
                msg = await ch.fetch_message(row["listing_message_id"])
                has_bid = row.get("min_bid") is not None
                view = ListingActionView(
                    row["id"], row["guild_id"], row["seller_id"],
                    has_bid, row["price"], row.get("min_bid")
                )
                await msg.edit(view=view)
            except Exception:
                pass
