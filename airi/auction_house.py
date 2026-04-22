# airi/auction_house.py — Full Agora-style Auction House
# Single /ah command, UI does everything: list → browse → buy/bid
# Auto-categorizes items. Clean channel — only listing embeds + ephemeral actions.
import discord
from discord.ext import commands
from discord.ext import tasks
from datetime import datetime, timezone, timedelta
import asyncio, random
import db
from utils import _err, C_ECONOMY, C_WARN, C_SUCCESS, C_ERROR
from airi.economy import add_coins, get_balance
from airi.guild_config import get_market_channel, check_channel

AH_FEE       = 0.05      # 5% seller fee
AH_EXPIRE_H  = 48        # listing expires in 48h
AH_MAX_SLOTS = 5         # max active listings per user
PAGE_SIZE    = 5         # listings per browse page

# ── Item categories (auto-detect from item_key prefix) ──────────────
CATEGORIES = {
    "weapon":    ("⚔️ Weapons",     0xe74c3c),
    "armor":     ("🛡️ Armor",       0x95a5a6),
    "potion":    ("🧪 Potions",      0x2ecc71),
    "accessory": ("💍 Accessories",  0x9b59b6),
    "spell":     ("📚 Spells",       0x3498db),
    "material":  ("🪨 Materials",    0x7f8c8d),
    "food":      ("🍖 Food",         0xf39c12),
    "other":     ("📦 Other",        0x34495e),
}

def _auto_category(item_key: str, item_name: str) -> str:
    k = item_key.lower(); n = item_name.lower()
    if any(w in k or w in n for w in ("sword","axe","dagger","bow","gun","staff","wand","spear","blade","weapon")): return "weapon"
    if any(w in k or w in n for w in ("armor","shield","cloak","robe","mail","plate","leather","helm","boots","gloves","chestplate")): return "armor"
    if any(w in k or w in n for w in ("potion","antidote","elixir","tonic","vial","brew","herb")): return "potion"
    if any(w in k or w in n for w in ("ring","amulet","charm","necklace","bracelet","accessory","lucky","luck","pendant")): return "accessory"
    if any(w in k or w in n for w in ("spell","scroll","tome","book","grimoire","magic","fireball","heal","ward")): return "spell"
    if any(w in k or w in n for w in ("ore","stone","crystal","gem","wood","leather","cloth","ingot","shard","fragment","material")): return "material"
    if any(w in k or w in n for w in ("food","meal","bread","fish","meat","mushroom","fruit","berry")): return "food"
    return "other"

RARITY_STAR = {"common":"⬜","uncommon":"🟩","rare":"🟦","epic":"🟣","legendary":"🟠","mythic":"🔴"}
RARITY_COLOR= {"common":0x808080,"uncommon":0x27ae60,"rare":0x2980b9,"epic":0x8e44ad,"legendary":0xf39c12,"mythic":0xe74c3c}


# ── Embed builders ───────────────────────────────────────────────────
def _listing_embed(row: dict, guild: discord.Guild, highlight: str = "") -> discord.Embed:
    cat_key  = row.get("category","other")
    cat_name, cat_color = CATEGORIES.get(cat_key, CATEGORIES["other"])
    star     = RARITY_STAR.get(row.get("rarity","common"),"⬜")
    rarity_c = RARITY_COLOR.get(row.get("rarity","common"),C_ECONOMY)
    seller   = guild.get_member(row["seller_id"])
    sname    = seller.display_name if seller else f"<@{row['seller_id']}>"

    color = rarity_c
    if highlight == "new_bid": color = 0x3498db
    if highlight == "sold":    color = 0x2ecc71
    if highlight == "expired": color = 0x808080

    has_bid  = row.get("min_bid") is not None
    cur_bid  = row.get("current_bid")
    bidder_id= row.get("current_bidder_id")
    bidder   = guild.get_member(bidder_id) if bidder_id else None
    expires  = row.get("expires_at")
    exp_aware= expires.replace(tzinfo=timezone.utc) if expires and not expires.tzinfo else expires

    qty = row.get("quantity",1)

    lines = [
        f"**Seller:** {sname}  ·  **Category:** {cat_name}",
        f"**Rarity:** {star} {row.get('rarity','common').title()}",
    ]
    if has_bid:
        if cur_bid:
            lines.append(f"📈 **Top Bid:** {cur_bid:,} 🪙 by {bidder.display_name if bidder else '?'}")
        else:
            lines.append(f"📈 **Starting Bid:** {row['min_bid']:,} 🪙")
        lines.append(f"💰 **Buyout:** {row['price']:,} 🪙")
    else:
        lines.append(f"💰 **Price:** {row['price']:,} 🪙")
    if exp_aware:
        lines.append(f"⏰ **Expires:** {discord.utils.format_dt(exp_aware,'R')}")
    if row.get("status","active") != "active":
        status_txt = {"sold":"✅ SOLD","expired":"⏰ EXPIRED","cancelled":"❌ CANCELLED"}.get(row["status"],"?")
        lines.append(f"\n**{status_txt}**")

    e = discord.Embed(
        title=f"{star} #{row['id']} — {row['item_name']}" + (f" ×{qty}" if qty>1 else ""),
        description="\n".join(lines),
        color=color,
    )
    e.set_footer(text=f"Listing #{row['id']} · {int(AH_FEE*100)}% tax on sale · /ah for full auction house")
    return e

