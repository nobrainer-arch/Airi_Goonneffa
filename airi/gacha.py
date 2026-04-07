# airi/gacha.py
# Persistent one-message gacha board. Any user presses a button → queued roll.
# Results are ephemeral to keep the channel clean.
import discord
from discord.ext import commands
from datetime import datetime
import random
import asyncio
import db
from utils import _err, C_GACHA
from airi.guild_config import check_channel, get_gacha_channel
from airi.economy import add_coins
from airi.inventory import add_item

SINGLE_COST = 500
MULTI_COST  = 4500
PITY_AT     = 50

REWARDS = {
    "common":    [("💰 Coins (100–300)",   "coins",        (100,   300))],
    "rare":      [("💰 Coins (500–1k)",    "coins",        (500,  1000)),
                  ("⚡ XP Boost (1h)",      "xp_boost_1h",  None)],
    "epic":      [("💰 Coins (1.5k–2.5k)", "coins",        (1500, 2500)),
                  ("💰 Daily ×2",          "daily_x2",     None),
                  ("🛡️ Claim Shield",      "shield_7d",    None)],
    "legendary": [("💰 Coins (5k–10k)",    "coins",        (5000, 10000)),
                  ("🎟️ Waifu Ticket",      "waifu_ticket", None),
                  ("🏭 Biz Boost",         "biz_boost_2h", None),
                  ("📜 Prenup Doc",         "prenup",       None),
                  ("🌟 XP Boost (24h)",    "xp_boost_24h", None)],
    "mythic":    [("💰 Coins (20k–50k)",   "coins",        (20000,50000)),
                  ("🎟️ Waifu Ticket ×3",  "waifu_ticket_3",None)],
}

RARITY_WEIGHTS = [("common",60),("rare",25),("epic",10),("legendary",4),("mythic",1)]
RARITY_COLORS  = {"common":0xaaaaaa,"rare":0x3498db,"epic":0x9b59b6,"legendary":0xf1c40f,"mythic":0xff0000}
RARITY_STAR    = {"common":"⬜","rare":"🟦","epic":"🟪","legendary":"🟨","mythic":"🟥"}


def _roll_rarity(pity: int) -> str:
    if pity >= PITY_AT: return "legendary"
    total = sum(w for _,w in RARITY_WEIGHTS)
    r = random.randint(1, total); cum = 0
    for name, weight in RARITY_WEIGHTS:
        cum += weight
        if r <= cum: return name
    return "common"


async def _do_roll(guild_id, user_id):
    pity = await db.pool.fetchval(
        "SELECT pulls FROM gacha_pity WHERE guild_id=$1 AND user_id=$2", guild_id, user_id
    ) or 0
    rarity = _roll_rarity(pity)
    label, item_key, coin_range = random.choice(REWARDS[rarity])
    new_pity = 0 if rarity in ("legendary","mythic") else pity + 1
    await db.pool.execute("""
        INSERT INTO gacha_pity (guild_id,user_id,pulls) VALUES ($1,$2,$3)
        ON CONFLICT (guild_id,user_id) DO UPDATE SET pulls=$3
    """, guild_id, user_id, new_pity)
    coins_gained = None
    if item_key == "coins":
        coins_gained = random.randint(*coin_range)
        await add_coins(guild_id, user_id, coins_gained)
    else:
        await add_item(guild_id, user_id, item_key, 1)
    return rarity, label, coins_gained, new_pity


