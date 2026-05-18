"""Cross-map route planning for Navigation V2."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from .map_registry import MapRegistry
from .navigator import BlockedEdge, Plan, plan_to_any
from .world_graph import ConnectionSpec, MapSpec, WarpSpec


TransitionKind = Literal["warp", "connection"]


@dataclass(frozen=True, slots=True)
class MapTransition:
    kind: TransitionKind
    source_key: tuple[int, int]
    dest_key: tuple[int, int]
    source_tiles: tuple[tuple[int, int], ...]
    dest_tiles: tuple[tuple[int, int], ...]
    warp: WarpSpec | None = None
    connection: ConnectionSpec | None = None

    @property
    def exit_action(self) -> str | None:
        if self.warp is not None:
            return None
        if self.connection is None:
            return None
        return connection_exit_action(self.connection.direction)


@dataclass(frozen=True, slots=True)
class RouteTarget:
    map_key: tuple[int, int]
    tiles: frozenset[tuple[int, int]]
    name: str = ""


@dataclass(frozen=True, slots=True)
class RoutePlan:
    start_key: tuple[int, int]
    start_tile: tuple[int, int]
    target: RouteTarget
    map_path: tuple[tuple[int, int], ...]
    next_transition: MapTransition | None
    same_map_plan: Plan | None
    next_action: str | None
    path_source: str = "cross_map_static_registry"

    @property
    def planned_path_length(self) -> int | None:
        if self.same_map_plan is None:
            return None
        return self.same_map_plan.planned_path_length


def outgoing_transitions(registry: MapRegistry, map_key: tuple[int, int]) -> tuple[MapTransition, ...]:
    map_spec = registry.require(map_key)
    transitions: list[MapTransition] = []
    for warp in map_spec.warps:
        if warp.dest_key is None:
            continue
        transitions.append(
            MapTransition(
                kind="warp",
                source_key=map_key,
                dest_key=warp.dest_key,
                source_tiles=(warp.source,),
                dest_tiles=(warp.dest,) if warp.dest is not None else (),
                warp=warp,
            )
        )
    for connection in map_spec.connections:
        if connection.dest_key is None:
            continue
        transitions.append(_connection_transition(registry, map_spec, connection))
    return tuple(transitions)


def connection_exit_action(direction: str) -> str | None:
    return {
        "north": "walk_up",
        "south": "walk_down",
        "west": "walk_left",
        "east": "walk_right",
    }.get(direction)


def warp_exit_action(map_spec: MapSpec, tile: tuple[int, int]) -> str | None:
    x, y = tile
    if y == 0:
        return "walk_up"
    if y == map_spec.height - 1:
        return "walk_down"
    if x == 0:
        return "walk_left"
    if x == map_spec.width - 1:
        return "walk_right"
    return None


def _connection_transition(
    registry: MapRegistry,
    source_map: MapSpec,
    connection: ConnectionSpec,
) -> MapTransition:
    if connection.dest_key is None:
        return MapTransition(
            kind="connection",
            source_key=source_map.key,
            dest_key=(-1, -1),
            source_tiles=(),
            dest_tiles=(),
            connection=connection,
        )
    dest_map = registry.require(connection.dest_key)
    tile_offset = connection.offset * 2
    pairs: list[tuple[tuple[int, int], tuple[int, int]]] = []
    if connection.direction == "north":
        for x in range(source_map.width):
            source = (x, 0)
            dest = (x - tile_offset, dest_map.height - 1)
            pairs.append((source, dest))
    elif connection.direction == "south":
        for x in range(source_map.width):
            source = (x, source_map.height - 1)
            dest = (x - tile_offset, 0)
            pairs.append((source, dest))
    elif connection.direction == "west":
        for y in range(source_map.height):
            source = (0, y)
            dest = (dest_map.width - 1, y - tile_offset)
            pairs.append((source, dest))
    elif connection.direction == "east":
        for y in range(source_map.height):
            source = (source_map.width - 1, y)
            dest = (0, y - tile_offset)
            pairs.append((source, dest))

    filtered = [
        (source, dest)
        for source, dest in pairs
        if source_map.contains(source)
        and dest_map.contains(dest)
        and source_map.is_walkable(source)
        and dest_map.is_walkable(dest)
    ]
    return MapTransition(
        kind="connection",
        source_key=source_map.key,
        dest_key=connection.dest_key,
        source_tiles=tuple(source for source, _ in filtered),
        dest_tiles=tuple(dest for _, dest in filtered),
        connection=connection,
    )


def find_map_path(
    registry: MapRegistry,
    start_key: tuple[int, int],
    target_keys: Iterable[tuple[int, int]],
) -> tuple[tuple[int, int], ...] | None:
    targets = set(target_keys)
    if start_key in targets:
        return (start_key,)
    queue: deque[tuple[tuple[int, int], tuple[tuple[int, int], ...]]] = deque(
        [(start_key, (start_key,))]
    )
    seen = {start_key}
    while queue:
        key, path = queue.popleft()
        for transition in outgoing_transitions(registry, key):
            if not transition.source_tiles:
                continue
            dest = transition.dest_key
            if dest in seen:
                continue
            next_path = path + (dest,)
            if dest in targets:
                return next_path
            seen.add(dest)
            queue.append((dest, next_path))
    return None


def transition_between(
    registry: MapRegistry,
    source_key: tuple[int, int],
    dest_key: tuple[int, int],
) -> MapTransition | None:
    for transition in outgoing_transitions(registry, source_key):
        if transition.dest_key == dest_key:
            return transition
    return None


def plan_route_to_target(
    registry: MapRegistry,
    start_key: tuple[int, int],
    start_tile: tuple[int, int],
    target: RouteTarget,
    blocked_edges_by_map: Mapping[tuple[int, int], frozenset[BlockedEdge]] | None = None,
) -> RoutePlan | None:
    blocked_edges_by_map = blocked_edges_by_map or {}
    map_path = find_map_path(registry, start_key, {target.map_key})
    if map_path is None:
        return None

    current_map = registry.require(start_key)
    if start_key == target.map_key:
        same_map_plan = plan_to_any(current_map, start_tile, target.tiles, blocked_edges_by_map.get(start_key, frozenset()))
        if same_map_plan is None:
            return None
        return RoutePlan(
            start_key=start_key,
            start_tile=start_tile,
            target=target,
            map_path=map_path,
            next_transition=None,
            same_map_plan=same_map_plan,
            next_action=same_map_plan.next_step,
            path_source="same_map_static_collision",
        )

    transition = transition_between(registry, start_key, map_path[1])
    if transition is None:
        return None
    if not transition.source_tiles:
        return RoutePlan(
            start_key=start_key,
            start_tile=start_tile,
            target=target,
            map_path=map_path,
            next_transition=transition,
            same_map_plan=None,
            next_action=None,
            path_source="unsupported_transition",
        )

    transition_source_tiles = transition.source_tiles
    if transition.connection is not None:
        exit_action = connection_exit_action(transition.connection.direction)
        if exit_action is not None:
            exit_direction = exit_action.removeprefix("walk_")
            blocked_edges = blocked_edges_by_map.get(start_key, frozenset())
            transition_source_tiles = tuple(
                tile for tile in transition.source_tiles if (tile, exit_direction) not in blocked_edges
            )
            if not transition_source_tiles:
                return None
            if transition_source_tiles != transition.source_tiles:
                transition = MapTransition(
                    kind=transition.kind,
                    source_key=transition.source_key,
                    dest_key=transition.dest_key,
                    source_tiles=transition_source_tiles,
                    dest_tiles=transition.dest_tiles,
                    warp=transition.warp,
                    connection=transition.connection,
                )
    same_map_plan = plan_to_any(current_map, start_tile, transition_source_tiles, blocked_edges_by_map.get(start_key, frozenset()))
    if same_map_plan is None:
        return None
    next_action = same_map_plan.next_step
    if next_action is None and transition.connection is not None:
        next_action = connection_exit_action(transition.connection.direction)
    if next_action is None and transition.warp is not None and start_tile in transition.source_tiles:
        next_action = warp_exit_action(current_map, start_tile)
    return RoutePlan(
        start_key=start_key,
        start_tile=start_tile,
        target=target,
        map_path=map_path,
        next_transition=transition,
        same_map_plan=same_map_plan,
        next_action=next_action,
    )