def _browse_embed(rows: list[dict], guild: discord.Guild, page: int,
                   total: int, category: str = "all") -> discord.Embed:
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    cat_name, cat_color = CATEGORIES.get(category, ("🏛️ All Items", C_ECONOMY))
    if category == "all": cat_name = "🏛️ All Items"

    e = discord.Embed(
        title=f"🏛️ Auction House — {cat_name}",
        description=f"Page **{page+1}**/{total_pages}  ·  {total} active listing(s)\n\u200b",
        color=cat_color if category != "all" else C_ECONOMY,
        timestamp=datetime.now(timezone.utc),
    )
    for r in rows:
        star  = RARITY_STAR.get(r.get("rarity","common"),"⬜")
        seller = guild.get_member(r["seller_id"])
        sname  = seller.display_name if seller else f"<@{r['seller_id']}>"
        has_bid = r.get("min_bid") is not None
        cur_bid = r.get("current_bid")
        exp = r.get("expires_at")
        exp_aware = exp.replace(tzinfo=timezone.utc) if exp and not exp.tzinfo else exp

        price_txt = f"{r['price']:,} 🪙"
        bid_txt   = f"  ·  Top bid: {cur_bid:,}" if cur_bid else (f"  ·  Bid from {r['min_bid']:,}" if has_bid else "")
        qty = r.get("quantity",1)
        cat_icon = CATEGORIES.get(r.get("category","other"),("📦",""))[0]

        e.add_field(
            name=f"{star} {cat_icon} **#{r['id']}** {r['item_name']}" + (f" ×{qty}" if qty>1 else ""),
            value=(
                f"💰 {price_txt}{bid_txt}\n"
                f"Seller: {sname}  ·  Expires {discord.utils.format_dt(exp_aware,'R') if exp_aware else '?'}"
            ),
            inline=False,
        )
    if not rows:
        e.description = "No listings in this category. Use **Sell** to list an item!"
    e.set_footer(text="Use the dropdown to filter by category · ◀▶ to browse")
    return e


# ── Bid Modal ────────────────────────────────────────────────────────
class BidModal(discord.ui.Modal, title="Place a Bid"):
    amount = discord.ui.TextInput(label="Your bid (coins)", required=True)
    def __init__(self, lid: int, gid: int, min_bid: int):
        super().__init__()
        self._lid = lid; self._gid = gid
        self.amount.placeholder = f"Minimum: {min_bid:,}"
    async def on_submit(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True)
        raw = self.amount.value.strip().replace(",","")
        if not raw.isdigit():
            return await inter.followup.send("❌ Enter a valid number.",ephemeral=True)
        await _place_bid(inter, self._gid, self._lid, inter.user.id, int(raw))

