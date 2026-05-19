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
    UNION_CAVE_TARGET,
    STARTER_TARGET,
    VIOLET_CITY_TARGET,
    VIOLET_GATE_TARGET,
    VIOLET_GYM_LOBBY_TARGET,
    VIOLET_MART_BUY_TARGET,
    VIOLET_POKECENTER_HEAL_TARGET,
    explain_story_objective,
    select_route_target,
)
from pokemon_agent.navigation import GOLD_MAP_REGISTRY, RoutePlan, RouteTarget, plan_route_to_target
from pokemon_agent.navigation.route_planner import find_map_path, transition_between
from pokemon_agent.autoplayer.learning import LearningFact, LearningMemory, import_gold_v1_teacher_snapshot, read_optional_json


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
NEW_GAME_BOOTSTRAP_SEQUENCE: tuple[str, ...] = ("wait_300", "wait_600", "press_start", "press_a")
TEXT_INPUT_END_SEQUENCE: tuple[str, ...] = ("press_a",)
NICKNAME_END_SEQUENCE = TEXT_INPUT_END_SEQUENCE
BATTLE_RUN_SEQUENCE: tuple[str, ...] = ("press_b", "press_b", "press_down", "press_right", "press_a")
MART_BUY_ONE_SEQUENCE: tuple[str, ...] = ("press_a", "press_a", "press_a", "press_a")
ROUTE31_GRIND_SEQUENCE: tuple[str, ...] = ("walk_left", "walk_right")
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


def walk_hold_frames_for_navigation(action: str, navigation: dict[str, Any] | None) -> int | None:
    """Use shorter holds near doors/warps so one logical step stays one tile."""
    if not action.startswith("walk_") or not isinstance(navigation, dict):
        return None
    path_source = str(navigation.get("path_source") or "")
    if navigation.get("transition") is not None:
        return SHORT_WALK_HOLD_FRAMES
    if "gate" in path_source or "warp" in path_source or "door" in path_source:
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
        if (before_state.get("visual") or {}) != (after_state.get("visual") or {}):
            return True, "visual_state_changed_after_press_a"
        if before.position != after.position:
            return True, "state_changed_after_press_a"
        if before.battle != after.battle:
            return True, "battle_state_changed_after_press_a"
        if before_dialog != after_dialog:
            return True, "dialogue_state_changed_after_press_a"
        before_menu = observed_menu(before_state)
        after_menu = observed_menu(after_state)
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
    snapshot = snapshot_from_state(state)
    if snapshot.position.map_key != VIOLET_MART_BUY_TARGET.map_key:
        return False
    balls = sum(item.quantity for item in snapshot.bag if item.item_id in BALL_ITEM_IDS)
    if balls >= 3 or (snapshot.money or 0) < 200:
        return False
    dialog = state.get("dialog") or {}
    if confirmed_dialogue(dialog) or visual_dialogue_active(state) or ambiguous_dialogue(dialog):
        return True
    visual = state.get("visual") or {}
    return visual.get("screen_class") == "menu_or_text" and visual.get("visual_textbox_active") is not False


