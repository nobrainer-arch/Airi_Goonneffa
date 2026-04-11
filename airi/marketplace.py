# airi/marketplace.py
# Waifu Marketplace — button-driven bidding and buyout.
# Listing posts go to the market channel and stay there.
# Bid/buyout/hammer happen via buttons — no text commands needed.
import discord
from discord.ext import commands
from datetime import datetime
import db
from airi.guild_config import get_market_channel
from airi.economy import add_coins, get_balance
from utils import _err, C_SOCIAL, C_WARN, C_SUCCESS, C_ERROR, C_ECONOMY

ESCROW_COLOR  = 0x1a1a2e
SOLD_COLOR    = 0x533483
BID_COLOR     = 0x3498db


def _market_embed(row, guild) -> discord.Embed:
    waifu_m  = guild.get_member(row["waifu_id"])
    seller_m = guild.get_member(row["seller_id"])
    wname    = waifu_m.display_name if waifu_m else f"<@{row['waifu_id']}>"
    sname    = seller_m.display_name if seller_m else f"<@{row['seller_id']}>"

    cur_bid  = row["current_bid"] or row["min_bid"]
    bidder_m = guild.get_member(row["current_bidder"]) if row["current_bidder"] else None
    bid_line = f"💸 Current bid: **{cur_bid:,}** coins" + (f" by {bidder_m.display_name}" if bidder_m else "")
    buy_line = f"⚡ Buyout: **{row['buyout_price']:,}** coins" if row["buyout_price"] else ""

    e = discord.Embed(
        title=f"🔒 {wname} — Waifu Auction",
        description=f"Seller: **{sname}**\n\nStarting bid: **{row['min_bid']:,}** coins\n{bid_line}" + (f"\n{buy_line}" if buy_line else ""),
        color=ESCROW_COLOR,
        timestamp=datetime.utcnow(),
    )
    if waifu_m:
        e.set_thumbnail(url=waifu_m.display_avatar.url)
    e.set_footer(text=f"Listing #{row['id']} · Use buttons below")
    return e


# ── Bid modal ─────────────────────────────────────────────────────
class WaifuBidModal(discord.ui.Modal, title="Place a Bid"):
    amount_input = discord.ui.TextInput(
        label="Bid amount (coins)",
        placeholder="Must be higher than current bid",
        required=True, max_length=10,
    )

    def __init__(self, listing_id: int):
        super().__init__()
        self._lid = listing_id

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.amount_input.value.strip().replace(",", "")
        if not raw.isdigit():
            await interaction.response.send_message("❌ Enter a valid number.", ephemeral=True); return
        amount = int(raw)
        uid = interaction.user.id
        gid = interaction.guild_id

        async with db.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT * FROM waifu_market WHERE id=$1 AND guild_id=$2 AND status='active' FOR UPDATE",
                    self._lid, gid
                )
                if not row:
                    await interaction.response.send_message("❌ Listing no longer active.", ephemeral=True); return
                if row["seller_id"] == uid:
                    await interaction.response.send_message("❌ Can't bid on your own listing.", ephemeral=True); return
                min_next = (row["current_bid"] or row["min_bid"]) + 1
                if amount < min_next:
                    await interaction.response.send_message(f"❌ Min bid is **{min_next:,}** coins.", ephemeral=True); return
                bal = await conn.fetchval("SELECT balance FROM economy WHERE guild_id=$1 AND user_id=$2", gid, uid) or 0
                if bal < amount:
                    await interaction.response.send_message(f"❌ Need **{amount:,}** but have **{bal:,}**.", ephemeral=True); return

                # Refund previous bidder
                if row["current_bidder"] and row["current_bid"]:
                    await conn.execute(
                        "UPDATE economy SET balance=balance+$1 WHERE guild_id=$2 AND user_id=$3",
                        row["current_bid"], gid, row["current_bidder"]
                    )
                # Hold new bid
                await conn.execute(
                    "UPDATE economy SET balance=balance-$1 WHERE guild_id=$2 AND user_id=$3", amount, gid, uid
                )
                await conn.execute(
                    "UPDATE waifu_market SET current_bid=$1, current_bidder=$2 WHERE id=$3",
                    amount, uid, self._lid
                )

        await _refresh_market_msg(interaction.client, gid, self._lid, interaction.guild)
        await interaction.response.send_message(
            f"✅ Bid of **{amount:,}** coins placed! You'll be refunded if outbid.", ephemeral=True
        )