class SellModal(discord.ui.Modal, title="List Item for Sale"):
    price_in = discord.ui.TextInput(label="Buyout price (coins)", required=True, placeholder="e.g. 2500")
    qty_in   = discord.ui.TextInput(label="Quantity", required=False, default="1")
    bid_in   = discord.ui.TextInput(label="Starting bid (leave blank = no auction)", required=False)
    def __init__(self, item_key: str, item_name: str, rarity: str, gid: int, uid: int):
        super().__init__()
        self._key=item_key; self._name=item_name; self._rarity=rarity
        self._gid=gid; self._uid=uid
    async def on_submit(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True)
        raw_p = self.price_in.value.strip().replace(",","")
        raw_q = self.qty_in.value.strip() or "1"
        raw_b = self.bid_in.value.strip().replace(",","")
        if not raw_p.isdigit():
            return await inter.followup.send("❌ Invalid price.",ephemeral=True)
        price = int(raw_p); qty = int(raw_q) if raw_q.isdigit() else 1
        min_bid = int(raw_b) if raw_b.isdigit() else None
        if min_bid and min_bid >= price:
            return await inter.followup.send("❌ Starting bid must be less than buyout.",ephemeral=True)
        await _post_listing(inter, self._gid, self._uid, self._key, self._name,
                            self._rarity, price, qty, min_bid)


# ── Listing action view (on each listing embed) ──────────────────────
class ListingActionView(discord.ui.View):
    def __init__(self, lid: int, gid: int, seller_id: int, has_bid: bool, price: int, min_bid: int|None):
        super().__init__(timeout=None)
        self._lid=lid; self._gid=gid; self._seller=seller_id
        self._has_bid=has_bid; self._price=price; self._min_bid=min_bid
        self.buyout_btn.custom_id = f"ah_buy_{lid}"
        self.bid_btn.custom_id    = f"ah_bid_{lid}"
        self.cancel_btn.custom_id = f"ah_cancel_{lid}"
        if not has_bid:
            self.remove_item(self.bid_btn)

    @discord.ui.button(label="💰 Buy Now", style=discord.ButtonStyle.success)
    async def buyout_btn(self, inter: discord.Interaction, btn):
        await inter.response.defer(ephemeral=True)
        await _execute_buyout(inter, self._gid, self._lid, inter.user.id)

    @discord.ui.button(label="📈 Place Bid", style=discord.ButtonStyle.primary)
    async def bid_btn(self, inter: discord.Interaction, btn):
        row = await db.pool.fetchrow("SELECT min_bid, current_bid FROM auction_house WHERE id=$1", self._lid)
        min_next = max(row["min_bid"] or 1, (row["current_bid"] or 0) + 1)
        await inter.response.send_modal(BidModal(self._lid, self._gid, min_next))

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel_btn(self, inter: discord.Interaction, btn):
        if inter.user.id != self._seller:
            return await inter.response.send_message("Only the seller can cancel.", ephemeral=True)
        class CV(discord.ui.View):
            def __init__(cv): super().__init__(timeout=30)
            @discord.ui.button(label="Yes, cancel",style=discord.ButtonStyle.danger)
            async def yes(cv,i2,b):
                await i2.response.defer(ephemeral=True)
                await _cancel_listing(i2, self._gid, self._lid, inter.user.id)
            @discord.ui.button(label="Keep listing",style=discord.ButtonStyle.secondary)
            async def no(cv,i2,b): await i2.response.send_message("OK.",ephemeral=True)
        await inter.response.send_message("Cancel this listing?",view=CV(),ephemeral=True)


