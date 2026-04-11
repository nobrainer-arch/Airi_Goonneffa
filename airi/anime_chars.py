# airi/anime_chars.py — Waifu/Husbando gacha with AniList characters, PERSISTENT boards
import discord
from discord.ext import commands
import random
import asyncio
from datetime import datetime
import db
from utils import _err, C_GACHA, C_SOCIAL
from airi.guild_config import check_channel
from airi.economy import get_balance
from airi.constants import RARITY_STYLE, CARD_FLAVOUR, PERSONALITY_TAGS, KAKERA_EMOJI

SINGLE_COST = 300
MULTI_COST  = 2500
PITY_AT     = 40

# Cache pools per guild+gender (in‑memory)
_char_cache: dict[str, dict] = {}  # key: f"{gid}_{gender}" → {"data": ..., "fetched_at": ...}
CACHE_TTL_SECONDS = 7200

RARITY_WEIGHTS = [("common",50),("rare",28),("epic",14),("legendary",6),("mythic",2)]

def _roll_rarity(pity: int) -> str:
    if pity >= PITY_AT: return "legendary"
    total = sum(w for _,w in RARITY_WEIGHTS)
    r = random.randint(1, total); cum = 0
    for name, w in RARITY_WEIGHTS:
        cum += w
        if r <= cum: return name
    return "common"

async def _get_char_pool(gid: int, gender: str) -> dict:
    """Get or refresh the character pool. Returns the buckets dict from anilist.py."""
    cache_key = f"{gid}_{gender}"
    cached = _char_cache.get(cache_key)
    if cached:
        age = (datetime.utcnow() - cached["fetched_at"]).total_seconds()
        if age < CACHE_TTL_SECONDS:
            return cached["data"]
    
    from airi.anilist import fetch_characters_for_board
    data = await fetch_characters_for_board(gender)
    _char_cache[cache_key] = {"data": data, "fetched_at": datetime.utcnow()}
    return data

def _card_embed(char: dict, owner: discord.Member, card_id: int) -> discord.Embed:
    from airi.constants import CARD_WRAPS
    rarity = char.get("rarity", "common")
    style  = RARITY_STYLE.get(rarity, RARITY_STYLE["common"])
    wrap   = CARD_WRAPS.get(char.get("card_wrap","default"), CARD_WRAPS["default"])
    flavour = random.choice(CARD_FLAVOUR)
    gender  = char.get("gender","female")
    type_lbl = "🌸 Waifu" if gender == "female" else "⚔️ Husbando"
    personality = char.get("personality_tag","")
    glow  = style["glow"]; stars = style["stars"]; aura = style["aura"]; hue = style["hue"]
    
    title = f"{glow} {stars} {rarity.upper()} {glow}"
    if rarity == "mythic":
        title = f"{'💫'*3} {stars} ✦ MYTHIC ✦ {stars} {'💫'*3}"
    
    bio = char.get("bio","")
    bio_line = f"\n📖 *{bio}*" if bio else ""
    e = discord.Embed(title=title, description=(
        f"**{char.get('name','Unknown')}**\n"
        f"{type_lbl}  ·  *{char.get('series','Unknown')}*\n\n"
        + (f"🎀 {personality}\n" if personality else "")
        + f"✨ {aura}  {hue}\n"
        + (f"💖 {char.get('favourites',0):,} favourites\n" if char.get("favourites") else "")
        + bio_line
        + f"\n\n*\"{flavour}\"*"
    ), color=style["color"], timestamp=datetime.utcnow())
    if char.get("image"):
        e.set_image(url=char["image"])
    e.set_author(name=owner.display_name, icon_url=owner.display_avatar.url)
    e.set_footer(text=f"Card #{card_id}  ·  {owner.display_name}'s collection"
        + (f"  ·  {wrap['emoji']} {wrap['bonus']}" if wrap.get("bonus") else ""))
    return e

def _banner_section(all_chars: list[dict]) -> str:
    """Build a text section listing the 7 banner characters (2 mythic + 5 legendary)."""
    mythics   = [c for c in all_chars if c["rarity"] == "mythic"][:2]
    legendaries = [c for c in all_chars if c["rarity"] == "legendary"][:5]
    banners = mythics + legendaries
    if not banners:
        return ""
    lines = ["**✨ Featured Banner Characters:**"]
    for c in banners:
        style = RARITY_STYLE.get(c["rarity"], RARITY_STYLE["common"])
        lines.append(f"{style['glow']} **{c['name']}** — {c['rarity'].title()} {style['hue']} ({c['favourites']:,} favs)")
    lines.append(f"\n*Featured characters have **2× pull rate!***")
    return "\n".join(lines)

