#!/usr/bin/env python3
"""Generate Navigation V2 map metadata from pret/pokecrystal."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pokemon_agent.navigation.pokecrystal_importer import (  # noqa: E402
    build_dataset,
    parse_block_table,
    parse_tileset_constants,
    parse_tileset_table,
    parse_map_table,
    tileset_collision_path,
)


BASE = "https://raw.githubusercontent.com/pret/pokecrystal/master"
OUTPUT = ROOT / "pokemon_agent" / "navigation" / "data" / "pokecrystal_maps.json"


def fetch_text(url: str) -> str:
    with urlopen(url, timeout=60) as response:
        return response.read().decode("utf-8")


def fetch_bytes(url: str) -> bytes:
    with urlopen(url, timeout=60) as response:
        return response.read()


def main() -> int:
    map_constants = fetch_text(f"{BASE}/constants/map_constants.asm")
    map_table = fetch_text(f"{BASE}/data/maps/maps.asm")
    attributes = fetch_text(f"{BASE}/data/maps/attributes.asm")
    blocks = fetch_text(f"{BASE}/data/maps/blocks.asm")
    tileset_constants = fetch_text(f"{BASE}/constants/tileset_constants.asm")
    tilesets = fetch_text(f"{BASE}/data/tilesets.asm")
    labels = sorted({entry["label"] for entry in parse_map_table(map_table).values() if entry.get("label")})
    event_texts = {label: fetch_text(f"{BASE}/maps/{label}.asm") for label in labels}
    block_paths = sorted(set(parse_block_table(blocks).values()))
    blockdata_hex = {path: fetch_bytes(f"{BASE}/{path}").hex() for path in block_paths}
    tileset_labels = parse_tileset_table(tilesets, parse_tileset_constants(tileset_constants))
    collision_paths = sorted({tileset_collision_path(label) for label in tileset_labels.values()})
    collision_texts = {path: fetch_text(f"{BASE}/{path}") for path in collision_paths}
    dataset = build_dataset(
        map_constants,
        map_table,
        attributes,
        event_texts,
        blocks,
        blockdata_hex,
        tileset_constants,
        tilesets,
        collision_texts,
    )
    OUTPUT.write_text(json.dumps(dataset, indent=2, sort_keys=True) + "\n")
    print(f"wrote {dataset['map_count']} maps to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