# ── Browse view ───────────────────────────────────────────────────────
class BrowseView(discord.ui.View):
    def __init__(self, guild: discord.Guild, rows: list, page: int, total: int, cat: str):
        super().__init__(timeout=300)
        self._guild=guild; self._rows=rows; self._page=page
        self._total=total; self._cat=cat
        self._upd()

    def _upd(self):
        total_pages = max(1,(self._total+PAGE_SIZE-1)//PAGE_SIZE)
        self.prev_btn.disabled = self._page==0
        self.next_btn.disabled = self._page>=total_pages-1

    @discord.ui.button(label="◀",style=discord.ButtonStyle.secondary,row=1)
    async def prev_btn(self, inter, btn):
        await inter.response.defer()
        self._page -= 1
        await self._reload(inter)

    @discord.ui.button(label="▶",style=discord.ButtonStyle.secondary,row=1)
    async def next_btn(self, inter, btn):
        await inter.response.defer()
        self._page += 1
        await self._reload(inter)

    async def _reload(self, inter):
        rows, total = await _fetch_page(inter.guild_id, self._page, self._cat)
        self._rows=rows; self._total=total; self._upd()
        await inter.edit_original_response(embed=_browse_embed(rows,inter.guild,self._page,total,self._cat), view=self)


# ── Main AH Hub View ─────────────────────────────────────────────────
class AHHubView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=300)
        self._ctx = ctx
        self._cat = "all"

    def _home_embed(self) -> discord.Embed:
        return discord.Embed(
            title="🏛️ Auction House",
            description=(
                "**Buy** rare items from other players.\n"
                "**Sell** your items with buyout or open bidding.\n"
                "**Browse** by category to find what you need.\n\n"
                f"• **5% tax** on successful sales\n"
                f"• Listings expire in **48 hours**\n"
                f"• Max **{AH_MAX_SLOTS}** active listings per person\n\n"
                "Use the buttons below to get started."
            ),
            color=C_ECONOMY,
            timestamp=datetime.now(timezone.utc),
        )

    @discord.ui.button(label="🏷️ Browse",    style=discord.ButtonStyle.primary,  row=0)
    async def browse_btn(self, inter: discord.Interaction, btn):
        if inter.user.id != self._ctx.author.id: return await inter.response.send_message("Not for you.",ephemeral=True)
        await inter.response.defer()
        await self._open_browse(inter)

    @discord.ui.button(label="📦 Sell Item", style=discord.ButtonStyle.success,   row=0)
    async def sell_btn(self, inter: discord.Interaction, btn):
        if inter.user.id != self._ctx.author.id: return await inter.response.send_message("Not for you.",ephemeral=True)
        await self._open_sell(inter)

    @discord.ui.button(label="📋 My Listings",style=discord.ButtonStyle.secondary,row=0)
    async def my_btn(self, inter: discord.Interaction, btn):
        if inter.user.id != self._ctx.author.id: return await inter.response.send_message("Not for you.",ephemeral=True)
        await inter.response.defer(ephemeral=True)
        await self._open_my(inter)

    @discord.ui.button(label="🔍 Find #ID",  style=discord.ButtonStyle.secondary, row=0)
    async def find_btn(self, inter: discord.Interaction, btn):
        if inter.user.id != self._ctx.author.id: return await inter.response.send_message("Not for you.",ephemeral=True)
        class FindModal(discord.ui.Modal, title="Find Listing by ID"):
            id_in = discord.ui.TextInput(label="Listing ID", required=True)
            async def on_submit(m, i2):
                await i2.response.defer(ephemeral=True)
                raw = m.id_in.value.strip()
                if not raw.isdigit(): return await i2.followup.send("❌ Enter a number.",ephemeral=True)
                row = await db.pool.fetchrow("SELECT * FROM auction_house WHERE id=$1 AND guild_id=$2", int(raw), inter.guild_id)
                if not row: return await i2.followup.send(f"❌ Listing #{raw} not found.",ephemeral=True)
                r = dict(row)
                has_bid = r.get("min_bid") is not None
                view = ListingActionView(r["id"],inter.guild_id,r["seller_id"],has_bid,r["price"],r.get("min_bid"))
                await i2.followup.send(embed=_listing_embed(r,i2.guild), view=view, ephemeral=True)
        await inter.response.send_modal(FindModal())

    async def _open_browse(self, inter: discord.Interaction):
        # Category filter dropdown
        cat_opts = [discord.SelectOption(label="🏛️ All Items", value="all", default=True)] + [
            discord.SelectOption(label=v[0][:50], value=k) for k,v in CATEGORIES.items()
        ]
        cat_sel = discord.ui.Select(placeholder="🔍 Filter by category…", options=cat_opts, row=0)

        async def cat_cb(i2: discord.Interaction):
            await i2.response.defer()
            cat = cat_sel.values[0]
            rows, total = await _fetch_page(i2.guild_id, 0, cat)
            bv = BrowseView(i2.guild, rows, 0, total, cat)
            # Add category dropdown to browse view
            bv.clear_items()
            for o in cat_sel.options: o.default = (o.value == cat)
            bv.add_item(cat_sel)
            bv.add_item(bv.prev_btn); bv.add_item(bv.next_btn)
            await i2.edit_original_response(
                embed=_browse_embed(rows, i2.guild, 0, total, cat),
                view=bv,
            )
        cat_sel.callback = cat_cb

        rows, total = await _fetch_page(inter.guild_id, 0, "all")
        bv = BrowseView(inter.guild, rows, 0, total, "all")
        bv.clear_items()
        bv.add_item(cat_sel)
        bv.add_item(bv.prev_btn); bv.add_item(bv.next_btn)
        await inter.edit_original_response(
            embed=_browse_embed(rows, inter.guild, 0, total, "all"),
            view=bv,
        )

    async def _open_sell(self, inter: discord.Interaction):
        gid, uid = inter.guild_id, inter.user.id
        # Check slot limit
        active = await db.pool.fetchval(
            "SELECT COUNT(*) FROM auction_house WHERE guild_id=$1 AND seller_id=$2 AND status='active'",
            gid, uid
        ) or 0
        if active >= AH_MAX_SLOTS:
            return await inter.response.send_message(
                f"❌ Max {AH_MAX_SLOTS} active listings. Cancel one first.", ephemeral=True
            )
        # Load inventory
        rows = await db.pool.fetch("""
            SELECT i.item_key, i.quantity, e.name, e.rarity
            FROM inventory i
            LEFT JOIN (
                SELECT key, name, rarity FROM (VALUES
                    ('hp_potion_s','Small HP Potion','common'),
                    ('hp_potion_m','Medium HP Potion','uncommon'),
                    ('hp_potion_l','Large HP Potion','rare'),
                    ('mana_potion','Mana Potion','uncommon'),
                    ('revival_orb','Revival Orb','epic'),
                    ('luck_charm','Lucky Charm','rare'),
                    ('speed_boots','Boots of Swiftness','rare'),
                    ('shadow_cloak','Shadow Cloak','rare'),
                    ('iron_shield','Iron Shield','uncommon'),
                    ('elixir','Elixir of Strength','epic'),
                    ('antidote','Antidote','common'),
                    ('mage_robe','Arcane Robe','rare')
                ) AS t(key,name,rarity)
            ) e ON i.item_key=e.key
            WHERE i.guild_id=$1 AND i.user_id=$2 AND i.quantity>0
        """, gid, uid)

        # Also check RPG equipment
        equip_rows = await db.pool.fetch(
            "SELECT slot||'_'||item_name AS item_key, item_name, item_rank FROM rpg_equipment WHERE guild_id=$1 AND user_id=$2",
            gid, uid
        )

        if not rows and not equip_rows:
            return await inter.response.send_message(
                "❌ No tradable items in your inventory. Buy items from `/market` or earn them from dungeons.",
                ephemeral=True,
            )

        all_items = []
        for r in rows:
            name   = r.get("name") or r["item_key"].replace("_"," ").title()
            rarity = r.get("rarity") or "common"
            all_items.append((r["item_key"], name, rarity, r["quantity"]))
        for r in equip_rows:
            rank_rarity = {"F":"common","E":"common","D":"uncommon","C":"uncommon","B":"rare","A":"epic","S":"legendary"}.get(r["item_rank"],"common")
            all_items.append((r["item_key"], r["item_name"], rank_rarity, 1))

        opts = [
            discord.SelectOption(
                label=f"{name[:50]} ×{qty}",
                value=f"{key}|{name}|{rarity}",
                description=f"{rarity.title()} item",
                emoji=RARITY_STAR.get(rarity,"⬜"),
            ) for key, name, rarity, qty in all_items[:25]
        ]
        sel = discord.ui.Select(placeholder="Select item to sell…", options=opts)
        async def sell_cb(i2):
            parts = sel.values[0].split("|",2)
            key, name, rarity = parts[0], parts[1], parts[2] if len(parts)>2 else "common"
            await i2.response.send_modal(SellModal(key, name, rarity, gid, uid))
        sel.callback = sell_cb
        sv = discord.ui.View(timeout=120); sv.add_item(sel)
        await inter.response.send_message("Which item do you want to list?", view=sv, ephemeral=True)

    async def _open_my(self, inter: discord.Interaction):
        rows = await db.pool.fetch("""
            SELECT * FROM auction_house
            WHERE guild_id=$1 AND seller_id=$2 AND status='active'
            ORDER BY listed_at DESC
        """, inter.guild_id, inter.user.id)
        if not rows:
            return await inter.followup.send("No active listings. Use **Sell** to list something!", ephemeral=True)
        e = discord.Embed(title="📋 Your Active Listings", color=C_ECONOMY)
        for r in rows:
            r = dict(r)
            star = RARITY_STAR.get(r.get("rarity","common"),"⬜")
            exp  = r.get("expires_at")
            exp_aware = exp.replace(tzinfo=timezone.utc) if exp and not exp.tzinfo else exp
            e.add_field(
                name=f"{star} #{r['id']} {r['item_name']}"+(f" ×{r['quantity']}" if r['quantity']>1 else ""),
                value=(
                    f"💰 {r['price']:,} 🪙"
                    +(f"  ·  Top bid: {r['current_bid']:,}" if r.get('current_bid') else "")
                    +f"\nExpires {discord.utils.format_dt(exp_aware,'R') if exp_aware else '?'}"
                ),
                inline=False,
            )
        await inter.followup.send(embed=e, ephemeral=True)


