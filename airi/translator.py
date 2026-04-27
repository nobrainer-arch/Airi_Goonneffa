# airi/translator.py — Translation + per-user language preference
# Free APIs only (no key needed): Google GTX + MyMemory with fallback
import aiohttp, asyncio, discord
from discord.ext import commands
import db
from utils import C_INFO, C_SUCCESS, C_WARN

LANGUAGES = {
    "en":"English","ja":"Japanese","ko":"Korean","zh-CN":"Chinese (Simplified)",
    "zh-TW":"Chinese (Traditional)","fr":"French","de":"German","es":"Spanish",
    "pt":"Portuguese","it":"Italian","ru":"Russian","ar":"Arabic","hi":"Hindi",
    "th":"Thai","vi":"Vietnamese","id":"Indonesian","tr":"Turkish","nl":"Dutch",
    "pl":"Polish","sv":"Swedish","uk":"Ukrainian","cs":"Czech","ro":"Romanian",
    "fi":"Finnish","hu":"Hungarian","da":"Danish","no":"Norwegian","el":"Greek",
    "he":"Hebrew","ms":"Malay","tl":"Filipino","sw":"Swahili",
}
FLAGS = {
    "en":"🇬🇧","fr":"🇫🇷","de":"🇩🇪","es":"🇪🇸","it":"🇮🇹","pt":"🇵🇹",
    "ja":"🇯🇵","ko":"🇰🇷","zh-CN":"🇨🇳","zh-TW":"🇹🇼","ru":"🇷🇺",
    "ar":"🇸🇦","hi":"🇮🇳","th":"🇹🇭","vi":"🇻🇳","id":"🇮🇩","tr":"🇹🇷",
    "nl":"🇳🇱","pl":"🇵🇱","sv":"🇸🇪","uk":"🇺🇦","cs":"🇨🇿","ro":"🇷🇴",
    "fi":"🇫🇮","hu":"🇭🇺","da":"🇩🇰","no":"🇳🇴","el":"🇬🇷","he":"🇮🇱",
    "ms":"🇲🇾","tl":"🇵🇭","sw":"🇰🇪",
}
_LOOKUP: dict[str,str] = {}
for _c,_n in LANGUAGES.items():
    _LOOKUP[_c.lower()]=_c; _LOOKUP[_n.lower()]=_c
_LOOKUP.update({"chinese":"zh-CN","mandarin":"zh-CN","jp":"ja","kr":"ko",
                "cn":"zh-CN","tw":"zh-TW","tagalog":"tl","espanol":"es"})

def resolve(q:str)->str|None:
    q=q.strip().lower().replace("-","")
    return _LOOKUP.get(q) or _LOOKUP.get(q.replace(" ",""))

# ── Translation backends ───────────────────────────────────────────
async def _google(text:str, tgt:str, src:str="auto")->str|None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://translate.googleapis.com/translate_a/single",
                params={"client":"gtx","sl":src,"tl":tgt,"dt":"t","q":text[:500]},
                headers={"User-Agent":"Mozilla/5.0"},
                timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status==200:
                    d=await r.json(content_type=None)
                    return "".join(seg[0] for seg in d[0] if seg[0])
    except: pass
    return None

async def _mymemory(text:str, tgt:str, src:str="en")->str|None:
    if src=="auto": src="en"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://api.mymemory.translated.net/get",
                params={"q":text[:500],"langpair":f"{src}|{tgt}"},
                headers={"Accept":"application/json"},
                timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status==200:
                    d=await r.json(content_type=None)
                    t=d.get("responseData",{}).get("translatedText","")
                    if t and t!=text.upper(): return t
    except: pass
    return None

async def translate(text:str, tgt:str, src:str="auto")->tuple[str,str]:
    if not text.strip() or tgt in ("en","auto"): return text,"none"
    for fn,name in [(_google,"Google"),(_mymemory,"MyMemory")]:
        r=await fn(text,tgt,src)
        if r and r.strip().lower()!=text.strip().lower(): return r,name
    return text,"failed"

