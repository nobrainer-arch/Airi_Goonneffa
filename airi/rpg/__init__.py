# airi/rpg/__init__.py — RPG system package
# Folder layout:
#   rpg/engine.py    — combat engine (damage pipeline, effects, speed/reaction)
#   rpg/skills.py    — skill definitions from Kinfang transcript
#   rpg/classes.py   — class definitions, talents, stats
#   rpg/stats.py     — RPGStatsCog (!rpg command panel)
#   rpg/dungeon.py   — DungeonCog (!dungeon command, monster DB)
from .stats import RPGStatsCog
from .dungeon import DungeonCog

from .guild_system import GuildSystemCog

from .events import EventsCog

from .market import MarketCog