# ── DB helpers ────────────────────────────────────────────────────────
async def _fetch_page(guild_id: int, page: int, category: str = "all") -> tuple[list, int]:
    offset = page * PAGE_SIZE
    where  = "WHERE guild_id=$1 AND status='active'"
    params = [guild_id]
    if category != "all":
        where += " AND category=$2"
        params.append(category)

    rows  = await db.pool.fetch(f"SELECT * FROM auction_house {where} ORDER BY listed_at DESC LIMIT {PAGE_SIZE} OFFSET {offset}", *params)
    total = await db.pool.fetchval(f"SELECT COUNT(*) FROM auction_house {where}", *params) or 0
    return [dict(r) for r in rows], total

async def _post_listing(inter, gid, uid, item_key, item_name, rarity, price, qty, min_bid):
    # Remove from inventory
    existing = await db.pool.fetchval(
        "SELECT quantity FROM inventory WHERE guild_id=$1 AND user_id=$2 AND item_key=$3", gid, uid, item_key
    )
    if existing and existing >= qty:
        await db.pool.execute(
            "UPDATE inventory SET quantity=quantity-$1 WHERE guild_id=$2 AND user_id=$3 AND item_key=$4",
            qty, gid, uid, item_key
        )

    category = _auto_category(item_key, item_name)
    expires  = datetime.now(timezone.utc) + timedelta(hours=AH_EXPIRE_H)
    row = await db.pool.fetchrow("""
        INSERT INTO auction_house
            (guild_id, seller_id, item_key, item_name, rarity, category, quantity,
             price, min_bid, status, expires_at, listed_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'active',$10,NOW())
        RETURNING id
    """, gid, uid, item_key, item_name, rarity, category, qty, price, min_bid, expires)
    lid = row["id"]

    # Post to market channel
    cat_name, cat_color = CATEGORIES.get(category, CATEGORIES["other"])
    has_bid = min_bid is not None
    lview   = ListingActionView(lid, gid, uid, has_bid, price, min_bid)
    le      = _listing_embed({"id":lid,"seller_id":uid,"item_name":item_name,"rarity":rarity,
                               "category":category,"quantity":qty,"price":price,"min_bid":min_bid,
                               "current_bid":None,"current_bidder_id":None,"expires_at":expires,
                               "status":"active"}, inter.guild)

    ch_id = await get_market_channel(gid)
    ch    = inter.client.get_channel(int(ch_id)) if ch_id else inter.channel
    if not ch: ch = inter.channel

    msg = await ch.send(embed=le, view=lview)
    inter.client.add_view(lview, message_id=msg.id)

    await db.pool.execute(
        "UPDATE auction_house SET message_id=$1, channel_id=$2 WHERE id=$3",
        msg.id, ch.id, lid
    )
    notice = f"✅ Listed **{item_name}** ×{qty} for **{price:,} 🪙**!" + (f" (Bid from {min_bid:,})" if min_bid else "")
    if ch.id != inter.channel_id:
        notice += f"\nPosted in {ch.mention}"
    await inter.followup.send(notice, ephemeral=True)

