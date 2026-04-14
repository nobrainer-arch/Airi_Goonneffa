# airi/kakera.py — Second currency. Earned from milestones, duplicates, achievements.
# Never earned from daily/work/crime. Spent in kakera shop for rare rewards.
import discord
from discord.ext import commands
import db
from utils import _err, C_GACHA, C_ECONOMY

KAKERA_SHOP: dict[str, dict] = {
    "legendary_ticket": {
        "name": "🎟️ Legendary Ticket",
        "desc": "Guarantees a Legendary on your next gacha roll.",
        "cost": 40,
        "type": "one_time_item",
    },
    "mythic_ticket": {
        "name": "🌟 Mythic Ticket",
        "desc": "Guarantees a Mythic on your next gacha roll.",
        "cost": 150,
        "type": "one_time_item",
    },
    "title_collector": {
        "name": "✨ Title: Collector",
        "desc": "Unlocks the *Collector* title. Shown on all embeds.",
        "cost": 50,
        "type": "title",
    },
    "title_heartbreaker": {
        "name": "💔 Title: Heartbreaker",
        "desc": "Unlocks the *Heartbreaker* title.",
        "cost": 75,
        "type": "title",
    },
    "title_veteran": {
        "name": "🏆 Title: Veteran",
        "desc": "Unlocks the *Veteran* title.",
        "cost": 100,
        "type": "title",
    },
    "pity_reset": {
        "name": "🔄 Pity Reset Token",
        "desc": "Instantly resets your gacha pity counter to 0 (fresh start).",
        "cost": 30,
        "type": "utility",
    },
    "xp_3day_boost": {
        "name": "⚡ XP Boost (3 days)",
        "desc": "Doubles XP gain for 3 days.",
        "cost": 80,
        "type": "boost",
    },
}


async def get_kakera(guild_id: int, user_id: int) -> int:
    row = await db.pool.fetchrow(
        "SELECT kakera FROM economy WHERE guild_id=$1 AND user_id=$2", guild_id, user_id
    )
    return row["kakera"] if row else 0


async def add_kakera(guild_id: int, user_id: int, amount: int) -> int:
    """Add (or remove if negative) kakera. Returns new balance."""
    row = await db.pool.fetchrow("""
        INSERT INTO economy (guild_id, user_id, kakera) VALUES ($1, $2, GREATEST(0, $3))
        ON CONFLICT (guild_id, user_id) DO UPDATE
        SET kakera = GREATEST(0, economy.kakera + $3)
        RETURNING kakera
    """, guild_id, user_id, amount)
    return row["kakera"] if row else 0


