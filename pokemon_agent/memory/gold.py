"""Pokemon Gold/Silver memory reader.

This reader intentionally starts with the small set of state that makes the
autoplayer reliable: map identity, player coordinates, party/starter progress,
badges, money, and battle state.  It uses the public Gold/Silver RAM map from
Data Crystal and avoids pretending to decode fields we do not yet trust.
"""

from __future__ import annotations

from typing import Any, Dict, List

from pokemon_agent.gameplay.gold_data import ITEM_NAMES, SPECIES_NAMES, item_name, species_name
from pokemon_agent.memory.reader import GameMemoryReader


MAP_NAMES: dict[tuple[int, int], str] = {
    (24, 3): "Route 29",
    (24, 4): "New Bark Town",
    (24, 5): "Elm's Lab",
    (24, 6): "Player's House 1F",
    (24, 7): "Player's House 2F",
    (24, 8): "Neighbor's House",
    (24, 9): "Elm's House",
    (26, 1): "Route 30",
    (26, 2): "Route 31",
    (26, 3): "Cherrygrove City",
    (26, 5): "Cherrygrove Pokemon Center 1F",
    (10, 5): "Violet City",
    (10, 6): "Violet Mart",
    (10, 7): "Violet Gym",
    (10, 10): "Violet Pokemon Center 1F",
}

FACING_NAMES = {
    0x00: "down",
    0x04: "up",
    0x08: "left",
    0x0C: "right",
}

MAX_PLAUSIBLE_WINDOW_STACK_SIZE = 8
MAX_PLAUSIBLE_PARTY_COUNT = 6
MAX_PLAUSIBLE_BAG_COUNT = 20
MAX_PLAUSIBLE_BALL_COUNT = 12

# pokegold WRAM symbols (bank 1, exposed through PyBoy's flat WRAM view):
#   wMoney     = $D573, 3-byte BCD
#   wNumItems  = $D5B7; wItems = $D5B8, MAX_ITEMS * 2 + terminator
#   wNumBalls  = $D5FC; wBalls = $D5FD, MAX_BALLS * 2 + terminator
# A previous decoder used $D5F7 for the Balls pocket, which is inside the
# Key Items pocket and therefore confidently reported zero balls on live Gold.
ADDR_MONEY = 0xD573
ADDR_NUM_ITEMS = 0xD5B7
ADDR_ITEMS = 0xD5B8
ADDR_NUM_BALLS = 0xD5FC
ADDR_BALLS = 0xD5FD

BATTLE_TYPE_NAMES = {
    0x00: "none",
    0x01: "wild",
    0x02: "trainer",
}

BATTLE_MAIN_CURSOR_BY_XY = {
    (1, 1): "fight",
    (2, 1): "pack",
    (1, 2): "pokemon",
    (2, 2): "run",
}

JOHTO_BADGES = ["Zephyr", "Hive", "Plain", "Fog", "Mineral", "Storm", "Glacier", "Rising"]
KANTO_BADGES = ["Boulder", "Cascade", "Thunder", "Rainbow", "Soul", "Marsh", "Volcano", "Earth"]

EVENT_FLAGS_BASE = 0xD7B7
GOLD_STORY_EVENT_FLAGS = {
    "got_pokemon_from_elm": 26,
    "got_cyndaquil_from_elm": 27,
    "got_totodile_from_elm": 28,
    "got_chikorita_from_elm": 29,
    "got_mystery_egg_from_mr_pokemon": 30,
    "gave_mystery_egg_to_elm": 31,
    "got_tm31_mud_slap": 8,
    "dude_talked_to_you": 65,
    "learned_to_catch_pokemon": 66,
    "elm_called_about_stolen_pokemon": 67,
}


