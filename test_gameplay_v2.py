from gold_autoplayer_v2 import GoldAutoplayerV2
from pokemon_agent.gameplay import choose_catch_action, roster_roles, snapshot_from_state, summarize_inventory
from pokemon_agent.gameplay.story import ELMS_LAB_RETURN_TARGET, ROUTE31_GRIND_TARGET, VIOLET_MART_BUY_TARGET, VIOLET_POKECENTER_HEAL_TARGET, explain_story_objective, registry_exploration_targets


def make_state(**overrides):
    state = {
        "metadata": {"game": "Pokemon Gold/Silver (GBC)"},
        "player": {
            "money": 1200,
            "badges": [],
            "facing": "up",
            "position": {
                "map_group": 26,
                "map_number": 1,
                "map_name": "Route 30",
                "raw_x": 53,
                "raw_y": 7,
                "actual_x": 7,
                "actual_y": 53,
            },
        },
        "party": [
            {"slot": 1, "species_id": 0x9B, "species": "Cyndaquil", "level": 8, "hp": 24, "max_hp": 30, "moves": [33, 43]},
        ],
        "bag": [{"item_id": 0x04, "item": "Poke Ball", "quantity": 3}],
        "battle": {"in_battle": False, "type_id": 0, "wild_species_id": 0, "enemy_level": 0},
        "flags": {"has_starter": True},
    }
    state.update(overrides)
    return state


def test_snapshot_uses_normalized_gold_coordinates():
    snapshot = snapshot_from_state(make_state())

    assert snapshot.position.raw_x == 53
    assert snapshot.position.raw_y == 7
    assert snapshot.position.tile == (7, 53)


def test_inventory_summary_counts_balls():
    snapshot = snapshot_from_state(make_state())

    summary = summarize_inventory(snapshot)

    assert summary.balls == 3
    assert summary.can_catch


def test_snapshot_filters_untrusted_party_members_for_starter_progress():
    state = make_state(
        party=[{"slot": 1, "species_id": 0x9B, "species": "Cyndaquil", "level": 255, "hp": 999, "max_hp": 1, "trusted": False}],
        flags={"has_starter": False, "party_count_trusted": False},
    )

    snapshot = snapshot_from_state(state)

    assert snapshot.party == ()
    assert snapshot.has_starter is False


def test_inventory_summary_ignores_untrusted_bag_items():
    state = make_state(bag=[{"item_id": 0x04, "item": "Poke Ball", "quantity": 250, "trusted": False}])

    summary = summarize_inventory(snapshot_from_state(state))

    assert summary.balls == 0
    assert summary.can_catch is False


def test_catch_policy_targets_progression_species():
    state = make_state(battle={"in_battle": True, "type_id": 1, "wild_species_id": 0xB3, "enemy_level": 6})
    snapshot = snapshot_from_state(state)

    decision = choose_catch_action(snapshot)

    assert decision.action == "throw_ball"
    assert decision.target_species == "Mareep"


def test_catch_policy_runs_from_duplicate():
    state = make_state(battle={"in_battle": True, "type_id": 1, "wild_species_id": 0x9B, "enemy_level": 6})
    snapshot = snapshot_from_state(state)

    decision = choose_catch_action(snapshot)

    assert decision.action == "run"
    assert "already owned" in decision.reason


def test_battle_snapshot_type_2_with_enemy_species_is_not_wild():
    state = make_state(battle={"in_battle": True, "type_id": 2, "wild_species_id": 0xB3, "enemy_level": 6})

    snapshot = snapshot_from_state(state)

    assert snapshot.battle.wild is False


def test_battle_snapshot_unknown_type_with_enemy_species_is_not_wild():
    state = make_state(battle={"in_battle": True, "type_id": None, "wild_species_id": 0xB3, "enemy_level": 6})

    snapshot = snapshot_from_state(state)

    assert snapshot.battle.wild is False


def test_battle_snapshot_includes_enemy_hp_status_and_moves():
    state = make_state(battle={
        "in_battle": True,
        "type_id": 1,
        "wild_species_id": 0xB3,
        "enemy_level": 6,
        "enemy_hp": 12,
        "enemy_max_hp": 20,
        "enemy_status_raw": 0x40,
        "enemy_status_condition": {"paralysis": True, "any": True},
        "enemy_moves": [33, 45],
    })

    snapshot = snapshot_from_state(state)

    assert snapshot.battle.enemy_hp == 12
    assert snapshot.battle.enemy_max_hp == 20
    assert snapshot.battle.enemy_status_raw == 0x40
    assert snapshot.battle.enemy_status_condition == {"paralysis": True, "any": True}
    assert snapshot.battle.enemy_moves == (33, 45)
    assert snapshot.battle.enemy_hp_ratio == 0.6
    assert snapshot.battle.enemy_has_status is True


