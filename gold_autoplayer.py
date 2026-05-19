#!/usr/bin/env python3
"""Goal-directed autonomous Pokemon Gold player for generic GBC mode.

This is deliberately *not* a pure random visual novelty bot.  It uses the best
parts of current Pokemon-agent/RL practice that are practical in our generic
Gold setup:

* structured observations when the server/RAM reader can provide them;
* visual screen classification when Gen-2 RAM is unavailable;
* curriculum phases for boot/title/intro/dialog/overworld;
* macro actions instead of one noisy button at a time;
* map/coordinate novelty and frontier-style rewards when coordinates exist;
* anti-oscillation and anti-menu-spam penalties;
* durable logs/policy/episode state so learning carries forward.

The low-level server still only exposes screenshots and button actions for Gold,
so this is a symbolic/heuristic controller with lightweight bandit learning, not
a full neural RL trainer.  It is designed to make observable progress now and to
be RAM-reader-ready later.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import random
import time
import urllib.error
import urllib.request
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageFilter, ImageStat

from pokemon_agent.navigation import GOLD_MAP_REGISTRY, plan_to_any

MOVE_ACTIONS = ["walk_up", "walk_down", "walk_left", "walk_right"]
INTERACT_ACTIONS = ["press_a", "press_b", "press_start"]
BASIC_ACTIONS = [*MOVE_ACTIONS, "press_a", "press_b"]
ODOMETRY_VERSION = 6
MOVE_DELTAS = {
    "walk_up": (0, -1),
    "walk_down": (0, 1),
    "walk_left": (-1, 0),
    "walk_right": (1, 0),
}

# Short deterministic procedures used by the curriculum controller.  All are
# intentionally small so the dashboard visibly updates and the loop can re-plan.
MACROS: dict[str, list[str]] = {
    "advance_text": ["press_a", "wait_20", "press_a", "wait_20"],
    "speed_text": ["hold_b_30", "press_a", "wait_20"],
    "confirm": ["press_a", "wait_30"],
    "cancel_or_close": ["press_b", "wait_20"],
    "start_or_confirm": ["press_start", "wait_30", "press_a", "wait_30"],
    "unstick_menu": ["press_b", "wait_20", "press_b", "wait_20", "press_a", "wait_20"],
    "probe_up": ["walk_up", "wait_20"],
    "probe_down": ["walk_down", "wait_20"],
    "probe_left": ["walk_left", "wait_20"],
    "probe_right": ["walk_right", "wait_20"],
}

# Collision-derived Route 29 path from pret/pokegold Route29.blk + Johto
# collision, treating ledges as blocked. Coordinates are actual map x/y.
ROUTE29_TO_CHERRYGROVE: list[tuple[int, int]] = [
    (53, 9), (44, 9), (44, 14), (38, 14), (38, 16),
    (31, 16), (31, 11), (36, 11), (36, 7), (23, 7),
    (23, 6), (21, 6), (21, 4), (16, 4), (16, 6),
    (11, 6), (11, 10), (4, 10), (4, 7), (0, 7),
]

ROUTE29_RECOVERY_WAYPOINTS: list[tuple[int, int]] = [
    (27, 14), (31, 14), (31, 11), (36, 11), (36, 7),
    (23, 7), (23, 6), (21, 6), (21, 4), (16, 4),
    (16, 6), (11, 6), (11, 10), (4, 10), (4, 7), (0, 7),
]

# Route 30 upper path to Route 31. The eastern branch dead-ends at Mr. Pokemon's
# house; after the egg quest the bot must weave back through the lower opening
# in the tree wall, then climb the west lane north.
ROUTE30_TO_ROUTE31: list[tuple[int, int]] = [
    (19, 6), (19, 9), (17, 9), (17, 15), (18, 15),
    (18, 19), (17, 19), (17, 21), (15, 21), (15, 30),
    (11, 30), (14, 30), (14, 23), (15, 20), (17, 20),
    (17, 19), (16, 19), (16, 18), (17, 18), (19, 18),
    (19, 15), (16, 15), (16, 13), (13, 13), (13, 10),
    (13, 17), (11, 17), (11, 23), (12, 23), (12, 25),
    (14, 25), (14, 30), (9, 30), (9, 28), (7, 28),
    (7, 26), (5, 26), (5, 0),
]

ROUTE30_GRID_ROWS: tuple[str, ...] = (
    "TTTT..ggTTTTTTTTTTTT",
    "TTTT..ggTTTTTTTTTTTT",
    "TTTT..ggTTTTSSSSTTTT",
    "TTTT..ggTTTTggggTTTT",
    "TTSS..ggTTggggggffff",
    "TTgg..ggTTgggggS....",
    "gggg..ggSggggggggggg",
    "gggg..ggSggggggggggg",
    "gggg..gSTTgggggggggg",
    "gggg..SSTTgggggggggg",
    "gggg..TTTTTTggggggTT",
    "gggg..TTTTTTggggggTT",
    "gggg..TTTTTTggggggTT",
    "gggg..TTTTTTggggggTT",
    "gggg..SSTTTTggTTgggg",
    "gggg..ggTTTTggTTgggg",
    "TTgg..ggTTSSggTTTTgg",
    "TTgg..ggTTggggTTTTgg",
    "TTgg..ggTTggTTTTgggg",
    "TTgg..ggTTggTTTTgggg",
    "TTgg..ggTTggTTggggTT",
    "TTgS..ggTTggTTggggTT",
    "TTgg..TTTTggggggTTTT",
    "TTgg..TTTTggggggTTTT",
    "TTgg..TTTTTTggggTTTT",
    "TTgg..TTTTTTggggTTTT",
    "TTgg..ggTTTTTTggTTTT",
    "TTgg..ggTTTTTTggTTTT",
    "TTgg..ggggTTgg..TTTT",
    "TTgg..ggggTTgS..TTTT",
    "TTgg..gggggg....ggTT",
    "TTgg..gggggg....ggTT",
    "TT............ggggTT",
    "TT............ggggTT",
    "TT..TTTTggggggggTTTT",
    "TT..TTTTggggggggTTTT",
    "TT..TTTTTTTTggggTTTT",
    "TT..TTTTTTTTggggTTTT",
    "TT..ggffffTTggSwwSTT",
    "TT..gg....TTggwwwwTT",
    "TT..........ggwwwwTT",
    "TT..........ggwwwwTT",
    "TTTTTTgggg..ggwwwwTT",
    "TTTTTTgggS..ggwwwwTT",
    "TTTTTT......ggTTTTTT",
    "TTTTTT......ggTTTTTT",
    "TTTTTT..ggggggTTTTTT",
    "TTTTTT..ggggggTTTTTT",
    "TTTTTT..ggggggTTTTTT",
    "TTTTTT..ggggggTTTTTT",
    "TTTTTT..TTTTTTTTTTSw",
    "TTTTTT..TTTTTTTTTTww",
    "TTTTTT..TTTTTTTTTTww",
    "TTTTTT..TTTTTTTTTTww",
)
ROUTE30_WALKABLE: set[tuple[int, int]] = {
    (x, y)
    for y, row in enumerate(ROUTE30_GRID_ROWS)
    for x, tile in enumerate(row)
    if tile in {".", "g", "f"}
}
ROUTE30_ROUTE31_TARGETS = {(4, 0), (5, 0)}
ROUTE30_CHERRYGROVE_TARGETS = {(6, 53), (7, 53)}

# Game Boy naming keyboard starts on A. These words are short enough to enter
# deterministically on the Gen-2 keyboard and keep names readable on the party UI.
NAMING_MACROS: dict[str, list[str]] = {
    "NOVA": [
        "walk_down", "walk_down", "walk_right", "press_a",  # N
        "walk_right", "press_a",                              # O
        "walk_down", "walk_right", "press_a",                 # V
        "walk_up", "walk_up", "walk_up", "walk_left", "walk_left", "walk_left", "press_a",  # A
        "press_start", "wait_60", "press_a", "wait_80",
    ],
    "EMBER": [
        "walk_right", "walk_right", "walk_right", "walk_right", "press_a",  # E
        "walk_down", "walk_down", "walk_left", "walk_left", "walk_left", "walk_left", "press_a",  # M
        "walk_up", "walk_up", "walk_right", "press_a",      # B
        "walk_right", "walk_right", "press_a",               # E
        "walk_down", "walk_down", "walk_right", "press_a",  # R
        "press_start", "wait_60", "press_a", "wait_80",
    ],
}


@dataclass
class Observation:
    visual: dict[str, Any]
    state: dict[str, Any]

    @property
    def screen_class(self) -> str:
        return str(self.visual.get("screen_class", "unknown"))

    @property
    def visual_hash(self) -> str:
        return str(self.visual.get("hash", ""))

    @property
    def coord(self) -> tuple[int, int, int] | None:
        player = self.state.get("player") or {}
        pos = player.get("position") or {}
        x, y = pos.get("x"), pos.get("y")
        map_id = pos.get("map_id")
        if isinstance(x, int) and isinstance(y, int) and isinstance(map_id, int) and map_id > 0:
            return (map_id, x, y)
        return None

    @property
    def in_battle(self) -> bool | None:
        battle = self.state.get("battle") or {}
        value = battle.get("in_battle")
        if isinstance(value, bool):
            return value
        return None

    @property
    def badges(self) -> int:
        flags = self.state.get("flags") or {}
        for key in ("badges", "badge_count"):
            value = flags.get(key)
            if isinstance(value, int):
                return value
        johto = flags.get("johto_badges")
        kanto = flags.get("kanto_badges")
        if isinstance(johto, int) or isinstance(kanto, int):
            return (johto if isinstance(johto, int) else 0).bit_count() + (kanto if isinstance(kanto, int) else 0).bit_count()
        return 0

    @property
    def has_starter(self) -> bool:
        if self.state.get("party"):
            return True
        flags = self.state.get("flags") or {}
        if isinstance(flags.get("has_starter"), bool):
            return bool(flags["has_starter"])
        return bool((self.state.get("party") or []))

    def bag_item_count(self, *item_ids: int) -> int:
        total = 0
        wanted = set(item_ids)
        for item in self.state.get("bag") or []:
            if not isinstance(item, dict):
                continue
            if item.get("item_id") in wanted and isinstance(item.get("quantity"), int):
                total += int(item["quantity"])
        return total

    @property
    def lead_hp_ratio(self) -> float | None:
        party = self.state.get("party") or []
        if not party or not isinstance(party[0], dict):
            return None
        hp = party[0].get("hp")
        max_hp = party[0].get("max_hp")
        if isinstance(hp, int) and isinstance(max_hp, int) and max_hp > 0:
            return hp / max_hp
        return None

    @property
    def lead_hp(self) -> int | None:
        party = self.state.get("party") or []
        if not party or not isinstance(party[0], dict):
            return None
        hp = party[0].get("hp")
        return hp if isinstance(hp, int) else None

    @property
    def needs_healing(self) -> bool:
        ratio = self.lead_hp_ratio
        return ratio is not None and ratio <= 0.40

    @property
    def money(self) -> int:
        money = (self.state.get("player") or {}).get("money")
        return money if isinstance(money, int) else 0

    @property
    def map_group_number(self) -> tuple[int, int] | None:
        pos = (self.state.get("player") or {}).get("position") or {}
        group = pos.get("map_group")
        number = pos.get("map_number")
        if isinstance(group, int) and isinstance(number, int):
            return group, number
        return None


@dataclass
class GoalDirectedGoldPlayer:
    base_url: str
    data_dir: Path
    rng: random.Random = field(default_factory=random.Random)
    q: dict[str, dict[str, float]] = field(default_factory=dict)
    visits: dict[str, int] = field(default_factory=dict)
    coord_visits: dict[str, int] = field(default_factory=dict)
    blocked_moves: dict[str, list[str]] = field(default_factory=dict)
    edges: dict[str, list[str]] = field(default_factory=dict)
    directed_edges: dict[str, dict[str, Any]] = field(default_factory=dict)
    places: dict[str, dict[str, Any]] = field(default_factory=dict)
    important_npcs: dict[str, dict[str, Any]] = field(default_factory=dict)
    local_position: dict[str, int] = field(default_factory=lambda: {"x": 0, "y": 0})
    local_cells: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_guidance_note: str = ""
    last_hash: str | None = None
    last_coord: tuple[int, int, int] | None = None
    repeated_hash_count: int = 0
    repeated_position_count: int = 0
    turn: int = 0
    last_save_turn: int = 0
    phase: str = "boot"
    recent_actions: deque[str] = field(default_factory=lambda: deque(maxlen=24))
    recent_hashes: deque[str] = field(default_factory=lambda: deque(maxlen=24))
    recent_coords: deque[str] = field(default_factory=lambda: deque(maxlen=24))
    recent_local_positions: deque[str] = field(default_factory=lambda: deque(maxlen=16))
    saved_milestones: set[str] = field(default_factory=set)
    route_target: str = ""
    route_last_distance: int | None = None
    route_no_progress_count: int = 0
    route_indices: dict[str, int] = field(default_factory=dict)
    last_event_map_id: int | None = None
    last_event_battle: bool = False
    last_event_battle_species: int | None = None
    last_event_battle_level: int | None = None
    last_event_hp: int | None = None
    last_event_max_hp: int | None = None
    last_event_needs_healing: bool = False
    last_event_party_count: int = 0
    last_event_badges: int = 0
    last_stuck_event_turn: int = -9999
    last_self_recovery_turn: int = -9999
    wrote_non_v1_idle_status: bool = False
    self_recovery_attempts: dict[str, int] = field(default_factory=dict)
    naming_sessions: dict[str, int] = field(default_factory=dict)

    @staticmethod
    def movement_primitive(actions: list[str]) -> str | None:
        return next((a for a in actions if a.startswith("walk_") or a.startswith("hold_")), None)

    @staticmethod
    def action_direction(action: str) -> str | None:
        parts = action.split("_")
        if len(parts) >= 2 and parts[0] in {"walk", "hold"} and parts[1] in {"up", "down", "left", "right"}:
            return parts[1]
        return None

    @staticmethod
    def direction_action(direction: str) -> str:
        return f"walk_{direction}"

    @staticmethod
    def normalized_move_action(action: str) -> str:
        direction = GoalDirectedGoldPlayer.action_direction(action)
        if direction and action.startswith("hold_"):
            return f"walk_{direction}"
        return action

    def coord_blocked_directions(self, obs: Observation) -> set[str]:
        coord_key = self.coord_key(obs.coord)
        blocked_actions = set(self.blocked_moves.get(coord_key, [])) if coord_key else set()
        out: set[str] = set()
        for action in blocked_actions:
            direction = self.action_direction(action)
            if direction:
                out.add(direction)
        return out

    @staticmethod
    def opposite_direction(direction: str) -> str | None:
        return {"up": "down", "down": "up", "left": "right", "right": "left"}.get(direction)

    @staticmethod
    def next_actual(actual: tuple[int, int], direction: str) -> tuple[int, int]:
        x, y = actual
        if direction == "left":
            return x - 1, y
        if direction == "right":
            return x + 1, y
        if direction == "up":
            return x, y - 1
        if direction == "down":
            return x, y + 1
        return x, y

    def coord_key_actual(self, key: str | None) -> tuple[int, int] | None:
        if not key or key == "none":
            return None
        try:
            _map_id, raw_x, raw_y = (int(part) for part in key.split(":", 2))
        except ValueError:
            return None
        return raw_y, raw_x

    def actual_coord_key(self, obs: Observation, actual: tuple[int, int]) -> str | None:
        if obs.coord is None:
            return None
        map_id = obs.coord[0]
        x, y = actual
        return f"{map_id}:{y}:{x}"

    def directed_edge_key(self, coord_key: str, direction: str) -> str:
        return f"{coord_key}|{direction}"

    def directed_edge_state(self, coord_key: str, direction: str) -> dict[str, Any]:
        return self.directed_edges.setdefault(self.directed_edge_key(coord_key, direction), {
            "from": coord_key,
            "direction": direction,
            "state": "unknown",
            "open_count": 0,
            "blocked_count": 0,
        })

    def edge_blocked(self, coord_key: str, direction: str) -> bool:
        state = self.directed_edges.get(self.directed_edge_key(coord_key, direction)) or {}
        if int(state.get("open_count", 0)) > 0:
            return False
        return state.get("state") == "blocked" or direction in {
            self.action_direction(action) for action in self.blocked_moves.get(coord_key, [])
        }

    def structured_dialog_active(self, obs: Observation) -> bool:
        dialog = obs.state.get("dialog") or {}
        active = bool(dialog.get("active")) or int(dialog.get("window_stack_size") or 0) > 0
        if not active:
            return False
        if obs.screen_class == "overworld_or_battle" and obs.in_battle is not True and not obs.visual.get("bright_dialogue_panel"):
            return False
        if (
            obs.map_group_number == (26, 1)
            and obs.coord is not None
            and obs.in_battle is not True
            and not obs.visual.get("bright_dialogue_panel")
        ):
            return False
        battle = obs.state.get("battle") or {}
        # The Gold RAM dialog/window flags can remain set after wild battle text.
        # If the screen has been unchanged for many turns and is not a bright
        # textbox or active battle, let overworld navigation recover instead of
        # pressing A forever.
        if (
            (self.repeated_hash_count >= 10 or self.repeated_position_count >= 10)
            and obs.in_battle is not True
            and (not obs.needs_healing or bool(battle.get("wild_species_id") or battle.get("enemy_level")))
            and not obs.visual.get("bright_dialogue_panel")
        ):
            return False
        if (
            self.repeated_position_count >= 2
            and obs.in_battle is not True
            and obs.needs_healing
            and bool(battle.get("wild_species_id") or battle.get("enemy_level"))
            and not obs.visual.get("bright_dialogue_panel")
        ):
            return False
        return True

    def edge_open_target(self, coord_key: str, direction: str) -> str | None:
        edge = self.directed_edges.get(self.directed_edge_key(coord_key, direction)) or {}
        if edge.get("state") == "open" and isinstance(edge.get("to"), str):
            return edge["to"]
        actual = self.coord_key_actual(coord_key)
        if actual is None:
            return None
        expected = self.next_actual(actual, direction)
        for neighbor in self.edges.get(coord_key, []):
            if self.coord_key_actual(neighbor) == expected:
                return neighbor
        return None

    def open_neighbors(self, coord_key: str) -> list[tuple[str, str]]:
        neighbors: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for direction in ("up", "down", "left", "right"):
            if self.edge_blocked(coord_key, direction):
                continue
            target = self.edge_open_target(coord_key, direction)
            if target:
                item = (direction, target)
                if item not in seen:
                    neighbors.append(item)
                    seen.add(item)
        return neighbors

    def coord_cycle_length(self) -> int | None:
        recent = [c for c in self.recent_coords if c]
        for size in (2, 3, 4):
            if len(recent) >= size * 3:
                tail = recent[-size * 3:]
                if tail[:size] == tail[size:size * 2] == tail[size * 2:]:
                    return size
            if len(recent) >= size * 2:
                tail = recent[-size * 2:]
                if tail[:size] == tail[size:]:
                    return size
        return None

    def planner_path_direction(self, obs: Observation, target: tuple[int, int]) -> str | None:
        start = self.coord_key(obs.coord)
        goal = self.actual_coord_key(obs, target)
        if start == "none" or goal is None or start == goal:
            return None
        queue: deque[str] = deque([start])
        parents: dict[str, tuple[str, str] | None] = {start: None}
        while queue and len(parents) < 512:
            node = queue.popleft()
            if node == goal:
                break
            for direction, neighbor in self.open_neighbors(node):
                if neighbor in parents:
                    continue
                parents[neighbor] = (node, direction)
                queue.append(neighbor)
        if goal not in parents:
            return None
        node = goal
        first_direction = None
        while parents[node] is not None:
            parent, direction = parents[node]
            first_direction = direction
            node = parent
            if parent == start:
                self.log_event("planner_path_found", {
                    "snapshot": self.event_snapshot(obs),
                    "target_actual": {"x": target[0], "y": target[1]},
                    "next_direction": direction,
                    "known_path_nodes": len(parents),
                })
                return direction
        return first_direction

    def planner_frontier_direction(
        self,
        obs: Observation,
        primary: str,
        target: tuple[int, int] | None,
    ) -> str | None:
        actual = self.actual_xy(obs)
        coord_key = self.coord_key(obs.coord)
        if actual is None or coord_key == "none":
            return None
        cycle = self.coord_cycle_length()
        if cycle:
            self.log_event("cycle_detected", {
                "snapshot": self.event_snapshot(obs),
                "cycle_length": cycle,
                "recent_coords": list(self.recent_coords)[-12:],
                "recent_actions": list(self.recent_actions)[-12:],
            })
        last_direction = self.action_direction(self.recent_actions[-1]) if self.recent_actions else None
        scored: list[tuple[float, str, str | None]] = []
        for direction in ("up", "down", "left", "right"):
            if self.edge_blocked(coord_key, direction):
                continue
            next_actual = self.next_actual(actual, direction)
            next_key = self.actual_coord_key(obs, next_actual)
            score = 0.0
            if target is not None:
                score += abs(target[0] - next_actual[0]) + abs(target[1] - next_actual[1])
            if direction == primary:
                score -= 0.35
            open_target = self.edge_open_target(coord_key, direction)
            if open_target:
                score -= 0.15
                next_key = open_target
            else:
                score -= 0.45
            if next_key:
                score += min(self.coord_visits.get(next_key, 0), 20) * 0.08
                if next_key in list(self.recent_coords)[-8:]:
                    score += 3.5 if cycle else 1.0
            if cycle and last_direction and direction == self.opposite_direction(last_direction):
                score += 5.0
            if self._would_oscillate(self.direction_action(direction)):
                score += 2.0
            scored.append((score, direction, next_key))
        if not scored:
            self.log_event("planner_no_safe_move", {
                "snapshot": self.event_snapshot(obs),
                "primary": primary,
                "target_actual": {"x": target[0], "y": target[1]} if target else None,
                "blocked_directions": sorted(self.coord_blocked_directions(obs)),
            })
            return None
        scored.sort(key=lambda item: (item[0], item[1]))
        score, direction, next_key = scored[0]
        self.log_event("planner_frontier_selected", {
            "snapshot": self.event_snapshot(obs),
            "primary": primary,
            "target_actual": {"x": target[0], "y": target[1]} if target else None,
            "selected_direction": direction,
            "selected_key": next_key,
            "score": round(score, 3),
            "cycle_length": cycle,
        })
        return direction

    def planned_direction(
        self,
        obs: Observation,
        primary: str,
        target: tuple[int, int] | None = None,
    ) -> str:
        if target is not None:
            path_direction = self.planner_path_direction(obs, target)
            if path_direction:
                return path_direction
        frontier_direction = self.planner_frontier_direction(obs, primary, target)
        if frontier_direction:
            return frontier_direction
        return primary

    def learned_detour_direction(
        self,
        obs: Observation,
        primary: str,
        target: tuple[int, int] | None = None,
    ) -> str:
        blocked = self.coord_blocked_directions(obs)
        stuck = self.repeated_position_count >= 2 or self._coord_looping() or self._coord_oscillating()
        coord_key = self.coord_key(obs.coord)
        if target is not None or stuck or primary in blocked or (coord_key != "none" and self.edge_blocked(coord_key, primary)):
            return self.planned_direction(obs, primary, target)
        if primary not in blocked and not stuck:
            return primary
        choices = [d for d in ("up", "down", "left", "right") if d not in blocked]
        if not choices:
            return primary
        opposite = {"up": "down", "down": "up", "left": "right", "right": "left"}
        last_direction = self.action_direction(self.recent_actions[-1]) if self.recent_actions else None
        if (self._coord_looping() or self._coord_oscillating()) and last_direction:
            non_reverse = [d for d in choices if d != opposite.get(last_direction)]
            if non_reverse:
                choices = non_reverse
        actual = self.actual_xy(obs)
        if target is not None and actual is not None:
            tx, ty = target
            scored = []
            for direction in choices:
                nx, ny = self.next_actual(actual, direction)
                # Prefer directions that reduce target distance, but keep trying
                # alternatives learned as open when the primary path is blocked.
                score = abs(tx - nx) + abs(ty - ny)
                if direction == primary:
                    score -= 0.25
                if direction in {"left", "right"} and primary in {"up", "down"}:
                    score -= 0.05
                if direction in {"up", "down"} and primary in {"left", "right"}:
                    score -= 0.05
                if last_direction and direction == opposite.get(last_direction):
                    score += 2.0
                scored.append((score, direction))
            return min(scored)[1]
        options = {
            "left": ["up", "down", "left", "right"],
            "right": ["up", "down", "right", "left"],
            "up": ["left", "right", "up", "down"],
            "down": ["left", "right", "down", "up"],
        }.get(primary, ["up", "down", "left", "right"])
        rotate = min(max(self.repeated_position_count - 2, 0), len(options) - 1)
        for direction in options[rotate:] + options[:rotate]:
            if direction in choices:
                return direction
        return choices[0]

    def learned_move_actions(
        self,
        obs: Observation,
        primary: str,
        name: str,
        target: tuple[int, int] | None = None,
    ) -> tuple[str, list[str]]:
        direction = self.learned_detour_direction(obs, primary, target)
        suffix = direction if direction == primary else f"learned_{direction}"
        return f"{name}_{suffix}", [self.direction_action(direction), "wait_30"]

    def choose_route_step(self, obs: Observation, target: str, preferred: list[str]) -> tuple[str, list[str]]:
        if target != self.route_target:
            self.route_target = target
            self.route_last_distance = None
            self.route_no_progress_count = 0
        pos = (obs.state.get("player") or {}).get("position") or {}
        x, y = pos.get("x"), pos.get("y")
        if isinstance(x, int) and isinstance(y, int):
            # In this PyBoy/Gold setup DA02 changes on up/down and DA03 changes
            # on left/right. Treat the second exposed coordinate as horizontal
            # for route progress, even though the public field is named "y".
            horizontal = y
            vertical = x
            distance = {"west": horizontal, "east": 255 - horizontal, "north": vertical, "south": 255 - vertical}.get(target)
            if distance is not None:
                if self.route_last_distance is not None and distance >= self.route_last_distance:
                    self.route_no_progress_count += 1
                else:
                    self.route_no_progress_count = 0
                self.route_last_distance = distance

        blocked = self.coord_blocked_directions(obs)
        # Coordinate-stuck beats visual-stuck: if the exact tile did not change,
        # immediately avoid the blocked direction at that coordinate.
        oscillating = self._coord_oscillating()
        force_detour = self.repeated_position_count >= 2 or self.route_no_progress_count >= 4 or oscillating
        choices = preferred[:]
        if (oscillating or self.route_no_progress_count >= 5) and target == "west":
            choices = ["up", "down", "left", "right"]
        elif (oscillating or self.route_no_progress_count >= 5) and target == "east":
            choices = ["up", "down", "right", "left"]
        elif oscillating:
            choices = ["up", "down", "left", "right"]
        if force_detour:
            choices = [d for d in preferred if d not in blocked] or ["up", "down", "right", "left"]
            if (oscillating or self.route_no_progress_count >= 5) and target == "west":
                choices = [d for d in ["up", "down", "left", "right"] if d not in blocked] or ["up"]
            # Rotate choices so repeated failures explore a different wall-follow
            # side instead of oscillating between the same two blocked moves.
            rotate = 0 if oscillating else min(max(self.repeated_position_count - 2, 0), len(choices) - 1)
            choices = choices[rotate:] + choices[:rotate]
        for direction in choices:
            if direction not in blocked:
                return f"route_{target}_{direction}", [self.direction_action(direction), "wait_30"]
        direction = choices[0] if choices else preferred[0]
        return f"route_{target}_{direction}", [self.direction_action(direction), "wait_30"]

    def _coord_oscillating(self) -> bool:
        recent = [c for c in self.recent_coords if c]
        if len(recent) < 6:
            return False
        tail = recent[-6:]
        return self.coord_cycle_length() == 2 or (len(set(tail)) <= 2 and tail[-1] == tail[-3] == tail[-5])

    def _coord_looping(self) -> bool:
        recent = [c for c in self.recent_coords if c]
        if len(recent) < 8:
            return False
        tail = recent[-8:]
        return self.coord_cycle_length() in {2, 3, 4} or len(set(tail)) <= 3

    @staticmethod
    def actual_xy(obs: Observation) -> tuple[int, int] | None:
        pos = (obs.state.get("player") or {}).get("position") or {}
        raw_x, raw_y = pos.get("x"), pos.get("y")
        if isinstance(raw_x, int) and isinstance(raw_y, int):
            # The exposed Gold RAM fields are reliable, but in this runtime the
            # first value changes on north/south and the second on west/east.
            # Use actual_x/actual_y for map waypoints and leave raw values visible.
            return raw_y, raw_x
        return None

    def step_toward_actual(self, obs: Observation, target_x: int, target_y: int, name: str) -> tuple[str, list[str]]:
        actual = self.actual_xy(obs)
        if actual is None:
            return f"{name}_wait_for_coords", ["wait_60"]
        x, y = actual
        primary = None
        if x < target_x:
            primary = "right"
        elif x > target_x:
            primary = "left"
        elif y < target_y:
            primary = "down"
        elif y > target_y:
            primary = "up"
        if primary:
            return self.learned_move_actions(obs, primary, name, (target_x, target_y))
        return f"{name}_arrived", ["wait_60"]

    def step_toward_actual_axis(self, obs: Observation, target_x: int, target_y: int, name: str, axis: str = "x") -> tuple[str, list[str]]:
        actual = self.actual_xy(obs)
        if actual is None:
            return f"{name}_wait_for_coords", ["wait_60"]
        x, y = actual
        primary = None
        if axis == "y":
            if y < target_y:
                primary = "down"
            elif y > target_y:
                primary = "up"
            elif x < target_x:
                primary = "right"
            elif x > target_x:
                primary = "left"
        else:
            if x < target_x:
                primary = "right"
            elif x > target_x:
                primary = "left"
            elif y < target_y:
                primary = "down"
            elif y > target_y:
                primary = "up"
        if primary:
            return self.learned_move_actions(obs, primary, name, (target_x, target_y))
        return f"{name}_arrived", ["wait_60"]

    def follow_actual_waypoints(self, obs: Observation, waypoints: list[tuple[int, int]], name: str) -> tuple[str, list[str]] | None:
        actual = self.actual_xy(obs)
        if actual is None or not waypoints:
            return None
        x, y = actual
        idx = self.route_indices.get(name)
        if idx is None:
            idx = self._waypoint_segment_target_index(actual, waypoints)
        else:
            idx = max(0, min(idx, len(waypoints) - 1))
            segment_idx = self._waypoint_segment_target_index(actual, waypoints)
            # Monotonic progress: if we are physically on a later segment, never
            # snap back to an earlier nearby waypoint.
            if name == "route30_to_route31_path":
                target_x, target_y = waypoints[idx]
                far_from_target = abs(x - target_x) + abs(y - target_y) > 8
                idx = segment_idx if far_from_target and segment_idx < idx else max(idx, segment_idx)
            else:
                idx = max(idx, segment_idx)
        if actual == waypoints[idx] and idx < len(waypoints) - 1:
            idx += 1
        self.route_indices[name] = idx
        target_x, target_y = waypoints[idx]
        if actual == (target_x, target_y) and idx == len(waypoints) - 1:
            return f"{name}_arrived", ["wait_60"]
        if x < target_x:
            direction = "right"
        elif x > target_x:
            direction = "left"
        elif y < target_y:
            direction = "down"
        elif y > target_y:
            direction = "up"
        else:
            direction = "down"
        if name == "route30_to_route31_path":
            blocked = self.coord_blocked_directions(obs)
            coord_key = self.coord_key(obs.coord)
            stuck = self.repeated_position_count >= 2 or self._coord_looping() or self._coord_oscillating()
            if direction not in blocked and not stuck and not (coord_key != "none" and self.edge_blocked(coord_key, direction)):
                learned = direction
            else:
                learned = self.learned_detour_direction(obs, direction, (target_x, target_y))
        else:
            learned = self.learned_detour_direction(obs, direction, (target_x, target_y))
        suffix = learned if learned == direction else f"learned_{learned}"
        return f"{name}_{idx}_{suffix}", [self.direction_action(learned), "wait_30"]

    def route30_grid_macro(self, obs: Observation, targets: set[tuple[int, int]], name: str) -> tuple[str, list[str]] | None:
        actual = self.actual_xy(obs)
        if actual is None:
            return None
        start = self._nearest_route30_walkable(actual)
        if start is None:
            return None
        if actual in targets:
            direction = "up" if min(y for _, y in targets) <= 1 else "down"
            return f"{name}_enter_connection", [self.direction_action(direction), "wait_80"]
        for avoid_learned_blocks in (True, False):
            first = self._route30_grid_first_step(start, targets, avoid_learned_blocks)
            if first is not None:
                direction = self._direction_between(start, first)
                if actual != start:
                    direction = self._direction_toward(actual, start)
                suffix = direction if avoid_learned_blocks else f"fallback_{direction}"
                return f"{name}_grid_{suffix}", [self.direction_action(direction), "wait_30"]
        return None

    def _route30_grid_first_step(self, start: tuple[int, int], targets: set[tuple[int, int]], avoid_learned_blocks: bool) -> tuple[int, int] | None:
        queue = deque([start])
        previous: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
        while queue:
            current = queue.popleft()
            if current in targets:
                step = current
                while previous[step] is not None and previous[step] != start:
                    step = previous[step]  # type: ignore[index]
                return step if step != start else None
            for direction in ("up", "right", "left", "down"):
                dx, dy = MOVE_DELTAS[self.direction_action(direction)]
                neighbor = (current[0] + dx, current[1] + dy)
                if neighbor in previous or neighbor not in ROUTE30_WALKABLE:
                    continue
                if avoid_learned_blocks and self.edge_blocked(f"6657:{current[1]}:{current[0]}", direction):
                    continue
                previous[neighbor] = current
                queue.append(neighbor)
        return None

    @staticmethod
    def _nearest_route30_walkable(actual: tuple[int, int]) -> tuple[int, int] | None:
        if actual in ROUTE30_WALKABLE:
            return actual
        ax, ay = actual
        candidates = sorted(ROUTE30_WALKABLE, key=lambda p: abs(p[0] - ax) + abs(p[1] - ay))
        return candidates[0] if candidates and abs(candidates[0][0] - ax) + abs(candidates[0][1] - ay) <= 4 else None

    @staticmethod
    def _direction_between(start: tuple[int, int], end: tuple[int, int]) -> str:
        return GoalDirectedGoldPlayer._direction_toward(start, end)

    @staticmethod
    def _direction_toward(start: tuple[int, int], target: tuple[int, int]) -> str:
        sx, sy = start
        tx, ty = target
        if abs(tx - sx) >= abs(ty - sy) and tx != sx:
            return "right" if tx > sx else "left"
        if ty != sy:
            return "down" if ty > sy else "up"
        if tx != sx:
            return "right" if tx > sx else "left"
        return "up"

    @staticmethod
    def _waypoint_segment_target_index(actual: tuple[int, int], waypoints: list[tuple[int, int]]) -> int:
        x, y = actual
        for i, (a, b) in enumerate(zip(waypoints, waypoints[1:])):
            ax, ay = a
            bx, by = b
            if ay == by == y and min(ax, bx) <= x <= max(ax, bx):
                return i + 1
            if ax == bx == x and min(ay, by) <= y <= max(ay, by):
                return i + 1
        distances = [abs(x - wx) + abs(y - wy) for wx, wy in waypoints]
        nearest = min(range(len(waypoints)), key=lambda i: distances[i])
        if nearest < len(waypoints) - 1:
            return nearest + 1 if distances[nearest] <= 1 else nearest
        return nearest

    @property
    def q_path(self) -> Path:
        return self.data_dir / "gold_policy.json"

    @property
    def world_path(self) -> Path:
        return self.data_dir / "gold_world_model.json"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "gold_autoplayer.jsonl"

    @property
    def event_log_path(self) -> Path:
        return self.data_dir / "gold_events.jsonl"

    @property
    def control_path(self) -> Path:
        return self.data_dir / "gold_autoplayer_control.json"

    @property
    def status_path(self) -> Path:
        return self.data_dir / "gold_autoplayer_status.json"

    def read_control(self) -> dict[str, Any]:
        defaults = {
            "enabled": True,
            "engine": "v1",
            "objective": "reach Violet City and win the first gym badge",
            "movement_bias": "west_north",
            "dialogue_speed": "fast",
            "guidance_prompt": "",
            "single_step": True,
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

    def load(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.q_path.exists():
            payload = json.loads(self.q_path.read_text())
            self.q = payload.get("q", {})
            self.visits = payload.get("visits", {})
            self.turn = payload.get("turn", 0)
            self.phase = payload.get("phase", self.phase)
        if self.world_path.exists():
            payload = json.loads(self.world_path.read_text())
            self.coord_visits = payload.get("coord_visits", {})
            self.blocked_moves = payload.get("blocked_moves", {})
            self.edges = payload.get("edges", {})
            self.directed_edges = payload.get("directed_edges", {})
            self.places = payload.get("places", {})
            self.important_npcs = payload.get("important_npcs", {})
            self.saved_milestones = set(payload.get("saved_milestones", []))
            self.route_target = payload.get("route_target", "")
            self.route_last_distance = payload.get("route_last_distance")
            self.route_no_progress_count = int(payload.get("route_no_progress_count", 0))
            if payload.get("odometry_version") == ODOMETRY_VERSION:
                self.local_position = payload.get("local_position", self.local_position)
                self.local_cells = payload.get("local_cells", {})
            else:
                self.local_position = {"x": 0, "y": 0}
                self.local_cells = {}
            self.last_guidance_note = payload.get("last_guidance_note", "")
            self.self_recovery_attempts = payload.get("self_recovery_attempts", {})

    def persist(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if len(self.self_recovery_attempts) > 500:
            self.self_recovery_attempts = dict(list(self.self_recovery_attempts.items())[-500:])
        tmp = self.q_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "q": self.q,
            "visits": self.visits,
            "turn": self.turn,
            "phase": self.phase,
        }, indent=2, sort_keys=True))
        tmp.replace(self.q_path)
        tmp_world = self.world_path.with_suffix(".tmp")
        tmp_world.write_text(json.dumps({
            "coord_visits": self.coord_visits,
            "blocked_moves": self.blocked_moves,
            "edges": self.edges,
            "directed_edges": self.directed_edges,
            "places": self.places,
            "important_npcs": self.important_npcs,
            "saved_milestones": sorted(self.saved_milestones),
            "route_target": self.route_target,
            "route_last_distance": self.route_last_distance,
            "route_no_progress_count": self.route_no_progress_count,
            "odometry_version": ODOMETRY_VERSION,
            "local_position": self.local_position,
            "local_cells": self.local_cells,
            "last_guidance_note": self.last_guidance_note,
            "self_recovery_attempts": self.self_recovery_attempts,
        }, indent=2, sort_keys=True))
        tmp_world.replace(self.world_path)

    def request(self, path: str, data: dict[str, Any] | None = None, timeout: int = 20) -> bytes:
        url = self.base_url.rstrip("/") + path
        if data is None:
            return urllib.request.urlopen(url, timeout=timeout).read()
        body = json.dumps(data).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        return urllib.request.urlopen(req, timeout=timeout).read()

    def get_state(self) -> dict[str, Any]:
        try:
            return json.loads(self.request("/state", timeout=8))
        except Exception as exc:  # noqa: BLE001
            return {"error": repr(exc)}

    def screenshot(self) -> Image.Image:
        raw = self.request("/screenshot", timeout=10)
        return Image.open(io.BytesIO(raw)).convert("RGB")

    def ahash(self, im: Image.Image, size: int = 16) -> str:
        small = im.convert("L").resize((size, size), Image.Resampling.BILINEAR)
        px = list(small.getdata())
        avg = sum(px) / len(px)
        bits = ["1" if p >= avg else "0" for p in px]
        return "".join("%x" % int("".join(bits[i:i + 4]), 2) for i in range(0, len(bits), 4))

    def dhash(self, im: Image.Image, size: int = 12) -> str:
        small = im.convert("L").resize((size + 1, size), Image.Resampling.BILINEAR)
        px = list(small.getdata())
        bits: list[str] = []
        for y in range(size):
            row = y * (size + 1)
            for x in range(size):
                bits.append("1" if px[row + x] > px[row + x + 1] else "0")
        return "".join("%x" % int("".join(bits[i:i + 4]), 2) for i in range(0, len(bits), 4))

    @staticmethod
    def hash_distance(a: str, b: str) -> int:
        if not a or not b:
            return 0
        n = min(len(a), len(b))
        return sum((int(a[i], 16) ^ int(b[i], 16)).bit_count() for i in range(n))

    def visual_distance(self, before: Observation, after: Observation) -> int:
        return self.hash_distance(before.visual_hash, after.visual_hash) + self.hash_distance(str(before.visual.get("dhash", "")), str(after.visual.get("dhash", "")))

    def visual_step_succeeded(self, before: Observation, after: Observation, primitive: str, distance: int | None = None) -> bool:
        if primitive not in MOVE_DELTAS:
            return False
        if after.screen_class in {"dialogue", "transition"}:
            return False
        if after.screen_class == "menu_or_text" and not after.visual.get("striped_floor_like"):
            return False
        before_center = before.visual.get("player_center")
        after_center = after.visual.get("player_center")
        if isinstance(before_center, list) and isinstance(after_center, list) and len(before_center) == 2 and len(after_center) == 2:
            dx = float(after_center[0]) - float(before_center[0])
            dy = float(after_center[1]) - float(before_center[1])
            if primitive == "walk_left":
                if dx <= -3:
                    return True
            if primitive == "walk_right":
                if dx >= 3:
                    return True
            if primitive == "walk_up":
                if dy <= -3:
                    return True
            if primitive == "walk_down":
                if dy >= 3:
                    return True
        if distance is None:
            distance = self.visual_distance(before, after)
        # A real tile step shifts many perceptual bits; bumping into a wall tends
        # to leave only small animation noise.  The threshold is intentionally
        # conservative so walls/corners become blocked cells.
        return distance >= 90

    def player_sprite_features(self, im: Image.Image) -> dict[str, Any]:
        # Detect the red/orange player sprite in Gold.  This is intentionally
        # simple but spatial: it lets the bot distinguish "the room changed a
        # little" from "my character actually moved on the screen".
        px = im.convert("RGB").load()
        width, height = im.size
        seen: set[tuple[int, int]] = set()
        comps: list[dict[str, Any]] = []
        for y in range(8, height - 6):
            for x in range(0, width):
                if (x, y) in seen:
                    continue
                r, g, b = px[x, y]
                redish = r >= 120 and 25 <= g <= 120 and b <= 90 and r > g + 30
                if not redish:
                    continue
                stack = [(x, y)]
                seen.add((x, y))
                xs: list[int] = []
                ys: list[int] = []
                while stack:
                    cx, cy = stack.pop()
                    xs.append(cx)
                    ys.append(cy)
                    for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                        if nx < 0 or nx >= width or ny < 8 or ny >= height - 6 or (nx, ny) in seen:
                            continue
                        rr, gg, bb = px[nx, ny]
                        if rr >= 120 and 25 <= gg <= 120 and bb <= 90 and rr > gg + 30:
                            seen.add((nx, ny))
                            stack.append((nx, ny))
                if len(xs) >= 4:
                    comps.append({
                        "bbox": [min(xs), min(ys), max(xs), max(ys)],
                        "center": [round(sum(xs) / len(xs), 1), round(sum(ys) / len(ys), 1)],
                        "pixels": len(xs),
                    })
        mats = []
        for comp in comps:
            x0, y0, x1, y1 = comp["bbox"]
            w = x1 - x0 + 1
            h = y1 - y0 + 1
            if w >= 18 and h >= 5 and comp["pixels"] >= 120:
                mats.append(comp)
        if not comps:
            return {"player_center": None, "red_components": [], "red_mat_center": None}
        def is_sprite_sized(comp: dict[str, Any]) -> bool:
            x0, y0, x1, y1 = comp["bbox"]
            w = x1 - x0 + 1
            h = y1 - y0 + 1
            pixels = int(comp["pixels"])
            return 4 <= w <= 14 and 3 <= h <= 14 and pixels <= 90

        ball_row_ids: set[int] = set()
        sprite_candidates = [(i, comp) for i, comp in enumerate(comps) if is_sprite_sized(comp)]
        for i, comp in sprite_candidates:
            row = [(j, other) for j, other in sprite_candidates if abs(float(other["center"][1]) - float(comp["center"][1])) <= 3]
            if len(row) >= 3:
                xs = sorted(float(other["center"][0]) for _j, other in row)
                if xs[-1] - xs[0] >= 20:
                    ball_row_ids.update(j for j, _other in row)

        def sprite_score(item: tuple[int, dict[str, Any]]) -> tuple[int, int, float, int]:
            idx, comp = item
            sprite_sized = is_sprite_sized(comp)
            not_ball = idx not in ball_row_ids
            pixels = int(comp["pixels"])
            return (1 if sprite_sized else 0, 1 if not_ball else 0, float(comp["center"][1]), -pixels)

        # Ignore big red floor mats/carpets; the controllable sprite contributes
        # a small red/orange hat component.
        indexed = list(enumerate(comps))
        indexed.sort(key=sprite_score, reverse=True)
        chosen = indexed[0][1]
        mats.sort(key=lambda c: (c["center"][1], c["pixels"]), reverse=True)
        return {
            "player_center": chosen["center"],
            "red_components": comps[:12],
            "red_mat_center": mats[0]["center"] if mats else None,
            "red_mat_bbox": mats[0]["bbox"] if mats else None,
        }

    def features(self, im: Image.Image) -> dict[str, Any]:
        gray = im.convert("L")
        stat = ImageStat.Stat(gray)
        bbox = gray.getbbox()
        hist = gray.histogram()
        total = gray.width * gray.height
        non_black = 1.0 - (hist[0] / total)
        entropy = self._entropy(hist, total)

        lower = gray.crop((0, 92, 160, 144))
        upper = gray.crop((0, 0, 160, 92))
        lower_stat = ImageStat.Stat(lower)
        upper_stat = ImageStat.Stat(upper)
        lower_edges = lower.filter(ImageFilter.FIND_EDGES)
        edge_stat = ImageStat.Stat(lower_edges)
        lower_colors = lower.getcolors(maxcolors=1_000_000) or []
        dark_lower = sum(c for c, v in lower_colors if v < 72) / (160 * 52)
        bright_lower = sum(c for c, v in lower_colors if v > 180) / (160 * 52)
        palette_levels = len({v // 16 for _c, v in lower_colors})

        # Gen-2 palettes are not always bright white; text boxes can be dark,
        # high-contrast, and lower-third-heavy.  Use relative contrast/edges.
        contrast_gap = abs(lower_stat.mean[0] - upper_stat.mean[0])
        # Gold's bedroom and town floors have dense dark stripes in the lower
        # half.  Treating those as text boxes makes the bot spam A/B forever.
        # Real dialogue/menu boxes are bright enough in the lower panel to pass
        # this threshold, while striped overworld floors are not.
        textbox_like = (
            bright_lower > 0.18
            and lower_stat.stddev[0] > 18
            and (edge_stat.mean[0] > 12 or contrast_gap > 14)
        )
        # Indoor floors in Gold often fill the lower half with bright/dark
        # horizontal stripes.  They look textbox-like to edge/contrast tests,
        # but they have very low palette entropy and a large dark floor share.
        striped_floor_like = (
            palette_levels <= 6
            and entropy < 2.35
            and lower_stat.stddev[0] > 35
            and edge_stat.mean[0] > 45
            and contrast_gap > 25
        )
        bright_dialogue_panel = bright_lower > 0.55 and contrast_gap > 45 and edge_stat.mean[0] > 35
        if striped_floor_like and not bright_dialogue_panel:
            textbox_like = False
        menu_like = textbox_like and lower_stat.stddev[0] > 28 and palette_levels <= 8
        black_transition = stat.mean[0] < 8 or non_black < 0.06
        low_information = stat.stddev[0] < 8 or (entropy < 2.0 and bright_lower > 0.25)

        h = self.ahash(im)
        dh = self.dhash(im)
        sprite = self.player_sprite_features(im)
        if black_transition:
            screen_class = "transition"
        elif textbox_like:
            screen_class = "menu_or_text" if menu_like else "dialogue"
        elif low_information:
            screen_class = "title_or_static"
        else:
            screen_class = "overworld_or_battle"

        return {
            "hash": h,
            "dhash": dh,
            "mean": round(stat.mean[0], 3),
            "stddev": round(stat.stddev[0], 3),
            "entropy": round(entropy, 3),
            "bbox": bbox,
            "dark_lower": round(dark_lower, 3),
            "bright_lower": round(bright_lower, 3),
            "lower_edge_mean": round(edge_stat.mean[0], 3),
            "lower_contrast_gap": round(contrast_gap, 3),
            "palette_levels": palette_levels,
            "textbox_like": textbox_like,
            "striped_floor_like": striped_floor_like,
            "bright_dialogue_panel": bright_dialogue_panel,
            "screen_class": screen_class,
            **sprite,
        }

    @staticmethod
    def _entropy(hist: list[int], total: int) -> float:
        ent = 0.0
        for count in hist:
            if count:
                p = count / total
                ent -= p * math.log2(p)
        return ent

    def observe(self) -> Observation:
        im = self.screenshot()
        state = self.get_state()
        return Observation(visual=self.features(im), state=state)

    def update_phase(self, obs: Observation) -> None:
        old_phase = self.phase
        control = self.read_control()
        guidance = str(control.get("guidance_prompt") or "").lower()
        forced_explore = any(word in guidance for word in ("not in dialogue", "not dialogue", "outside", "overworld", "walk", "move"))
        forced_explore = forced_explore or any(phrase in guidance for phrase in ("find the exit", "find exit", "look for exit", "search for exit"))
        true_textbox = self.visual_textbox_active(obs)
        visually_overworld = bool(obs.visual.get("player_center")) and obs.screen_class == "overworld_or_battle"
        battle = obs.state.get("battle") or {}
        stale_battle_dialog = (
            obs.in_battle is not True
            and obs.needs_healing
            and bool(battle.get("wild_species_id") or battle.get("enemy_level"))
            and (self.repeated_hash_count >= 3 or self.repeated_position_count >= 2)
        )
        stale_route30_menu = (
            obs.map_group_number == (26, 1)
            and obs.coord is not None
            and obs.in_battle is not True
            and not obs.visual.get("bright_dialogue_panel")
        )
        structured_dialog = self.structured_dialog_active(obs)
        if obs.in_battle is True and not visually_overworld:
            self.phase = "battle"
        elif stale_route30_menu:
            self.phase = "overworld"
        elif obs.coord is not None and not structured_dialog and not obs.visual.get("bright_dialogue_panel"):
            self.phase = "overworld"
        elif obs.coord is not None and not structured_dialog and not true_textbox:
            self.phase = "overworld"
        elif structured_dialog or true_textbox or (
            obs.screen_class in {"dialogue", "menu_or_text"}
            and not stale_battle_dialog
            and self.repeated_hash_count < 10
            and not (forced_explore and self.repeated_hash_count >= 2)
        ):
            # Dialog is a first-class phase; most real story progress is gated by
            # repeatedly advancing text and selecting defaults.
            self.phase = "dialogue"
        elif obs.coord is not None:
            self.phase = "overworld"
        elif obs.screen_class == "transition":
            self.phase = "transition"
        elif self.turn < 80 or (old_phase in {"boot", "title", "intro"} and obs.screen_class in {"title_or_static", "transition"}):
            self.phase = "intro"
        elif obs.screen_class == "title_or_static" and self.repeated_hash_count >= 3:
            self.phase = "title"
        else:
            self.phase = "visual_explore"
        if self.phase != old_phase:
            self.log({"event": "phase_changed", "from": old_phase, "to": self.phase, "screen_class": obs.screen_class})

    def choose_macro(self, obs: Observation) -> tuple[str, list[str]]:
        self.visits[obs.visual_hash] = self.visits.get(obs.visual_hash, 0) + 1
        self.update_place_memory(obs)
        self.update_phase(obs)
        if obs.lead_hp == 0 and obs.screen_class != "overworld_or_battle":
            return "faint_clear_transition", ["press_a", "wait_60", "press_a", "wait_60"]
        if self.phase == "battle":
            return self._battle_macro(obs)
        storage_macro = self._storage_macro(obs)
        if storage_macro is not None:
            return storage_macro
        mart_macro = self._mart_macro(obs)
        if mart_macro is not None:
            return mart_macro
        naming_macro = self._naming_macro(obs)
        if naming_macro is not None:
            return naming_macro
        healing_macro = self._healing_macro(obs)
        if healing_macro is not None:
            return healing_macro
        structured_macro = self._structured_goal_macro(obs)
        if structured_macro is not None:
            return structured_macro
        if self.phase == "dialogue":
            return self._dialogue_macro(obs)
        self_recovery = self._self_recovery_macro(obs)
        if self_recovery is not None:
            return self_recovery
        main_route_macro = self._main_route_macro(obs)
        if main_route_macro is not None:
            return main_route_macro
        starter_macro = self._starter_lab_macro(obs)
        if starter_macro is not None:
            return starter_macro
        guidance_macro = self._guidance_macro(obs)
        if guidance_macro is not None:
            return guidance_macro

        # Deterministic curriculum for the boot/title/new-game/professor intro.
        if self.phase in {"boot", "title", "intro"}:
            return self._intro_macro(obs)
        if self.phase == "transition":
            return "wait_transition", ["wait_60", "wait_60"]
        if self.phase in {"visual_explore", "overworld"} or (self.phase == "dialogue" and self.repeated_hash_count >= 2):
            learned_macro = self._learned_place_macro(obs)
            if learned_macro is not None:
                return learned_macro
        if self.phase == "dialogue":
            return self._dialogue_macro(obs)
        if obs.coord is not None:
            return self._coordinate_macro(obs)
        return self._visual_macro(obs)

    def _structured_goal_macro(self, obs: Observation) -> tuple[str, list[str]] | None:
        if obs.map_group_number == (24, 5) and obs.has_starter and obs.screen_class in {"dialogue", "menu_or_text"}:
            return None
        if obs.map_group_number == (24, 5) and not obs.has_starter and self.phase == "dialogue":
            return None
        if self.visual_textbox_active(obs) and obs.map_group_number not in {(24, 7), (24, 4)}:
            return None
        if obs.map_group_number == (24, 5) and obs.has_starter:
            # After receiving the starter, leave Elm's Lab through the lower
            # doorway instead of continuing to inspect the remaining balls.
            # Exit warps are actual (4, 11) and (5, 11); stand just above one.
            actual = self.actual_xy(obs)
            if actual is not None:
                x, y = actual
                if y < 6:
                    return "structured_lab_exit_back_away", ["walk_down", "wait_30"]
                if x > 5:
                    return "structured_lab_exit_align_left", ["walk_left", "wait_30"]
                if x < 5:
                    return "structured_lab_exit_align_right", ["walk_right", "wait_30"]
                if y < 10:
                    return "structured_lab_exit_down", ["walk_down", "wait_30"]
                if y > 10:
                    return "structured_lab_exit_up", ["walk_up", "wait_30"]
                return "structured_lab_leave", ["walk_down", "wait_80"]
            return "structured_lab_exit_wait_for_coords", ["wait_60"]
        if obs.map_group_number == (24, 5) and not obs.has_starter:
            # Cyndaquil's ball is actual (6, 3); stand below it at actual
            # (6, 4), face up, and interact.
            actual = self.actual_xy(obs)
            if actual == (6, 4):
                return "structured_lab_pick_starter", ["press_a", "wait_60"]
            if actual is not None:
                x, y = actual
                if y < 4:
                    if x > 4:
                        return "structured_lab_clear_elm_left", ["walk_left", "wait_30"]
                    return "structured_lab_cyndaquil_row_down", ["walk_down", "wait_30"]
                if x < 6:
                    return "structured_lab_cyndaquil_right", ["walk_right", "wait_30"]
                if x > 6:
                    return "structured_lab_cyndaquil_left", ["walk_left", "wait_30"]
                if y > 4:
                    return "structured_lab_cyndaquil_up", ["walk_up", "wait_30"]
            return "structured_lab_to_cyndaquil_wait_for_coords", ["wait_60"]
        if obs.map_group_number == (24, 6):
            # Player's House 1F. Exit from the center/right side of the mat;
            # the left edge can leave the player facing the wall below it.
            pos = (obs.state.get("player") or {}).get("position") or {}
            x, y = pos.get("x"), pos.get("y")
            if isinstance(x, int) and isinstance(y, int):
                if x < 7:
                    return "structured_house_exit_bottom_row", ["walk_down", "wait_30"]
                if y > 6:
                    return "structured_house_exit_left_to_mat", ["walk_left", "wait_30"]
                if y < 6:
                    return "structured_house_exit_right_to_mat", ["walk_right", "wait_30"]
                return "structured_house_exit_down", ["walk_down", "wait_60"]
        if obs.map_group_number == (24, 7):
            # Player's House 2F. The TV/bookcase can trap screenshot logic in
            # repeated A presses. The stair warp is at pokecrystal actual
            # coordinate (7, 0). Gold RAM exposes this runtime's raw axes swapped,
            # so route with actual_xy and align x before walking up to avoid the
            # blocked PC/radio row.
            if obs.visual.get("bright_dialogue_panel"):
                return None
            actual = self.actual_xy(obs)
            if actual is not None:
                x, y = actual
                if x < 7:
                    return "structured_house2f_to_stairs_right", ["walk_right", "wait_30"]
                if x > 7:
                    return "structured_house2f_to_stairs_left", ["walk_left", "wait_30"]
                return "structured_house2f_enter_stairs_up", ["walk_up", "wait_80"]
            return "structured_house2f_wait_for_coords", ["wait_60"]
        if obs.map_group_number == (24, 4) and obs.has_starter:
            # With a starter, leave New Bark Town to the west toward Route 29.
            actual = self.actual_xy(obs)
            if actual is not None:
                x, y = actual
                if y < 8:
                    if x > 4:
                        return "structured_town_route29_avoid_npc_left", ["walk_left", "wait_30"]
                    return "structured_town_route29_row_down", ["walk_down", "wait_30"]
                if y > 9:
                    return "structured_town_route29_row_up", ["walk_up", "wait_30"]
                if x > 1:
                    if self.repeated_position_count >= 3 or self._coord_looping():
                        recovery = self._route_recovery_macro(obs, "left")
                        if recovery:
                            return recovery
                    return "structured_town_route29_west", ["walk_left", "wait_30"]
                return "structured_town_enter_route29", ["walk_left", "wait_80"]
            return "structured_town_route29_wait_for_coords", ["wait_60"]
        if obs.map_group_number == (24, 4) and not obs.has_starter:
            # New Bark Town. Elm's Lab door is pokecrystal coord (6, 3). Use
            # imported V2 collision because a greedy route from the southeast
            # gets trapped against houses/fences.
            actual = self.actual_xy(obs)
            if actual is not None:
                map_spec = GOLD_MAP_REGISTRY.get((24, 4))
                if map_spec is not None:
                    plan = plan_to_any(map_spec, actual, {(6, 3)})
                    if plan is not None and plan.next_step is not None:
                        wait = "wait_80" if plan.planned_path_length == 1 else "wait_30"
                        return "structured_town_to_lab_v2_map", [plan.next_step, wait]
            return self.step_toward_actual(obs, 6, 4, "structured_town_to_lab_door")
        return None

    def _naming_macro(self, obs: Observation) -> tuple[str, list[str]] | None:
        if obs.screen_class != "menu_or_text" or self.visual_textbox_active(obs):
            return None
        if obs.map_group_number == (24, 5) and obs.has_starter:
            key = f"pokemon:{self.coord_key(obs.coord)}"
            if self.naming_sessions.get(key, 0) >= 1:
                return "name_pokemon_confirm", ["press_a", "wait_60"]
            self.naming_sessions[key] = self.naming_sessions.get(key, 0) + 1
            self.log_event("naming_started", {"kind": "pokemon", "word": "EMBER", "snapshot": self.event_snapshot(obs)})
            return "name_pokemon_ember", NAMING_MACROS["EMBER"]
        if not obs.has_starter and obs.coord is None and self.phase in {"intro", "title", "visual_explore", "dialogue"}:
            key = "player:intro"
            if self.naming_sessions.get(key, 0) >= 1:
                return None
            self.naming_sessions[key] = 1
            self.log_event("naming_started", {"kind": "player", "word": "NOVA", "snapshot": self.event_snapshot(obs)})
            return "name_player_nova", NAMING_MACROS["NOVA"]
        return None

    def _main_route_macro(self, obs: Observation) -> tuple[str, list[str]] | None:
        if not obs.has_starter or obs.in_battle or self.visual_textbox_active(obs):
            return None
        actual = self.actual_xy(obs)
        if actual is None:
            return None
        x, y = actual

        if obs.map_group_number == (24, 3):
            # Route 29 has several ledges and tree pockets. Follow a path
            # generated from the map's collision data instead of greedily walking
            # west. West connection tiles are at actual y 6/7.
            if x <= 1 and y in {6, 7}:
                return "route29_enter_cherrygrove", ["walk_left", "wait_80"]
            if x < 27 and y >= 10:
                return self.step_toward_actual(obs, 27, 14, "route29_recover_to_mid_detour")
            if x == 27 and 10 <= y < 14:
                return "route29_recover_drop_to_detour", ["walk_down", "wait_30"]
            if 27 <= x < 31 and y >= 14:
                return self.step_toward_actual(obs, 31, 14, "route29_recover_to_mid_detour")
            if x == 31 and y > 11:
                return self.step_toward_actual(obs, 31, 11, "route29_recover_to_mid_detour")
            if 16 < x <= 21 and y == 4:
                return "route29_west_midlane", ["walk_left", "wait_30"]
            if x == 16 and y < 6:
                return "route29_midlane_drop", ["walk_down", "wait_30"]
            if 11 < x <= 16 and y == 6:
                return "route29_continue_ledge_line_west", ["walk_left", "wait_30"]
            if 0 < x <= 11 and y == 7:
                return "route29_west_edge_line", ["walk_left", "wait_30"]
            route29_macro = self.follow_actual_waypoints(obs, ROUTE29_TO_CHERRYGROVE, "route29_collision_path")
            if route29_macro is not None:
                return route29_macro
            return "route29_wait_for_coords", ["wait_60"]

        if obs.map_group_number == (26, 3):
            # Cherrygrove: head north to Route 30 when healthy. The center route
            # is handled by _healing_macro when HP is low.
            if y >= 10 and x > 16:
                return "cherrygrove_align_route30_lane_west", ["walk_left", "wait_30"]
            if 20 <= x <= 24 and 7 <= y <= 9:
                return "cherrygrove_clear_mart_sign_down", ["walk_down", "wait_30"]
            if 20 <= x <= 24 and y >= 7:
                return "cherrygrove_clear_mart_npc_up", ["walk_up", "wait_30"]
            if 20 <= x <= 24 and y == 6:
                return "cherrygrove_clear_mart_sign_up", ["walk_up", "wait_30"]
            if 20 <= x <= 24 and y <= 10:
                return "cherrygrove_clear_mart_door_west", ["walk_left", "wait_30"]
            if 27 <= x <= 31 and y <= 5:
                return "cherrygrove_clear_pokecenter_door_west", ["walk_left", "wait_30"]
            if 25 < x <= 31 and y < 8:
                return "cherrygrove_clear_pokecenter_lane_west", ["walk_left", "wait_30"]
            if y <= 1:
                return "cherrygrove_enter_route30", ["walk_up", "wait_80"]
            return self.step_toward_actual_axis(obs, 16, 1, "cherrygrove_to_route30", axis="x")

        if obs.map_group_number == (26, 5):
            # Leave Cherrygrove center after healing/visiting, then continue the
            # main route north from the city.
            if actual == (4, 7):
                return "cherrygrove_center_exit", ["walk_down", "wait_80"]
            if y < 7:
                return "cherrygrove_center_to_exit_down", ["walk_down", "wait_30"]
            if x < 4:
                return "cherrygrove_center_to_exit_right", ["walk_right", "wait_30"]
            if x > 4:
                return "cherrygrove_center_to_exit_left", ["walk_left", "wait_30"]
            return "cherrygrove_center_to_exit_up", ["walk_up", "wait_30"]

        if obs.map_group_number == (26, 4):
            # Cherrygrove Mart. If route clearing enters it, leave immediately.
            if actual in {(2, 7), (3, 7)}:
                return "cherrygrove_mart_exit", ["walk_down", "wait_80"]
            if x > 3:
                return "cherrygrove_mart_to_exit_left", ["walk_left", "wait_30"]
            if x < 2:
                return "cherrygrove_mart_to_exit_right", ["walk_right", "wait_30"]
            if y < 7:
                return "cherrygrove_mart_to_exit_down", ["walk_down", "wait_30"]
            return "cherrygrove_mart_to_exit_up", ["walk_up", "wait_30"]

        if obs.map_group_number == (26, 1):
            route30_macro = self.route30_grid_macro(obs, ROUTE30_ROUTE31_TARGETS, "route30_to_route31")
            if route30_macro is not None:
                return route30_macro
            return "route30_grid_fallback_north", ["walk_up", "wait_30"]

        if obs.map_group_number == (26, 2):
            # Route 31: go west through the gate to Violet, avoiding Dark Cave.
            if x <= 4 and y in {6, 7}:
                return "route31_enter_violet_gate", ["walk_left", "wait_80"]
            if y < 6:
                return "route31_gate_row_down", ["walk_down", "wait_30"]
            if y > 7:
                return "route31_gate_row_up", ["walk_up", "wait_30"]
            return "route31_west_to_gate", ["walk_left", "wait_30"]

        if obs.map_group_number == (26, 11):
            # Route 31/Violet gate: cross west into Violet City.
            if x <= 1:
                return "violet_gate_enter_city", ["walk_left", "wait_80"]
            if y < 4:
                return "violet_gate_align_down", ["walk_down", "wait_30"]
            if y > 5:
                return "violet_gate_align_up", ["walk_up", "wait_30"]
            return "violet_gate_west", ["walk_left", "wait_30"]

        if obs.map_group_number == (10, 5):
            # Violet City: enter the gym when healthy. The center route is used
            # by healing logic when the lead is hurt.
            if actual == (18, 18):
                return "violet_enter_gym", ["walk_up", "wait_80"]
            return self.step_toward_actual(obs, 18, 18, "violet_to_gym")

        if obs.map_group_number == (10, 10):
            if actual == (4, 7):
                return "violet_center_exit", ["walk_down", "wait_80"]
            if y < 7:
                return "violet_center_to_exit_down", ["walk_down", "wait_30"]
            if x < 4:
                return "violet_center_to_exit_right", ["walk_right", "wait_30"]
            if x > 4:
                return "violet_center_to_exit_left", ["walk_left", "wait_30"]
            return "violet_center_to_exit_up", ["walk_up", "wait_30"]

        if obs.map_group_number == (10, 7):
            # Violet Gym: move to Falkner's interaction tile and press A.
            if actual == (5, 2):
                return "violet_gym_talk_falkner", ["press_a", "wait_80"]
            return self.step_toward_actual(obs, 5, 2, "violet_gym_to_falkner")

        if obs.map_group_number == (26, 9):
            # Route 30 story house. If generic exploration enters it after the
            # interaction sequence, leave through the south door and continue.
            if actual == (2, 7):
                return "route30_house_exit", ["walk_down", "wait_80"]
            if y < 7:
                return self.step_toward_actual_axis(obs, 2, 7, "route30_house_to_exit", axis="y")
            return self.step_toward_actual_axis(obs, 2, 7, "route30_house_to_exit", axis="x")

        return None

    def _self_recovery_key(self, obs: Observation) -> str:
        map_key = "none"
        if obs.map_group_number is not None:
            map_key = f"{obs.map_group_number[0]}:{obs.map_group_number[1]}"
        dialog = obs.state.get("dialog") or {}
        window = "window" if bool(dialog.get("active")) or int(dialog.get("window_stack_size") or 0) > 0 else "no_window"
        return f"{map_key}|{self.coord_key(obs.coord)}|{self.phase}|{obs.screen_class}|{window}|heal={obs.needs_healing}"

    def _self_recovery_macro(self, obs: Observation) -> tuple[str, list[str]] | None:
        stuck = self.repeated_position_count >= 18 or self.repeated_hash_count >= 18 or self._coord_oscillating()
        if not stuck or self.turn - self.last_self_recovery_turn < 2:
            return None
        key = self._self_recovery_key(obs)
        attempt = int(self.self_recovery_attempts.get(key, 0))
        self.self_recovery_attempts[key] = attempt + 1
        self.last_self_recovery_turn = self.turn

        dialog = obs.state.get("dialog") or {}
        raw_window = bool(dialog.get("active")) or int(dialog.get("window_stack_size") or 0) > 0
        if obs.screen_class == "overworld_or_battle" and obs.in_battle is not True:
            raw_window = False
        stale_route30_menu = (
            obs.map_group_number == (26, 1)
            and obs.coord is not None
            and obs.in_battle is not True
            and not obs.visual.get("bright_dialogue_panel")
        )
        if stale_route30_menu:
            raw_window = False
        coord_key = self.coord_key(obs.coord)
        blocked = self.coord_blocked_directions(obs)

        if raw_window or (obs.screen_class in {"dialogue", "menu_or_text"} and not stale_route30_menu):
            options: list[tuple[str, list[str]]] = [
                ("self_recover_menu_back", ["press_b", "wait_60"]),
                ("self_recover_menu_confirm", ["press_a", "wait_80"]),
                ("self_recover_menu_down_confirm", ["walk_down", "wait_30", "press_a", "wait_80"]),
                ("self_recover_menu_up_back", ["walk_up", "wait_30", "press_b", "wait_60"]),
                ("self_recover_menu_start_back", ["press_start", "wait_30", "press_b", "wait_60"]),
            ]
        else:
            directions = [d for d in ("up", "right", "down", "left") if d not in blocked]
            if not directions:
                directions = ["up", "right", "down", "left"]
            last_direction = self.action_direction(self.recent_actions[-1]) if self.recent_actions else None
            opposite = {"up": "down", "down": "up", "left": "right", "right": "left"}
            if last_direction and len(directions) > 1:
                directions = [d for d in directions if d != opposite.get(last_direction)] or directions
            direction = directions[attempt % len(directions)]
            options = [(f"self_recover_overworld_{direction}", [self.direction_action(direction), "wait_80"])]

        macro, actions = options[attempt % len(options)]
        self.log_event("self_recovery_selected", {
            "snapshot": self.event_snapshot(obs),
            "key": key,
            "attempt": attempt + 1,
            "macro": macro,
            "actions": actions,
            "coord": coord_key,
            "blocked_directions": sorted(blocked),
            "recent_coords": list(self.recent_coords)[-8:],
            "recent_actions": list(self.recent_actions)[-8:],
        })
        return macro, actions

    def _storage_macro(self, obs: Observation) -> tuple[str, list[str]] | None:
        if obs.map_group_number != (20, 1):
            return None
        actual = self.actual_xy(obs)
        # Map 20:1 is a Pokecenter link/storage floor in this runtime. Storage
        # work is risky without explicit intent, so the safe default is to back
        # out of PC menus and leave rather than depositing/releasing anything.
        if actual is not None:
            x, y = actual
            if y >= 6:
                if x > 0:
                    return "storage_floor_to_stairs_left", ["walk_left", "wait_80"]
                return "storage_floor_take_stairs", ["walk_up", "wait_80"]
        dialog = obs.state.get("dialog") or {}
        raw_window = bool(dialog.get("active")) or int(dialog.get("window_stack_size") or 0) > 0
        if obs.screen_class in {"dialogue", "menu_or_text"} or raw_window:
            if self.repeated_position_count >= 8:
                step = (self.repeated_position_count - 8) % 5
                if step < 4:
                    return "storage_escape_to_see_ya_down", ["walk_down", "wait_80"]
                return "storage_escape_select_see_ya", ["press_a", "wait_120"]
            return "storage_back_out_menu", ["press_b", "wait_60"]
        if actual is None:
            return "storage_wait_for_coords", ["wait_60"]
        if self.repeated_position_count >= 4:
            options = ["walk_left", "walk_up", "walk_right", "walk_down"]
            action = options[((self.repeated_position_count - 4) // 4) % len(options)]
            return "storage_floor_unstick_" + action.removeprefix("walk_"), [action, "wait_80"]
        if y >= 6:
            if x > 0:
                return "storage_floor_to_stairs_left", ["walk_left", "wait_80"]
            return "storage_floor_take_stairs", ["walk_up", "wait_80"]
        return "storage_floor_to_exit_down", ["walk_down", "wait_30"]

    def _mart_macro(self, obs: Observation) -> tuple[str, list[str]] | None:
        if obs.map_group_number not in {(26, 4), (10, 6)}:
            return None
        actual = self.actual_xy(obs)
        balls = obs.bag_item_count(2, 3, 4)
        should_buy = balls < 3 and obs.money >= 200
        if obs.screen_class in {"dialogue", "menu_or_text"} or self.structured_dialog_active(obs):
            if not should_buy or self.repeated_position_count >= 20:
                return "mart_back_out_menu", ["press_b", "wait_30", "press_b", "wait_60"]
            return "mart_buy_one_default_item", ["press_a", "wait_40", "press_a", "wait_40", "press_a", "wait_40", "press_a", "wait_80"]
        if actual is None:
            return "mart_wait_for_coords", ["wait_60"]
        x, y = actual
        if should_buy:
            if y > 3:
                return "mart_to_counter_up", ["walk_up", "wait_30"]
            if x < 2:
                return "mart_to_counter_right", ["walk_right", "wait_30"]
            if x > 2:
                return "mart_to_counter_left", ["walk_left", "wait_30"]
            return "mart_talk_clerk", ["press_a", "wait_80"]
        if actual in {(2, 7), (3, 7)}:
            return "mart_exit", ["walk_down", "wait_80"]
        if x > 3:
            return "mart_to_exit_left", ["walk_left", "wait_30"]
        if x < 2:
            return "mart_to_exit_right", ["walk_right", "wait_30"]
        if y < 7:
            return "mart_to_exit_down", ["walk_down", "wait_30"]
        return "mart_to_exit_up", ["walk_up", "wait_30"]

    def _intro_macro(self, obs: Observation) -> tuple[str, list[str]]:
        # The first several dozen turns after a fresh Gold boot are dominated by
        # title/menu/professor/name prompts.  The safest productive behavior is
        # Start/A confirmation, with B only as a rare escape.  This gets to the
        # controllable bedroom much more reliably than random walking.
        cycle = self.turn % 9
        if obs.screen_class in {"dialogue", "menu_or_text"}:
            return "intro_advance_text", MACROS["advance_text"]
        if self.repeated_hash_count >= 5:
            return "intro_start_or_confirm", MACROS["start_or_confirm"]
        if cycle in {0, 4}:
            return "intro_start", ["press_start", "wait_30"]
        return "intro_confirm", MACROS["confirm"]

    def _dialogue_macro(self, obs: Observation) -> tuple[str, list[str]]:
        actual = self.actual_xy(obs)
        if obs.map_group_number == (26, 3) and actual == (30, 4) and self.repeated_position_count >= 4:
            return "dialogue_menu_exit_cherrygrove_center_sign", ["press_b", "wait_30", "walk_left", "wait_80"]
        if obs.map_group_number == (26, 9) and not obs.needs_healing and self.repeated_position_count >= 8:
            if actual is not None:
                x, y = actual
                if x < 2:
                    return "route30_house_stale_dialog_exit_right", ["walk_right", "wait_80"]
                if x > 2:
                    return "route30_house_stale_dialog_exit_left", ["walk_left", "wait_80"]
                if y < 7:
                    return "route30_house_stale_dialog_exit_down", ["walk_down", "wait_80"]
                return "route30_house_stale_dialog_exit", ["walk_down", "wait_80"]
        if obs.map_group_number == (26, 5) and not obs.needs_healing and self.repeated_position_count >= 8:
            if not obs.visual.get("bright_dialogue_panel"):
                return "post_heal_leave_nurse", ["walk_down", "wait_80"]
            return "post_heal_clear_dialog", ["press_b", "wait_30", "press_a", "wait_60"]
        menu_cursor = (obs.state.get("dialog") or {}).get("menu_cursor") or {}
        if self.repeated_position_count >= 20 and int(menu_cursor.get("x") or 0) >= 120:
            menu_attempt = (self.repeated_position_count - 20) // 10 % 4
            if menu_attempt == 0:
                return "dialogue_menu_select_top", ["walk_up", "wait_20", "press_a", "wait_80"]
            if menu_attempt == 1:
                return "dialogue_menu_select_bottom", ["walk_down", "wait_20", "press_a", "wait_80"]
            if menu_attempt == 2:
                return "dialogue_menu_back_confirm", ["press_b", "wait_30", "press_a", "wait_80"]
            return "dialogue_menu_stubborn", ["press_b", "wait_30", "press_a", "wait_30", "press_start", "wait_30", "press_b", "wait_30"]
        battle = obs.state.get("battle") or {}
        lingering_battle = bool(battle.get("wild_species_id") or battle.get("enemy_level"))
        if lingering_battle and obs.needs_healing and self.repeated_hash_count >= 4 and obs.in_battle is True:
            return "battle_run_low_hp_dialog", ["hold_down_20", "wait_20", "hold_right_20", "wait_20", "press_a", "wait_80", "press_b", "wait_30"]
        if self.repeated_hash_count >= 8 or self.repeated_position_count >= 40:
            return "dialogue_confirm_stubborn", ["press_b", "wait_30", "press_a", "wait_30", "press_start", "wait_30", "press_b", "wait_30"]
        if self.repeated_hash_count >= 4 and not self.structured_dialog_active(obs):
            return self._visual_macro(obs)
        # Mostly A/held-B to make text crawl go fast, occasionally default A.
        if self.rng.random() < 0.65:
            return "dialogue_speed_text", MACROS["speed_text"]
        return "dialogue_advance", MACROS["advance_text"]

    def _guidance_macro(self, obs: Observation) -> tuple[str, list[str]] | None:
        guidance = str(self.read_control().get("guidance_prompt") or "").lower()
        if not guidance:
            return None
        if self.visual_textbox_active(obs):
            return None
        directions: list[str] = []
        for word, action in (("west", "walk_left"), ("left", "walk_left"), ("north", "walk_up"), ("up", "walk_up"), ("east", "walk_right"), ("right", "walk_right"), ("south", "walk_down"), ("down", "walk_down")):
            if word in guidance and action not in directions:
                directions.append(action)

        negates_leave = any(phrase in guidance for phrase in ("do not leave", "don't leave", "dont leave", "not leave", "stay in"))
        wants_to_leave = (not negates_leave) and any(phrase in guidance for phrase in ("get out", "leave", "exit", "stop talking", "stop interacting"))
        wants_exit_search = any(phrase in guidance for phrase in ("find the exit", "find exit", "look for exit", "search for exit"))
        forbids_interact = any(phrase in guidance for phrase in ("stop talking", "stop interacting", "do not talk", "don't talk", "dont talk", "no talking", "avoid npc", "ignore npc"))
        if forbids_interact and obs.screen_class in {"dialogue", "menu_or_text"}:
            place = self.places.get(self.place_key(obs)) or {}
            recent_b = sum(1 for action in self.recent_actions if action == "press_b")
            if recent_b < 6:
                return "guidance_clear_current_text", ["press_b", "wait_20", "press_b", "wait_20"]
        if wants_exit_search:
            if self.visual_textbox_active(obs):
                return None
            room_exit = self._beginning_room_exit_macro(obs)
            if room_exit is not None:
                return room_exit
            return self._exit_search_macro(obs)
        if wants_to_leave and not directions:
            if self.read_control().get("movement_bias") == "north_east":
                directions.extend(["walk_up", "walk_right"])
            else:
                directions.extend(["walk_left", "walk_up", "walk_down"])
        if not forbids_interact and ("press a" in guidance or "talk" in guidance or "interact" in guidance):
            directions.append("press_a")
        if not directions:
            return None
        directions = self._filter_blocked_guidance_directions(obs, directions)
        if not directions:
            return self._exit_search_macro(obs)
        action = directions[self.turn % len(directions)]
        if self._would_oscillate(action) and len(directions) > 1:
            action = directions[(self.turn + 1) % len(directions)]
        return "guidance_" + action, [action, "wait_20" if action.startswith("walk_") else "wait_30"]

    def _beginning_room_exit_macro(self, obs: Observation) -> tuple[str, list[str]] | None:
        player = obs.visual.get("player_center")
        mat = obs.visual.get("red_mat_center")
        if not (isinstance(player, list) and isinstance(mat, list) and len(player) == 2 and len(mat) == 2):
            return None
        px, py = float(player[0]), float(player[1])
        mx, my = float(mat[0]), float(mat[1])
        bbox = obs.visual.get("red_mat_bbox")
        # In the starting bedroom the red mat marks the exit/threshold.  Use it
        # as a visible waypoint instead of wandering by whole-screen hashes.
        if isinstance(bbox, list) and len(bbox) == 4:
            x0, y0, x1, y1 = [float(v) for v in bbox]
            if x0 - 12 <= px <= x1 + 12:
                if self._locally_blocked("walk_down"):
                    return None
                if py < y0 - 2:
                    return "room_exit_step_down_to_mat", ["walk_down", "wait_30"]
                return "room_exit_leave_down", ["walk_down", "wait_50"]
        if px > mx + 8:
            if self._locally_blocked("walk_left"):
                return "room_exit_detour_down", ["walk_down", "wait_30"]
            return "room_exit_align_left", ["walk_left", "wait_30"]
        if px < mx - 8:
            if self._locally_blocked("walk_right"):
                return "room_exit_detour_down", ["walk_down", "wait_30"]
            return "room_exit_align_right", ["walk_right", "wait_30"]
        if py < my - 4:
            if self._locally_blocked("walk_down"):
                return "room_exit_detour_side", ["walk_right", "wait_30"]
            return "room_exit_step_down_to_mat", ["walk_down", "wait_30"]
        return "room_exit_leave_down", ["walk_down", "wait_40"]

    def _starter_lab_macro(self, obs: Observation) -> tuple[str, list[str]] | None:
        if self.visual_textbox_active(obs):
            return None
        control = self.read_control()
        text = (str(control.get("objective") or "") + " " + str(control.get("guidance_prompt") or "")).lower()
        if not any(word in text for word in ("starter", "pokemon", "pokémon", "elm")):
            return None
        player = obs.visual.get("player_center")
        comps = obs.visual.get("red_components") or []
        if not (isinstance(player, list) and len(player) == 2 and isinstance(comps, list)):
            return None
        balls = []
        for comp in comps:
            bbox = comp.get("bbox") if isinstance(comp, dict) else None
            center = comp.get("center") if isinstance(comp, dict) else None
            pixels = int(comp.get("pixels", 0)) if isinstance(comp, dict) else 0
            if not (isinstance(bbox, list) and isinstance(center, list) and len(center) == 2):
                continue
            if abs(float(center[0]) - float(player[0])) < 16 and abs(float(center[1]) - float(player[1])) < 24:
                continue
            x0, y0, x1, y1 = bbox
            w = x1 - x0 + 1
            h = y1 - y0 + 1
            if 7 <= w <= 14 and 4 <= h <= 10 and 25 <= pixels <= 70 and abs(float(center[1]) - float(player[1])) > 8:
                balls.append(center)
        if len(balls) < 2:
            return None
        # Pick the left-most visible starter first; once a dialog/menu opens the
        # dialogue handler will confirm through it.
        target = sorted(balls, key=lambda c: c[0])[0]
        px, py = float(player[0]), float(player[1])
        tx, ty = float(target[0]), float(target[1])
        if py > ty + 22:
            if self._locally_blocked("walk_up") or self.repeated_hash_count >= 6:
                return "starter_lab_detour_from_upper_block", ["walk_right", "wait_30"]
            return "starter_lab_approach_up", ["walk_up", "wait_30"]
        if py < ty - 18:
            if self._locally_blocked("walk_down") or self.repeated_hash_count >= 6:
                return "starter_lab_detour_from_lower_block", ["walk_left", "wait_30"]
            return "starter_lab_approach_down", ["walk_down", "wait_30"]
        if px < tx - 7:
            if self._locally_blocked("walk_right") or self.repeated_hash_count >= 6:
                return "starter_lab_align_right_blocked_detour", ["walk_down", "wait_30"]
            return "starter_lab_align_right", ["walk_right", "wait_30"]
        if px > tx + 7:
            if self._locally_blocked("walk_left") or self.repeated_hash_count >= 6:
                return "starter_lab_align_left_blocked_detour", ["walk_down", "wait_30"]
            return "starter_lab_align_left", ["walk_left", "wait_30"]
        vertical_gap = abs(py - ty)
        if 10 <= vertical_gap <= 22:
            return "starter_lab_interact", ["press_a", "wait_30"]
        # Close but not adjacent; reposition instead of reading nearby shelves.
        return "starter_lab_reposition", ["walk_down" if py < ty else "walk_up", "wait_30"]

    def _locally_blocked(self, action: str) -> bool:
        stat = self.current_local_cell().get("actions", {}).get(action) or {}
        return int(stat.get("tries", 0)) >= 3 and int(stat.get("open", 0)) == 0 and bool(stat.get("blocked"))

    def visual_textbox_active(self, obs: Observation) -> bool:
        if self.structured_dialog_active(obs):
            return True
        if obs.screen_class not in {"dialogue", "menu_or_text"}:
            return False
        if obs.visual.get("dark_lower", 0) <= 0.12 or obs.visual.get("bright_lower", 0) <= 0.5:
            return False
        if obs.visual.get("bright_dialogue_panel"):
            return True
        return obs.visual.get("dark_lower", 0) > 0.14 and obs.visual.get("lower_contrast_gap", 0) > 70

    def _filter_blocked_guidance_directions(self, obs: Observation, directions: list[str]) -> list[str]:
        place = self.places.get(self.place_key(obs)) or {}
        actions = place.get("actions") or {}
        kept: list[str] = []
        for action in directions:
            stat = actions.get(action) or {}
            tries = int(stat.get("tries", 0))
            changed = int(stat.get("changed", 0))
            moved = int(stat.get("moved", 0))
            reward = float(stat.get("reward_total", 0.0))
            avg_reward = reward / max(tries, 1)
            blocked = action.startswith("walk_") and tries >= 4 and moved == 0 and (changed == 0 or avg_reward < -0.05)
            if not blocked:
                kept.append(action)
        return kept

    def _exit_search_macro(self, obs: Observation) -> tuple[str, list[str]]:
        oscillation_escape = self._oscillation_escape_macro()
        if oscillation_escape is not None:
            return oscillation_escape
        place = self.places.get(self.place_key(obs)) or {}
        actions = place.get("actions") or {}
        cell = self.current_local_cell()
        cell_actions = cell.setdefault("actions", {})
        candidates = []
        for action in MOVE_ACTIONS:
            cell_stat = cell_actions.get(action) or {}
            if cell_stat.get("blocked") and int(cell_stat.get("tries", 0)) >= 2:
                continue
            stat = actions.get(action) or {}
            tries = int(stat.get("tries", 0))
            changed = int(stat.get("changed", 0))
            moved = int(stat.get("moved", 0))
            reward = float(stat.get("reward_total", 0.0))
            avg_reward = reward / max(tries, 1)
            if tries >= 4 and moved == 0 and (changed == 0 or avg_reward < -0.05):
                continue
            local_tries = int(cell_stat.get("tries", 0))
            dx, dy = MOVE_DELTAS[action]
            nx = int(self.local_position.get("x", 0)) + dx
            ny = int(self.local_position.get("y", 0)) + dy
            next_visits = int(self.local_cells.get(self.local_key(nx, ny), {}).get("visits", 0))
            score = 3.5 / (1.0 + local_tries) + 2.0 / (1.0 + next_visits) + 1.0 / (1.0 + tries)
            if cell_stat.get("open"):
                score += 3.0
            elif moved:
                score += 2.0
            elif changed and avg_reward > 0.0:
                score += 0.5
            if self._would_oscillate(action):
                score -= 0.4
            recent_same = sum(1 for recent in self.recent_actions if recent == action)
            if recent_same >= 3:
                score -= 1.2
            if self._is_recent_backtrack(action):
                score -= 2.0
            if self._local_position_looping():
                score -= 1.5 * next_visits
            candidates.append((score, action))
        if not candidates:
            # If every direction is currently marked blocked, retest one
            # direction at a time.  Multi-step macros make odometry ambiguous.
            action = MOVE_ACTIONS[self.turn % len(MOVE_ACTIONS)]
            return "guidance_corner_probe_" + action, [action, "wait_30"]
        candidates.sort(reverse=True)
        action = candidates[0][1]
        return "guidance_exit_search_" + action, [action, "wait_20"]

    def _oscillation_escape_macro(self) -> tuple[str, list[str]] | None:
        moves = [a for a in self.recent_actions if a in MOVE_DELTAS]
        if len(moves) < 6:
            return None
        recent = moves[-6:]
        horizontal = {"walk_left", "walk_right"}
        vertical = {"walk_up", "walk_down"}
        if set(recent).issubset(horizontal) and len(set(recent)) == 2:
            # We are bouncing along a row.  Probe vertically with a longer hold
            # so doors/stairs have time to trigger, even if short taps failed.
            return "guidance_break_horizontal_oscillation", ["walk_down", "wait_40", "walk_down", "wait_40", "walk_up", "wait_20"]
        if set(recent).issubset(vertical) and len(set(recent)) == 2:
            return "guidance_break_vertical_oscillation", ["walk_left", "wait_40", "walk_left", "wait_40", "walk_right", "wait_20"]
        return None

    def _local_position_looping(self) -> bool:
        if len(self.recent_local_positions) < 8:
            return False
        recent = list(self.recent_local_positions)[-8:]
        return len(set(recent)) <= 3

    def _is_recent_backtrack(self, action: str) -> bool:
        opposite = {
            "walk_up": "walk_down",
            "walk_down": "walk_up",
            "walk_left": "walk_right",
            "walk_right": "walk_left",
        }
        moves = [a for a in self.recent_actions if a in MOVE_DELTAS]
        return bool(moves and moves[-1] == opposite.get(action))

    def _battle_macro(self, obs: Observation) -> tuple[str, list[str]]:
        battle = obs.state.get("battle") or {}
        ball_count = obs.bag_item_count(2, 3, 4)
        potion_count = obs.bag_item_count(18)
        party_count = len(obs.state.get("party") or [])
        battle_type = int(battle.get("type_id") or 0)
        is_wild = battle_type == 1
        # If the lead is hurt, prioritize escaping wild battles so the overworld
        # healing route can take over instead of fainting during grinding.
        if is_wild and obs.lead_hp_ratio is not None and obs.lead_hp_ratio <= 0.25 and self.repeated_position_count >= 8:
            return "battle_run_after_potion_stall", ["hold_down_20", "wait_20", "hold_right_20", "wait_20", "press_a", "wait_80", "press_b", "wait_30"]
        if obs.lead_hp_ratio is not None and obs.lead_hp_ratio <= 0.25 and potion_count > 0:
            return "battle_use_potion_low_hp", ["walk_right", "wait_20", "press_a", "wait_40", "press_a", "wait_40", "press_a", "wait_120"]
        if is_wild and obs.needs_healing and (not self.visual_textbox_active(obs) or self.repeated_hash_count >= 3 or self.repeated_position_count >= 5):
            return "battle_run_low_hp", ["hold_down_20", "wait_20", "hold_right_20", "wait_20", "press_a", "wait_80"]
        if is_wild and ball_count > 0 and party_count < 3 and self.repeated_position_count >= 4:
            return "battle_throw_ball", ["walk_right", "wait_20", "press_a", "wait_40", "press_a", "wait_120"]
        if self.repeated_position_count >= 20 and is_wild:
            return "battle_run_stuck_wild", ["press_b", "wait_30", "hold_down_20", "wait_20", "hold_right_20", "wait_20", "press_a", "wait_80"]
        if self.repeated_position_count >= 8 and battle_type == 2 and party_count <= 1:
            return "battle_attack_single_party_stuck_menu", ["press_b", "wait_30", "press_b", "wait_30", "hold_up_20", "wait_20", "hold_left_20", "wait_20", "press_a", "wait_60", "press_a", "wait_100", "hold_b_40", "wait_20"]
        if self.repeated_position_count >= 8:
            move_attempt = (self.repeated_position_count - 8) // 8 % 4
            move_select: list[str]
            if move_attempt == 1:
                move_select = ["walk_down", "wait_20"]
            elif move_attempt == 2:
                move_select = ["walk_right", "wait_20"]
            elif move_attempt == 3:
                move_select = ["walk_right", "wait_20", "walk_down", "wait_20"]
            else:
                move_select = []
            return "battle_attack_stuck_menu", ["press_a", "wait_30", *move_select, "press_a", "wait_80", "hold_b_40", "wait_20"]
        if self.visual_textbox_active(obs):
            return "battle_clear_textbox", ["press_a", "wait_60"]
        # Simple early-game battle policy: choose FIGHT/default first move, then
        # advance animation text.
        if self.rng.random() < 0.9:
            return "battle_attack", ["press_a", "wait_30", "press_a", "wait_60", "hold_b_40", "wait_20"]
        return "battle_clear_text", MACROS["speed_text"]

    def _healing_macro(self, obs: Observation) -> tuple[str, list[str]] | None:
        if not obs.needs_healing or self.visual_textbox_active(obs):
            return None
        actual = self.actual_xy(obs)
        actual_x, actual_y = actual if actual is not None else (None, None)

        # Explicit early healing route:
        # New Bark Town -> Route 29 -> Cherrygrove City -> Pokecenter 1F -> nurse.
        # Coordinates below use pokecrystal event coordinates. actual_x/actual_y
        # account for the swapped-looking RAM axes in this runtime.
        if obs.map_group_number == (24, 3):
            # Route 29 west edge exits to Cherrygrove. Use the same collision-
            # derived path as the main route so low-HP mode avoids ledge pockets.
            if isinstance(actual_x, int) and actual_x <= 1 and actual_y in {6, 7}:
                return "heal_route29_enter_cherrygrove", ["walk_left", "wait_60"]
            if isinstance(actual_x, int) and isinstance(actual_y, int):
                if actual_x == 16 and actual_y > 6:
                    return "heal_route29_rejoin_midlane", ["walk_up", "wait_30"]
                if 16 < actual_x <= 21 and actual_y == 4:
                    return "heal_route29_west_midlane", ["walk_left", "wait_30"]
                if 11 < actual_x <= 16 and actual_y == 6:
                    return "heal_route29_continue_ledge_line_west", ["walk_left", "wait_30"]
                if 0 < actual_x <= 11 and actual_y == 7:
                    return "heal_route29_west_edge_line", ["walk_left", "wait_30"]
                if actual_x < 27 and actual_y >= 10:
                    return self.step_toward_actual(obs, 27, 14, "heal_route29_recover_to_mid_detour")
                if actual_x == 27 and 10 <= actual_y < 14:
                    return "heal_route29_recover_drop_to_detour", ["walk_down", "wait_30"]
                if 27 <= actual_x < 31 and actual_y >= 14:
                    return self.step_toward_actual(obs, 31, 14, "heal_route29_recover_to_mid_detour")
                if actual_x == 31 and actual_y > 11:
                    return self.step_toward_actual(obs, 31, 11, "heal_route29_recover_to_mid_detour")
            route29_heal = self.follow_actual_waypoints(obs, ROUTE29_TO_CHERRYGROVE, "heal_route29_collision_path")
            if route29_heal is not None:
                return route29_heal
            return "heal_route29_wait_for_coords", ["wait_60"]

        if obs.map_group_number == (24, 4):
            # New Bark has no Pokemon Center. Exit west to Route 29, aligning to
            # the west road if necessary.
            if isinstance(actual_x, int) and actual_x <= 1:
                return "heal_new_bark_enter_route29", ["walk_left", "wait_60"]
            if isinstance(actual_y, int) and actual_y < 8:
                return "heal_new_bark_align_road_south", ["walk_down", "wait_30"]
            return "heal_new_bark_to_route29_west", ["walk_left", "wait_30"]

        if obs.map_group_number == (26, 3):
            # Cherrygrove Pokecenter entrance warp is at actual (29, 3). Stand
            # one tile below it at (29, 4), then step up through the door.
            if actual == (29, 4):
                return "heal_cherrygrove_enter_center", ["walk_up", "wait_80"]
            return self.step_toward_actual(obs, 29, 4, "heal_cherrygrove_to_center_door")

        if obs.map_group_number == (26, 5):
            # Cherrygrove Pokecenter nurse stands at actual (3, 1). Interact
            # from actual (3, 2), then dialogue handling confirms the heal flow.
            if isinstance(actual_x, int) and isinstance(actual_y, int):
                if actual_y > 3:
                    if actual_x < 3:
                        return "heal_cherrygrove_to_nurse_right", ["walk_right", "wait_30"]
                    if actual_x > 3:
                        return "heal_cherrygrove_to_nurse_left", ["walk_left", "wait_30"]
                    return "heal_cherrygrove_to_nurse_up", ["walk_up", "wait_30"]
                if actual_y < 3:
                    if actual_x < 3:
                        return "heal_cherrygrove_to_nurse_right", ["walk_right", "wait_30"]
                    if actual_x > 3:
                        return "heal_cherrygrove_to_nurse_left", ["walk_left", "wait_30"]
                    return "heal_cherrygrove_back_to_nurse_spot", ["walk_down", "wait_30"]
                if actual_x < 3:
                    return "heal_cherrygrove_to_nurse_right", ["walk_right", "wait_30"]
                if actual_x > 3:
                    return "heal_cherrygrove_to_nurse_left", ["walk_left", "wait_30"]
                return "heal_cherrygrove_talk_to_nurse", ["press_a", "wait_60"]
            return "heal_cherrygrove_wait_for_coords", ["wait_60"]

        if obs.map_group_number == (26, 1):
            if obs.lead_hp_ratio is not None and obs.lead_hp_ratio <= 0.30:
                route30_heal = self.route30_grid_macro(obs, ROUTE30_CHERRYGROVE_TARGETS, "heal_route30_to_cherrygrove")
                if route30_heal is not None:
                    return route30_heal
            route30_violet = self.route30_grid_macro(obs, ROUTE30_ROUTE31_TARGETS, "heal_route30_to_route31")
            if route30_violet is not None:
                return route30_violet
            return "heal_route30_grid_fallback", ["walk_up", "wait_30"]

        if obs.map_group_number == (26, 2):
            # Route 31: nearest reliable center is Violet, through the west gate.
            if actual == (4, 6) or actual == (4, 7):
                return "heal_route31_enter_violet_gate", ["walk_left", "wait_80"]
            if isinstance(actual_y, int) and actual_y < 6:
                return "heal_route31_gate_row_down", ["walk_down", "wait_30"]
            if isinstance(actual_y, int) and actual_y > 7:
                return "heal_route31_gate_row_up", ["walk_up", "wait_30"]
            return "heal_route31_west_to_gate", ["walk_left", "wait_30"]

        if obs.map_group_number == (26, 11):
            if isinstance(actual_x, int) and actual_x <= 1:
                return "heal_violet_gate_enter_city", ["walk_left", "wait_80"]
            if isinstance(actual_y, int) and actual_y < 4:
                return "heal_violet_gate_align_down", ["walk_down", "wait_30"]
            if isinstance(actual_y, int) and actual_y > 5:
                return "heal_violet_gate_align_up", ["walk_up", "wait_30"]
            return "heal_violet_gate_west", ["walk_left", "wait_30"]

        if obs.map_group_number == (10, 5):
            # Violet Pokecenter entrance is actual (31, 25); stand below at
            # (31, 26), then step up.
            if actual == (31, 26):
                return "heal_violet_enter_center", ["walk_up", "wait_80"]
            return self.step_toward_actual(obs, 31, 26, "heal_violet_to_center_door")

        if obs.map_group_number == (10, 10):
            if actual in {(3, 2), (3, 3)}:
                return "heal_violet_talk_to_nurse", ["press_a", "wait_60"]
            if actual == (4, 3):
                return "heal_violet_align_nurse_left", ["walk_left", "wait_30"]
            if actual == (3, 2):
                return "heal_violet_talk_to_nurse", ["press_a", "wait_60"]
            return self.step_toward_actual(obs, 3, 2, "heal_violet_to_nurse")

        if obs.map_group_number == (10, 7):
            # Leave gym first, then Violet City center handling takes over.
            if actual in {(4, 15), (5, 15)}:
                return "heal_violet_gym_exit", ["walk_down", "wait_80"]
            return self.step_toward_actual(obs, 5, 15, "heal_violet_gym_to_exit")
        return None

    def _route_recovery_macro(self, obs: Observation, primary: str) -> tuple[str, list[str]] | None:
        if self.repeated_position_count < 3 and not self._coord_looping():
            return None
        coord_key = self.coord_key(obs.coord)
        blocked = self.coord_blocked_directions(obs) if coord_key else set()
        # Try perpendicular/retreat steps before retrying the primary route. This
        # avoids endless facing changes against trees, ledges, and building edges.
        options = {
            "left": ["up", "down", "right", "left"],
            "right": ["up", "down", "left", "right"],
            "up": ["left", "right", "down", "up"],
            "down": ["left", "right", "up", "down"],
        }.get(primary, ["up", "down", "left", "right"])
        index = min((self.repeated_position_count - 3) // 2, len(options) - 1)
        for direction in options[index:] + options[:index]:
            if direction not in blocked:
                return f"route_recover_{direction}", [self.direction_action(direction), "wait_30"]
        for direction in options[index:] + options[:index]:
            if direction != primary:
                return f"route_recover_probe_{direction}", [self.direction_action(direction), "wait_30"]
        return None

    def _coordinate_macro(self, obs: Observation) -> tuple[str, list[str]]:
        coord_key = self.coord_key(obs.coord)
        self.coord_visits[coord_key] = self.coord_visits.get(coord_key, 0) + 1
        blocked = set(self.blocked_moves.get(coord_key, []))
        candidates = [a for a in MOVE_ACTIONS if a not in blocked] or MOVE_ACTIONS[:]

        # Frontier/count-based exploration: prefer directions least recently used
        # at this coordinate and avoid immediate two-action oscillations.
        weights = []
        for action in candidates:
            action_count = self.q.setdefault(coord_key, {}).get(action, 0.0)
            weight = 1.0 / (1.0 + max(action_count, 0.0))
            if self._would_oscillate(action):
                weight *= 0.25
            weights.append(weight)
        action = self.rng.choices(candidates, weights=weights)[0]
        self.q.setdefault(coord_key, {})[action] = self.q[coord_key].get(action, 0.0) + 1.0
        return f"frontier_{action}", [action, "wait_20"]

    def _visual_macro(self, obs: Observation) -> tuple[str, list[str]]:
        if self.repeated_hash_count >= 8:
            return self.rng.choice([
                ("visual_unstick_text", MACROS["advance_text"]),
                ("visual_unstick_menu", MACROS["unstick_menu"]),
                ("visual_unstick_sweep_down", ["walk_down", "wait_20", "walk_down", "wait_20"]),
                ("visual_unstick_sweep_right", ["walk_right", "wait_20", "walk_right", "wait_20"]),
            ])

        h = obs.visual_hash
        self.q.setdefault(h, {})
        epsilon = max(0.10, 0.45 - min(self.turn, 3000) / 9000)
        if self.rng.random() < epsilon or not self.q[h]:
            # Early Gold objective: leave New Bark, progress west/north through
            # Cherrygrove/Violet, then fight Falkner.  Without Gen-2 RAM/tiles,
            # visual mode uses a curriculum bias rather than uniform wandering.
            control = self.read_control()
            if control.get("movement_bias") == "balanced":
                weights = [1.2, 1.2, 1.2, 1.2, 0.55, 0.12]
            elif control.get("movement_bias") == "north_east":
                weights = [1.7, 0.8, 0.9, 1.6, 0.45, 0.10]
            else:
                weights = [1.5, 0.8, 1.7, 1.0, 0.45, 0.10]
            action = self.rng.choices(BASIC_ACTIONS, weights=weights)[0]
        else:
            action = max(self.q[h].items(), key=lambda kv: kv[1])[0]
        if self._would_oscillate(action) and action.startswith("walk_"):
            action = self.rng.choice([a for a in MOVE_ACTIONS if a != action])
        return f"visual_{action}", [action, "wait_20" if action.startswith("walk_") else "wait_30"]

    def _learned_place_macro(self, obs: Observation) -> tuple[str, list[str]] | None:
        place = self.places.get(self.place_key(obs)) or {}
        actions = place.get("actions") or {}
        if not isinstance(actions, dict):
            return None
        # Re-try valuable interactions at places that have produced dialogue,
        # transitions, badges, or user-marked NPC/important hints.
        if place.get("important") or place.get("interaction_score", 0) >= 2.0:
            press_a = actions.get("press_a") or {}
            tries = float(press_a.get("tries", 0))
            avg_reward = float(press_a.get("reward_total", 0.0)) / max(tries, 1.0)
            exhausted = bool(place.get("interaction_exhausted")) or (tries >= 8 and avg_reward <= 0.0)
            if not exhausted and (tries < 4 or avg_reward > 0.05):
                return "memory_interact_important", ["press_a", "wait_30"]

        best_action = None
        best_score = -999.0
        for action in MOVE_ACTIONS:
            local_stat = self.current_local_cell().get("actions", {}).get(action) or {}
            if local_stat.get("blocked") and int(local_stat.get("tries", 0)) >= 2:
                continue
            stat = actions.get(action) or {}
            tries = float(stat.get("tries", 0))
            score = float(stat.get("reward_total", 0.0)) / max(tries, 1.0)
            if local_stat.get("open"):
                score += 0.8
            if stat.get("moved", 0) > 0:
                score += 0.35
            if self._would_oscillate(action):
                score -= 0.6
            # Bias toward actions that have not been sufficiently tested here.
            if tries < 2:
                score += 0.45
            if score > best_score:
                best_score = score
                best_action = action
        if best_action and best_score > 0.15 and self.rng.random() < 0.70:
            return "memory_" + best_action, [best_action, "wait_20"]
        return None

    def _would_oscillate(self, action: str) -> bool:
        opposite = {
            "walk_up": "walk_down",
            "walk_down": "walk_up",
            "walk_left": "walk_right",
            "walk_right": "walk_left",
        }
        if not self.recent_actions:
            return False
        if self.recent_actions[-1] == opposite.get(action):
            return True
        recent = list(self.recent_actions)[-6:]
        if len(recent) >= 4 and recent[-1] == recent[-3] and recent[-2] == action:
            return True
        if len(recent) >= 5 and recent[-1] == recent[-4] and recent[-2] == recent[-5] and recent[-3] == action:
            return True
        return False

    @staticmethod
    def coord_key(coord: tuple[int, int, int] | None) -> str:
        if coord is None:
            return "none"
        return f"{coord[0]}:{coord[1]}:{coord[2]}"

    def local_key(self, x: int | None = None, y: int | None = None) -> str:
        if x is None:
            x = int(self.local_position.get("x", 0))
        if y is None:
            y = int(self.local_position.get("y", 0))
        return f"{x},{y}"

    def current_local_cell(self) -> dict[str, Any]:
        key = self.local_key()
        return self.local_cells.setdefault(key, {"visits": 0, "actions": {}, "seen_hashes": []})

    def place_key(self, obs: Observation) -> str:
        if obs.coord is not None:
            return "coord:" + self.coord_key(obs.coord)
        # Generic Gold currently lacks RAM position.  Group by perceptual hashes
        # so a visually repeated room/town view becomes a remembered place.
        return "visual:" + obs.visual_hash[:24] + ":" + str(obs.visual.get("dhash", ""))[:12]

    def update_place_memory(self, obs: Observation) -> None:
        key = self.place_key(obs)
        place = self.places.setdefault(key, {
            "visits": 0,
            "first_seen_turn": self.turn,
            "actions": {},
            "exits": {},
            "notes": [],
            "screen_classes": {},
        })
        place["visits"] = int(place.get("visits", 0)) + 1
        place["last_seen_turn"] = self.turn
        place["last_hash"] = obs.visual_hash
        place["coord"] = self.coord_key(obs.coord)
        place["screen_class"] = obs.screen_class
        classes = place.setdefault("screen_classes", {})
        classes[obs.screen_class] = int(classes.get(obs.screen_class, 0)) + 1
        cell = self.current_local_cell()
        cell["visits"] = int(cell.get("visits", 0)) + 1
        hashes = cell.setdefault("seen_hashes", [])
        if obs.visual_hash not in hashes:
            hashes.append(obs.visual_hash)
            del hashes[:-6]
        cell["last_seen_turn"] = self.turn
        self._apply_guidance_note(key, place)

    def update_visual_odometry(self, before: Observation, after: Observation, primitive: str, distance: int) -> bool:
        if primitive not in MOVE_DELTAS:
            return False
        before_key = self.local_key()
        before_cell = self.local_cells.setdefault(before_key, {"visits": 0, "actions": {}, "seen_hashes": []})
        action_stat = before_cell.setdefault("actions", {}).setdefault(primitive, {"tries": 0, "open": 0, "blocked": 0, "last_distance": 0})
        action_stat["tries"] = int(action_stat.get("tries", 0)) + 1
        action_stat["last_distance"] = distance
        moved = self.visual_step_succeeded(before, after, primitive, distance)
        if moved:
            dx, dy = MOVE_DELTAS[primitive]
            self.local_position["x"] = int(self.local_position.get("x", 0)) + dx
            self.local_position["y"] = int(self.local_position.get("y", 0)) + dy
            self.recent_local_positions.append(self.local_key())
            action_stat["open"] = int(action_stat.get("open", 0)) + 1
            action_stat["blocked"] = False
            after_cell = self.current_local_cell()
            after_cell["entered_from"] = before_key
            after_cell["last_enter_action"] = primitive
        else:
            action_stat["blocked"] = int(action_stat.get("blocked", 0)) + 1
        return moved

    def _apply_guidance_note(self, key: str, place: dict[str, Any]) -> None:
        guidance = str(self.read_control().get("guidance_prompt") or "").strip()
        if not guidance or guidance == self.last_guidance_note:
            return
        self.last_guidance_note = guidance
        lowered = guidance.lower()
        note = {"turn": self.turn, "text": guidance}
        notes = place.setdefault("notes", [])
        notes.append(note)
        del notes[:-8]
        if any(word in lowered for word in ("important", "npc", "rival", "mom", "professor", "elm", "guide", "gym", "badge", "falkner")):
            place["important"] = True
            self.important_npcs[key] = {
                "place": key,
                "turn": self.turn,
                "hint": guidance,
                "last_seen_hash": place.get("last_hash"),
            }

    def update_world_model(self, before: Observation, after: Observation, macro_name: str, actions: list[str]) -> None:
        b_place = self.place_key(before)
        a_place = self.place_key(after)
        primitive = next((a for a in actions if not a.startswith("wait_") and not a.startswith("hold_")), macro_name)
        movement = self.movement_primitive(actions)
        distance = self.visual_distance(before, after)
        ui_active = self.structured_dialog_active(before) or self.structured_dialog_active(after)
        local_moved = False if (ui_active and movement) else self.update_visual_odometry(before, after, primitive, distance)
        before_place = self.places.setdefault(b_place, {"visits": 0, "actions": {}, "exits": {}, "notes": [], "screen_classes": {}})
        action_stats = before_place.setdefault("actions", {}).setdefault(primitive, {"tries": 0, "changed": 0, "moved": 0, "reward_total": 0.0})
        action_stats["tries"] = int(action_stats.get("tries", 0)) + 1
        action_stats["last_visual_distance"] = distance
        changed = before.visual_hash != after.visual_hash
        moved = local_moved or b_place != a_place
        if changed:
            action_stats["changed"] = int(action_stats.get("changed", 0)) + 1
        if moved:
            action_stats["moved"] = int(action_stats.get("moved", 0)) + 1
            before_place.setdefault("exits", {})[primitive] = a_place

        if primitive == "press_a" and after.screen_class in {"dialogue", "menu_or_text"}:
            before_place["interaction_score"] = float(before_place.get("interaction_score", 0.0)) + 1.0
            if before_place.get("interaction_score", 0.0) >= 2.0:
                before_place["important"] = True
                self.important_npcs.setdefault(b_place, {
                    "place": b_place,
                    "turn": self.turn,
                    "hint": "Repeated press_a here opens dialogue/menu; possible NPC/sign/story object.",
                    "last_seen_hash": before.visual_hash,
                })
        if primitive == "press_a":
            tries = float(action_stats.get("tries", 0))
            reward_total = float(action_stats.get("reward_total", 0.0))
            if tries >= 8 and reward_total / max(tries, 1.0) <= 0.0:
                before_place["interaction_exhausted"] = True

        before_coord = before.coord
        after_coord = after.coord
        if before_coord is None or after_coord is None:
            return
        b = self.coord_key(before_coord)
        a = self.coord_key(after_coord)
        self.coord_visits[a] = self.coord_visits.get(a, 0) + 1
        if ui_active and a == b and movement:
            self.log_event("movement_ignored_during_dialog", {
                "snapshot": self.event_snapshot(before),
                "macro": macro_name,
                "actions": actions,
            })
            return
        if a != b:
            edges = set(self.edges.get(b, []))
            edges.add(a)
            self.edges[b] = sorted(edges)
            direction = self.action_direction(movement or primitive)
            if direction:
                edge = self.directed_edge_state(b, direction)
                old_state = edge.get("state")
                edge.update({
                    "from": b,
                    "to": a,
                    "direction": direction,
                    "action": self.direction_action(direction),
                    "state": "open",
                    "last_macro": macro_name,
                    "last_turn": self.turn,
                    "last_objective": self.read_control().get("objective", ""),
                })
                edge["open_count"] = int(edge.get("open_count", 0)) + 1
                if old_state != "open":
                    self.log_event("edge_opened", {
                        "from": b,
                        "to": a,
                        "direction": direction,
                        "action": edge.get("action"),
                        "open_count": edge["open_count"],
                        "blocked_count": int(edge.get("blocked_count", 0)),
                        "macro": macro_name,
                    })
        else:
            for action in actions:
                if action.startswith("walk_") or action.startswith("hold_"):
                    blocked = set(self.blocked_moves.get(b, []))
                    blocked.add(action)
                    direction = self.action_direction(action)
                    if direction:
                        blocked.add(f"walk_{direction}")
                        blocked.add(f"hold_{direction}_60")
                        edge = self.directed_edge_state(b, direction)
                        old_state = edge.get("state")
                        edge.update({
                            "from": b,
                            "to": self.actual_coord_key(before, self.next_actual(self.actual_xy(before), direction)) if self.actual_xy(before) else None,
                            "direction": direction,
                            "action": self.direction_action(direction),
                            "state": "open" if int(edge.get("open_count", 0)) > 0 else "blocked",
                            "last_macro": macro_name,
                            "last_turn": self.turn,
                            "last_objective": self.read_control().get("objective", ""),
                        })
                        edge["blocked_count"] = int(edge.get("blocked_count", 0)) + 1
                        if old_state != "blocked" or edge["blocked_count"] in {1, 2, 5, 10}:
                            self.log_event("edge_blocked", {
                                "from": b,
                                "to": edge.get("to"),
                                "direction": direction,
                                "action": edge.get("action"),
                                "blocked_count": edge["blocked_count"],
                                "open_count": int(edge.get("open_count", 0)),
                                "macro": macro_name,
                            })
                    self.blocked_moves[b] = sorted(blocked)

    def update_reward(self, before: Observation, macro_name: str, actions: list[str], after: Observation) -> float:
        prev_hash = before.visual_hash
        new_hash = after.visual_hash
        h_reward = 0.0
        novelty = 1.0 / (1.0 + self.visits.get(new_hash, 0))
        h_reward += novelty
        h_reward += 0.5 if new_hash != prev_hash else -0.35
        if after.coord is not None:
            coord_key = self.coord_key(after.coord)
            h_reward += 2.0 / (1.0 + self.coord_visits.get(coord_key, 0))
            if before.coord and after.coord != before.coord:
                h_reward += 0.75
        if after.badges > before.badges:
            h_reward += 1000.0 * (after.badges - before.badges)
        if before.screen_class in {"dialogue", "menu_or_text"} and after.screen_class not in {"dialogue", "menu_or_text"}:
            h_reward += 1.25
        if after.screen_class == "transition":
            h_reward += 0.2
        if self.repeated_hash_count >= 5:
            h_reward -= 0.55
        if self._action_loop_penalty():
            h_reward -= 0.65
        if macro_name.startswith("intro") and after.screen_class != "transition":
            h_reward += 0.05

        # Learn both per-hash and per-coordinate action value.  Macro name is
        # kept for explainability; first button action is the control primitive.
        primitive = next((a for a in actions if not a.startswith("wait_")), macro_name)
        for key in [prev_hash, self.coord_key(before.coord)]:
            self.q.setdefault(key, {})
            old = self.q[key].get(primitive, 0.0)
            self.q[key][primitive] = old + 0.20 * (h_reward - old)
        place = self.places.setdefault(self.place_key(before), {"visits": 0, "actions": {}, "exits": {}, "notes": [], "screen_classes": {}})
        stat = place.setdefault("actions", {}).setdefault(primitive, {"tries": 0, "changed": 0, "moved": 0, "reward_total": 0.0})
        stat["reward_total"] = float(stat.get("reward_total", 0.0)) + h_reward
        if primitive == "press_a":
            tries = float(stat.get("tries", 0))
            avg_reward = float(stat.get("reward_total", 0.0)) / max(tries, 1.0)
            if tries >= 8 and avg_reward <= 0.0:
                place["interaction_exhausted"] = True
        return h_reward

    def memory_summary(self, obs: Observation | None = None) -> dict[str, Any]:
        current_key = self.place_key(obs) if obs is not None else None
        current = self.places.get(current_key, {}) if current_key else {}
        important = sorted(
            self.important_npcs.values(),
            key=lambda item: int(item.get("turn", 0)),
            reverse=True,
        )[:6]
        return {
            "places_known": len(self.places),
            "local_position": dict(self.local_position),
            "local_cell": self.current_local_cell(),
            "local_cells_known": len(self.local_cells),
            "important_npcs": important,
            "current_place": {
                "key": current_key,
                "visits": current.get("visits", 0),
                "important": bool(current.get("important")),
                "notes": current.get("notes", [])[-3:],
                "exits": current.get("exits", {}),
                "actions": current.get("actions", {}),
            },
        }

    def _action_loop_penalty(self) -> bool:
        if len(self.recent_actions) < 8:
            return False
        counts = Counter(self.recent_actions)
        most_common = counts.most_common(1)[0][1]
        return most_common >= 7

    def act(self, actions: list[str]) -> dict[str, Any]:
        payload = json.loads(self.request("/action", {"actions": actions}, timeout=35))
        for action in actions:
            if not action.startswith("wait_"):
                self.recent_actions.append(self.normalized_move_action(action))
        return payload

    def single_step_actions(self, obs: Observation, macro_name: str, actions: list[str]) -> list[str]:
        control = self.read_control()
        if control.get("single_step", True) is False:
            return actions
        # Text needs one confirm at a time. Holding B plus A in a single turn can
        # skip through prompts or trigger movement before the next screenshot.
        if macro_name == "battle_run_low_hp":
            return actions
        if macro_name.startswith("battle_"):
            return actions
        if macro_name.startswith("name_"):
            return actions
        if macro_name == "dialogue_confirm_stubborn":
            return actions
        if macro_name.startswith("dialogue_menu_"):
            return actions
        if macro_name.startswith(("storage_", "mart_")):
            return actions
        if macro_name.startswith("self_recover_"):
            return actions
        if macro_name.startswith(("route30_rejoin_lower_cherrygrove_lane", "route30_rejoin_upper_exit_lane", "route30_upper_tree_bypass", "route30_upper_exit_path", "route30_to_route31_path", "route30_to_route31_grid", "heal_route30_to_cherrygrove_grid", "heal_route30_to_route31_grid")):
            primitive = next((a for a in actions if not a.startswith("wait_")), None)
            if primitive is None:
                return ["wait_60"]
            return [primitive, "wait_80" if primitive.startswith(("walk_", "hold_")) else "wait_60"]
        if macro_name == "route30_north_to_route31" and obs.map_group_number == (26, 1):
            actual = self.actual_xy(obs)
            if actual is not None and actual[0] == 9 and actual[1] < 20:
                return ["walk_up", "wait_80"]
        if obs.needs_healing and macro_name.startswith(("heal_cherrygrove_", "heal_route29_", "heal_route30_", "heal_route31_", "heal_violet_")):
            primitive = next((a for a in actions if not a.startswith("wait_")), None)
            if primitive is None:
                return ["wait_60"]
            return [primitive, "wait_80" if primitive.startswith(("walk_", "hold_")) else "wait_60"]
        if macro_name.startswith("structured_house"):
            primitive = next((a for a in actions if not a.startswith("wait_")), None)
            if primitive is None:
                return ["wait_60"]
            return [primitive, "wait_80" if primitive.startswith(("walk_", "hold_")) else "wait_60"]
        if macro_name.startswith("structured_town_to_lab_v2_map"):
            primitive = next((a for a in actions if not a.startswith("wait_")), None)
            if primitive is None:
                return ["wait_60"]
            return [primitive, "wait_80" if primitive.startswith(("walk_", "hold_")) else "wait_60"]
        if self.visual_textbox_active(obs) or (macro_name.startswith("dialogue") and obs.screen_class in {"dialogue", "menu_or_text"}):
            return ["press_a", "wait_60"]
        primitive = next((a for a in actions if not a.startswith("wait_")), None)
        if primitive is None:
            return ["wait_60"]
        if primitive.startswith("walk_") or primitive.startswith("hold_"):
            direction = self.action_direction(primitive)
            if direction:
                learned = self.learned_detour_direction(obs, direction)
                if learned != direction:
                    return [self.direction_action(learned), "wait_80"]
            return [primitive, "wait_80"]
        return [primitive, "wait_60"]

    def save_state(self, name: str) -> None:
        try:
            self.request("/save", {"name": name}, timeout=20)
        except Exception as exc:  # noqa: BLE001
            self.log({"event": "save_failed", "error": repr(exc), "name": name})

    def maybe_save_milestone(self, obs: Observation) -> None:
        milestones: list[str] = []
        if obs.has_starter:
            milestones.append("after_starter_cyndaquil")
        if obs.map_group_number == (24, 3):
            milestones.append("route_29_with_starter")
        if obs.badges >= 1:
            milestones.append("after_first_badge")
        for name in milestones:
            if name in self.saved_milestones:
                continue
            self.save_state(name)
            self.saved_milestones.add(name)
            self.log({"event": "milestone_save", "name": name})

    def log(self, item: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        item = {"ts": time.time(), "turn": self.turn, **item}
        with self.log_path.open("a") as f:
            f.write(json.dumps(item, sort_keys=True) + "\n")

    def log_event(self, event: str, item: dict[str, Any] | None = None) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {"ts": time.time(), "turn": self.turn, "event": event, **(item or {})}
        with self.event_log_path.open("a") as f:
            f.write(json.dumps(payload, sort_keys=True) + "\n")

    def event_snapshot(self, obs: Observation) -> dict[str, Any]:
        player = obs.state.get("player") or {}
        pos = player.get("position") or {}
        party = obs.state.get("party") or []
        lead = party[0] if party and isinstance(party[0], dict) else {}
        battle = obs.state.get("battle") or {}
        dialog = obs.state.get("dialog") or {}
        actual = self.actual_xy(obs)
        return {
            "map": {
                "id": pos.get("map_id"),
                "group": pos.get("map_group"),
                "number": pos.get("map_number"),
                "name": pos.get("map_name"),
            },
            "raw_position": {"x": pos.get("x"), "y": pos.get("y")},
            "actual_position": {"x": actual[0], "y": actual[1]} if actual else None,
            "facing": player.get("facing"),
            "lead": {
                "species": lead.get("species"),
                "species_id": lead.get("species_id"),
                "level": lead.get("level"),
                "hp": lead.get("hp"),
                "max_hp": lead.get("max_hp"),
                "hp_ratio": obs.lead_hp_ratio,
            },
            "party_count": len(party),
            "battle": {
                "in_battle": battle.get("in_battle"),
                "type_id": battle.get("type_id"),
                "wild_species_id": battle.get("wild_species_id"),
                "enemy_level": battle.get("enemy_level"),
            },
            "dialog": {
                "active": dialog.get("active"),
                "window_stack_size": dialog.get("window_stack_size"),
                "menu_cursor": dialog.get("menu_cursor"),
            },
            "screen_class": obs.screen_class,
            "coord": self.coord_key(obs.coord),
        }

    def log_state_events(self, before: Observation, after: Observation, macro_name: str, actions: list[str], reward: float) -> None:
        before_player = before.state.get("player") or {}
        after_player = after.state.get("player") or {}
        before_pos = before_player.get("position") or {}
        after_pos = after_player.get("position") or {}
        before_party = before.state.get("party") or []
        after_party = after.state.get("party") or []
        before_lead = before_party[0] if before_party and isinstance(before_party[0], dict) else {}
        after_lead = after_party[0] if after_party and isinstance(after_party[0], dict) else {}
        before_battle = before.state.get("battle") or {}
        after_battle = after.state.get("battle") or {}

        before_map_id = before_pos.get("map_id")
        after_map_id = after_pos.get("map_id")
        if before_map_id != after_map_id:
            self.route_indices.clear()
            self.log_event("map_changed", {
                "from": self.event_snapshot(before),
                "to": self.event_snapshot(after),
                "macro": macro_name,
                "actions": actions,
            })

        before_in_battle = bool(before_battle.get("in_battle"))
        after_in_battle = bool(after_battle.get("in_battle"))
        if not before_in_battle and after_in_battle:
            self.log_event("battle_started", {
                "snapshot": self.event_snapshot(after),
                "wild_species_id": after_battle.get("wild_species_id"),
                "enemy_level": after_battle.get("enemy_level"),
                "lead_hp_before": before_lead.get("hp"),
                "lead_hp_after": after_lead.get("hp"),
                "macro": macro_name,
                "actions": actions,
            })
        if before_in_battle and not after_in_battle:
            before_hp = before_lead.get("hp")
            after_hp = after_lead.get("hp")
            hp_delta = after_hp - before_hp if isinstance(before_hp, int) and isinstance(after_hp, int) else None
            self.log_event("battle_ended", {
                "snapshot": self.event_snapshot(after),
                "wild_species_id": before_battle.get("wild_species_id"),
                "enemy_level": before_battle.get("enemy_level"),
                "lead_hp_before": before_hp,
                "lead_hp_after": after_hp,
                "hp_delta": hp_delta,
                "macro": macro_name,
                "actions": actions,
            })

        before_hp = before_lead.get("hp")
        after_hp = after_lead.get("hp")
        if isinstance(before_hp, int) and isinstance(after_hp, int) and before_hp != after_hp:
            self.log_event("hp_changed", {
                "snapshot": self.event_snapshot(after),
                "hp_before": before_hp,
                "hp_after": after_hp,
                "delta": after_hp - before_hp,
                "needs_healing": after.needs_healing,
                "macro": macro_name,
                "actions": actions,
            })

        if before.needs_healing != after.needs_healing:
            self.log_event("healing_state_changed", {
                "snapshot": self.event_snapshot(after),
                "needs_healing_before": before.needs_healing,
                "needs_healing_after": after.needs_healing,
                "macro": macro_name,
                "actions": actions,
            })

        if len(before_party) != len(after_party):
            self.log_event("party_count_changed", {
                "snapshot": self.event_snapshot(after),
                "party_count_before": len(before_party),
                "party_count_after": len(after_party),
                "macro": macro_name,
                "actions": actions,
            })

        if macro_name.startswith("self_recover_"):
            moved = before.coord is not None and after.coord is not None and after.coord != before.coord
            visual_changed = after.visual_hash != before.visual_hash
            map_changed = before_map_id != after_map_id
            self.log_event("self_recovery_result", {
                "snapshot": self.event_snapshot(after),
                "macro": macro_name,
                "actions": actions,
                "moved": moved,
                "visual_changed": visual_changed,
                "map_changed": map_changed,
                "position_stuck": self.repeated_position_count,
            })
            if moved or map_changed:
                self.route_no_progress_count = 0

        if before.badges != after.badges:
            self.log_event("badges_changed", {
                "snapshot": self.event_snapshot(after),
                "badges_before": before.badges,
                "badges_after": after.badges,
                "macro": macro_name,
                "actions": actions,
            })

        after_battle_active = bool((after.state.get("battle") or {}).get("in_battle"))
        if not after_battle_active and (self.repeated_position_count >= 8 or self._coord_oscillating()) and self.turn - self.last_stuck_event_turn >= 10:
            self.last_stuck_event_turn = self.turn
            self.log_event("navigation_warning", {
                "snapshot": self.event_snapshot(after),
                "macro": macro_name,
                "actions": actions,
                "reward": round(reward, 3),
                "position_stuck": self.repeated_position_count,
                "coord_oscillating": self._coord_oscillating(),
                "recent_coords": list(self.recent_coords)[-8:],
                "recent_actions": list(self.recent_actions)[-8:],
                "blocked_directions": sorted(self.coord_blocked_directions(after)),
                "route_indices": dict(self.route_indices),
            })

    def run(self, max_turns: int | None = None, delay: float = 0.35) -> None:
        self.load()
        self.log({"event": "autoplayer_started", "base_url": self.base_url, "mode": "goal_directed_gold"})
        self.log_event("autoplayer_started", {"base_url": self.base_url, "mode": "goal_directed_gold"})
        while max_turns is None or self.turn < max_turns:
            try:
                control = self.read_control()
                if not control.get("enabled", True):
                    self.write_status({
                        "enabled": False,
                        "engine": "v1",
                        "objective": control.get("objective"),
                        "phase": self.phase,
                        "turn": self.turn,
                        "message": "Autoplayer paused from dashboard",
                        "updated_at": time.time(),
                    })
                    time.sleep(max(delay, 0.5))
                    continue
                if control.get("engine", "v1") != "v1":
                    if not self.wrote_non_v1_idle_status:
                        self.write_status({
                            "enabled": True,
                            "engine": "v1",
                            "selected_engine": control.get("engine"),
                            "objective": control.get("objective"),
                            "phase": "PAUSED",
                            "turn": self.turn,
                            "message": "Legacy V1 idle while another bot engine is selected",
                            "updated_at": time.time(),
                        })
                        self.wrote_non_v1_idle_status = True
                    time.sleep(max(delay, 0.5))
                    continue
                self.wrote_non_v1_idle_status = False
                before = self.observe()
                h = before.visual_hash
                self.recent_hashes.append(h)
                if before.coord is not None:
                    self.recent_coords.append(self.coord_key(before.coord))

                macro_name, actions = self.choose_macro(before)
                actions = self.single_step_actions(before, macro_name, actions)
                self.act(actions)
                after = self.observe()
                if after.visual_hash == h:
                    self.repeated_hash_count += 1
                else:
                    self.repeated_hash_count = 0
                primitive = self.movement_primitive(actions)
                if primitive and before.coord is not None and after.coord == before.coord:
                    self.repeated_position_count += 1
                elif after.coord is not None and after.coord == self.last_coord:
                    self.repeated_position_count += 1
                else:
                    self.repeated_position_count = 0
                self.update_world_model(before, after, macro_name, actions)
                self.update_place_memory(after)
                self.maybe_save_milestone(after)
                reward = self.update_reward(before, macro_name, actions, after)
                self.log_state_events(before, after, macro_name, actions, reward)
                self.last_hash = after.visual_hash
                self.last_coord = after.coord
                self.turn += 1
                self.log({
                    "event": "turn",
                    "objective": control.get("objective", "reach Violet City and win the first gym badge"),
                    "guidance_prompt": control.get("guidance_prompt", ""),
                    "phase": self.phase,
                    "macro": macro_name,
                    "actions": actions,
                    "reward": round(reward, 3),
                    "before": before.visual,
                    "after": after.visual,
                    "coord_before": self.coord_key(before.coord),
                    "coord_after": self.coord_key(after.coord),
                    "stuck": self.repeated_hash_count,
                    "position_stuck": self.repeated_position_count,
                    "route_target": self.route_target,
                    "route_no_progress": self.route_no_progress_count,
                    "route_indices": dict(self.route_indices),
                    "coord_oscillating": self._coord_oscillating(),
                    "state_flags": before.state.get("flags", {}),
                    "memory": self.memory_summary(after),
                })
                self.write_status({
                    "enabled": True,
                    "engine": "v1",
                    "objective": control.get("objective"),
                    "movement_bias": control.get("movement_bias"),
                    "dialogue_speed": control.get("dialogue_speed"),
                    "guidance_prompt": control.get("guidance_prompt", ""),
                    "turn": self.turn,
                    "phase": self.phase,
                    "macro": macro_name,
                    "actions": actions,
                    "reward": round(reward, 3),
                    "stuck": self.repeated_hash_count,
                    "position_stuck": self.repeated_position_count,
                    "route_target": self.route_target,
                    "route_no_progress": self.route_no_progress_count,
                    "coord_oscillating": self._coord_oscillating(),
                    "screen_class": after.screen_class,
                    "visual_hash": after.visual_hash,
                    "coord": self.coord_key(after.coord),
                    "lead_hp_ratio": after.lead_hp_ratio,
                    "needs_healing": after.needs_healing,
                    "event_log": str(self.event_log_path),
                    "memory": self.memory_summary(after),
                    "updated_at": time.time(),
                })
                if self.turn % 25 == 0:
                    self.persist()
                time.sleep(delay)
            except (urllib.error.URLError, TimeoutError) as exc:
                self.log({"event": "http_error", "error": repr(exc)})
                time.sleep(2)
            except KeyboardInterrupt:
                break
            except Exception as exc:  # noqa: BLE001
                self.log({"event": "error", "error": repr(exc)})
                time.sleep(1)
        self.persist()
        self.log({"event": "autoplayer_stopped"})
        self.log_event("autoplayer_stopped")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:9879")
    ap.add_argument("--data-dir", default="/home/mojo/.pokemon-agent-gold")
    ap.add_argument("--max-turns", type=int, default=None)
    ap.add_argument("--delay", type=float, default=0.35)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    player = GoalDirectedGoldPlayer(args.base_url, Path(args.data_dir))
    if args.seed is not None:
        player.rng.seed(args.seed)
    player.run(max_turns=args.max_turns, delay=args.delay)


# Backwards-compatible name for older scripts/imports.
Learner = GoalDirectedGoldPlayer


if __name__ == "__main__":
    main()
