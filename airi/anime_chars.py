# airi/anime_chars.py — Waifu/Husbando gacha with AniList characters, PERSISTENT boards
import discord
from discord.ext import commands
import random
import asyncio
from datetime import datetime, timezone, timedelta
import db
from utils import _err, C_GACHA, C_SOCIAL
from airi.guild_config import check_channel
from airi.economy import get_balance
from airi.constants import RARITY_STYLE, CARD_FLAVOUR, PERSONALITY_TAGS, KAKERA_EMOJI

SINGLE_COST = 300
MULTI_COST  = 2500
PITY_AT     = 60  # increased from 40 — legendary less frequent

# Cache pools per guild+gender (in‑memory)
_char_cache: dict[str, dict] = {}  # key: f"{gid}_{gender}" → {"data": ..., "fetched_at": ...}
CACHE_TTL_SECONDS = 7200

# Rebalanced: legendary/mythic harder to get
RARITY_WEIGHTS = [("common",55),("rare",28),("epic",12),("legendary",4),("mythic",1)]

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
        age = (datetime.now(timezone.utc) - cached["fetched_at"]).total_seconds()
        if age < CACHE_TTL_SECONDS:
            return cached["data"]
    
    from airi.anilist import fetch_characters_for_board
    data = await fetch_characters_for_board(gender)
    _char_cache[cache_key] = {"data": data, "fetched_at": datetime.now(timezone.utc)}
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
    ), color=style["color"], timestamp=datetime.now(timezone.utc))
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
        self._all_chars = []
        self._featured  = set()
        # FIX: encode gender into custom_ids so female and male boards
        # don't share the same ID (which would route all clicks to one view)
        for child in self.children:
            if hasattr(child, "custom_id") and child.custom_id:
                child.custom_id = child.custom_id.replace("waifu_pull_", f"waifu_pull_{gender}_")

    async def _ensure_pool(self):
        """Load the character pool from cache (or re-fetch) into the view's attributes."""
        cache_key = f"{self._gid}_{self._gender}"
        cached = _char_cache.get(cache_key)
        if not cached or (datetime.now(timezone.utc) - cached["fetched_at"]).total_seconds() >= CACHE_TTL_SECONDS:
            from airi.anilist import fetch_characters_for_board
            pool_data = await fetch_characters_for_board(self._gender)
            _char_cache[cache_key] = {"data": pool_data, "fetched_at": datetime.now(timezone.utc)}
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
            if featured_pool and random.random() < 0.10:  # FIX: reduced from 20%
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
                color=color, timestamp=datetime.now(timezone.utc),
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
    _char_cache[cache_key] = {"data": pool_data, "fetched_at": datetime.now(timezone.utc)}

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
        # No channel restriction – works anywhere
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

        # Use CardNavView (unified view with boost/sell/give/filter)
        view = CardNavView(cards, uid, gid, target, ctx.bot)
        view._page = 0
        view._update_btns()
        await ctx.send(embed=view.build_embed(), view=view)

    @commands.hybrid_command(name="waifuinfo", aliases=["wcard"], description="View a waifu card by ID")
    async def waifuinfo(self, ctx, card_id: int):
        # No channel restriction
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

    @commands.hybrid_command(name="cardmarket", aliases=["cmarket", "cm"], description="Browse the card marketplace")
    async def cardmarket(self, ctx):
        """Browse active card listings."""
        gid = ctx.guild.id
        rows = await db.pool.fetch("""
            SELECT cm.*, aw.char_name, aw.rarity, aw.series, aw.char_image
            FROM card_market cm JOIN anime_waifus aw ON aw.id = cm.card_id
            WHERE cm.guild_id=$1 AND cm.status='active'
            ORDER BY cm.listed_at DESC LIMIT 20
        """, gid)
        if not rows:
            return await ctx.send(embed=discord.Embed(
                description="No active card listings. Use `!waifucollection` → Sell to list a card!",
                color=C_SOCIAL,
            ))
        from airi.constants import RARITY_STYLE
        e = discord.Embed(title="🎴 Card Marketplace", color=C_SOCIAL)
        for r in rows[:10]:
            style = RARITY_STYLE.get(r["rarity"], RARITY_STYLE["common"])
            seller = ctx.guild.get_member(r["seller_id"])
            sname  = seller.display_name if seller else f"ID {r['seller_id']}"
            e.add_field(
                name=f"{style['glow']} #{r['id']} — {r['char_name']}",
                value=(
                    f"*{r['series']}* · **{r['rarity'].title()}** {style['hue']}\n"
                    f"💰 {r['price']:,} coins"
                    + (f" · Bid from {r['min_bid']:,}" if r['min_bid'] else "")
                    + f"\nSeller: {sname}"
                ),
                inline=True,
            )
        e.set_footer(text=f"Showing {min(len(rows), 10)} active listings")
        await ctx.send(embed=e)

    @commands.hybrid_command(name="waifulb", aliases=["waifutop"], description="Waifu card collection leaderboard")
    async def waifulb(self, ctx):
        # No channel restriction
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