def test_catch_policy_weakens_full_hp_priority_species():
    state = make_state(battle={"in_battle": True, "type_id": 1, "wild_species_id": 0xB3, "enemy_level": 6, "enemy_hp": 20, "enemy_max_hp": 20})

    decision = choose_catch_action(snapshot_from_state(state))

    assert decision.action == "weaken"
    assert decision.reason == "target HP is high"


def test_catch_policy_throws_when_priority_species_low_hp():
    state = make_state(battle={"in_battle": True, "type_id": 1, "wild_species_id": 0xB3, "enemy_level": 6, "enemy_hp": 10, "enemy_max_hp": 20})

    decision = choose_catch_action(snapshot_from_state(state))

    assert decision.action == "throw_ball"
    assert decision.reason == "target HP is reduced"


def test_catch_policy_throws_when_priority_species_statused():
    state = make_state(battle={
        "in_battle": True,
        "type_id": 1,
        "wild_species_id": 0xB3,
        "enemy_level": 6,
        "enemy_hp": 20,
        "enemy_max_hp": 20,
        "enemy_status_condition": {"any": True},
    })

    decision = choose_catch_action(snapshot_from_state(state))

    assert decision.action == "throw_ball"
    assert decision.reason == "target has status condition"


def test_catch_policy_does_not_weaken_enemy_with_dangerous_move():
    state = make_state(battle={"in_battle": True, "type_id": 1, "wild_species_id": 0xB3, "enemy_level": 6, "enemy_hp": 20, "enemy_max_hp": 20, "enemy_moves": [120]})

    decision = choose_catch_action(snapshot_from_state(state))

    assert decision.action == "throw_ball"
    assert decision.reason == "target has dangerous move; avoid weakening"


def test_snapshot_menu_rejects_low_confidence():
    snapshot = snapshot_from_state(make_state(menu={"active": True, "name": "battle_main", "cursor": "pack", "confidence": "low"}))

    assert snapshot.menu.usable is False
    assert snapshot.menu.blocked_reason == "low_confidence"


def test_snapshot_menu_extracts_visible_item_ids():
    snapshot = snapshot_from_state(make_state(menu={
        "active": True,
        "name": "bag_items",
        "cursor": 0,
        "confidence": "medium",
        "visible_items": [{"item_id": 0x12}, {"name": "bad"}, "bad"],
    }))

    assert snapshot.menu.usable is True
    assert snapshot.menu.visible_item_ids == (0x12,)


def test_snapshot_dialog_marks_ambiguous_when_active_but_implausible():
    snapshot = snapshot_from_state(make_state(dialog={"active": True, "window_stack_plausible": False, "window_stack_size": 138}))

    assert snapshot.dialog.ambiguous is True
    assert snapshot.dialog.confirmed is False


def test_snapshot_story_uses_derived_flags_without_event_addresses():
    snapshot = snapshot_from_state(make_state(flags={
        "derived_story_flags": {
            "source": "derived_from_party_and_badges",
            "event_flags_available": False,
            "has_starter": True,
            "has_zephyr_badge": True,
            "badge_count": 1,
        }
    }))

    assert snapshot.story.has_starter is True
    assert snapshot.story.has_zephyr_badge is True
    assert snapshot.story.event_flags_available is False


def test_snapshot_story_uses_decoded_event_flags():
    snapshot = snapshot_from_state(make_state(flags={
        "derived_story_flags": {
            "source": "gold_event_flags_party_and_badges",
            "event_flags_available": True,
            "has_starter": True,
            "has_zephyr_badge": False,
            "badge_count": 0,
            "got_mystery_egg_from_mr_pokemon": True,
            "gave_mystery_egg_to_elm": False,
            "elm_called_about_stolen_pokemon": True,
            "learned_to_catch_pokemon": True,
        }
    }))

    assert snapshot.story.event_flags_available is True
    assert snapshot.story.got_mystery_egg_from_mr_pokemon is True
    assert snapshot.story.gave_mystery_egg_to_elm is False
    assert snapshot.story.elm_called_about_stolen_pokemon is True
    assert snapshot.story.learned_to_catch_pokemon is True


def test_snapshot_ignores_malformed_party_and_bag_entries():
    snapshot = snapshot_from_state(make_state(party=["bad"], bag=["bad"]))

    assert snapshot.party == ()
    assert snapshot.bag == ()