async def _place_bid(inter, gid, lid, uid, bid_amount):
    row = await db.pool.fetchrow("SELECT * FROM auction_house WHERE id=$1 AND status='active' AND guild_id=$2", lid, gid)
    if not row: return await inter.followup.send("❌ Listing gone.",ephemeral=True)
    if uid == row["seller_id"]: return await inter.followup.send("❌ Can't bid on your own listing.",ephemeral=True)
    min_next = max(row["min_bid"] or 1, (row["current_bid"] or 0) + 1)
    if bid_amount < min_next: return await inter.followup.send(f"❌ Minimum bid is **{min_next:,}**.",ephemeral=True)
    if bid_amount >= row["price"]: return await _execute_buyout(inter, gid, lid, uid)
    bal = await get_balance(gid, uid)
    if bal < bid_amount: return await inter.followup.send(f"❌ Need {bid_amount:,} but have {bal:,}.",ephemeral=True)

    # Refund previous bidder
    if row["current_bidder_id"] and row["current_bid"]:
        await add_coins(gid, row["current_bidder_id"], row["current_bid"])
        prev = inter.guild.get_member(row["current_bidder_id"])
        if prev:
            try:
                await prev.send(embed=discord.Embed(
                    title="📉 Outbid!",
                    description=f"You were outbid on **{row['item_name']}**. Your {row['current_bid']:,} 🪙 refunded.",
                    color=C_WARN,
                ))
            except: pass

    await add_coins(gid, uid, -bid_amount)
    await db.pool.execute("UPDATE auction_house SET current_bid=$1, current_bidder_id=$2 WHERE id=$3", bid_amount, uid, lid)
    await inter.followup.send(f"✅ Bid of **{bid_amount:,} 🪙** placed on **{row['item_name']}**!", ephemeral=True)
    # Update listing embed
    await _refresh_listing(inter.client, gid, lid, inter.guild, "new_bid")

