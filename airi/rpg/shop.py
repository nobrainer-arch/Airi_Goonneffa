# airi/rpg/shop.py – D&D API shop with pagination
import aiohttp
import discord
from discord.ext import commands
import db
from utils import C_INFO
from airi.economy import get_balance, add_coins

_spell_cache = None
_equipment_cache = None
_spell_details_cache = {}
_equipment_details_cache = {}

def rank_from_level(level):
    return {0:"F",1:"E",2:"D",3:"C",4:"B",5:"A",6:"S",7:"S",8:"S",9:"SS"}.get(level, "C")

def price_from_level(level):
    return 500 + (level * 800)

async def fetch_spells(limit=200):
    global _spell_cache
    if _spell_cache:
        return _spell_cache[:limit]
    spells = []
    url = "https://www.dnd5eapi.co/api/spells"
    async with aiohttp.ClientSession() as session:
        while url:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    spells.extend(data.get("results", []))
                    url = data.get("next")
                else:
                    break
    _spell_cache = spells
    return spells[:limit]

async def fetch_equipment(limit=200):
    global _equipment_cache
    if _equipment_cache:
        return _equipment_cache[:limit]
    items = []
    url = "https://www.dnd5eapi.co/api/equipment"
    async with aiohttp.ClientSession() as session:
        while url:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items.extend(data.get("results", []))
                    url = data.get("next")
                else:
                    break
    _equipment_cache = items
    return items[:limit]

async def get_spell_details(index):
    if index in _spell_details_cache:
        return _spell_details_cache[index]
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://www.dnd5eapi.co/api/spells/{index}") as resp:
            if resp.status == 200:
                data = await resp.json()
                _spell_details_cache[index] = data
                return data
    return None

async def get_equipment_details(index):
    if index in _equipment_details_cache:
        return _equipment_details_cache[index]
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://www.dnd5eapi.co/api/equipment/{index}") as resp:
            if resp.status == 200:
                data = await resp.json()
                _equipment_details_cache[index] = data
                return data
    return None