# ─────────────────────────────────────────────────────────────────────
# Card navigation view (inline from boards or !waifucollection)
# ─────────────────────────────────────────────────────────────────────
from airi.constants import RARITY_STYLE

class CardNavView(discord.ui.View):
    """
    Paginated card browser with:
    • Prev / Next navigation
    • Boost button (per-card XP/kakera boost)
    • Sell button (list on card market)
    • Give button (transfer card)
    • Back button to return to calling view
    """
    def __init__(self, cards: list[dict], owner_id: int, gid: int,
                 owner: discord.Member, bot, page: int = 0):
        super().__init__(timeout=300)
        self._cards    = cards
        self._owner_id = owner_id
        self._gid      = gid
        self._owner    = owner
        self._bot      = bot
        self._page     = page
        self._update_btns()

    def _update_btns(self):
        self.prev_btn.disabled = self._page == 0
        self.next_btn.disabled = self._page == len(self._cards) - 1
        # Only owner can boost/sell/give
        is_owner = True  # enforced in callbacks

    def build_embed(self) -> discord.Embed:
        c = self._cards[self._page]
        rarity = c.get("rarity", "common")
        style  = RARITY_STYLE.get(rarity, RARITY_STYLE["common"])
        gender = c.get("gender", "female")
        type_lbl = "🌸 Waifu" if gender == "female" else "⚔️ Husbando"
        boosted = c.get("boosted_until")
        boost_txt = ""
        if boosted and boosted > datetime.now(timezone.utc):
            boost_txt = f"\n⚡ **Boosted** until {discord.utils.format_dt(boosted, 'R')}"

        e = discord.Embed(
            title=f"{style['glow']} {style['stars']} {rarity.upper()} {style['glow']}",
            description=(
                f"**{c.get('char_name', 'Unknown')}**\n"
                f"{type_lbl}  ·  *{c.get('series', 'Unknown')}*\n\n"
                f"✨ {style['aura']}  {style['hue']}\n"
                + (f"🎀 {c.get('personality_tag', '')}\n" if c.get("personality_tag") else "")
                + (f"💖 {c.get('favourites', 0):,} favourites\n" if c.get("favourites") else "")
                + boost_txt
            ),
            color=style["color"],
            timestamp=datetime.now(timezone.utc),
        )
        if c.get("char_image"):
            e.set_image(url=c["char_image"])
        e.set_author(name=self._owner.display_name, icon_url=self._owner.display_avatar.url)
        e.set_footer(
            text=f"Card #{c['id']}  ·  {self._page+1}/{len(self._cards)} cards  ·  "
                 f"Affection: {c.get('affection', 0)}"
        )
        return e

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=0)
    async def prev_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._owner_id:
            return await interaction.response.send_message("Not your cards.", ephemeral=True)
        self._page -= 1
        self._update_btns()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=0)
    async def next_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._owner_id:
            return await interaction.response.send_message("Not your cards.", ephemeral=True)
        self._page += 1
        self._update_btns()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="⚡ Boost", style=discord.ButtonStyle.primary, row=1)
    async def boost_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._owner_id:
            return await interaction.response.send_message("Not your cards.", ephemeral=True)
        c = self._cards[self._page]
        cid = c["id"]
        # Check if already boosted
        now = datetime.now(timezone.utc)
        if c.get("boosted_until") and c["boosted_until"] > now:
            return await interaction.response.send_message(
                f"⚡ This card is already boosted until {discord.utils.format_dt(c['boosted_until'], 'R')}!",
                ephemeral=True,
            )
        # Cost: 200 coins to boost for 24h
        BOOST_COST = 200
        row = await db.pool.fetchrow(
            "UPDATE economy SET balance=balance-$1 WHERE guild_id=$2 AND user_id=$3 AND balance>=$1 RETURNING balance",
            BOOST_COST, self._gid, self._owner_id,
        )
        if not row:
            return await interaction.response.send_message(
                f"❌ Need **{BOOST_COST}** coins to boost this card.", ephemeral=True
            )
        until = now + timedelta(hours=24)
        await db.pool.execute(
            "UPDATE anime_waifus SET boosted_until=$1 WHERE id=$2", until, cid
        )
        # Update local cache
        self._cards[self._page]["boosted_until"] = until
        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self,
        )
        await interaction.followup.send(
            f"⚡ **{c.get('char_name')}** boosted for 24h! "
            f"This card now earns +50% kakera on interactions. (-{BOOST_COST} 🪙)",
            ephemeral=True,
        )

    @discord.ui.button(label="💰 Sell", style=discord.ButtonStyle.success, row=1)
    async def sell_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._owner_id:
            return await interaction.response.send_message("Not your cards.", ephemeral=True)
        c = self._cards[self._page]

        class SellModal(discord.ui.Modal, title=f"Sell {c.get('char_name', 'Card')}"):
            price_in = discord.ui.TextInput(
                label="Buyout price (coins)", placeholder="e.g. 5000", required=True
            )
            bid_in = discord.ui.TextInput(
                label="Starting bid (optional, leave blank = fixed price)",
                placeholder="e.g. 1000", required=False,
            )
            async def on_submit(m_self, inter2):
                await inter2.response.defer(ephemeral=True)
                raw_p = m_self.price_in.value.strip().replace(",", "")
                raw_b = m_self.bid_in.value.strip().replace(",", "")
                if not raw_p.isdigit():
                    return await inter2.followup.send("❌ Invalid price.", ephemeral=True)
                price = int(raw_p)
                min_bid = int(raw_b) if raw_b.isdigit() else None
                if min_bid and min_bid >= price:
                    return await inter2.followup.send("❌ Starting bid must be less than price.", ephemeral=True)
                await _do_card_sell(inter2, self._gid, self._owner_id, c, price, min_bid)

        await interaction.response.send_modal(SellModal())

    @discord.ui.button(label="🎁 Give", style=discord.ButtonStyle.secondary, row=1)
    async def give_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._owner_id:
            return await interaction.response.send_message("Not your cards.", ephemeral=True)
        c = self._cards[self._page]
        sel = discord.ui.UserSelect(placeholder="Give this card to…")
        async def sel_cb(i2: discord.Interaction):
            rec = sel.values[0]
            if rec.id == self._owner_id or rec.bot:
                return await i2.response.send_message("❌ Invalid target.", ephemeral=True)
            await db.pool.execute(
                "UPDATE anime_waifus SET owner_id=$1 WHERE id=$2 AND owner_id=$3",
                rec.id, c["id"], self._owner_id,
            )
            del self._cards[self._page]
            self._page = max(0, self._page - 1)
            for item in gv.children: item.disabled = True
            await i2.response.edit_message(
                content=f"✅ **{c.get('char_name')}** given to {rec.mention}!", view=gv
            )
            if self._cards:
                self._update_btns()
                await i2.followup.send(embed=self.build_embed(), view=self, ephemeral=True)
        sel.callback = sel_cb
        class gv(discord.ui.View):
            def __init__(gv_self): super().__init__(timeout=60); gv_self.add_item(sel)
        await interaction.response.send_message("Give card to:", view=gv(), ephemeral=True)

    @discord.ui.button(label="🔍 Filter", style=discord.ButtonStyle.secondary, row=2)
    async def filter_btn(self, interaction: discord.Interaction, btn):
        """Jump to a specific character by name."""
        if interaction.user.id != self._owner_id:
            return await interaction.response.send_message("Not your cards.", ephemeral=True)

        nav = self  # capture CardNavView reference for closure
        class FilterModal(discord.ui.Modal, title="Find a Card"):
            name_in = discord.ui.TextInput(
                label="Character name (partial OK)",
                placeholder="e.g. Rem, Mikasa…",
                required=True, max_length=50,
            )
            async def on_submit(m_self, inter2):
                await inter2.response.defer(ephemeral=True)
                query = m_self.name_in.value.strip().lower()
                matches = [i for i, card in enumerate(nav._cards)
                           if query in card.get("char_name", "").lower()]
                if not matches:
                    return await inter2.followup.send(
                        f"❌ No card matching '{m_self.name_in.value}' found.", ephemeral=True
                    )
                nav._page = matches[0]
                nav._update_btns()
                await inter2.followup.send(
                    embed=nav.build_embed(), view=nav, ephemeral=True
                )

        await interaction.response.send_modal(FilterModal())


