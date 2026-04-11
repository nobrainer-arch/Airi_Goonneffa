# airi/help_ui.py — Help command with full category dropdown
import discord
from discord.ext import commands
from utils import C_INFO, C_SOCIAL, C_ECONOMY, C_GACHA

PAGES: dict[str, tuple] = {
    "economy": (
        "💰 Economy", C_ECONOMY,
        "Earn and spend coins. `!daily` opens the Economy Panel.",
        [
            ("`!daily`",          "Economy Panel — Daily / Work / Crime with live cooldowns. Also: `!dp` `!earn`"),
            ("`!balance [@user]`","Wallet. [Pay] and [Give] buttons built in. Also: `!bal` `!coins`"),
            ("`!pay @user`",      "Send coins (5% tax, max 10,000). No args → UserSelect then amount modal."),
            ("`!give @user`",     "Tax-free gift (max 1,000). No args → UserSelect then amount modal."),
            ("`!shop`",           "Browse shop — dropdown of items, confirm button."),
            ("`!buy [item]`",     "Buy an item. No item → dropdown. Has item → confirm."),
            ("`!title [name]`",   "Equip a title. No name → dropdown of owned titles."),
            ("`!kakera [@user]`", "Check 💎 kakera balance. Also: `!kak`"),
            ("`!kakeashop`",      "Spend kakera on rare rewards — dropdown. Also: `!kshop`"),
        ],
    ),
    "social": (
        "💕 Social", C_SOCIAL,
        "Profiles, rep, claims, and leaderboards.",
        [
            ("`!profile [@user]`","Full profile embed with Rep/Claim/Waifus buttons."),
            ("`!rep [@user]`",    "Give reputation. No args → UserSelect."),
            ("`!claim [@user]`",  "Claim someone as your waifu (500 coins). No args → UserSelect."),
            ("`!release [@user]`","Release a claimed waifu. No args → dropdown of your claims."),
            ("`!mywaifu [@user]`","Paginated waifu harem with Give buttons. Also: `!harem`"),
            ("`!waifu [@user]`",  "Check waifu status — owner, shield, etc."),
            ("`!nsfwoptout`",     "Toggle NSFW opt-out on yourself."),
            ("`!reloptout`",      "Toggle relationship command opt-out."),
            ("`!leaderboard`",    "Server leaderboard — dropdown to switch: XP/Coins/Rep/Hugs/Kisses/Pats/Marriage/Waifus. Also: `!lb` `!top`"),
        ],
    ),
    "relationships": (
        "💍 Relationships", 0xe91e8c,
        "Dating, marriage, court, and shared accounts.",
        [
            ("`!propose`",        "Propose dating or marriage. Opens type select → UserSelect → dowry modal."),
            ("`!myrel`",          "Your relationship status + [End][Court][Shared] buttons."),
            ("`!endrel`",         "End a relationship. Court subcommand files for divorce."),
            ("`!shared`",         "Shared account — Balance / Deposit / Withdraw tabs."),
            ("`!verdict`",        "Judges only — approve/deny open court cases."),
        ],
    ),
    "gacha": (
        "🎰 Gacha & Cards", C_GACHA,
        "Item gacha + anime character cards with full rarity system.",
        [
            ("Item Gacha",        "`!gachaboard` (mod) — posts Roll ×1 / ×10 board. 5-min view timeout."),
            ("Waifu Board",       "`!waifuboard` (mod) — posts female character board with banners embedded."),
            ("Husbando Board",    "`!husbandoboard` (mod) — posts male character board."),
            ("Banner System",     "2 mythic + 5 legendary featured per board with 2× pull rate!"),
            ("`!waifucollection`","Paginated card collection — full card art, bio, stats, Give button."),
            ("`!waifuinfo <id>`", "Full card view by ID."),
            ("`!waifulb`",        "Card leaderboard by rarity score."),
            ("One-user-only",     "⚠️ Legendary & Mythic are exclusive per server. Duplicates → 💎 kakera."),
        ],
    ),
    "milestones": (
        "🏆 Milestones & Achievements", C_GACHA,
        "Earn coins and kakera from reaching milestones.",
        [
            ("`!milestones [@user]`","Hug/kiss/pat/level/gacha milestone progress. Also: `!ms`"),
            ("`!achieve [@user]`",   "Achievement progress with bars. Also: `!achievements`"),
            ("How to earn",          "Receive hugs, kisses, pats; level up; roll gacha; get married."),
        ],
    ),
    "inventory": (
        "🎒 Inventory", C_ECONOMY,
        "Manage your items.",
        [
            ("`!inventory [@user]`","Paginated inventory with category filter. Use/Sell buttons. Also: `!inv`"),
            ("`!use [item]`",       "Use an item. No args → dropdown of usable items."),
        ],
    ),
    "auction": (
        "🏪 Auction House", C_ECONOMY,
        "Buy and sell items with bidding. Listings post in current channel.",
        [
            ("`!ah sell`",  "Sell an item. No args → item Select → quantity modal → price modal → posts."),
            ("`!ah list`",  "Browse active listings with Bid/Buyout/Cancel buttons."),
            ("`!ah bid`",   "Bid on a listing. No args → listing Select → amount modal."),
            ("`!ah buy`",   "Direct buyout. No args → listing Select → confirm."),
            ("`!ah info`",  "Info on a specific listing."),
        ],
    ),
    "orders": (
        "📦 Order Board", C_ECONOMY,
        "Post buy orders — sellers fulfill for coins.",
        [
            ("`!orderbook`",    "Browse orders with Fulfill button. Also: `!orders`"),
            ("`!order new`",    "Post a buy order — item dropdown → price modal → posts."),
            ("`!order cancel`", "Cancel one of your open orders."),
        ],
    ),
    "business": (
        "🏢 Business", C_ECONOMY,
        "Own and upgrade businesses for passive income.",
        [
            ("`!startbiz`",  "Start a business — type Select → name modal → confirm."),
            ("`!mybiz`",     "Your business — Collect / Upgrade / Hire Manager / Sell buttons."),
            ("`!listbiz`",   "Browse businesses for sale with Buy button."),
        ],
    ),
    "gifs": (
        "🎭 GIF Actions", C_SOCIAL,
        "Anime GIF reactions. All work with `/cmd` `/!cmd` or `airi cmd`.",
        [
            ("SFW actions",   "hug, kiss, pat, cuddle, poke, wave, lick, slap, spank, bite, hi, bye, cry, sad, shrug, peek, watch, lol, bored, rage, sip, shock, punch, kick, kill, handhold, tickle, feed, heal, highfive, clap, stare, wink, smack, tease, nod, sleep, scared, pout, glare, cheeks, splash, spray, throw, tsundere, gaming, baka, bang, cook"),
            ("NSFW actions",  "fap, grabbutts, grabboobs, grind, blowjob, kuni, pussyeat, lickdick, titjob, fuck, dickride, bfuck, anal, bathroomfuck, bondage, cum, 69, threesome, gangbang, feet, finger, fuck_lesbian"),
            ("Pickers",       "All commands: no args → UserSelect. Solo cmds (fap, cry, etc.) have no target."),
            ("Back buttons",  "hug, kiss, pat, poke, bite, wave, lick, slap, spank, cuddle show a Back button. Always different GIF!"),
            ("`!gifsearch`",  "Search Klipy for GIFs — paginated with Lock button."),
            ("`!rpblock`",    "Block/unblock someone from RP actions — button UI."),
        ],
    ),
    "admin": (
        "⚙️ Admin", C_INFO,
        "Server configuration and moderation.",
        [
            ("`!config`",     "Server config panel — channel type dropdown + bulk add/remove."),
            ("`!setup`",      "Setup wizard — channel & role selects."),
            ("`!ignore [cmd]`","Toggle a command on/off in this server only. `!ignored` to list."),
            ("`!gachaboard`", "Post the item gacha board."),
            ("`!waifuboard`", "Post the waifu character board."),
            ("`!husbandoboard`","Post the husbando character board."),
            ("`!warn`",       "Warn a member. No args → UserSelect → reason modal."),
            ("`!timeout`",    "Timeout a member. No args → UserSelect → duration modal."),
            ("`!kick`",       "Kick a member. No args → UserSelect → confirm."),
            ("`!ban`",        "Ban a member. No args → UserSelect → reason modal."),
        ],
    ),
    "other": (
        "🔧 Other", C_INFO,
        "Miscellaneous commands.",
        [
            ("`!profile`",    "Full profile card with action buttons."),
            ("`!rank`",       "XP rank card with progress bar."),
            ("`!avatar`",     "Show avatar with Download/Server Avatar buttons."),
            ("`!gender`",     "Set your gender for GIF text targeting."),
            ("`!afk [reason]`","Set AFK status."),
            ("`!audit`",      "View transaction history."),
            ("`!kakera`",     "Check your 💎 kakera balance."),
            ("`!kakeashop`",  "Spend kakera on rare items."),
            ("`!milestones`", "View your milestone progress."),
            ("`!achieve`",    "View your achievements."),
        ],
    ),
}

