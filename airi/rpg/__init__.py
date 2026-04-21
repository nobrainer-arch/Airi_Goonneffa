# airi/rpg/__init__.py — RPG package
# char.py     = unified character module (replaces stats.py + character.py)
# dungeon_final.py = unified dungeon (replaces dungeon.py + dungeon_v2.py)
from .char         import RPGStatsCog
from .dungeon_final import DungeonCog
from .guild_system import GuildSystemCog
from .events       import EventsCog
from .market       import MarketCog
