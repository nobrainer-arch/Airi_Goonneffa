# airi/inventory.py
import discord
from discord.ext import commands
import asyncio
import db
from utils import _err, C_GACHA
from airi.guild_config import check_channel, get_market_channel

ITEMS: dict[str, dict] = {
    "xp_boost_1h":    {"name": "⚡ XP Boost (1h)",       "rarity": "rare",      "tradable": True},
    "xp_boost_24h":   {"name": "🌟 XP Boost (24h)",      "rarity": "legendary", "tradable": True},
    "daily_x2":       {"name": "💰 Daily x2",             "rarity": "epic",      "tradable": True},
    "shield_7d":      {"name": "🛡️ Claim Shield (7d)",    "rarity": "epic",      "tradable": True},
    "prenup":         {"name": "📜 Prenup Doc",            "rarity": "legendary", "tradable": True},
    "waifu_ticket":   {"name": "🎟️ Waifu Ticket",         "rarity": "legendary", "tradable": True},
    "waifu_ticket_3": {"name": "🎟️ Waifu Ticket ×3",     "rarity": "mythic",    "tradable": True},
    "biz_boost_2h":   {"name": "🏭 Business Boost (2h)",  "rarity": "legendary", "tradable": True},
    "coins_small":    {"name": "💰 Coin Pouch (small)",   "rarity": "common",    "tradable": False},
    "coins_medium":   {"name": "💰 Coin Pouch (medium)",  "rarity": "rare",      "tradable": False},
    "coins_large":    {"name": "💰 Coin Pouch (large)",   "rarity": "epic",      "tradable": False},
    "coins_jackpot":  {"name": "💎 Coin Jackpot",         "rarity": "mythic",    "tradable": False},
}

RARITY_STAR = {
    "common": "⬜", "rare": "🟦", "epic": "🟪",
    "legendary": "🟨", "mythic": "🟥",
}

# ── DB helpers ────────────────────────────────────────────────────

async def add_item(guild_id, user_id, item_key, qty=1):
    await db.pool.execute("""
        INSERT INTO inventory (guild_id, user_id, item_key, quantity)
        VALUES ($1,$2,$3,$4)
        ON CONFLICT (guild_id,user_id,item_key)
        DO UPDATE SET quantity=inventory.quantity+$4
    """, guild_id, user_id, item_key, qty)

async def remove_item(guild_id, user_id, item_key, qty=1) -> bool:
    async with db.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT quantity FROM inventory WHERE guild_id=$1 AND user_id=$2 AND item_key=$3",
            guild_id, user_id, item_key
        )
        if not row or row["quantity"] < qty: return False
        if row["quantity"] == qty:
            await conn.execute("DELETE FROM inventory WHERE guild_id=$1 AND user_id=$2 AND item_key=$3", guild_id, user_id, item_key)
        else:
            await conn.execute("UPDATE inventory SET quantity=quantity-$1 WHERE guild_id=$2 AND user_id=$3 AND item_key=$4", qty, guild_id, user_id, item_key)
    return True

async def get_quantity(guild_id, user_id, item_key) -> int:
    row = await db.pool.fetchrow("SELECT quantity FROM inventory WHERE guild_id=$1 AND user_id=$2 AND item_key=$3", guild_id, user_id, item_key)
    return row["quantity"] if row else 0

async def get_inventory(guild_id, user_id) -> list[dict]:
    rows = await db.pool.fetch("SELECT item_key,quantity FROM inventory WHERE guild_id=$1 AND user_id=$2 ORDER BY item_key", guild_id, user_id)
    result = []
    for r in rows:
        info = ITEMS.get(r["item_key"], {"name": r["item_key"], "rarity": "common", "tradable": False})
        result.append({"key": r["item_key"], "qty": r["quantity"], **info})
    return result


# ── Embed builders ─────────────────────────────────────────────────

def _build_inv_embed(chunk: list[dict], target: discord.Member, page: int, total_pages: int) -> discord.Embed:
    tname = target.display_name if target else f"<@{target.id if target else 'Unknown'}>"
    e = discord.Embed(
        title=f"📦 {tname}'s Inventory",
        description="\n".join([
            f"{RARITY_STAR.get(it['rarity'], '⬜')} **{it['name']}** ×{it['qty']}"
            for it in chunk
        ]) or "No items on this page.",
        color=C_GACHA
    )
    if total_pages > 1:
        e.set_footer(text=f"Page {page + 1}/{total_pages}")
    return e

# ── Item action buttons ────────────────────────────────────────────

