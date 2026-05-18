"""Pokemon Gold map specs for Navigation V2."""

from __future__ import annotations

from .map_registry import MapRegistry
from .pokecrystal_importer import build_gold_registry, load_imported_gold_maps
from .world_graph import MapSpec


# Route 30 upper path to Route 31. These rows are copied from the legacy V1
# grid so V2 starts with the same observed collision surface, but uses it as a
# static map spec instead of scattered route-specific macros.
ROUTE30_ROWS: tuple[str, ...] = (
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


ROUTE30 = MapSpec(
    map_group=26,
    map_number=1,
    name="Route 30",
    rows=ROUTE30_ROWS,
)

ROUTE31_TARGETS: frozenset[tuple[int, int]] = frozenset({(4, 0), (5, 0)})
CHERRYGROVE_TARGETS: frozenset[tuple[int, int]] = frozenset({(6, 53), (7, 53)})

IMPORTED_GOLD_MAPS: tuple[MapSpec, ...] = load_imported_gold_maps()
GOLD_MAP_REGISTRY: MapRegistry = build_gold_registry()

# Keep the hand-authored Route 30 collision grid available for same-map V2 tests
# while the imported registry provides full-game metadata, dimensions, warps, and
# connections keyed by runtime map group/number.
MAPS: dict[tuple[int, int], MapSpec] = dict(GOLD_MAP_REGISTRY.maps_by_key)
MAPS[ROUTE30.key] = ROUTE30