# ─────────────────────────────────────────────────────────────────
# Spell Shop with Pagination
# ─────────────────────────────────────────────────────────────────
class SpellShopView(discord.ui.View):
    def __init__(self, ctx, spells, page=0):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.spells = spells
        self.page = page
        self.items_per_page = 10
        self.total_pages = max(1, (len(spells) + self.items_per_page - 1) // self.items_per_page)
        self._create_buttons()

    def _create_buttons(self):
        self.prev_btn = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.secondary, custom_id=f"spell_prev_{self.page}")
        self.next_btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary, custom_id=f"spell_next_{self.page}")
        self.prev_btn.callback = self.prev_callback
        self.next_btn.callback = self.next_callback
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= self.total_pages - 1
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)

    async def prev_callback(self, interaction):
        self.page -= 1
        await self._refresh(interaction)

    async def next_callback(self, interaction):
        self.page += 1
        await self._refresh(interaction)

    async def _refresh(self, interaction):
        # Clear and rebuild
        self.clear_items()
        self._create_buttons()
        select = await self._build_select()
        self.add_item(select)
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)
        embed = discord.Embed(
            title="📚 Spell Shop",
            description=f"Page {self.page+1}/{self.total_pages} · Prices based on spell level",
            color=C_INFO
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _build_select(self):
        start = self.page * self.items_per_page
        end = start + self.items_per_page
        items = self.spells[start:end]
        options = []
        for spell in items:
            details = await get_spell_details(spell['index'])
            if details:
                level = details.get('level', 0)
                name = details['name']
                options.append(discord.SelectOption(
                    label=f"{name[:40]} (Lv.{level})",
                    value=spell['index'],
                    description=f"Price: {price_from_level(level)} coins"
                ))
        select = discord.ui.Select(placeholder="Choose a spell to buy...", options=options[:25])
        select.callback = self._buy_spell
        return select

    async def _buy_spell(self, interaction):
        spell_idx = interaction.data["values"][0]
        details = await get_spell_details(spell_idx)
        if not details:
            return await interaction.response.send_message("❌ Spell not found.", ephemeral=True)
        name = details['name']
        level = details.get('level', 0)
        price = price_from_level(level)
        rank = rank_from_level(level)

        owned = await db.pool.fetchval(
            "SELECT 1 FROM rpg_skills WHERE guild_id=$1 AND user_id=$2 AND skill_name=$3",
            self.ctx.guild.id, self.ctx.author.id, name
        )
        if owned:
            return await interaction.response.send_message(f"❌ You already know **{name}**!", ephemeral=True)

        bal = await get_balance(self.ctx.guild.id, self.ctx.author.id)
        if bal < price:
            return await interaction.response.send_message(f"❌ Need **{price}** coins. You have {bal}.", ephemeral=True)

        await add_coins(self.ctx.guild.id, self.ctx.author.id, -price)
        await db.pool.execute(
            "INSERT INTO rpg_skills (guild_id, user_id, skill_name, skill_rank) VALUES ($1,$2,$3,$4)",
            self.ctx.guild.id, self.ctx.author.id, name, rank
        )
        embed = discord.Embed(
            title="✅ Spell Learned!",
            description=f"**{name}** (Level {level} / Rank {rank})\nCost: {price} coins",
            color=0x2ecc71
        )
        embed.add_field(name="Description", value=details.get('desc', ['No description'])[0][:200], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def send(self):
        select = await self._build_select()
        self.add_item(select)
        embed = discord.Embed(
            title="📚 Spell Shop",
            description=f"Page {self.page+1}/{self.total_pages} · Prices based on spell level",
            color=C_INFO
        )
        await self.ctx.send(embed=embed, view=self)

# ─────────────────────────────────────────────────────────────────
# Equipment Shop with Pagination
# ─────────────────────────────────────────────────────────────────
class EquipmentShopView(discord.ui.View):
    def __init__(self, ctx, items, page=0):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.items = items
        self.page = page
        self.items_per_page = 10
        self.total_pages = max(1, (len(items) + self.items_per_page - 1) // self.items_per_page)
        self._create_buttons()

    def _create_buttons(self):
        self.prev_btn = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.secondary, custom_id=f"eq_prev_{self.page}")
        self.next_btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary, custom_id=f"eq_next_{self.page}")
        self.prev_btn.callback = self.prev_callback
        self.next_btn.callback = self.next_callback
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= self.total_pages - 1
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)

    async def prev_callback(self, interaction):
        self.page -= 1
        await self._refresh(interaction)

    async def next_callback(self, interaction):
        self.page += 1
        await self._refresh(interaction)

    async def _refresh(self, interaction):
        self.clear_items()
        self._create_buttons()
        select = await self._build_select()
        self.add_item(select)
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)
        embed = discord.Embed(
            title="🛡️ Equipment Shop",
            description=f"Page {self.page+1}/{self.total_pages} · Prices approximate in coins",
            color=C_INFO
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _build_select(self):
        start = self.page * self.items_per_page
        end = start + self.items_per_page
        items = self.items[start:end]
        options = []
        for eq in items:
            details = await get_equipment_details(eq['index'])
            if details:
                name = details['name']
                cost = details.get('cost', {})
                quantity = cost.get('quantity', 1)
                unit = cost.get('unit', 'gp')
                if unit == 'gp':
                    price = quantity * 100
                elif unit == 'sp':
                    price = quantity * 10
                else:
                    price = quantity
                options.append(discord.SelectOption(
                    label=f"{name[:40]}",
                    value=eq['index'],
                    description=f"~{price} coins"
                ))
        select = discord.ui.Select(placeholder="Choose equipment to buy...", options=options[:25])
        select.callback = self._buy_equipment
        return select

    async def _buy_equipment(self, interaction):
        eq_idx = interaction.data["values"][0]
        details = await get_equipment_details(eq_idx)
        if not details:
            return await interaction.response.send_message("❌ Item not found.", ephemeral=True)
        name = details['name']
        cost = details.get('cost', {})
        quantity = cost.get('quantity', 1)
        unit = cost.get('unit', 'gp')
        if unit == 'gp':
            price = quantity * 100
        elif unit == 'sp':
            price = quantity * 10
        else:
            price = quantity

        category = details.get('equipment_category', {}).get('name', '').lower()
        if 'weapon' in category:
            slot = 'weapon'
        elif 'armor' in category:
            slot = 'armor'
        else:
            slot = 'accessory'

        bal = await get_balance(self.ctx.guild.id, self.ctx.author.id)
        if bal < price:
            return await interaction.response.send_message(f"❌ Need **{price}** coins. You have {bal}.", ephemeral=True)

        await add_coins(self.ctx.guild.id, self.ctx.author.id, -price)
        await db.pool.execute("""
            INSERT INTO rpg_equipment (guild_id, user_id, slot, item_name, item_rank, effect_desc)
            VALUES ($1, $2, $3, $4, 'D', $5)
            ON CONFLICT (guild_id, user_id, slot) DO UPDATE SET item_name = $4, effect_desc = $5
        """, self.ctx.guild.id, self.ctx.author.id, slot, name, f"Bought for {price} coins")

        embed = discord.Embed(title="✅ Equipment Purchased!", description=f"**{name}** (Slot: {slot})\nCost: {price} coins", color=0x2ecc71)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def send(self):
        select = await self._build_select()
        self.add_item(select)
        embed = discord.Embed(
            title="🛡️ Equipment Shop",
            description=f"Page {self.page+1}/{self.total_pages} · Prices approximate in coins",
            color=C_INFO
        )
        await self.ctx.send(embed=embed, view=self)

async def shop_skills(ctx):
    spells = await fetch_spells(200)
    if not spells:
        return await ctx.send("❌ Could not fetch spells from D&D API.", ephemeral=True)
    view = SpellShopView(ctx, spells)
    await view.send()

async def shop_equipment(ctx):
    items = await fetch_equipment(200)
    if not items:
        return await ctx.send("❌ Could not fetch equipment from D&D API.", ephemeral=True)
    view = EquipmentShopView(ctx, items)
    await view.send()