async def _execute_buyout(inter, gid, lid, uid):
    row = await db.pool.fetchrow("SELECT * FROM auction_house WHERE id=$1 AND status='active' AND guild_id=$2", lid, gid)
    if not row: return await inter.followup.send("❌ Listing gone.",ephemeral=True)
    if uid == row["seller_id"]: return await inter.followup.send("❌ Can't buy your own listing.",ephemeral=True)
    bal = await get_balance(gid, uid)
    if bal < row["price"]: return await inter.followup.send(f"❌ Need {row['price']:,} but have {bal:,}.",ephemeral=True)

    # Refund existing bidder
    if row["current_bidder_id"] and row["current_bid"] and row["current_bidder_id"] != uid:
        await add_coins(gid, row["current_bidder_id"], row["current_bid"])

    await add_coins(gid, uid, -row["price"])
    fee = int(row["price"] * AH_FEE)
    await add_coins(gid, row["seller_id"], row["price"] - fee)

    # Give item
    await db.pool.execute("""
        INSERT INTO inventory (guild_id,user_id,item_key,quantity) VALUES ($1,$2,$3,$4)
        ON CONFLICT (guild_id,user_id,item_key) DO UPDATE SET quantity=inventory.quantity+$4
    """, gid, uid, row["item_key"], row["quantity"])

    await db.pool.execute("UPDATE auction_house SET status='sold', buyer_id=$1, sold_at=NOW() WHERE id=$2", uid, lid)

    buyer  = inter.guild.get_member(uid)
    seller = inter.guild.get_member(row["seller_id"])
    await inter.followup.send(
        embed=discord.Embed(
            title="✅ Purchase Complete!",
            description=(
                f"**{row['item_name']}** ×{row['quantity']}\n"
                f"Paid: **{row['price']:,}** 🪙  ·  Fee: {fee:,}"
            ),
            color=C_SUCCESS,
        ), ephemeral=True
    )
    if seller:
        try:
            await seller.send(embed=discord.Embed(
                title="💰 Item Sold!",
                description=f"**{row['item_name']}** sold to {buyer.mention if buyer else 'someone'} for {row['price']:,} 🪙 (you received {row['price']-fee:,}).",
                color=C_SUCCESS,
            ))
        except: pass
    await _refresh_listing(inter.client, gid, lid, inter.guild, "sold")