async def _use_item(interaction: discord.Interaction, gid: int, uid: int, item_key: str):
    """Apply an item's effect. Called from Use button."""
    info = ITEMS.get(item_key)
    if not info:
        await interaction.followup.send("❌ Unknown item.", ephemeral=True)
        return

    ok = await remove_item(gid, uid, item_key, 1)
    if not ok:
        await interaction.followup.send("❌ Item not in inventory (or qty 0).", ephemeral=True)
        return

    from datetime import datetime, timedelta
    key = item_key

    if key == "xp_boost_1h":
        until = datetime.utcnow() + timedelta(hours=1)
        await db.pool.execute("UPDATE economy SET xp_boost_until=$1 WHERE guild_id=$2 AND user_id=$3", until, gid, uid)
        msg = "⚡ XP Boost active for **1 hour**!"
    elif key == "xp_boost_24h":
        until = datetime.utcnow() + timedelta(hours=24)
        await db.pool.execute("UPDATE economy SET xp_boost_until=$1 WHERE guild_id=$2 AND user_id=$3", until, gid, uid)
        msg = "🌟 XP Boost active for **24 hours**!"
    elif key == "daily_x2":
        await db.pool.execute("UPDATE economy SET daily_boost=TRUE WHERE guild_id=$1 AND user_id=$2", gid, uid)
        msg = "💰 Next `!daily` will be **doubled**!"
    elif key == "shield_7d":
        until = datetime.utcnow() + timedelta(days=7)
        await db.pool.execute("""
            INSERT INTO protection (guild_id,user_id,expires_at) VALUES ($1,$2,$3)
            ON CONFLICT (guild_id,user_id) DO UPDATE SET expires_at=$3
        """, gid, uid, until)
        msg = "🛡️ **Claim Shield** active for **7 days**!"
    elif key == "prenup":
        await db.pool.execute("""
            UPDATE economy SET
                titles=CASE WHEN titles IS NULL THEN ARRAY['prenup']::TEXT[]
                            ELSE ARRAY_APPEND(titles,'prenup') END
            WHERE guild_id=$1 AND user_id=$2
        """, gid, uid)
        msg = "📜 **Prenup Doc** added. Attach it when proposing marriage."
    elif key in ("waifu_ticket", "waifu_ticket_3"):
        qty_bonus = 3 if key == "waifu_ticket_3" else 1
        msg = f"🎟️ Waifu Ticket ×{qty_bonus} — *claim feature coming soon!*"
    elif key == "biz_boost_2h":
        msg = "🏭 **Business Boost** applied for 2 hours!"
    elif key.startswith("coins_"):
        import random
        ranges = {"coins_small": (100,300), "coins_medium": (200,500), "coins_large": (500,1500), "coins_jackpot": (5000,20000)}
        lo, hi = ranges.get(key, (100,300))
        gained = random.randint(lo, hi)
        from airi.economy import add_coins
        await add_coins(gid, uid, gained)
        msg = f"💰 Opened and received **{gained:,} coins**!"
    else:
        msg = f"✅ Used **{info['name']}**!"

    await interaction.followup.send(msg, ephemeral=True)


