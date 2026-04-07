# airi/anime_chars.py — Full gachapon card system with AniList characters
# Waifu (female) + Husbando (male) separate boards, card-style display,
# rarity hue/stars/aura, personality tags, one-user-only for Legendary/Mythic.
import discord
from discord.ext import commands
import random
from datetime import datetime
import db
from utils import _err, C_GACHA, C_SOCIAL
from airi.guild_config import check_channel, get_gacha_channel
from airi.economy import add_coins
from airi.constants import RARITY_STYLE, CARD_FLAVOUR, PERSONALITY_TAGS, KAKERA_EMOJI

SINGLE_COST = 300
MULTI_COST  = 2500
PITY_AT     = 40

RARITY_WEIGHTS = [("common",50),("rare",28),("epic",14),("legendary",6),("mythic",2)]


def _roll_rarity(pity: int) -> str:
    if pity >= PITY_AT: return "legendary"
    total = sum(w for _,w in RARITY_WEIGHTS)
    r     = random.randint(1, total); cum = 0
    for name, w in RARITY_WEIGHTS:
        cum += w
        if r <= cum: return name
    return "common"


def _card_embed(char_name: str, image_url: str, rarity: str,
                series: str, owner: discord.Member, card_id: int = 0,
                gender: str = "female", favourites: int = 0,
                personality: str = "", card_wrap: str = "default") -> discord.Embed:
    """Build a premium gachapon card embed."""
    from airi.constants import CARD_WRAPS
    style = RARITY_STYLE.get(rarity, RARITY_STYLE["common"])
    wrap  = CARD_WRAPS.get(card_wrap, CARD_WRAPS["default"])
    glow  = style["glow"]
    stars = style["stars"]
    aura  = style["aura"]
    hue   = style["hue"]
    flavour = random.choice(CARD_FLAVOUR)
    type_label = "🌸 Waifu" if gender == "female" else "⚔️ Husbando"

    title = f"{glow} {stars} {rarity.upper()} {glow}"
    if rarity == "mythic":
        title = f"{'💫'*3} {stars} ✦ MYTHIC ✦ {stars} {'💫'*3}"

    e = discord.Embed(
        title=title,
        description=(
            f"**{char_name}**\n"
            f"{type_label}  ·  *{series}*\n\n"
            + (f"🎀 {personality}\n" if personality else "")
            + f"✨ {aura}  {hue}\n"
            + (f"💖 Favourites: {favourites:,}\n" if favourites else "")
            + f"\n*\"{flavour}\"*"
        ),
        color=style["color"],
        timestamp=datetime.utcnow(),
    )
    if image_url:
        e.set_image(url=image_url)
    e.set_author(name=owner.display_name, icon_url=owner.display_avatar.url)
    e.set_footer(
        text=f"Card #{card_id}  ·  {owner.display_name}'s collection"
        + (f"  ·  {wrap['emoji']} {wrap['bonus']}" if wrap["bonus"] else "")
    )
    return e


