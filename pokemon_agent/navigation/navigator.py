"""Same-map planning for Navigation V2."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable

from pokemon_agent.pathfinding import find_path

from .world_graph import Direction, MapSpec


@dataclass(frozen=True, slots=True)
class Plan:
    map_name: str
    start: tuple[int, int]
    goal: tuple[int, int]
    directions: tuple[Direction, ...]
    path_source: str = "static_collision"

    @property
    def actions(self) -> tuple[str, ...]:
        return tuple(f"walk_{direction}" for direction in self.directions)

    @property
    def next_step(self) -> str | None:
        return self.actions[0] if self.actions else None

    @property
    def planned_path_length(self) -> int:
        return len(self.directions)


BlockedEdge = tuple[tuple[int, int], str]


def plan_to_any(
    map_spec: MapSpec,
    start: tuple[int, int],
    goals: Iterable[tuple[int, int]],
    blocked_edges: frozenset[BlockedEdge] = frozenset(),
) -> Plan | None:
    """Return the shortest same-map plan from start to any reachable goal."""
    if not map_spec.is_walkable(start):
        return None

    best_goal: tuple[int, int] | None = None
    best_path: list[str] | None = None
    collision = map_spec.collision_map()

    for goal in goals:
        if not map_spec.is_walkable(goal):
            continue
        path = _find_path_avoiding(start, goal, collision, blocked_edges) if blocked_edges else find_path(start, goal, collision)
        if start != goal and not path:
            continue
        if best_path is None or len(path) < len(best_path):
            best_goal = goal
            best_path = path

    if best_goal is None or best_path is None:
        return None
    return Plan(
        map_name=map_spec.name,
        start=start,
        goal=best_goal,
        directions=tuple(best_path),  # type: ignore[arg-type]
    )


def _find_path_avoiding(
    start: tuple[int, int],
    goal: tuple[int, int],
    collision: dict[tuple[int, int], bool],
    blocked_edges: frozenset[BlockedEdge],
) -> list[str]:
    if start == goal:
        return []
    queue: deque[tuple[int, int]] = deque([start])
    parents: dict[tuple[int, int], tuple[tuple[int, int], str]] = {}
    seen = {start}
    for current in iter(queue.popleft, None):
        for direction, delta in (("up", (0, -1)), ("down", (0, 1)), ("left", (-1, 0)), ("right", (1, 0))):
            if (current, direction) in blocked_edges:
                continue
            neighbor = (current[0] + delta[0], current[1] + delta[1])
            if neighbor in seen or not collision.get(neighbor, False):
                continue
            parents[neighbor] = (current, direction)
            if neighbor == goal:
                return _reconstruct_path(parents, neighbor)
            seen.add(neighbor)
            queue.append(neighbor)
        if not queue:
            break
    return []


def _reconstruct_path(
    parents: dict[tuple[int, int], tuple[tuple[int, int], str]],
    current: tuple[int, int],
) -> list[str]:
    path: list[str] = []
    while current in parents:
        current, direction = parents[current]
        path.append(direction)
    path.reverse()
    return path