# ── Per-listing view ──────────────────────────────────────────────
class WaifuListingView(discord.ui.View):
    def __init__(self, listing_id: int, seller_id: int, has_buyout: bool):
        super().__init__(timeout=None)
        self._lid       = listing_id
        self._seller    = seller_id
        self._has_buyout = has_buyout
        for child in self.children:
            if hasattr(child, "label"):
                if child.label == "💸 Bid":
                    child.custom_id = f"wm_bid_{listing_id}"
                elif child.label == "⚡ Buyout":
                    child.custom_id = f"wm_buy_{listing_id}"
                elif "Hammer" in child.label:
                    child.custom_id = f"wm_hammer_{listing_id}"

    @discord.ui.button(label="💸 Bid", style=discord.ButtonStyle.primary)
    async def bid_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id == self._seller:
            await interaction.response.send_message("Use 🔨 to end your auction.", ephemeral=True); return
        await interaction.response.send_modal(WaifuBidModal(self._lid))

    @discord.ui.button(label="⚡ Buyout", style=discord.ButtonStyle.success)
    async def buyout_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._has_buyout:
            await interaction.response.send_message("This listing is bid-only.", ephemeral=True); return
        if interaction.user.id == self._seller:
            await interaction.response.send_message("Can't buy your own waifu.", ephemeral=True); return
        row = await db.pool.fetchrow("SELECT * FROM waifu_market WHERE id=$1 AND status='active'", self._lid)
        if not row:
            await interaction.response.send_message("❌ Listing no longer active.", ephemeral=True); return

        class ConfirmView(discord.ui.View):
            def __init__(self_, price): super().__init__(timeout=30); self_._price = price
            @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success)
            async def yes(self_, inter, btn):
                for i in self_.children: i.disabled = True
                await inter.response.edit_message(view=self_)
                await _execute_buyout(inter, self_._lid, inter.user.id)
            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def no(self_, inter, btn):
                for i in self_.children: i.disabled = True
                await inter.response.edit_message(content="Cancelled.", view=self_)

        await interaction.response.send_message(
            f"Buy this waifu for **{row['buyout_price']:,}** coins?",
            view=ConfirmView(row["buyout_price"]), ephemeral=True
        )

    @discord.ui.button(label="🔨 Hammer (end)", style=discord.ButtonStyle.danger)
    async def hammer_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self._seller and not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Only the seller can end the auction.", ephemeral=True); return
        await _execute_hammer(interaction, self._lid)


# ── Execution helpers ─────────────────────────────────────────────
async def _execute_buyout(interaction: discord.Interaction, lid: int, uid: int):
    gid = interaction.guild_id
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM waifu_market WHERE id=$1 AND guild_id=$2 AND status='active' FOR UPDATE",
                lid, gid
            )
            if not row or not row["buyout_price"]:
                await interaction.followup.send("❌ Not available.", ephemeral=True); return
            bal = await conn.fetchval("SELECT balance FROM economy WHERE guild_id=$1 AND user_id=$2", gid, uid) or 0
            if bal < row["buyout_price"]:
                await interaction.followup.send(f"❌ Need **{row['buyout_price']:,}**, have **{bal:,}**.", ephemeral=True); return

            # Refund existing bidder
            if row["current_bidder"] and row["current_bid"]:
                await conn.execute(
                    "UPDATE economy SET balance=balance+$1 WHERE guild_id=$2 AND user_id=$3",
                    row["current_bid"], gid, row["current_bidder"]
                )
            # Pay
            fee    = max(1, int(row["buyout_price"] * 0.05))
            payout = row["buyout_price"] - fee
            await conn.execute("UPDATE economy SET balance=balance-$1 WHERE guild_id=$2 AND user_id=$3", row["buyout_price"], gid, uid)
            await conn.execute("""
                INSERT INTO economy (guild_id,user_id,balance) VALUES ($1,$2,$3)
                ON CONFLICT (guild_id,user_id) DO UPDATE SET balance=economy.balance+$3
            """, gid, row["seller_id"], payout)
            # Transfer waifu ownership
            await conn.execute("UPDATE claims SET claimer_id=$1 WHERE guild_id=$2 AND claimed_id=$3", uid, gid, row["waifu_id"])
            await conn.execute("UPDATE waifu_market SET status='sold' WHERE id=$1", lid)

    waifu_m = interaction.guild.get_member(row["waifu_id"])
    wname   = waifu_m.display_name if waifu_m else f"<@{row['waifu_id']}>"
    await interaction.followup.send(
        f"✅ You bought **{wname}**! They're now in your harem.", ephemeral=True
    )
    await _refresh_market_msg(interaction.client, gid, lid, interaction.guild)