async def _cancel_listing(inter, gid, lid, uid):
    row = await db.pool.fetchrow("SELECT * FROM auction_house WHERE id=$1 AND seller_id=$2 AND status='active'", lid, uid)
    if not row: return await inter.followup.send("❌ Listing not found.",ephemeral=True)
    if row["current_bidder_id"] and row["current_bid"]:
        await add_coins(gid, row["current_bidder_id"], row["current_bid"])
    # Return item
    await db.pool.execute("""
        INSERT INTO inventory (guild_id,user_id,item_key,quantity) VALUES ($1,$2,$3,$4)
        ON CONFLICT (guild_id,user_id,item_key) DO UPDATE SET quantity=inventory.quantity+$4
    """, gid, uid, row["item_key"], row["quantity"])
    await db.pool.execute("UPDATE auction_house SET status='cancelled' WHERE id=$1", lid)
    await inter.followup.send("❌ Listing cancelled. Item returned.",ephemeral=True)
    await _refresh_listing(inter.client, gid, lid, inter.guild)

async def _refresh_listing(bot, gid, lid, guild, highlight=""):
    row = await db.pool.fetchrow("SELECT * FROM auction_house WHERE id=$1", lid)
    if not row: return
    ch  = bot.get_channel(row.get("channel_id"))
    if not ch: return
    try:
        msg = await ch.fetch_message(row["message_id"])
        r = dict(row)
        e = _listing_embed(r, guild, highlight)
        if r["status"] != "active":
            await msg.edit(embed=e, view=None)
        else:
            has_bid = r.get("min_bid") is not None
            v = ListingActionView(lid,gid,r["seller_id"],has_bid,r["price"],r.get("min_bid"))
            await msg.edit(embed=e, view=v)
    except Exception: pass


# ── Cog ───────────────────────────────────────────────────────────────
class AuctionHouseCog(commands.Cog, name="AuctionHouse"):
    def __init__(self, bot):
        self.bot = bot
        self.expire_loop.start()

    def cog_unload(self): self.expire_loop.cancel()

    @tasks.loop(minutes=15)
    async def expire_loop(self):
        await self.bot.wait_until_ready()
        rows = await db.pool.fetch("SELECT * FROM auction_house WHERE status='active' AND expires_at < NOW()")
        for r in rows:
            r = dict(r)
            if r.get("current_bidder_id") and r.get("current_bid"):
                await add_coins(r["guild_id"], r["current_bidder_id"], r["current_bid"])
            await db.pool.execute("""
                INSERT INTO inventory (guild_id,user_id,item_key,quantity) VALUES ($1,$2,$3,$4)
                ON CONFLICT (guild_id,user_id,item_key) DO UPDATE SET quantity=inventory.quantity+$4
            """, r["guild_id"], r["seller_id"], r["item_key"], r["quantity"])
            await db.pool.execute("UPDATE auction_house SET status='expired' WHERE id=$1", r["id"])
            guild = self.bot.get_guild(r["guild_id"])
            if guild: await _refresh_listing(self.bot, r["guild_id"], r["id"], guild)

    async def cog_load(self):
    # Schedule view restoration after bot is ready, don't block cog loading
        self.bot.loop.create_task(self._restore_views())

    async def _restore_views(self):
        await self.bot.wait_until_ready()
        rows = await db.pool.fetch("SELECT * FROM auction_house WHERE status='active' AND message_id IS NOT NULL")
        for r in rows:
            r = dict(r)
            has_bid = r.get("min_bid") is not None
            v = ListingActionView(r["id"], r["guild_id"], r["seller_id"], has_bid, r["price"], r.get("min_bid"))
            self.bot.add_view(v, message_id=r["message_id"])



    @commands.hybrid_command(name="ah", aliases=["auction","auctionhouse","market_ah"],
                             description="Auction House — buy, sell, and browse player listings")
    async def ah(self, ctx):
        """Full Agora-style auction house in one command."""
        view = AHHubView(ctx)
        await ctx.send(embed=view._home_embed(), view=view)
