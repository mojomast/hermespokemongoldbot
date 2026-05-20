"""Small trusted Pokemon Gold data tables for early V2 policies.

These tables are intentionally partial. Unknown IDs stay visible as numeric IDs
instead of being guessed, which keeps policy decisions auditable while the full
Gold database is added incrementally.
"""

from __future__ import annotations


SPECIES_NAMES: dict[int, str] = {
    0x10: "Pidgey",
    0x13: "Rattata",
    0x15: "Spearow",
    0x1D: "Nidoran F",
    0x20: "Nidoran M",
    0x29: "Zubat",
    0x3C: "Poliwag",
    0x42: "Machop",
    0x48: "Tentacool",
    0x4A: "Geodude",
    0x5C: "Gastly",
    0x81: "Magikarp",
    0x98: "Chikorita",
    0x9B: "Cyndaquil",
    0x9E: "Totodile",
    0xA1: "Sentret",
    0xA3: "Hoothoot",
    0xA5: "Ledyba",
    0xA7: "Spinarak",
    0xB3: "Mareep",
    0xB9: "Marill",
    0xBB: "Sudowoodo",
    0xC2: "Wooper",
    0xC7: "Slowking",
}

ITEM_NAMES: dict[int, str] = {
    0x02: "Ultra Ball",
    0x04: "Great Ball",
    0x05: "Poke Ball",
    0x12: "Potion",
    0x13: "Antidote",
    0x14: "Burn Heal",
    0x15: "Ice Heal",
    0x16: "Awakening",
    0x17: "Parlyz Heal",
    0x18: "Full Restore",
    0x20: "Escape Rope",
    0x22: "Repel",
}

BALL_ITEM_IDS: frozenset[int] = frozenset({0x02, 0x04, 0x05})
HEALING_ITEM_IDS: frozenset[int] = frozenset({0x12, 0x18})
STATUS_HEAL_ITEM_IDS: frozenset[int] = frozenset({0x13, 0x14, 0x15, 0x16, 0x17})
DANGEROUS_ENEMY_MOVE_IDS: frozenset[int] = frozenset({18, 46, 120, 153})

HM_ROLE_SPECIES: dict[str, frozenset[str]] = {
    "fly": frozenset({"Pidgey", "Spearow", "Hoothoot"}),
    "surf": frozenset({"Totodile", "Poliwag", "Tentacool", "Marill", "Wooper"}),
    "strength": frozenset({"Geodude", "Machop", "Sudowoodo"}),
    "flash": frozenset({"Chikorita", "Mareep", "Gastly"}),
}

CATCH_PRIORITY_SPECIES: frozenset[str] = frozenset({
    "Pidgey",
    "Spearow",
    "Hoothoot",
    "Mareep",
    "Geodude",
    "Wooper",
    "Poliwag",
    "Tentacool",
    "Gastly",
})


def species_name(species_id: int | None, fallback: str | None = None) -> str:
    if species_id is None:
        return fallback or "Unknown"
    return SPECIES_NAMES.get(species_id, fallback or f"Species {species_id:#04x}")


def item_name(item_id: int | None, fallback: str | None = None) -> str:
    if item_id is None:
        return fallback or "Unknown item"
    return ITEM_NAMES.get(item_id, fallback or f"Item {item_id:#04x}")