async def _execute_hammer(interaction: discord.Interaction, lid: int):
    gid = interaction.guild_id
    row = await db.pool.fetchrow("SELECT * FROM waifu_market WHERE id=$1 AND guild_id=$2 AND status='active'", lid, gid)
    if not row:
        await interaction.response.send_message("❌ Listing not found.", ephemeral=True); return

    winner_id = row["current_bidder"]
    won_amount = row["current_bid"] or 0

    if winner_id and won_amount > 0:
        fee    = max(1, int(won_amount * 0.05))
        payout = won_amount - fee
        await db.pool.execute("""
            INSERT INTO economy (guild_id,user_id,balance) VALUES ($1,$2,$3)
            ON CONFLICT (guild_id,user_id) DO UPDATE SET balance=economy.balance+$3
        """, gid, row["seller_id"], payout)
        # Transfer waifu
        await db.pool.execute("UPDATE claims SET claimer_id=$1 WHERE guild_id=$2 AND claimed_id=$3", winner_id, gid, row["waifu_id"])
        winner = interaction.guild.get_member(winner_id)
        result_txt = f"Winner: {winner.mention if winner else f'<@{winner_id}>'} with **{won_amount:,}** coins"
    else:
        # No bids — return to seller
        result_txt = "No bids — waifu returned to seller."

    await db.pool.execute("UPDATE waifu_market SET status='ended' WHERE id=$1", lid)

    waifu_m = interaction.guild.get_member(row["waifu_id"])
    wname   = waifu_m.display_name if waifu_m else f"<@{row['waifu_id']}>"
    await interaction.response.send_message(
        embed=discord.Embed(
            title=f"🔨 Auction Ended — {wname}",
            description=result_txt, color=SOLD_COLOR,
        ),
        ephemeral=False
    )
    await _refresh_market_msg(interaction.client, gid, lid, interaction.guild)


async def _refresh_market_msg(bot, gid: int, lid: int, guild):
    row = await db.pool.fetchrow("SELECT * FROM waifu_market WHERE id=$1", lid)
    if not row: return
    ch  = bot.get_channel(row["channel_id"])
    if not ch: return
    try:
        msg = await ch.fetch_message(row["message_id"])
        if row["status"] == "active":
            e    = _market_embed(row, guild)
            view = WaifuListingView(lid, row["seller_id"], bool(row["buyout_price"]))
            await msg.edit(embed=e, view=view)
        else:
            e       = _market_embed(row, guild)
            e.color = SOLD_COLOR
            e.title = "✅ Sold — " + e.title
            await msg.edit(embed=e, view=None)
    except Exception:
        pass