async def _do_card_sell(interaction: discord.Interaction, gid: int, seller_id: int,
                         card: dict, price: int, min_bid: int | None):
    """List a card on the card_market table."""
    # Verify ownership
    row = await db.pool.fetchrow(
        "SELECT id FROM anime_waifus WHERE id=$1 AND owner_id=$2 AND guild_id=$3",
        card["id"], seller_id, gid,
    )
    if not row:
        return await interaction.followup.send("❌ Card not found or not yours.", ephemeral=True)
    # Check not already listed
    existing = await db.pool.fetchrow(
        "SELECT id FROM card_market WHERE card_id=$1 AND status='active'", card["id"]
    )
    if existing:
        return await interaction.followup.send("❌ This card is already listed.", ephemeral=True)
    from datetime import timedelta
    expires = datetime.now(timezone.utc) + timedelta(hours=72)
    listing = await db.pool.fetchrow("""
        INSERT INTO card_market (guild_id, seller_id, card_id, price, min_bid, expires_at)
        VALUES ($1,$2,$3,$4,$5,$6) RETURNING id
    """, gid, seller_id, card["id"], price, min_bid, expires)
    lid = listing["id"]

    # Try to post to cards channel
    from airi.guild_config import get_cards_channel
    cards_ch_id = await get_cards_channel(gid)
    if cards_ch_id:
        ch = interaction.client.get_channel(cards_ch_id)
        if ch:
            e = _card_listing_embed(card, interaction.user, price, min_bid, lid)
            view = CardListingView(lid, seller_id, price, min_bid)
            msg = await ch.send(embed=e, view=view)
            await db.pool.execute(
                "UPDATE card_market SET channel_id=$1, message_id=$2 WHERE id=$3",
                ch.id, msg.id, lid,
            )
            await interaction.followup.send(
                f"✅ **{card.get('char_name')}** listed in {ch.mention} for **{price:,}** coins!",
                ephemeral=True,
            )
            return
    await interaction.followup.send(
        f"✅ **{card.get('char_name')}** listed for **{price:,}** coins! (ID #{lid})\n"
        f"Set a `#cards` channel with `!config` to display listings publicly.",
        ephemeral=True,
    )