class InventoryView(discord.ui.View):
    """Per-item action buttons: Use | List in AH | Sell (quick)."""

    def __init__(self, items: list[dict], gid: int, uid: int, page: int, total_pages: int, bot):
        super().__init__(timeout=180)
        self._gid   = gid
        self._uid   = uid
        self._page  = page
        self._total = total_pages
        self._bot   = bot
        self._items = items

        # Item dropdown
        if items:
            sel = discord.ui.Select(
                placeholder="Select an item to act on...",
                options=[
                    discord.SelectOption(
                        label=f"{it['name'][:50]} ×{it['qty']}",
                        value=it["key"],
                        description=f"{it['rarity'].title()} · {'Tradable' if it['tradable'] else 'Not tradable'}",
                        emoji=RARITY_STAR.get(it["rarity"], "⬜"),
                    )
                    for it in items[:25]
                ]
            )
            sel.callback = self._item_selected
            self.add_item(sel)

        # Page buttons
        if total_pages > 1:
            prev = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.secondary, custom_id="inv_prev", disabled=(page == 0))
            next_ = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary, custom_id="inv_next", disabled=(page == total_pages - 1))
            prev.callback = self._prev
            next_.callback = self._next
            self.add_item(prev)
            self.add_item(next_)

    async def _item_selected(self, interaction: discord.Interaction):
        if interaction.user.id != self._uid:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        key  = interaction.data["values"][0]
        info = ITEMS.get(key, {})
        tradable = info.get("tradable", False)

        class ItemActionView(discord.ui.View):
            def __init__(self_, key_, tradable_):
                super().__init__(timeout=60)
                self_._key = key_
                self_._tradable = tradable_

            @discord.ui.button(label="▶️ Use", style=discord.ButtonStyle.success)
            async def use_btn(self_, inter, btn):
                if inter.user.id != self._uid:
                    return await inter.response.send_message("Not for you.", ephemeral=True)
                for item in self_.children: item.disabled = True
                await inter.response.defer(ephemeral=True)
                await _use_item(inter, self._gid, self._uid, self_._key)

            @discord.ui.button(label="🏪 List in AH", style=discord.ButtonStyle.primary)
            async def list_btn(self_, inter, btn):
                if inter.user.id != self._uid:
                    return await inter.response.send_message("Not for you.", ephemeral=True)
                if not self_._tradable:
                    await inter.response.send_message("❌ This item cannot be traded.", ephemeral=True)
                    return
                # Open a modal to get price / qty / optional min_bid
                class ListModal(discord.ui.Modal, title="List in Auction House"):
                    price_in = discord.ui.TextInput(label="Buyout price (coins)", placeholder="e.g. 2000", required=True)
                    qty_in   = discord.ui.TextInput(label="Quantity", placeholder="1", default="1", required=False)
                    bid_in   = discord.ui.TextInput(label="Start bid (blank = buyout only)", placeholder="e.g. 500", required=False)

                    async def on_submit(self__, inter2):
                        raw_price = self__.price_in.value.strip().replace(",","")
                        raw_qty   = self__.qty_in.value.strip() or "1"
                        raw_bid   = self__.bid_in.value.strip().replace(",","")
                        if not raw_price.isdigit() or not raw_qty.isdigit():
                            await inter2.response.send_message("❌ Enter valid numbers.", ephemeral=True)
                            return
                        price = int(raw_price); qty = int(raw_qty)
                        min_bid = int(raw_bid) if raw_bid.isdigit() else None
                        if min_bid and min_bid >= price:
                            await inter2.response.send_message("❌ Bid start must be less than buyout.", ephemeral=True)
                            return
                        # Check market channel and redirect
                        gid_ = self._gid
                        uid_ = self._uid
                        market_ch_id = await get_market_channel(gid_)
                        target_ch = self._bot.get_channel(market_ch_id) if market_ch_id else inter2.channel
                        if target_ch and target_ch.id != inter2.channel_id:
                            await inter2.response.send_message(
                                f"⚠️ Listings go in {target_ch.mention}. Head there and your item was NOT listed yet.",
                                ephemeral=True
                            )
                            return
                        # Check owned qty
                        owned = await get_quantity(gid_, uid_, self_._key)
                        if owned < qty:
                            await inter2.response.send_message(f"❌ You only have ×{owned}.", ephemeral=True)
                            return
                        # Remove from inventory, post listing
                        from airi.auction_house import _listing_embed, ListingActionView
                        item_info = ITEMS[self_._key]
                        from datetime import datetime, timedelta
                        await remove_item(gid_, uid_, self_._key, qty)
                        expires = datetime.utcnow() + timedelta(hours=48)
                        row = await db.pool.fetchrow("""
                            INSERT INTO auction_house
                                (guild_id,seller_id,item_key,item_name,rarity,quantity,price,min_bid,expires_at)
                            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id
                        """, gid_, uid_, self_._key, item_info["name"], item_info["rarity"], qty, price, min_bid, expires)
                        lid     = row["id"]
                        full_row = await db.pool.fetchrow("SELECT * FROM auction_house WHERE id=$1", lid)
                        has_bid  = min_bid is not None
                        av       = ListingActionView(lid, gid_, uid_, has_bid, price, min_bid)
                        e        = _listing_embed(full_row, inter2.guild)
                        msg      = await target_ch.send(embed=e, view=av)
                        await db.pool.execute(
                            "UPDATE auction_house SET listing_message_id=$1,listing_channel_id=$2 WHERE id=$3",
                            msg.id, target_ch.id, lid
                        )
                        await inter2.response.send_message(
                            f"✅ Listed **{item_info['name']} ×{qty}** for **{price:,}** coins in {target_ch.mention}!",
                            ephemeral=True
                        )

                await inter.response.send_modal(ListModal())

        name = info.get("name", key)
        qty  = await get_quantity(self._gid, self._uid, key)
        e = discord.Embed(
            title=f"{RARITY_STAR.get(info.get('rarity','common'),'⬜')} {name} ×{qty}",
            description=f"Rarity: **{info.get('rarity','?').title()}**\nTradable: {'✅' if tradable else '❌'}",
            color=C_GACHA
        )
        await interaction.response.send_message(embed=e, view=ItemActionView(key, tradable), ephemeral=True)

    async def _prev(self, interaction: discord.Interaction):
        if interaction.user.id != self._uid:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        new_page = self._page - 1
        await self._go_page(interaction, new_page)

    async def _next(self, interaction: discord.Interaction):
        if interaction.user.id != self._uid:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        new_page = self._page + 1
        await self._go_page(interaction, new_page)

    async def _go_page(self, interaction: discord.Interaction, new_page: int):
        items  = await get_inventory(self._gid, self._uid)
        pages  = [items[i:i+INV_PAGE_SIZE] for i in range(0, max(len(items),1), INV_PAGE_SIZE)]
        new_page = max(0, min(new_page, len(pages)-1))
        chunk  = pages[new_page]
        target = interaction.guild.get_member(self._uid)
        e      = _build_inv_embed(chunk, target, new_page, len(pages))
        view   = InventoryView(chunk, self._gid, self._uid, new_page, len(pages), self._bot)
        # Use edit_message — this is a button callback on the same message
        await interaction.response.edit_message(embed=e, view=view)


