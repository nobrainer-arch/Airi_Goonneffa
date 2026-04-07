# airi/help_ui.py — compact help with Select category menu
import discord
from discord.ext import commands
from utils import C_INFO, C_ECONOMY, C_SOCIAL, C_REL, C_GACHA, C_BUSINESS

# ── Page data (title, color, description, [(field_name, field_value)...]) ──────
PAGES = {
    "overview": (
        "📖 Airi Help",
        C_INFO,
        "Select a category below to see commands.",
        [
            ("💰 Economy",       "Daily coins, balance, pay, work, crime, shop"),
            ("👥 Social",        "Profile, rep, claim/release waifus, leaderboard"),
            ("💘 Relationships", "Propose, hookup, shared account, divorce court"),
            ("🏪 Auction House", "List items, bid, buy, order book"),
            ("🎰 Gacha",         "Roll for items — use the board in gacha channel"),
            ("🏭 Business",      "Start a business, collect income, upgrade"),
            ("🎭 GIF Actions",   "Anime GIF reactions — see full list with !cmds"),
            ("⚙️ Config / Mod",  "!setup, !config, !goonneffa moderation bot"),
        ],
    ),
    "economy": (
        "💰 Economy",
        C_ECONOMY,
        "Earn and spend coins. Works in configured bot channels.",
        [
            ("`!daily`",            "150–350 coins every 22h. Streak bonus up to +200."),
            ("`!balance [@user]`",  "Wallet + last 10 transactions. Aliases: `!bal` `!coins`"),
            ("`!pay @user <amt>`",  "Send coins with 5% tax. Max 10,000 per tx."),
            ("`!give @user <amt>`", "Tax-free gift."),
            ("`!work`",             "200–500 coins. 1h cooldown."),
            ("`!crime`",            "60% win 500–1500 / 40% pay fine. 2h cooldown."),
            ("`!shop`",             "Browse titles, boosts, shield, prenup — dropdown UI."),
            ("`!title [name]`",     "Equip an owned title. Shows on all embeds."),
        ],
    ),
    "social": (
        "👥 Social & Waifus",
        C_SOCIAL,
        "Profiles, rep, and the waifu claim system.",
        [
            ("`!profile [@user]`",   "Full profile card. Redirected if profile channel set."),
            ("`!rank [@user]`",      "XP rank card. Aliases: `!level` `!xp`"),
            ("`!leaderboard`",       "Top 10 with dropdown for XP / Coins / Rep."),
            ("`!rep [@user]`",       "Give +1 rep (picker if no @). 12h cooldown."),
            ("`!claim [@user]`",     "Claim someone as waifu (500 coins). Picker if no @."),
            ("`!mywaifu [@user]`",   "Paginated harem list. Aliases: `!harem`"),
            ("`!release [@user]`",   "Release a waifu — dropdown if no @."),
            ("`!waifu [@user]`",     "See who owns someone and how many they own."),
            ("`!nsfwoptout [out/in]`","Opt out of NSFW targeting."),
            ("`!rpblock @user`",     "Block a user from RP/GIF commands on you."),
            ("`!gender`",            "Set RP gender via buttons."),
        ],
    ),
    "relationships": (
        "💘 Relationships",
        C_REL,
        "Hookups, dating, marriage, and divorce court.",
        [
            ("`!hookup @user <coins>`",     "Paid hookup — no consent needed. Min 200 coins."),
            ("`!propose dating @user`",     "Ask to date — buttons for accept/decline."),
            ("`!propose marriage @user`",   "Propose marriage with optional dowry."),
            ("`!myrel`",                    "View your relationship. Aliases: `!relationship`"),
            ("`!shared balance/deposit/withdraw`", "Married couples' shared account."),
            ("`!endrel`",                   "End hookup/dating instantly."),
            ("`!endrel court <reason>`",    "File for divorce — case posted to court channel."),
            ("`!verdict <id> divorce|dismiss`", "Judge only — rule on a divorce case."),
            ("`!reloptout [out/in]`",       "Opt out of relationship commands."),
        ],
    ),
    "market": (
        "🏪 Auction House & Orders",
        C_GACHA,
        "Trade items via the AH or post buy orders.",
        [
            ("`!inventory`",        "Your items with **Use** and **List in AH** buttons."),
            ("`!ah list`",          "Browse active listings with page buttons."),
            ("AH Listing Buttons",  "Every listing has **Bid** (modal), **Buyout** (confirm), **🔨 Stop** (seller)."),
            ("`!orderbook`",        "Browse buy orders. **Fulfil** / **Post Order** / **Cancel** buttons."),
            ("`!ah info <id>`",     "Jump link to a listing in the AH channel."),
        ],
    ),
"gacha": (
        "🎰 Gacha & Cards",
        C_GACHA,
        "Item gacha + anime character cards. Use the persistent boards in your gacha channel.",
        [
            ("Item Board",          "Press **Roll ×1** (500c) or **Roll ×10** (4,500c). Results private.  to post."),
            ("Waifu Board",         "Press **Pull ×1** (300c) or **Pull ×10** (2,500c). Real anime characters. ."),
            ("Husbando Board",      "Same as waifu but male characters. ."),
            ("`!banners`",          "See the 5 featured characters with countdown timers. Featured chars have 2× pull rate."),
            ("Legendary/Mythic",    "⚠️ These characters are ONE USER ONLY per server. Duplicates give kakera instead."),
            ("`!waifucollection`",  "Browse your cards — full card art, stats, Give button. Aliases: "),
            ("`!waifuinfo <id>`",   "Full card view for any card by ID. Aliases: "),
            ("`!waifulb`",          "Leaderboard scored by rarity: Mythic=100, Legendary=20, Epic=5…"),
            ("`!milestones`",       "Track hug/kiss/pat/level/gacha milestone progress + rewards. Aliases: "),
            ("`!achieve`",          "View achievement progress with bars + rewards. Aliases: "),
        ],
    ),
    "business": (
        "🏭 Business",
        C_BUSINESS,
        "Passive income — start a business and collect over time.",
        [
            ("`!startbiz <name>`",  "Start your business (one per server)."),
            ("`!mybiz`",            "View your business stats and income."),
            ("`!collect`",          "Collect accumulated income."),
            ("`!upgrade`",          "Upgrade your business for more income/capacity."),
            ("`!hire @user`",       "Hire a partner for a bonus."),
            ("`!listbiz`",          "See all businesses in the server."),
        ],
    ),
    "actions": (
        "🎭 GIF Actions",
        C_INFO,
        "Anime GIF reaction commands. Type `!cmds` for the full list.",
        [
            ("How to use",          "`!hug @user` or just `!hug` — shows a recipient picker."),
            ("Back buttons",        "Some actions (hug, kiss, pat...) show a **[X] back** button for the target."),
            ("NSFW actions",        "Only work in the configured NSFW channel. Opt out with `!nsfwoptout`."),
            ("`!gender`",           "Set your RP gender for more relevant action text."),
            ("`!rpblock @user`",    "Block someone from using actions on you."),
            ("`!gifsearch <query>`","Search Klipy for any GIF — returns 8 varied results."),
            ("Aliases",             "Many commands have aliases — all listed in config.py."),
        ],
    ),
    "config": (
        "⚙️ Config & Moderation",
        C_INFO,
        "Server setup and moderation commands.",
        [
            ("`!setup`",                    "GUI wizard — select channels via dropdowns. Re-runnable."),
            ("`!config show`",              "View current channel configuration."),
            ("`!config set <type> #ch`",    "Change a single channel setting."),
            ("`!config add <type> #ch`",    "Add to a multi-channel list."),
            ("`!config judge @role`",       "Set who can rule on divorce cases."),
            ("Goonneffa bot",               "`!goonneffa ban/kick/timeout @user reason`"),
            ("`!chatdelhist [n]`",          "Delete last N bot messages (mod only)."),
        ],
    ),
}

