# airi/banners.py — Rotating banner system
import discord
from discord.ext import commands
import random
import asyncio
from datetime import datetime, timedelta
import db
from utils import C_GACHA

BANNER_COUNT  = 7
MIN_DAYS      = 0.5
MAX_DAYS      = 1
BOOST_MULT    = 2.0

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
    await db.pool.execute("UPDATE banners SET is_active=FALSE WHERE ends_at <= NOW() AND is_active=TRUE")

async def fill_banners(guild_id: int, bot):
    """Ensure guild has BANNER_COUNT active banners. Fill gaps with random chars."""
    active = await get_active_banners(guild_id)
    needed = BANNER_COUNT - len(active)
    if needed <= 0:
        return

    # Pull from AniList to get high-quality characters for banners
    from airi.anilist import fetch_characters_for_board
    gender = random.choice(["female", "male"])
    pool_data = await fetch_characters_for_board(gender)  # returns dict with 'all' list
    chars = pool_data.get("all", [])
    # Prefer legendary/mythic for banners
    hi_rar = [c for c in chars if c["rarity"] in ("legendary", "mythic")]
    pool = hi_rar if len(hi_rar) >= needed else chars

    for i in range(min(needed, len(pool))):
        c = pool[i]
        duration = random.uniform(MIN_DAYS, MAX_DAYS)
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
        e.set_image(url=banners[0]["char_image"])
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
                # Fill banners for all guilds that have the bot
                for guild in self.bot.guilds:
                    try:
                        await fill_banners(guild.id, self.bot)
                    except Exception as e:
                        print(f"Error filling banners for guild {guild.id}: {e}")
            except Exception as e:
                print(f"Banner loop error: {e}")
            await asyncio.sleep(3600)  # Check every hour

    @commands.hybrid_command(aliases=["banner", "featured"])
    async def banners(self, ctx):
        """Show the currently active gacha banners with countdown timers."""
        gid = ctx.guild.id
        active = await get_active_banners(gid)
        if not active:
            # Try to fill immediately
            await fill_banners(gid, self.bot)
            active = await get_active_banners(gid)
        if not active:
            return await ctx.send("No banners active right now. Try again shortly!")

        # Build paginated view
        class BannerView(discord.ui.View):
            def __init__(self, banners):
                super().__init__(timeout=180)
                self.banners = banners
                self.current = 0
                self._update_buttons()

            def _update_buttons(self):
                # Clear existing buttons and re-add with updated states
                self.clear_items()
                prev = discord.ui.Button(
                    label="◀ Prev",
                    style=discord.ButtonStyle.secondary,
                    disabled=(self.current == 0),
                    custom_id="banner_prev"
                )
                nxt = discord.ui.Button(
                    label="Next ▶",
                    style=discord.ButtonStyle.secondary,
                    disabled=(self.current == len(self.banners) - 1),
                    custom_id="banner_next"
                )
                prev.callback = self._prev_callback
                nxt.callback = self._next_callback
                self.add_item(prev)
                self.add_item(nxt)

            async def _prev_callback(self, interaction: discord.Interaction):
                if interaction.user.id != self._ctx_author:  # Need to store author
                    await interaction.response.send_message("Not for you.", ephemeral=True)
                    return
                self.current -= 1
                self._update_buttons()
                await interaction.response.edit_message(embed=self._embed(), view=self)

            async def _next_callback(self, interaction: discord.Interaction):
                if interaction.user.id != self._ctx_author:
                    await interaction.response.send_message("Not for you.", ephemeral=True)
                    return
                self.current += 1
                self._update_buttons()
                await interaction.response.edit_message(embed=self._embed(), view=self)

            def _embed(self):
                b = self.banners[self.current]
                from airi.constants import RARITY_STYLE
                style = RARITY_STYLE.get(b["rarity"], RARITY_STYLE["common"])
                e = discord.Embed(
                    title=f"{style['glow']} Banner {self.current+1}/{len(self.banners)}: {b['char_name']}",
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
                e.set_footer(text=f"Use !waifuboard or !husbandoboard to pull  ·  {self._guild_name}")
                return e

            async def send(self, ctx):
                self._ctx_author = ctx.author.id
                self._guild_name = ctx.guild.name
                await ctx.send(embed=self._embed(), view=self)

        view = BannerView(active)
        view._ctx_author = ctx.author.id
        view._guild_name = ctx.guild.name
        await ctx.send(embed=view._embed(), view=view)