def _card_listing_embed(card: dict, seller: discord.Member, price: int,
                         min_bid: int | None, lid: int) -> discord.Embed:
    from airi.constants import RARITY_STYLE
    rarity = card.get("rarity", "common")
    style  = RARITY_STYLE.get(rarity, RARITY_STYLE["common"])
    e = discord.Embed(
        title=f"{style['glow']} #{lid} — {card.get('char_name', 'Unknown Card')}",
        description=(
            f"*{card.get('series', 'Unknown')}*\n"
            f"Rarity: **{rarity.title()}** {style['hue']}\n\n"
            f"💰 **Price:** {price:,} coins"
            + (f"\n🎯 **Starting bid:** {min_bid:,} coins" if min_bid else "")
            + f"\n\n👤 Seller: {seller.mention}"
        ),
        color=style["color"],
    )
    if card.get("char_image"):
        e.set_thumbnail(url=card["char_image"])
    e.set_footer(text=f"Card #{card.get('id')} · Listing #{lid} · Expires in 72h")
    return e


class CardListingView(discord.ui.View):
    """Persistent buy/bid view for a card market listing."""
    def __init__(self, lid: int, seller_id: int, price: int, min_bid: int | None):
        super().__init__(timeout=None)
        self._lid      = lid
        self._seller   = seller_id
        self._price    = price
        self._has_bid  = min_bid is not None
        for child in self.children:
            if hasattr(child, "custom_id") and child.custom_id:
                child.custom_id = f"card_listing_{lid}_{child.custom_id}"

    @discord.ui.button(label="💰 Buy Now", style=discord.ButtonStyle.success, custom_id="buy")
    async def buy_now(self, interaction: discord.Interaction, btn):
        await interaction.response.defer(ephemeral=True)
        uid = interaction.user.id
        gid = interaction.guild_id
        row = await db.pool.fetchrow(
            "SELECT * FROM card_market WHERE id=$1 AND status='active'", self._lid
        )
        if not row:
            return await interaction.followup.send("❌ Listing no longer active.", ephemeral=True)
        if row["seller_id"] == uid:
            return await interaction.followup.send("❌ Can't buy your own listing.", ephemeral=True)
        paid = await db.pool.fetchrow(
            "UPDATE economy SET balance=balance-$1 WHERE guild_id=$2 AND user_id=$3 AND balance>=$1 RETURNING balance",
            row["price"], gid, uid,
        )
        if not paid:
            return await interaction.followup.send(
                f"❌ Need **{row['price']:,}** coins.", ephemeral=True
            )
        fee    = max(1, int(row["price"] * 0.05))
        payout = row["price"] - fee
        await db.pool.execute(
            "UPDATE economy SET balance=balance+$1 WHERE guild_id=$2 AND user_id=$3",
            payout, gid, row["seller_id"],
        )
        # Transfer card
        await db.pool.execute(
            "UPDATE anime_waifus SET owner_id=$1 WHERE id=$2", uid, row["card_id"]
        )
        await db.pool.execute("UPDATE card_market SET status='sold' WHERE id=$1", self._lid)
        card_row = await db.pool.fetchrow("SELECT char_name FROM anime_waifus WHERE id=$1", row["card_id"])
        cname = card_row["char_name"] if card_row else "Unknown"
        await interaction.followup.send(
            f"✅ You bought **{cname}** for **{row['price']:,}** coins!", ephemeral=True
        )
        from utils import log_txn
        await log_txn(interaction.client, gid, "Card Purchase", interaction.user,
                      interaction.guild.get_member(row["seller_id"]) or row["seller_id"],
                      row["price"], f"Card: {cname}")

    @discord.ui.button(label="🎯 Bid", style=discord.ButtonStyle.primary, custom_id="bid")
    async def bid_now(self, interaction: discord.Interaction, btn):
        if not self._has_bid:
            return await interaction.response.send_message(
                "This listing is fixed-price only.", ephemeral=True
            )
        class BidM(discord.ui.Modal, title="Place a Bid"):
            amount_in = discord.ui.TextInput(label="Bid amount (coins)", required=True)
            async def on_submit(m_self, inter2):
                await inter2.response.defer(ephemeral=True)
                raw = m_self.amount_in.value.strip().replace(",", "")
                if not raw.isdigit():
                    return await inter2.followup.send("❌ Invalid amount.", ephemeral=True)
                amount = int(raw)
                uid = inter2.user.id
                gid = inter2.guild_id
                row = await db.pool.fetchrow(
                    "SELECT * FROM card_market WHERE id=$1 AND status='active'", self._lid
                )
                if not row:
                    return await inter2.followup.send("❌ Listing ended.", ephemeral=True)
                if row["seller_id"] == uid:
                    return await inter2.followup.send("❌ Can't bid on your own card.", ephemeral=True)
                cur = row["current_bid"] or row["min_bid"] or 0
                if amount <= cur:
                    return await inter2.followup.send(f"❌ Must bid more than **{cur:,}**.", ephemeral=True)
                paid = await db.pool.fetchrow(
                    "UPDATE economy SET balance=balance-$1 WHERE guild_id=$2 AND user_id=$3 AND balance>=$1 RETURNING balance",
                    amount, gid, uid,
                )
                if not paid:
                    return await inter2.followup.send(f"❌ Insufficient coins.", ephemeral=True)
                # Refund previous bidder
                if row["current_bidder"] and row["current_bid"]:
                    await db.pool.execute(
                        "UPDATE economy SET balance=balance+$1 WHERE guild_id=$2 AND user_id=$3",
                        row["current_bid"], gid, row["current_bidder"],
                    )
                await db.pool.execute(
                    "UPDATE card_market SET current_bid=$1, current_bidder=$2 WHERE id=$3",
                    amount, uid, self._lid,
                )
                await inter2.followup.send(
                    f"✅ Bid of **{amount:,}** coins placed!", ephemeral=True
                )
        await interaction.response.send_modal(BidM())

    @discord.ui.button(label="🔨 End Listing", style=discord.ButtonStyle.danger, custom_id="end")
    async def end_listing(self, interaction: discord.Interaction, btn):
        await interaction.response.defer(ephemeral=True)
        if interaction.user.id != self._seller and not interaction.user.guild_permissions.manage_guild:
            return await interaction.followup.send("Only the seller can end this listing.", ephemeral=True)
        row = await db.pool.fetchrow(
            "SELECT * FROM card_market WHERE id=$1 AND status='active'", self._lid
        )
        if not row:
            return await interaction.followup.send("❌ Listing not found.", ephemeral=True)
        gid = interaction.guild_id
        if row["current_bidder"] and row["current_bid"]:
            # Award card to highest bidder
            fee    = max(1, int(row["current_bid"] * 0.05))
            payout = row["current_bid"] - fee
            await db.pool.execute(
                "UPDATE economy SET balance=balance+$1 WHERE guild_id=$2 AND user_id=$3",
                payout, gid, row["seller_id"],
            )
            await db.pool.execute(
                "UPDATE anime_waifus SET owner_id=$1 WHERE id=$2",
                row["current_bidder"], row["card_id"],
            )
            winner = interaction.guild.get_member(row["current_bidder"])
            result = f"Sold to {winner.mention if winner else row['current_bidder']} for **{row['current_bid']:,}** coins!"
        else:
            result = "No bids — card returned to seller."
        await db.pool.execute("UPDATE card_market SET status='ended' WHERE id=$1", self._lid)
        await interaction.followup.send(
            embed=discord.Embed(title="🔨 Listing Ended", description=result, color=0x533483),
        )