def likely_untrusted_wild_battle_main(state: dict[str, Any]) -> bool:
    snapshot = snapshot_from_state(state)
    if not snapshot.battle.wild:
        return False
    if observed_menu(state):
        return False
    if confirmed_dialogue(state.get("dialog") or {}):
        return False
    visual = state.get("visual") or {}
    return visual.get("screen_class") == "menu_or_text" and visual.get("visual_textbox_active") is not True


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
        self.mart_face_left_attempted_at: tuple[int, int] | None = None
        self.nickname_sequence_index = 0
        self.battle_run_sequence_index = 0
        self.new_game_bootstrap_index = 0
        self.mart_buy_sequence_index = 0
        self.grind_sequence_index = 0
        self.adaptive_recovery_index = 0
        self.adaptive_loop_recovery_index = 0
        self.adaptive_mode = False
        self.loop_sleep_seconds = DEFAULT_LOOP_SLEEP_SECONDS
        self.learning = self.load_learning()
        self.shared_memory = LearningMemory(self.data_dir / "pokemon_learning_memory.json")
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

    def learning_summary(self) -> dict[str, Any]:
        action_values = self.learning.get("action_values") if isinstance(self.learning, dict) else {}
        blocked_edges = self.learning.get("blocked_edges") if isinstance(self.learning, dict) else {}
        tile_visits = self.learning.get("tile_visits") if isinstance(self.learning, dict) else {}
        return {
            "path": str(self.learning_path),
            "shared": self.shared_learning_summary(),
            "state_action_keys": len(action_values) if isinstance(action_values, dict) else 0,
            "blocked_edges_learned": len(blocked_edges) if isinstance(blocked_edges, dict) else 0,
            "tiles_visited": len(tile_visits) if isinstance(tile_visits, dict) else 0,
            "turns": self.learning.get("turns", 0) if isinstance(self.learning, dict) else 0,
            "v1_teacher_import": self.learning.get("v1_teacher_import", {}) if isinstance(self.learning, dict) else {},
        }

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
            reward += 1.5
        elif before.position.tile != after.position.tile:
            reward += 0.6
        tile_key = f"{after.position.map_group}:{after.position.map_number}:{after.position.x}:{after.position.y}"
        visits = int((self.learning.get("tile_visits") or {}).get(tile_key, 0))
        reward += 0.2 / ((visits + 1) ** 0.5)
        if len(after.party) > len(before.party):
            reward += 5.0
        before_badges = int((before_state.get("player") or {}).get("badges", 0) or 0)
        after_badges = int((after_state.get("player") or {}).get("badges", 0) or 0)
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
        if verified and reward > 0:
            self.record_shared_fact(
                "PKM:PROGRESS",
                f"{action} made verified progress from {state_key} to {after_key}",
                confidence="verified",
                data={"action": action, "before": state_key, "after": after_key, "reward": reward, "evidence_count": 1},
            )
        elif not verified:
            self.record_shared_fact(
                "PKM:STUCK",
                f"{action} failed at {state_key}: {reason}",
                confidence="observed",
                data={"action": action, "state_key": state_key, "reason": reason, "post_result": post_result, "evidence_count": 1},
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
            and not verified
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
            and snapshot.position.map_key == VIOLET_POKECENTER_HEAL_TARGET.map_key
            and snapshot.position.tile in VIOLET_POKECENTER_HEAL_TARGET.tiles
        ):
            self.pokecenter_face_up_attempted_at = snapshot.position.tile
            self.last_step_verified = True
            self.verification_reason = "pokecenter_nurse_facing_attempted"
            self.stuck_counter = 0
            self.recovery_level = 0
            return
        if action in TEXT_INPUT_END_SEQUENCE and likely_text_input_keyboard(before_state or {}):
            expected = TEXT_INPUT_END_SEQUENCE[self.nickname_sequence_index] if self.nickname_sequence_index < len(TEXT_INPUT_END_SEQUENCE) else None
            if action == expected and executed:
                self.nickname_sequence_index += 1
        if action in BATTLE_RUN_SEQUENCE and likely_untrusted_wild_battle_main(before_state or {}):
            expected = BATTLE_RUN_SEQUENCE[self.battle_run_sequence_index] if self.battle_run_sequence_index < len(BATTLE_RUN_SEQUENCE) else None
            if action == expected and executed:
                self.battle_run_sequence_index += 1
        if action in MART_BUY_ONE_SEQUENCE and likely_mart_purchase_screen(before_state or {}):
            expected = MART_BUY_ONE_SEQUENCE[self.mart_buy_sequence_index] if self.mart_buy_sequence_index < len(MART_BUY_ONE_SEQUENCE) else None
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
            if reason == "map_transition_observed":
                self.blocked_edges_by_map.clear()

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
        if self.stuck_counter == 0 and self.recovery_level == 0:
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
        }

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
            if battle_untrusted(state):
                return choose_battle_actions(state)
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
        self.battle_run_sequence_index = 0
        if likely_mart_purchase_screen(state):
            if self.mart_buy_sequence_index >= len(MART_BUY_ONE_SEQUENCE):
                self.mart_buy_sequence_index = 0
            action = MART_BUY_ONE_SEQUENCE[self.mart_buy_sequence_index]
            return [action], {
                "path_source": "mart_buy_balls",
                "next_step": action,
                "planned_path_length": len(MART_BUY_ONE_SEQUENCE) - self.mart_buy_sequence_index,
                "mart_buy_sequence_index": self.mart_buy_sequence_index,
                "reason": "buying Poke Balls before grinding/capture",
            } | self.blocked_edge_status()
        if confirmed_dialogue(dialog) or visual_dialogue_active(state):
            self.button_failure_count = 0
            return ["press_a"], {"path_source": "dialogue", "next_step": "press_a", "planned_path_length": 1}
        if ambiguous_dialogue(dialog):
            return [], {
                "path_source": "ambiguous_dialogue",
                "next_step": None,
                "planned_path_length": None,
                "dialogue_reason": "window_stack_implausible",
            }
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
                "roster_roles": roster_roles(snapshot),
                "catch_decision": {
                    "action": catch_decision.action,
                    "reason": catch_decision.reason,
                    "target_species": catch_decision.target_species,
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
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            state = None
            self.record_api_failure(sys.exc_info()[1])

        status = self.build_status(control, state)
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
                    posted_actions = posted_actions_for(action, walk_hold_frames_for_navigation(action, status.get("navigation")))
                    status["navigation"]["posted_actions"] = posted_actions
                    result = self.post_actions(posted_actions)
                    if result.get("success") is False:
                        self.record_action_outcome(action, False, "post_actions_unsuccessful", "post_failed", state)
                    else:
                        after_state = self.request_json("/state")
                        verified, reason = verify_single_action(state, after_state, action)
                        self.record_action_outcome(action, verified, reason, "ok", state)
                        reward = self.record_learning_transition(action, verified, reason, "ok", state, after_state)
                        status["learning"] = self.learning_summary() | {"last_reward": reward}
                if self.last_step_result in {"dry_run", "blocked_by_control", "blocked_invalid_action", "post_failed"}:
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