def test_roster_roles_detect_starter_main():
    snapshot = snapshot_from_state(make_state())

    roles = roster_roles(snapshot)

    assert roles["main"] == ["Cyndaquil"]


def test_v2_status_includes_gameplay_policy_snapshot(tmp_path):
    bot = GoldAutoplayerV2(data_dir=tmp_path)

    status = bot.build_status({"enabled": True, "engine": "v2"}, make_state())

    assert status["engine"] == "v2"
    assert status["phase"] == "OVERWORLD"
    assert status["current_goal"]["map_name"] == "Route 30"
    assert status["gameplay"]["balls"] == 3
    assert status["gameplay"]["position"]["x"] == 7
    assert status["gameplay"]["position"]["y"] == 53


def test_story_routes_cherrygrove_back_to_elm_after_mystery_egg():
    state = make_state(
        player={
            "money": 1200,
            "badges": [],
            "facing": "up",
            "position": {"map_group": 26, "map_number": 3, "map_name": "Cherrygrove City", "actual_x": 16, "actual_y": 0},
        },
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "event_flags_available": True,
                "got_mystery_egg_from_mr_pokemon": True,
                "gave_mystery_egg_to_elm": False,
            }
        },
    )

    decision = explain_story_objective(state)

    assert decision.objective_key == "return_to_elm"
    assert decision.target == ELMS_LAB_RETURN_TARGET


def test_story_routes_low_hp_in_violet_gym_to_pokecenter_before_falkner():
    state = make_state(
        player={
            "money": 1414,
            "badges": [],
            "facing": "down",
            "position": {"map_group": 10, "map_number": 7, "map_name": "Violet Gym", "actual_x": 5, "actual_y": 10},
        },
        party=[{"slot": 1, "species_id": 158, "species": "Totodile", "level": 9, "hp": 1, "max_hp": 29}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "event_flags_available": True,
                "got_mystery_egg_from_mr_pokemon": True,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
                "has_zephyr_badge": False,
            }
        },
    )

    decision = explain_story_objective(state)

    assert decision.objective_key == "violet_heal"
    assert decision.target == VIOLET_POKECENTER_HEAL_TARGET


def test_story_routes_no_balls_in_violet_to_mart_before_grind():
    state = make_state(
        player={
            "money": 1414,
            "badges": [],
            "facing": "down",
            "position": {"map_group": 10, "map_number": 10, "map_name": "Violet Pokemon Center 1F", "actual_x": 3, "actual_y": 6},
        },
        party=[{"slot": 1, "species_id": 158, "species": "Totodile", "level": 9, "hp": 29, "max_hp": 29}],
        bag=[{"item_id": 0x12, "item": "Potion", "quantity": 1}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "has_zephyr_badge": False,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
    )

    decision = explain_story_objective(state)

    assert decision.objective_key == "violet_buy_balls"
    assert decision.target == VIOLET_MART_BUY_TARGET


def test_story_routes_with_balls_and_low_level_to_route31_grind():
    state = make_state(
        player={
            "money": 814,
            "badges": [],
            "facing": "down",
            "position": {"map_group": 10, "map_number": 5, "map_name": "Violet City", "actual_x": 31, "actual_y": 25},
        },
        party=[{"slot": 1, "species_id": 158, "species": "Totodile", "level": 9, "hp": 29, "max_hp": 29}],
        bag=[{"item_id": 0x04, "item": "Poke Ball", "quantity": 3}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "has_zephyr_badge": False,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
    )

    decision = explain_story_objective(state)

    assert decision.objective_key == "route31_grind"
    assert decision.target == ROUTE31_GRIND_TARGET


def test_story_fallback_after_scripted_targets_uses_registry_transition():
    state = make_state()
    state["player"]["badges"] = ["Zephyr"]
    state["player"]["position"] = {
        "map_group": 3,
        "map_number": 40,
        "map_name": "Slowpoke Well B1F",
        "actual_x": 17,
        "actual_y": 14,
    }

    decision = explain_story_objective(state)

    assert decision.objective_key == "registry_exploration"
    assert decision.target is not None
    assert decision.target.map_key == (8, 7)


def test_registry_exploration_returns_reachable_targets_only():
    state = make_state()
    state["player"]["position"] = {
        "map_group": 3,
        "map_number": 40,
        "map_name": "Slowpoke Well B1F",
        "actual_x": 17,
        "actual_y": 14,
    }

    targets = registry_exploration_targets(state)

    assert targets
    assert all(target.tiles for target in targets)
