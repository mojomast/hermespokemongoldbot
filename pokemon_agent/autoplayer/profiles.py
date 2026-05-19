"""Game capability profiles for the unified autoplayer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CapabilitySet:
    structured_ram: bool
    map_registry: bool
    story_planner: bool
    battle_policy: bool
    menu_decoder: bool
    visual_fallback: bool = True


@dataclass(frozen=True, slots=True)
class GameProfile:
    game_id: str
    display_name: str
    generation: int
    reader: str
    capabilities: CapabilitySet
    map_registry: str | None = None
    story_module: str | None = None
    battle_policy: str | None = None


RED_BLUE_PROFILE = GameProfile(
    game_id="red_blue",
    display_name="Pokemon Red/Blue",
    generation=1,
    reader="PokemonRedReader",
    capabilities=CapabilitySet(
        structured_ram=True,
        map_registry=False,
        story_planner=False,
        battle_policy=False,
        menu_decoder=False,
    ),
)

YELLOW_PROFILE = GameProfile(
    game_id="yellow",
    display_name="Pokemon Yellow",
    generation=1,
    reader="PokemonYellowReader",
    capabilities=CapabilitySet(
        structured_ram=False,
        map_registry=False,
        story_planner=False,
        battle_policy=False,
        menu_decoder=False,
    ),
)

GOLD_SILVER_PROFILE = GameProfile(
    game_id="gold_silver",
    display_name="Pokemon Gold/Silver",
    generation=2,
    reader="PokemonGoldReader",
    map_registry="GOLD_MAP_REGISTRY",
    story_module="pokemon_agent.gameplay.story",
    battle_policy="gold_v2",
    capabilities=CapabilitySet(
        structured_ram=True,
        map_registry=True,
        story_planner=True,
        battle_policy=True,
        menu_decoder=True,
    ),
)

GENERIC_GB_PROFILE = GameProfile(
    game_id="generic_gb",
    display_name="Generic Game Boy Pokemon",
    generation=0,
    reader="GenericGameBoyReader",
    capabilities=CapabilitySet(
        structured_ram=False,
        map_registry=False,
        story_planner=False,
        battle_policy=False,
        menu_decoder=False,
    ),
)


def profile_for_game_type(game_type: str | None) -> GameProfile:
    normalized = (game_type or "").strip().lower().replace("-", "_")
    if normalized in {"red", "blue", "red_blue", "gen1"}:
        return RED_BLUE_PROFILE
    if normalized == "yellow":
        return YELLOW_PROFILE
    if normalized in {"gold", "silver", "gold_silver", "gen2"}:
        return GOLD_SILVER_PROFILE
    return GENERIC_GB_PROFILE