# ── DB ─────────────────────────────────────────────────────────────
async def get_user_lang(gid,uid)->str|None:
    return await db.pool.fetchval(
        "SELECT lang_code FROM user_languages WHERE guild_id=$1 AND user_id=$2",gid,uid)

async def set_user_lang(gid,uid,code:str|None):
    if code:
        await db.pool.execute("""INSERT INTO user_languages(guild_id,user_id,lang_code)
            VALUES($1,$2,$3) ON CONFLICT(guild_id,user_id) DO UPDATE SET lang_code=$3""",gid,uid,code)
    else:
        await db.pool.execute("DELETE FROM user_languages WHERE guild_id=$1 AND user_id=$2",gid,uid)

async def get_server_lang(gid)->str|None:
    return await db.pool.fetchval("SELECT lang_code FROM server_languages WHERE guild_id=$1",gid)

async def set_server_lang(gid,code:str|None):
    if code:
        await db.pool.execute("""INSERT INTO server_languages(guild_id,lang_code)
            VALUES($1,$2) ON CONFLICT(guild_id) DO UPDATE SET lang_code=$2""",gid,code)
    else:
        await db.pool.execute("DELETE FROM server_languages WHERE guild_id=$1",gid)

async def get_lang(gid,uid)->str|None:
    """User pref > server default > None (English)."""
    return await get_user_lang(gid,uid) or await get_server_lang(gid)

async def auto_tr(text:str, gid:int, uid:int)->str:
    lang=await get_lang(gid,uid)
    if not lang or lang=="en": return text
    result,_=await translate(text,lang)
    return result

# ── Language picker UI ─────────────────────────────────────────────
def _build_select_opts(subset:list[tuple[str,str]])->list[discord.SelectOption]:
    return [discord.SelectOption(label=f"{FLAGS.get(c,'🌐')} {n}",value=c) for c,n in subset]

class LangPickerView(discord.ui.View):
    GROUPS = [
        ("🌏 Asian",   ["ja","ko","zh-CN","zh-TW","hi","th","vi","id","ms","tl"]),
        ("🌍 European",["fr","de","es","it","pt","nl","pl","sv","da","no","fi","ru","uk","cs","ro","hu","el","he"]),
        ("🌎 Others",  ["en","ar","tr","sw"]),
    ]
    def __init__(self, ctx, on_confirm, scope="user"):
        super().__init__(timeout=180)
        self._ctx=ctx; self._cb=on_confirm; self._scope=scope; self._sel=None
        self._build()

    def _build(self):
        self.clear_items()
        for i,(label,codes) in enumerate(self.GROUPS):
            pairs=[(c,LANGUAGES[c]) for c in codes if c in LANGUAGES]
            sel=discord.ui.Select(placeholder=label,options=_build_select_opts(pairs),row=i)
            async def cb(inter,s=sel):
                if inter.user.id!=self._ctx.author.id:
                    return await inter.response.send_message("Not for you.",ephemeral=True)
                self._sel=s.values[0]
                name=LANGUAGES.get(self._sel,self._sel)
                self._build_confirm()
                e=discord.Embed(title="🌐 Language Selected",
                    description=f"Selected: **{FLAGS.get(self._sel,'🌐')} {name}**\n\nClick **✅ Confirm** to save, or pick another.",
                    color=C_INFO)
                await inter.response.edit_message(embed=e,view=self)
            sel.callback=cb
            self.add_item(sel)

    def _build_confirm(self):
        for c in [x for x in self.children if isinstance(x,discord.ui.Button)]:
            self.remove_item(c)
        cfm=discord.ui.Button(label="✅ Confirm",style=discord.ButtonStyle.success,row=3)
        rst=discord.ui.Button(label="🔄 Reset to English",style=discord.ButtonStyle.danger,row=3)
        async def cfm_cb(inter): await self._cb(inter,self._sel)
        async def rst_cb(inter): await self._cb(inter,None)
        cfm.callback=cfm_cb; rst.callback=rst_cb
        self.add_item(cfm); self.add_item(rst)

    def home_embed(self)->discord.Embed:
        return discord.Embed(title="🌐 Set Your Language",
            description="Pick from the dropdowns below.\nAiri will respond in your chosen language automatically.",
            color=C_INFO)


