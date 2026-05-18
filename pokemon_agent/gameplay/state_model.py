"""Typed, normalized state model consumed by Navigation V2 gameplay policies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .gold_data import item_name, species_name


@dataclass(frozen=True, slots=True)
class Position:
    map_group: int | None
    map_number: int | None
    map_name: str
    x: int | None
    y: int | None
    raw_x: int | None = None
    raw_y: int | None = None

    @property
    def map_key(self) -> tuple[int, int] | None:
        if self.map_group is None or self.map_number is None:
            return None
        return (self.map_group, self.map_number)

    @property
    def tile(self) -> tuple[int, int] | None:
        if self.x is None or self.y is None:
            return None
        return (self.x, self.y)


@dataclass(frozen=True, slots=True)
class PartyMon:
    slot: int
    species_id: int | None
    species: str
    level: int | None
    hp: int | None
    max_hp: int | None
    moves: tuple[int, ...]

    @property
    def hp_ratio(self) -> float | None:
        if self.hp is None or not self.max_hp:
            return None
        return max(0.0, min(1.0, self.hp / self.max_hp))

    @property
    def fainted(self) -> bool:
        return self.hp == 0 if self.hp is not None else False


@dataclass(frozen=True, slots=True)
class BagItem:
    item_id: int
    name: str
    quantity: int


@dataclass(frozen=True, slots=True)
class BattleSnapshot:
    in_battle: bool
    type_id: int | None
    enemy_species_id: int | None
    enemy_species: str
    enemy_level: int | None
    enemy_hp: int | None = None
    enemy_max_hp: int | None = None
    enemy_status_raw: int | None = None
    enemy_status_condition: dict[str, Any] | None = None
    enemy_moves: tuple[int, ...] = ()

    @property
    def wild(self) -> bool:
        return self.in_battle and self.type_id == 1 and self.enemy_species_id not in (None, 0)

    @property
    def enemy_hp_ratio(self) -> float | None:
        if self.enemy_hp is None or not self.enemy_max_hp:
            return None
        return max(0.0, min(1.0, self.enemy_hp / self.enemy_max_hp))

    @property
    def enemy_has_status(self) -> bool:
        if not self.enemy_status_condition:
            return False
        return bool(self.enemy_status_condition.get("any"))


@dataclass(frozen=True, slots=True)
class MenuSnapshot:
    active: bool
    name: str | None
    confidence: str
    usable: bool
    cursor_label: str | None
    cursor_index: int | None
    cursor_xy: dict[str, int] | None
    selected_item_id: int | None
    visible_item_ids: tuple[int, ...]
    blocked_reason: str | None = None


@dataclass(frozen=True, slots=True)
class DialogSnapshot:
    active: bool
    confirmed: bool
    ambiguous: bool
    window_stack_plausible: bool | None
    window_stack_size: int | None


@dataclass(frozen=True, slots=True)
class StorySnapshot:
    has_starter: bool
    has_zephyr_badge: bool
    badge_count: int | None
    source: str
    event_flags_available: bool
    got_mystery_egg_from_mr_pokemon: bool = False
    gave_mystery_egg_to_elm: bool = False
    elm_called_about_stolen_pokemon: bool = False
    learned_to_catch_pokemon: bool = False


@dataclass(frozen=True, slots=True)
class GameSnapshot:
    position: Position
    facing: str | None
    party: tuple[PartyMon, ...]
    bag: tuple[BagItem, ...]
    battle: BattleSnapshot
    menu: MenuSnapshot
    dialog: DialogSnapshot
    story: StorySnapshot
    badges: tuple[str, ...]
    money: int | None
    has_starter: bool

    @property
    def lead(self) -> PartyMon | None:
        return self.party[0] if self.party else None

    @property
    def party_full(self) -> bool:
        return len(self.party) >= 6


def snapshot_from_state(state: dict[str, Any]) -> GameSnapshot:
    player = state.get("player") or {}
    pos = player.get("position") or {}
    flags = state.get("flags") or {}
    party = tuple(_party_mon(mon) for mon in (state.get("party") or []) if isinstance(mon, dict) and mon.get("trusted") is not False)
    bag = tuple(_bag_item(item) for item in (state.get("bag") or []) if isinstance(item, dict) and item.get("trusted") is not False)
    battle = _battle_snapshot(state.get("battle") or {})
    story = _story_snapshot(flags, player.get("badges") or [], bool(party))

    return GameSnapshot(
        position=Position(
            map_group=pos.get("map_group"),
            map_number=pos.get("map_number"),
            map_name=pos.get("map_name") or "Unknown",
            x=pos.get("actual_x", pos.get("x")),
            y=pos.get("actual_y", pos.get("y")),
            raw_x=pos.get("raw_x", pos.get("x")),
            raw_y=pos.get("raw_y", pos.get("y")),
        ),
        facing=player.get("facing"),
        party=party,
        bag=bag,
        battle=battle,
        menu=_menu_snapshot(state.get("menu") or {}),
        dialog=_dialog_snapshot(state.get("dialog") or {}),
        story=story,
        badges=tuple(player.get("badges") or []),
        money=player.get("money"),
        has_starter=story.has_starter,
    )


def _party_mon(mon: dict[str, Any]) -> PartyMon:
    species_id = mon.get("species_id")
    moves = tuple(int(move) for move in (mon.get("moves") or []) if isinstance(move, int))
    return PartyMon(
        slot=int(mon.get("slot") or 0),
        species_id=species_id,
        species=species_name(species_id, mon.get("species")),
        level=mon.get("level"),
        hp=mon.get("hp"),
        max_hp=mon.get("max_hp"),
        moves=moves,
    )


def _bag_item(item: dict[str, Any]) -> BagItem:
    item_id = int(item.get("item_id") or 0)
    return BagItem(
        item_id=item_id,
        name=item_name(item_id, item.get("item")),
        quantity=int(item.get("quantity") or 0),
    )


def _battle_snapshot(battle: dict[str, Any]) -> BattleSnapshot:
    species_id = battle.get("wild_species_id")
    moves = tuple(int(move) for move in (battle.get("enemy_moves") or []) if isinstance(move, int))
    return BattleSnapshot(
        in_battle=bool(battle.get("in_battle")),
        type_id=battle.get("type_id"),
        enemy_species_id=species_id,
        enemy_species=species_name(species_id),
        enemy_level=battle.get("enemy_level"),
        enemy_hp=battle.get("enemy_hp") if isinstance(battle.get("enemy_hp"), int) else None,
        enemy_max_hp=battle.get("enemy_max_hp") if isinstance(battle.get("enemy_max_hp"), int) else None,
        enemy_status_raw=battle.get("enemy_status_raw") if isinstance(battle.get("enemy_status_raw"), int) else None,
        enemy_status_condition=battle.get("enemy_status_condition") if isinstance(battle.get("enemy_status_condition"), dict) else None,
        enemy_moves=moves,
    )


def _menu_snapshot(menu: dict[str, Any]) -> MenuSnapshot:
    confidence = str(menu.get("confidence") or "none")
    active = menu.get("active") is True
    name = menu.get("name") or menu.get("menu")
    cursor = menu.get("cursor")
    cursor_label = cursor.lower() if isinstance(cursor, str) else None
    cursor_index = cursor if isinstance(cursor, int) else None
    cursor_xy = menu.get("cursor_xy") if isinstance(menu.get("cursor_xy"), dict) else None
    visible_item_ids = tuple(
        item["item_id"]
        for item in (menu.get("visible_items") or [])
        if isinstance(item, dict) and isinstance(item.get("item_id"), int)
    )
    blocked_reason = None
    if not active:
        blocked_reason = "inactive"
    elif confidence not in {"medium", "high"}:
        blocked_reason = "low_confidence"
    elif menu.get("needs_stronger_decode") is True:
        blocked_reason = "needs_stronger_decode"
    elif menu.get("window_stack_plausible") is False:
        blocked_reason = "implausible_window_stack"
    elif not name:
        blocked_reason = "missing_name"
    return MenuSnapshot(
        active=active,
        name=name,
        confidence=confidence,
        usable=blocked_reason is None,
        cursor_label=cursor_label,
        cursor_index=cursor_index,
        cursor_xy=cursor_xy,
        selected_item_id=menu.get("selected_item_id") if isinstance(menu.get("selected_item_id"), int) else None,
        visible_item_ids=visible_item_ids,
        blocked_reason=blocked_reason,
    )


def _dialog_snapshot(dialog: dict[str, Any]) -> DialogSnapshot:
    active = dialog.get("active") is True
    plausible = dialog.get("window_stack_plausible")
    return DialogSnapshot(
        active=active,
        confirmed=active and plausible is not False,
        ambiguous=active and plausible is False,
        window_stack_plausible=plausible if isinstance(plausible, bool) else None,
        window_stack_size=dialog.get("window_stack_size") if isinstance(dialog.get("window_stack_size"), int) else None,
    )


def _story_snapshot(flags: dict[str, Any], badges: list[str], has_party: bool) -> StorySnapshot:
    derived = flags.get("derived_story_flags") if isinstance(flags.get("derived_story_flags"), dict) else {}
    has_starter = bool(derived.get("has_starter") if "has_starter" in derived else flags.get("has_starter") or has_party)
    return StorySnapshot(
        has_starter=has_starter,
        has_zephyr_badge=bool(derived.get("has_zephyr_badge") or "Zephyr" in badges),
        badge_count=derived.get("badge_count") if isinstance(derived.get("badge_count"), int) else len(badges),
        source=str(derived.get("source") or "derived_from_party_and_badges"),
        event_flags_available=bool(derived.get("event_flags_available")),
        got_mystery_egg_from_mr_pokemon=bool(derived.get("got_mystery_egg_from_mr_pokemon")),
        gave_mystery_egg_to_elm=bool(derived.get("gave_mystery_egg_to_elm")),
        elm_called_about_stolen_pokemon=bool(derived.get("elm_called_about_stolen_pokemon")),
        learned_to_catch_pokemon=bool(derived.get("learned_to_catch_pokemon")),
    )
