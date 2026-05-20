#!/usr/bin/env python3
"""Pokemon Gold autoplayer V2 entrypoint skeleton.

This file is intentionally separate from `gold_autoplayer.py` so the legacy
runner remains available while the navigation rewrite is built incrementally.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any

from pokemon_agent.gameplay import choose_catch_action, roster_roles, snapshot_from_state, summarize_inventory
from pokemon_agent.gameplay.gold_data import BALL_ITEM_IDS, HEALING_ITEM_IDS
from pokemon_agent.gameplay.story import (
    CHERRYGROVE_TARGET,
    ELMS_LAB_RETURN_TARGET,
    FALKNER_TARGET,
    MR_POKEMON_HOUSE_TARGET,
    NEW_BARK_AFTER_STARTER_TARGET,
    ROUTE29_TARGET,
    ROUTE30_TARGET,
    ROUTE31_TARGET,
    ROUTE31_GRIND_TARGET,
    ROUTE32_TARGET,
    ROUTE33_TARGET,
    STARTER_CHOICES,
    UNION_CAVE_TARGET,
    FALKNER_MIN_HP_RATIO,
    FALKNER_MIN_LEVEL,
    FALKNER_MIN_BACKUP_LEVEL,
    FALKNER_MIN_PARTY_COUNT,
    STARTER_TARGET,
    VIOLET_CITY_TARGET,
    VIOLET_GATE_TARGET,
    VIOLET_POKECENTER_AIDE_TARGET,
    VIOLET_GYM_LOBBY_TARGET,
    VIOLET_MART_BUY_TARGET,
    VIOLET_POKECENTER_HEAL_TARGET,
    explain_story_objective,
    select_route_target,
)
from pokemon_agent.navigation import GOLD_MAP_REGISTRY, RoutePlan, RouteTarget, plan_route_to_target
from pokemon_agent.navigation.route_planner import find_map_path, transition_between
from pokemon_agent.autoplayer.learning import LearningFact, LearningMemory, import_gold_v1_teacher_snapshot, read_optional_json
from pokemon_agent.autoplayer.save_states import MilestoneSaveStateManager


DEFAULT_OBJECTIVE = "reach Violet City and win the first gym badge"
DEFAULT_LOOP_SLEEP_SECONDS = 0.25
BUTTON_SETTLE_FRAMES = 30
WALK_HOLD_FRAMES = 48
SHORT_WALK_HOLD_FRAMES = 24
WALK_SETTLE_FRAMES = 12
ADAPTIVE_BUTTON_RECOVERY_SEQUENCE: tuple[str, ...] = ("press_b", "wait_300", "press_a")
ADAPTIVE_LOOP_RECOVERY_SEQUENCE: tuple[str, ...] = ("press_b", "wait_300", "walk_right", "walk_left", "walk_down", "walk_up")
ALLOWED_ACTIONS = frozenset({
    "wait_300",
    "wait_600",
    "walk_up",
    "walk_down",
    "walk_left",
    "walk_right",
    "press_up",
    "press_down",
    "press_left",
    "press_right",
    "press_start",
    "press_a",
    "press_b",
})
STUCK_CIRCUIT_BREAKER_THRESHOLD = 3
BATTLE_NO_PROGRESS_THRESHOLD = 6
FORCED_CAPTURE_MAX_FALLBACK_CYCLES = 3
NEW_GAME_BOOTSTRAP_SEQUENCE: tuple[str, ...] = ("wait_300", "wait_600", "press_start", "press_a")
TEXT_INPUT_END_SEQUENCE: tuple[str, ...] = ("press_a",)
NICKNAME_END_SEQUENCE = TEXT_INPUT_END_SEQUENCE
SILLY_STARTER_NICKNAMES: tuple[str, ...] = ("A", "AA", "AAA", "AAAA", "AAAAA")
STARTER_CHOICE_ORDER: tuple[str, ...] = ("cyndaquil", "totodile", "chikorita")
BATTLE_RUN_SEQUENCE: tuple[str, ...] = ("press_b", "press_b", "press_down", "press_right", "press_a")
BATTLE_CAPTURE_OPEN_PACK_SEQUENCE: tuple[str, ...] = ("press_b", "press_b", "press_down", "press_a")
# When Gold/Silver menu RAM is implausible during wild capture, opening Pack is
# only half the fallback. The live run can land in the Pack with no usable menu
# decode; blocking there causes an infinite press-A/no-progress loop. This
# bounded continuation moves from Items to Balls, selects the first ball, then
# confirms use. It is only used when state independently confirms: wild battle,
# catch policy says throw_ball, and the bag reader sees balls available.
BATTLE_CAPTURE_THROW_BALL_SEQUENCE: tuple[str, ...] = ("press_right", "press_a", "press_a")
BATTLE_RECOVERY_SEQUENCE: tuple[str, ...] = ("press_b", "wait_300", "press_a")
AMBIGUOUS_DIALOGUE_RECOVERY_SEQUENCE: tuple[str, ...] = ("wait_300", "press_b", "press_a")
STALE_DIALOGUE_RECOVERY_SEQUENCE: tuple[str, ...] = ("press_b", "wait_300", "press_a", "walk_down")
AIDE_NURSE_DIALOGUE_ESCAPE_SEQUENCE: tuple[str, ...] = ("press_down", "press_a", "press_b", "walk_down")
TRAINER_MISSING_MENU_FIGHT_SEQUENCE: tuple[str, ...] = ("press_b", "press_b", "press_up", "press_left", "press_a", "press_a")
MOVE_SLOT_SELECT_SEQUENCES: tuple[tuple[str, ...], ...] = ((), ("press_down",), ("press_down", "press_down"), ("press_down", "press_down", "press_down"))
DAMAGING_MOVE_IDS: frozenset[int] = frozenset({10, 16, 17, 22, 29, 33, 40, 44, 52, 55, 60, 65, 71, 84, 98, 99, 122, 125, 129, 145, 154, 172})
MOVE_DAMAGE_PRIORITY: dict[int, int] = {
    52: 40,  # Ember beats early-game normal attacks, especially in caves.
    55: 40,
    84: 40,
    33: 35,
}
TRAINER_MISSING_MENU_HEAL_SEQUENCE: tuple[str, ...] = ("press_b", "press_b", "press_up", "press_right", "press_a", "press_a", "press_a")
MART_BUY_ONE_SEQUENCE: tuple[str, ...] = ("press_a", "press_a", "press_a", "press_a")
MART_BUY_POTION_FROM_TOP_SEQUENCE: tuple[str, ...] = ("press_down", *MART_BUY_ONE_SEQUENCE)
ROUTE31_GRIND_SEQUENCE: tuple[str, ...] = ("walk_left", "walk_right")
MIN_BALLS_BEFORE_FALKNER = 2
MIN_HEALING_ITEMS_BEFORE_FALKNER = 1
MONEY_RESERVE_BEFORE_FALKNER = 300
POKE_BALL_PRICE = 200
POTION_PRICE = 300
POTION_ITEM_ID = 0x12
WALK_DELTAS: dict[str, tuple[int, int]] = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}
OPPOSITE_DIRECTIONS: dict[str, str] = {
    "up": "down",
    "down": "up",
    "left": "right",
    "right": "left",
}


def posted_actions_for(action: str, walk_hold_frames: int | None = None) -> list[str]:
    if action in {"press_a", "press_b"}:
        return [action, f"wait_{BUTTON_SETTLE_FRAMES}"]
    if action.startswith("walk_"):
        direction = action.removeprefix("walk_")
        frames = walk_hold_frames or WALK_HOLD_FRAMES
        return [f"hold_{direction}_{frames}", f"wait_{WALK_SETTLE_FRAMES}"]
    if action.startswith("press_") and action.removeprefix("press_") in {"up", "down", "left", "right"}:
        direction = action.removeprefix("press_")
        return [f"hold_{direction}_12", "wait_72"]
    return [action]


def posted_actions_for_context(action: str, navigation: dict[str, Any] | None = None, phase: str | None = None) -> list[str]:
    if action in {"press_a", "press_b"} and phase == "BATTLE" and isinstance(navigation, dict) and navigation.get("path_source") == "battle_fallback":
        return [action, "wait_60"]
    return posted_actions_for(action, walk_hold_frames_for_navigation(action, navigation))


def battle_progress_signature(state: dict[str, Any] | None) -> tuple[Any, ...] | None:
    if not state:
        return None
    battle = state.get("battle") or {}
    if battle.get("in_battle") is not True:
        return ("not_in_battle",)
    party = state.get("party") or []
    lead_hp = None
    if party and isinstance(party[0], dict):
        lead_hp = party[0].get("hp")
    return (
        battle.get("type_id"),
        battle.get("enemy_species_id"),
        battle.get("enemy_hp"),
        battle.get("enemy_max_hp"),
        lead_hp,
    )


def battle_identity(state: dict[str, Any] | None) -> tuple[Any, ...] | None:
    if not state:
        return None
    battle = state.get("battle") or {}
    if battle.get("in_battle") is not True:
        return None
    map_info = state.get("map") or {}
    return (
        battle.get("type_id"),
        battle.get("wild_species_id") or battle.get("enemy_species_id"),
        battle.get("enemy_level"),
        battle.get("enemy_max_hp"),
        map_info.get("map_group"),
        map_info.get("map_number"),
    )


def walk_hold_frames_for_navigation(action: str, navigation: dict[str, Any] | None) -> int | None:
    """Use shorter holds near doors/warps so one logical step stays one tile."""
    if not action.startswith("walk_") or not isinstance(navigation, dict):
        return None
    path_source = str(navigation.get("path_source") or "")
    if navigation.get("transition") is not None:
        return SHORT_WALK_HOLD_FRAMES
    if "gate" in path_source or "warp" in path_source or "door" in path_source:
        return SHORT_WALK_HOLD_FRAMES
    if path_source == "starter_live_macro":
        return SHORT_WALK_HOLD_FRAMES
    return None


def effective_navigation_tile(state: dict[str, Any], key: tuple[int, int] | None, tile: tuple[int, int] | None) -> tuple[tuple[int, int] | None, dict[str, Any]]:
    """Prefer normalized tile, but fall back to raw RAM coords if maps reject it."""
    if key is None or tile is None:
        return tile, {}
    map_spec = GOLD_MAP_REGISTRY.get(key)
    if map_spec is None or map_spec.is_walkable(tile):
        return tile, {}
    position = ((state.get("player") or {}).get("position") or {})
    raw_x = position.get("raw_x")
    raw_y = position.get("raw_y")
    raw_tile = (raw_x, raw_y) if isinstance(raw_x, int) and isinstance(raw_y, int) else None
    if raw_tile is not None and map_spec.is_walkable(raw_tile):
        return raw_tile, {
            "coordinate_source": "raw_ram_fallback",
            "normalized_tile": {"x": tile[0], "y": tile[1]},
            "raw_tile": {"x": raw_tile[0], "y": raw_tile[1]},
        }
    return tile, {"coordinate_source": "normalized_unwalkable", "normalized_tile": {"x": tile[0], "y": tile[1]}}


def ruins_of_alph_room_local_tile(state: dict[str, Any], key: tuple[int, int] | None) -> tuple[int, int] | None:
    if key not in {(3, 23), (3, 29)}:
        return None
    map_spec = GOLD_MAP_REGISTRY.get(key)
    if map_spec is None:
        return None
    position = ((state.get("player") or {}).get("position") or {})
    candidates = []
    for x_key, y_key in (("actual_x", "actual_y"), ("raw_x", "raw_y"), ("x", "y")):
        x = position.get(x_key)
        y = position.get(y_key)
        if isinstance(x, int) and isinstance(y, int):
            candidates.append((x, y))
            candidates.append((x, y - map_spec.tile_height))
    for candidate in candidates:
        x, y = candidate
        if 0 <= x < map_spec.tile_width and 0 <= y < map_spec.tile_height:
            return candidate
    return None


def union_cave_live_alias_state(state: dict[str, Any], key: tuple[int, int] | None) -> dict[str, Any] | None:
    if key != (3, 29):
        return None
    position = ((state.get("player") or {}).get("position") or {})
    actual_x = position.get("actual_x")
    actual_y = position.get("actual_y")
    union_cave = GOLD_MAP_REGISTRY.get((3, 37))
    if not isinstance(actual_x, int) or not isinstance(actual_y, int) or union_cave is None:
        return None
    if not (0 <= actual_x < union_cave.tile_width and 0 <= actual_y < union_cave.tile_height):
        return None
    aliased = dict(state)
    aliased_player = dict(state.get("player") or {})
    aliased_position = dict(position)
    aliased_position.update({
        "map_group": 3,
        "map_number": 37,
        "map_name": "Union Cave 1F",
        "trusted": True,
        "in_bounds": True,
        "confidence": "aliased",
    })
    aliased_player["position"] = aliased_position
    aliased["player"] = aliased_player
    aliased_map = dict(state.get("map") or {})
    aliased_map.update({
        "map_group": 3,
        "map_number": 37,
        "map_name": "Union Cave 1F",
        "trusted": True,
        "position_in_bounds": True,
        "confidence": "aliased",
    })
    aliased["map"] = aliased_map
    return aliased


def ruins_of_alph_escape_action(state: dict[str, Any], key: tuple[int, int] | None) -> tuple[str, dict[str, Any]] | None:
    if union_cave_live_alias_state(state, key) is not None:
        return None
    tile = ruins_of_alph_room_local_tile(state, key)
    if tile is None:
        return None
    x, y = tile
    if x < 3:
        action = "walk_right"
    elif x > 4:
        action = "walk_left"
    elif y < 9:
        action = "walk_down"
    else:
        action = "walk_down"
    return action, {
        "path_source": "ruins_of_alph_escape",
        "next_step": action,
        "planned_path_length": abs(x - 3) if x < 3 else abs(x - 4) if x > 4 else max(1, 9 - y + 1),
        "room_local_tile": {"x": x, "y": y},
        "reason": "escape Ruins of Alph room with untrusted RAM position after Zephyr",
    }


def expected_tile_after_walk(tile: tuple[int, int], action: str) -> tuple[int, int] | None:
    direction = action.removeprefix("walk_") if action.startswith("walk_") else ""
    delta = WALK_DELTAS.get(direction)
    if delta is None:
        return None
    return (tile[0] + delta[0], tile[1] + delta[1])


def observed_walk_progress(
    start: tuple[int, int],
    current: tuple[int, int],
    action: str,
) -> bool:
    if not action.startswith("walk_"):
        return False
    direction = action.removeprefix("walk_")
    sx, sy = start
    cx, cy = current
    if direction == "up":
        return cx == sx and cy < sy
    if direction == "down":
        return cx == sx and cy > sy
    if direction == "left":
        return cy == sy and cx < sx
    if direction == "right":
        return cy == sy and cx > sx
    return False


def verify_single_action(
    before_state: dict[str, Any],
    after_state: dict[str, Any],
    action: str,
) -> tuple[bool, str]:
    if action not in ALLOWED_ACTIONS:
        return False, "unknown_action"
    before = snapshot_from_state(before_state)
    after = snapshot_from_state(after_state)
    before_key = before.position.map_key
    before_tile = before.position.tile
    after_key = after.position.map_key
    after_tile = after.position.tile

    if action.startswith("walk_"):
        direction = action.removeprefix("walk_")
        if before.battle.in_battle or after.battle.in_battle:
            before_menu = observed_menu(before_state)
            after_menu = observed_menu(after_state)
            if before_menu != after_menu and (before_menu or after_menu):
                return True, "menu_state_changed_after_direction"
        if position_untrusted(after_state):
            return False, "after_position_untrusted"
        expected = expected_tile_after_walk(before_tile, action) if before_tile else None
        if expected is not None and before_key == after_key and after_tile == expected:
            return True, "walked_expected_tile"
        if before_tile is not None and after_tile is not None and before_key == after_key and observed_walk_progress(before_tile, after_tile, action):
            return True, "walked_progress_in_direction"
        if before_key != after_key:
            if before_key is not None and after_key is not None:
                transition = transition_between(GOLD_MAP_REGISTRY, before_key, after_key)
                if transition is not None:
                    source_ok = before_tile in transition.source_tiles if before_tile is not None else False
                    entered_source_ok = expected in transition.source_tiles if expected is not None else False
                    dest_ok = after_tile in transition.dest_tiles if after_tile is not None else False
                    if source_ok or entered_source_ok or dest_ok:
                        return True, "map_transition_observed"
            return False, "unexpected_map_transition"
        if before.facing != direction and after.facing == direction:
            return True, "facing_changed_after_direction"
        return False, "position_did_not_advance"
    if action == "press_a":
        if after.has_starter and not before.has_starter:
            return True, "starter_obtained"
        before_dialog = before_state.get("dialog") or {}
        after_dialog = after_state.get("dialog") or {}
        before_menu = observed_menu(before_state)
        after_menu = observed_menu(after_state)
        visual_changed = (before_state.get("visual") or {}) != (after_state.get("visual") or {})
        implausible_visual_textbox_without_semantic_progress = (
            visual_changed
            and visual_dialogue_active(before_state)
            and visual_dialogue_active(after_state)
            and before_dialog.get("window_stack_plausible") is False
            and after_dialog.get("window_stack_plausible") is False
            and before_dialog.get("ram_active") is False
            and after_dialog.get("ram_active") is False
            and before.position == after.position
            and before.battle == after.battle
            and before.party == after.party
            and before_dialog == after_dialog
            and before_menu == after_menu
        )
        if implausible_visual_textbox_without_semantic_progress:
            return False, "press_a_visual_only_no_semantic_progress"
        if visual_changed:
            return True, "visual_state_changed_after_press_a"
        if before.position != after.position:
            return True, "state_changed_after_press_a"
        if before.battle != after.battle:
            return True, "battle_state_changed_after_press_a"
        if before_dialog != after_dialog:
            return True, "dialogue_state_changed_after_press_a"
        if before_menu != after_menu and (before_menu or after_menu):
            return True, "menu_state_changed_after_press_a"
        if before.party != after.party:
            return True, "party_changed_after_press_a"
        return False, "press_a_no_progress"
    if action == "press_b":
        if before.battle.in_battle and not after.battle.in_battle:
            return True, "battle_ended_after_press_b"
        if before.battle != after.battle:
            return True, "battle_state_changed_after_press_b"
        if (before_state.get("dialog") or {}) != (after_state.get("dialog") or {}):
            return True, "dialogue_state_changed_after_press_b"
        before_menu = observed_menu(before_state)
        after_menu = observed_menu(after_state)
        if before_menu != after_menu and (before_menu or after_menu):
            return True, "menu_state_changed_after_press_b"
        return False, "press_b_no_progress"
    if action == "press_start":
        if (before_state.get("visual") or {}) != (after_state.get("visual") or {}):
            return True, "visual_state_changed_after_press_start"
        if before.position != after.position or before.menu != after.menu or before.dialog != after.dialog:
            return True, "state_changed_after_press_start"
        return False, "press_start_no_progress"
    if action in {"wait_300", "wait_600"}:
        if (before_state.get("metadata") or {}).get("frame_count") != (after_state.get("metadata") or {}).get("frame_count"):
            return True, "frames_advanced_after_wait"
        if (before_state.get("visual") or {}) != (after_state.get("visual") or {}):
            return True, "visual_state_changed_after_wait"
        return False, "wait_no_progress"
    if action in {"press_up", "press_down", "press_left", "press_right"}:
        before_visual = before_state.get("visual") or {}
        after_visual = after_state.get("visual") or {}
        if before_visual != after_visual:
            return True, "visual_state_changed_after_direction_press"
        if likely_text_input_keyboard(before_state):
            return True, "nickname_cursor_direction_posted"
        return False, "direction_press_no_progress"
    return False, "unknown_action"


def confirmed_dialogue(dialog: dict[str, Any]) -> bool:
    if dialog.get("visual_active") is True:
        return True
    if dialog.get("active") is not True:
        return False
    return dialog.get("window_stack_plausible") is not False


def ambiguous_dialogue(dialog: dict[str, Any]) -> bool:
    return dialog.get("active") is True and dialog.get("window_stack_plausible") is False and dialog.get("visual_active") is not True


def visual_dialogue_active(state: dict[str, Any]) -> bool:
    visual = state.get("visual") or {}
    if visual.get("visual_textbox_active") is True:
        return True
    if (
        visual.get("screen_class") == "menu_or_text"
        and float(visual.get("bright_lower") or 0.0) > 0.72
        and float(visual.get("dark_lower") or 0.0) > 0.1
        and (visual.get("lower_contrast_gap") is None or float(visual.get("lower_contrast_gap") or 0.0) > 20.0)
    ):
        return True
    return (
        visual.get("screen_class") == "dialogue"
        and float(visual.get("bright_lower") or 0.0) > 0.7
        and float(visual.get("dark_lower") or 0.0) > 0.1
    )


def pre_overworld_text_active(state: dict[str, Any]) -> bool:
    map_info = state.get("map") or {}
    if map_info.get("map_group") != 0 or map_info.get("map_number") != 0:
        return False
    visual = state.get("visual") or {}
    return (
        visual.get("visual_textbox_active") is True
        or visual.get("bright_dialogue_panel") is True
        or float(visual.get("bright_lower") or 0.0) > 0.7
        or (visual.get("screen_class") == "menu_or_text" and float(visual.get("bright_lower") or 0.0) > 0.25)
    )


def text_or_dialogue_suspected(state: dict[str, Any] | None) -> bool:
    if not state:
        return False
    dialog = state.get("dialog") or {}
    if confirmed_dialogue(dialog) or ambiguous_dialogue(dialog):
        return True
    menu = state.get("menu") or {}
    if isinstance(menu, dict) and menu.get("active") is True:
        return True
    if likely_elm_phone_call(state):
        return True
    if likely_text_input_keyboard(state):
        return True
    visual = state.get("visual") or {}
    if isinstance(visual, dict):
        if visual_dialogue_active(state):
            return True
    return False


def likely_starter_nickname_screen(state: dict[str, Any]) -> bool:
    snapshot = snapshot_from_state(state)
    if not snapshot.has_starter or snapshot.position.map_key != (24, 5):
        return False
    if snapshot.story.got_mystery_egg_from_mr_pokemon or snapshot.story.elm_called_about_stolen_pokemon:
        return False
    if snapshot.battle.in_battle or confirmed_dialogue(state.get("dialog") or {}):
        return False
    menu = state.get("menu") or {}
    if menu.get("active") is True:
        return False
    visual = state.get("visual") or {}
    return visual.get("screen_class") == "menu_or_text" and visual.get("visual_textbox_active") is not True


def likely_rival_name_screen(state: dict[str, Any]) -> bool:
    snapshot = snapshot_from_state(state)
    if snapshot.position.map_key != (24, 5):
        return False
    if not snapshot.story.got_mystery_egg_from_mr_pokemon or snapshot.story.gave_mystery_egg_to_elm:
        return False
    if snapshot.battle.in_battle or confirmed_dialogue(state.get("dialog") or {}):
        return False
    menu = state.get("menu") or {}
    if menu.get("active") is True:
        return False
    visual = state.get("visual") or {}
    return (
        visual.get("screen_class") == "menu_or_text"
        and visual.get("visual_textbox_active") is not True
        and float(visual.get("bright_lower") or 0.0) > 0.8
        and float(visual.get("dark_lower") or 0.0) < 0.08
    )


def likely_text_input_keyboard(state: dict[str, Any]) -> bool:
    return likely_starter_nickname_screen(state) or likely_rival_name_screen(state)


def likely_mart_purchase_screen(state: dict[str, Any]) -> bool:
    if mart_purchase_policy(state) is None:
        return False
    snapshot = snapshot_from_state(state)
    menu = observed_menu(state)
    if menu.get("name") == "mart_items" or menu.get("selected_item_id") is not None or menu.get("visible_items"):
        return True
    if snapshot.position.tile not in VIOLET_MART_BUY_TARGET.tiles:
        return False
    dialog = state.get("dialog") or {}
    if confirmed_dialogue(dialog) or visual_dialogue_active(state) or ambiguous_dialogue(dialog):
        return True
    visual = state.get("visual") or {}
    return visual.get("screen_class") == "menu_or_text" and visual.get("visual_textbox_active") is not False


def mart_purchase_policy(state: dict[str, Any]) -> str | None:
    snapshot = snapshot_from_state(state)
    if snapshot.position.map_key != VIOLET_MART_BUY_TARGET.map_key:
        return None
    inventory = summarize_inventory(snapshot)
    money = snapshot.money or 0
    missing_heals = max(0, MIN_HEALING_ITEMS_BEFORE_FALKNER - inventory.healing_items)
    if inventory.balls <= 0 and money >= POKE_BALL_PRICE:
        return "balls"
    if inventory.balls < MIN_BALLS_BEFORE_FALKNER and money >= POKE_BALL_PRICE + (missing_heals * POTION_PRICE) + MONEY_RESERVE_BEFORE_FALKNER:
        return "balls"
    if inventory.healing_items < MIN_HEALING_ITEMS_BEFORE_FALKNER and money >= POTION_PRICE + MONEY_RESERVE_BEFORE_FALKNER:
        return "potions"
    return None


def selected_mart_item_id(state: dict[str, Any]) -> int | None:
    menu = observed_menu(state)
    if not menu:
        raw_menu = state.get("menu") or {}
        menu = raw_menu if isinstance(raw_menu, dict) else {}
    return _selected_item_id(menu)


def mart_purchase_sequence(state: dict[str, Any]) -> tuple[str, ...]:
    policy = mart_purchase_policy(state)
    selected_item = selected_mart_item_id(state)
    if policy == "potions" and selected_item is None:
        return ("press_b",)
    if policy == "potions" and selected_item != POTION_ITEM_ID:
        return MART_BUY_POTION_FROM_TOP_SEQUENCE
    return MART_BUY_ONE_SEQUENCE


def party_member_needs_pokecenter_heal(mon: dict[str, Any]) -> bool:
    if mon.get("trusted") is False:
        return False
    hp = mon.get("hp")
    max_hp = mon.get("max_hp")
    if isinstance(hp, int) and isinstance(max_hp, int) and max_hp > 0 and hp < max_hp:
        return True
    status = mon.get("status_condition")
    if isinstance(status, dict) and status.get("any") is True:
        return True
    status_raw = mon.get("status_raw")
    return isinstance(status_raw, int) and status_raw != 0


def state_has_pokecenter_healing_need(state: dict[str, Any]) -> bool:
    return any(party_member_needs_pokecenter_heal(mon) for mon in (state.get("party") or []) if isinstance(mon, dict))


def active_pokecenter_heal_dialogue(state: dict[str, Any]) -> bool:
    snapshot = snapshot_from_state(state)
    if select_route_target(state) == VIOLET_POKECENTER_AIDE_TARGET:
        return False
    return (
        state_has_pokecenter_healing_need(state)
        and snapshot.position.map_key == VIOLET_POKECENTER_HEAL_TARGET.map_key
        and snapshot.position.tile in VIOLET_POKECENTER_HEAL_TARGET.tiles
    )


def badge_count_from_player(player: dict[str, Any]) -> int:
    badges = player.get("badges")
    if isinstance(badges, list):
        return len(badges)
    if isinstance(badges, int):
        return badges
    return 0


def likely_untrusted_wild_battle_main(state: dict[str, Any]) -> bool:
    snapshot = snapshot_from_state(state)
    if not snapshot.battle.wild:
        return False
    if observed_menu(state):
        return False
    if confirmed_dialogue(state.get("dialog") or {}):
        return False
    visual = state.get("visual") or {}
    return visual.get("screen_class") in {"menu_or_text", "overworld_or_battle", "battle"} and visual.get("visual_textbox_active") is not True


def likely_wild_grind_missing_menu(state: dict[str, Any]) -> bool:
    if not likely_untrusted_wild_battle_main(state):
        return False
    return should_grind_wild_encounter(snapshot_from_state(state))


def likely_trainer_battle_missing_menu(state: dict[str, Any]) -> bool:
    snapshot = snapshot_from_state(state)
    if not snapshot.battle.in_battle or snapshot.battle.wild:
        return False
    battle = state.get("battle") or {}
    if battle.get("trusted") is False or battle.get("type_id") != 2:
        return False
    if observed_menu(state):
        return False
    if confirmed_dialogue(state.get("dialog") or {}) or visual_dialogue_active(state):
        return False
    visual = state.get("visual") or {}
    return visual.get("screen_class") in {"menu_or_text", "overworld_or_battle", "battle"} and visual.get("visual_textbox_active") is not True


def likely_elm_phone_call(state: dict[str, Any]) -> bool:
    snapshot = snapshot_from_state(state)
    if snapshot.position.map_key != (26, 1):
        return False
    if snapshot.position.tile != (17, 6):
        return False
    if not snapshot.story.got_mystery_egg_from_mr_pokemon or snapshot.story.gave_mystery_egg_to_elm:
        return False
    if snapshot.battle.in_battle:
        return False
    visual = state.get("visual") or {}
    if snapshot.story.elm_called_about_stolen_pokemon and not (
        visual.get("visual_textbox_active") is True
        or visual.get("bright_dialogue_panel") is True
        or float(visual.get("bright_lower") or 0.0) > 0.7
    ):
        return False
    return visual.get("screen_class") == "menu_or_text"


def position_untrusted(state: dict[str, Any]) -> bool:
    position = ((state.get("player") or {}).get("position") or {})
    return position.get("trusted") is False or position.get("in_bounds") is False


def battle_untrusted(state: dict[str, Any]) -> bool:
    battle = state.get("battle") or {}
    return battle.get("trusted") is False


def lead_move_pps(state: dict[str, Any]) -> tuple[int | None, ...]:
    party = state.get("party") or []
    if not party or not isinstance(party[0], dict):
        return ()
    raw_pp = party[0].get("pp") or party[0].get("move_pp_raw") or []
    pps: list[int | None] = []
    for value in raw_pp[:4]:
        pps.append(value if isinstance(value, int) else None)
    return tuple(pps)


def all_party_fainted(state: dict[str, Any]) -> bool:
    party = state.get("party") or []
    trusted_party = [mon for mon in party if isinstance(mon, dict) and mon.get("trusted") is not False]
    if not trusted_party:
        return False
    return all(isinstance(mon.get("hp"), int) and mon.get("hp") <= 0 for mon in trusted_party)


def preferred_move_slot(state: dict[str, Any]) -> int:
    party = state.get("party") or []
    lead = party[0] if party and isinstance(party[0], dict) else {}
    moves = [move for move in (lead.get("moves") or [])[:4] if isinstance(move, int)]
    pps = lead_move_pps(state)
    usable_slots = [index for index, pp in enumerate(pps[:len(moves)]) if pp is None or pp > 0]
    if not usable_slots:
        return 0
    damaging_slots = [index for index in usable_slots if index < len(moves) and moves[index] in DAMAGING_MOVE_IDS]
    if damaging_slots:
        return max(damaging_slots, key=lambda index: MOVE_DAMAGE_PRIORITY.get(moves[index], 30))
    return usable_slots[0]


def missing_menu_fight_sequence_for_state(state: dict[str, Any]) -> tuple[str, ...]:
    move_slot = preferred_move_slot(state)
    select_sequence = MOVE_SLOT_SELECT_SEQUENCES[move_slot] if 0 <= move_slot < len(MOVE_SLOT_SELECT_SEQUENCES) else ()
    return ("press_b", "press_b", "press_up", "press_left", "press_a", *select_sequence, "press_a")


def choose_battle_actions(state: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    snapshot = snapshot_from_state(state)
    dialog = state.get("dialog") or {}
    if confirmed_dialogue(dialog) or visual_dialogue_active(state):
        return ["press_a"], {
            "path_source": "battle_fallback",
            "battle_policy": "advance_dialog",
            "next_step": "press_a",
            "planned_path_length": 1,
        }
    battle = state.get("battle") or {}
    if battle.get("enemy_hp_trusted") is True and battle.get("enemy_hp") == 0:
        return ["press_a"], {
            "path_source": "battle_fallback",
            "battle_policy": "advance_post_ko_text",
            "next_step": "press_a",
            "planned_path_length": 1,
            "reason": "enemy HP is zero; advance battle result text even when menu RAM is unavailable",
        }
    if battle_untrusted(state):
        return [], {
            "path_source": "battle_fallback",
            "battle_policy": "battle_blocked_untrusted_ram",
            "next_step": None,
            "planned_path_length": None,
            "battle_type_id": battle.get("type_id"),
            "battle_raw": battle.get("raw"),
        }
    if snapshot.battle.type_id not in {1, 2}:
        battle = state.get("battle") or {}
        return [], {
            "path_source": "battle_fallback",
            "battle_policy": "battle_blocked_unknown_type",
            "next_step": None,
            "planned_path_length": None,
            "battle_type_id": battle.get("type_id"),
            "battle_raw": battle.get("raw"),
        }
    catch_decision = choose_catch_action(snapshot)
    lead_ratio = snapshot.lead.hp_ratio if snapshot.lead else None
    if snapshot.battle.wild:
        if catch_decision.action == "throw_ball":
            return choose_capture_actions(state)
        if catch_decision.action == "weaken":
            return choose_fight_actions(state, "wild_weaken", catch_decision.reason)
        story_decision = explain_story_objective(state)
        if story_decision.objective_key == "route31_grind" and lead_ratio is not None and lead_ratio >= 0.35:
            return choose_fight_actions(state, "wild_grind", "route31 objective: fight safe non-target wild encounter for EXP")
        if should_grind_wild_encounter(snapshot):
            return choose_fight_actions(state, "wild_grind", "pre-Falkner grinding: no balls available, fight wild encounter for EXP")
        policy = "wild_run_low_hp" if lead_ratio is not None and lead_ratio < 0.25 else "wild_run"
        return choose_run_actions(state, policy)
    if lead_ratio is not None and lead_ratio < 0.35 and has_healing_item(snapshot):
        heal_actions, heal_status = choose_healing_item_actions(state)
        if heal_actions:
            return heal_actions, heal_status
    if lead_ratio is not None and lead_ratio < 0.25 and not has_healing_item(snapshot):
        return [], {
            "path_source": "battle_fallback",
            "battle_policy": "trainer_blocked_critical_hp_no_healing",
            "next_step": None,
            "planned_path_length": None,
            "lead_hp_ratio": lead_ratio,
        }
    return choose_fight_actions(state, "trainer_fight", "trainer battle: use first available move only from Fight cursor")


def has_healing_item(snapshot: Any) -> bool:
    return any(item.item_id in HEALING_ITEM_IDS and item.quantity > 0 for item in snapshot.bag)


def resource_accounting(snapshot: Any) -> dict[str, Any]:
    inventory = summarize_inventory(snapshot)
    lead = snapshot.lead
    lead_hp_ratio = lead.hp_ratio if lead else None
    lead_level = lead.level if lead else None
    backup_levels = [mon.level for mon in snapshot.party[1:] if mon.level is not None]
    has_zephyr = "Zephyr" in snapshot.badges or snapshot.story.has_zephyr_badge
    falkner_prep_ready = snapshot.story.learned_to_catch_pokemon and snapshot.story.gave_mystery_egg_to_elm
    money = snapshot.money or 0
    roster_blocked_by_resources = inventory.balls <= 0 and money < 200
    lead_ready = lead_level is not None and lead_level >= FALKNER_MIN_LEVEL and (lead_hp_ratio is None or lead_hp_ratio >= FALKNER_MIN_HP_RATIO)
    readiness_blockers: list[str] = []
    if not has_zephyr and falkner_prep_ready:
        if len(snapshot.party) < FALKNER_MIN_PARTY_COUNT and not (roster_blocked_by_resources and lead_ready):
            readiness_blockers.append("party_count_below_falkner_floor")
        elif not roster_blocked_by_resources and (len(backup_levels) < FALKNER_MIN_PARTY_COUNT - 1 or min(backup_levels) < FALKNER_MIN_BACKUP_LEVEL):
            readiness_blockers.append("backup_levels_below_falkner_floor")
        if lead_level is None or lead_level < FALKNER_MIN_LEVEL:
            readiness_blockers.append("lead_level_below_falkner_floor")
        if lead_hp_ratio is not None and lead_hp_ratio < FALKNER_MIN_HP_RATIO:
            readiness_blockers.append("lead_hp_below_falkner_floor")
    can_restock_balls = money >= 200
    return {
        "money": money,
        "balls": inventory.balls,
        "healing_items": inventory.healing_items,
        "status_heals": inventory.status_heals,
        "lead_level": lead_level,
        "lead_hp_ratio": round(lead_hp_ratio, 3) if lead_hp_ratio is not None else None,
        "party_count": len(snapshot.party),
        "party_full": snapshot.party_full,
        "can_catch": inventory.can_catch,
        "can_restock_balls": can_restock_balls,
        "broke_no_balls": money < 200 and inventory.balls <= 0,
        "roster_blocked_by_resources": roster_blocked_by_resources,
        "falkner_prep_ready": falkner_prep_ready,
        "falkner_ready": has_zephyr or (falkner_prep_ready and not readiness_blockers),
        "readiness_blockers": readiness_blockers,
        "minima": {
            "falkner_min_level": FALKNER_MIN_LEVEL,
            "falkner_min_hp_ratio": FALKNER_MIN_HP_RATIO,
            "falkner_min_party_count": FALKNER_MIN_PARTY_COUNT,
            "falkner_min_backup_level": FALKNER_MIN_BACKUP_LEVEL,
            "pokeball_cost": 200,
        },
    }


def should_grind_wild_encounter(snapshot: Any) -> bool:
    if not snapshot.battle.wild or snapshot.lead is None:
        return False
    if snapshot.story.has_zephyr_badge:
        return False
    if snapshot.lead.level is None or snapshot.lead.level >= FALKNER_MIN_LEVEL:
        return False
    if snapshot.lead.hp_ratio is not None and snapshot.lead.hp_ratio < 0.35:
        return False
    inventory = summarize_inventory(snapshot)
    if inventory.balls > 0:
        return False
    return snapshot.position.map_key in {(26, 1), (26, 2), (10, 5), (26, 11)}


def should_force_capture_despite_missing_menu(snapshot: Any, catch_decision: Any) -> bool:
    """Keep catch-first pressure when RAM menu decode is broken.

    This is deliberately narrower than the normal catch policy: it only overrides
    the anti-loop fight escape while the early roster is still below the Falkner
    prep floor and state independently says a wild, non-KO target can be caught.
    """
    battle = snapshot.battle
    if catch_decision.action != "throw_ball":
        return False
    if not battle.wild:
        return False
    if battle.enemy_hp is not None and battle.enemy_hp <= 0:
        return False
    inventory = summarize_inventory(snapshot)
    if inventory.balls <= 0 or snapshot.party_full:
        return False
    if len(snapshot.party) >= FALKNER_MIN_PARTY_COUNT:
        return False
    if battle.enemy_hp_ratio is not None and battle.enemy_hp_ratio <= 0.5:
        return True
    if battle.enemy_level is not None and battle.enemy_level <= 4:
        return True
    return battle.enemy_hp_ratio is None


def readiness_allows_action(status: dict[str, Any], action: str) -> tuple[bool, str | None]:
    readiness = status.get("readiness") if isinstance(status.get("readiness"), dict) else {}
    if readiness.get("safe_to_post_actions") is True:
        return True, None
    blockers = set(readiness.get("blockers") or [])
    if not blockers:
        return True, None
    navigation = status.get("navigation") if isinstance(status.get("navigation"), dict) else {}
    path_source = navigation.get("path_source")
    battle_policy = navigation.get("battle_policy")
    safe_untrusted_sources = {
        "new_game_bootstrap",
        "new_game_bootstrap_dialogue",
        "fresh_run_intro_dialogue",
        "dialogue",
        "stale_dialogue_recovery",
        "ambiguous_dialogue_recovery",
        "adaptive_button_recovery",
    }
    if blockers <= {"position_untrusted"} and path_source in safe_untrusted_sources and not action.startswith("walk_"):
        return True, None
    if blockers <= {"battle_ram_untrusted"} and battle_policy == "advance_dialog" and action == "press_a":
        return True, None
    return False, ",".join(sorted(blockers))


def observed_menu(state: dict[str, Any]) -> dict[str, Any]:
    battle = state.get("battle") or {}
    menu = state.get("menu") or state.get("ui") or {}
    if isinstance(battle.get("menu_state"), dict):
        candidate = dict(battle["menu_state"])
        return candidate if menu_usable(candidate) else {}
    if isinstance(menu, dict) and menu:
        candidate = dict(menu)
        return candidate if menu_usable(candidate) else {}
    if isinstance(battle.get("menu"), str) and battle.get("menu_confidence") in {"medium", "high"}:
        candidate = {
            "name": battle.get("menu"),
            "cursor": battle.get("cursor"),
            "confidence": battle.get("menu_confidence"),
            "active": True,
            "selected_item_id": battle.get("selected_item_id"),
            "visible_items": battle.get("visible_items", []),
        }
        return candidate if menu_usable(candidate) else {}
    return {}


def menu_usable(menu: dict[str, Any]) -> bool:
    if menu.get("active") is False:
        return False
    if menu.get("confidence") not in {"medium", "high"}:
        return False
    if menu.get("needs_stronger_decode") is True:
        return False
    if menu.get("window_stack_plausible") is False:
        return False
    return bool(menu.get("name") or menu.get("menu"))


def menu_blocked_reason(state: dict[str, Any]) -> str:
    menu = state.get("menu") or state.get("ui") or {}
    if isinstance(menu, dict) and menu:
        if menu.get("active") is False:
            return menu.get("blocked_reason") or "inactive"
        if menu.get("confidence") not in {"medium", "high"}:
            return menu.get("blocked_reason") or "low_confidence"
        if menu.get("needs_stronger_decode") is True:
            return menu.get("blocked_reason") or "needs_stronger_decode"
        if menu.get("window_stack_plausible") is False:
            return menu.get("blocked_reason") or "implausible_window_stack"
        if not (menu.get("name") or menu.get("menu")):
            return menu.get("blocked_reason") or "missing_name"
    return "missing_menu_state"


def capture_menu_ambiguous(state: dict[str, Any]) -> bool:
    return menu_blocked_reason(state) in {"implausible_window_stack", "needs_stronger_decode", "low_confidence", "missing_name", "missing_menu_state"}


def ram_health_from_state(state: dict[str, Any]) -> dict[str, Any]:
    metadata = state.get("metadata") or {}
    position = ((state.get("player") or {}).get("position") or {})
    battle = state.get("battle") or {}
    dialog = state.get("dialog") or {}
    menu = state.get("menu") or {}
    flags = state.get("flags") or {}
    return {
        "position_trusted": position.get("trusted"),
        "position_confidence": position.get("confidence"),
        "position_in_bounds": position.get("in_bounds"),
        "battle_trusted": battle.get("trusted"),
        "battle_type_id": battle.get("type_id"),
        "dialog_window_stack_plausible": dialog.get("window_stack_plausible"),
        "dialog_window_stack_size": dialog.get("window_stack_size"),
        "menu_active": menu.get("active"),
        "menu_name": menu.get("name"),
        "menu_confidence": menu.get("confidence"),
        "menu_needs_stronger_decode": menu.get("needs_stronger_decode"),
        "menu_blocked_reason": menu.get("blocked_reason"),
        "menu_window_stack_plausible": menu.get("window_stack_plausible"),
        "menu_cursor_xy": menu.get("cursor_xy"),
        "menu_selected_item_id": menu.get("selected_item_id"),
        "menu_visible_item_count": len(menu.get("visible_items") or []) if isinstance(menu.get("visible_items"), list) else None,
        "party_count_trusted": flags.get("party_count_trusted"),
        "party_terminator_present": flags.get("party_terminator_present"),
        "bag_count_trusted": flags.get("bag_count_trusted"),
        "bag_terminator_present": flags.get("bag_terminator_present"),
        "read_consistent": metadata.get("read_consistent"),
        "read_started_frame": metadata.get("read_started_frame"),
        "read_finished_frame": metadata.get("read_finished_frame"),
    }


def _menu_name(menu: dict[str, Any]) -> str:
    return str(menu.get("name") or menu.get("menu") or "")


def _cursor_label(menu: dict[str, Any]) -> str:
    return str(menu.get("cursor") or menu.get("cursor_label") or "").lower()


def _move_toward_battle_pack(cursor: str) -> str | None:
    return {
        "fight": "walk_right",
        "pokemon": "walk_up",
        "pkmn": "walk_up",
        "run": "walk_up",
    }.get(cursor)


def _move_toward_battle_run(cursor: str) -> str | None:
    return {
        "fight": "walk_down",
        "pack": "walk_down",
        "pokemon": "walk_right",
        "pkmn": "walk_right",
    }.get(cursor)


def _move_toward_battle_fight(cursor: str) -> str | None:
    return {
        "pack": "walk_left",
        "pokemon": "walk_up",
        "pkmn": "walk_up",
        "run": "walk_up",
    }.get(cursor)


def _selected_item_id(menu: dict[str, Any]) -> int | None:
    value = menu.get("selected_item_id")
    if isinstance(value, int):
        return value
    visible = menu.get("visible_items") or []
    cursor = menu.get("cursor")
    if isinstance(cursor, int) and isinstance(visible, list) and 0 <= cursor < len(visible):
        item = visible[cursor]
        if isinstance(item, dict) and isinstance(item.get("item_id"), int):
            return item["item_id"]
    return None


def _move_toward_visible_item(menu: dict[str, Any], item_ids: frozenset[int]) -> str | None:
    visible = menu.get("visible_items") or []
    cursor = menu.get("cursor")
    if not isinstance(visible, list) or not isinstance(cursor, int):
        return None
    target_index = None
    for index, item in enumerate(visible):
        if isinstance(item, dict) and item.get("item_id") in item_ids:
            target_index = index
            break
    if target_index is None or target_index == cursor:
        return None
    return "walk_down" if target_index > cursor else "walk_up"


def choose_capture_actions(state: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    menu = observed_menu(state)
    name = _menu_name(menu)
    cursor = _cursor_label(menu)
    if name == "battle_main":
        if cursor == "pack":
            return ["press_a"], _controller_status("capture", "open_pack", "press_a", "pack cursor selected")
        move = _move_toward_battle_pack(cursor)
        if move:
            return [move], _controller_status("capture", "move_to_pack", move, f"battle cursor is {cursor}")
    if name in {"bag_pocket", "bag_items", "bag_balls"}:
        selected = _selected_item_id(menu)
        if selected in BALL_ITEM_IDS:
            return ["press_a"], _controller_status("capture", "select_ball", "press_a", "selected visible ball")
        move = _move_toward_visible_item(menu, BALL_ITEM_IDS)
        if move:
            return [move], _controller_status("capture", "move_to_ball", move, "ball visible in bag list")
    if name in {"bag_item_confirm", "throw_confirm"}:
        return ["press_a"], _controller_status("capture", "confirm_throw", "press_a", "throw confirmation")
    return [], {
        "path_source": "battle_fallback",
        "battle_policy": "capture_blocked_missing_menu_state",
        "next_step": None,
        "planned_path_length": None,
        "capture_intent": "throw_ball",
        "blocked_reason": menu_blocked_reason(state),
    }


def choose_fight_actions(state: dict[str, Any], policy: str, reason: str) -> tuple[list[str], dict[str, Any]]:
    menu = observed_menu(state)
    name = _menu_name(menu)
    cursor = _cursor_label(menu)
    if name == "battle_main":
        if cursor == "fight":
            return ["press_a"], _controller_status("fight", "open_fight", "press_a", reason) | {"battle_policy": policy}
        move = _move_toward_battle_fight(cursor)
        if move:
            return [move], _controller_status("fight", "move_to_fight", move, f"battle cursor is {cursor}") | {"battle_policy": policy}
    return [], {
        "path_source": "battle_fallback",
        "battle_policy": f"{policy}_blocked_missing_menu_state",
        "next_step": None,
        "planned_path_length": None,
        "blocked_reason": menu_blocked_reason(state),
    }


def choose_run_actions(state: dict[str, Any], policy: str = "wild_run") -> tuple[list[str], dict[str, Any]]:
    menu = observed_menu(state)
    name = _menu_name(menu)
    cursor = _cursor_label(menu)
    if name == "battle_main":
        if cursor == "run":
            return ["press_a"], _controller_status("run", "confirm", "press_a", "run cursor selected") | {"battle_policy": policy}
        move = _move_toward_battle_run(cursor)
        if move:
            return [move], _controller_status("run", "move_to_run", move, f"battle cursor is {cursor}") | {"battle_policy": policy}
    return ["press_b"], {
        "path_source": "battle_fallback",
        "battle_policy": policy,
        "controller": "run",
        "controller_state": "fallback_press_b_missing_menu",
        "next_step": "press_b",
        "planned_path_length": 1,
        "reason": "run menu state unavailable; using legacy B fallback",
    }


def choose_healing_item_actions(state: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    menu = observed_menu(state)
    name = _menu_name(menu)
    cursor = _cursor_label(menu)
    if name == "battle_main":
        if cursor == "pack":
            return ["press_a"], _controller_status("healing", "open_pack", "press_a", "pack cursor selected")
        move = _move_toward_battle_pack(cursor)
        if move:
            return [move], _controller_status("healing", "move_to_pack", move, f"battle cursor is {cursor}")
    if name in {"bag_pocket", "bag_items"}:
        selected = _selected_item_id(menu)
        if selected in HEALING_ITEM_IDS:
            return ["press_a"], _controller_status("healing", "select_item", "press_a", "selected healing item")
        move = _move_toward_visible_item(menu, HEALING_ITEM_IDS)
        if move:
            return [move], _controller_status("healing", "move_to_item", move, "healing item visible in bag list")
    if name in {"bag_item_confirm", "party_target"}:
        return ["press_a"], _controller_status("healing", "confirm_item", "press_a", "confirm healing item")
    return [], {
        "path_source": "battle_fallback",
        "battle_policy": "healing_blocked_missing_menu_state",
        "next_step": None,
        "planned_path_length": None,
        "blocked_reason": menu_blocked_reason(state),
    }


def _controller_status(controller: str, state: str, action: str, reason: str) -> dict[str, Any]:
    return {
        "path_source": "battle_fallback",
        "battle_policy": f"{controller}_{state}",
        "controller": controller,
        "controller_state": state,
        "next_step": action,
        "planned_path_length": 1,
        "reason": reason,
    }


def inconsistent_state_read(state: dict[str, Any]) -> bool:
    return (state.get("metadata") or {}).get("read_consistent") is False


def navigation_status_from_route_plan(plan: RoutePlan) -> dict[str, Any]:
    transition = plan.next_transition
    status: dict[str, Any] = {
        "path_source": plan.path_source,
        "next_step": plan.next_action,
        "planned_path_length": plan.planned_path_length,
        "map_path": [
            {"map_group": group, "map_number": number} for group, number in plan.map_path
        ],
        "target": {
            "name": plan.target.name,
            "map_group": plan.target.map_key[0],
            "map_number": plan.target.map_key[1],
            "tiles": [{"x": x, "y": y} for x, y in sorted(plan.target.tiles)],
        },
        "transition": None,
    }
    if transition is not None:
        status["transition"] = {
            "kind": transition.kind,
            "source_map": {
                "map_group": transition.source_key[0],
                "map_number": transition.source_key[1],
            },
            "dest_map": {
                "map_group": transition.dest_key[0],
                "map_number": transition.dest_key[1],
            },
            "source_tiles": [{"x": x, "y": y} for x, y in transition.source_tiles],
            "dest_tiles": [{"x": x, "y": y} for x, y in transition.dest_tiles],
            "dest_map_const": transition.warp.dest_map_const if transition.warp else None,
        }
    if plan.same_map_plan is not None:
        status["goal"] = {"x": plan.same_map_plan.goal[0], "y": plan.same_map_plan.goal[1]}
    return status


def diagnose_route_failure(start_key: tuple[int, int], start_tile: tuple[int, int], target: RouteTarget) -> dict[str, Any]:
    start_map = GOLD_MAP_REGISTRY.get(start_key)
    if start_map is None:
        return {"path_source": "no_route", "route_failure": "unknown_start_map", "next_step": None, "planned_path_length": None}
    if not start_map.is_walkable(start_tile):
        return {
            "path_source": "no_route",
            "route_failure": "start_not_walkable",
            "next_step": None,
            "planned_path_length": None,
            "start_tile": {"x": start_tile[0], "y": start_tile[1]},
        }
    target_map = GOLD_MAP_REGISTRY.get(target.map_key)
    if target_map is None:
        return {"path_source": "no_route", "route_failure": "unknown_target_map", "next_step": None, "planned_path_length": None}
    walkable_targets = [tile for tile in target.tiles if target_map.is_walkable(tile)]
    if not walkable_targets:
        return {"path_source": "no_route", "route_failure": "target_not_walkable", "next_step": None, "planned_path_length": None}
    if find_map_path(GOLD_MAP_REGISTRY, start_key, {target.map_key}) is None:
        return {"path_source": "no_route", "route_failure": "no_map_path", "next_step": None, "planned_path_length": None}
    return {"path_source": "no_route", "route_failure": "same_map_unreachable", "next_step": None, "planned_path_length": None}


class GoldAutoplayerV2:
    """Minimal V2 runner shell that emits dashboard-compatible status.

    The real V2 navigation stack should grow behind this entrypoint. Until then,
    selecting V2 is visible and safe: it does not send emulator actions.
    """

    def __init__(self, data_dir: Path | None = None, base_url: str = "http://127.0.0.1:9879"):
        self.data_dir = data_dir or Path(os.environ.get("POKEMON_AGENT_DATA_DIR", "~/.pokemon-agent-gold")).expanduser()
        self.base_url = base_url.rstrip("/")
        self.turn = 0
        self.last_step_result: str | None = None
        self.last_step_action: str | None = None
        self.last_step_verified: bool | None = None
        self.verification_reason: str | None = None
        self.stuck_counter = 0
        self.button_failure_count = 0
        self.battle_no_progress_count = 0
        self.replan_count = 0
        self.recovery_level = 0
        self.blocked_edges_by_map: dict[tuple[int, int], set[tuple[tuple[int, int], str]]] = {}
        self.api_failure_count = 0
        self.api_backoff_seconds = 0.0
        self.next_api_retry_at = 0.0
        self.last_api_error: str | None = None
        self.min_api_backoff_seconds = 1.0
        self.max_api_backoff_seconds = 30.0
        self.recent_failures: deque[dict[str, Any]] = deque(maxlen=10)
        self.recent_learning_transitions: deque[dict[str, Any]] = deque(maxlen=8)
        self.starter_face_up_attempted_at: tuple[int, int] | None = None
        self.elm_return_face_up_attempted_at: tuple[int, int] | None = None
        self.pokecenter_face_up_attempted_at: tuple[int, int] | None = None
        self.pokecenter_aide_face_up_attempted_at: tuple[int, int] | None = None
        self.pokecenter_heal_attempt_signature: tuple[int | None, int | None, int | None, int | None] | None = None
        self.mart_face_left_attempted_at: tuple[int, int] | None = None
        self.nickname_sequence_index = 0
        self.active_starter_choice: str | None = None
        self.active_starter_nickname: str | None = None
        self.battle_run_sequence_index = 0
        self.battle_capture_sequence_index = 0
        self.battle_capture_throw_sequence_index = 0
        self.battle_capture_fallback_cycles = 0
        self.last_battle_identity: tuple[Any, ...] | None = None
        self.last_battle_identity_change: dict[str, Any] | None = None
        self.capture_safety_events: deque[dict[str, Any]] = deque(maxlen=8)
        self.battle_recovery_index = 0
        self.ambiguous_dialogue_index = 0
        self.stale_dialogue_recovery_index = 0
        self.trainer_missing_menu_fight_index = 0
        self.trainer_missing_menu_heal_index = 0
        self.trainer_missing_menu_heal_cycles = 0
        self.new_game_bootstrap_index = 0
        self.mart_buy_sequence_index = 0
        self.grind_sequence_index = 0
        self.adaptive_recovery_index = 0
        self.adaptive_loop_recovery_index = 0
        self.adaptive_mode = False
        self.loop_sleep_seconds = DEFAULT_LOOP_SLEEP_SECONDS
        self.learning = self.load_learning()
        self.shared_memory = LearningMemory(self.data_dir / "pokemon_learning_memory.json")
        self.milestone_save_state_dry_run_default = os.environ.get("POKEMON_MILESTONE_SAVE_STATES_DRY_RUN", "1").lower() not in {"0", "false", "no"}
        self.save_state_manager = MilestoneSaveStateManager(
            self.data_dir,
            self.base_url,
            dry_run=self.milestone_save_state_dry_run_default,
        )
        self.import_v1_learning_snapshot()

    @property
    def control_path(self) -> Path:
        return self.data_dir / "gold_autoplayer_control.json"

    @property
    def status_path(self) -> Path:
        return self.data_dir / "gold_autoplayer_status.json"

    @property
    def event_log_path(self) -> Path:
        return self.data_dir / "gold_autoplayer_v2.jsonl"

    @property
    def learning_path(self) -> Path:
        return self.data_dir / "gold_autoplayer_v2_learning.json"

    def load_learning(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.learning_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("schema_version", 1)
        payload.setdefault("engine", "v2")
        payload.setdefault("action_values", {})
        payload.setdefault("blocked_edges", {})
        payload.setdefault("tile_visits", {})
        payload.setdefault("planner_imitation", {})
        payload.setdefault("turns", 0)
        payload.setdefault("updated_at", 0.0)
        return payload

    def persist_learning(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.learning["turns"] = self.turn
        self.learning["updated_at"] = time.time()
        tmp = self.learning_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.learning, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.learning_path)

    def resolve_starter_choice(self) -> str:
        selection = self.learning.setdefault("starter_selection", {})
        if not isinstance(selection, dict):
            selection = {}
            self.learning["starter_selection"] = selection
        active = selection.get("active_choice")
        if active in STARTER_CHOICES:
            self.active_starter_choice = str(active)
            return str(active)
        last_value = selection.get("last_choice_index", -1)
        last_index = int(last_value) if isinstance(last_value, int) else -1
        next_index = (last_index + 1) % len(STARTER_CHOICE_ORDER)
        choice = STARTER_CHOICE_ORDER[next_index]
        selection.update({"active_choice": choice, "active_choice_index": next_index, "updated_at": time.time()})
        self.active_starter_choice = choice
        self.persist_learning()
        return choice

    def resolve_starter_nickname(self) -> str:
        selection = self.learning.setdefault("starter_selection", {})
        if not isinstance(selection, dict):
            selection = {}
            self.learning["starter_selection"] = selection
        active = selection.get("active_nickname")
        if active in SILLY_STARTER_NICKNAMES:
            self.active_starter_nickname = str(active)
            return str(active)
        last_value = selection.get("last_nickname_index", -1)
        last_index = int(last_value) if isinstance(last_value, int) else -1
        next_index = (last_index + 1) % len(SILLY_STARTER_NICKNAMES)
        nickname = SILLY_STARTER_NICKNAMES[next_index]
        selection.update({"active_nickname": nickname, "active_nickname_index": next_index, "updated_at": time.time()})
        self.active_starter_nickname = nickname
        self.persist_learning()
        return nickname

    def starter_nickname_sequence(self) -> tuple[str, ...]:
        nickname = self.active_starter_nickname or self.resolve_starter_nickname()
        return tuple("press_a" for _ in nickname) + ("press_start", "press_a")

    def finalize_starter_selection(self) -> None:
        selection = self.learning.setdefault("starter_selection", {})
        if not isinstance(selection, dict):
            return
        choice = selection.get("active_choice")
        nickname = selection.get("active_nickname")
        if selection.get("active_choice_index") is not None:
            selection["last_choice_index"] = selection.get("active_choice_index")
        if selection.get("active_nickname_index") is not None:
            selection["last_nickname_index"] = selection.get("active_nickname_index")
        if choice is not None:
            selection["last_choice"] = choice
        if nickname is not None:
            selection["last_nickname"] = nickname
        selection.pop("active_choice", None)
        selection.pop("active_choice_index", None)
        selection.pop("active_nickname", None)
        selection.pop("active_nickname_index", None)
        selection["updated_at"] = time.time()
        self.record_shared_fact(
            "PKM:TEAM",
            f"Selected starter {choice or 'unknown'} with nickname {nickname or 'unknown'}",
            confidence="observed",
            data={"kind": "starter_selection", "starter": choice, "nickname": nickname, "evidence_count": 1},
        )
        self.persist_learning()

    def lead_health_signature(self, snapshot: Any) -> tuple[int | None, int | None, int | None, int | None] | None:
        lead = getattr(snapshot, "lead", None)
        if lead is None:
            return None
        return (lead.species_id, lead.level, lead.hp, lead.max_hp)

    def suppress_repeated_pokecenter_heal(self, snapshot: Any) -> bool:
        signature = self.lead_health_signature(snapshot)
        lead = getattr(snapshot, "lead", None)
        if signature is None or lead is None:
            return False
        if lead.hp_ratio is not None and lead.hp_ratio >= FALKNER_MIN_HP_RATIO:
            self.pokecenter_heal_attempt_signature = None
            return False
        return self.pokecenter_heal_attempt_signature == signature

    def target_after_pokecenter_heal_attempt(self, snapshot: Any) -> RouteTarget:
        lead = getattr(snapshot, "lead", None)
        if lead is not None and lead.hp_ratio is not None and lead.hp_ratio < FALKNER_MIN_HP_RATIO:
            return ROUTE31_GRIND_TARGET
        if lead is not None and lead.level is not None and lead.level < FALKNER_MIN_LEVEL:
            return ROUTE31_GRIND_TARGET
        return FALKNER_TARGET

    def repeated_press_a_same_state_count(self, state: dict[str, Any]) -> int:
        key = self.state_key_for_learning(state)
        count = 0
        for transition in reversed(self.recent_learning_transitions):
            if transition.get("before") != key or transition.get("after") != key:
                break
            if transition.get("action") == "press_a":
                count += 1
        return count

    def stale_dialogue_recovery_action(self, state: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if self.stale_dialogue_recovery_index >= len(STALE_DIALOGUE_RECOVERY_SEQUENCE):
            self.stale_dialogue_recovery_index = 0
        action = STALE_DIALOGUE_RECOVERY_SEQUENCE[self.stale_dialogue_recovery_index]
        self.stale_dialogue_recovery_index += 1
        return action, {
            "path_source": "stale_dialogue_recovery",
            "next_step": action,
            "planned_path_length": len(STALE_DIALOGUE_RECOVERY_SEQUENCE) - self.stale_dialogue_recovery_index + 1,
            "recovery_reason": "repeated press_a left the game in the same state; treating dialogue detection as stale",
            "same_state_press_a_count": self.repeated_press_a_same_state_count(state),
            "dialogue_recovery_index": self.stale_dialogue_recovery_index - 1,
        }

    def learning_summary(self) -> dict[str, Any]:
        action_values = self.learning.get("action_values") if isinstance(self.learning, dict) else {}
        blocked_edges = self.learning.get("blocked_edges") if isinstance(self.learning, dict) else {}
        tile_visits = self.learning.get("tile_visits") if isinstance(self.learning, dict) else {}
        shared = self.shared_learning_summary()
        return {
            "path": str(self.learning_path),
            "shared": shared,
            "state_action_keys": len(action_values) if isinstance(action_values, dict) else 0,
            "blocked_edges_learned": len(blocked_edges) if isinstance(blocked_edges, dict) else 0,
            "tiles_visited": len(tile_visits) if isinstance(tile_visits, dict) else 0,
            "turns": self.learning.get("turns", 0) if isinstance(self.learning, dict) else 0,
            "last_transition": self.learning.get("last_transition") if isinstance(self.learning, dict) else None,
            "recent_transitions": list(self.recent_learning_transitions),
            "shared_counts": self.shared_learning_counts(),
            "v1_teacher_import": self.learning.get("v1_teacher_import", {}) if isinstance(self.learning, dict) else {},
        }

    def shared_learning_counts(self) -> dict[str, int]:
        try:
            rows = self.shared_memory.facts_for(game_id="gold_silver")
        except Exception:
            return {}
        counts: dict[str, int] = {}
        for row in rows:
            category = str(row.get("category") or "PKM:PROGRESS")
            counts[category] = counts.get(category, 0) + 1
        return counts

    def recent_shared_facts(self, limit: int = 8) -> list[dict[str, Any]]:
        try:
            rows = self.shared_memory.facts_for(game_id="gold_silver")
        except Exception:
            return []
        rows.sort(key=lambda row: float(row.get("updated_at") or 0.0), reverse=True)
        return rows[:limit]

    def record_resource_facts(self, resources: dict[str, Any], story_decision: Any) -> None:
        objective = getattr(story_decision, "objective_key", "unknown")
        for blocker in resources.get("readiness_blockers") or []:
            self.record_shared_fact(
                "PKM:RESOURCE",
                f"Resource gate for {objective}: {blocker}",
                confidence="observed",
                data={"kind": "readiness_resource_gate", "objective": objective, "blocker": blocker, "resources": resources, "evidence_count": 1},
            )
        if resources.get("broke_no_balls"):
            self.record_shared_fact(
                "PKM:RESOURCE",
                f"No balls and money below Pokeball cost during {objective}",
                confidence="observed",
                data={"kind": "broke_no_balls", "objective": objective, "resources": resources, "evidence_count": 1},
            )

    def record_readiness_facts(self, blockers: list[str], phase: str, navigation: dict[str, Any]) -> None:
        for blocker in blockers:
            if blocker in {"engine_not_selected", "disabled"}:
                continue
            self.record_shared_fact(
                "PKM:POLICY",
                f"Readiness blocker in {phase}: {blocker}",
                confidence="observed",
                data={"kind": "readiness_blocker", "phase": phase, "blocker": blocker, "navigation": {k: navigation.get(k) for k in ("path_source", "battle_policy", "next_step")}, "evidence_count": 1},
            )

    def state_key_for_learning(self, state: dict[str, Any] | None) -> str:
        if not state:
            return "state:unavailable"
        try:
            snapshot = snapshot_from_state(state)
            story = explain_story_objective(state)
            battle = "battle" if snapshot.battle.in_battle else "overworld"
            return f"{snapshot.position.map_group}:{snapshot.position.map_number}:{snapshot.position.x}:{snapshot.position.y}:{battle}:{story.objective_key}"
        except Exception:
            return "state:untrusted"

    def shared_learning_summary(self) -> dict[str, Any]:
        try:
            payload = self.shared_memory.load()
        except Exception:
            payload = {"facts": []}
        facts = payload.get("facts") if isinstance(payload, dict) else []
        return {
            "path": str(self.shared_memory.path),
            "facts": len(facts) if isinstance(facts, list) else 0,
            "updated_at": payload.get("updated_at") if isinstance(payload, dict) else None,
        }

    def record_shared_fact(
        self,
        category: str,
        text: str,
        confidence: str = "observed",
        data: dict[str, Any] | None = None,
        source: str | None = None,
    ) -> None:
        try:
            self.shared_memory.add_fact(LearningFact(
                category=category,
                game_id="gold_silver",
                text=text,
                confidence=confidence,
                source=source or ("v2_adaptive" if self.adaptive_mode else "v2"),
                data=data or {},
            ))
        except Exception:
            return

    def import_v1_learning_snapshot(self) -> None:
        """Mirror durable V1 world/policy evidence into shared learning memory."""
        marker = self.learning.get("v1_import_marker") if isinstance(self.learning, dict) else None
        result = import_gold_v1_teacher_snapshot(
            self.data_dir,
            self.shared_memory,
            previous_marker=marker if isinstance(marker, dict) else None,
            force=not bool(self.learning.get("v1_teacher_import")),
        )
        counts = result.get("counts") if isinstance(result, dict) else None
        next_marker = result.get("marker") if isinstance(result, dict) else None
        if not isinstance(next_marker, dict):
            return
        self.learning["v1_import_marker"] = next_marker
        if isinstance(counts, dict) and any(counts.values()):
            self.learning["v1_teacher_import"] = {**counts, "updated_at": result.get("updated_at", time.time())}

    @staticmethod
    def _read_optional_json(path: Path) -> dict[str, Any]:
        return read_optional_json(path)

    def reward_for_transition(
        self,
        before_state: dict[str, Any] | None,
        after_state: dict[str, Any] | None,
        action: str,
        verified: bool,
        reason: str,
        post_result: str,
    ) -> float:
        reward = 0.0
        if post_result == "ok":
            reward += 0.05
        if verified:
            reward += 0.25
        if action.startswith("walk_") and not verified and post_result == "ok":
            reward -= 0.5
        if reason == "position_did_not_advance":
            reward -= 0.75
        if after_state is None or before_state is None:
            return reward
        try:
            before = snapshot_from_state(before_state)
            after = snapshot_from_state(after_state)
        except Exception:
            return reward
        if before.position.map_key != after.position.map_key:
            before_story_progress = (
                before.story.gave_mystery_egg_to_elm,
                before.story.learned_to_catch_pokemon,
                before.story.has_zephyr_badge,
                len(before.party),
                badge_count_from_player(before_state.get("player") or {}),
            )
            after_story_progress = (
                after.story.gave_mystery_egg_to_elm,
                after.story.learned_to_catch_pokemon,
                after.story.has_zephyr_badge,
                len(after.party),
                badge_count_from_player(after_state.get("player") or {}),
            )
            if reason == "map_transition_observed" and before_story_progress == after_story_progress:
                # Crossing a reversible map boundary verifies button/movement mechanics,
                # but it is not strategic progress by itself. Keep a small shaping
                # reward so the action is not treated as failed, while avoiding the
                # high reward that taught A<->B boundary ping-pong as a goal.
                reward += 0.1
            else:
                reward += 1.5
        elif before.position.tile != after.position.tile:
            reward += 0.6
        tile_key = f"{after.position.map_group}:{after.position.map_number}:{after.position.x}:{after.position.y}"
        visits = int((self.learning.get("tile_visits") or {}).get(tile_key, 0))
        reward += 0.2 / ((visits + 1) ** 0.5)
        if len(after.party) > len(before.party):
            reward += 5.0
        before_badges = badge_count_from_player(before_state.get("player") or {})
        after_badges = badge_count_from_player(after_state.get("player") or {})
        if after_badges > before_badges:
            reward += 20.0
        return round(reward, 4)

    def record_learning_transition(
        self,
        action: str,
        verified: bool,
        reason: str,
        post_result: str,
        before_state: dict[str, Any] | None,
        after_state: dict[str, Any] | None,
    ) -> float:
        reward = self.reward_for_transition(before_state, after_state, action, verified, reason, post_result)
        state_key = self.state_key_for_learning(before_state)
        after_key = self.state_key_for_learning(after_state)
        action_values = self.learning.setdefault("action_values", {})
        state_values = action_values.setdefault(state_key, {})
        stats = state_values.setdefault(action, {"count": 0, "reward_total": 0.0, "mean_reward": 0.0, "last_reason": ""})
        stats["count"] = int(stats.get("count", 0)) + 1
        stats["reward_total"] = round(float(stats.get("reward_total", 0.0)) + reward, 4)
        stats["mean_reward"] = round(stats["reward_total"] / max(1, stats["count"]), 4)
        stats["last_reason"] = reason
        stats["last_verified"] = verified
        stats["updated_at"] = time.time()
        if after_state is not None:
            try:
                after = snapshot_from_state(after_state)
                before = snapshot_from_state(before_state) if before_state is not None else None
                if before is not None and after.has_starter and not before.has_starter:
                    self.finalize_starter_selection()
                tile_key = f"{after.position.map_group}:{after.position.map_number}:{after.position.x}:{after.position.y}"
                visits = self.learning.setdefault("tile_visits", {})
                visits[tile_key] = int(visits.get(tile_key, 0)) + 1
            except Exception:
                pass
        self.learning["last_transition"] = {
            "state_key": state_key,
            "after_state_key": after_key,
            "action": action,
            "reward": reward,
            "verified": verified,
            "reason": reason,
            "post_result": post_result,
            "updated_at": time.time(),
        }
        self.recent_learning_transitions.append({
            "before": state_key,
            "after": after_key,
            "action": action,
            "verified": verified,
            "reward": reward,
            "reason": reason,
        })
        if verified and reward > 0 and not (reason == "map_transition_observed" and reward < 0.75):
            self.record_shared_fact(
                "PKM:PROGRESS",
                f"{action} made verified progress from {state_key} to {after_key}",
                confidence="verified",
                data={"action": action, "before": state_key, "after": after_key, "reward": reward, "evidence_count": 1},
            )
        elif not verified:
            failure_data = {"action": action, "state_key": state_key, "reason": reason, "post_result": post_result, "evidence_count": 1}
            self.record_shared_fact("PKM:STUCK", f"{action} failed at {state_key}: {reason}", confidence="observed", data=failure_data)
            self.record_shared_fact("PKM:FAILURE", f"Avoid repeating {action} in {state_key} after {reason}", confidence="observed", data=failure_data)
        if before_state is not None or after_state is not None:
            self.record_shared_fact(
                "PKM:OUTCOME",
                f"{action} outcome at {state_key}: reward {reward} ({reason})",
                confidence="verified" if verified else "observed",
                data={"action": action, "before": state_key, "after": after_key, "reward": reward, "verified": verified, "reason": reason, "post_result": post_result, "evidence_count": 1},
            )
        return reward

    def read_control(self) -> dict[str, Any]:
        defaults: dict[str, Any] = {
            "enabled": True,
            "engine": "v1",
            "objective": DEFAULT_OBJECTIVE,
            "movement_bias": "west_north",
            "dialogue_speed": "fast",
            "guidance_prompt": "",
            "dry_run": True,
            "allow_overworld_movement": False,
            "allow_battle_actions": False,
        }
        try:
            payload = json.loads(self.control_path.read_text())
        except Exception:
            return defaults
        if isinstance(payload, dict):
            defaults.update(payload)
        return defaults

    def write_status(self, status: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(status, indent=2, sort_keys=True))
        tmp.replace(self.status_path)

    def log_event(self, event: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = dict(event)
        payload.setdefault("engine", "v2")
        payload.setdefault("turn", self.turn)
        payload.setdefault("updated_at", time.time())
        with self.event_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def request_json(self, path: str, timeout: float = 5.0) -> dict[str, Any]:
        with urllib.request.urlopen(f"{self.base_url}{path}", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_actions(self, actions: list[str], timeout: float = 10.0) -> dict[str, Any]:
        payload = json.dumps({"actions": actions}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/action",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def record_api_failure(self, exc: BaseException) -> None:
        self.api_failure_count += 1
        self.api_backoff_seconds = self.min_api_backoff_seconds if self.api_backoff_seconds <= 0 else min(self.api_backoff_seconds * 2, self.max_api_backoff_seconds)
        self.next_api_retry_at = time.time() + self.api_backoff_seconds
        self.last_api_error = f"{type(exc).__name__}: {exc}"

    def record_api_success(self) -> None:
        self.api_failure_count = 0
        self.api_backoff_seconds = 0.0
        self.next_api_retry_at = 0.0
        self.last_api_error = None

    def record_action_outcome(
        self,
        action: str,
        verified: bool,
        reason: str,
        post_result: str = "ok",
        before_state: dict[str, Any] | None = None,
    ) -> None:
        self.last_step_result = post_result
        self.last_step_action = action
        self.last_step_verified = verified
        self.verification_reason = reason
        executed = post_result == "ok"
        snapshot = snapshot_from_state(before_state) if before_state else None
        if (
            action == "walk_up"
            and executed
            and snapshot is not None
            and snapshot.position.map_key == STARTER_TARGET.map_key
            and snapshot.position.tile in STARTER_TARGET.tiles
            and not snapshot.has_starter
        ):
            self.starter_face_up_attempted_at = snapshot.position.tile
            self.last_step_verified = True
            self.verification_reason = "starter_facing_attempted"
            self.stuck_counter = 0
            self.recovery_level = 0
            return
        if (
            action == "walk_left"
            and executed
            and snapshot is not None
            and snapshot.position.map_key == VIOLET_MART_BUY_TARGET.map_key
            and snapshot.position.tile in VIOLET_MART_BUY_TARGET.tiles
        ):
            self.mart_face_left_attempted_at = snapshot.position.tile
            self.last_step_verified = True
            self.verification_reason = "mart_clerk_facing_attempted"
            self.stuck_counter = 0
            self.recovery_level = 0
            return
        if (
            action == "walk_up"
            and not verified
            and executed
            and snapshot is not None
            and snapshot.position.map_key == ELMS_LAB_RETURN_TARGET.map_key
            and snapshot.position.tile in ELMS_LAB_RETURN_TARGET.tiles
            and snapshot.story.got_mystery_egg_from_mr_pokemon
            and not snapshot.story.gave_mystery_egg_to_elm
        ):
            self.elm_return_face_up_attempted_at = snapshot.position.tile
            self.last_step_verified = True
            self.verification_reason = "elm_return_facing_attempted"
            self.stuck_counter = 0
            self.recovery_level = 0
            return
        if (
            action == "walk_up"
            and not verified
            and executed
            and snapshot is not None
            and snapshot.position.map_key == VIOLET_POKECENTER_AIDE_TARGET.map_key
            and snapshot.position.tile in VIOLET_POKECENTER_AIDE_TARGET.tiles
        ):
            self.pokecenter_aide_face_up_attempted_at = snapshot.position.tile
            self.last_step_verified = True
            self.verification_reason = "pokecenter_aide_facing_attempted"
            self.stuck_counter = 0
            self.recovery_level = 0
            return
        if (
            action == "walk_up"
            and not verified
            and executed
            and snapshot is not None
            and snapshot.position.map_key == VIOLET_POKECENTER_HEAL_TARGET.map_key
            and snapshot.position.tile in VIOLET_POKECENTER_HEAL_TARGET.tiles
        ):
            self.pokecenter_face_up_attempted_at = snapshot.position.tile
            self.last_step_verified = True
            self.verification_reason = "pokecenter_nurse_facing_attempted"
            self.stuck_counter = 0
            self.recovery_level = 0
            return
        if (
            action == "press_a"
            and executed
            and snapshot is not None
            and snapshot.position.map_key == VIOLET_POKECENTER_HEAL_TARGET.map_key
            and snapshot.position.tile in VIOLET_POKECENTER_HEAL_TARGET.tiles
            and self.pokecenter_face_up_attempted_at == snapshot.position.tile
        ):
            self.pokecenter_heal_attempt_signature = self.lead_health_signature(snapshot)
        if action in (*TEXT_INPUT_END_SEQUENCE, "press_start") and likely_text_input_keyboard(before_state or {}):
            sequence = self.starter_nickname_sequence() if likely_starter_nickname_screen(before_state or {}) else TEXT_INPUT_END_SEQUENCE
            expected = sequence[self.nickname_sequence_index] if self.nickname_sequence_index < len(sequence) else None
            if action == expected and executed:
                self.nickname_sequence_index += 1
        if action in BATTLE_RUN_SEQUENCE and likely_untrusted_wild_battle_main(before_state or {}) and not likely_wild_grind_missing_menu(before_state or {}):
            expected = BATTLE_RUN_SEQUENCE[self.battle_run_sequence_index] if self.battle_run_sequence_index < len(BATTLE_RUN_SEQUENCE) else None
            if action == expected and executed:
                self.battle_run_sequence_index += 1
        open_pack_complete_before_action = self.battle_capture_sequence_index >= len(BATTLE_CAPTURE_OPEN_PACK_SEQUENCE)
        if action in BATTLE_CAPTURE_OPEN_PACK_SEQUENCE and likely_untrusted_wild_battle_main(before_state or {}):
            catch_decision = choose_catch_action(snapshot_from_state(before_state or {}))
            expected = BATTLE_CAPTURE_OPEN_PACK_SEQUENCE[self.battle_capture_sequence_index] if self.battle_capture_sequence_index < len(BATTLE_CAPTURE_OPEN_PACK_SEQUENCE) else None
            if catch_decision.action == "throw_ball" and action == expected and executed:
                self.battle_capture_sequence_index += 1
        if open_pack_complete_before_action and action in BATTLE_CAPTURE_THROW_BALL_SEQUENCE and likely_untrusted_wild_battle_main(before_state or {}):
            catch_decision = choose_catch_action(snapshot_from_state(before_state or {}))
            expected = BATTLE_CAPTURE_THROW_BALL_SEQUENCE[self.battle_capture_throw_sequence_index] if self.battle_capture_throw_sequence_index < len(BATTLE_CAPTURE_THROW_BALL_SEQUENCE) else None
            if catch_decision.action == "throw_ball" and action == expected and executed:
                self.battle_capture_throw_sequence_index += 1
        if action in BATTLE_RECOVERY_SEQUENCE and before_state is not None and (before_state.get("battle") or {}).get("in_battle") is True:
            expected = BATTLE_RECOVERY_SEQUENCE[self.battle_recovery_index] if self.battle_recovery_index < len(BATTLE_RECOVERY_SEQUENCE) else None
            if action == expected and executed:
                self.battle_recovery_index += 1
        missing_menu_fight_active = (
            likely_trainer_battle_missing_menu(before_state or {})
            or likely_wild_grind_missing_menu(before_state or {})
            or (
                likely_untrusted_wild_battle_main(before_state or {})
                and (
                    choose_catch_action(snapshot_from_state(before_state or {})).action == "weaken"
                    or self.battle_capture_fallback_cycles >= 2
                    or self.battle_no_progress_count >= BATTLE_NO_PROGRESS_THRESHOLD
                )
            )
        )
        if missing_menu_fight_active and action in (*TRAINER_MISSING_MENU_FIGHT_SEQUENCE, *missing_menu_fight_sequence_for_state(before_state or {})):
            sequence = missing_menu_fight_sequence_for_state(before_state or {})
            expected = sequence[self.trainer_missing_menu_fight_index] if self.trainer_missing_menu_fight_index < len(sequence) else None
            if action == expected and executed:
                self.trainer_missing_menu_fight_index += 1
        if action in TRAINER_MISSING_MENU_HEAL_SEQUENCE and likely_trainer_battle_missing_menu(before_state or {}):
            expected = TRAINER_MISSING_MENU_HEAL_SEQUENCE[self.trainer_missing_menu_heal_index] if self.trainer_missing_menu_heal_index < len(TRAINER_MISSING_MENU_HEAL_SEQUENCE) else None
            if action == expected and executed:
                self.trainer_missing_menu_heal_index += 1
                if self.trainer_missing_menu_heal_index >= len(TRAINER_MISSING_MENU_HEAL_SEQUENCE):
                    self.trainer_missing_menu_heal_cycles += 1
        if missing_menu_fight_active and action in (*TRAINER_MISSING_MENU_HEAL_SEQUENCE, *TRAINER_MISSING_MENU_FIGHT_SEQUENCE, *missing_menu_fight_sequence_for_state(before_state or {})) and not verified and executed:
            self.last_step_verified = True
            self.verification_reason = "battle_missing_menu_sequence_step_posted"
            self.button_failure_count = 0
            return
        if action in BATTLE_CAPTURE_OPEN_PACK_SEQUENCE and likely_untrusted_wild_battle_main(before_state or {}) and not verified and executed:
            catch_decision = choose_catch_action(snapshot_from_state(before_state or {}))
            if catch_decision.action == "throw_ball":
                self.last_step_verified = True
                self.verification_reason = "battle_capture_open_pack_sequence_step_posted"
                self.button_failure_count = 0
                return
        if open_pack_complete_before_action and action in BATTLE_CAPTURE_THROW_BALL_SEQUENCE and likely_untrusted_wild_battle_main(before_state or {}) and not verified and executed:
            catch_decision = choose_catch_action(snapshot_from_state(before_state or {}))
            if catch_decision.action == "throw_ball":
                self.last_step_verified = True
                self.verification_reason = "battle_capture_throw_ball_sequence_step_posted"
                self.button_failure_count = 0
                return
        if action in (*MART_BUY_ONE_SEQUENCE, *MART_BUY_POTION_FROM_TOP_SEQUENCE) and likely_mart_purchase_screen(before_state or {}):
            sequence = mart_purchase_sequence(before_state or {})
            expected = sequence[self.mart_buy_sequence_index] if self.mart_buy_sequence_index < len(sequence) else None
            if action == expected and executed:
                self.mart_buy_sequence_index += 1
        if action in NEW_GAME_BOOTSTRAP_SEQUENCE and before_state is not None:
            map_info = before_state.get("map") or {}
            if map_info.get("map_group") == 0 and map_info.get("map_number") == 0:
                expected = NEW_GAME_BOOTSTRAP_SEQUENCE[self.new_game_bootstrap_index] if self.new_game_bootstrap_index < len(NEW_GAME_BOOTSTRAP_SEQUENCE) else None
                if action == expected and executed:
                    self.new_game_bootstrap_index += 1
        if action.startswith("walk_") and not verified:
            if not executed:
                return
            self.stuck_counter += 1
            if before_state is not None and (before_state.get("battle") or {}).get("in_battle") is True:
                self.record_recent_failure(action, reason, post_result, before_state, blocked_edge=False)
                if self.stuck_counter >= STUCK_CIRCUIT_BREAKER_THRESHOLD:
                    self.recovery_level = 2
                    self.replan_count += 1
                return
            if text_or_dialogue_suspected(before_state):
                self.stuck_counter = 0
                self.recovery_level = 0
                self.record_recent_failure(action, reason, post_result, before_state, blocked_edge=False)
                return
            blocked_edge = False
            if reason == "position_did_not_advance":
                self.record_blocked_edge(before_state, action)
                self.recovery_level = max(self.recovery_level, 1)
                blocked_edge = before_state is not None
            self.record_recent_failure(action, reason, post_result, before_state, blocked_edge=blocked_edge)
            if self.stuck_counter >= STUCK_CIRCUIT_BREAKER_THRESHOLD:
                self.recovery_level = 2
                self.replan_count += 1
        elif not verified and executed:
            self.button_failure_count += 1
            self.record_recent_failure(action, reason, post_result, before_state, blocked_edge=False)
            if self.button_failure_count >= STUCK_CIRCUIT_BREAKER_THRESHOLD:
                self.recovery_level = 2
                self.replan_count += 1
        elif verified:
            self.stuck_counter = 0
            self.button_failure_count = 0
            self.recovery_level = 0
            self.adaptive_recovery_index = 0
            self.battle_recovery_index = 0
            self.ambiguous_dialogue_index = 0
            if action.startswith("walk_") or reason in {"map_transition_observed", "position_changed_after_walk", "position_advanced_after_walk"}:
                self.stale_dialogue_recovery_index = 0
            if reason == "map_transition_observed":
                self.blocked_edges_by_map.clear()

    def record_battle_semantic_progress(
        self,
        before_state: dict[str, Any] | None,
        after_state: dict[str, Any] | None,
        action: str,
        navigation: dict[str, Any] | None,
    ) -> None:
        if not isinstance(navigation, dict) or navigation.get("path_source") != "battle_fallback":
            self.battle_no_progress_count = 0
            return
        if action not in {"press_a", "press_b"}:
            return
        before = battle_progress_signature(before_state)
        after = battle_progress_signature(after_state)
        if before is None or after is None or after == ("not_in_battle",) or before != after:
            self.battle_no_progress_count = 0
            return
        self.battle_no_progress_count += 1
        if self.battle_no_progress_count >= BATTLE_NO_PROGRESS_THRESHOLD:
            self.recovery_level = max(self.recovery_level, 2)
            self.verification_reason = "battle_no_semantic_progress"
            self.record_recent_failure(action, "battle_no_semantic_progress", "ok", before_state, blocked_edge=False)
            self.record_shared_fact(
                "PKM:STUCK",
                f"Battle fallback made no semantic progress after {self.battle_no_progress_count} button actions at {self.state_key_for_learning(before_state)}",
                confidence="observed",
                data={"action": action, "battle_signature": before, "evidence_count": 1},
            )

    def record_recent_failure(
        self,
        action: str,
        reason: str,
        post_result: str,
        before_state: dict[str, Any] | None,
        *,
        blocked_edge: bool,
    ) -> None:
        snapshot = snapshot_from_state(before_state) if before_state else None
        key = snapshot.position.map_key if snapshot else None
        tile = snapshot.position.tile if snapshot else None
        self.recent_failures.append({
            "turn": self.turn,
            "action": action,
            "reason": reason,
            "post_result": post_result,
            "map": {"map_group": key[0], "map_number": key[1]} if key else None,
            "tile": {"x": tile[0], "y": tile[1]} if tile else None,
            "blocked_edge": blocked_edge,
            "recovery_level": self.recovery_level,
        })

    def reset_recovery_after_manual_progress(self, state: dict[str, Any]) -> None:
        if self.stuck_counter == 0 and self.recovery_level == 0 and self.button_failure_count == 0:
            return
        snapshot = snapshot_from_state(state)
        current_key = snapshot.position.map_key
        current_tile = snapshot.position.tile
        if current_key is None or current_tile is None:
            return
        for failure in reversed(self.recent_failures):
            failure_map = failure.get("map") or {}
            failure_tile = failure.get("tile") or {}
            previous_key = (failure_map.get("map_group"), failure_map.get("map_number"))
            previous_tile = (failure_tile.get("x"), failure_tile.get("y"))
            if previous_key[0] is None or previous_tile[0] is None:
                continue
            action = failure.get("action")
            if current_key == previous_key and isinstance(action, str) and observed_walk_progress(previous_tile, current_tile, action):
                edges = self.blocked_edges_by_map.get(current_key)
                if edges is not None:
                    edges.discard((previous_tile, action.removeprefix("walk_")))
                    if not edges:
                        self.blocked_edges_by_map.pop(current_key, None)
            if current_key != previous_key or current_tile != previous_tile:
                self.stuck_counter = 0
                self.button_failure_count = 0
                self.recovery_level = 0
            return

    def record_blocked_edge(self, state: dict[str, Any] | None, action: str) -> None:
        if state is None or not action.startswith("walk_"):
            return
        snapshot = snapshot_from_state(state)
        key = snapshot.position.map_key
        tile = snapshot.position.tile
        direction = action.removeprefix("walk_")
        if key is None or tile is None:
            return
        self.blocked_edges_by_map.setdefault(key, set()).add((tile, direction))
        edge_key = f"{key[0]}:{key[1]}:{tile[0]}:{tile[1]}:{direction}"
        blocked = self.learning.setdefault("blocked_edges", {})
        stats = blocked.setdefault(edge_key, {"count": 0, "map_group": key[0], "map_number": key[1], "x": tile[0], "y": tile[1], "direction": direction})
        stats["count"] = int(stats.get("count", 0)) + 1
        stats["updated_at"] = time.time()

    def blocked_edges_for_planner(self) -> dict[tuple[int, int], frozenset[tuple[tuple[int, int], str]]]:
        return {key: frozenset(edges) for key, edges in self.blocked_edges_by_map.items()}

    def blocked_edge_status(self) -> dict[str, Any]:
        blocked_by_map = [
            {
                "map_group": key[0],
                "map_number": key[1],
                "edges": [{"x": tile[0], "y": tile[1], "direction": direction} for tile, direction in sorted(edges)],
            }
            for key, edges in sorted(self.blocked_edges_by_map.items())
        ]
        return {
            "blocked_edges": sum(len(edges) for edges in self.blocked_edges_by_map.values()),
            "blocked_edges_by_map": blocked_by_map,
            "button_failures": self.button_failure_count,
            "recent_failures": list(self.recent_failures),
            "battle_identity": self.last_battle_identity,
            "battle_identity_change": self.last_battle_identity_change,
            "capture_safety": list(self.capture_safety_events),
        }

    def reset_battle_sequences(self, reason: str) -> None:
        self.battle_run_sequence_index = 0
        self.battle_capture_sequence_index = 0
        self.battle_capture_throw_sequence_index = 0
        self.battle_capture_fallback_cycles = 0
        self.battle_recovery_index = 0
        self.trainer_missing_menu_fight_index = 0
        self.trainer_missing_menu_heal_index = 0
        self.trainer_missing_menu_heal_cycles = 0
        self.battle_no_progress_count = 0
        self.last_battle_identity_change = {"reason": reason, "updated_at": time.time()}

    def sync_battle_identity(self, state: dict[str, Any] | None) -> None:
        identity = battle_identity(state)
        if identity == self.last_battle_identity:
            return
        previous = self.last_battle_identity
        self.last_battle_identity = identity
        self.reset_battle_sequences("battle_identity_changed" if identity is not None else "battle_ended")
        self.last_battle_identity_change = {
            "reason": "battle_identity_changed" if identity is not None else "battle_ended",
            "previous": previous,
            "current": identity,
            "updated_at": time.time(),
        }

    def record_capture_safety_event(self, state: dict[str, Any], reason: str, action: str | None = None) -> None:
        self.capture_safety_events.append({
            "reason": reason,
            "action": action,
            "blocked_reason": menu_blocked_reason(state),
            "battle_identity": battle_identity(state),
            "open_pack_index": self.battle_capture_sequence_index,
            "throw_index": self.battle_capture_throw_sequence_index,
            "updated_at": time.time(),
        })

    def recovery_circuit_open(self) -> bool:
        return self.recovery_level >= 2


    def choose_adaptive_button_recovery_action(self) -> str:
        action = ADAPTIVE_BUTTON_RECOVERY_SEQUENCE[self.adaptive_recovery_index % len(ADAPTIVE_BUTTON_RECOVERY_SEQUENCE)]
        self.adaptive_recovery_index += 1
        return action

    def adaptive_oscillation_detected(self) -> bool:
        if len(self.recent_learning_transitions) < 4:
            return False
        rows = list(self.recent_learning_transitions)[-4:]
        if not all(row.get("verified") for row in rows):
            return False
        pairs = [(row.get("before"), row.get("after")) for row in rows]
        return pairs[0] == pairs[2] and pairs[1] == pairs[3] and pairs[0] == (pairs[1][1], pairs[1][0])

    def choose_adaptive_loop_recovery_action(self, planned_action: str | None = None) -> str:
        for _ in range(len(ADAPTIVE_LOOP_RECOVERY_SEQUENCE)):
            action = ADAPTIVE_LOOP_RECOVERY_SEQUENCE[self.adaptive_loop_recovery_index % len(ADAPTIVE_LOOP_RECOVERY_SEQUENCE)]
            self.adaptive_loop_recovery_index += 1
            if action != planned_action:
                return action
        return "press_b"

    def tile_visit_count(self, key: tuple[int, int], tile: tuple[int, int]) -> int:
        visits = self.learning.get("tile_visits") if isinstance(self.learning, dict) else {}
        return int((visits or {}).get(f"{key[0]}:{key[1]}:{tile[0]}:{tile[1]}", 0) or 0)

    def least_visited_neighbor_action(self, key: tuple[int, int], tile: tuple[int, int], *, avoid_action: str | None = None) -> str | None:
        map_spec = GOLD_MAP_REGISTRY.get(key)
        if map_spec is None:
            return None
        blocked_here = self.blocked_directions_at_tile(key, tile)
        candidates: list[tuple[int, str]] = []
        for neighbor, direction in map_spec.neighbors(tile):
            action = f"walk_{direction}"
            if direction in blocked_here or action == avoid_action:
                continue
            candidates.append((self.tile_visit_count(key, neighbor), action))
        if not candidates:
            return None
        candidates.sort(key=lambda row: (row[0], row[1]))
        return candidates[0][1]

    def v1_teacher_action_for_tile(self, key: tuple[int, int], tile: tuple[int, int], *, avoid_action: str | None = None) -> str | None:
        map_spec = GOLD_MAP_REGISTRY.get(key)
        if map_spec is None:
            return None
        coord_key = f"{key[0] * 256 + key[1]}:{tile[1]}:{tile[0]}"
        blocked_here = self.blocked_directions_at_tile(key, tile)
        candidates: list[tuple[int, str]] = []
        try:
            facts = self.shared_memory.facts_for(category="PKM:MAP", game_id="gold_silver")
        except Exception:
            return None
        valid_actions = {f"walk_{direction}" for _, direction in map_spec.neighbors(tile)}
        for fact in facts:
            if fact.get("source") != "v1_teacher":
                continue
            data = fact.get("data") if isinstance(fact.get("data"), dict) else {}
            if data.get("kind") != "v1_open_edge" or data.get("from") != coord_key:
                continue
            action = str(data.get("action") or "")
            direction = action.removeprefix("walk_") if action.startswith("walk_") else ""
            if action not in valid_actions or action == avoid_action or direction in blocked_here:
                continue
            candidates.append((int(data.get("open_count", 0) or 0), action))
        if not candidates:
            return None
        candidates.sort(key=lambda row: (-row[0], row[1]))
        return candidates[0][1]

    def choose_safe_exploratory_action(
        self,
        key: tuple[int, int],
        tile: tuple[int, int],
        *,
        fallback_from: str,
        route_failure: str | None = None,
    ) -> tuple[list[str], dict[str, Any]]:
        action = self.v1_teacher_action_for_tile(key, tile)
        action_source = "v1_teacher" if action is not None else "least_visited_neighbor"
        if action is None:
            action = self.least_visited_neighbor_action(key, tile)
        if action is None and self.blocked_directions_at_tile(key, tile):
            # All local edges can be marked blocked after a battle/menu overlay because
            # overworld walk attempts made while input is still being consumed look
            # like wall bumps. Treat that as stale once every option is exhausted:
            # clear only this tile's blocked edges and immediately try a real move
            # instead of waiting forever.
            stale_edges = self.blocked_edges_by_map.get(key, set())
            self.blocked_edges_by_map[key] = {edge for edge in stale_edges if edge[0] != tile}
            if not self.blocked_edges_by_map[key]:
                self.blocked_edges_by_map.pop(key, None)
            action = self.least_visited_neighbor_action(key, tile)
            action_source = "clear_stale_blocked_edges"
        if action is None:
            action = "wait_300"
            action_source = "wait_fallback"
        status = {
            "path_source": "exploratory_fallback",
            "fallback_from": fallback_from,
            "route_failure": route_failure,
            "next_step": action,
            "fallback_action_source": action_source,
            "planned_path_length": 1,
            "return_policy": "v2_planner_after_verified_progress",
        } | self.blocked_edge_status()
        return [action], status

    def choose_recovery_probe_action(self, key: tuple[int, int], tile: tuple[int, int]) -> str | None:
        map_spec = GOLD_MAP_REGISTRY.get(key)
        blocked_here = self.blocked_directions_at_tile(key, tile)
        if not blocked_here:
            return None
        candidates: list[str] = []
        for failure in reversed(self.recent_failures):
            failure_map = failure.get("map") or {}
            failure_tile = failure.get("tile") or {}
            if (failure_map.get("map_group"), failure_map.get("map_number")) != key:
                continue
            if (failure_tile.get("x"), failure_tile.get("y")) != tile:
                continue
            action = failure.get("action")
            if not isinstance(action, str) or not action.startswith("walk_"):
                continue
            opposite = OPPOSITE_DIRECTIONS.get(action.removeprefix("walk_"))
            if opposite:
                candidates.append(opposite)
        if map_spec is not None:
            candidates.extend(direction for _, direction in map_spec.neighbors(tile))
        candidates.extend(("down", "right", "left", "up"))
        for direction in candidates:
            if direction in blocked_here:
                continue
            delta = WALK_DELTAS.get(direction)
            if delta is None:
                continue
            neighbor = (tile[0] + delta[0], tile[1] + delta[1])
            if map_spec is not None and not map_spec.contains(neighbor):
                continue
            return f"walk_{direction}"
        return None

    def blocked_directions_at_tile(self, key: tuple[int, int], tile: tuple[int, int]) -> set[str]:
        return {
            direction
            for blocked_tile, direction in self.blocked_edges_by_map.get(key, set())
            if blocked_tile == tile
        }

    def choose_overworld_actions(self, state: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        snapshot = snapshot_from_state(state)
        if inconsistent_state_read(state):
            metadata = state.get("metadata") or {}
            return [], {
                "path_source": "inconsistent_state_read",
                "next_step": None,
                "planned_path_length": None,
                "read_started_frame": metadata.get("read_started_frame"),
                "read_finished_frame": metadata.get("read_finished_frame"),
            } | self.blocked_edge_status()
        dialog = state.get("dialog") or {}
        if snapshot.battle.in_battle:
            battle_fallback_no_progress = self.battle_no_progress_count >= BATTLE_NO_PROGRESS_THRESHOLD
            active_battle_sequence = any((self.battle_run_sequence_index, self.battle_capture_sequence_index, self.battle_capture_throw_sequence_index, self.trainer_missing_menu_fight_index, self.trainer_missing_menu_heal_index)) or (likely_untrusted_wild_battle_main(state) and battle_fallback_no_progress)
            if self.recovery_circuit_open() and not active_battle_sequence:
                if self.battle_recovery_index >= len(BATTLE_RECOVERY_SEQUENCE):
                    self.battle_recovery_index = 0
                action = BATTLE_RECOVERY_SEQUENCE[self.battle_recovery_index]
                self.battle_run_sequence_index = 0
                self.battle_capture_sequence_index = 0
                self.battle_capture_throw_sequence_index = 0
                self.battle_capture_fallback_cycles = 0
                self.trainer_missing_menu_fight_index = 0
                self.trainer_missing_menu_heal_index = 0
                return [action], {
                    "path_source": "battle_resume_recovery",
                    "battle_policy": "resume_after_no_progress",
                    "controller": "recovery",
                    "controller_state": "fallback_battle_resume_sequence",
                    "next_step": action,
                    "planned_path_length": len(BATTLE_RECOVERY_SEQUENCE) - self.battle_recovery_index,
                    "battle_recovery_index": self.battle_recovery_index,
                    "recovery_reason": self.verification_reason,
                    "button_failures": self.button_failure_count,
                    "battle_no_progress_count": self.battle_no_progress_count,
                    "return_policy": "normal_battle_policy_after_verified_progress",
                } | self.blocked_edge_status()
            if battle_untrusted(state):
                return choose_battle_actions(state)
            if snapshot.battle.enemy_hp is not None and snapshot.battle.enemy_hp <= 0:
                return ["press_a"], {
                    "path_source": "battle_fallback",
                    "battle_policy": "advance_post_ko_text",
                    "controller": "dialogue",
                    "controller_state": "fallback_post_ko_advance",
                    "next_step": "press_a",
                    "planned_path_length": 1,
                    "reason": "enemy HP is zero; advance battle result text instead of trying to catch/fight a KO target",
                    "blocked_reason": menu_blocked_reason(state),
                } | self.blocked_edge_status()
            if likely_trainer_battle_missing_menu(state):
                # With low HP and a verified healing item, survive first; otherwise
                # hidden menu drift into PKMN/switch is worse than attacking.
                party_count = len(snapshot.party)
                use_heal_sequence = party_count > 1 and snapshot.lead is not None and snapshot.lead.hp_ratio is not None and snapshot.lead.hp_ratio < 0.35 and has_healing_item(snapshot) and self.trainer_missing_menu_heal_cycles < 1
                sequence = TRAINER_MISSING_MENU_HEAL_SEQUENCE if use_heal_sequence else missing_menu_fight_sequence_for_state(state)
                index = self.trainer_missing_menu_heal_index if use_heal_sequence else self.trainer_missing_menu_fight_index
                if index >= len(sequence):
                    index = 0
                    if use_heal_sequence:
                        self.trainer_missing_menu_heal_index = 0
                    else:
                        self.trainer_missing_menu_fight_index = 0
                action = sequence[index]
                return [action], {
                    "path_source": "battle_fallback",
                    "battle_policy": "trainer_heal_missing_menu_sequence" if use_heal_sequence else "trainer_fight_missing_menu_sequence",
                    "controller": "healing" if use_heal_sequence else "fight",
                    "controller_state": "fallback_trainer_missing_menu_heal_sequence" if use_heal_sequence else "fallback_trainer_missing_menu_sequence",
                    "next_step": action,
                    "planned_path_length": len(sequence) - index,
                    "battle_sequence_index": index,
                    "move_slot": preferred_move_slot(state) if not use_heal_sequence else None,
                    "move_pp": list(lead_move_pps(state)),
                    "reason": "trainer battle menu RAM unavailable with low HP; backing out and using Potion" if use_heal_sequence else "trainer battle menu RAM unavailable; backing out and forcing Fight/first move",
                    "blocked_reason": menu_blocked_reason(state),
                }
            if likely_untrusted_wild_battle_main(state):
                catch_decision = choose_catch_action(snapshot)
                if catch_decision.action == "throw_ball":
                    force_capture = should_force_capture_despite_missing_menu(snapshot, catch_decision)
                    forced_capture_exhausted = self.battle_capture_fallback_cycles >= min(FORCED_CAPTURE_MAX_FALLBACK_CYCLES, max(1, summarize_inventory(snapshot).balls))
                    ambiguous_capture_after_open_pack = self.battle_capture_sequence_index >= len(BATTLE_CAPTURE_OPEN_PACK_SEQUENCE) and capture_menu_ambiguous(state)
                    if ambiguous_capture_after_open_pack:
                        self.record_capture_safety_event(state, "suppress_blind_throw_after_ambiguous_pack_open")
                    if self.battle_capture_fallback_cycles >= 2 or battle_fallback_no_progress or ambiguous_capture_after_open_pack:
                        sequence = missing_menu_fight_sequence_for_state(state)
                        if self.trainer_missing_menu_fight_index >= len(sequence):
                            self.trainer_missing_menu_fight_index = 0
                        action = sequence[self.trainer_missing_menu_fight_index]
                        return [action], {
                            "path_source": "battle_fallback",
                            "battle_policy": "capture_abandoned_to_fight_missing_menu_sequence",
                            "controller": "fight",
                            "controller_state": "fallback_capture_abandoned_to_fight_sequence",
                            "next_step": action,
                            "planned_path_length": len(sequence) - self.trainer_missing_menu_fight_index,
                            "battle_sequence_index": self.trainer_missing_menu_fight_index,
                            "capture_fallback_cycles": self.battle_capture_fallback_cycles,
                            "battle_no_progress_count": self.battle_no_progress_count,
                            "forced_capture": force_capture,
                            "forced_capture_exhausted": forced_capture_exhausted,
                            "move_slot": preferred_move_slot(state),
                            "move_pp": list(lead_move_pps(state)),
                            "target_species": catch_decision.target_species,
                            "reason": "capture fallback reached ambiguous menu state after opening Pack; suppressing blind throw to avoid selecting PKMN" if ambiguous_capture_after_open_pack else ("capture fallback exhausted bounded blind ball attempts; forcing Fight to end the wild battle instead of looping" if forced_capture_exhausted else "capture fallback made no semantic progress; forcing Fight to end the wild battle instead of looping"),
                            "blocked_reason": menu_blocked_reason(state),
                            "capture_safety": list(self.capture_safety_events),
                        } | self.blocked_edge_status()
                    if force_capture and (self.battle_capture_fallback_cycles >= 2 or battle_fallback_no_progress):
                        self.trainer_missing_menu_fight_index = 0
                        self.battle_no_progress_count = 0
                        if self.battle_capture_sequence_index >= len(BATTLE_CAPTURE_OPEN_PACK_SEQUENCE) and self.battle_capture_throw_sequence_index >= len(BATTLE_CAPTURE_THROW_BALL_SEQUENCE):
                            self.battle_capture_sequence_index = 0
                            self.battle_capture_throw_sequence_index = 0
                    if self.battle_capture_sequence_index >= len(BATTLE_CAPTURE_OPEN_PACK_SEQUENCE):
                        if self.battle_capture_throw_sequence_index >= len(BATTLE_CAPTURE_THROW_BALL_SEQUENCE):
                            self.battle_capture_fallback_cycles += 1
                            self.battle_capture_sequence_index = 0
                            self.battle_capture_throw_sequence_index = 0
                        action = BATTLE_CAPTURE_THROW_BALL_SEQUENCE[self.battle_capture_throw_sequence_index]
                        return [action], {
                            "path_source": "battle_fallback",
                            "battle_policy": "capture_missing_menu_throw_ball_sequence",
                            "controller": "capture",
                            "controller_state": "fallback_capture_throw_ball_sequence",
                            "next_step": action,
                            "planned_path_length": len(BATTLE_CAPTURE_THROW_BALL_SEQUENCE) - self.battle_capture_throw_sequence_index,
                            "battle_capture_sequence_index": self.battle_capture_sequence_index,
                            "battle_capture_throw_sequence_index": self.battle_capture_throw_sequence_index,
                            "capture_intent": "throw_ball",
                            "target_species": catch_decision.target_species,
                            "reason": "Pack was opened but menu RAM is unavailable; using bounded Balls-pocket throw sequence from verified balls inventory",
                            "blocked_reason": menu_blocked_reason(state),
                            "capture_safety": list(self.capture_safety_events),
                        } | self.blocked_edge_status()
                    action = BATTLE_CAPTURE_OPEN_PACK_SEQUENCE[self.battle_capture_sequence_index]
                    return [action], {
                        "path_source": "battle_fallback",
                        "battle_policy": "capture_missing_menu_open_pack_sequence",
                        "controller": "capture",
                        "controller_state": "fallback_capture_open_pack_sequence",
                        "next_step": action,
                        "planned_path_length": len(BATTLE_CAPTURE_OPEN_PACK_SEQUENCE) - self.battle_capture_sequence_index,
                        "battle_capture_sequence_index": self.battle_capture_sequence_index,
                        "capture_intent": "throw_ball",
                        "target_species": catch_decision.target_species,
                        "reason": catch_decision.reason,
                        "blocked_reason": menu_blocked_reason(state),
                        "capture_safety": list(self.capture_safety_events),
                    } | self.blocked_edge_status()
                if catch_decision.action == "weaken":
                    sequence = missing_menu_fight_sequence_for_state(state)
                    if self.trainer_missing_menu_fight_index >= len(sequence):
                        self.trainer_missing_menu_fight_index = 0
                    action = sequence[self.trainer_missing_menu_fight_index]
                    return [action], {
                        "path_source": "battle_fallback",
                        "battle_policy": "wild_weaken_missing_menu_sequence",
                        "controller": "fight",
                        "controller_state": "fallback_wild_weaken_missing_menu_sequence",
                        "next_step": action,
                        "planned_path_length": len(sequence) - self.trainer_missing_menu_fight_index,
                        "battle_sequence_index": self.trainer_missing_menu_fight_index,
                        "move_slot": preferred_move_slot(state),
                        "move_pp": list(lead_move_pps(state)),
                        "target_species": catch_decision.target_species,
                        "reason": catch_decision.reason,
                        "blocked_reason": menu_blocked_reason(state),
                    } | self.blocked_edge_status()
            if likely_wild_grind_missing_menu(state):
                sequence = missing_menu_fight_sequence_for_state(state)
                if self.trainer_missing_menu_fight_index >= len(sequence):
                    self.trainer_missing_menu_fight_index = 0
                action = sequence[self.trainer_missing_menu_fight_index]
                return [action], {
                    "path_source": "battle_fallback",
                    "battle_policy": "wild_grind_missing_menu_sequence",
                    "controller": "fight",
                    "controller_state": "fallback_wild_grind_missing_menu_sequence",
                    "next_step": action,
                    "planned_path_length": len(sequence) - self.trainer_missing_menu_fight_index,
                    "battle_sequence_index": self.trainer_missing_menu_fight_index,
                    "move_slot": preferred_move_slot(state),
                    "move_pp": list(lead_move_pps(state)),
                    "reason": "wild battle menu RAM unavailable during pre-Falkner grind; backing out and forcing Fight/first move",
                    "blocked_reason": menu_blocked_reason(state),
                }
            if likely_untrusted_wild_battle_main(state) and self.battle_run_sequence_index >= len(BATTLE_RUN_SEQUENCE):
                self.battle_run_sequence_index = 0
            if likely_untrusted_wild_battle_main(state) and self.battle_run_sequence_index < len(BATTLE_RUN_SEQUENCE):
                action = BATTLE_RUN_SEQUENCE[self.battle_run_sequence_index]
                return [action], {
                    "path_source": "battle_fallback",
                    "battle_policy": "wild_run",
                    "controller": "run",
                    "controller_state": "fallback_battle_main_sequence",
                    "next_step": action,
                    "planned_path_length": len(BATTLE_RUN_SEQUENCE) - self.battle_run_sequence_index,
                    "battle_run_sequence_index": self.battle_run_sequence_index,
                    "reason": "battle menu RAM unavailable; using observed wild battle main-menu run sequence",
                }
            return choose_battle_actions(state)
        if all_party_fainted(state):
            return ["press_a"], {
                "path_source": "blackout_recovery",
                "next_step": "press_a",
                "planned_path_length": 1,
                "reason": "all party members fainted; advance loss/blackout text instead of navigating",
            } | self.blocked_edge_status()
        self.battle_run_sequence_index = 0
        self.battle_capture_sequence_index = 0
        self.battle_capture_throw_sequence_index = 0
        self.battle_capture_fallback_cycles = 0
        self.trainer_missing_menu_fight_index = 0
        self.trainer_missing_menu_heal_index = 0
        self.trainer_missing_menu_heal_cycles = 0
        starter_selection = self.learning.get("starter_selection")
        if (
            snapshot.has_starter
            and isinstance(starter_selection, dict)
            and (starter_selection.get("active_choice") is not None or starter_selection.get("active_nickname") is not None)
            and not likely_starter_nickname_screen(state)
        ):
            self.finalize_starter_selection()
        if likely_mart_purchase_screen(state):
            sequence = mart_purchase_sequence(state)
            policy = mart_purchase_policy(state) or "balls"
            if self.mart_buy_sequence_index >= len(sequence):
                self.mart_buy_sequence_index = 0
                return ["press_b"], {
                    "path_source": "mart_purchase_unverified_reopen",
                    "next_step": "press_b",
                    "planned_path_length": 1,
                    "mart_purchase_policy": policy,
                    "reason": "completed buy sequence without verified inventory change; back out before retrying",
                } | self.blocked_edge_status()
            if sequence == ("press_b",):
                self.mart_buy_sequence_index = 0
            action = sequence[self.mart_buy_sequence_index]
            return [action], {
                "path_source": "mart_buy_potions" if policy == "potions" else "mart_buy_balls",
                "next_step": action,
                "planned_path_length": len(sequence) - self.mart_buy_sequence_index,
                "mart_buy_sequence_index": self.mart_buy_sequence_index,
                "mart_purchase_policy": policy,
                "reason": "buying Potions before grinding/Falkner" if policy == "potions" else "buying Poke Balls before grinding/capture while reserving Potion money",
            } | self.blocked_edge_status()
        aide_stale_visual_dialogue = (
            select_route_target(state) == VIOLET_POKECENTER_AIDE_TARGET
            and visual_dialogue_active(state)
            and dialog.get("window_stack_plausible") is False
            and self.button_failure_count >= 4
        )
        if (confirmed_dialogue(dialog) or visual_dialogue_active(state)) and not aide_stale_visual_dialogue:
            same_state_press_a_count = self.repeated_press_a_same_state_count(state)
            map_info = state.get("map") or {}
            if map_info.get("map_group") == 0 and map_info.get("map_number") == 0:
                self.button_failure_count = 0
                return ["press_a"], {
                    "path_source": "new_game_bootstrap_dialogue",
                    "next_step": "press_a",
                    "planned_path_length": 1,
                    "same_state_press_a_count": same_state_press_a_count,
                    "reason": "pre-overworld intro text can leave RAM state unchanged while dialogue advances",
                } | self.blocked_edge_status()
            if not snapshot.has_starter and snapshot.position.map_key in {(24, 4), (24, 5), (24, 6), (24, 7)}:
                self.button_failure_count = 0
                return ["press_a"], {
                    "path_source": "fresh_run_intro_dialogue",
                    "next_step": "press_a",
                    "planned_path_length": 1,
                    "same_state_press_a_count": same_state_press_a_count,
                    "reason": "early setup and Elm dialogue can keep RAM unchanged while text advances",
                } | self.blocked_edge_status()
            if select_route_target(state) == VIOLET_POKECENTER_AIDE_TARGET and visual_dialogue_active(state) and dialog.get("window_stack_plausible") is False and self.button_failure_count >= 1:
                if self.stale_dialogue_recovery_index >= len(AIDE_NURSE_DIALOGUE_ESCAPE_SEQUENCE):
                    self.stale_dialogue_recovery_index = 0
                action = AIDE_NURSE_DIALOGUE_ESCAPE_SEQUENCE[self.stale_dialogue_recovery_index]
                self.stale_dialogue_recovery_index += 1
                return [action], {
                    "path_source": "pokecenter_aide_stale_visual_escape",
                    "next_step": action,
                    "planned_path_length": len(AIDE_NURSE_DIALOGUE_ESCAPE_SEQUENCE) - self.stale_dialogue_recovery_index + 1,
                    "dialogue_recovery_index": self.stale_dialogue_recovery_index - 1,
                    "same_state_press_a_count": same_state_press_a_count,
                    "reason": "clear implausible visual textbox before moving to Elm's aide",
                } | self.blocked_edge_status()
            if select_route_target(state) == VIOLET_POKECENTER_AIDE_TARGET and same_state_press_a_count < 8 and self.button_failure_count == 0:
                self.button_failure_count = 0
                return ["press_a"], {
                    "path_source": "pokecenter_aide_dialogue",
                    "next_step": "press_a",
                    "planned_path_length": 1,
                    "same_state_press_a_count": same_state_press_a_count,
                    "reason": "advance open Pokemon Center text before moving to Elm's aide",
                } | self.blocked_edge_status()
            if select_route_target(state) == VIOLET_POKECENTER_AIDE_TARGET and snapshot.position.tile in VIOLET_POKECENTER_HEAL_TARGET.tiles:
                if self.stale_dialogue_recovery_index >= len(AIDE_NURSE_DIALOGUE_ESCAPE_SEQUENCE):
                    self.stale_dialogue_recovery_index = 0
                action = AIDE_NURSE_DIALOGUE_ESCAPE_SEQUENCE[self.stale_dialogue_recovery_index]
                self.stale_dialogue_recovery_index += 1
                return [action], {
                    "path_source": "pokecenter_aide_nurse_dialogue_escape",
                    "next_step": action,
                    "planned_path_length": len(AIDE_NURSE_DIALOGUE_ESCAPE_SEQUENCE) - self.stale_dialogue_recovery_index + 1,
                    "dialogue_recovery_index": self.stale_dialogue_recovery_index - 1,
                    "same_state_press_a_count": same_state_press_a_count,
                    "reason": "decline nurse prompt before moving to Elm's aide",
                } | self.blocked_edge_status()
            if active_pokecenter_heal_dialogue(state) and same_state_press_a_count < 8:
                self.button_failure_count = 0
                return ["press_a"], {
                    "path_source": "pokecenter_heal_dialogue",
                    "next_step": "press_a",
                    "planned_path_length": 1,
                    "same_state_press_a_count": same_state_press_a_count,
                    "reason": "low-HP nurse interaction must be advanced before stale-dialogue escape",
                } | self.blocked_edge_status()
            if same_state_press_a_count >= STUCK_CIRCUIT_BREAKER_THRESHOLD:
                action, status = self.stale_dialogue_recovery_action(state)
                return [action], status | self.blocked_edge_status()
            self.button_failure_count = 0
            return ["press_a"], {"path_source": "dialogue", "next_step": "press_a", "planned_path_length": 1}
        self.reset_recovery_after_manual_progress(state)
        if ambiguous_dialogue(dialog):
            if self.ambiguous_dialogue_index >= len(AMBIGUOUS_DIALOGUE_RECOVERY_SEQUENCE):
                self.ambiguous_dialogue_index = 0
            action = AMBIGUOUS_DIALOGUE_RECOVERY_SEQUENCE[self.ambiguous_dialogue_index]
            self.ambiguous_dialogue_index += 1
            return [action], {
                "path_source": "ambiguous_dialogue_recovery",
                "next_step": action,
                "planned_path_length": len(AMBIGUOUS_DIALOGUE_RECOVERY_SEQUENCE) - self.ambiguous_dialogue_index + 1,
                "dialogue_reason": "window_stack_implausible",
                "recovery_reason": "bounded resume probe for ambiguous text/menu state",
                "ambiguous_dialogue_index": self.ambiguous_dialogue_index - 1,
            } | self.blocked_edge_status()
        if self.button_failure_count >= STUCK_CIRCUIT_BREAKER_THRESHOLD:
            if self.adaptive_mode:
                action = self.choose_adaptive_button_recovery_action()
                return [action], {
                    "path_source": "adaptive_button_recovery",
                    "next_step": action,
                    "planned_path_length": 1,
                    "recovery_reason": self.verification_reason,
                    "last_failed_action": self.last_step_action,
                    "button_failures": self.button_failure_count,
                    "fallback_from": "v2_button_safety_circuit",
                    "return_policy": "v2_planner_after_verified_progress",
                } | self.blocked_edge_status()
            return [], {
                "path_source": "safety_button_circuit_breaker",
                "next_step": None,
                "planned_path_length": None,
                "recovery_reason": self.verification_reason,
                "last_failed_action": self.last_step_action,
                "button_failures": self.button_failure_count,
            } | self.blocked_edge_status()
        key = snapshot.position.map_key
        tile = snapshot.position.tile
        if key is None or tile is None:
            map_info = state.get("map") or {}
            if map_info.get("map_group") == 0 and map_info.get("map_number") == 0:
                if confirmed_dialogue(dialog) or visual_dialogue_active(state) or pre_overworld_text_active(state):
                    return ["press_a"], {
                        "path_source": "new_game_bootstrap_dialogue",
                        "next_step": "press_a",
                        "planned_path_length": 1,
                    } | self.blocked_edge_status()
                if self.new_game_bootstrap_index >= len(NEW_GAME_BOOTSTRAP_SEQUENCE):
                    self.new_game_bootstrap_index = 0
                action = NEW_GAME_BOOTSTRAP_SEQUENCE[self.new_game_bootstrap_index]
                return [action], {
                    "path_source": "new_game_bootstrap",
                    "next_step": action,
                    "planned_path_length": len(NEW_GAME_BOOTSTRAP_SEQUENCE) - self.new_game_bootstrap_index,
                    "bootstrap_sequence_index": self.new_game_bootstrap_index,
                } | self.blocked_edge_status()
            source = "missing_position"
            return [], {"path_source": source, "next_step": None, "planned_path_length": None} | self.blocked_edge_status()
        aliased_state = union_cave_live_alias_state(state, key)
        if aliased_state is not None:
            state = aliased_state
            snapshot = snapshot_from_state(state)
            key = snapshot.position.map_key
            tile = snapshot.position.tile
        ruins_escape = ruins_of_alph_escape_action(state, key)
        if ruins_escape is not None:
            action, status = ruins_escape
            return [action], status | self.blocked_edge_status()
        tile, coordinate_status = effective_navigation_tile(state, key, tile)
        if tile is None:
            return [], {"path_source": "missing_position", "next_step": None, "planned_path_length": None} | self.blocked_edge_status()
        if likely_elm_phone_call(state):
            return ["press_a"], {
                "path_source": "elm_phone_call",
                "next_step": "press_a",
                "planned_path_length": 1,
            } | self.blocked_edge_status()
        if likely_text_input_keyboard(state):
            if likely_starter_nickname_screen(state):
                sequence = self.starter_nickname_sequence()
                if self.nickname_sequence_index >= len(sequence):
                    return [], {
                        "path_source": "text_input_keyboard_blocked_after_nickname_sequence",
                        "next_step": None,
                        "planned_path_length": None,
                        "nickname": self.active_starter_nickname,
                        "reason": "nickname sequence completed but keyboard still appears active; refusing to type more",
                    } | self.blocked_edge_status()
                action = sequence[self.nickname_sequence_index]
                return [action], {
                    "path_source": "starter_nickname_entry",
                    "next_step": action,
                    "planned_path_length": len(sequence) - self.nickname_sequence_index,
                    "text_input_sequence_index": self.nickname_sequence_index,
                    "nickname": self.active_starter_nickname,
                    "reason": "entering rotating silly starter nickname without keyboard OCR",
                } | self.blocked_edge_status()
            if self.nickname_sequence_index >= len(TEXT_INPUT_END_SEQUENCE):
                self.nickname_sequence_index = 0
            action = TEXT_INPUT_END_SEQUENCE[self.nickname_sequence_index]
            return [action], {
                "path_source": "text_input_keyboard_end",
                "next_step": action,
                "planned_path_length": len(TEXT_INPUT_END_SEQUENCE) - self.nickname_sequence_index,
                "text_input_sequence_index": self.nickname_sequence_index,
            } | self.blocked_edge_status()
        self.reset_recovery_after_manual_progress(state)
        if self.recovery_circuit_open():
            recovery_action = self.choose_recovery_probe_action(key, tile)
            if recovery_action is not None:
                return [recovery_action], {
                    "path_source": "recovery_probe",
                    "next_step": recovery_action,
                    "planned_path_length": 1,
                    "recovery_reason": self.verification_reason,
                    "last_failed_action": self.last_step_action,
                    "recovery_tile": {"x": tile[0], "y": tile[1]},
                } | self.blocked_edge_status()
            if self.blocked_edges_by_map.get(key) and not self.blocked_directions_at_tile(key, tile):
                self.blocked_edges_by_map.pop(key, None)
                self.recovery_level = 0
                self.stuck_counter = 0
            else:
                return [], {
                    "path_source": "safety_circuit_breaker",
                    "next_step": None,
                    "planned_path_length": None,
                    "recovery_reason": self.verification_reason,
                    "last_failed_action": self.last_step_action,
                } | self.blocked_edge_status()
        if position_untrusted(state):
            position = ((state.get("player") or {}).get("position") or {})
            return [], {
                "path_source": "untrusted_position",
                "next_step": None,
                "planned_path_length": None,
                "position_confidence": position.get("confidence"),
                "position_in_bounds": position.get("in_bounds"),
            } | self.blocked_edge_status()
        target = select_route_target(state)
        if target is None:
            actions, status = self.choose_safe_exploratory_action(key, tile, fallback_from="no_goal")
            status.update(coordinate_status)
            return actions, status
        if target == VIOLET_POKECENTER_HEAL_TARGET and self.suppress_repeated_pokecenter_heal(snapshot):
            target = self.target_after_pokecenter_heal_attempt(snapshot)
            coordinate_status.update({
                "pokecenter_heal_suppressed": True,
                "suppressed_target": "Violet Pokemon Center nurse counter",
                "suppression_reason": "already completed one nurse interaction for the current lead HP snapshot",
            })
        if target == VIOLET_MART_BUY_TARGET and key == VIOLET_MART_BUY_TARGET.map_key and tile in VIOLET_MART_BUY_TARGET.tiles:
            action = "press_a" if self.mart_face_left_attempted_at == tile else "walk_left"
            return [action], {
                "path_source": "mart_clerk_interact",
                "next_step": action,
                "planned_path_length": 1,
                "reason": "open mart buy menu before grinding/capture",
            } | self.blocked_edge_status()
        if target == ROUTE31_GRIND_TARGET and key == ROUTE31_GRIND_TARGET.map_key and tile in ROUTE31_GRIND_TARGET.tiles:
            action = ROUTE31_GRIND_SEQUENCE[self.grind_sequence_index % len(ROUTE31_GRIND_SEQUENCE)]
            self.grind_sequence_index += 1
            return [action], {
                "path_source": "route31_grind_loop",
                "next_step": action,
                "planned_path_length": 1,
                "grind_sequence_index": self.grind_sequence_index,
                "reason": "seek wild encounters for training/capture before Falkner",
            } | self.blocked_edge_status()
        if target == STARTER_TARGET and key == STARTER_TARGET.map_key and not snapshot.has_starter:
            live_tile = snapshot.position.tile
            if live_tile is not None:
                x, y = live_tile
                selected_choice = self.active_starter_choice or self.resolve_starter_choice()
                selected_target = STARTER_CHOICES.get(selected_choice, STARTER_TARGET)
                target_x, target_y = next(iter(selected_target.tiles))
                if y > target_y:
                    action = "walk_up"
                elif y < target_y:
                    action = "walk_down"
                elif x < target_x:
                    action = "walk_right"
                elif x > target_x:
                    action = "walk_left"
                else:
                    action = "press_a" if self.starter_face_up_attempted_at == live_tile else "walk_up"
                return [action], {
                    "path_source": "starter_live_macro",
                    "next_step": action,
                    "planned_path_length": abs(x - target_x) + abs(y - target_y) + (0 if live_tile == (target_x, target_y) else 1),
                    "reason": "starter selection uses live normalized coordinates; static collision/raw fallback is unreliable in Elm's Lab",
                    "starter_choice": selected_choice,
                    "starter_nickname": self.active_starter_nickname,
                    "target": {
                        "map_group": selected_target.map_key[0],
                        "map_number": selected_target.map_key[1],
                        "name": selected_target.name,
                        "tiles": [{"x": tx, "y": ty} for tx, ty in sorted(selected_target.tiles)],
                    },
                } | self.blocked_edge_status()
        if target == VIOLET_GATE_TARGET and key == (26, 2):
            if tile == (4, 6):
                return ["walk_down"], {
                    "path_source": "route31_gate_live_entry",
                    "next_step": "walk_down",
                    "planned_path_length": 2,
                    "reason": "live gate entry requires the lower doorway tile",
                } | self.blocked_edge_status()
            if tile == (4, 7):
                return ["walk_left"], {
                    "path_source": "route31_gate_live_entry",
                    "next_step": "walk_left",
                    "planned_path_length": 1,
                    "reason": "live gate warp triggers by stepping left from the lower doorway tile",
                } | self.blocked_edge_status()
        if target == VIOLET_POKECENTER_HEAL_TARGET and key == VIOLET_POKECENTER_HEAL_TARGET.map_key and tile in VIOLET_POKECENTER_HEAL_TARGET.tiles:
            action = "press_a" if self.pokecenter_face_up_attempted_at == tile else "walk_up"
            return [action], {
                "path_source": "pokecenter_heal_interact",
                "next_step": action,
                "planned_path_length": 1,
                "reason": "lead_hp_low_before_zephyr",
            } | self.blocked_edge_status()
        if target == VIOLET_POKECENTER_AIDE_TARGET and key == VIOLET_POKECENTER_AIDE_TARGET.map_key:
            if tile == (3, 3):
                action = "walk_down"
            elif tile == (3, 4):
                action = "walk_right"
            elif tile in {(4, 5), (4, 6)}:
                action = "walk_up"
            elif tile == (4, 4):
                action = "press_a" if self.pokecenter_aide_face_up_attempted_at == tile else "walk_up"
            elif tile[0] > 4:
                action = "walk_left"
            elif tile[0] < 4:
                action = "walk_right"
            elif tile[1] > 4:
                action = "walk_up"
            elif tile[1] < 4:
                action = "walk_down"
            else:
                action = None
            if action is None:
                pass
            else:
                return [action], {
                    "path_source": "pokecenter_aide_interact",
                    "next_step": action,
                    "planned_path_length": 1,
                    "reason": "collect Egg from Elm's aide before Route 32 guard",
                } | self.blocked_edge_status()
        if target == VIOLET_POKECENTER_AIDE_TARGET and key == VIOLET_POKECENTER_AIDE_TARGET.map_key and tile in VIOLET_POKECENTER_AIDE_TARGET.tiles:
            action = "press_a" if self.pokecenter_aide_face_up_attempted_at == tile else "walk_up"
            return [action], {
                "path_source": "pokecenter_aide_interact",
                "next_step": action,
                "planned_path_length": 1,
                "reason": "collect Egg from Elm's aide before Route 32 guard",
            } | self.blocked_edge_status()
        if GOLD_MAP_REGISTRY.get(key) is None or GOLD_MAP_REGISTRY.get(target.map_key) is None:
            return [], diagnose_route_failure(key, tile, target)
        if target == STARTER_TARGET and key == STARTER_TARGET.map_key and tile in STARTER_TARGET.tiles:
            action = "press_a" if self.starter_face_up_attempted_at == tile else "walk_up"
            return [action], {
                "path_source": "starter_interact",
                "next_step": action,
                "planned_path_length": 1,
                "target": {
                    "map_group": STARTER_TARGET.map_key[0],
                    "map_number": STARTER_TARGET.map_key[1],
                    "tiles": [{"x": x, "y": y} for x, y in sorted(STARTER_TARGET.tiles)],
                },
            }
        if target in {MR_POKEMON_HOUSE_TARGET, ELMS_LAB_RETURN_TARGET, FALKNER_TARGET} and key == target.map_key and tile in target.tiles:
            action = "press_a"
            if target == ELMS_LAB_RETURN_TARGET:
                action = "press_a" if self.elm_return_face_up_attempted_at == tile else "walk_up"
            return [action], {
                "path_source": "story_interact",
                "next_step": action,
                "planned_path_length": 1,
                "target": {
                    "name": target.name,
                    "map_group": target.map_key[0],
                    "map_number": target.map_key[1],
                    "tiles": [{"x": x, "y": y} for x, y in sorted(target.tiles)],
                },
            }
        plan = plan_route_to_target(GOLD_MAP_REGISTRY, key, tile, target, self.blocked_edges_for_planner())
        if plan is not None and plan.next_action is not None:
            status = navigation_status_from_route_plan(plan)
            status.update(self.blocked_edge_status())
            status.update(coordinate_status)
            if self.adaptive_mode and self.adaptive_oscillation_detected():
                recovery_action = self.least_visited_neighbor_action(key, tile, avoid_action=plan.next_action) or self.choose_adaptive_loop_recovery_action(plan.next_action)
                status["path_source"] = "adaptive_loop_recovery"
                status["fallback_from"] = "v2_planner_oscillation"
                status["next_step"] = recovery_action
                status["planned_path_length"] = 1
                status["return_policy"] = "v2_planner_after_verified_progress"
                status["loop_recovery_reason"] = "recent verified transitions oscillated between the same two states"
                self.record_shared_fact(
                    "PKM:STUCK",
                    f"Adaptive mode detected planner oscillation near {self.state_key_for_learning(state)}",
                    confidence="observed",
                    data={"planned_action": plan.next_action, "recovery_action": recovery_action, "evidence_count": 1},
                )
                return [recovery_action], status
            return [plan.next_action], status
        if plan is not None and plan.next_action is None and key == target.map_key and tile in target.tiles:
            status = navigation_status_from_route_plan(plan)
            status["path_source"] = "arrived_at_story_target"
            status.update(self.blocked_edge_status())
            status.update(coordinate_status)
            return [], status
        if self.blocked_edges_by_map.get(key):
            if not self.blocked_directions_at_tile(key, tile):
                unblocked_plan = plan_route_to_target(GOLD_MAP_REGISTRY, key, tile, target, {})
                if unblocked_plan is not None and unblocked_plan.next_action is not None:
                    self.blocked_edges_by_map.pop(key, None)
                    self.recovery_level = 0
                    self.stuck_counter = 0
                    status = navigation_status_from_route_plan(unblocked_plan)
                    status["path_source"] = "stale_blocked_edges_cleared"
                    status.update(self.blocked_edge_status())
                    status.update(coordinate_status)
                    return [unblocked_plan.next_action], status
            self.recovery_level = max(self.recovery_level, 2)
            if self.adaptive_mode:
                self.blocked_edges_by_map.pop(key, None)
                self.recovery_level = 1
                self.stuck_counter = 0
                unblocked_plan = plan_route_to_target(GOLD_MAP_REGISTRY, key, tile, target, {})
                if unblocked_plan is not None and unblocked_plan.next_action is not None:
                    status = navigation_status_from_route_plan(unblocked_plan)
                    status["path_source"] = "adaptive_clear_stale_blocked_edges"
                    status["fallback_from"] = "v2_blocked_edges_exhausted"
                    status["return_policy"] = "v2_planner_after_verified_progress"
                    status.update(self.blocked_edge_status())
                    status.update(coordinate_status)
                    return [unblocked_plan.next_action], status
            actions, status = self.choose_safe_exploratory_action(key, tile, fallback_from="blocked_edges_exhausted", route_failure="blocked_edges_exhausted")
            status["recovery_level"] = self.recovery_level
            status.update(coordinate_status)
            return actions, status
        failure = diagnose_route_failure(key, tile, target)
        if GOLD_MAP_REGISTRY.get(key) is not None:
            actions, status = self.choose_safe_exploratory_action(key, tile, fallback_from="no_route", route_failure=str(failure.get("route_failure") or "no_route"))
            status.update(coordinate_status)
            return actions, status
        return [], failure | coordinate_status | self.blocked_edge_status()

    def build_status(self, control: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
        enabled = control.get("enabled") is not False
        dry_run = control.get("dry_run") is not False
        allow_overworld = control.get("allow_overworld_movement") is True
        allow_battle = control.get("allow_battle_actions") is True
        state_error = None
        current_goal: dict[str, Any] = {"type": "bootstrap", "name": "Build navigation V2 modules"}
        gameplay: dict[str, Any] = {}
        policy_candidates: list[dict[str, Any]] = []
        actions: list[str] = []
        navigation: dict[str, Any] = {
            "path_source": "not_planned",
            "planned_path_length": None,
            "next_step": None,
            "last_step_result": self.last_step_result,
            "last_step_action": self.last_step_action,
            "last_step_verified": self.last_step_verified,
            "verification_reason": self.verification_reason,
            "replan_count": self.replan_count,
            "stuck_counter": self.stuck_counter,
            "recovery_level": self.recovery_level,
        }
        navigation.update(self.blocked_edge_status())
        phase = "BOOT_SYNC" if enabled else "PAUSED"
        if state:
            snapshot = snapshot_from_state(state)
            story_decision = explain_story_objective(state)
            self.adaptive_mode = control.get("engine") == "adaptive"
            actions, navigation_update = self.choose_overworld_actions(state)
            if not enabled:
                actions = []
            navigation.update(navigation_update)
            inventory = summarize_inventory(snapshot)
            catch_decision = choose_catch_action(snapshot)
            resources = resource_accounting(snapshot)
            policy_candidates = [
                {
                    "name": story_decision.objective_key,
                    "kind": "objective",
                    "selected": not snapshot.battle.in_battle,
                    "score": 100,
                    "expected_outcome": story_decision.objective_title,
                    "blockers": resources.get("readiness_blockers", []) if story_decision.objective_key == "falkner" else [],
                    "evidence": [story_decision.reason],
                }
            ]
            if snapshot.battle.in_battle:
                policy_candidates.append({
                    "name": navigation_update.get("battle_policy") or "battle_policy",
                    "kind": "battle",
                    "selected": True,
                    "score": 100,
                    "expected_outcome": "survive battle and improve resources/progress",
                    "blockers": [item for item in [navigation_update.get("blocked_reason"), "capture_safety_active" if navigation_update.get("capture_safety") else None] if item],
                    "evidence": [navigation_update.get("reason") or catch_decision.reason, navigation_update.get("controller_state"), navigation_update.get("next_step")],
                })
            elif resources.get("broke_no_balls") and story_decision.objective_key == "route31_grind":
                policy_candidates.append({
                    "name": "wild_exp_economy_recovery",
                    "kind": "resource_policy",
                    "selected": True,
                    "score": 90,
                    "expected_outcome": "gain levels without spending money until trainer/gym income is safer",
                    "blockers": ["no_balls", "money_below_pokeball_cost"],
                    "evidence": [story_decision.reason],
                })
            current_goal = {
                "type": story_decision.objective_key,
                "name": story_decision.objective_title,
                "reason": story_decision.reason,
                "map_name": snapshot.position.map_name,
                "map_group": snapshot.position.map_group,
                "map_number": snapshot.position.map_number,
                "x": snapshot.position.x,
                "y": snapshot.position.y,
            }
            if snapshot.battle.in_battle:
                phase = "BATTLE"
            elif enabled:
                phase = "OVERWORLD"
            gameplay = {
                "position": {
                    "map_name": snapshot.position.map_name,
                    "map_group": snapshot.position.map_group,
                    "map_number": snapshot.position.map_number,
                    "x": snapshot.position.x,
                    "y": snapshot.position.y,
                    "raw_x": snapshot.position.raw_x,
                    "raw_y": snapshot.position.raw_y,
                    "facing": snapshot.facing,
                    "trusted": ((state.get("player") or {}).get("position") or {}).get("trusted"),
                    "confidence": ((state.get("player") or {}).get("position") or {}).get("confidence"),
                    "in_bounds": ((state.get("player") or {}).get("position") or {}).get("in_bounds"),
                },
                "party_count": len(snapshot.party),
                "party_full": snapshot.party_full,
                "lead": snapshot.lead.species if snapshot.lead else None,
                "balls": inventory.balls,
                "healing_items": inventory.healing_items,
                "resources": resources,
                "roster_roles": roster_roles(snapshot),
                "catch_decision": {
                    "action": catch_decision.action,
                    "reason": catch_decision.reason,
                    "target_species": catch_decision.target_species,
                },
                "decision_context": {
                    "blocked_edges": self.blocked_edge_status(),
                    "battle_identity": self.last_battle_identity,
                    "battle_identity_change": self.last_battle_identity_change,
                    "capture_safety": list(self.capture_safety_events),
                    "menu_blocked_reason": menu_blocked_reason(state),
                },
                "ram_health": ram_health_from_state(state),
                "story": {
                    "objective_key": story_decision.objective_key,
                    "objective_title": story_decision.objective_title,
                    "reason": story_decision.reason,
                    "confidence": story_decision.confidence,
                    "target": {
                        "map_group": story_decision.target.map_key[0],
                        "map_number": story_decision.target.map_key[1],
                        "name": story_decision.target.name,
                    } if story_decision.target else None,
                },
            }
        else:
            state_error = "state unavailable"
        ram_health = (gameplay.get("ram_health") if isinstance(gameplay, dict) else {}) or {}
        readiness_blockers: list[str] = []
        engine_selected = control.get("engine") in {"v2", "unified", "adaptive"}
        if not engine_selected:
            readiness_blockers.append("engine_not_selected")
        if not enabled:
            readiness_blockers.append("disabled")
        if state_error:
            readiness_blockers.append("state_unavailable")
        if ram_health.get("position_trusted") is False:
            readiness_blockers.append("position_untrusted")
        if ram_health.get("battle_trusted") is False:
            readiness_blockers.append("battle_ram_untrusted")
        if dry_run:
            readiness_blockers.append("dry_run_enabled")
        if phase != "BATTLE" and actions and any(str(action).startswith("walk_") for action in actions) and not allow_overworld:
            readiness_blockers.append("overworld_movement_disabled")
        if actions and phase == "BATTLE" and not allow_battle:
            readiness_blockers.append("battle_actions_disabled")
        safe_to_post_actions = enabled and engine_selected and state_error is None and not dry_run and not readiness_blockers
        if state:
            resources_for_facts = gameplay.get("resources") if isinstance(gameplay, dict) else {}
            story_for_facts = gameplay.get("story") if isinstance(gameplay, dict) else {}
            if isinstance(resources_for_facts, dict):
                self.record_resource_facts(resources_for_facts, type("StoryFact", (), {"objective_key": story_for_facts.get("objective_key", "unknown")})())
            self.record_readiness_facts(readiness_blockers, phase, navigation)
        return {
            "schema_version": 2,
            "engine": "v2",
            "enabled": enabled,
            "turn": self.turn,
            "phase": phase,
            "objective": control.get("objective") or DEFAULT_OBJECTIVE,
            "current_task": current_goal.get("type", "navigation_v2_bootstrap"),
            "current_goal": current_goal,
            "message": "Navigation V2 selected; imported map/collision planning is active for early Johto routing.",
            "gameplay": gameplay,
            "state_error": state_error,
            "navigation": navigation,
            "actions": actions,
            "runner": {
                "engine": "v2",
                "mode": "active" if engine_selected and enabled else "paused",
                "selected_engine": control.get("engine"),
                "adaptive_mode": self.adaptive_mode,
                "event_log": str(self.event_log_path),
                "learning_file": str(self.learning_path),
                "last_error": self.last_api_error or state_error,
                "dry_run": dry_run,
                "allow_overworld_movement": allow_overworld,
                "allow_battle_actions": allow_battle,
                "api_failure_count": self.api_failure_count,
                "api_backoff_seconds": self.api_backoff_seconds,
                "next_api_retry_at": self.next_api_retry_at,
            },
            "readiness": {
                "engine_selected": engine_selected,
                "enabled": enabled,
                "state_available": state_error is None,
                "dry_run": dry_run,
                "allow_overworld_movement": allow_overworld,
                "allow_battle_actions": allow_battle,
                "position_trusted": ram_health.get("position_trusted"),
                "battle_trusted": ram_health.get("battle_trusted"),
                "safe_to_post_actions": safe_to_post_actions,
                "blockers": readiness_blockers,
            },
            "mode_policy": {
                "selected": control.get("engine"),
                "optimal": "v2_planner",
                "fallback_active": isinstance(navigation.get("path_source"), str) and navigation.get("path_source", "").startswith("adaptive_"),
                "return_policy": navigation.get("return_policy") or "v2_planner_after_verified_progress",
                "switch_reason": navigation.get("fallback_reason") or navigation.get("route_failure") or navigation.get("verification_reason") or navigation.get("reason"),
            },
            "intent": {
                "phase": phase,
                "goal": current_goal,
                "selected_policy": navigation.get("battle_policy") or navigation.get("path_source") or current_goal.get("type"),
                "expected_outcome": policy_candidates[-1].get("expected_outcome") if policy_candidates else current_goal.get("name"),
            },
            "decision_trace": [
                {"step": "observe", "evidence": {"phase": phase, "map": current_goal.get("map_name")}},
                {"step": "assess_resources", "evidence": gameplay.get("resources", {})},
                {"step": "select_goal", "evidence": gameplay.get("story", {})},
                {"step": "plan_action", "evidence": navigation},
                {"step": "learn", "evidence": {"shared_counts": self.shared_learning_counts(), "last_transition": self.learning.get("last_transition")}},
            ],
            "policy_candidates": policy_candidates if state else [],
            "resource_accounting": gameplay.get("resources", {}) if isinstance(gameplay, dict) else {},
            "failure_memory": {
                "recent": list(self.recent_failures),
                "shared_counts": self.shared_learning_counts(),
                "recent_facts": self.recent_shared_facts(),
            },
            "learning": self.learning_summary(),
            "updated_at": time.time(),
        }

    def run_once(self) -> dict[str, Any]:
        control = self.read_control()
        if control.get("engine") not in {"v2", "unified", "adaptive"}:
            status = self.build_status({**control, "enabled": False}, None)
            status["selected_engine"] = control.get("engine")
            status["message"] = "V2 idle while another bot engine is selected."
            self.write_status(status)
            self.log_event({"event": "idle", "selected_engine": control.get("engine"), "status": status})
            self.turn += 1
            return status

        if self.next_api_retry_at and time.time() < self.next_api_retry_at:
            status = self.build_status(control, None)
            status["phase"] = "API_BACKOFF"
            status["actions"] = []
            status["navigation"]["path_source"] = "api_backoff"
            self.write_status(status)
            self.log_event({"event": "api_backoff", "status": status})
            self.turn += 1
            return status

        state = None
        try:
            state = self.request_json("/state")
            self.record_api_success()
            self.sync_battle_identity(state)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            state = None
            self.record_api_failure(sys.exc_info()[1])

        status = self.build_status(control, state)
        if state is not None:
            try:
                self.save_state_manager.dry_run = self.milestone_save_state_dry_run_default or control.get("dry_run") is not False
                status["save_states"] = self.save_state_manager.maybe_save(state, status, max_saves=1)
            except Exception as exc:  # noqa: BLE001 - save snapshots must never block gameplay
                status["save_states"] = {
                    "enabled": True,
                    "dry_run": self.save_state_manager.dry_run,
                    "safety": "save_only",
                    "load_policy": "manual_only",
                    "error": f"{type(exc).__name__}: {exc}",
                }
                self.log_event({"event": "save_state_milestone_error", "error": status["save_states"]["error"]})
        if state is None:
            status["phase"] = "API_UNAVAILABLE"
            status["navigation"]["path_source"] = "api_unavailable"
        enabled = control.get("enabled") is not False
        actions = status.get("actions") if enabled else []
        if isinstance(actions, list) and actions and state is not None:
            action = str(actions[0])
            try:
                if action not in ALLOWED_ACTIONS:
                    self.record_action_outcome(action, False, "invalid_action", "blocked_invalid_action", state)
                elif control.get("dry_run") is not False:
                    self.record_action_outcome(action, False, "dry_run_no_action_posted", "dry_run", state)
                elif action.startswith("walk_") and status.get("phase") != "BATTLE" and control.get("allow_overworld_movement") is not True:
                    self.record_action_outcome(action, False, "overworld_movement_disabled", "blocked_by_control", state)
                elif control.get("allow_battle_actions") is not True and (state.get("battle") or {}).get("in_battle"):
                    self.record_action_outcome(action, False, "battle_actions_disabled", "blocked_by_control", state)
                else:
                    readiness_ok, readiness_reason = readiness_allows_action(status, action)
                    if not readiness_ok:
                        self.record_action_outcome(action, False, f"readiness_blocked:{readiness_reason}", "blocked_by_readiness", state)
                        status["actions"] = []
                        status["navigation"]["blocked_reason"] = "readiness_blocked"
                        status["navigation"]["readiness_blockers"] = (status.get("readiness") or {}).get("blockers")
                    else:
                        posted_actions = posted_actions_for_context(action, status.get("navigation"), status.get("phase"))
                        status["navigation"]["posted_actions"] = posted_actions
                        result = self.post_actions(posted_actions)
                        if result.get("success") is False:
                            self.record_action_outcome(action, False, "post_actions_unsuccessful", "post_failed", state)
                        else:
                            after_state = self.request_json("/state")
                            verified, reason = verify_single_action(state, after_state, action)
                            self.record_action_outcome(action, verified, reason, "ok", state)
                            self.record_battle_semantic_progress(state, after_state, action, status.get("navigation"))
                            reward = self.record_learning_transition(action, verified, reason, "ok", state, after_state)
                            status["learning"] = self.learning_summary() | {"last_reward": reward}
                if self.last_step_result in {"dry_run", "blocked_by_control", "blocked_by_readiness", "blocked_invalid_action", "post_failed"}:
                    reward = self.record_learning_transition(action, False, self.verification_reason or "not_posted", self.last_step_result or "blocked", state, None)
                    status["learning"] = self.learning_summary() | {"last_reward": reward}
                status["navigation"]["last_step_result"] = self.last_step_result
                status["navigation"]["last_step_action"] = self.last_step_action
                status["navigation"]["last_step_verified"] = self.last_step_verified
                status["navigation"]["verification_reason"] = self.verification_reason
                status["navigation"]["stuck_counter"] = self.stuck_counter
                status["navigation"]["recovery_level"] = self.recovery_level
                status["navigation"]["replan_count"] = self.replan_count
                status["navigation"].update(self.blocked_edge_status())
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                self.record_api_failure(exc)
                status["navigation"]["last_step_result"] = f"error:{type(exc).__name__}"
                self.last_step_result = f"error:{type(exc).__name__}"
                self.last_step_action = action
                self.last_step_verified = False
                self.verification_reason = "verification_state_unavailable"
                status["navigation"]["last_step_action"] = self.last_step_action
                status["navigation"]["last_step_verified"] = self.last_step_verified
                status["navigation"]["verification_reason"] = self.verification_reason
                status["navigation"].update(self.blocked_edge_status())
        self.write_status(status)
        if self.turn % 5 == 0:
            self.persist_learning()
        self.log_event({
            "event": "turn",
            "phase": status.get("phase"),
            "actions": status.get("actions"),
            "navigation": status.get("navigation"),
            "ram_health": (status.get("gameplay") or {}).get("ram_health"),
        })
        self.turn += 1
        return status

    def run_forever(self) -> None:
        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                break
            except Exception as exc:  # noqa: BLE001
                self.last_api_error = f"{type(exc).__name__}: {exc}"
                try:
                    self.log_event({"event": "error", "error": self.last_api_error})
                except Exception:
                    pass
            time.sleep(self.api_backoff_seconds or self.loop_sleep_seconds)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:9879")
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args()
    data_dir = Path(args.data_dir).expanduser() if args.data_dir else None
    GoldAutoplayerV2(data_dir=data_dir, base_url=args.base_url).run_forever()


if __name__ == "__main__":
    main()