# ── Persistent Gacha Board ────────────────────────────────────────
class GachaBoardView(discord.ui.View):
    """Persistent view attached to the one gacha message per guild."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎰 Roll ×1  (500 coins)", style=discord.ButtonStyle.primary,
                       custom_id="gacha_roll_1")
    async def roll_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._roll(interaction, 1)

    @discord.ui.button(label="🎰 Roll ×10  (4,500 coins)", style=discord.ButtonStyle.secondary,
                       custom_id="gacha_roll_10")
    async def roll_10(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._roll(interaction, 10)

    async def _roll(self, interaction: discord.Interaction, count: int):
        await interaction.response.defer(ephemeral=True)
        gid = interaction.guild_id
        uid = interaction.user.id
        cost = SINGLE_COST if count == 1 else MULTI_COST

        # Atomic balance deduct
        row = await db.pool.fetchrow("""
            UPDATE economy SET balance=balance-$1
            WHERE guild_id=$2 AND user_id=$3 AND balance>=$1
            RETURNING balance
        """, cost, gid, uid)
        if not row:
            bal = await db.pool.fetchval(
                "SELECT balance FROM economy WHERE guild_id=$1 AND user_id=$2", gid, uid
            ) or 0
            await interaction.followup.send(
                f"❌ Need **{cost:,}** coins but you have **{bal:,}**.", ephemeral=True
            )
            return

        from airi.audit_log import log as audit
        await audit(gid, uid, "gacha_roll", f"{count}x", -cost)
        from utils import log_txn
        await log_txn(interaction.client, gid, f"Gacha Roll ×{count}", interaction.user, "System", cost)

        if count == 1:
            rarity, label, coins, pity = await _do_roll(gid, uid)
            result_txt = f"🎁 **{label}**" + (f"\n\n💰 +**{coins:,}** coins" if coins else "\n\n*Check `!inventory` to use your item!*")
            e = discord.Embed(
                title=f"🎰 {RARITY_STAR[rarity]} {rarity.upper()}",
                description=f"{interaction.user.mention} rolled...\n\n{result_txt}",
                color=RARITY_COLORS[rarity],
                timestamp=datetime.utcnow(),
            )
            e.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
            e.set_footer(text=f"Pity: {pity}/{PITY_AT} · Spent {cost:,} coins")
        else:
            results = []
            for _ in range(10):
                rarity, label, coins, pity = await _do_roll(gid, uid)
                star = RARITY_STAR[rarity]
                results.append((rarity, f"{star} **{rarity.title()}** — {label}"))
            best = max(results, key=lambda x: list(RARITY_COLORS).index(x[0]))
            e = discord.Embed(
                title="🎰 10× Gacha Pull",
                description="\n".join(r[1] for r in results),
                color=RARITY_COLORS[best[0]],
                timestamp=datetime.utcnow(),
            )
            e.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
            e.set_footer(text=f"Pity: {pity}/{PITY_AT} · Spent {cost:,} coins · Items in !inventory")

        await interaction.followup.send(embed=e, ephemeral=True)


def _board_embed() -> discord.Embed:
    e = discord.Embed(
        title="🎰 Gacha Machine",
        description=(
            "**Roll for items and coins!**\n\n"
            "🎰 ×1 roll costs **500 coins**\n"
            "🎰 ×10 roll costs **4,500 coins**\n\n"
            "**Rarities:**\n"
            "⬜ Common (60%) · 🟦 Rare (25%) · 🟪 Epic (10%)\n"
            "🟨 Legendary (4%) · 🟥 Mythic (1%)\n\n"
            f"*Pity: Legendary guaranteed at pull {PITY_AT}*\n"
            "Results are private — only you see what you roll."
        ),
        color=C_GACHA,
    )
    return e


class GachaCog(commands.Cog, name="Gacha"):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        """Re-attach persistent views on restart."""
        self.bot.add_view(GachaBoardView())

    @commands.command(aliases=["roll"])
    async def gacha(self, ctx, count: int = 1):
        """Roll gacha. !gacha or !gacha 10"""
        if not await check_channel(ctx, "gacha"): return

        gacha_ch_id = await get_gacha_channel(ctx.guild.id)
        if gacha_ch_id and ctx.channel.id != gacha_ch_id:
            ch = self.bot.get_channel(gacha_ch_id)
            if ch:
                await _err(ctx, f"Use the gacha machine in {ch.mention}!")
                return

        if count not in (1, 10):
            return await _err(ctx, "Use `!gacha` for 1 roll or `!gacha 10` for 10 rolls.")

        # Let the user roll inline via ephemeral (board message preferred)
        cost = SINGLE_COST if count == 1 else MULTI_COST
        row = await db.pool.fetchrow("""
            UPDATE economy SET balance=balance-$1
            WHERE guild_id=$2 AND user_id=$3 AND balance>=$1
            RETURNING balance
        """, cost, ctx.guild.id, ctx.author.id)
        if not row:
            bal = await db.pool.fetchval("SELECT balance FROM economy WHERE guild_id=$1 AND user_id=$2", ctx.guild.id, ctx.author.id) or 0
            return await _err(ctx, f"Need **{cost:,}** coins but you have **{bal:,}**.")

        from airi.audit_log import log as audit
        await audit(ctx.guild.id, ctx.author.id, "gacha_roll", f"{count}x", -cost)

        if count == 1:
            rarity, label, coins, pity = await _do_roll(ctx.guild.id, ctx.author.id)
            e = discord.Embed(
                title=f"🎰 {RARITY_STAR[rarity]} {rarity.upper()}",
                description=f"**{ctx.author.display_name}** rolled...\n\n🎁 **{label}**" +
                            (f"\n\n💰 +**{coins:,}** coins" if coins else "\n\n*Check `!inventory`!*"),
                color=RARITY_COLORS[rarity], timestamp=datetime.utcnow(),
            )
            e.set_footer(text=f"Pity: {pity}/{PITY_AT} · Spent {cost:,} coins")
        else:
            results = []
            for _ in range(10):
                rarity, label, coins, pity = await _do_roll(ctx.guild.id, ctx.author.id)
                results.append((rarity, f"{RARITY_STAR[rarity]} **{rarity.title()}** — {label}"))
            best = max(results, key=lambda x: list(RARITY_COLORS).index(x[0]))
            e = discord.Embed(
                title="🎰 10× Gacha Pull",
                description="\n".join(r[1] for r in results),
                color=RARITY_COLORS[best[0]], timestamp=datetime.utcnow(),
            )
            e.set_footer(text=f"Pity: {pity}/{PITY_AT} · Spent {cost:,} coins")

        await ctx.send(embed=e, delete_after=30)
        try: await ctx.message.delete()
        except Exception: pass

    @commands.command()
    @commands.has_permissions(manage_channels=True)
    async def gachaboard(self, ctx):
        """Post (or repost) the persistent gacha board to this channel."""
        gid = ctx.guild.id
        # Remove old board if exists
        old = await db.pool.fetchrow("SELECT channel_id, message_id FROM gacha_persistent WHERE guild_id=$1", gid)
        if old:
            try:
                oc = self.bot.get_channel(old["channel_id"])
                if oc:
                    om = await oc.fetch_message(old["message_id"])
                    await om.delete()
            except Exception:
                pass

        msg = await ctx.channel.send(embed=_board_embed(), view=GachaBoardView())
        await db.pool.execute("""
            INSERT INTO gacha_persistent (guild_id, channel_id, message_id)
            VALUES ($1,$2,$3)
            ON CONFLICT (guild_id) DO UPDATE SET channel_id=$2, message_id=$3
        """, gid, ctx.channel.id, msg.id)
        # Save gacha channel config
        from airi.guild_config import set_value, K_GACHA
        await set_value(gid, K_GACHA, str(ctx.channel.id))
        await ctx.message.delete()