INV_PAGE_SIZE = 10


class InventoryCog(commands.Cog, name="Inventory"):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(aliases=["inv", "items"])
    async def inventory(self, ctx, member: discord.Member = None):
        if not await check_channel(ctx, "economy"): return
        target = member or ctx.author
        await self._show_inv(ctx, target.guild.id, target.id, 0, ctx)

    async def _show_inv(self, ctx_or_inter, gid, uid, page, orig_ctx=None):
        items = await get_inventory(gid, uid)
        if not items:
            target = (orig_ctx or ctx_or_inter).guild.get_member(uid)
            name   = target.display_name if target else f"<@{uid}>"
            if hasattr(ctx_or_inter, "send"):
                return await ctx_or_inter.send(embed=discord.Embed(
                    description=("Your" if uid == ctx_or_inter.author.id else name + "'s") + " inventory is empty.",
                    color=C_GACHA
                ))
            return

        pages = [items[i:i+INV_PAGE_SIZE] for i in range(0, len(items), INV_PAGE_SIZE)]
        page  = max(0, min(page, len(pages) - 1))
        chunk = pages[page]

        guild  = (orig_ctx or ctx_or_inter).guild
        target = guild.get_member(uid)
        tname  = target.display_name if target else f"<@{uid}>"

        e = _build_inv_embed(chunk, target, page, len(pages))

        view = InventoryView(chunk, gid, uid, page, len(pages), self.bot)

        if hasattr(ctx_or_inter, "send"):
            await ctx_or_inter.send(embed=e, view=view)
        else:
            # It's an interaction — just edit the original response
            try:
                await ctx_or_inter.edit_original_response(embed=e, view=view)
            except Exception:
                pass

    @commands.command()
    async def use(self, ctx, item_key: str):
        """Directly use an item by key (e.g. !use xp_boost_1h). Prefer using inventory buttons."""
        if not await check_channel(ctx, "economy"): return
        item_key = item_key.lower().strip()
        if item_key not in ITEMS:
            return await _err(ctx, f"Unknown item `{item_key}`. Check `!inventory`.")

        class UseConfirmView(discord.ui.View):
            def __init__(self_):
                super().__init__(timeout=30)

            @discord.ui.button(label="Use", style=discord.ButtonStyle.success)
            async def use_btn(self_, inter, btn):
                if inter.user.id != ctx.author.id:
                    return await inter.response.send_message("Not for you.", ephemeral=True)
                for item in self_.children: item.disabled = True
                await inter.response.defer(ephemeral=True)
                await _use_item(inter, ctx.guild.id, ctx.author.id, item_key)
                self_.stop()

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel_btn(self_, inter, btn):
                for item in self_.children: item.disabled = True
                await inter.response.edit_message(content="Cancelled.", view=self_)
                self_.stop()

        info = ITEMS[item_key]
        qty  = await get_quantity(ctx.guild.id, ctx.author.id, item_key)
        if qty == 0: return await _err(ctx, f"You don't have **{info['name']}**.")
        e = discord.Embed(
            title=f"Use {info['name']}?",
            description=f"You have ×{qty}. This will consume 1.",
            color=C_GACHA
        )
        await ctx.send(embed=e, view=UseConfirmView())