def _board_embed(gender: str, pool_data: dict | None = None) -> discord.Embed:
    t = "🌸 Waifu" if gender == "female" else "⚔️ Husbando"
    desc = (
        f"**Pull for unique anime {'waifus' if gender=='female' else 'husbandos'}!**\n\n"
        f"🎴 ×1 pull — **300 coins**\n"
        f"🎴 ×10 pull — **2,500 coins**\n\n"
        f"⬜★☆☆☆☆ Common · 🔵★★☆☆☆ Rare · 🟣★★★☆☆ Epic\n"
        f"🟡★★★★☆ Legendary · 🌈★★★★★ Mythic\n\n"
        f"⚠️ **Legendary & Mythic** are one user only per server!\n"
        f"Duplicates give {KAKERA_EMOJI} kakera.\n"
        f"*Guaranteed Legendary at pull {PITY_AT}*\n\n"
    )
    if pool_data and pool_data.get("all"):
        banner_text = _banner_section(pool_data["all"])
        if banner_text:
            desc += banner_text
    color = 0xff69b4 if gender == "female" else 0x3498db
    return discord.Embed(title=f"{t} Character Gacha", description=desc, color=color)


class WaifuBoardView(discord.ui.View):
    """PERSISTENT board – does NOT store pool_data, fetches from cache on each interaction."""
    def __init__(self, guild_id: int, gender: str):
        super().__init__(timeout=None)
        self._gid    = guild_id
        self._gender = gender
        # These will be populated on each interaction
        self._all_chars = []
        self._featured  = set()

    async def _ensure_pool(self):
        """Load the character pool from cache (or re-fetch) into the view's attributes."""
        cache_key = f"{self._gid}_{self._gender}"
        cached = _char_cache.get(cache_key)
        if not cached or (datetime.utcnow() - cached["fetched_at"]).total_seconds() >= CACHE_TTL_SECONDS:
            from airi.anilist import fetch_characters_for_board
            pool_data = await fetch_characters_for_board(self._gender)
            _char_cache[cache_key] = {"data": pool_data, "fetched_at": datetime.utcnow()}
        else:
            pool_data = cached["data"]
        self._all_chars = pool_data.get("all", [])
        # Featured: mythic + legendary (for 2x boost)
        self._featured = {c["id"] for c in self._all_chars if c.get("id") and c["rarity"] in ("mythic", "legendary")}
        return pool_data

    @discord.ui.button(label="🎴 Pull ×1  (300 coins)", style=discord.ButtonStyle.primary, custom_id="waifu_pull_1")
    async def pull_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._pull(interaction, 1)

    @discord.ui.button(label="🎴 Pull ×10  (2,500 coins)", style=discord.ButtonStyle.secondary, custom_id="waifu_pull_10")
    async def pull_10(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._pull(interaction, 10)

    async def _pull(self, interaction: discord.Interaction, count: int):
        await interaction.response.defer(ephemeral=True)
        gid = interaction.guild_id
        uid = interaction.user.id
        cost = SINGLE_COST if count == 1 else MULTI_COST

        # Ensure pool is loaded
        pool_data = await self._ensure_pool()
        if not self._all_chars:
            return await interaction.followup.send("❌ No characters available right now.", ephemeral=True)

        row = await db.pool.fetchrow("""
            UPDATE economy SET balance=balance-$1
            WHERE guild_id=$2 AND user_id=$3 AND balance>=$1
            RETURNING balance
        """, cost, gid, uid)
        if not row:
            bal = await get_balance(gid, uid)
            return await interaction.followup.send(
                f"❌ Need **{cost:,}** coins, you have **{bal:,}**.", ephemeral=True)

        from airi.kakera import add_kakera
        from airi.milestones import check_milestone, update_achievement

        pity = await db.pool.fetchval(
            "SELECT pulls FROM gacha_pity WHERE guild_id=$1 AND user_id=$2", gid, uid
        ) or 0

        results = []
        char_pool = list(self._all_chars)

        for _ in range(count):
            # 20% chance to pull a featured (banner) character
            featured_pool = [c for c in char_pool if c.get("id") in self._featured]
            if featured_pool and random.random() < 0.20:
                char = random.choice(featured_pool)
                rarity = char["rarity"]
            else:
                rarity = _roll_rarity(pity)
                pool_f = [c for c in char_pool if c["rarity"] == rarity]
                char = random.choice(pool_f if pool_f else char_pool)

            # One-user-only for legendary/mythic
            if rarity in ("legendary","mythic") and char.get("id"):
                existing = await db.pool.fetchrow(
                    "SELECT owner_id FROM anime_waifus WHERE guild_id=$1 AND source_id=$2 AND rarity=$3",
                    gid, char["id"], rarity
                )
                if existing:
                    if existing["owner_id"] == uid:
                        kak = 10 if rarity == "legendary" else 50
                        await add_kakera(gid, uid, kak)
                        results.append({"dup": True, "kakera": kak, "rarity": rarity, "name": char.get("name","?")})
                        pity = 0
                        continue
                    else:
                        rarity = "epic"  # Demote if owned by someone else

            personality = random.choice(PERSONALITY_TAGS)
            pity = 0 if rarity in ("legendary","mythic") else pity + 1

            db_row = await db.pool.fetchrow("""
                INSERT INTO anime_waifus
                    (guild_id, owner_id, char_name, char_image, rarity,
                     source_id, series, gender, favourites, personality_tag)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING id
            """, gid, uid,
                char.get("name","Unknown"), char.get("image",""), rarity,
                char.get("id"), char.get("series","Unknown"),
                self._gender, char.get("favourites",0), personality
            )
            char_copy = dict(char)
            char_copy.update({"rarity": rarity, "personality_tag": personality, "card_wrap": "default"})
            results.append({"id": db_row["id"], "char": char_copy, "dup": False})
            if char in char_pool and len(char_pool) > 1:
                char_pool.remove(char)

        await db.pool.execute("""
            INSERT INTO gacha_pity (guild_id,user_id,pulls) VALUES ($1,$2,$3)
            ON CONFLICT (guild_id,user_id) DO UPDATE SET pulls=$3
        """, gid, uid, pity)

        # Milestones
        total = await db.pool.fetchval(
            "SELECT COUNT(*) FROM anime_waifus WHERE guild_id=$1 AND owner_id=$2", gid, uid
        ) or 0
        await check_milestone(None, gid, uid, "gacha", total, None)
        await update_achievement(None, gid, uid, "roller", count, None)

        # Build response
        real = [r for r in results if not r.get("dup")]
        dups = [r for r in results if r.get("dup")]
        dup_note = ""
        if dups:
            dup_note = f"\n\n🔄 {len(dups)} duplicate(s) → {sum(r['kakera'] for r in dups)} {KAKERA_EMOJI} kakera"

        if count == 1 and real:
            r = real[0]
            e = _card_embed(r["char"], interaction.user, r["id"])
            if dup_note:
                e.description = (e.description or "") + dup_note
        elif count == 1 and not real and dups:
            e = discord.Embed(
                description=f"🔄 Duplicate **{dups[0]['name']}** ({dups[0]['rarity'].title()})!\n"
                            f"💎 +**{dups[0]['kakera']}** {KAKERA_EMOJI} kakera",
                color=RARITY_STYLE.get(dups[0]["rarity"],{}).get("color",0xaaaaaa)
            )
        else:
            best = max(real, key=lambda x: list(RARITY_STYLE.keys()).index(x["char"]["rarity"]), default=None)
            lines = []
            for r in results:
                if r.get("dup"):
                    lines.append(f"🔄 Duplicate {r['name']} → {r['kakera']} {KAKERA_EMOJI}")
                else:
                    s = RARITY_STYLE.get(r["char"]["rarity"], RARITY_STYLE["common"])
                    lines.append(f"{s['glow']} **{r['char']['rarity'].title()}** — {r['char']['name']}")
            color = RARITY_STYLE.get(best["char"]["rarity"] if best else "common",{}).get("color", C_GACHA)
            e = discord.Embed(
                title=f"🎴 {count}× Pull Results",
                description="\n".join(lines),
                color=color, timestamp=datetime.utcnow(),
            )
            if best and best["char"].get("image"):
                e.set_image(url=best["char"]["image"])
            e.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
            e.set_footer(text=f"Pity: {pity}/{PITY_AT} · {cost:,} coins · !waifucollection")

        await interaction.followup.send(embed=e, ephemeral=True)


async def _post_board(ctx, gender: str):
    """Fetch chars (with rate-limit retry), then post board with banners embedded."""
    msg = await ctx.send(f"⏳ Fetching {'waifu' if gender=='female' else 'husbando'} characters...")
    try: await ctx.message.delete()
    except Exception: pass

    from airi.anilist import fetch_characters_for_board
    pool_data = await fetch_characters_for_board(gender)

    if pool_data.get("rate_limited"):
        await msg.edit(content="⏳ Hit AniList rate limit. Waiting 60 seconds then retrying...")
        await asyncio.sleep(61)
        pool_data = await fetch_characters_for_board(gender)

    all_chars = pool_data.get("all", [])
    if not all_chars:
        await msg.edit(content="❌ Could not fetch characters from AniList. Try again later.")
        return

    # Cache for this guild
    cache_key = f"{ctx.guild.id}_{gender}"
    _char_cache[cache_key] = {"data": pool_data, "fetched_at": datetime.utcnow()}

    embed = _board_embed(gender, pool_data)
    view  = WaifuBoardView(ctx.guild.id, gender)   # persistent view, no pool data stored
    await msg.edit(content=None, embed=embed, view=view)


class AnimeCharsCog(commands.Cog, name="AnimeChars"):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        """Register persistent views for both genders (dummy IDs). Discord keeps them alive."""
        self.bot.add_view(WaifuBoardView(0, "female"))
        self.bot.add_view(WaifuBoardView(0, "male"))

    @commands.hybrid_command(name="waifuboard", description="[Admin] Post the waifu (female) gacha board here")
    @commands.has_permissions(manage_channels=True)
    async def waifuboard(self, ctx):
        await _post_board(ctx, "female")

    @commands.hybrid_command(name="husbandoboard", description="[Admin] Post the husbando (male) gacha board here")
    @commands.has_permissions(manage_channels=True)
    async def husbandoboard(self, ctx):
        await _post_board(ctx, "male")

    @commands.hybrid_command(name="waifucollection", aliases=["mycards"], description="Browse your anime card collection")
    async def waifucollection(self, ctx, member: discord.Member = None):
        if not await check_channel(ctx, "gacha"): return
        target = member or ctx.author
        gid, uid = ctx.guild.id, target.id

        rows = await db.pool.fetch("""
            SELECT id, char_name, char_image, rarity, series, gender, favourites,
                   personality_tag, card_wrap, affection, obtained_at
            FROM anime_waifus WHERE guild_id=$1 AND owner_id=$2
            ORDER BY CASE rarity WHEN 'mythic' THEN 0 WHEN 'legendary' THEN 1 WHEN 'epic' THEN 2
                                 WHEN 'rare' THEN 3 ELSE 4 END, obtained_at DESC
        """, gid, uid)

        if not rows:
            whose = "You have" if target == ctx.author else f"{target.display_name} has"
            return await ctx.send(embed=discord.Embed(
                description=f"{whose} no cards yet. Use the waifu/husbando board to pull!", color=C_SOCIAL
            ))

        cards = [dict(r) for r in rows]
        current = [0]

        def build(idx):
            c = cards[idx]
            char_data = {
                "name": c["char_name"], "image": c["char_image"],
                "rarity": c["rarity"],  "series": c["series"] or "Unknown",
                "gender": c["gender"] or "female", "favourites": c["favourites"] or 0,
                "personality_tag": c["personality_tag"] or "", "card_wrap": c["card_wrap"] or "default",
            }
            e = _card_embed(char_data, target, c["id"])
            e.set_footer(text=f"{e.footer.text}  ·  Card {idx+1}/{len(cards)}")
            return e

        class ColView(discord.ui.View):
            def __init__(self_):
                super().__init__(timeout=300)
                self_._upd()
            def _upd(self_):
                self_.prev.disabled = current[0] == 0
                self_.nxt.disabled  = current[0] == len(cards) - 1
            @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
            async def prev(self_, i, b):
                if i.user.id != ctx.author.id: return await i.response.send_message("Not for you.", ephemeral=True)
                current[0] -= 1; self_._upd()
                await i.response.edit_message(embed=build(current[0]), view=self_)
            @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
            async def nxt(self_, i, b):
                if i.user.id != ctx.author.id: return await i.response.send_message("Not for you.", ephemeral=True)
                current[0] += 1; self_._upd()
                await i.response.edit_message(embed=build(current[0]), view=self_)
            @discord.ui.button(label="🎁 Give", style=discord.ButtonStyle.primary)
            async def give(self_, i, b):
                if i.user.id != uid: return await i.response.send_message("Not for you.", ephemeral=True)
                cid = cards[current[0]]["id"]; cname = cards[current[0]]["char_name"]
                sel = discord.ui.UserSelect(placeholder="Give this card to…")
                async def cb(i2):
                    rec = sel.values[0]
                    if rec.id == uid or rec.bot: return await i2.response.send_message("Invalid.", ephemeral=True)
                    await db.pool.execute("UPDATE anime_waifus SET owner_id=$1 WHERE id=$2", rec.id, cid)
                    for btn in self_.children: btn.disabled = True
                    await i2.response.edit_message(view=self_)
                    await i2.followup.send(f"✅ **{cname}** given to {rec.mention}!", ephemeral=True)
                    self_.stop()
                sel.callback = cb
                class GV(discord.ui.View):
                    def __init__(self__): super().__init__(timeout=60); self__.add_item(sel)
                await i.response.send_message("Give to:", view=GV(), ephemeral=True)

        await ctx.send(embed=build(0), view=ColView())

    @commands.hybrid_command(name="waifuinfo", aliases=["wcard"], description="View a waifu card by ID")
    async def waifuinfo(self, ctx, card_id: int):
        if not await check_channel(ctx, "gacha"): return
        row = await db.pool.fetchrow(
            "SELECT * FROM anime_waifus WHERE id=$1 AND guild_id=$2", card_id, ctx.guild.id
        )
        if not row:
            return await _err(ctx, f"Card `#{card_id}` not found.")
        owner = ctx.guild.get_member(row["owner_id"]) or ctx.author
        char_data = {
            "name":row["char_name"],"image":row["char_image"],"rarity":row["rarity"],
            "series":row.get("series","Unknown"),"gender":row.get("gender","female"),
            "favourites":row.get("favourites",0),"personality_tag":row.get("personality_tag",""),
            "card_wrap":row.get("card_wrap","default"),
        }
        e = _card_embed(char_data, owner, card_id)
        if row.get("obtained_at"):
            e.add_field(name="Obtained", value=discord.utils.format_dt(row["obtained_at"],"R"), inline=True)
        await ctx.send(embed=e)

    @commands.hybrid_command(name="waifulb", aliases=["waifutop"], description="Waifu card collection leaderboard")
    async def waifulb(self, ctx):
        if not await check_channel(ctx, "social"): return
        gid = ctx.guild.id
        rows = await db.pool.fetch("""
            SELECT owner_id, COUNT(*) AS total,
                   SUM(CASE WHEN rarity='mythic' THEN 100 WHEN rarity='legendary' THEN 20
                            WHEN rarity='epic' THEN 5 WHEN rarity='rare' THEN 2 ELSE 1 END) AS score
            FROM anime_waifus WHERE guild_id=$1
            GROUP BY owner_id ORDER BY score DESC LIMIT 10
        """, gid)
        if not rows:
            return await ctx.send(embed=discord.Embed(description="No cards collected yet!", color=C_SOCIAL))
        medals = ["🥇","🥈","🥉"]
        lines  = [
            f"{medals[i] if i<3 else f'`{i+1}`'}  **{m.display_name if (m:=ctx.guild.get_member(r['owner_id'])) else r['owner_id']}** — {r['total']} cards · {r['score']:,} pts"
            for i, r in enumerate(rows) if ctx.guild.get_member(r["owner_id"])
        ]
        e = discord.Embed(title="🎴 Card Leaderboard", description="\n".join(lines) or "No data.", color=C_SOCIAL)
        e.set_footer(text="Mythic=100 · Legendary=20 · Epic=5 · Rare=2 · Common=1")
        await ctx.send(embed=e)