# ── Persistent Board View ─────────────────────────────────────────
class _GachaBoardView(discord.ui.View):
    """Shared board for both waifu and husbando pulls."""

    def __init__(self, gender: str):
        super().__init__(timeout=None)
        self._gender = gender
        label_1  = "🌸 Pull ×1  (300 coins)"  if gender == "female" else "⚔️ Pull ×1  (300 coins)"
        label_10 = "🌸 Pull ×10  (2,500)"     if gender == "female" else "⚔️ Pull ×10  (2,500)"
        cid_1    = f"waifu_pull_1_{gender}"
        cid_10   = f"waifu_pull_10_{gender}"

        b1  = discord.ui.Button(label=label_1,  style=discord.ButtonStyle.primary,   custom_id=cid_1)
        b10 = discord.ui.Button(label=label_10, style=discord.ButtonStyle.secondary, custom_id=cid_10)
        b1.callback  = self._pull_1
        b10.callback = self._pull_10
        self.add_item(b1); self.add_item(b10)

    async def _pull_1(self,  i: discord.Interaction, b): await self._pull(i, 1)
    async def _pull_10(self, i: discord.Interaction, b): await self._pull(i, 10)

    async def _pull(self, interaction: discord.Interaction, count: int):
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
            bal = await db.pool.fetchval("SELECT balance FROM economy WHERE guild_id=$1 AND user_id=$2", gid, uid) or 0
            return await interaction.followup.send(f"❌ Need **{cost:,}** coins, you have **{bal:,}**.", ephemeral=True)

        from airi.audit_log import log as audit
        await audit(gid, uid, "waifu_gacha", f"{count}x {self._gender}", -cost)
        from airi.kakera import add_kakera
        from airi.milestones import check_milestone, update_achievement
        from airi.anilist import fetch_characters, is_char_taken
        from airi.banners import get_active_banners

        banners = await get_active_banners(gid)
        banner_ids = {b["source_id"] for b in banners if b.get("source_id")}

        chars  = await fetch_characters(count=count * 3, gender=self._gender)
        if not chars:
            await add_coins(gid, uid, cost)
            return await interaction.followup.send("❌ Couldn't fetch characters right now. Coins refunded.", ephemeral=True)

        pity = await db.pool.fetchval("SELECT pulls FROM gacha_pity WHERE guild_id=$1 AND user_id=$2", gid, uid) or 0

        results = []
        char_pool = list(chars)

        for _ in range(count):
            # Banner boost: if any banner chars available, 20% chance to pull one
            banner_chars = [c for c in char_pool if c.get("id") in banner_ids]
            if banner_chars and random.random() < 0.20:
                char = random.choice(banner_chars)
                rarity = "legendary"
            else:
                rarity = _roll_rarity(pity)
                pool_f = [c for c in char_pool if c["rarity"] == rarity]
                if not pool_f: pool_f = char_pool
                char = random.choice(pool_f) if pool_f else char_pool[0]

            # Legendary/Mythic: one user only per guild
            if rarity in ("legendary", "mythic") and char.get("id"):
                owner_id = await is_char_taken(gid, char["id"], rarity)
                if owner_id:
                    if owner_id == uid:
                        # Give kakera for duplicate
                        kak = 10 if rarity == "legendary" else 50
                        await add_kakera(gid, uid, kak)
                        results.append({"dup": True, "kakera": kak, "rarity": rarity, "name": char.get("name","?")})
                        pity = 0
                        continue
                    else:
                        # Demote to epic
                        rarity = "epic"

            personality = random.choice(PERSONALITY_TAGS)
            pity = 0 if rarity in ("legendary","mythic") else pity + 1

            db_row = await db.pool.fetchrow("""
                INSERT INTO anime_waifus (guild_id, owner_id, char_name, char_image, rarity,
                    source_id, series, gender, favourites, personality_tag)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING id
            """, gid, uid,
                char.get("name","Unknown"), char.get("image",""),
                rarity, char.get("id"), char.get("series","Unknown"),
                self._gender, char.get("favourites",0), personality
            )

            results.append({
                "id":          db_row["id"],
                "name":        char.get("name","Unknown"),
                "image":       char.get("image",""),
                "rarity":      rarity,
                "series":      char.get("series","Unknown"),
                "gender":      self._gender,
                "favourites":  char.get("favourites",0),
                "personality": personality,
                "dup":         False,
            })
            if char in char_pool: char_pool.remove(char)

        await db.pool.execute("""
            INSERT INTO gacha_pity (guild_id,user_id,pulls) VALUES ($1,$2,$3)
            ON CONFLICT (guild_id,user_id) DO UPDATE SET pulls=$3
        """, gid, uid, pity)

        # Check gacha milestone
        total_rolls = await db.pool.fetchval(
            "SELECT COUNT(*) FROM anime_waifus WHERE guild_id=$1 AND owner_id=$2", gid, uid
        ) or 0
        await check_milestone(None, gid, uid, "gacha", total_rolls, None)
        await update_achievement(None, gid, uid, "roller", count, None)
        await update_achievement(None, gid, uid, "first_claim", 1, None)

        if count == 1 and results:
            r = results[0]
            if r.get("dup"):
                e = discord.Embed(
                    description=f"⭐ Duplicate **{r['name']}** ({r['rarity'].title()})!\n💎 +**{r['kakera']}** {KAKERA_EMOJI} kakera",
                    color=RARITY_STYLE.get(r["rarity"],{}).get("color", 0xaaaaaa)
                )
            else:
                owner = interaction.user
                e = _card_embed(r["name"], r["image"], r["rarity"], r["series"],
                                owner, r["id"], r["gender"], r["favourites"], r["personality"])
            await interaction.followup.send(embed=e, ephemeral=True)
        else:
            # Multi-pull summary
            best = max((r for r in results if not r.get("dup")),
                       key=lambda x: list(RARITY_STYLE.keys()).index(x["rarity"]),
                       default=None)
            lines = []
            for r in results:
                s = RARITY_STYLE.get(r.get("rarity","common"), RARITY_STYLE["common"])
                if r.get("dup"):
                    lines.append(f"🔄 Duplicate {r['name']} → {r['kakera']} {KAKERA_EMOJI}")
                else:
                    lines.append(f"{s['glow']} **{r['rarity'].title()}** — {r['name']}")
            color = RARITY_STYLE.get(best["rarity"] if best else "common",{}).get("color", C_GACHA)
            e = discord.Embed(
                title=f"🎴 {count}× Pull Results",
                description="\n".join(lines),
                color=color, timestamp=datetime.utcnow(),
            )
            if best and best.get("image"):
                e.set_image(url=best["image"])
            e.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
            e.set_footer(text=f"Pity: {pity}/{PITY_AT} · Spent {cost:,} coins · !waifucollection")
            await interaction.followup.send(embed=e, ephemeral=True)


