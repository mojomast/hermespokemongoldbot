"""Map registry for Navigation V2."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from .world_graph import MapSpec


class MapRegistry:
    """Indexed collection of static map specs."""

    def __init__(self, maps: Iterable[MapSpec]):
        by_key: dict[tuple[int, int], MapSpec] = {}
        by_const: dict[str, MapSpec] = {}
        by_name: dict[str, list[MapSpec]] = defaultdict(list)
        for map_spec in maps:
            if map_spec.key in by_key:
                raise ValueError(f"duplicate map key: {map_spec.key}")
            by_key[map_spec.key] = map_spec
            if map_spec.map_const:
                by_const[map_spec.map_const] = map_spec
            by_name[map_spec.name].append(map_spec)
        self.maps_by_key = by_key
        self.maps_by_const = by_const
        self._maps_by_name = {name: tuple(values) for name, values in by_name.items()}

    def get(self, key: tuple[int, int]) -> MapSpec | None:
        return self.maps_by_key.get(key)

    def require(self, key: tuple[int, int]) -> MapSpec:
        map_spec = self.get(key)
        if map_spec is None:
            raise KeyError(f"unknown map key: {key}")
        return map_spec

    def get_const(self, map_const: str) -> MapSpec | None:
        return self.maps_by_const.get(map_const)

    def by_name(self, name: str) -> tuple[MapSpec, ...]:
        return self._maps_by_name.get(name, ())

    def contains(self, key: tuple[int, int]) -> bool:
        return key in self.maps_by_key

    def all_maps(self) -> tuple[MapSpec, ...]:
        return tuple(self.maps_by_key.values())

    def validate_warp_destinations(self) -> list[str]:
        errors: list[str] = []
        for map_spec in self.all_maps():
            for index, warp in enumerate(map_spec.warps, start=1):
                if warp.dest_key is None:
                    errors.append(f"{map_spec.map_const} warp {index} unresolved {warp.dest_map_const}")
                elif warp.dest_key not in self.maps_by_key:
                    errors.append(f"{map_spec.map_const} warp {index} missing {warp.dest_key}")
            for connection in map_spec.connections:
                if connection.dest_key is None:
                    errors.append(f"{map_spec.map_const} connection unresolved {connection.dest_map_const}")
                elif connection.dest_key not in self.maps_by_key:
                    errors.append(f"{map_spec.map_const} connection missing {connection.dest_key}")
        return errors
