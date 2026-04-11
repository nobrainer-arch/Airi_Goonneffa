# airi/gacha.py — Item gacha, PERSISTENT board (survives restarts)
import discord
from discord.ext import commands
from datetime import datetime
import random
import db
from utils import _err, C_GACHA, log_txn
from airi.guild_config import check_channel, get_gacha_channel
from airi.economy import add_coins, get_balance
from airi.inventory import add_item, ITEMS, RARITY_STAR

SINGLE_COST = 500
MULTI_COST  = 4500
PITY_AT     = 50
RARITY_WEIGHTS = [("common",50),("rare",28),("epic",14),("legendary",6),("mythic",2)]
RARITY_COLORS  = {
    "common":0x808080,"rare":0x3498db,"epic":0x9b59b6,"legendary":0xf1c40f,"mythic":0xff66ff
}

def _roll_rarity(pity: int) -> str:
    if pity >= PITY_AT: return "legendary"
    total = sum(w for _,w in RARITY_WEIGHTS)
    r = random.randint(1, total); cum = 0
    for name, w in RARITY_WEIGHTS:
        cum += w
        if r <= cum: return name
    return "common"

async def _do_roll(gid: int, uid: int) -> tuple[str, str, int, int]:
    """Roll once; returns (rarity, label, coins_won, new_pity)."""
    pity = await db.pool.fetchval(
        "SELECT pulls FROM gacha_pity WHERE guild_id=$1 AND user_id=$2", gid, uid
    ) or 0
    rarity = _roll_rarity(pity)
    new_pity = 0 if rarity in ("legendary","mythic") else pity + 1
    await db.pool.execute("""
        INSERT INTO gacha_pity (guild_id,user_id,pulls) VALUES ($1,$2,$3)
        ON CONFLICT (guild_id,user_id) DO UPDATE SET pulls=$3
    """, gid, uid, new_pity)

    pool = [k for k,v in ITEMS.items() if v.get("rarity") == rarity]
    if not pool: pool = list(ITEMS.keys())
    item_key = random.choice(pool)
    item = ITEMS[item_key]

    if item.get("type") == "coins":
        coins = random.randint(*item.get("coin_range", (100,500)))
        await add_coins(gid, uid, coins)
        return rarity, f"💰 {coins:,} coins", coins, new_pity
    else:
        await add_item(gid, uid, item_key, 1)
        return rarity, item["name"], 0, new_pity

def _board_embed() -> discord.Embed:
    e = discord.Embed(
        title="🎰 Item Gacha",
        description=(
            "**Roll for rare items and coins!**\n\n"
            "🎰 ×1 roll — **500 coins**\n"
            "🎰 ×10 roll — **4,500 coins**\n\n"
            "⬜ Common · 🔵 Rare · 🟣 Epic · 🟡 Legendary · 🌈 Mythic\n"
            f"*Guaranteed Legendary at pull {PITY_AT}*\n\n"
            "Results are private. Items land in `!inventory`."
        ),
        color=C_GACHA,
    )
    return e

class GachaBoardView(discord.ui.View):
    """PERSISTENT board — survives bot restarts (timeout=None)."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎰 Roll ×1  (500 coins)", style=discord.ButtonStyle.primary, custom_id="gacha_roll_1")
    async def roll_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._roll(interaction, 1)

    @discord.ui.button(label="🎰 Roll ×10  (4,500 coins)", style=discord.ButtonStyle.secondary, custom_id="gacha_roll_10")
    async def roll_10(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._roll(interaction, 10)

    async def _roll(self, interaction: discord.Interaction, count: int):
        await interaction.response.defer(ephemeral=True)
        gid = interaction.guild_id
        uid = interaction.user.id
        cost = SINGLE_COST if count == 1 else MULTI_COST

        row = await db.pool.fetchrow("""
            UPDATE economy SET balance=balance-$1
            WHERE guild_id=$2 AND user_id=$3 AND balance>=$1
            RETURNING balance
        """, cost, gid, uid)
        if not row:
            bal = await get_balance(gid, uid)
            return await interaction.followup.send(
                f"❌ Need **{cost:,}** coins but you have **{bal:,}**.", ephemeral=True
            )

        from airi.audit_log import log as audit
        await audit(gid, uid, "gacha_roll", f"{count}x", -cost)
        await log_txn(interaction.client, gid, f"Gacha Roll ×{count}", interaction.user, "System", cost)

        if count == 1:
            rarity, label, coins, pity = await _do_roll(gid, uid)
            result_txt = f"🎁 **{label}**" + (f"\n\n💰 +**{coins:,}** coins" if coins else "\n\n*Check `!inventory` to use your item!*")
            e = discord.Embed(
                title=f"🎰 {RARITY_STAR.get(rarity,'⬜')} {rarity.upper()}",
                description=f"{interaction.user.mention} rolled...\n\n{result_txt}",
                color=RARITY_COLORS.get(rarity, C_GACHA),
                timestamp=datetime.utcnow(),
            )
            e.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
            e.set_footer(text=f"Pity: {pity}/{PITY_AT} · Spent {cost:,} coins")
        else:
            results = []
            for _ in range(10):
                rarity, label, coins, pity = await _do_roll(gid, uid)
                star = RARITY_STAR.get(rarity,'⬜')
                results.append((rarity, f"{star} **{rarity.title()}** — {label}"))
            best = max(results, key=lambda x: list(RARITY_COLORS).index(x[0]))
            e = discord.Embed(
                title="🎰 10× Gacha Pull",
                description="\n".join(r[1] for r in results),
                color=RARITY_COLORS.get(best[0], C_GACHA),
                timestamp=datetime.utcnow(),
            )
            e.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
            e.set_footer(text=f"Pity: {pity}/{PITY_AT} · Spent {cost:,} coins · Items in !inventory")

        await interaction.followup.send(embed=e, ephemeral=True)


class GachaCog(commands.Cog, name="Gacha"):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        """Register persistent view for all gacha boards (existing and future)."""
        self.bot.add_view(GachaBoardView())

    @commands.hybrid_command(name="gachaboard", description="[Admin] Post the gacha board here")
    @commands.has_permissions(manage_channels=True)
    async def gachaboard(self, ctx):
        await ctx.send(embed=_board_embed(), view=GachaBoardView())
        try: await ctx.message.delete()
        except Exception: pass

    @commands.hybrid_command(name="gacha", description="Roll the gacha (or use the board)")
    async def gacha(self, ctx, count: int = 1):
        if count not in (1, 10):
            return await _err(ctx, "Roll `1` or `10` at a time.")
        gacha_ch = await get_gacha_channel(ctx.guild.id)
        if gacha_ch and ctx.channel.id != gacha_ch:
            ch = ctx.guild.get_channel(gacha_ch)
            return await _err(ctx, f"Use the gacha board in {ch.mention if ch else f'<#{gacha_ch}>'}.")
        # Show mini board
        await ctx.send(embed=_board_embed(), view=GachaBoardView(), delete_after=300)