class KakeraCog(commands.Cog, name="Kakera"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_command(aliases=["kak", "k"])
    async def kakera(self, ctx, member: discord.Member = None):
        """Check your kakera balance."""
        target = member or ctx.author
        gid, uid = ctx.guild.id, target.id
        kak = await get_kakera(gid, uid)
        e = discord.Embed(
            title=f"💎 {target.display_name}'s Kakera",
            description=f"**{kak:,}** 💎 kakera",
            color=C_GACHA,
        )
        e.set_thumbnail(url=target.display_avatar.url)
        e.set_footer(text="Earn kakera from milestones, duplicate gacha pulls, and achievements.")
        await ctx.send(embed=e)

    @commands.hybrid_command(aliases=["kshop", "kakshop"])
    async def kakeashop(self, ctx):
        """Browse the kakera shop."""
        gid, uid = ctx.guild.id, ctx.author.id
        bal = await get_kakera(gid, uid)

        class KShopSelect(discord.ui.Select):
            def __init__(self_):
                options = [
                    discord.SelectOption(
                        label=f"{v['name'][:50]}",
                        value=k,
                        description=f"{v['cost']} 💎 — {v['desc'][:50]}",
                    )
                    for k, v in KAKERA_SHOP.items()
                ]
                super().__init__(placeholder="Select an item to buy…", options=options[:25])

            async def callback(self_, inter: discord.Interaction):
                if inter.user.id != ctx.author.id:
                    return await inter.response.send_message("Not for you.", ephemeral=True)
                key  = self_.values[0]
                item = KAKERA_SHOP[key]

                class BuyConfirm(discord.ui.View):
                    def __init__(self__): super().__init__(timeout=30)
                    @discord.ui.button(label="✅ Buy", style=discord.ButtonStyle.success)
                    async def buy_btn(self__, inter2, btn):
                        for i in self__.children: i.disabled = True
                        await inter2.response.edit_message(view=self__)
                        await _do_kshop_buy(inter2, ctx.guild.id, inter2.user.id, key)
                        self__.stop()
                    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
                    async def cancel_btn(self__, inter2, btn):
                        for i in self__.children: i.disabled = True
                        await inter2.response.edit_message(content="Cancelled.", view=self__)
                        self__.stop()

                e = discord.Embed(
                    title=f"Buy {item['name']}?",
                    description=f"{item['desc']}\n\n**Cost:** {item['cost']:,} 💎 kakera\n**Your balance:** {bal:,} 💎",
                    color=C_GACHA,
                )
                await inter.response.send_message(embed=e, view=BuyConfirm(), ephemeral=True)

        class KShopView(discord.ui.View):
            def __init__(self_): super().__init__(timeout=120); self_.add_item(KShopSelect())

        e = discord.Embed(
            title="💎 Kakera Shop",
            description=f"Your balance: **{bal:,}** 💎 kakera\n\nSelect an item to preview and buy:",
            color=C_GACHA,
        )
        for k, v in KAKERA_SHOP.items():
            e.add_field(name=f"{v['name']} — {v['cost']} 💎", value=v["desc"], inline=True)
        await ctx.send(embed=e, view=KShopView())


async def _do_kshop_buy(interaction: discord.Interaction, guild_id: int, user_id: int, key: str):
    item = KAKERA_SHOP.get(key)
    if not item:
        return await interaction.followup.send("❌ Unknown item.", ephemeral=True)

    cost = item["cost"]
    bal  = await get_kakera(guild_id, user_id)
    if bal < cost:
        return await interaction.followup.send(
            f"❌ Need **{cost}** 💎 but you have **{bal}** 💎.", ephemeral=True
        )

    itype = item["type"]

    if itype == "title":
        title_name = key.replace("title_", "")
        await db.pool.execute("""
            UPDATE economy SET
                titles = CASE WHEN titles IS NULL THEN ARRAY[$1]::TEXT[]
                              ELSE ARRAY_APPEND(titles, $1) END
            WHERE guild_id=$2 AND user_id=$3
        """, title_name, guild_id, user_id)
        msg = f"✅ Title **{title_name}** unlocked! Equip with `!title {title_name}`."

    elif itype == "boost" and key == "xp_3day_boost":
        from datetime import datetime, timedelta, timezone
        from datetime import timezone as _tz
        until = datetime.now(_tz.utc) + timedelta(days=3)
        await db.pool.execute(
            "UPDATE economy SET xp_boost_until=$1 WHERE guild_id=$2 AND user_id=$3",
            until, guild_id, user_id
        )
        msg = "✅ **XP Boost** active for **3 days**!"

    elif itype == "utility" and key == "pity_reset":
        await db.pool.execute(
            "UPDATE gacha_pity SET pulls=0 WHERE guild_id=$1 AND user_id=$2",
            guild_id, user_id
        )
        msg = "✅ Gacha pity counter reset to 0!"

    elif itype == "one_time_item":
        item_map = {
            "legendary_ticket": "waifu_ticket",
            "mythic_ticket":    "waifu_ticket_3",
        }
        from airi.inventory import add_item
        await add_item(guild_id, user_id, item_map[key], 1)
        msg = f"✅ **{item['name']}** added to your inventory!"
    else:
        msg = "✅ Purchase successful!"

    await add_kakera(guild_id, user_id, -cost)
    await interaction.followup.send(msg, ephemeral=True)