class PokemonGoldReader(GameMemoryReader):
    """Focused structured reader for Pokemon Gold/Silver (GBC)."""

    @property
    def game_name(self) -> str:
        return "Pokemon Gold/Silver (GBC)"

    def _u8(self, addr: int) -> int:
        return self.emu.read_u8(addr)

    def _u16be(self, addr: int) -> int:
        return (self._u8(addr) << 8) | self._u8(addr + 1)

    def _u16le(self, addr: int) -> int:
        return self._u8(addr) | (self._u8(addr + 1) << 8)

    def _bcd_bytes_valid(self, addr: int, num_bytes: int) -> bool:
        raw = self.emu.read_range(addr, num_bytes)
        return all(((b >> 4) & 0x0F) <= 9 and (b & 0x0F) <= 9 for b in raw)

    def _money(self) -> int:
        return self.read_bcd(ADDR_MONEY, 3)

    def _money_raw(self) -> list[int]:
        return list(self.emu.read_range(ADDR_MONEY, 3))

    def _badges(self) -> list[str]:
        out: list[str] = []
        johto = self._u8(0xD57C)
        kanto = self._u8(0xD57D)
        for i, name in enumerate(JOHTO_BADGES):
            if johto & (1 << i):
                out.append(name)
        for i, name in enumerate(KANTO_BADGES):
            if kanto & (1 << i):
                out.append(name)
        return out

    def _event_flag(self, index: int) -> bool:
        return bool(self._u8(EVENT_FLAGS_BASE + index // 8) & (1 << (index % 8)))

    def _story_event_flags(self) -> dict[str, bool]:
        return {name: self._event_flag(index) for name, index in GOLD_STORY_EVENT_FLAGS.items()}

    def _status_condition(self, raw: int) -> dict[str, Any]:
        return {
            "raw": raw,
            "sleep_turns": raw & 0x07,
            "poison": bool(raw & 0x08),
            "burn": bool(raw & 0x10),
            "freeze": bool(raw & 0x20),
            "paralysis": bool(raw & 0x40),
            "any": bool(raw & 0x7F),
        }

    def _play_time(self) -> str | None:
        # pokegold symbols: wGameTimeHours=$D1EB, Minutes=$D1ED,
        # Seconds=$D1EE. Hours is a little-endian 16-bit counter.
        hours = self._u16le(0xD1EB)
        if hours > 999:
            big_endian_hours = self._u16be(0xD1EB)
            if big_endian_hours <= 999:
                hours = big_endian_hours
        minutes = self._u8(0xD1ED)
        seconds = self._u8(0xD1EE)
        if minutes >= 60 or seconds >= 60 or hours > 999:
            return None
        return f"{hours}:{minutes:02d}:{seconds:02d}"

    def _map_group(self) -> int:
        return self._u8(0xDA00)

    def _map_number(self) -> int:
        return self._u8(0xDA01)

    def _map_name(self) -> str:
        if self._map_group() == 0 or self._map_number() == 0:
            return "Unknown"
        return MAP_NAMES.get((self._map_group(), self._map_number()), f"Map {self._map_group()}:{self._map_number()}")

    def _map_spec(self, map_group: int, map_number: int) -> Any | None:
        if not map_group or not map_number:
            return None
        try:
            from pokemon_agent.navigation import GOLD_MAP_REGISTRY
        except Exception:
            return None
        return GOLD_MAP_REGISTRY.get((map_group, map_number))

    def _position_metadata(self, map_group: int, map_number: int, raw_x: int, raw_y: int) -> dict[str, Any]:
        map_spec = self._map_spec(map_group, map_number)
        map_known = map_spec is not None
        actual_x = raw_y
        actual_y = raw_x
        width = getattr(map_spec, "tile_width", None) if map_spec is not None else None
        height = getattr(map_spec, "tile_height", None) if map_spec is not None else None
        in_bounds = None
        if width is not None and height is not None:
            in_bounds = 0 <= actual_x < width and 0 <= actual_y < height
        trusted = bool(map_group and map_number and (in_bounds is not False))
        confidence = "high" if map_known and in_bounds is True else "medium" if map_group and map_number else "none"
        return {
            "map_known": map_known,
            "map_width": width,
            "map_height": height,
            "in_bounds": in_bounds,
            "trusted": trusted,
            "confidence": confidence,
            "actual_x": actual_x,
            "actual_y": actual_y,
        }

    def _ui_registers(self) -> dict[str, Any]:
        window_stack_size = self._u8(0xCF81)
        menu_cursor_y = self._u8(0xCFA9)
        menu_cursor_x = self._u8(0xCFAA)
        plausible_window_stack = 0 < window_stack_size <= MAX_PLAUSIBLE_WINDOW_STACK_SIZE
        return {
            "window_stack_size": window_stack_size,
            "window_stack_plausible": plausible_window_stack,
            "menu_cursor": {"x": menu_cursor_x, "y": menu_cursor_y},
            "raw": {
                "wWindowStackSize": window_stack_size,
                "wMenuCursorY": menu_cursor_y,
                "wMenuCursorX": menu_cursor_x,
            },
        }

    def read_player(self) -> Dict[str, Any]:
        map_group = self._map_group()
        map_number = self._map_number()
        x = self._u8(0xDA02)
        y = self._u8(0xDA03)
        facing_raw = self._u8(0xD20C)
        map_id = (map_group << 8) | map_number if map_group and map_number else None
        position = self._position_metadata(map_group, map_number, x, y)
        return {
            "name": None,
            "money": self._money(),
            "money_raw_bcd": self._money_raw(),
            "money_trusted": self._bcd_bytes_valid(ADDR_MONEY, 3),
            "money_source": "pokegold_wMoney_d573_3byte_bcd",
            "badges": self._badges(),
            "badge_count": len(self._badges()),
            "position": {
                "map_id": map_id,
                "map_group": map_group,
                "map_number": map_number,
                "map_name": self._map_name(),
                "raw_x": x if map_id is not None else None,
                "raw_y": y if map_id is not None else None,
                "x": x if map_id is not None else None,
                "y": y if map_id is not None else None,
                "actual_x": position["actual_x"] if map_id is not None else None,
                "actual_y": position["actual_y"] if map_id is not None else None,
                "map_known": position["map_known"],
                "map_width": position["map_width"],
                "map_height": position["map_height"],
                "in_bounds": position["in_bounds"],
                "trusted": position["trusted"],
                "confidence": position["confidence"],
            },
            "facing": FACING_NAMES.get(facing_raw & 0x0C, f"unknown({facing_raw:#04x})"),
            "facing_raw": facing_raw,
            "play_time": self._play_time(),
        }

    def read_party(self) -> List[Dict[str, Any]]:
        count_raw = self._u8(0xDA22)
        if count_raw > MAX_PLAUSIBLE_PARTY_COUNT:
            return []
        count = count_raw
        party: list[dict[str, Any]] = []
        for i in range(count):
            species_id = self._u8(0xDA23 + i)
            base = 0xDA2A + i * 0x30
            hp = self._u16be(base + 0x22)
            max_hp = self._u16be(base + 0x24)
            level = self._u8(base + 0x1F)
            moves_raw = [self._u8(base + 2 + j) for j in range(4)]
            pp_raw = [self._u8(base + 0x17 + j) for j in range(4)]
            status_raw = self._u8(base + 0x20)
            trusted = species_id not in (0x00, 0xFF) and 0 < level <= 100 and max_hp <= 999 and hp <= max_hp
            party.append({
                "slot": i + 1,
                "species_id": species_id,
                "species": species_name(species_id),
                "level": level,
                "hp": hp,
                "max_hp": max_hp,
                "moves": [move for move in moves_raw if move != 0],
                "move_pp_raw": pp_raw,
                "pp": [pp for move, pp in zip(moves_raw, pp_raw) if move != 0],
                "status_raw": status_raw,
                "status_condition": self._status_condition(status_raw),
                "trusted": trusted,
            })
        return party

    def read_bag(self) -> List[Dict[str, Any]]:
        count_raw = self._u8(ADDR_NUM_ITEMS)
        if count_raw > MAX_PLAUSIBLE_BAG_COUNT:
            items: list[dict[str, Any]] = []
        else:
            count = count_raw
            items = []
            for i in range(count):
                item_id = self._u8(ADDR_ITEMS + i * 2)
                qty = self._u8(ADDR_ITEMS + i * 2 + 1)
                if item_id in (0x00, 0xFF):
                    continue
                items.append({
                    "slot": i + 1,
                    "pocket": "items",
                    "item_id": item_id,
                    "item": item_name(item_id),
                    "quantity": qty,
                    "trusted": 0 < qty <= 99,
                })
        items.extend(self.read_ball_pocket())
        return items

    def read_ball_pocket(self) -> List[Dict[str, Any]]:
        count_raw = self._u8(ADDR_NUM_BALLS)
        if count_raw > MAX_PLAUSIBLE_BALL_COUNT:
            return []
        items: list[dict[str, Any]] = []
        for i in range(count_raw):
            item_id = self._u8(ADDR_BALLS + i * 2)
            qty = self._u8(ADDR_BALLS + i * 2 + 1)
            if item_id in (0x00, 0xFF):
                continue
            items.append({
                "slot": i + 1,
                "pocket": "balls",
                "item_id": item_id,
                "item": item_name(item_id),
                "quantity": qty,
                "trusted": 0 < qty <= 99,
            })
        return items

    def read_battle(self) -> Dict[str, Any]:
        battle_type = self._u8(0xD116)
        enemy_species_raw = self._u8(0xD0ED)
        enemy_level = self._u8(0xD0FC)
        enemy_moves = [self._u8(0xD0F1 + i) for i in range(4)]
        enemy_status_raw = self._u8(0xD0FD)
        enemy_hp = self._u16be(0xD0FF)
        enemy_max_hp = self._u16be(0xD101)
        in_battle = battle_type != 0
        level_plausible = 0 < enemy_level <= 100
        species_plausible = enemy_species_raw not in (0x00, 0xFF)
        enemy_species_id = enemy_species_raw if species_plausible else None
        enemy_hp_known = enemy_max_hp > 0
        enemy_hp_trusted = not enemy_hp_known or (enemy_max_hp <= 999 and enemy_hp <= enemy_max_hp)
        if battle_type == 0x00:
            trusted = True
        elif battle_type == 0x01:
            trusted = species_plausible and level_plausible and enemy_hp_trusted
        elif battle_type == 0x02:
            trusted = level_plausible and enemy_hp_trusted
        else:
            trusted = False
        enemy = None
        if in_battle:
            enemy = {
                "species_id": enemy_species_id,
                "species": species_name(enemy_species_id),
                "level": enemy_level if level_plausible else None,
                "moves": [move for move in enemy_moves if move != 0],
                "status_raw": enemy_status_raw,
                "status_condition": self._status_condition(enemy_status_raw),
                "hp": enemy_hp if enemy_hp_known else None,
                "max_hp": enemy_max_hp if enemy_hp_known else None,
                "hp_trusted": enemy_hp_trusted,
            }
            if enemy_hp_known and enemy_max_hp > 0:
                enemy["hp_percent"] = max(0, min(100, round((enemy_hp / enemy_max_hp) * 100)))
        return {
            "in_battle": in_battle,
            "type_id": battle_type,
            "type": BATTLE_TYPE_NAMES.get(battle_type, f"type_{battle_type:#04x}"),
            "battle_mode": BATTLE_TYPE_NAMES.get(battle_type, f"mode_{battle_type:#04x}"),
            "enemy_species_id": enemy_species_id,
            "enemy_species": species_name(enemy_species_id) if in_battle else None,
            "wild_species_id": enemy_species_id if battle_type == 0x01 else None,
            "enemy_level": enemy_level if 0 < enemy_level <= 100 else None,
            "enemy_moves": [move for move in enemy_moves if move != 0],
            "enemy_status_raw": enemy_status_raw,
            "enemy_status_condition": self._status_condition(enemy_status_raw),
            "enemy_hp": enemy_hp if enemy_hp_known else None,
            "enemy_max_hp": enemy_max_hp if enemy_hp_known else None,
            "enemy_hp_trusted": enemy_hp_trusted,
            "enemy": enemy,
            "wild": in_battle and battle_type == 0x01 and species_plausible,
            "trusted": trusted,
            "confidence": "high" if trusted else "none",
            "raw": {
                "wBattleMode": battle_type,
                "wBattleType": battle_type,
                "wTempEnemyMonSpecies": enemy_species_raw,
                "wCurPartyLevel": enemy_level,
                "wEnemyMonMoves": enemy_moves,
                "wEnemyMonStatus": enemy_status_raw,
                "wEnemyMonHP": enemy_hp,
                "wEnemyMonMaxHP": enemy_max_hp,
            },
        }

    def read_dialog(self) -> Dict[str, Any]:
        # wWindowStackSize lives in general menu metadata. In practice this byte
        # is not cleared reliably after some Gold battle/menu paths and can hold
        # unrelated/stale union data (e.g. 138 on an overworld Route 30 screen).
        # Treat only small stack depths as active and expose raw values so callers
        # can diagnose suspicious state without blocking overworld navigation.
        ui = self._ui_registers()
        return {
            "active": ui["window_stack_plausible"],
            "text": None,
            "window_stack_size": ui["window_stack_size"],
            "window_stack_plausible": ui["window_stack_plausible"],
            "menu_cursor": ui["menu_cursor"],
            "raw": ui["raw"],
            "note": "Gold RAM reader treats only plausible menu stack depths as active; visual classifier confirms text boxes.",
        }

    def read_menu(self) -> Dict[str, Any]:
        ui = self._ui_registers()
        battle = self.read_battle()
        cursor = ui["menu_cursor"]
        cursor_label = None
        name = None
        confidence = "none"
        blocked_reason = "not_in_battle"
        if battle["in_battle"]:
            blocked_reason = "implausible_window_stack"
        if battle["in_battle"] and ui["window_stack_plausible"]:
            blocked_reason = "untrusted_battle_ram"
        if battle["in_battle"] and ui["window_stack_plausible"] and battle.get("trusted") is True and battle.get("type_id") in {1, 2}:
            blocked_reason = "unknown_battle_cursor"
            cursor_label = BATTLE_MAIN_CURSOR_BY_XY.get((cursor["x"], cursor["y"]))
            if cursor_label is not None:
                name = "battle_main"
                confidence = "medium"
                blocked_reason = None
        return {
            "active": confidence != "none",
            "name": name,
            "source": "gold_ram",
            "cursor": cursor_label,
            "cursor_xy": cursor,
            "confidence": confidence,
            "selected_item_id": None,
            "visible_items": [],
            "bag_pocket": None,
            "needs_stronger_decode": confidence == "none",
            "blocked_reason": blocked_reason,
            "window_stack_size": ui["window_stack_size"],
            "window_stack_plausible": ui["window_stack_plausible"],
            "raw": ui["raw"],
        }

    def read_map_info(self) -> Dict[str, Any]:
        group = self._map_group()
        number = self._map_number()
        raw_x = self._u8(0xDA02)
        raw_y = self._u8(0xDA03)
        position = self._position_metadata(group, number, raw_x, raw_y)
        return {
            "map_id": (group << 8) | number if group and number else None,
            "map_group": group,
            "map_number": number,
            "map_name": self._map_name(),
            "known": position["map_known"],
            "width": position["map_width"],
            "height": position["map_height"],
            "position_in_bounds": position["in_bounds"],
            "trusted": position["trusted"],
            "confidence": position["confidence"],
            "raw": {
                "wMapGroup": group,
                "wMapNumber": number,
                "wXCoord": raw_x,
                "wYCoord": raw_y,
            },
        }

    def read_flags(self) -> Dict[str, Any]:
        party_count_raw = self._u8(0xDA22)
        party_count = party_count_raw if party_count_raw <= MAX_PLAUSIBLE_PARTY_COUNT else 0
        species = [self._u8(0xDA23 + i) for i in range(party_count)]
        party_terminator = self._u8(0xDA23 + party_count) if party_count_raw <= MAX_PLAUSIBLE_PARTY_COUNT else None
        bag_count_raw = self._u8(ADDR_NUM_ITEMS)
        bag_count = bag_count_raw if bag_count_raw <= MAX_PLAUSIBLE_BAG_COUNT else 0
        bag_terminator = self._u8(ADDR_ITEMS + bag_count * 2) if bag_count_raw <= MAX_PLAUSIBLE_BAG_COUNT else None
        balls_count_raw = self._u8(ADDR_NUM_BALLS)
        balls_count = balls_count_raw if balls_count_raw <= MAX_PLAUSIBLE_BALL_COUNT else 0
        balls_terminator = self._u8(ADDR_BALLS + balls_count * 2) if balls_count_raw <= MAX_PLAUSIBLE_BALL_COUNT else None
        party_count_trusted = party_count_raw <= MAX_PLAUSIBLE_PARTY_COUNT
        bag_count_trusted = bag_count_raw <= MAX_PLAUSIBLE_BAG_COUNT
        balls_count_trusted = balls_count_raw <= MAX_PLAUSIBLE_BALL_COUNT
        story_events = self._story_event_flags()
        party_has_starter = any(s in {0x98, 0x9B, 0x9E} for s in species)
        return {
            "structured_state_available": True,
            "generic_mode": False,
            "party_count": party_count,
            "party_count_raw": party_count_raw,
            "party_count_trusted": party_count_trusted,
            "party_terminator": party_terminator,
            "party_terminator_present": party_terminator == 0xFF if party_terminator is not None else False,
            "bag_count": bag_count,
            "bag_count_raw": bag_count_raw,
            "bag_count_trusted": bag_count_trusted,
            "bag_terminator": bag_terminator,
            "bag_terminator_present": bag_terminator == 0xFF if bag_terminator is not None else False,
            "balls_count": balls_count,
            "balls_count_raw": balls_count_raw,
            "balls_count_trusted": balls_count_trusted,
            "balls_terminator": balls_terminator,
            "balls_terminator_present": balls_terminator == 0xFF if balls_terminator is not None else False,
            "money_raw_bcd": self._money_raw(),
            "money_trusted": self._bcd_bytes_valid(ADDR_MONEY, 3),
            "has_starter": story_events["got_pokemon_from_elm"] or party_has_starter,
            "johto_badges": self._u8(0xD57C),
            "kanto_badges": self._u8(0xD57D),
            "story_event_flags": story_events,
            "derived_story_flags": {
                "source": "gold_event_flags_party_and_badges",
                "event_flags_available": True,
                "event_flags_source": "pokegold_event_flags_d7b7",
                "has_starter": story_events["got_pokemon_from_elm"] or party_has_starter,
                "has_starter_source": "event_flag" if story_events["got_pokemon_from_elm"] else "party_species" if party_has_starter else "none",
                "starter_flags": {
                    "cyndaquil": story_events["got_cyndaquil_from_elm"],
                    "totodile": story_events["got_totodile_from_elm"],
                    "chikorita": story_events["got_chikorita_from_elm"],
                },
                "got_mystery_egg_from_mr_pokemon": story_events["got_mystery_egg_from_mr_pokemon"],
                "gave_mystery_egg_to_elm": story_events["gave_mystery_egg_to_elm"],
                "elm_called_about_stolen_pokemon": story_events["elm_called_about_stolen_pokemon"],
                "learned_to_catch_pokemon": story_events["learned_to_catch_pokemon"],
                "badge_count": len(self._badges()),
                "has_zephyr_badge": bool(self._u8(0xD57C) & 0x01),
            },
            "raw_map_group": self._map_group(),
            "raw_map_number": self._map_number(),
        }