class MarketplaceCog(commands.Cog, name="Marketplace"):
    def __init__(self, bot): self.bot = bot

    async def cog_load(self):
        # Re-attach views for active listings on restart
        rows = await db.pool.fetch(
            "SELECT * FROM waifu_market WHERE status='active' AND message_id IS NOT NULL"
        )
        for row in rows:
            self.bot.add_view(WaifuListingView(row["id"], row["seller_id"], bool(row["buyout_price"])))

    @commands.hybrid_command(aliases=["wl", "listwaifu"])
    async def wlist(self, ctx, member: discord.Member = None, min_bid: int = 100, buyout_price: int = None):
        """List a waifu for auction. !wlist @user <min_bid> [buyout_price]"""
        if member is None:
            # UserSelect picker
            gid = ctx.guild.id
            uid = ctx.author.id
            rows = await db.pool.fetch(
                "SELECT claimed_id FROM claims WHERE guild_id=$1 AND claimer_id=$2", gid, uid
            )
            if not rows: return await _err(ctx, "You have no waifus to list.")
            options = []
            for r in rows[:25]:
                m = ctx.guild.get_member(r["claimed_id"])
                if m: options.append(discord.SelectOption(label=m.display_name, value=str(r["claimed_id"])))
            if not options: return await _err(ctx, "No members found.")

            class ListSelect(discord.ui.Select):
                def __init__(self_): super().__init__(placeholder="Select waifu to list...", options=options)
                async def callback(self_, inter):
                    if inter.user.id != uid: return await inter.response.send_message("Not for you.", ephemeral=True)
                    m = inter.guild.get_member(int(self_.values[0]))
                    if m:
                        for i in self_.view.children: i.disabled = True
                        await inter.response.edit_message(view=self_.view)
                        fake_ctx = ctx
                        fake_ctx.author = inter.user
                        await _do_wlist(inter.client, inter.guild, inter.channel, inter.user, m, min_bid, buyout_price)
                    self_.view.stop()

            class ListView(discord.ui.View):
                def __init__(self_): super().__init__(timeout=60); self_.add_item(ListSelect())
            await ctx.send("Which waifu do you want to list?", view=ListView())
            return

        await _do_wlist(self.bot, ctx.guild, ctx.channel, ctx.author, member, min_bid, buyout_price)

    @commands.hybrid_command(aliases=["wmarket", "wm"])
    async def waifumarket(self, ctx):
        """Browse active waifu market listings."""
        gid  = ctx.guild.id
        rows = await db.pool.fetch(
            "SELECT * FROM waifu_market WHERE guild_id=$1 AND status='active' ORDER BY listed_at DESC LIMIT 20",
            gid
        )
        if not rows:
            return await ctx.send(embed=discord.Embed(
                description="No active waifu listings. Use `!wlist @waifu <min_bid>` to list one!",
                color=C_SOCIAL
            ))
        e = discord.Embed(title="💘 Waifu Market", color=C_SOCIAL)
        for r in rows:
            wm = ctx.guild.get_member(r["waifu_id"])
            wn = wm.display_name if wm else f"<@{r['waifu_id']}>"
            cb = r["current_bid"] or r["min_bid"]
            e.add_field(
                name=f"#{r['id']} {wn}",
                value=f"Bid: **{cb:,}**" + (f" · Buy: **{r['buyout_price']:,}**" if r["buyout_price"] else "") +
                      (f"\n→ [Go to listing](https://discord.com/channels/{gid}/{r['channel_id']}/{r['message_id']})" if r["message_id"] else ""),
                inline=True,
            )
        await ctx.send(embed=e)


async def _do_wlist(bot, guild, channel, author, member, min_bid, buyout_price):
    gid = guild.id
    sid = author.id
    wid = member.id

    if min_bid < 0 or (buyout_price is not None and buyout_price <= min_bid):
        await channel.send("❌ Invalid prices. Buyout must be higher than min bid.", delete_after=8); return
    if not await db.pool.fetchrow("SELECT 1 FROM claims WHERE guild_id=$1 AND claimer_id=$2 AND claimed_id=$3", gid, sid, wid):
        await channel.send(f"❌ You don't own {member.mention}.", delete_after=8); return
    if await db.pool.fetchrow("SELECT id FROM waifu_market WHERE guild_id=$1 AND waifu_id=$2 AND status='active'", gid, wid):
        await channel.send(f"❌ {member.display_name} is already listed.", delete_after=8); return

    market_ch_id = await get_market_channel(gid)
    target_ch    = bot.get_channel(market_ch_id) if market_ch_id else channel

    row = await db.pool.fetchrow("""
        INSERT INTO waifu_market (guild_id, seller_id, waifu_id, min_bid, buyout_price, channel_id)
        VALUES ($1,$2,$3,$4,$5,$6) RETURNING *
    """, gid, sid, wid, min_bid, buyout_price, target_ch.id)

    e    = _market_embed(row, guild)
    view = WaifuListingView(row["id"], sid, bool(buyout_price))
    msg  = await target_ch.send(embed=e, view=view)

    await db.pool.execute("UPDATE waifu_market SET message_id=$1 WHERE id=$2", msg.id, row["id"])
    if target_ch != channel:
        await channel.send(f"✅ Listed in {target_ch.mention}!", delete_after=8)