def _board_embed(gender: str) -> discord.Embed:
    t = "🌸 Waifu" if gender == "female" else "⚔️ Husbando"
    e = discord.Embed(
        title=f"{t} Character Gacha",
        description=(
            f"**Pull for unique anime {'waifus' if gender=='female' else 'husbandos'}!**\n\n"
            f"🎴 ×1 pull — **300 coins**\n"
            f"🎴 ×10 pull — **2,500 coins**\n\n"
            f"⬜★☆☆☆☆ Common · 🔵★★☆☆☆ Rare · 🟣★★★☆☆ Epic\n"
            f"🟡★★★★☆ Legendary · 🌈★★★★★ Mythic\n\n"
            f"⚠️ **Legendary & Mythic** characters can only be owned by one user per server!\n"
            f"Duplicates give {KAKERA_EMOJI} kakera instead.\n\n"
            f"*Guaranteed Legendary at pull {PITY_AT}*\n"
            f"Results are private — only you see your pulls.\n\n"
            f"**Commands:** `!waifucollection` · `!waifuinfo <id>` · `!banners`"
        ),
        color=0xff69b4 if gender == "female" else 0x3498db,
    )
    return e


class AnimeCharsCog(commands.Cog, name="AnimeChars"):
    def __init__(self, bot): self.bot = bot

    async def cog_load(self):
        from airi.anilist import ensure_char_columns
        await ensure_char_columns()
        self.bot.add_view(_GachaBoardView("female"))
        self.bot.add_view(_GachaBoardView("male"))

    @commands.command()
    @commands.has_permissions(manage_channels=True)
    async def waifuboard(self, ctx):
        """Post the waifu gacha board (female characters)."""
        msg = await ctx.channel.send(embed=_board_embed("female"), view=_GachaBoardView("female"))
        await ctx.message.delete()

    @commands.command()
    @commands.has_permissions(manage_channels=True)
    async def husbandoboard(self, ctx):
        """Post the husbando gacha board (male characters)."""
        msg = await ctx.channel.send(embed=_board_embed("male"), view=_GachaBoardView("male"))
        await ctx.message.delete()

    @commands.command(aliases=["mycards", "waifu_inv"])
    async def waifucollection(self, ctx, member: discord.Member = None):
        """View your anime card collection with full card display."""
        if not await check_channel(ctx, "gacha"): return
        target = member or ctx.author
        gid, uid = ctx.guild.id, target.id

        rows = await db.pool.fetch("""
            SELECT id, char_name, char_image, rarity, series, gender, favourites,
                   personality_tag, card_wrap, affection, obtained_at
            FROM anime_waifus WHERE guild_id=$1 AND owner_id=$2
            ORDER BY
                CASE rarity WHEN 'mythic' THEN 0 WHEN 'legendary' THEN 1 WHEN 'epic' THEN 2
                            WHEN 'rare' THEN 3 ELSE 4 END,
                obtained_at DESC
        """, gid, uid)

        if not rows:
            whose = "You have" if target == ctx.author else f"{target.display_name} has"
            return await ctx.send(embed=discord.Embed(
                description=f"{whose} no cards yet. Use the waifu/husbando board to pull!",
                color=C_SOCIAL
            ))

        PAGE = 1  # One card per page = full card display
        pages = list(rows)
        current = [0]

        def build_card(idx):
            r = pages[idx]
            return _card_embed(
                r["char_name"], r["char_image"], r["rarity"],
                r["series"] or "Unknown", target, r["id"],
                r["gender"] or "female", r["favourites"] or 0,
                r["personality_tag"] or "", r["card_wrap"] or "default"
            )

        class ColView(discord.ui.View):
            def __init__(self_):
                super().__init__(timeout=180)
                self_._author = ctx.author.id
                self_._upd()

            def _upd(self_):
                self_.prev.disabled = current[0] == 0
                self_.nxt.disabled  = current[0] == len(pages) - 1

            @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
            async def prev(self_, inter, btn):
                if inter.user.id != self_._author: return await inter.response.send_message("Not for you.", ephemeral=True)
                current[0] -= 1; self_._upd()
                await inter.response.edit_message(embed=build_card(current[0]), view=self_)

            @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
            async def nxt(self_, inter, btn):
                if inter.user.id != self_._author: return await inter.response.send_message("Not for you.", ephemeral=True)
                current[0] += 1; self_._upd()
                await inter.response.edit_message(embed=build_card(current[0]), view=self_)

            @discord.ui.button(label=f"🎁 Give", style=discord.ButtonStyle.primary)
            async def give(self_, inter, btn):
                if inter.user.id != uid: return await inter.response.send_message("Not for you.", ephemeral=True)
                cid = pages[current[0]]["id"]
                cname = pages[current[0]]["char_name"]

                class GiveSelect(discord.ui.UserSelect):
                    def __init__(self__): super().__init__(placeholder="Give this card to...")
                    async def callback(self__, inter2):
                        rec = self__.values[0]
                        if rec.id == uid or rec.bot: return await inter2.response.send_message("Invalid.", ephemeral=True)
                        await db.pool.execute("UPDATE anime_waifus SET owner_id=$1 WHERE id=$2", rec.id, cid)
                        for i in self_.view.children: i.disabled = True
                        await inter2.response.edit_message(view=self_.view)
                        await inter2.followup.send(f"✅ **{cname}** given to {rec.mention}!", ephemeral=True)
                        self_.view.stop()

                class GV(discord.ui.View):
                    def __init__(self__): super().__init__(timeout=60); self__.add_item(GiveSelect())
                await inter.response.send_message("Give this card to...", view=GV(), ephemeral=True)

        count_txt = f"Card {current[0]+1}/{len(pages)}"
        e = build_card(0)
        e.set_footer(text=e.footer.text + f"  ·  {count_txt}")
        await ctx.send(embed=e, view=ColView())

    @commands.command(aliases=["wcard"])
    async def waifuinfo(self, ctx, card_id: int):
        """View a specific waifu/husbando card by ID."""
        if not await check_channel(ctx, "gacha"): return
        row = await db.pool.fetchrow(
            "SELECT * FROM anime_waifus WHERE id=$1 AND guild_id=$2", card_id, ctx.guild.id
        )
        if not row:
            return await _err(ctx, f"Card `#{card_id}` not found in this server.")
        owner = ctx.guild.get_member(row["owner_id"]) or ctx.author
        e = _card_embed(
            row["char_name"], row["char_image"], row["rarity"],
            row.get("series","Unknown"), owner, card_id,
            row.get("gender","female"), row.get("favourites",0),
            row.get("personality_tag",""), row.get("card_wrap","default")
        )
        if row["obtained_at"]:
            e.add_field(name="Obtained", value=discord.utils.format_dt(row["obtained_at"],"R"), inline=True)
        await ctx.send(embed=e)

    @commands.command(aliases=["waifutop"])
    async def waifulb(self, ctx):
        """Waifu collection leaderboard — scored by rarity."""
        if not await check_channel(ctx, "social"): return
        gid = ctx.guild.id
        rows = await db.pool.fetch("""
            SELECT owner_id,
                   COUNT(*) AS total,
                   SUM(CASE WHEN rarity='mythic' THEN 100 WHEN rarity='legendary' THEN 20
                            WHEN rarity='epic' THEN 5 WHEN rarity='rare' THEN 2 ELSE 1 END) AS score
            FROM anime_waifus WHERE guild_id=$1
            GROUP BY owner_id ORDER BY score DESC LIMIT 10
        """, gid)
        if not rows:
            return await ctx.send(embed=discord.Embed(description="No cards collected yet!", color=C_SOCIAL))
        medals = ["🥇","🥈","🥉"]
        lines  = []
        for i, r in enumerate(rows):
            m = ctx.guild.get_member(r["owner_id"])
            if not m: continue
            lines.append(f"{medals[i] if i<3 else f'`{i+1}`.'}  **{m.display_name}** — {r['total']} cards · {r['score']:,} pts")
        e = discord.Embed(title="🎴 Card Collection Leaderboard", description="\n".join(lines) or "No data.", color=C_SOCIAL)
        e.set_footer(text="Score: Mythic=100 · Legendary=20 · Epic=5 · Rare=2 · Common=1")
        await ctx.send(embed=e)
