"""Static map graph types for Navigation V2."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal


Direction = Literal["up", "down", "left", "right"]

DELTAS: dict[Direction, tuple[int, int]] = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}


@dataclass(frozen=True, slots=True)
class Location:
    map_group: int
    map_number: int
    x: int
    y: int
    map_name: str = ""

    @property
    def tile(self) -> tuple[int, int]:
        return (self.x, self.y)

    @property
    def key(self) -> tuple[int, int]:
        return (self.map_group, self.map_number)


@dataclass(frozen=True, slots=True)
class WarpSpec:
    source: tuple[int, int]
    dest_map_group: int | None
    dest_map_number: int | None
    dest_map_const: str
    dest_warp_id: int
    dest: tuple[int, int] | None = None

    @property
    def dest_key(self) -> tuple[int, int] | None:
        if self.dest_map_group is None or self.dest_map_number is None:
            return None
        return (self.dest_map_group, self.dest_map_number)


@dataclass(frozen=True, slots=True)
class ConnectionSpec:
    direction: str
    dest_map_group: int | None
    dest_map_number: int | None
    dest_map_const: str
    offset: int

    @property
    def dest_key(self) -> tuple[int, int] | None:
        if self.dest_map_group is None or self.dest_map_number is None:
            return None
        return (self.dest_map_group, self.dest_map_number)


@dataclass(frozen=True, slots=True)
class MapSpec:
    map_group: int
    map_number: int
    name: str
    rows: tuple[str, ...] = ()
    passable_tiles: frozenset[str] = frozenset({".", "g", "f"})
    terrain_rows: tuple[str, ...] = ()
    tile_width: int | None = None
    tile_height: int | None = None
    map_const: str = ""
    label: str = ""
    tileset: str = ""
    environment: str = ""
    landmark: str = ""
    warps: tuple[WarpSpec, ...] = ()
    connections: tuple[ConnectionSpec, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def width(self) -> int:
        if self.tile_width is not None:
            return self.tile_width
        return max((len(row) for row in self.rows), default=0)

    @property
    def height(self) -> int:
        if self.tile_height is not None:
            return self.tile_height
        return len(self.rows)

    @property
    def key(self) -> tuple[int, int]:
        return (self.map_group, self.map_number)

    @property
    def walkable(self) -> frozenset[tuple[int, int]]:
        if self.terrain_rows:
            return frozenset(
                (x, y)
                for y, row in enumerate(self.terrain_rows)
                for x, tile in enumerate(row)
                if tile in self.passable_tiles
            )
        if not self.rows:
            return frozenset()
        return frozenset(
            (x, y)
            for y, row in enumerate(self.rows)
            for x, tile in enumerate(row)
            if tile in self.passable_tiles
        )

    def contains(self, tile: tuple[int, int]) -> bool:
        x, y = tile
        if not self.rows:
            return 0 <= x < self.width and 0 <= y < self.height
        return 0 <= y < self.height and 0 <= x < len(self.rows[y])

    def is_walkable(self, tile: tuple[int, int]) -> bool:
        return tile in self.walkable

    def neighbors(self, tile: tuple[int, int]) -> Iterable[tuple[tuple[int, int], Direction]]:
        for direction, (dx, dy) in DELTAS.items():
            neighbor = (tile[0] + dx, tile[1] + dy)
            if self.is_walkable(neighbor):
                yield neighbor, direction

    def collision_map(self) -> dict[tuple[int, int], bool]:
        return {tile: True for tile in self.walkable}
