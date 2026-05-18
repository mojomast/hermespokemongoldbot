"""Navigation V2 primitives for Pokemon Gold."""

from .gold_maps import CHERRYGROVE_TARGETS, GOLD_MAP_REGISTRY, IMPORTED_GOLD_MAPS, ROUTE30, ROUTE31_TARGETS, build_gold_registry
from .map_registry import MapRegistry
from .navigator import Plan, plan_to_any
from .pokecrystal_importer import load_imported_gold_maps
from .route_planner import (
    MapTransition,
    RoutePlan,
    RouteTarget,
    connection_exit_action,
    find_map_path,
    outgoing_transitions,
    plan_route_to_target,
    transition_between,
)
from .world_graph import ConnectionSpec, Direction, Location, MapSpec, WarpSpec

__all__ = [
    "CHERRYGROVE_TARGETS",
    "Direction",
    "ConnectionSpec",
    "GOLD_MAP_REGISTRY",
    "IMPORTED_GOLD_MAPS",
    "Location",
    "MapTransition",
    "MapRegistry",
    "MapSpec",
    "Plan",
    "ROUTE30",
    "ROUTE31_TARGETS",
    "RoutePlan",
    "RouteTarget",
    "WarpSpec",
    "build_gold_registry",
    "connection_exit_action",
    "find_map_path",
    "load_imported_gold_maps",
    "outgoing_transitions",
    "plan_route_to_target",
    "plan_to_any",
    "transition_between",
]
