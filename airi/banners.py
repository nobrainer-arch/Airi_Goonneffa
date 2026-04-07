# airi/banners.py — Rotating banner system
# 5 active banners at a time, each lasting 3–7 days randomly.
# Banners boost pull rate for featured characters (legendary/mythic only).
import discord
from discord.ext import commands
import random
import asyncio
from datetime import datetime, timedelta
import db
from utils import C_GACHA

BANNER_COUNT  = 5
MIN_DAYS      = 3
MAX_DAYS      = 7
BOOST_MULT    = 2.0   # featured chars have 2× pull weight


async def _ensure_banner_table():
    await db.pool.execute("""
        CREATE TABLE IF NOT EXISTS banners (
            id          SERIAL PRIMARY KEY,
            guild_id    BIGINT NOT NULL,
            char_name   TEXT NOT NULL,
            char_image  TEXT NOT NULL,
            char_gender TEXT NOT NULL DEFAULT 'female',
            rarity      TEXT NOT NULL,
            series      TEXT DEFAULT 'Unknown',
            source_id   INTEGER,
            boost_mult  FLOAT DEFAULT 2.0,
            starts_at   TIMESTAMP NOT NULL DEFAULT NOW(),
            ends_at     TIMESTAMP NOT NULL,
            is_active   BOOLEAN DEFAULT TRUE
        )
    """)


async def get_active_banners(guild_id: int) -> list[dict]:
    rows = await db.pool.fetch("""
        SELECT * FROM banners
        WHERE guild_id=$1 AND is_active=TRUE AND ends_at > NOW()
        ORDER BY ends_at ASC
    """, guild_id)
    return [dict(r) for r in rows]


async def expire_old_banners():
    """Called from background task. Deactivates expired banners."""
    await db.pool.execute("UPDATE banners SET is_active=FALSE WHERE ends_at <= NOW() AND is_active=TRUE")


async def fill_banners(guild_id: int, bot):
    """Ensure guild has BANNER_COUNT active banners. Fill gaps with random chars."""
    active = await get_active_banners(guild_id)
    needed = BANNER_COUNT - len(active)
    if needed <= 0:
        return

    # Pull from AniList to get high-quality characters for banners
    from airi.anilist import fetch_characters
    gender = random.choice(["female", "male"])
    chars  = await fetch_characters(count=needed * 3, gender=gender)
    # Prefer legendary/mythic for banners
    hi_rar = [c for c in chars if c["rarity"] in ("legendary", "mythic")]
    pool   = hi_rar if len(hi_rar) >= needed else chars

    for i in range(min(needed, len(pool))):
        c = pool[i]
        duration = random.randint(MIN_DAYS, MAX_DAYS)
        ends_at  = datetime.utcnow() + timedelta(days=duration)
        await db.pool.execute("""
            INSERT INTO banners
                (guild_id, char_name, char_image, char_gender, rarity, series, source_id, boost_mult, ends_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        """, guild_id, c["name"], c.get("image", ""), c.get("gender", "female"),
            c["rarity"], c.get("series", "Unknown"), c.get("id"), BOOST_MULT, ends_at)


def _countdown_str(ends_at) -> str:
    delta = ends_at - datetime.utcnow()
    total_s = int(delta.total_seconds())
    if total_s <= 0: return "Ending soon"
    d = total_s // 86400
    h = (total_s % 86400) // 3600
    m = (total_s % 3600) // 60
    if d: return f"{d}d {h}h"
    if h: return f"{h}h {m}m"
    return f"{m}m"


def build_banner_embed(banners: list[dict], guild_name: str) -> discord.Embed:
    from airi.constants import RARITY_STYLE
    e = discord.Embed(
        title="🎌 Active Banners",
        description=(
            f"**{len(banners)}** featured characters with **{BOOST_MULT}× pull rate boost!**\n"
            f"Pull from `!waifuboard` or `!husbandoboard` for a chance at these characters.\n"
        ),
        color=C_GACHA,
    )
    for b in banners:
        style = RARITY_STYLE.get(b["rarity"], RARITY_STYLE["common"])
        e.add_field(
            name=f"{style['glow']} {style['stars']} {b['char_name']}",
            value=(
                f"*{b['series']}*\n"
                f"Rarity: **{b['rarity'].title()}** {style['hue']}\n"
                f"⏰ Ends in **{_countdown_str(b['ends_at'])}**\n"
                f"🎯 {style['aura']}"
            ),
            inline=True,
        )
    if banners:
        e.set_image(url=banners[0]["char_image"])  # Show first banner as preview
    e.set_footer(text=f"{guild_name} · Banners rotate every {MIN_DAYS}–{MAX_DAYS} days")
    return e


class BannersCog(commands.Cog, name="Banners"):
    def __init__(self, bot):
        self.bot = bot
        self._task = None

    async def cog_load(self):
        await _ensure_banner_table()
        self._task = asyncio.create_task(self._banner_loop())

    async def _banner_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await expire_old_banners()
                # Fill banners for all guilds
                guilds = await db.pool.fetch("SELECT DISTINCT guild_id FROM guild_config")
                for row in guilds:
                    await fill_banners(row["guild_id"], self.bot)
            except Exception as e:
                print(f"Banner loop error: {e}")
            await asyncio.sleep(3600)  # Check every hour

    @commands.command(aliases=["banner", "featured"])
    async def banners(self, ctx):
        """Show the currently active gacha banners with countdown timers."""
        gid = ctx.guild.id
        active = await get_active_banners(gid)
        if not active:
            await fill_banners(gid, self.bot)
            active = await get_active_banners(gid)
        if not active:
            return await ctx.send("No banners active right now. Try again shortly!")

        class BannerView(discord.ui.View):
            def __init__(self_, banners_):
                super().__init__(timeout=180)
                self_._banners  = banners_
                self_._current  = 0
                self_._upd()

            def _upd(self_):
                self_.prev.disabled = self_._current == 0
                self_.nxt.disabled  = self_._current == len(self_._banners) - 1

            def _embed(self_) -> discord.Embed:
                b = self_._banners[self_._current]
                from airi.constants import RARITY_STYLE
                style = RARITY_STYLE.get(b["rarity"], RARITY_STYLE["common"])
                e = discord.Embed(
                    title=f"{style['glow']} Banner {self_._current+1}/{len(self_._banners)}: {b['char_name']}",
                    description=(
                        f"*{b['series']}*\n\n"
                        f"{style['hue']} Rarity: **{b['rarity'].title()}**\n"
                        f"✨ {style['stars']}  {style['aura']}\n"
                        f"🚀 Pull rate boost: **{b['boost_mult']}×**\n"
                        f"⏰ Ends in: **{_countdown_str(b['ends_at'])}**"
                    ),
                    color=style["color"],
                )
                if b["char_image"]:
                    e.set_image(url=b["char_image"])
                e.set_footer(text=f"Use !waifuboard or !husbandoboard to pull  ·  {ctx.guild.name}")
                return e

            @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
            async def prev(self_, inter, btn):
                self_._current -= 1; self_._upd()
                await inter.response.edit_message(embed=self_._embed(), view=self_)

            @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
            async def nxt(self_, inter, btn):
                self_._current += 1; self_._upd()
                await inter.response.edit_message(embed=self_._embed(), view=self_)

        v = BannerView(active)
        await ctx.send(embed=v._embed(), view=v)