class TranslatorCog(commands.Cog, name="Translator"):
    def __init__(self,bot): self.bot=bot

    @commands.hybrid_command(name="translate",aliases=["tr","trans"],
                             description="Translate text to any language")
    async def translate_cmd(self,ctx,language:str,*,text:str):
        """
        Translate text.  Examples:
          /translate japanese Hello world
          /translate fr Good morning!
          !translate ko How are you?
        """
        await ctx.defer()
        code=resolve(language)
        if not code:
            return await ctx.send(embed=discord.Embed(
                description=f"❌ Unknown language: **{language}**\nTry a name like `Japanese` or code like `ja`.\nSee `/langs` for the full list.",
                color=0xe74c3c))
        result,engine=await translate(text,code)
        lang_name=LANGUAGES.get(code,code)
        flag=FLAGS.get(code,"🌐")
        e=discord.Embed(color=C_INFO)
        e.set_author(name=ctx.author.display_name,icon_url=ctx.author.display_avatar.url)
        e.add_field(name="📝 Original",value=text[:400],inline=False)
        e.add_field(name=f"{flag} {lang_name}",
                    value=result[:400] if result!=text else "*(unchanged)*",inline=False)
        e.set_footer(text=f"Engine: {engine}  ·  /setlang to set your default language")
        await ctx.send(embed=e)

    @commands.hybrid_command(name="setlang",aliases=["mylang","langpref","language"],
                             description="Set your preferred language for Airi's responses")
    async def setlang(self,ctx):
        gid,uid=ctx.guild.id,ctx.author.id
        current=await get_user_lang(gid,uid)
        cur_name=LANGUAGES.get(current,"English") if current else "English"
        async def confirm(inter,code):
            await set_user_lang(gid,uid,code)
            name=LANGUAGES.get(code,"English") if code else "English"
            flag=FLAGS.get(code,"🌐") if code else "🇬🇧"
            e=discord.Embed(title="✅ Language Saved!",
                description=f"Your language is now **{flag} {name}**.\n\nAiri will use this language for your responses.",
                color=C_SUCCESS)
            await inter.response.edit_message(embed=e,view=None)
        e=discord.Embed(title="🌐 Your Language",
            description=f"Current: **{FLAGS.get(current,'🌐')} {cur_name}**\n\nPick a new language below:",
            color=C_INFO)
        view=LangPickerView(ctx,confirm,"user")
        await ctx.send(embed=e,view=view)

    @commands.hybrid_command(name="serverlang",aliases=["slang","defaultlang"],
                             description="[Admin] Set the server default language")
    @commands.has_permissions(manage_guild=True)
    async def serverlang(self,ctx):
        gid=ctx.guild.id
        current=await get_server_lang(gid)
        cur_name=LANGUAGES.get(current,"English") if current else "English"
        async def confirm(inter,code):
            await set_server_lang(gid,code)
            name=LANGUAGES.get(code,"English") if code else "English"
            flag=FLAGS.get(code,"🌐") if code else "🇬🇧"
            e=discord.Embed(title="✅ Server Language Set!",
                description=f"Default language → **{flag} {name}**\nUsers can override with `/setlang`.",
                color=C_SUCCESS)
            await inter.response.edit_message(embed=e,view=None)
        e=discord.Embed(title="🌐 Server Language",
            description=f"Current default: **{FLAGS.get(current,'🌐')} {cur_name}**\n\nPick a new default for the whole server:",
            color=C_INFO)
        view=LangPickerView(ctx,confirm,"server")
        await ctx.send(embed=e,view=view)

    @commands.hybrid_command(name="langs",aliases=["langlist","languages"],
                             description="Browse all supported languages")
    async def langs(self,ctx):
        e=discord.Embed(title="🌐 Supported Languages",color=C_INFO)
        for label,codes in LangPickerView.GROUPS:
            lines=[f"{FLAGS.get(c,'🌐')} `{c}` {LANGUAGES[c]}" for c in codes if c in LANGUAGES]
            e.add_field(name=label,value="\n".join(lines),inline=True)
        e.set_footer(text="/translate <lang> <text>  ·  /setlang to set your preference")
        await ctx.send(embed=e)
