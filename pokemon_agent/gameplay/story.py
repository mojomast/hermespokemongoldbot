"""High-level Pokemon Gold story objective skeleton for V2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pokemon_agent.gameplay.state_model import snapshot_from_state
from pokemon_agent.navigation import GOLD_MAP_REGISTRY, RouteTarget, outgoing_transitions, plan_route_to_target


@dataclass(frozen=True, slots=True)
class StoryObjective:
    key: str
    title: str
    success_badge: str | None = None
    required_items: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True, slots=True)
class StoryDecision:
    objective_key: str
    objective_title: str
    target: RouteTarget | None
    reason: str
    confidence: str = "derived"


JOHTO_MAIN_OBJECTIVES: tuple[StoryObjective, ...] = (
    StoryObjective("get_starter", "Choose starter in Elm's Lab", notes="Verify party_count > 0."),
    StoryObjective("mr_pokemon", "Visit Mr. Pokemon and return to Elm", notes="Needs Route 30/Cherrygrove navigation and rival battle handling."),
    StoryObjective("falkner", "Reach Violet City and beat Falkner", success_badge="Zephyr"),
    StoryObjective("bugsy", "Clear Slowpoke Well and beat Bugsy", success_badge="Hive"),
    StoryObjective("whitney", "Reach Goldenrod and beat Whitney", success_badge="Plain"),
    StoryObjective("morty", "Reach Ecruteak and beat Morty", success_badge="Fog"),
    StoryObjective("chuck", "Reach Cianwood and beat Chuck", success_badge="Storm", required_items=("Surf",)),
    StoryObjective("jasmine", "Return medicine and beat Jasmine", success_badge="Mineral"),
    StoryObjective("pryce", "Clear Lake of Rage/Rocket Hideout and beat Pryce", success_badge="Glacier"),
    StoryObjective("clair", "Reach Blackthorn, beat Clair, clear Dragon's Den", success_badge="Rising"),
    StoryObjective("elite_four", "Cross Victory Road and beat the Elite Four"),
    StoryObjective("kanto_badges", "Collect all Kanto badges"),
    StoryObjective("red", "Reach Mt. Silver and defeat Red"),
)


STARTER_TARGET = RouteTarget(
    map_key=(24, 5),
    tiles=frozenset({(7, 4)}),
    name="stand below Totodile's Poke Ball",
)
NEW_BARK_AFTER_STARTER_TARGET = RouteTarget(
    map_key=(24, 4),
    tiles=frozenset({(6, 3)}),
    name="New Bark Town outside Elm's Lab",
)
ROUTE29_TARGET = RouteTarget(
    map_key=(24, 3),
    tiles=frozenset({(59, 8), (59, 9)}),
    name="Route 29 east edge",
)
CHERRYGROVE_TARGET = RouteTarget(
    map_key=(26, 3),
    tiles=frozenset({(39, 6), (39, 7)}),
    name="Cherrygrove City east edge",
)
ROUTE30_TARGET = RouteTarget(
    map_key=(26, 1),
    tiles=frozenset({(6, 53), (7, 53)}),
    name="Route 30 south edge",
)
MR_POKEMON_HOUSE_TARGET = RouteTarget(
    map_key=(26, 10),
    tiles=frozenset({(2, 5), (3, 5), (3, 6)}),
    name="Mr. Pokemon's house interior",
)
ELMS_LAB_RETURN_TARGET = RouteTarget(
    map_key=(24, 5),
    tiles=frozenset({(5, 3)}),
    name="Elm's Lab return tile",
)
ROUTE31_TARGET = RouteTarget(
    map_key=(26, 2),
    tiles=frozenset({(24, 17), (25, 17), (26, 17), (27, 17)}),
    name="Route 31 south edge",
)
VIOLET_GATE_TARGET = RouteTarget(
    map_key=(26, 11),
    tiles=frozenset({(9, 4), (9, 5)}),
    name="Route 31 Violet Gate",
)
VIOLET_CITY_TARGET = RouteTarget(
    map_key=(10, 5),
    tiles=frozenset({(39, 24), (39, 25)}),
    name="Violet City east gate exit",
)
VIOLET_MART_BUY_TARGET = RouteTarget(
    map_key=(10, 6),
    tiles=frozenset({(3, 3)}),
    name="Violet Mart clerk counter",
)
VIOLET_POKECENTER_HEAL_TARGET = RouteTarget(
    map_key=(10, 10),
    tiles=frozenset({(3, 3), (4, 3)}),
    name="Violet Pokemon Center nurse counter",
)
ROUTE31_GRIND_TARGET = RouteTarget(
    map_key=(26, 2),
    tiles=frozenset({(16, 12), (17, 12), (18, 12), (19, 12)}),
    name="Route 31 training grass",
)
VIOLET_GYM_LOBBY_TARGET = RouteTarget(
    map_key=(10, 7),
    tiles=frozenset({(4, 15), (5, 15)}),
    name="Violet Gym lobby",
)
FALKNER_TARGET = RouteTarget(
    map_key=(10, 7),
    tiles=frozenset({(5, 2)}),
    name="Falkner interaction tile",
)
ROUTE32_TARGET = RouteTarget(
    map_key=(10, 1),
    tiles=frozenset({(14, 0), (15, 0)}),
    name="Route 32 north edge",
)
UNION_CAVE_TARGET = RouteTarget(
    map_key=(3, 37),
    tiles=frozenset({(17, 3)}),
    name="Union Cave Route 32 entrance",
)
ROUTE33_TARGET = RouteTarget(
    map_key=(8, 6),
    tiles=frozenset({(11, 9)}),
    name="Route 33 Union Cave exit",
)
AZALEA_TARGET = RouteTarget(
    map_key=(8, 7),
    tiles=frozenset({(39, 14), (39, 15)}),
    name="Azalea Town east edge",
)
SLOWPOKE_WELL_TARGET = RouteTarget(
    map_key=(3, 40),
    tiles=frozenset({(17, 15)}),
    name="Slowpoke Well entrance",
)

FALKNER_PREP_MAPS = frozenset({(10, 5), (10, 6), (10, 7), (10, 10), (26, 11), (26, 2), (26, 1)})
FALKNER_MIN_LEVEL = 12
FALKNER_MIN_HP_RATIO = 0.65


def _effective_story_tile(state: dict[str, Any], key: tuple[int, int] | None, tile: tuple[int, int] | None) -> tuple[int, int] | None:
    if key is None or tile is None:
        return tile
    map_spec = GOLD_MAP_REGISTRY.get(key)
    if map_spec is None or map_spec.is_walkable(tile):
        return tile
    pos = ((state.get("player") or {}).get("position") or {})
    raw_x = pos.get("raw_x")
    raw_y = pos.get("raw_y")
    raw_tile = (raw_x, raw_y) if isinstance(raw_x, int) and isinstance(raw_y, int) else None
    if raw_tile is not None and map_spec.is_walkable(raw_tile):
        return raw_tile
    return tile


def registry_exploration_targets(state: dict[str, Any]) -> tuple[RouteTarget, ...]:
    """Return reachable transition targets when the story table has no answer."""
    snapshot = snapshot_from_state(state)
    key = snapshot.position.map_key
    tile = _effective_story_tile(state, key, snapshot.position.tile)
    if key is None or tile is None or GOLD_MAP_REGISTRY.get(key) is None:
        return ()
    candidates: list[tuple[int, RouteTarget]] = []
    for transition in outgoing_transitions(GOLD_MAP_REGISTRY, key):
        if not transition.source_tiles:
            continue
        dest_map = GOLD_MAP_REGISTRY.get(transition.dest_key)
        if dest_map is None:
            continue
        dest_tiles = frozenset(tile for tile in transition.dest_tiles if dest_map.is_walkable(tile))
        if not dest_tiles:
            continue
        name = f"Explore {dest_map.map_const or dest_map.name}"
        target = RouteTarget(map_key=transition.dest_key, tiles=dest_tiles, name=name)
        plan = plan_route_to_target(GOLD_MAP_REGISTRY, key, tile, target)
        if plan is None or plan.next_action is None:
            continue
        label = f"{dest_map.map_const or dest_map.name}".upper()
        score = 0
        if any(word in label for word in ("ROUTE", "GATE", "FOREST", "CAVE", "WELL", "TOWN", "CITY")):
            score -= 10
        if any(word in label for word in ("POKECENTER", "MART", "HOUSE", "GYM")):
            score += 10
        score += plan.planned_path_length or 0
        candidates.append((score, target))
    candidates.sort(key=lambda row: (row[0], row[1].name))
    return tuple(target for _, target in candidates)


def explain_story_objective(state: dict[str, Any]) -> StoryDecision:
    """Explain the next conservative story/navigation target for early Johto."""
    snapshot = snapshot_from_state(state)
    key = snapshot.position.map_key
    lead = snapshot.party[0] if snapshot.party else None
    lead_hp_ratio = lead.hp_ratio if lead is not None else None
    lead_level = lead.level if lead is not None else None
    balls = sum(item.quantity for item in snapshot.bag if item.item_id in {0x02, 0x03, 0x04})
    money = snapshot.money or 0
    has_zephyr = "Zephyr" in snapshot.badges or snapshot.story.has_zephyr_badge
    falkner_prep_ready = snapshot.story.learned_to_catch_pokemon and snapshot.story.gave_mystery_egg_to_elm
    needs_heal_before_falkner = (
        lead_hp_ratio is not None
        and lead_hp_ratio < FALKNER_MIN_HP_RATIO
        and not has_zephyr
        and falkner_prep_ready
        and key in {(10, 5), (10, 7), (10, 10), (26, 11), (26, 2)}
    )
    if needs_heal_before_falkner:
        return StoryDecision("violet_heal", "Heal before Falkner", VIOLET_POKECENTER_HEAL_TARGET, "lead_hp_low_before_zephyr")
    needs_balls_before_grind = balls < 3 and money >= 200 and falkner_prep_ready and not has_zephyr
    if needs_balls_before_grind and key in {(10, 5), (10, 6), (10, 7), (10, 10), (26, 11), (26, 2)}:
        return StoryDecision("violet_buy_balls", "Buy Poke Balls in Violet", VIOLET_MART_BUY_TARGET, "need_balls_before_grind_or_capture")
    needs_grind_before_falkner = (
        lead_level is not None
        and lead_level < FALKNER_MIN_LEVEL
        and falkner_prep_ready
        and not has_zephyr
        and key in FALKNER_PREP_MAPS
    )
    if needs_grind_before_falkner:
        reason = "lead_level_low_before_falkner"
        if balls <= 0 and money < 200:
            reason = "broke_no_balls_grind_for_exp_before_falkner"
        return StoryDecision("route31_grind", "Train and catch on Route 31", ROUTE31_GRIND_TARGET, reason)
    if not snapshot.has_starter:
        return StoryDecision("get_starter", "Choose starter in Elm's Lab", STARTER_TARGET, "needs_starter_no_trusted_party")
    needs_elm_return = snapshot.story.event_flags_available and snapshot.story.got_mystery_egg_from_mr_pokemon and not snapshot.story.gave_mystery_egg_to_elm
    if key == (24, 5):
        if needs_elm_return:
            return StoryDecision("return_to_elm", "Return Mystery Egg to Elm", ELMS_LAB_RETURN_TARGET, "inside_elms_lab_mystery_egg_not_delivered")
        return StoryDecision("leave_elms_lab", "Leave Elm's Lab", NEW_BARK_AFTER_STARTER_TARGET, "starter_obtained_leave_elms_lab")
    if key in {(24, 7), (24, 6), (24, 4)}:
        if needs_elm_return:
            return StoryDecision("return_to_elm", "Return Mystery Egg to Elm", ELMS_LAB_RETURN_TARGET, "mystery_egg_obtained_enter_elms_lab")
        return StoryDecision("route29", "Travel from New Bark toward Cherrygrove", ROUTE29_TARGET, "starter_obtained_leave_new_bark")
    if key == (24, 3):
        if needs_elm_return:
            return StoryDecision("return_to_elm", "Return Mystery Egg to Elm", ELMS_LAB_RETURN_TARGET, "mystery_egg_obtained_return_through_route29")
        return StoryDecision("cherrygrove", "Reach Cherrygrove City", CHERRYGROVE_TARGET, "route29_to_cherrygrove")
    if key == (26, 3):
        if needs_elm_return:
            return StoryDecision("return_to_elm", "Return Mystery Egg to Elm", ELMS_LAB_RETURN_TARGET, "mystery_egg_obtained_return_through_cherrygrove")
        return StoryDecision("route30", "Enter Route 30", ROUTE30_TARGET, "cherrygrove_to_route30")
    if key == (26, 1):
        if snapshot.story.event_flags_available and not snapshot.story.got_mystery_egg_from_mr_pokemon:
            return StoryDecision("mr_pokemon", "Visit Mr. Pokemon", MR_POKEMON_HOUSE_TARGET, "starter_obtained_mystery_egg_not_observed")
        if needs_elm_return:
            return StoryDecision("return_to_elm", "Return Mystery Egg to Elm", ELMS_LAB_RETURN_TARGET, "mystery_egg_obtained_not_delivered")
        return StoryDecision("route31", "Enter Route 31", ROUTE31_TARGET, "route30_to_route31")
    if key == (26, 10):
        if snapshot.story.event_flags_available and not snapshot.story.got_mystery_egg_from_mr_pokemon:
            return StoryDecision("mr_pokemon", "Visit Mr. Pokemon", MR_POKEMON_HOUSE_TARGET, "inside_mr_pokemon_house_wait_for_egg")
        if needs_elm_return:
            return StoryDecision("return_to_elm", "Return Mystery Egg to Elm", ELMS_LAB_RETURN_TARGET, "mystery_egg_obtained_leave_mr_pokemon")
    if key == (26, 2):
        return StoryDecision("violet_gate", "Enter Violet gate", VIOLET_GATE_TARGET, "route31_to_violet_gate")
    if key == (26, 11):
        return StoryDecision("violet_city", "Reach Violet City", VIOLET_CITY_TARGET, "violet_gate_to_violet_city")
    has_hive = "Hive" in snapshot.badges
    if has_zephyr and not has_hive:
        if key in {(10, 5), (10, 7)}:
            return StoryDecision("route32", "Travel south to Route 32", ROUTE32_TARGET, "zephyr_observed_continue_to_route32")
        if key == (10, 1):
            return StoryDecision("union_cave", "Enter Union Cave", UNION_CAVE_TARGET, "route32_to_union_cave")
        if key == (3, 37):
            return StoryDecision("route33", "Exit Union Cave to Route 33", ROUTE33_TARGET, "union_cave_to_route33")
        if key == (8, 6):
            return StoryDecision("azalea", "Reach Azalea Town", AZALEA_TARGET, "route33_to_azalea")
        if key == (8, 7):
            return StoryDecision("slowpoke_well", "Enter Slowpoke Well", SLOWPOKE_WELL_TARGET, "azalea_to_slowpoke_well_before_hive")
    if has_zephyr:
        fallback_targets = registry_exploration_targets(state)
        if fallback_targets:
            return StoryDecision("registry_exploration", "Explore reachable map transition", fallback_targets[0], "zephyr_badge_observed_registry_exploration", confidence="fallback")
        return StoryDecision("falkner_complete", "Zephyr badge observed", None, "zephyr_badge_observed_objective_complete")
    if not has_zephyr and falkner_prep_ready and lead_level is not None and lead_level < FALKNER_MIN_LEVEL and key in FALKNER_PREP_MAPS:
        return StoryDecision("route31_grind", "Train before Falkner", ROUTE31_GRIND_TARGET, "falkner_not_ready_keep_grinding")
    if not has_zephyr and falkner_prep_ready and lead_hp_ratio is not None and lead_hp_ratio < FALKNER_MIN_HP_RATIO and key in {(10, 5), (10, 7), (10, 10)}:
        return StoryDecision("violet_heal", "Heal before Falkner", VIOLET_POKECENTER_HEAL_TARGET, "falkner_ready_but_hp_not_safe")
    if key == (10, 5):
        return StoryDecision("falkner", "Challenge Falkner", FALKNER_TARGET, "violet_city_to_falkner_before_zephyr")
    if key == (10, 7):
        return StoryDecision("falkner", "Challenge Falkner", FALKNER_TARGET, "inside_violet_gym_reach_falkner")
    fallback_targets = registry_exploration_targets(state)
    if fallback_targets:
        return StoryDecision("registry_exploration", "Explore reachable map transition", fallback_targets[0], "no_conservative_story_target_registry_transition", confidence="fallback")
    return StoryDecision("no_conservative_target", "No conservative story target", None, "no_conservative_story_target_for_current_map")


def select_route_target(state: dict[str, Any]) -> RouteTarget | None:
    """Return the next conservative story/navigation target for early Johto."""
    return explain_story_objective(state).target