C_INFO = 0x3498db


def _build_help_embed(cat: str) -> discord.Embed:
    title, color, desc, fields = PAGES[cat]
    e = discord.Embed(title=title, description=desc, color=color)
    for name, value in fields:
        e.add_field(name=name, value=value, inline=False)
    e.set_footer(text="Use the dropdown to switch category · /cmd or !cmd or airi cmd all work")
    return e


class HelpView(discord.ui.View):
    def __init__(self, current: str = "economy"):
        super().__init__(timeout=300)
        opts = [
            discord.SelectOption(label=v[0][:50], value=k, default=(k==current))
            for k, v in PAGES.items()
        ]
        sel = discord.ui.Select(placeholder="Select a category…", options=opts[:25])
        sel.callback = self._cb
        self.add_item(sel)
        self._sel = sel

    async def _cb(self, interaction: discord.Interaction):
        cat = self._sel.values[0]
        for opt in self._sel.options: opt.default = (opt.value == cat)
        await interaction.response.edit_message(embed=_build_help_embed(cat), view=self)


class HelpCog(commands.Cog, name="Help"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_command(name="help", aliases=["h","bothelp"], description="Show bot help")
    async def help_cmd(self, ctx, *, category: str = "economy"):
        cat = category.lower()
        if cat not in PAGES: cat = "economy"
        await ctx.send(embed=_build_help_embed(cat), view=HelpView(cat))

    @commands.hybrid_command(name="cmds", aliases=["commands","cmdlist"], description="Full command list")
    async def cmds(self, ctx):
        e = discord.Embed(title="📋 All Commands", color=C_INFO,
                          description="Use the dropdown in `!help` for details on each category.\n\n"
                          "All commands work as `/cmd` (slash), `!cmd` (prefix), or `airi cmd`.")
        for k, (title, _, _, _) in PAGES.items():
            e.add_field(name=title, value=f"`!help {k}` for details", inline=True)
        e.set_footer(text="Tip: type 'airi' alone to open help")
        await ctx.send(embed=e)
