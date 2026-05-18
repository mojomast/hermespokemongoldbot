"""Import pokecrystal map metadata into Navigation V2 specs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .map_registry import MapRegistry
from .world_graph import ConnectionSpec, MapSpec, WarpSpec


DEFAULT_DATA_PATH = Path(__file__).with_name("data") / "pokecrystal_maps.json"


def _clean_line(line: str) -> str:
    return line.split(";", 1)[0].strip()


def _parts(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",")]


def parse_map_constants(text: str) -> list[dict[str, Any]]:
    maps: list[dict[str, Any]] = []
    group = 0
    number = 0
    group_name = ""
    for line in text.splitlines():
        line = _clean_line(line)
        if not line:
            continue
        match = re.match(r"newgroup\s+([A-Z0-9_]+)", line)
        if match:
            group += 1
            number = 0
            group_name = match.group(1)
            continue
        match = re.match(r"map_const\s+([A-Z0-9_]+),\s*(\d+),\s*(\d+)", line)
        if match:
            number += 1
            map_const, width_blocks, height_blocks = match.groups()
            maps.append(
                {
                    "map_group": group,
                    "map_number": number,
                    "group_name": group_name,
                    "map_const": map_const,
                    "width_blocks": int(width_blocks),
                    "height_blocks": int(height_blocks),
                    "tile_width": int(width_blocks) * 2,
                    "tile_height": int(height_blocks) * 2,
                }
            )
    return maps


def parse_map_table(text: str) -> dict[tuple[int, int], dict[str, str]]:
    entries: dict[tuple[int, int], dict[str, str]] = {}
    group = 0
    number = 0
    for line in text.splitlines():
        line = _clean_line(line)
        if line.startswith("MapGroup_"):
            group += 1
            number = 0
            continue
        if not line.startswith("map "):
            continue
        number += 1
        parts = _parts(line[4:])
        if len(parts) < 8:
            continue
        entries[(group, number)] = {
            "label": parts[0],
            "tileset": parts[1],
            "environment": parts[2],
            "landmark": parts[3],
            "music": parts[4],
            "phone": parts[5],
            "palette": parts[6],
            "fishgroup": parts[7],
        }
    return entries


def parse_connections(text: str, const_to_key: dict[str, tuple[int, int]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    current_label = ""
    for line in text.splitlines():
        line = _clean_line(line)
        match = re.match(r"map_attributes\s+([A-Za-z0-9_]+),\s*([A-Z0-9_]+),", line)
        if match:
            current_label = match.group(1)
            result.setdefault(current_label, [])
            continue
        match = re.match(r"connection\s+(north|south|west|east),\s*([A-Za-z0-9_]+),\s*([A-Z0-9_]+),\s*(-?\d+)", line)
        if match and current_label:
            direction, dest_label, dest_const, offset = match.groups()
            dest_key = const_to_key.get(dest_const)
            result.setdefault(current_label, []).append(
                {
                    "direction": direction,
                    "dest_label": dest_label,
                    "dest_map_const": dest_const,
                    "dest_map_group": dest_key[0] if dest_key else None,
                    "dest_map_number": dest_key[1] if dest_key else None,
                    "offset": int(offset),
                }
            )
    return result


def parse_block_table(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    labels: list[str] = []
    for raw_line in text.splitlines():
        line = _clean_line(raw_line)
        if not line:
            continue
        match = re.match(r"([A-Za-z0-9_]+)_Blocks:", line)
        if match:
            labels.append(match.group(1))
            continue
        match = re.match(r'INCBIN\s+"([^"]+)"', line)
        if match:
            path = match.group(1)
            for label in labels:
                result[label] = path
            labels = []
    return result


def parse_tileset_constants(text: str) -> list[str]:
    result: list[str] = []
    for line in text.splitlines():
        line = _clean_line(line)
        match = re.match(r"const\s+(TILESET_[A-Z0-9_]+)", line)
        if match:
            result.append(match.group(1))
    return result


def parse_tileset_table(text: str, constants: list[str]) -> dict[str, str]:
    labels: list[str] = []
    for line in text.splitlines():
        line = _clean_line(line)
        match = re.match(r"tileset\s+(Tileset[A-Za-z0-9]+)", line)
        if match:
            labels.append(match.group(1))
    if labels and labels[0] == "Tileset0":
        labels = labels[1:]
    return {constant: label for constant, label in zip(constants, labels)}


def tileset_collision_path(label: str) -> str:
    name = label.removeprefix("Tileset").replace("PokeCom", "Pokecom")
    words = re.findall(r"[A-Z]+(?=[A-Z][a-z]|$)|[A-Z]?[a-z]+|\d+", name)
    return "data/tilesets/" + "_".join(word.lower() for word in words) + "_collision.asm"


def parse_collision_table(text: str) -> list[tuple[str, str, str, str]]:
    table: list[tuple[str, str, str, str]] = []
    for line in text.splitlines():
        line = _clean_line(line)
        match = re.match(r"tilecoll\s+([^,]+),\s*([^,]+),\s*([^,]+),\s*([^,]+)", line)
        if match:
            table.append(tuple(part.strip().upper() for part in match.groups()))  # type: ignore[arg-type]
    return table


def collision_token_to_terrain(token: str) -> str:
    token = token.upper().removeprefix("COLL_")
    if token in {"FLOOR", "ICE", "LADDER", "STAIRCASE", "CAVE", "DOOR", "PIT", "WARP_PANEL"}:
        return "."
    if token.startswith("WARP_CARPET"):
        return "."
    if "GRASS" in token:
        return "g"
    if token in {"WATER", "WATER_21", "WATERFALL", "WHIRLPOOL"} or token.startswith("CURRENT_"):
        return "w"
    if token.startswith("HOP_") or token.startswith("WALK_"):
        return "."
    return "#"


def terrain_rows_from_blockdata(
    blockdata_hex: str,
    width_blocks: int,
    height_blocks: int,
    collision_table: list[tuple[str, str, str, str]],
) -> tuple[str, ...]:
    if not blockdata_hex or not collision_table:
        return ()
    block_ids = bytes.fromhex(blockdata_hex)
    if len(block_ids) != width_blocks * height_blocks:
        return ()
    rows: list[list[str]] = [[] for _ in range(height_blocks * 2)]
    for block_y in range(height_blocks):
        for block_x in range(width_blocks):
            block_id = block_ids[block_y * width_blocks + block_x]
            quadrants = collision_table[block_id] if block_id < len(collision_table) else ("FF", "FF", "FF", "FF")
            chars = [collision_token_to_terrain(token) for token in quadrants]
            rows[block_y * 2].extend(chars[:2])
            rows[block_y * 2 + 1].extend(chars[2:])
    return tuple("".join(row) for row in rows)


def parse_map_events(text: str, const_to_key: dict[str, tuple[int, int]]) -> dict[str, Any]:
    warps: list[dict[str, Any]] = []
    bg_events: list[dict[str, Any]] = []
    coord_events: list[dict[str, Any]] = []
    object_events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = _clean_line(line)
        match = re.match(r"warp_event\s+(-?\d+),\s*(-?\d+),\s*([A-Z0-9_]+|NONE),\s*(-?\d+)", line)
        if match:
            x, y, dest_const, dest_warp_id = match.groups()
            dest_key = const_to_key.get(dest_const)
            warps.append(
                {
                    "x": int(x),
                    "y": int(y),
                    "dest_map_const": dest_const,
                    "dest_map_group": dest_key[0] if dest_key else None,
                    "dest_map_number": dest_key[1] if dest_key else None,
                    "dest_warp_id": int(dest_warp_id),
                }
            )
            continue
        match = re.match(r"bg_event\s+(-?\d+),\s*(-?\d+),\s*([^,]+),\s*(.+)", line)
        if match:
            x, y, kind, script = match.groups()
            bg_events.append({"x": int(x), "y": int(y), "kind": kind.strip(), "script": script.strip()})
            continue
        match = re.match(r"coord_event\s+(-?\d+),\s*(-?\d+),\s*([^,]+),\s*(.+)", line)
        if match:
            x, y, scene, script = match.groups()
            coord_events.append({"x": int(x), "y": int(y), "scene": scene.strip(), "script": script.strip()})
            continue
        match = re.match(r"object_event\s+(-?\d+),\s*(-?\d+),\s*(.+)", line)
        if match:
            x, y, rest = match.groups()
            object_events.append({"x": int(x), "y": int(y), "raw": rest.strip()})
    return {"warps": warps, "bg_events": bg_events, "coord_events": coord_events, "object_events": object_events}


def build_dataset(
    map_constants_text: str,
    map_table_text: str,
    attributes_text: str,
    map_event_texts: dict[str, str],
    blocks_text: str = "",
    blockdata_hex: dict[str, str] | None = None,
    tileset_constants_text: str = "",
    tilesets_text: str = "",
    collision_texts: dict[str, str] | None = None,
) -> dict[str, Any]:
    constants = parse_map_constants(map_constants_text)
    table = parse_map_table(map_table_text)
    const_to_key = {entry["map_const"]: (entry["map_group"], entry["map_number"]) for entry in constants}
    connections = parse_connections(attributes_text, const_to_key)
    block_paths = parse_block_table(blocks_text) if blocks_text else {}
    blockdata_hex = blockdata_hex or {}
    collision_texts = collision_texts or {}
    tileset_labels: dict[str, str] = {}
    if tileset_constants_text and tilesets_text:
        tileset_labels = parse_tileset_table(tilesets_text, parse_tileset_constants(tileset_constants_text))
    maps: list[dict[str, Any]] = []
    for entry in constants:
        meta = table.get((entry["map_group"], entry["map_number"]), {})
        label = meta.get("label", "")
        events = parse_map_events(map_event_texts.get(label, ""), const_to_key) if label else {}
        block_path = block_paths.get(label, "")
        block_hex = blockdata_hex.get(block_path, "")
        tileset_label = tileset_labels.get(meta.get("tileset", ""), "")
        collision_path = tileset_collision_path(tileset_label) if tileset_label else ""
        collision_table = parse_collision_table(collision_texts.get(collision_path, "")) if collision_path else []
        terrain_rows = terrain_rows_from_blockdata(
            block_hex,
            int(entry["width_blocks"]),
            int(entry["height_blocks"]),
            collision_table,
        )
        maps.append(
            {
                **entry,
                **meta,
                **events,
                "connections": connections.get(label, []),
                "block_path": block_path,
                "blockdata_hex": block_hex,
                "collision_path": collision_path,
                "terrain_rows": terrain_rows,
            }
        )
    _resolve_warp_destinations(maps)
    return {"source": "pret/pokecrystal", "map_count": len(maps), "maps": maps}


def _resolve_warp_destinations(maps: list[dict[str, Any]]) -> None:
    by_key = {(m["map_group"], m["map_number"]): m for m in maps}
    for map_data in maps:
        for warp in map_data.get("warps", []):
            dest_key = (warp.get("dest_map_group"), warp.get("dest_map_number"))
            dest_warp_id = warp.get("dest_warp_id")
            dest_map = by_key.get(dest_key)
            if not dest_map or not isinstance(dest_warp_id, int) or dest_warp_id < 1:
                continue
            dest_warps = dest_map.get("warps", [])
            if dest_warp_id <= len(dest_warps):
                dest_warp = dest_warps[dest_warp_id - 1]
                warp["dest_x"] = dest_warp.get("x")
                warp["dest_y"] = dest_warp.get("y")


def load_dataset(path: Path = DEFAULT_DATA_PATH) -> dict[str, Any]:
    return json.loads(path.read_text())


def map_spec_from_dict(data: dict[str, Any]) -> MapSpec:
    warps = tuple(
        WarpSpec(
            source=(int(warp["x"]), int(warp["y"])),
            dest_map_group=warp.get("dest_map_group"),
            dest_map_number=warp.get("dest_map_number"),
            dest_map_const=str(warp.get("dest_map_const", "")),
            dest_warp_id=int(warp.get("dest_warp_id", 0)),
            dest=(warp["dest_x"], warp["dest_y"]) if "dest_x" in warp and "dest_y" in warp else None,
        )
        for warp in data.get("warps", [])
    )
    connections = tuple(
        ConnectionSpec(
            direction=str(connection.get("direction", "")),
            dest_map_group=connection.get("dest_map_group"),
            dest_map_number=connection.get("dest_map_number"),
            dest_map_const=str(connection.get("dest_map_const", "")),
            offset=int(connection.get("offset", 0)),
        )
        for connection in data.get("connections", [])
    )
    return MapSpec(
        map_group=int(data["map_group"]),
        map_number=int(data["map_number"]),
        name=str(data.get("label") or data.get("map_const", "")),
        terrain_rows=tuple(data.get("terrain_rows", ())),
        tile_width=int(data.get("tile_width", 0)),
        tile_height=int(data.get("tile_height", 0)),
        map_const=str(data.get("map_const", "")),
        label=str(data.get("label", "")),
        tileset=str(data.get("tileset", "")),
        environment=str(data.get("environment", "")),
        landmark=str(data.get("landmark", "")),
        warps=warps,
        connections=connections,
        metadata={
            "width_blocks": data.get("width_blocks"),
            "height_blocks": data.get("height_blocks"),
            "group_name": data.get("group_name"),
            "block_path": data.get("block_path", ""),
            "blockdata_hex": data.get("blockdata_hex", ""),
            "collision_path": data.get("collision_path", ""),
            "bg_events": data.get("bg_events", []),
            "coord_events": data.get("coord_events", []),
            "object_events": data.get("object_events", []),
        },
    )


def load_imported_gold_maps(path: Path = DEFAULT_DATA_PATH) -> tuple[MapSpec, ...]:
    dataset = load_dataset(path)
    return tuple(map_spec_from_dict(map_data) for map_data in dataset.get("maps", []))


def build_gold_registry(path: Path = DEFAULT_DATA_PATH) -> MapRegistry:
    return MapRegistry(load_imported_gold_maps(path))