CATEGORY_OPTIONS = [
    discord.SelectOption(label="Overview",         value="overview",      emoji="📖", default=True),
    discord.SelectOption(label="Economy",           value="economy",       emoji="💰"),
    discord.SelectOption(label="Social & Waifus",  value="social",        emoji="👥"),
    discord.SelectOption(label="Relationships",    value="relationships", emoji="💘"),
    discord.SelectOption(label="Auction House",    value="market",        emoji="🏪"),
    discord.SelectOption(label="Gacha",            value="gacha",         emoji="🎰"),
    discord.SelectOption(label="Business",         value="business",      emoji="🏭"),
    discord.SelectOption(label="GIF Actions",      value="actions",       emoji="🎭"),
    discord.SelectOption(label="Config & Mod",     value="config",        emoji="⚙️"),
]


def _build_embed(page_key: str) -> discord.Embed:
    title, color, desc, fields = PAGES[page_key]
    e = discord.Embed(title=title, description=desc, color=color)
    for name, value in fields:
        e.add_field(name=name, value=value, inline=False)
    e.set_footer(text="Use the dropdown to switch category · !cmds for full action list")
    return e


class HelpView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=300)
        self._author = author_id

    @discord.ui.select(placeholder="Select a category...", options=CATEGORY_OPTIONS)
    async def category_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        cat = select.values[0]
        for opt in select.options: opt.default = (opt.value == cat)
        await interaction.response.edit_message(embed=_build_embed(cat), view=self)


class HelpCog(commands.Cog, name="Help"):
    def __init__(self, bot): self.bot = bot

    @commands.command(name="help", aliases=["bothelp", "cmdshelp"])
    async def help_cmd(self, ctx):
        view = HelpView(ctx.author.id)
        await ctx.send(embed=_build_embed("overview"), view=view)
