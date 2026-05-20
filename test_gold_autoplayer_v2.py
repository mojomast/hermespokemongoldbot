import json
import time
import urllib.error

from gold_autoplayer_v2 import (
    BATTLE_RUN_SEQUENCE,
    BATTLE_CAPTURE_OPEN_PACK_SEQUENCE,
    BATTLE_CAPTURE_THROW_BALL_SEQUENCE,
    CHERRYGROVE_TARGET,
    MART_BUY_ONE_SEQUENCE,
    NEW_GAME_BOOTSTRAP_SEQUENCE,
    ROUTE29_TARGET,
    ROUTE30_TARGET,
    ROUTE31_TARGET,
    ROUTE31_GRIND_SEQUENCE,
    STARTER_TARGET,
    STUCK_CIRCUIT_BREAKER_THRESHOLD,
    UNION_CAVE_TARGET,
    VIOLET_CITY_TARGET,
    VIOLET_GATE_TARGET,
    VIOLET_GYM_LOBBY_TARGET,
    GoldAutoplayerV2,
    NICKNAME_END_SEQUENCE,
    choose_run_actions,
    observed_menu,
    choose_capture_actions,
    choose_healing_item_actions,
    choose_battle_actions,
    posted_actions_for,
    posted_actions_for_context,
    walk_hold_frames_for_navigation,
    select_route_target,
    verify_single_action,
)
from pokemon_agent.gameplay import snapshot_from_state
from pokemon_agent.autoplayer.save_states import MilestoneSaveStateManager, evaluate_milestone_requests
from pokemon_agent.gameplay.story import AZALEA_TARGET, CATCHING_TUTORIAL_TARGET, ELMS_LAB_RETURN_TARGET, FALKNER_TARGET, MR_POKEMON_HOUSE_TARGET, ROUTE31_GRIND_TARGET, ROUTE32_TARGET, ROUTE33_TARGET, SLOWPOKE_WELL_TARGET, VIOLET_MART_BUY_TARGET, VIOLET_POKECENTER_AIDE_TARGET, VIOLET_POKECENTER_HEAL_TARGET


def make_state(
    map_group: int,
    map_number: int,
    x: int,
    y: int,
    *,
    party: list[dict] | None = None,
    flags: dict | None = None,
    dialog: dict | None = None,
    battle: dict | None = None,
    bag: list[dict] | None = None,
    menu: dict | None = None,
    position_extra: dict | None = None,
    facing: str = "down",
    visual: dict | None = None,
) -> dict:
    position = {
        "map_group": map_group,
        "map_number": map_number,
        "actual_x": x,
        "actual_y": y,
        "map_name": "test",
    }
    if position_extra:
        position.update(position_extra)
    state = {
        "player": {
            "position": position,
            "facing": facing,
        },
        "party": party or [],
        "flags": flags or {},
        "battle": battle or {"in_battle": False},
        "bag": bag or [],
        "dialog": dialog or {},
    }
    if menu is not None:
        state["menu"] = menu
    if visual is not None:
        state["visual"] = visual
    return state


def test_v2_starter_route_returns_single_walk_action():
    player = GoldAutoplayerV2()

    actions, nav = player.choose_overworld_actions(make_state(24, 7, 4, 2))

    assert actions == ["walk_right"]
    assert nav["path_source"] == "cross_map_static_registry"
    assert nav["next_step"] == "walk_right"
    assert nav["map_path"] == [
        {"map_group": 24, "map_number": 7},
        {"map_group": 24, "map_number": 6},
        {"map_group": 24, "map_number": 4},
        {"map_group": 24, "map_number": 5},
    ]


def test_v2_blocked_edge_replans_around_failed_walk():
    player = GoldAutoplayerV2()
    player.blocked_edges_by_map = {(24, 7): {((4, 2), "right")}}

    actions, nav = player.choose_overworld_actions(make_state(24, 7, 4, 2))

    assert actions == ["walk_down"]
    assert nav["blocked_edges"] == 1


def test_v2_clears_false_blocked_edge_after_late_progress():
    player = GoldAutoplayerV2()
    before = make_state(24, 6, 9, 1)

    player.record_action_outcome("walk_left", False, "position_did_not_advance", before_state=before)
    player.recovery_level = 2
    player.reset_recovery_after_manual_progress(make_state(24, 6, 8, 1))

    assert player.blocked_edges_by_map == {}
    assert player.recovery_level == 0
    assert player.stuck_counter == 0


def test_v2_clears_false_blocked_edge_after_overshoot_progress():
    player = GoldAutoplayerV2()
    before = make_state(24, 6, 8, 2)

    player.record_action_outcome("walk_down", False, "position_did_not_advance", before_state=before)
    player.recovery_level = 2
    player.reset_recovery_after_manual_progress(make_state(24, 6, 8, 4))

    assert player.blocked_edges_by_map == {}
    assert player.recovery_level == 0
    assert player.stuck_counter == 0


def test_v2_button_circuit_resets_after_external_movement_progress():
    player = GoldAutoplayerV2()
    before = make_state(
        26,
        2,
        26,
        17,
        party=[{"slot": 1, "species_id": 155, "level": 8, "hp": 4, "max_hp": 25}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "got_mystery_egg_from_mr_pokemon": True,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
        bag=[{"pocket": "balls", "item_id": 5, "quantity": 5}],
    )
    current = make_state(
        26,
        2,
        26,
        15,
        party=[{"slot": 1, "species_id": 155, "level": 8, "hp": 4, "max_hp": 25}],
        flags=before["flags"],
        bag=before["bag"],
    )
    player.recent_failures.append({
        "turn": 1,
        "action": "walk_up",
        "reason": "position_did_not_advance",
        "post_result": "ok",
        "map": {"map_group": 26, "map_number": 2},
        "tile": {"x": 26, "y": 17},
        "blocked_edge": True,
        "recovery_level": 1,
    })
    player.blocked_edges_by_map = {(26, 2): {((26, 17), "up"), ((26, 17), "left"), ((26, 17), "right")}}
    player.button_failure_count = STUCK_CIRCUIT_BREAKER_THRESHOLD
    player.recovery_level = 2

    actions, nav = player.choose_overworld_actions(current)

    assert actions != []
    assert nav["path_source"] != "safety_button_circuit_breaker"
    assert player.button_failure_count == 0
    assert player.recovery_level == 0


def test_v2_recovery_circuit_probes_opposite_live_direction():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        3,
        17,
        7,
        party=[{"slot": 1, "species_id": 158, "level": 5, "hp": 20, "max_hp": 20}],
    )
    player.blocked_edges_by_map = {(24, 3): {((17, 7), "left"), ((17, 7), "up")}}
    player.recent_failures.append({
        "turn": 1,
        "action": "walk_left",
        "reason": "position_did_not_advance",
        "post_result": "ok",
        "map": {"map_group": 24, "map_number": 3},
        "tile": {"x": 17, "y": 7},
        "blocked_edge": True,
        "recovery_level": 1,
    })
    player.recent_failures.append({
        "turn": 2,
        "action": "walk_up",
        "reason": "position_did_not_advance",
        "post_result": "ok",
        "map": {"map_group": 24, "map_number": 3},
        "tile": {"x": 17, "y": 7},
        "blocked_edge": True,
        "recovery_level": 1,
    })
    player.recovery_level = 2

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_down"]
    assert nav["path_source"] == "recovery_probe"


def test_v2_recovery_circuit_stops_after_all_probe_directions_fail():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        3,
        17,
        7,
        party=[{"slot": 1, "species_id": 158, "level": 5, "hp": 20, "max_hp": 20}],
    )
    player.blocked_edges_by_map = {(24, 3): {((17, 7), direction) for direction in ("up", "down", "left", "right")}}
    player.recovery_level = 2

    actions, nav = player.choose_overworld_actions(state)

    assert actions == []
    assert nav["path_source"] == "safety_circuit_breaker"


def test_v2_recovery_clears_stale_future_blocked_edges():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        3,
        30,
        6,
        party=[{"slot": 1, "species_id": 158, "level": 6, "hp": 22, "max_hp": 22}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "event_flags_available": True,
                "got_mystery_egg_from_mr_pokemon": True,
                "elm_called_about_stolen_pokemon": True,
                "gave_mystery_egg_to_elm": False,
            }
        },
    )
    player.blocked_edges_by_map = {
        (26, 3): {
            ((31, 6), "right"),
            ((32, 7), "up"),
            ((33, 7), "right"),
            ((33, 7), "up"),
        }
    }
    player.recovery_level = 2

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_right"]
    assert player.blocked_edges_by_map == {}
    assert nav["path_source"] == "cross_map_static_registry"


def test_v2_untrusted_position_blocks_route_planning():
    player = GoldAutoplayerV2()

    actions, nav = player.choose_overworld_actions(
        make_state(24, 7, 99, 0, position_extra={"trusted": False, "in_bounds": False, "confidence": "medium"})
    )

    assert actions == []
    assert nav["path_source"] == "untrusted_position"
    assert nav["position_confidence"] == "medium"
    assert nav["position_in_bounds"] is False


def test_v2_new_run_bootstrap_ticks_before_position_is_trusted():
    player = GoldAutoplayerV2()
    state = make_state(0, 0, 0, 0, position_extra={"actual_x": None, "actual_y": None, "trusted": False})
    state["map"] = {"map_group": 0, "map_number": 0}

    actions, nav = player.choose_overworld_actions(state)

    assert actions == [NEW_GAME_BOOTSTRAP_SEQUENCE[0]]
    assert nav["path_source"] == "new_game_bootstrap"


def test_v2_new_run_bootstrap_sequence_advances_after_action():
    player = GoldAutoplayerV2()
    state = make_state(0, 0, 0, 0, position_extra={"actual_x": None, "actual_y": None, "trusted": False})
    state["map"] = {"map_group": 0, "map_number": 0}

    player.record_action_outcome(NEW_GAME_BOOTSTRAP_SEQUENCE[0], True, "frames_advanced_after_wait", before_state=state)
    actions, nav = player.choose_overworld_actions(state)

    assert player.new_game_bootstrap_index == 1
    assert actions == [NEW_GAME_BOOTSTRAP_SEQUENCE[1]]
    assert nav["bootstrap_sequence_index"] == 1


def test_v2_new_run_bootstrap_recycles_until_position_is_trusted():
    player = GoldAutoplayerV2()
    player.new_game_bootstrap_index = len(NEW_GAME_BOOTSTRAP_SEQUENCE)
    state = make_state(0, 0, 0, 0, position_extra={"actual_x": None, "actual_y": None, "trusted": False})
    state["map"] = {"map_group": 0, "map_number": 0}

    actions, nav = player.choose_overworld_actions(state)

    assert actions == [NEW_GAME_BOOTSTRAP_SEQUENCE[0]]
    assert nav["path_source"] == "new_game_bootstrap"
    assert nav["bootstrap_sequence_index"] == 0


def test_v2_new_run_bootstrap_advances_visual_dialogue_after_sequence():
    player = GoldAutoplayerV2()
    player.new_game_bootstrap_index = len(NEW_GAME_BOOTSTRAP_SEQUENCE)
    state = make_state(0, 0, 0, 0, position_extra={"actual_x": None, "actual_y": None, "trusted": False})
    state["map"] = {"map_group": 0, "map_number": 0}
    state["visual"] = {"visual_textbox_active": True, "screen_class": "menu_or_text"}

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "new_game_bootstrap_dialogue"


def test_v2_new_run_bootstrap_keeps_advancing_intro_text_after_repeated_same_state_press_a():
    player = GoldAutoplayerV2()
    player.recent_action_outcomes = [
        {"action": "press_a", "state_changed": False, "before_signature": (0, 0), "after_signature": (0, 0)}
        for _ in range(STUCK_CIRCUIT_BREAKER_THRESHOLD)
    ]
    state = make_state(0, 0, 0, 0, position_extra={"actual_x": None, "actual_y": None, "trusted": False})
    state["map"] = {"map_group": 0, "map_number": 0}
    state["visual"] = {"visual_textbox_active": True, "screen_class": "menu_or_text"}

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "new_game_bootstrap_dialogue"


def test_v2_fresh_run_setup_keeps_advancing_player_house_text_before_starter():
    player = GoldAutoplayerV2()
    player.recent_action_outcomes = [
        {"action": "press_a", "state_changed": False, "before_signature": (24, 6, 9, 1), "after_signature": (24, 6, 9, 1)}
        for _ in range(STUCK_CIRCUIT_BREAKER_THRESHOLD)
    ]
    state = make_state(
        24,
        6,
        9,
        1,
        position_extra={"trusted": True, "in_bounds": True},
        flags={"derived_story_flags": {"has_starter": False}},
        visual={"visual_textbox_active": True, "screen_class": "menu_or_text"},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "fresh_run_intro_dialogue"


def test_v2_new_run_bootstrap_advances_bright_oak_text_after_sequence():
    player = GoldAutoplayerV2()
    player.new_game_bootstrap_index = len(NEW_GAME_BOOTSTRAP_SEQUENCE)
    state = make_state(0, 0, 0, 0, position_extra={"actual_x": None, "actual_y": None, "trusted": False})
    state["map"] = {"map_group": 0, "map_number": 0}
    state["visual"] = {"visual_textbox_active": False, "screen_class": "overworld_or_battle", "bright_lower": 0.82}

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "new_game_bootstrap_dialogue"


def test_v2_map_zero_menu_text_advances_instead_of_waiting_after_starter_text():
    player = GoldAutoplayerV2()
    state = make_state(0, 0, 0, 0, position_extra={"actual_x": None, "actual_y": None, "trusted": False})
    state["map"] = {"map_group": 0, "map_number": 0}
    state["visual"] = {"screen_class": "menu_or_text", "visual_textbox_active": False, "bright_lower": 0.36, "dark_lower": 0.01}

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "new_game_bootstrap_dialogue"


def test_v2_inconsistent_state_read_blocks_actions():
    player = GoldAutoplayerV2()
    state = make_state(24, 7, 4, 2)
    state["metadata"] = {"read_consistent": False, "read_started_frame": 10, "read_finished_frame": 11}

    actions, nav = player.choose_overworld_actions(state)

    assert actions == []
    assert nav["path_source"] == "inconsistent_state_read"


def test_v2_ruins_item_room_escapes_with_untrusted_shifted_position():
    player = GoldAutoplayerV2()
    state = make_state(
        3,
        29,
        30,
        3,
        party=[{"slot": 1, "species_id": 156, "level": 16, "hp": 22, "max_hp": 47}],
        position_extra={"raw_x": 3, "raw_y": 17, "trusted": False, "in_bounds": False},
    )
    state["player"]["badges"] = ["Zephyr"]

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_down"]
    assert nav["path_source"] == "ruins_of_alph_escape"
    assert nav["room_local_tile"] == {"x": 3, "y": 7}


def test_v2_union_cave_live_alias_routes_instead_of_ruins_escape():
    player = GoldAutoplayerV2()
    state = make_state(
        3,
        29,
        17,
        3,
        party=[{"slot": 1, "species_id": 156, "level": 16, "hp": 22, "max_hp": 47}],
        position_extra={"raw_x": 3, "raw_y": 17, "trusted": False, "in_bounds": False},
    )
    state["player"]["badges"] = ["Zephyr"]

    actions, nav = player.choose_overworld_actions(state)

    assert actions
    assert nav["path_source"] != "ruins_of_alph_escape"
    assert nav["path_source"] != "untrusted_position"
    assert nav["target"]["map_group"] == ROUTE33_TARGET.map_key[0]
    assert nav["target"]["map_number"] == ROUTE33_TARGET.map_key[1]


def test_v2_repeated_button_failures_open_safety_circuit():
    player = GoldAutoplayerV2()
    state = make_state(24, 3, 50, 8, party=[{"slot": 1, "species_id": 158, "level": 5, "hp": 20, "max_hp": 20}])

    for _ in range(3):
        player.record_action_outcome("press_a", False, "press_a_no_progress", before_state=state)

    actions, nav = player.choose_overworld_actions(state)

    assert actions == []
    assert nav["path_source"] == "safety_button_circuit_breaker"
    assert nav["button_failures"] == 3


def test_adaptive_button_failures_cascade_to_recovery_action():
    player = GoldAutoplayerV2()
    player.adaptive_mode = True
    state = make_state(24, 3, 50, 8, party=[{"slot": 1, "species_id": 158, "level": 5, "hp": 20, "max_hp": 20}])
    player.button_failure_count = 3
    player.last_step_action = "press_a"
    player.verification_reason = "press_a_no_progress"

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_b"]
    assert nav["path_source"] == "adaptive_button_recovery"
    assert nav["fallback_from"] == "v2_button_safety_circuit"


def test_adaptive_detects_verified_two_state_oscillation(tmp_path):
    player = GoldAutoplayerV2(data_dir=tmp_path)
    player.recent_learning_transitions.extend([
        {"before": "a", "after": "b", "verified": True},
        {"before": "b", "after": "a", "verified": True},
        {"before": "a", "after": "b", "verified": True},
        {"before": "b", "after": "a", "verified": True},
    ])

    assert player.adaptive_oscillation_detected() is True
    assert player.choose_adaptive_loop_recovery_action("press_b") == "wait_300"


def test_v2_records_shared_learning_facts(tmp_path):
    player = GoldAutoplayerV2(data_dir=tmp_path)
    before = make_state(24, 7, 4, 2)
    after = make_state(24, 7, 5, 2)

    player.record_learning_transition("walk_right", True, "walked_expected_tile", "ok", before, after)

    shared = json.loads((tmp_path / "pokemon_learning_memory.json").read_text())
    assert shared["facts"]
    progress_facts = [fact for fact in shared["facts"] if fact["category"] == "PKM:PROGRESS"]
    assert progress_facts
    assert progress_facts[-1]["source"] == "v2"


def test_v2_imports_v1_learning_as_teacher_facts(tmp_path):
    (tmp_path / "gold_world_model.json").write_text(json.dumps({
        "blocked_moves": {"24:7:4": ["walk_left", "hold_up_60", "walk_up"]},
        "directed_edges": {
            "6151:2:4|right": {"action": "walk_right", "direction": "right", "from": "6151:2:4", "to": "6151:2:5", "state": "open", "open_count": 5, "blocked_count": 0},
        },
        "places": {"24:7": {"visits": 3, "important": True, "notes": ["starter room"]}},
    }), encoding="utf-8")
    (tmp_path / "gold_policy.json").write_text(json.dumps({"phase": "dialogue", "turn": 42}), encoding="utf-8")

    player = GoldAutoplayerV2(data_dir=tmp_path)

    shared = json.loads((tmp_path / "pokemon_learning_memory.json").read_text(encoding="utf-8"))
    sources = {fact["source"] for fact in shared["facts"]}
    kinds = {fact["data"].get("kind") for fact in shared["facts"]}
    assert sources == {"v1_teacher"}
    assert {"v1_blocked_moves", "v1_open_edge", "v1_place", "v1_policy_snapshot"}.issubset(kinds)
    assert player.learning_summary()["v1_teacher_import"]["blocked_moves"] == 1


def test_v2_exploratory_fallback_prefers_v1_teacher_open_edge(tmp_path):
    (tmp_path / "gold_world_model.json").write_text(json.dumps({
        "directed_edges": {
            "6151:2:4|right": {"action": "walk_right", "direction": "right", "from": "6151:2:4", "to": "6151:2:5", "state": "open", "open_count": 7, "blocked_count": 0},
            "6151:2:4|down": {"action": "walk_down", "direction": "down", "from": "6151:2:4", "to": "6151:3:4", "state": "open", "open_count": 2, "blocked_count": 0},
        }
    }), encoding="utf-8")
    player = GoldAutoplayerV2(data_dir=tmp_path)

    actions, status = player.choose_safe_exploratory_action((24, 7), (4, 2), fallback_from="no_route")

    assert actions == ["walk_right"]
    assert status["fallback_action_source"] == "v1_teacher"


def test_v2_falls_back_to_raw_tile_when_normalized_tile_is_blocked():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        5,
        2,
        5,
        position_extra={"raw_x": 5, "raw_y": 2, "actual_x": 2, "actual_y": 5},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions
    assert nav["path_source"] == "starter_live_macro"
    assert "coordinate_source" not in nav
    assert nav["path_source"] != "no_route"


def test_v2_starter_macro_handles_known_lab_tile_without_route():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        5,
        2,
        5,
        position_extra={"raw_x": 2, "raw_y": 5, "actual_x": 2, "actual_y": 5},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions
    assert nav["path_source"] == "starter_live_macro"


def test_v2_no_goal_uses_exploratory_fallback_on_known_map():
    player = GoldAutoplayerV2()
    state = make_state(
        3,
        40,
        17,
        14,
        party=[{"slot": 1, "species_id": 158, "level": 14, "hp": 30, "max_hp": 30}],
    )
    state["player"]["badges"] = ["Zephyr"]

    actions, nav = player.choose_overworld_actions(state)

    assert actions
    assert nav["path_source"] != "no_goal"


def test_v2_visual_dialogue_overrides_button_safety_circuit():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        5,
        5,
        3,
        party=[{"slot": 1, "species_id": 158, "level": 5, "hp": 20, "max_hp": 20}],
        visual={"screen_class": "menu_or_text", "visual_textbox_active": True, "bright_lower": 0.8, "dark_lower": 0.18},
    )
    player.button_failure_count = 3

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "dialogue"
    assert player.button_failure_count == 0


def test_v2_low_hp_pokecenter_heal_is_not_suppressed_to_falkner():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        10,
        3,
        6,
        party=[{"slot": 1, "species_id": 155, "level": 12, "hp": 10, "max_hp": 34}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
                "has_zephyr_badge": False,
            }
        },
    )
    snapshot = snapshot_from_state(state)
    player.pokecenter_heal_attempt_signature = player.lead_health_signature(snapshot)

    assert player.suppress_repeated_pokecenter_heal(snapshot) is True
    assert player.target_after_pokecenter_heal_attempt(snapshot) == ROUTE31_GRIND_TARGET


def test_v2_unknown_start_map_reports_route_failure():
    player = GoldAutoplayerV2()

    actions, nav = player.choose_overworld_actions(make_state(99, 99, 1, 1))

    assert actions == []
    assert nav["path_source"] == "no_route"
    assert nav["route_failure"] == "unknown_start_map"


def test_v2_unwalkable_start_reports_route_failure():
    player = GoldAutoplayerV2()

    actions, nav = player.choose_overworld_actions(make_state(24, 7, 99, 99))

    assert actions == ["wait_300"]
    assert nav["path_source"] == "exploratory_fallback"
    assert nav["route_failure"] == "start_not_walkable"


def test_v2_missing_control_defaults_to_v1_for_safety(tmp_path):
    player = GoldAutoplayerV2(data_dir=tmp_path)

    control = player.read_control()

    assert control["engine"] == "v1"


def test_v2_starter_interact_faces_up_before_press_a(tmp_path):
    player = GoldAutoplayerV2(data_dir=tmp_path)

    actions, nav = player.choose_overworld_actions(make_state(24, 5, 5, 5))

    assert actions == ["walk_up"]
    assert nav["path_source"] == "starter_live_macro"
    assert nav["next_step"] == "walk_up"


def test_v2_starter_interact_presses_a_after_facing_attempt(tmp_path):
    player = GoldAutoplayerV2(data_dir=tmp_path)
    player.starter_face_up_attempted_at = (5, 5)

    actions, nav = player.choose_overworld_actions(make_state(24, 5, 5, 5))

    assert actions == ["press_a"]
    assert nav["path_source"] == "starter_live_macro"
    assert nav["next_step"] == "press_a"


def test_v2_starter_live_macro_avoids_raw_coordinate_down_loop():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        5,
        2,
        5,
        position_extra={"raw_x": 5, "raw_y": 2, "actual_x": 2, "actual_y": 5},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_right"]
    assert nav["path_source"] == "starter_live_macro"
    assert nav["reason"].startswith("starter selection uses live normalized coordinates")


def test_v2_starter_live_macro_interacts_from_any_starter_ball_tile(tmp_path):
    player = GoldAutoplayerV2(data_dir=tmp_path)

    actions, nav = player.choose_overworld_actions(make_state(24, 5, 5, 5))

    assert actions == ["walk_up"]
    assert nav["path_source"] == "starter_live_macro"


def test_v2_starter_choice_rotates_from_learning_file(tmp_path):
    (tmp_path / "gold_autoplayer_v2_learning.json").write_text(json.dumps({
        "starter_selection": {"last_choice_index": 0, "last_nickname_index": 1},
    }))
    player = GoldAutoplayerV2(data_dir=tmp_path)

    actions, nav = player.choose_overworld_actions(make_state(24, 5, 5, 5))

    assert actions == ["walk_right"]
    assert nav["starter_choice"] == "totodile"
    assert nav["starter_nickname"] is None


def test_v2_starter_live_macro_uses_short_walk_holds():
    frames = walk_hold_frames_for_navigation("walk_right", {"path_source": "starter_live_macro"})

    assert frames == 24


def test_v2_starter_nickname_does_not_repeat_after_completed_sequence(tmp_path):
    (tmp_path / "gold_autoplayer_v2_learning.json").write_text(json.dumps({
        "starter_selection": {"active_nickname": "A", "active_nickname_index": 0},
    }))
    player = GoldAutoplayerV2(data_dir=tmp_path)
    player.nickname_sequence_index = 3
    state = make_state(
        24,
        5,
        7,
        4,
        party=[{"slot": 1, "species_id": 158, "level": 5, "hp": 20, "max_hp": 20}],
        visual={"screen_class": "menu_or_text", "visual_textbox_active": False, "bright_lower": 0.8},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == []
    assert nav["path_source"] == "text_input_keyboard_blocked_after_nickname_sequence"


def test_v2_starter_face_up_attempt_does_not_record_blocked_edge():
    player = GoldAutoplayerV2()

    player.record_action_outcome(
        "walk_up",
        False,
        "position_did_not_advance",
        before_state=make_state(24, 5, 7, 5),
    )

    assert player.last_step_verified is True
    assert player.verification_reason == "starter_facing_attempted"
    assert player.starter_face_up_attempted_at == (7, 5)
    assert player.blocked_edges_by_map == {}


def test_v2_after_starter_routes_from_players_room_toward_route29():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        7,
        4,
        2,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_right"]
    assert nav["path_source"] == "cross_map_static_registry"
    assert nav["target"]["map_group"] == 24
    assert nav["target"]["map_number"] == 3


def test_select_route_target_returns_starter_before_party():
    target = select_route_target(make_state(24, 7, 4, 2))

    assert target == STARTER_TARGET


def test_select_route_target_returns_route29_after_starter_in_new_bark():
    target = select_route_target(
        make_state(
            24,
            4,
            6,
            3,
            party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        )
    )

    assert target == ROUTE29_TARGET


def test_catching_tutorial_routes_past_route29_new_bark_boundary():
    player = GoldAutoplayerV2()
    flags = {
        "derived_story_flags": {
            "event_flags_available": True,
            "has_starter": True,
            "gave_mystery_egg_to_elm": True,
            "learned_to_catch_pokemon": False,
            "has_zephyr_badge": False,
        }
    }

    actions, nav = player.choose_overworld_actions(make_state(
        24,
        3,
        58,
        8,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 14, "max_hp": 19}],
        flags=flags,
        bag=[{"item_id": 5, "quantity": 5}],
    ))

    assert select_route_target(make_state(24, 4, 0, 8, party=[{"slot": 1, "species_id": 155}], flags=flags)) == CATCHING_TUTORIAL_TARGET
    assert actions == ["walk_left"]
    assert nav["target"]["tiles"] == [{"x": 53, "y": 8}, {"x": 53, "y": 9}]
    assert nav["transition"] is None


def test_v2_after_starter_uses_planner_from_elms_lab():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        5,
        6,
        4,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_down"]
    assert nav["path_source"] == "cross_map_static_registry"
    assert nav["target"]["map_group"] == 24
    assert nav["target"]["map_number"] == 4


def test_v2_starter_nickname_screen_uses_silly_name_sequence(tmp_path):
    player = GoldAutoplayerV2(data_dir=tmp_path)
    state = make_state(
        24,
        5,
        7,
        4,
        party=[{"slot": 1, "species_id": 158, "level": 5, "hp": 20, "max_hp": 20}],
        visual={"screen_class": "menu_or_text", "visual_textbox_active": False, "bright_lower": 0.8},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "starter_nickname_entry"
    assert nav["nickname"] in {"A", "AA", "AAA", "AAAA", "AAAAA"}


def test_v2_starter_nickname_sequence_advances_after_posted_action(tmp_path):
    player = GoldAutoplayerV2(data_dir=tmp_path)
    state = make_state(
        24,
        5,
        7,
        4,
        party=[{"slot": 1, "species_id": 158, "level": 5, "hp": 20, "max_hp": 20}],
        visual={"screen_class": "menu_or_text", "visual_textbox_active": False, "bright_lower": 0.8},
    )

    player.record_action_outcome(NICKNAME_END_SEQUENCE[0], True, "nickname_cursor_direction_posted", before_state=state)
    actions, nav = player.choose_overworld_actions(state)

    assert player.nickname_sequence_index == 1
    assert actions == ["press_start"]
    assert nav["text_input_sequence_index"] == 1


def test_v2_starter_nickname_sequence_confirms_after_start(tmp_path):
    player = GoldAutoplayerV2(data_dir=tmp_path)
    state = make_state(
        24,
        5,
        7,
        4,
        party=[{"slot": 1, "species_id": 158, "level": 5, "hp": 20, "max_hp": 20}],
        visual={"screen_class": "menu_or_text", "visual_textbox_active": False, "bright_lower": 0.8},
    )

    player.record_action_outcome("press_a", True, "nickname_letter_posted", before_state=state)
    player.record_action_outcome("press_start", True, "nickname_start_posted", before_state=state)
    actions, nav = player.choose_overworld_actions(state)

    assert player.nickname_sequence_index == 2
    assert actions == ["press_a"]
    assert nav["text_input_sequence_index"] == 2


def test_v2_finalizes_starter_nickname_after_keyboard_exits(tmp_path):
    (tmp_path / "gold_autoplayer_v2_learning.json").write_text(json.dumps({
        "starter_selection": {
            "last_choice": "cyndaquil",
            "last_choice_index": 0,
            "active_nickname": "A",
            "active_nickname_index": 0,
        },
    }))
    player = GoldAutoplayerV2(data_dir=tmp_path)
    state = make_state(
        24,
        5,
        5,
        8,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 19, "max_hp": 19}],
        visual={"screen_class": "overworld_or_battle", "visual_textbox_active": False, "bright_lower": 0.1},
    )

    player.choose_overworld_actions(state)

    selection = player.learning["starter_selection"]
    assert selection["last_choice"] == "cyndaquil"
    assert selection["last_nickname"] == "A"
    assert selection["last_nickname_index"] == 0
    assert "active_nickname" not in selection


def test_v2_rival_name_screen_uses_end_sequence_before_recovery():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        5,
        4,
        3,
        party=[{"slot": 1, "species_id": 158, "level": 6, "hp": 22, "max_hp": 22}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "event_flags_available": True,
                "got_mystery_egg_from_mr_pokemon": True,
                "elm_called_about_stolen_pokemon": True,
                "gave_mystery_egg_to_elm": False,
            }
        },
        visual={"screen_class": "menu_or_text", "visual_textbox_active": False, "bright_lower": 0.849, "dark_lower": 0.045},
    )
    player.recovery_level = 2
    player.blocked_edges_by_map = {(24, 5): {((4, 3), direction) for direction in ("up", "down", "left", "right")}}

    actions, nav = player.choose_overworld_actions(state)

    assert actions == [NICKNAME_END_SEQUENCE[0]]
    assert nav["path_source"] == "text_input_keyboard_end"


def test_v2_route31_gate_uses_live_entry_from_upper_door_tile():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        2,
        4,
        6,
        party=[
            {"slot": 1, "species_id": 158, "level": 12, "hp": 29, "max_hp": 29},
            {"slot": 2, "species_id": 179, "level": 7, "hp": 20, "max_hp": 20},
            {"slot": 3, "species_id": 16, "level": 6, "hp": 18, "max_hp": 18},
        ],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "event_flags_available": True,
                "got_mystery_egg_from_mr_pokemon": True,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_down"]
    assert nav["path_source"] == "route31_gate_live_entry"


def test_v2_route31_gate_uses_live_entry_from_lower_door_tile():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        2,
        4,
        7,
        party=[
            {"slot": 1, "species_id": 158, "level": 12, "hp": 29, "max_hp": 29},
            {"slot": 2, "species_id": 179, "level": 7, "hp": 20, "max_hp": 20},
            {"slot": 3, "species_id": 16, "level": 6, "hp": 18, "max_hp": 18},
        ],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "event_flags_available": True,
                "got_mystery_egg_from_mr_pokemon": True,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_left"]
    assert nav["path_source"] == "route31_gate_live_entry"


def test_v2_low_hp_in_violet_gym_routes_to_pokecenter():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        7,
        5,
        10,
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

    actions, nav = player.choose_overworld_actions(state)

    assert actions
    assert nav["target"]["map_group"] == VIOLET_POKECENTER_HEAL_TARGET.map_key[0]
    assert nav["target"]["map_number"] == VIOLET_POKECENTER_HEAL_TARGET.map_key[1]


def test_v2_pokecenter_nurse_interact_faces_up_before_press_a():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        10,
        3,
        3,
        party=[{"slot": 1, "species_id": 158, "species": "Totodile", "level": 9, "hp": 1, "max_hp": 29}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "has_zephyr_badge": False,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_up"]
    assert nav["path_source"] == "pokecenter_heal_interact"


def test_v2_pokecenter_nurse_interact_presses_a_after_facing_attempt():
    player = GoldAutoplayerV2()
    player.pokecenter_face_up_attempted_at = (3, 3)
    state = make_state(
        10,
        10,
        3,
        3,
        party=[{"slot": 1, "species_id": 158, "species": "Totodile", "level": 9, "hp": 1, "max_hp": 29}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "has_zephyr_badge": False,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "pokecenter_heal_interact"


def test_v2_pokecenter_heal_attempt_does_not_reopen_nurse_dialogue_with_stale_hp():
    player = GoldAutoplayerV2()
    player.pokecenter_face_up_attempted_at = (3, 3)
    state = make_state(
        10,
        10,
        3,
        3,
        party=[{"slot": 1, "species_id": 158, "species": "Totodile", "level": 12, "hp": 1, "max_hp": 29}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "has_zephyr_badge": False,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
    )

    player.record_action_outcome("press_a", True, "dialogue_state_changed_after_press_a", before_state=state)
    actions, nav = player.choose_overworld_actions(state)

    assert actions != ["press_a"]
    assert nav["pokecenter_heal_suppressed"] is True
    assert nav["target"]["map_group"] == ROUTE31_GRIND_TARGET.map_key[0]
    assert nav["target"]["map_number"] == ROUTE31_GRIND_TARGET.map_key[1]


def test_v2_stale_dialogue_at_nurse_counter_recovers_instead_of_pressing_a_forever():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        10,
        3,
        3,
        party=[{"slot": 1, "species_id": 155, "species": "Cyndaquil", "level": 10, "hp": 29, "max_hp": 29}],
        bag=[{"item_id": 0x11, "name": "Potion", "quantity": 1}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "has_zephyr_badge": False,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
        visual={"screen_class": "dialogue", "visual_textbox_active": True},
    )
    key = player.state_key_for_learning(state)
    for _ in range(8):
        player.recent_learning_transitions.append({"action": "press_a", "before": key, "after": key})

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_b"]
    assert nav["path_source"] == "stale_dialogue_recovery"
    assert nav["same_state_press_a_count"] == 8


def test_v2_low_hp_nurse_dialogue_advances_before_stale_dialogue_recovery():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        10,
        3,
        3,
        party=[{"slot": 1, "species_id": 155, "species": "Cyndaquil", "level": 12, "hp": 7, "max_hp": 33}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "has_zephyr_badge": False,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
        visual={"screen_class": "dialogue", "visual_textbox_active": True},
    )
    key = player.state_key_for_learning(state)
    for _ in range(3):
        player.recent_learning_transitions.append({"action": "press_a", "before": key, "after": key})

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "pokecenter_heal_dialogue"


def test_v2_full_hp_implausible_nurse_text_recovers_instead_of_pressing_a():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        10,
        3,
        3,
        party=[{"slot": 1, "species_id": 155, "species": "Cyndaquil", "level": 12, "hp": 33, "max_hp": 33}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "has_zephyr_badge": False,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
        dialog={"active": True, "ram_active": False, "visual_active": True, "window_stack_size": 138, "window_stack_plausible": False},
        visual={"screen_class": "menu_or_text", "visual_textbox_active": True},
    )
    key = player.state_key_for_learning(state)
    for _ in range(STUCK_CIRCUIT_BREAKER_THRESHOLD):
        player.recent_learning_transitions.append({"action": "press_a", "before": key, "after": key})

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_b"]
    assert nav["path_source"] == "stale_dialogue_recovery"
    assert "low-HP nurse interaction" not in nav.get("reason", "")


def test_v2_live_full_hp_pokecenter_trap_recovers_before_eighth_press_a():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        10,
        3,
        3,
        party=[{
            "slot": 1,
            "species_id": 155,
            "species": "Cyndaquil",
            "level": 10,
            "hp": 31,
            "max_hp": 31,
            "status_raw": 0,
            "status_condition": {"raw": 0, "sleep_turns": 0, "poison": False, "burn": False, "freeze": False, "paralysis": False, "any": False},
            "trusted": True,
        }],
        bag=[{"item_id": 18, "item": "Potion", "quantity": 1, "trusted": True}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "has_zephyr_badge": False,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
        dialog={
            "active": True,
            "window_stack_size": 138,
            "window_stack_plausible": False,
            "ram_active": False,
            "visual_active": True,
        },
        visual={
            "screen_class": "menu_or_text",
            "bright_dialogue_panel": True,
            "visual_textbox_active": True,
        },
    )
    key = player.state_key_for_learning(state)
    for _ in range(7):
        player.recent_learning_transitions.append({"action": "press_a", "before": key, "after": key})

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_b"]
    assert nav["path_source"] == "stale_dialogue_recovery"
    assert nav["same_state_press_a_count"] == 7


def test_v2_stale_dialogue_recovery_continues_after_wait_without_real_progress():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        10,
        3,
        3,
        party=[{"slot": 1, "species_id": 155, "species": "Cyndaquil", "level": 10, "hp": 29, "max_hp": 29}],
        bag=[{"item_id": 0x11, "name": "Potion", "quantity": 1}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "has_zephyr_badge": False,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
        visual={"screen_class": "dialogue", "visual_textbox_active": True},
    )
    key = player.state_key_for_learning(state)
    for _ in range(3):
        player.recent_learning_transitions.append({"action": "press_a", "before": key, "after": key})

    player.record_action_outcome("press_b", False, "press_b_no_progress", before_state=state)
    player.record_action_outcome("wait_300", True, "frames_advanced_after_wait", before_state=state)
    player.stale_dialogue_recovery_index = 2
    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "stale_dialogue_recovery"


def test_v2_pokecenter_face_up_attempt_does_not_record_blocked_edge():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        10,
        3,
        3,
        party=[{"slot": 1, "species_id": 158, "species": "Totodile", "level": 9, "hp": 1, "max_hp": 29}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "has_zephyr_badge": False,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
    )

    player.record_action_outcome("walk_up", False, "position_did_not_advance", before_state=state)

    assert player.last_step_verified is True
    assert player.verification_reason == "pokecenter_nurse_facing_attempted"
    assert player.blocked_edges_by_map == {}


def test_v2_violet_mart_target_interacts_with_clerk():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        6,
        3,
        3,
        party=[{"slot": 1, "species_id": 158, "species": "Totodile", "level": 9, "hp": 29, "max_hp": 29}],
        bag=[{"item_id": 0x12, "quantity": 1}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "has_zephyr_badge": False,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
    )
    state["player"]["money"] = 1414

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_left"]
    assert nav["path_source"] == "mart_clerk_interact"


def test_v2_violet_mart_target_presses_a_after_facing_clerk():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        6,
        3,
        3,
        party=[{"slot": 1, "species_id": 158, "species": "Totodile", "level": 9, "hp": 29, "max_hp": 29}],
        bag=[{"item_id": 0x12, "quantity": 1}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "has_zephyr_badge": False,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
    )
    state["player"]["money"] = 1414
    player.record_action_outcome("walk_left", True, "facing_changed_after_direction", before_state=state)

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "mart_clerk_interact"


def test_v2_violet_mart_purchase_screen_uses_buy_sequence():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        6,
        3,
        3,
        party=[{"slot": 1, "species_id": 158, "species": "Totodile", "level": 9, "hp": 29, "max_hp": 29}],
        bag=[{"item_id": 0x12, "quantity": 1}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "has_zephyr_badge": False,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
        visual={"screen_class": "menu_or_text", "visual_textbox_active": True, "bright_lower": 0.8, "dark_lower": 0.16},
    )
    state["player"]["money"] = 1414

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "mart_buy_balls"
    assert nav["mart_purchase_policy"] == "balls"


def test_v2_violet_mart_does_not_overshop_when_supplies_met():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        5,
        39,
        24,
        party=[{"slot": 1, "species_id": 158, "species": "Totodile", "level": 9, "hp": 29, "max_hp": 29}],
        bag=[{"item_id": 0x05, "quantity": 3}, {"item_id": 0x12, "quantity": 1}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "has_zephyr_badge": False,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
    )
    state["player"]["money"] = 648

    actions, nav = player.choose_overworld_actions(state)

    assert actions
    assert nav["target"]["map_group"] == ROUTE31_GRIND_TARGET.map_key[0]
    assert nav["target"]["map_number"] == ROUTE31_GRIND_TARGET.map_key[1]


def test_v2_violet_mart_purchase_sequence_advances_after_action():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        6,
        3,
        3,
        party=[{"slot": 1, "species_id": 158, "species": "Totodile", "level": 9, "hp": 29, "max_hp": 29}],
        bag=[{"item_id": 0x12, "quantity": 1}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "has_zephyr_badge": False,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
        visual={"screen_class": "menu_or_text", "visual_textbox_active": True, "bright_lower": 0.8, "dark_lower": 0.16},
    )
    state["player"]["money"] = 1414

    player.record_action_outcome("press_a", True, "visual_state_changed_after_press_a", before_state=state)

    assert player.mart_buy_sequence_index == 1


def test_v2_violet_mart_purchase_screen_buys_potion_after_balls():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        6,
        3,
        3,
        party=[{"slot": 1, "species_id": 158, "species": "Totodile", "level": 9, "hp": 29, "max_hp": 29}],
        bag=[{"item_id": 0x05, "quantity": 2}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "has_zephyr_badge": False,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
        visual={"screen_class": "menu_or_text", "visual_textbox_active": True, "bright_lower": 0.8, "dark_lower": 0.16},
    )
    state["player"]["money"] = 900

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_b"]
    assert nav["path_source"] == "mart_buy_potions"
    assert nav["mart_purchase_policy"] == "potions"


def test_v2_violet_mart_buys_at_least_one_ball_before_potion_reserve():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        6,
        3,
        3,
        party=[{"slot": 1, "species_id": 158, "species": "Totodile", "level": 9, "hp": 29, "max_hp": 29}],
        bag=[{"item_id": 0x12, "quantity": 1}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "has_zephyr_badge": False,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
        visual={"screen_class": "menu_or_text", "visual_textbox_active": True, "bright_lower": 0.8, "dark_lower": 0.16},
    )
    state["player"]["money"] = 500

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "mart_buy_balls"
    assert nav["mart_purchase_policy"] == "balls"


def test_v2_violet_mart_buys_one_ball_with_low_money_no_reserve():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        6,
        3,
        3,
        party=[{"slot": 1, "species_id": 155, "level": 12, "hp": 25, "max_hp": 34}],
        bag=[{"item_id": 0x12, "quantity": 1}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "has_zephyr_badge": False,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": False,
            }
        },
        visual={"screen_class": "menu_or_text", "visual_textbox_active": True, "bright_lower": 0.8, "dark_lower": 0.16},
    )
    state["player"]["money"] = 307

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "mart_buy_balls"
    assert nav["mart_purchase_policy"] == "balls"


def test_v2_violet_mart_does_not_buy_from_ambiguous_text_away_from_clerk():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        6,
        5,
        5,
        party=[{"slot": 1, "species_id": 155, "level": 12, "hp": 34, "max_hp": 34}],
        bag=[],
        flags={"derived_story_flags": {"has_starter": True, "gave_mystery_egg_to_elm": True}},
        visual={"screen_class": "menu_or_text", "visual_textbox_active": True, "bright_lower": 0.8, "dark_lower": 0.16},
    )
    state["player"]["money"] = 500

    actions, nav = player.choose_overworld_actions(state)

    assert nav["path_source"] != "mart_buy_balls"


def test_v2_violet_mart_completed_unverified_sequence_backs_out_before_retry():
    player = GoldAutoplayerV2()
    player.mart_buy_sequence_index = len(MART_BUY_ONE_SEQUENCE)
    state = make_state(
        10,
        6,
        3,
        3,
        party=[{"slot": 1, "species_id": 155, "level": 12, "hp": 34, "max_hp": 34}],
        bag=[],
        flags={"derived_story_flags": {"has_starter": True, "gave_mystery_egg_to_elm": True}},
        visual={"screen_class": "menu_or_text", "visual_textbox_active": True, "bright_lower": 0.8, "dark_lower": 0.16},
    )
    state["player"]["money"] = 500

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_b"]
    assert nav["path_source"] == "mart_purchase_unverified_reopen"


def test_v2_violet_mart_potion_buy_uses_a_when_potion_selected():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        6,
        3,
        3,
        party=[{"slot": 1, "species_id": 158, "species": "Totodile", "level": 9, "hp": 29, "max_hp": 29}],
        bag=[{"item_id": 0x05, "quantity": 2}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "has_zephyr_badge": False,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
        menu={"active": True, "name": "mart_items", "cursor": 0, "confidence": "medium", "selected_item_id": 0x12},
        visual={"screen_class": "menu_or_text", "visual_textbox_active": True, "bright_lower": 0.8, "dark_lower": 0.16},
    )
    state["player"]["money"] = 900

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "mart_buy_potions"


def test_v2_route31_grind_target_walks_back_and_forth():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        2,
        18,
        12,
        party=[{"slot": 1, "species_id": 158, "species": "Totodile", "level": 9, "hp": 29, "max_hp": 29}],
        bag=[{"item_id": 0x05, "quantity": 3}, {"item_id": 0x12, "quantity": 2}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "has_zephyr_badge": False,
                "gave_mystery_egg_to_elm": True,
                "learned_to_catch_pokemon": True,
            }
        },
    )
    state["player"]["money"] = 814

    actions, nav = player.choose_overworld_actions(state)

    assert actions == [ROUTE31_GRIND_SEQUENCE[0]]
    assert nav["path_source"] == "route31_grind_loop"


def test_v2_untrusted_wild_battle_menu_uses_run_sequence():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        3,
        49,
        13,
        party=[{"slot": 1, "species_id": 158, "level": 5, "hp": 20, "max_hp": 20}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 161, "trusted": True},
        menu={"active": False, "confidence": "none", "needs_stronger_decode": True},
        visual={"screen_class": "menu_or_text", "visual_textbox_active": False},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == [BATTLE_RUN_SEQUENCE[0]]
    assert nav["controller_state"] == "fallback_battle_main_sequence"


def test_v2_untrusted_wild_battle_overworld_visual_uses_run_sequence():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        3,
        45,
        12,
        party=[{"slot": 1, "species_id": 155, "level": 7, "hp": 23, "max_hp": 23}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 163, "trusted": True},
        menu={"active": False, "confidence": "none", "needs_stronger_decode": True, "window_stack_plausible": False},
        visual={"screen_class": "overworld_or_battle", "visual_textbox_active": False},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == [BATTLE_RUN_SEQUENCE[0]]
    assert nav["controller_state"] == "fallback_battle_main_sequence"


def test_v2_untrusted_wild_battle_run_sequence_advances_after_posted_action():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        3,
        49,
        13,
        party=[{"slot": 1, "species_id": 158, "level": 5, "hp": 20, "max_hp": 20}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 161, "trusted": True},
        menu={"active": False, "confidence": "none", "needs_stronger_decode": True},
        visual={"screen_class": "menu_or_text", "visual_textbox_active": False},
    )

    player.record_action_outcome(BATTLE_RUN_SEQUENCE[0], True, "nickname_cursor_direction_posted", before_state=state)
    actions, nav = player.choose_overworld_actions(state)

    assert player.battle_run_sequence_index == 1
    assert actions == [BATTLE_RUN_SEQUENCE[1]]
    assert nav["battle_run_sequence_index"] == 1


def test_v2_battle_run_sequence_bypasses_button_circuit_breaker():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        2,
        31,
        14,
        party=[{"slot": 1, "species_id": 158, "level": 12, "hp": 26, "max_hp": 26}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 161, "trusted": True},
        menu={"active": False, "confidence": "none", "needs_stronger_decode": True},
        visual={"screen_class": "menu_or_text", "visual_textbox_active": False},
    )
    player.button_failure_count = 3
    player.battle_run_sequence_index = 3

    actions, nav = player.choose_overworld_actions(state)

    assert actions == [BATTLE_RUN_SEQUENCE[3]]
    assert nav["controller_state"] == "fallback_battle_main_sequence"


def test_v2_battle_run_sequence_bypasses_recovery_circuit_until_confirm():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        2,
        31,
        14,
        party=[{"slot": 1, "species_id": 158, "level": 12, "hp": 26, "max_hp": 26}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 161, "trusted": True},
        menu={"active": False, "confidence": "none", "needs_stronger_decode": True},
        visual={"screen_class": "menu_or_text", "visual_textbox_active": False},
    )
    player.recovery_level = 2
    player.battle_run_sequence_index = 3

    actions, nav = player.choose_overworld_actions(state)

    assert actions == [BATTLE_RUN_SEQUENCE[3]]
    assert nav["controller_state"] == "fallback_battle_main_sequence"
    assert nav["path_source"] == "battle_fallback"


def test_v2_elm_phone_call_presses_a_before_route_planning():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        1,
        17,
        6,
        party=[{"slot": 1, "species_id": 158, "level": 5, "hp": 20, "max_hp": 20}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "event_flags_available": True,
                "got_mystery_egg_from_mr_pokemon": True,
                "elm_called_about_stolen_pokemon": False,
            }
        },
        visual={"screen_class": "menu_or_text", "visual_textbox_active": False},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "elm_phone_call"


def test_v2_elm_phone_call_stops_after_visual_clears():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        1,
        17,
        6,
        party=[{"slot": 1, "species_id": 158, "level": 5, "hp": 20, "max_hp": 20}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "event_flags_available": True,
                "got_mystery_egg_from_mr_pokemon": True,
                "elm_called_about_stolen_pokemon": True,
                "gave_mystery_egg_to_elm": False,
            }
        },
        visual={"screen_class": "menu_or_text", "visual_textbox_active": False, "bright_lower": 0.4},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions != ["press_a"]
    assert nav["path_source"] != "elm_phone_call"


def test_v2_visual_dialogue_screen_presses_a_without_ram_dialog_flag():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        3,
        33,
        7,
        party=[{"slot": 1, "species_id": 158, "level": 5, "hp": 20, "max_hp": 20}],
        visual={"screen_class": "dialogue", "visual_textbox_active": True, "bright_lower": 0.8},
        dialog={"active": False, "visual_active": False},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "dialogue"


def test_v2_bright_overworld_dialogue_class_false_positive_does_not_press_a():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        4,
        12,
        6,
        visual={"screen_class": "dialogue", "visual_textbox_active": False, "bright_lower": 0.84, "dark_lower": 0.03},
        dialog={"active": False, "visual_active": False},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions != ["press_a"]
    assert nav["path_source"] != "dialogue"


def test_v2_dialogue_class_with_dark_textbox_presses_a_without_visual_active_flag():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        3,
        33,
        7,
        party=[{"slot": 1, "species_id": 158, "level": 5, "hp": 20, "max_hp": 20}],
        visual={"screen_class": "dialogue", "visual_textbox_active": False, "bright_lower": 0.827, "dark_lower": 0.148},
        dialog={"active": False, "visual_active": False},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "dialogue"


def test_v2_menu_or_text_with_dark_textbox_presses_a_without_visual_active_flag():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        3,
        50,
        11,
        party=[{"slot": 1, "species_id": 158, "level": 6, "hp": 22, "max_hp": 22}],
        visual={"screen_class": "menu_or_text", "visual_textbox_active": False, "bright_lower": 0.812, "dark_lower": 0.183},
        dialog={"active": False, "visual_active": False},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "dialogue"


def test_v2_visual_dialogue_false_positive_does_not_press_a():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        3,
        45,
        9,
        party=[{"slot": 1, "species_id": 158, "level": 6, "hp": 23, "max_hp": 23}],
        visual={"screen_class": "dialogue", "visual_textbox_active": False, "bright_lower": 0.4},
        dialog={"active": False, "visual_active": False},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions != ["press_a"]
    assert nav["path_source"] != "dialogue"


def test_v2_bright_dialogue_panel_false_positive_does_not_press_a():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        4,
        6,
        4,
        party=[{"slot": 1, "species_id": 158, "level": 6, "hp": 23, "max_hp": 23}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "event_flags_available": True,
                "got_mystery_egg_from_mr_pokemon": True,
                "gave_mystery_egg_to_elm": False,
            }
        },
        visual={"screen_class": "menu_or_text", "visual_textbox_active": False, "bright_dialogue_panel": True, "bright_lower": 0.86},
        dialog={"active": False, "visual_active": False},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions != ["press_a"]
    assert nav["path_source"] != "dialogue"


def test_recovery_does_not_record_blocked_edge_for_elm_phone_call():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        1,
        17,
        6,
        party=[{"slot": 1, "species_id": 158, "level": 5, "hp": 20, "max_hp": 20}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "event_flags_available": True,
                "got_mystery_egg_from_mr_pokemon": True,
                "elm_called_about_stolen_pokemon": False,
            }
        },
        visual={"screen_class": "menu_or_text", "visual_textbox_active": False},
    )

    player.record_action_outcome("walk_down", False, "position_did_not_advance", before_state=state)

    assert player.blocked_edges_by_map == {}
    assert player.stuck_counter == 0


def test_v2_elm_phone_call_continues_after_flag_flips():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        1,
        17,
        6,
        party=[{"slot": 1, "species_id": 158, "level": 5, "hp": 20, "max_hp": 20}],
        flags={
            "derived_story_flags": {
                "has_starter": True,
                "event_flags_available": True,
                "got_mystery_egg_from_mr_pokemon": True,
                "elm_called_about_stolen_pokemon": True,
                "gave_mystery_egg_to_elm": False,
            }
        },
        visual={"screen_class": "menu_or_text", "visual_textbox_active": False, "bright_lower": 0.8},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "elm_phone_call"


def test_v2_after_starter_routes_new_bark_to_route29():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        4,
        6,
        3,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_down"]
    assert nav["target"]["map_group"] == ROUTE29_TARGET.map_key[0]
    assert nav["target"]["map_number"] == ROUTE29_TARGET.map_key[1]


def test_v2_after_starter_routes_route29_to_cherrygrove():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        3,
        59,
        8,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_left"]
    assert nav["target"]["map_group"] == CHERRYGROVE_TARGET.map_key[0]
    assert nav["target"]["map_number"] == CHERRYGROVE_TARGET.map_key[1]


def test_v2_after_starter_routes_cherrygrove_to_route30():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        3,
        39,
        6,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_left"]
    assert nav["target"]["map_group"] == ROUTE30_TARGET.map_key[0]
    assert nav["target"]["map_number"] == ROUTE30_TARGET.map_key[1]


def test_v2_after_starter_routes_route30_to_route31():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        1,
        7,
        53,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_up"]
    assert nav["target"]["map_group"] == ROUTE31_TARGET.map_key[0]
    assert nav["target"]["map_number"] == ROUTE31_TARGET.map_key[1]


def test_select_route_target_uses_event_flags_to_visit_mr_pokemon_before_route31():
    target = select_route_target(
        make_state(
            26,
            1,
            7,
            53,
            party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
            flags={
                "derived_story_flags": {
                    "event_flags_available": True,
                    "has_starter": True,
                    "got_mystery_egg_from_mr_pokemon": False,
                    "gave_mystery_egg_to_elm": False,
                }
            },
        )
    )

    assert target == MR_POKEMON_HOUSE_TARGET


def test_select_route_target_uses_event_flags_to_return_egg_to_elm():
    target = select_route_target(
        make_state(
            26,
            1,
            17,
            5,
            party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
            flags={
                "derived_story_flags": {
                    "event_flags_available": True,
                    "has_starter": True,
                    "got_mystery_egg_from_mr_pokemon": True,
                    "gave_mystery_egg_to_elm": False,
                }
            },
        )
    )

    assert target == ELMS_LAB_RETURN_TARGET


def test_v2_arrived_at_mr_pokemon_presses_a():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        10,
        3,
        5,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        flags={
            "derived_story_flags": {
                "event_flags_available": True,
                "has_starter": True,
                "got_mystery_egg_from_mr_pokemon": False,
                "gave_mystery_egg_to_elm": False,
            }
        },
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "story_interact"


def test_v2_arrived_at_elm_return_faces_up_before_press_a():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        5,
        5,
        3,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        flags={
            "derived_story_flags": {
                "event_flags_available": True,
                "has_starter": True,
                "got_mystery_egg_from_mr_pokemon": True,
                "gave_mystery_egg_to_elm": False,
            }
        },
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_up"]
    assert nav["path_source"] == "story_interact"


def test_v2_arrived_at_elm_return_presses_a_after_facing_attempt():
    player = GoldAutoplayerV2()
    player.elm_return_face_up_attempted_at = (5, 3)
    state = make_state(
        24,
        5,
        5,
        3,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        flags={
            "derived_story_flags": {
                "event_flags_available": True,
                "has_starter": True,
                "got_mystery_egg_from_mr_pokemon": True,
                "gave_mystery_egg_to_elm": False,
            }
        },
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "story_interact"


def test_v2_after_starter_routes_route31_to_violet_gate():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        2,
        24,
        17,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_up"]
    assert nav["target"]["map_group"] == VIOLET_GATE_TARGET.map_key[0]
    assert nav["target"]["map_number"] == VIOLET_GATE_TARGET.map_key[1]


def test_v2_route31_to_violet_mart_ignores_unusable_direct_violet_connection():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        2,
        24,
        17,
        party=[{"slot": 1, "species_id": 158, "level": 12, "hp": 26, "max_hp": 26}],
        bag=[{"item_id": 0x12, "quantity": 1}],
        flags={
            "derived_story_flags": {
                "event_flags_available": True,
                "has_starter": True,
                "learned_to_catch_pokemon": True,
                "gave_mystery_egg_to_elm": True,
            }
        },
    )
    state["player"]["money"] = 1344

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_up"]
    assert nav["path_source"] == "cross_map_static_registry"
    assert nav["map_path"][:2] == [
        {"map_group": 26, "map_number": 2},
        {"map_group": 26, "map_number": 11},
    ]
    assert nav["target"]["map_group"] == VIOLET_MART_BUY_TARGET.map_key[0]
    assert nav["target"]["map_number"] == VIOLET_MART_BUY_TARGET.map_key[1]


def test_v2_after_starter_routes_violet_gate_to_violet_city():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        11,
        9,
        4,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_left"]
    assert nav["target"]["map_group"] == VIOLET_CITY_TARGET.map_key[0]
    assert nav["target"]["map_number"] == VIOLET_CITY_TARGET.map_key[1]


def test_select_route_target_does_not_deadlock_single_ready_starter_when_broke():
    target = select_route_target(
        make_state(
            10,
            5,
            39,
            24,
            party=[{"slot": 1, "species_id": 155, "level": 12, "hp": 26, "max_hp": 26}],
            flags={"derived_story_flags": {"learned_to_catch_pokemon": True, "gave_mystery_egg_to_elm": True}},
        )
    )

    assert target == FALKNER_TARGET


def test_select_route_target_routes_trained_team_to_gym_before_zephyr():
    target = select_route_target(
        make_state(
            10,
            5,
            39,
            24,
            party=[
                {"slot": 1, "species_id": 155, "level": 12, "hp": 26, "max_hp": 26},
                {"slot": 2, "species_id": 179, "level": 7, "hp": 20, "max_hp": 20},
                {"slot": 3, "species_id": 16, "level": 6, "hp": 18, "max_hp": 18},
            ],
            flags={"derived_story_flags": {"learned_to_catch_pokemon": True, "gave_mystery_egg_to_elm": True}},
        )
    )

    assert target == FALKNER_TARGET


def test_select_route_target_grinds_before_falkner_when_underleveled_and_broke():
    state = make_state(
        10,
        5,
        39,
        24,
        party=[{"slot": 1, "species_id": 155, "level": 9, "hp": 26, "max_hp": 26}],
        flags={"derived_story_flags": {"learned_to_catch_pokemon": True, "gave_mystery_egg_to_elm": True}},
    )
    state["player"]["money"] = 13

    assert select_route_target(state) == ROUTE31_GRIND_TARGET


def test_select_route_target_stops_after_zephyr_badge():
    state = make_state(10, 5, 39, 24, party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}, {"slot": 2, "species_id": 0xFD, "level": 5, "hp": 20, "max_hp": 20}])
    state["player"]["badges"] = ["Zephyr"]

    assert select_route_target(state) == ROUTE32_TARGET


def test_select_route_target_after_zephyr_routes_route32_to_union_cave():
    state = make_state(10, 1, 14, 0, party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}, {"slot": 2, "species_id": 0xFD, "level": 5, "hp": 20, "max_hp": 20}])
    state["player"]["badges"] = ["Zephyr"]

    assert select_route_target(state) == UNION_CAVE_TARGET


def test_select_route_target_after_zephyr_gets_egg_before_route32_guard():
    state = make_state(10, 1, 18, 6, party=[{"slot": 1, "species_id": 156, "level": 15, "hp": 17, "max_hp": 45}])
    state["player"]["badges"] = ["Zephyr"]

    assert select_route_target(state) == VIOLET_POKECENTER_AIDE_TARGET


def test_v2_pokecenter_aide_interact_faces_up_before_press_a():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        10,
        4,
        4,
        party=[{"slot": 1, "species_id": 156, "level": 15, "hp": 45, "max_hp": 45}],
    )
    state["player"]["badges"] = ["Zephyr"]

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_up"]
    assert nav["path_source"] == "pokecenter_aide_interact"


def test_v2_pokecenter_aide_interact_recovers_from_nurse_counter_tile():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        10,
        3,
        3,
        party=[{"slot": 1, "species_id": 156, "level": 15, "hp": 45, "max_hp": 45}],
    )
    state["player"]["badges"] = ["Zephyr"]

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_down"]
    assert nav["path_source"] == "pokecenter_aide_interact"


def test_v2_pokecenter_aide_dialogue_advances_open_text_before_recovery():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        10,
        3,
        3,
        party=[{"slot": 1, "species_id": 156, "level": 15, "hp": 45, "max_hp": 45}],
        visual={"screen_class": "dialogue", "visual_textbox_active": True},
    )
    state["player"]["badges"] = ["Zephyr"]
    key = player.state_key_for_learning(state)
    for _ in range(4):
        player.recent_learning_transitions.append({"action": "press_a", "before": key, "after": key})

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "pokecenter_aide_dialogue"


def test_v2_pokecenter_aide_dialogue_recovery_after_bounded_press_a():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        10,
        3,
        3,
        party=[{"slot": 1, "species_id": 156, "level": 15, "hp": 45, "max_hp": 45}],
        visual={"screen_class": "dialogue", "visual_textbox_active": True},
    )
    state["player"]["badges"] = ["Zephyr"]
    key = player.state_key_for_learning(state)
    for _ in range(8):
        player.recent_learning_transitions.append({"action": "press_a", "before": key, "after": key})

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_down"]
    assert nav["path_source"] == "pokecenter_aide_nurse_dialogue_escape"


def test_v2_pokecenter_aide_dialogue_escapes_after_press_a_failure():
    player = GoldAutoplayerV2()
    player.button_failure_count = 1
    state = make_state(
        10,
        10,
        3,
        3,
        party=[{"slot": 1, "species_id": 156, "level": 15, "hp": 45, "max_hp": 45}],
        visual={"screen_class": "dialogue", "visual_textbox_active": True},
    )
    state["player"]["badges"] = ["Zephyr"]

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_down"]
    assert nav["path_source"] == "pokecenter_aide_nurse_dialogue_escape"


def test_v2_pokecenter_aide_ignores_stale_visual_dialogue_after_button_failures():
    player = GoldAutoplayerV2()
    player.button_failure_count = 2
    state = make_state(
        10,
        10,
        4,
        6,
        party=[{"slot": 1, "species_id": 156, "level": 15, "hp": 45, "max_hp": 45}],
        dialog={"active": True, "window_stack_plausible": False, "visual_active": True},
        visual={"screen_class": "menu_or_text", "visual_textbox_active": True},
    )
    state["player"]["badges"] = ["Zephyr"]

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_down"]
    assert nav["path_source"] == "pokecenter_aide_stale_visual_escape"


def test_v2_pokecenter_aide_ignores_stale_visual_dialogue_on_wrong_npc_tile():
    player = GoldAutoplayerV2()
    player.button_failure_count = 1
    state = make_state(
        10,
        10,
        6,
        2,
        party=[{"slot": 1, "species_id": 156, "level": 15, "hp": 45, "max_hp": 45}],
        dialog={"active": True, "window_stack_plausible": False, "visual_active": True},
        visual={"screen_class": "menu_or_text", "visual_textbox_active": True},
    )
    state["player"]["badges"] = ["Zephyr"]

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_down"]
    assert nav["path_source"] == "pokecenter_aide_stale_visual_escape"


def test_v2_pokecenter_aide_dialogue_escape_confirms_no_prompt():
    player = GoldAutoplayerV2()
    player.stale_dialogue_recovery_index = 1
    state = make_state(
        10,
        10,
        3,
        3,
        party=[{"slot": 1, "species_id": 156, "level": 15, "hp": 45, "max_hp": 45}],
        visual={"screen_class": "dialogue", "visual_textbox_active": True},
    )
    state["player"]["badges"] = ["Zephyr"]
    key = player.state_key_for_learning(state)
    for _ in range(8):
        player.recent_learning_transitions.append({"action": "press_a", "before": key, "after": key})

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "pokecenter_aide_nurse_dialogue_escape"


def test_v2_pokecenter_aide_interact_handles_live_offset_tile():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        10,
        3,
        4,
        party=[{"slot": 1, "species_id": 156, "level": 15, "hp": 45, "max_hp": 45}],
    )
    state["player"]["badges"] = ["Zephyr"]

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_right"]
    assert nav["path_source"] == "pokecenter_aide_interact"


def test_v2_pokecenter_aide_interact_recovers_from_lower_center_tile():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        10,
        4,
        6,
        party=[{"slot": 1, "species_id": 156, "level": 15, "hp": 45, "max_hp": 45}],
    )
    state["player"]["badges"] = ["Zephyr"]

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_up"]
    assert nav["path_source"] == "pokecenter_aide_interact"


def test_v2_pokecenter_aide_interact_routes_from_right_side():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        10,
        9,
        2,
        party=[{"slot": 1, "species_id": 156, "level": 15, "hp": 45, "max_hp": 45}],
    )
    state["player"]["badges"] = ["Zephyr"]

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_left"]
    assert nav["path_source"] == "pokecenter_aide_interact"


def test_v2_pokecenter_aide_interact_routes_from_above_target_column():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        10,
        4,
        2,
        party=[{"slot": 1, "species_id": 156, "level": 15, "hp": 45, "max_hp": 45}],
    )
    state["player"]["badges"] = ["Zephyr"]

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_down"]
    assert nav["path_source"] == "pokecenter_aide_interact"


def test_v2_pokecenter_aide_interact_presses_a_after_facing_attempt():
    player = GoldAutoplayerV2()
    player.pokecenter_aide_face_up_attempted_at = (4, 4)
    state = make_state(
        10,
        10,
        4,
        4,
        party=[{"slot": 1, "species_id": 156, "level": 15, "hp": 45, "max_hp": 45}],
    )
    state["player"]["badges"] = ["Zephyr"]

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "pokecenter_aide_interact"


def test_select_route_target_after_zephyr_routes_union_cave_to_route33():
    state = make_state(3, 37, 17, 3, party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}])
    state["player"]["badges"] = ["Zephyr"]

    assert select_route_target(state) == ROUTE33_TARGET


def test_select_route_target_after_zephyr_routes_route33_to_azalea():
    state = make_state(8, 6, 11, 9, party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}])
    state["player"]["badges"] = ["Zephyr"]

    assert select_route_target(state) == AZALEA_TARGET


def test_select_route_target_after_zephyr_routes_azalea_to_slowpoke_well():
    state = make_state(8, 7, 39, 14, party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}])
    state["player"]["badges"] = ["Zephyr"]

    assert select_route_target(state) == SLOWPOKE_WELL_TARGET


def test_v2_violet_city_routes_to_violet_gym_lobby():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        5,
        39,
        24,
        party=[
            {"slot": 1, "species_id": 155, "level": 12, "hp": 26, "max_hp": 26},
            {"slot": 2, "species_id": 179, "level": 7, "hp": 20, "max_hp": 20},
            {"slot": 3, "species_id": 16, "level": 6, "hp": 18, "max_hp": 18},
        ],
        flags={"derived_story_flags": {"learned_to_catch_pokemon": True, "gave_mystery_egg_to_elm": True}},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_left"]
    assert nav["target"]["map_group"] == 10
    assert nav["target"]["map_number"] == 7


def test_v2_in_violet_gym_routes_to_falkner():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        7,
        4,
        15,
        party=[
            {"slot": 1, "species_id": 155, "level": 12, "hp": 26, "max_hp": 26},
            {"slot": 2, "species_id": 179, "level": 7, "hp": 20, "max_hp": 20},
            {"slot": 3, "species_id": 16, "level": 6, "hp": 18, "max_hp": 18},
        ],
        flags={"derived_story_flags": {"learned_to_catch_pokemon": True, "gave_mystery_egg_to_elm": True}},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_up"]
    assert nav["target"]["name"] == "Falkner interaction tile"


def test_v2_arrived_at_falkner_presses_a():
    player = GoldAutoplayerV2()
    state = make_state(
        10,
        7,
        5,
        2,
        party=[
            {"slot": 1, "species_id": 155, "level": 12, "hp": 26, "max_hp": 26},
            {"slot": 2, "species_id": 179, "level": 7, "hp": 20, "max_hp": 20},
            {"slot": 3, "species_id": 16, "level": 6, "hp": 18, "max_hp": 18},
        ],
        flags={"derived_story_flags": {"learned_to_catch_pokemon": True, "gave_mystery_egg_to_elm": True}},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "story_interact"


def test_v2_dialogue_blocks_route_planning_after_starter():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        5,
        4,
        11,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        dialog={"active": True},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "dialogue"


def test_v2_trainer_battle_blocks_without_usable_menu_after_starter():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        5,
        4,
        11,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        battle={"in_battle": True, "type_id": 2, "enemy_level": 6},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == []
    assert nav["path_source"] == "battle_fallback"
    assert nav["battle_policy"] == "trainer_fight_blocked_missing_menu_state"


def test_v2_trainer_battle_missing_menu_uses_safe_fight_sequence():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        3,
        33,
        7,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        battle={"in_battle": True, "type_id": 2, "enemy_level": 4, "trusted": True, "enemy_hp": 14, "enemy_hp_trusted": True},
        visual={"screen_class": "menu_or_text", "visual_textbox_active": False, "bright_lower": 0.812, "dark_lower": 0.175, "lower_contrast_gap": 15.061},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_b"]
    assert nav["path_source"] == "battle_fallback"
    assert nav["battle_policy"] == "trainer_fight_missing_menu_sequence"
    assert nav["reason"].startswith("trainer battle menu RAM unavailable")


def test_v2_trainer_battle_missing_menu_handles_overworld_or_battle_visual_class():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        2,
        18,
        12,
        party=[{"slot": 1, "species_id": 155, "level": 8, "hp": 22, "max_hp": 27}],
        battle={"in_battle": True, "type_id": 2, "enemy_level": 2, "trusted": True, "enemy_hp": 14, "enemy_hp_trusted": True},
        visual={"screen_class": "overworld_or_battle", "visual_textbox_active": False, "bright_lower": 0.818, "dark_lower": 0.182, "lower_contrast_gap": 33.271},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_b"]
    assert nav["battle_policy"] == "trainer_fight_missing_menu_sequence"


def test_v2_low_hp_trainer_battle_missing_menu_uses_potion_sequence():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        2,
        18,
        12,
        party=[{"slot": 1, "species_id": 155, "level": 8, "hp": 5, "max_hp": 24}],
        bag=[{"item_id": 18, "quantity": 1}],
        battle={"in_battle": True, "type_id": 2, "enemy_level": 2, "trusted": True, "enemy_hp": 14, "enemy_hp_trusted": True},
        visual={"screen_class": "overworld_or_battle", "visual_textbox_active": False, "bright_lower": 0.818, "dark_lower": 0.182, "lower_contrast_gap": 33.271},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_b"]
    assert nav["battle_policy"] == "trainer_fight_missing_menu_sequence"
    assert nav["reason"] == "trainer battle menu RAM unavailable; backing out and forcing Fight/first move"


def test_v2_low_hp_single_pokemon_heal_sequence_never_targets_switch_menu():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        2,
        18,
        12,
        party=[{"slot": 1, "species_id": 155, "level": 8, "hp": 5, "max_hp": 24}],
        bag=[{"item_id": 18, "quantity": 1}],
        battle={"in_battle": True, "type_id": 2, "enemy_level": 2, "trusted": True, "enemy_hp": 14, "enemy_hp_trusted": True},
        visual={"screen_class": "overworld_or_battle", "visual_textbox_active": False},
    )
    actions_seen = []
    for _ in range(6):
        actions, nav = player.choose_overworld_actions(state)
        action = actions[0]
        actions_seen.append(action)
        player.record_action_outcome(action, True, "visual_state_changed_after_press_a", "ok", state)

    assert actions_seen == ["press_b", "press_b", "press_up", "press_left", "press_a", "press_a"]
    assert "press_down" not in actions_seen
    assert "walk_down" not in actions_seen
    assert "press_right" not in actions_seen
    assert "walk_right" not in actions_seen


def test_v2_missing_menu_fight_sequence_avoids_zero_pp_first_move():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        3,
        33,
        7,
        party=[{"slot": 1, "species_id": 155, "level": 7, "hp": 4, "max_hp": 23, "moves": [33, 43, 108], "pp": [0, 30, 20]}],
        battle={"in_battle": True, "type_id": 2, "enemy_level": 5, "trusted": True, "enemy_hp": 2, "enemy_hp_trusted": True},
        visual={"screen_class": "menu_or_text", "visual_textbox_active": False},
    )
    actions_seen = []

    for _ in range(7):
        actions, nav = player.choose_overworld_actions(state)
        action = actions[0]
        actions_seen.append(action)
        player.record_action_outcome(action, True, "battle_missing_menu_sequence_step_posted", "ok", state)

    assert actions_seen == ["press_b", "press_b", "press_up", "press_left", "press_a", "press_down", "press_a"]
    assert nav["move_slot"] == 1
    assert nav["move_pp"] == [0, 30, 20]


def test_v2_missing_menu_fight_sequence_prefers_ember_over_tackle():
    player = GoldAutoplayerV2()
    state = make_state(
        3,
        29,
        5,
        4,
        party=[{"slot": 1, "species_id": 156, "level": 16, "hp": 5, "max_hp": 47, "moves": [33, 43, 108, 52], "pp": [17, 30, 20, 25]}],
        battle={"in_battle": True, "type_id": 2, "enemy_species_id": 95, "enemy_level": 11, "trusted": True, "enemy_hp": 13, "enemy_hp_trusted": True},
        visual={"screen_class": "menu_or_text", "visual_textbox_active": False},
    )

    actions_seen = []
    for _ in range(9):
        actions, nav = player.choose_overworld_actions(state)
        action = actions[0]
        actions_seen.append(action)
        player.record_action_outcome(action, True, "battle_missing_menu_sequence_step_posted", "ok", state)

    assert actions_seen == ["press_b", "press_b", "press_up", "press_left", "press_a", "press_down", "press_down", "press_down", "press_a"]
    assert nav["move_slot"] == 3


def test_v2_all_party_fainted_advances_blackout_instead_of_walking():
    player = GoldAutoplayerV2()
    state = make_state(
        3,
        29,
        5,
        4,
        party=[
            {"slot": 1, "species_id": 156, "level": 16, "hp": 0, "max_hp": 47},
            {"slot": 2, "species_id": 253, "level": 5, "hp": 0, "max_hp": 20},
        ],
        battle={"in_battle": False, "type_id": 0, "trusted": True},
        visual={"screen_class": "menu_or_text", "visual_textbox_active": False, "bright_lower": 0.8},
    )
    state["player"]["badges"] = ["Zephyr"]

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "blackout_recovery"


def test_v2_heal_sequence_direction_steps_do_not_open_button_circuit():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        2,
        18,
        12,
        party=[{"slot": 1, "species_id": 155, "level": 8, "hp": 5, "max_hp": 24}],
        bag=[{"item_id": 18, "quantity": 1}],
        battle={"in_battle": True, "type_id": 2, "enemy_level": 2, "trusted": True, "enemy_hp": 14, "enemy_hp_trusted": True},
        visual={"screen_class": "overworld_or_battle", "visual_textbox_active": False},
    )

    player.record_action_outcome("press_b", False, "press_b_no_progress", "ok", state)
    player.record_action_outcome("press_b", False, "press_b_no_progress", "ok", state)
    player.record_action_outcome("press_up", False, "direction_press_no_progress", "ok", state)
    player.record_action_outcome("press_left", False, "direction_press_no_progress", "ok", state)

    assert player.button_failure_count == 0
    assert player.recovery_level == 0
    assert player.trainer_missing_menu_fight_index == 4


def test_v2_single_pokemon_missing_menu_avoids_heal_switch_drift_before_attack_fallback():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        2,
        18,
        12,
        party=[{"slot": 1, "species_id": 155, "level": 8, "hp": 5, "max_hp": 24}],
        bag=[{"item_id": 18, "quantity": 1}],
        battle={"in_battle": True, "type_id": 2, "enemy_level": 2, "trusted": True, "enemy_hp": 14, "enemy_hp_trusted": True},
        visual={"screen_class": "overworld_or_battle", "visual_textbox_active": False},
    )
    actions, nav = player.choose_overworld_actions(state)

    assert player.trainer_missing_menu_heal_cycles == 0
    assert actions == ["press_b"]
    assert nav["battle_policy"] == "trainer_fight_missing_menu_sequence"


def test_v2_repeated_battle_fallback_without_hp_change_opens_recovery(tmp_path):
    player = GoldAutoplayerV2(data_dir=tmp_path)
    state = make_state(
        26,
        2,
        18,
        12,
        party=[{"slot": 1, "species_id": 155, "level": 8, "hp": 5, "max_hp": 24}],
        battle={"in_battle": True, "type_id": 2, "enemy_level": 2, "trusted": True, "enemy_hp": 14, "enemy_max_hp": 14, "enemy_hp_trusted": True},
    )
    nav = {"path_source": "battle_fallback"}

    for _ in range(6):
        player.record_battle_semantic_progress(state, state, "press_a", nav)

    assert player.recovery_level == 2
    assert player.verification_reason == "battle_no_semantic_progress"


def test_v2_trainer_battle_uses_fight_cursor_when_available():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        5,
        4,
        11,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        battle={"in_battle": True, "type_id": 2, "enemy_level": 6},
        menu={"active": True, "name": "battle_main", "cursor": "fight", "confidence": "medium"},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["controller"] == "fight"
    assert nav["battle_policy"] == "trainer_fight"


def test_unknown_battle_type_blocks_actions():
    state = make_state(
        24,
        5,
        4,
        11,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        battle={"in_battle": True, "trusted": True},
    )

    actions, nav = choose_battle_actions(state)

    assert actions == []
    assert nav["battle_policy"] == "battle_blocked_unknown_type"


def test_v2_battle_dialog_advances_text():
    state = make_state(
        24,
        3,
        50,
        8,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 16, "enemy_level": 3},
        dialog={"active": True, "window_stack_plausible": True},
    )

    actions, nav = choose_battle_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "battle_fallback"
    assert nav["battle_policy"] == "advance_dialog"


def test_v2_battle_post_ko_advances_text_without_menu_state():
    state = make_state(
        24,
        3,
        50,
        8,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        battle={"in_battle": True, "type_id": 2, "enemy_level": 5, "enemy_hp": 0, "enemy_max_hp": 21, "enemy_hp_trusted": True},
        menu={"active": False, "confidence": "none", "window_stack_plausible": False, "blocked_reason": "implausible_window_stack"},
    )

    actions, nav = choose_battle_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "battle_fallback"
    assert nav["battle_policy"] == "advance_post_ko_text"


def test_v2_wild_battle_runs():
    state = make_state(
        24,
        3,
        50,
        8,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 16, "enemy_level": 3},
    )

    actions, nav = choose_battle_actions(state)

    assert actions == ["press_b"]
    assert nav["battle_policy"] == "wild_run"


def test_v2_wild_battle_grinds_before_falkner_when_no_balls():
    state = make_state(
        26,
        1,
        7,
        27,
        party=[{"slot": 1, "species_id": 155, "level": 9, "hp": 26, "max_hp": 26}],
        flags={"derived_story_flags": {"has_zephyr_badge": False}},
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 10, "enemy_level": 3},
        menu={"active": True, "name": "battle_main", "cursor": "fight", "confidence": "medium"},
    )

    actions, nav = choose_battle_actions(state)

    assert actions == ["press_a"]
    assert nav["battle_policy"] == "wild_grind"


def test_v2_route31_grind_fights_safe_non_target_even_with_balls():
    state = make_state(
        26,
        2,
        17,
        12,
        party=[
            {"slot": 1, "species_id": 155, "level": 10, "hp": 30, "max_hp": 30},
            {"slot": 2, "species_id": 16, "species": "Pidgey", "level": 3, "hp": 12, "max_hp": 12},
            {"slot": 3, "species_id": 19, "species": "Rattata", "level": 3, "hp": 12, "max_hp": 12},
            {"slot": 4, "species_id": 165, "species": "Ledyba", "level": 3, "hp": 12, "max_hp": 12},
        ],
        flags={"derived_story_flags": {"has_zephyr_badge": False, "gave_mystery_egg_to_elm": True, "learned_to_catch_pokemon": True}},
        bag=[{"item_id": 0x05, "item": "Poke Ball", "quantity": 2}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 10, "enemy_level": 3},
        menu={"active": True, "name": "battle_main", "cursor": "fight", "confidence": "medium"},
    )

    actions, nav = choose_battle_actions(state)

    assert actions == ["press_a"]
    assert nav["battle_policy"] == "wild_grind"


def test_v2_wild_battle_still_catches_when_balls_available():
    state = make_state(
        26,
        1,
        7,
        27,
        party=[{"slot": 1, "species_id": 155, "level": 9, "hp": 26, "max_hp": 26}],
        flags={"derived_story_flags": {"has_zephyr_badge": False}},
        bag=[{"item_id": 2, "quantity": 1}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 16, "enemy_level": 3, "enemy_hp": 4, "enemy_max_hp": 12, "enemy_hp_trusted": True},
        menu={"active": True, "name": "battle_main", "cursor": "pack", "confidence": "medium"},
    )

    actions, nav = choose_battle_actions(state)

    assert actions == ["press_a"]
    assert nav["battle_policy"] == "capture_open_pack"


def test_v2_missing_wild_menu_opens_pack_for_low_level_capture():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        1,
        7,
        26,
        party=[{"slot": 1, "species_id": 155, "species": "Cyndaquil", "level": 9, "hp": 17, "max_hp": 28}],
        flags={"derived_story_flags": {"has_zephyr_badge": False, "gave_mystery_egg_to_elm": True, "learned_to_catch_pokemon": True}},
        bag=[{"item_id": 0x05, "item": "Poke Ball", "quantity": 3}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 16, "enemy_level": 3, "enemy_hp": 16, "enemy_max_hp": 16, "enemy_hp_trusted": True, "trusted": True},
        dialog={"active": False},
        visual={"screen_class": "menu_or_text", "visual_textbox_active": False},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == [BATTLE_CAPTURE_OPEN_PACK_SEQUENCE[0]]
    assert nav["battle_policy"] == "capture_missing_menu_open_pack_sequence"
    assert nav["capture_intent"] == "throw_ball"


def test_v2_missing_wild_menu_suppresses_blind_ball_throw_after_pack_open():
    player = GoldAutoplayerV2()
    player.battle_capture_sequence_index = len(BATTLE_CAPTURE_OPEN_PACK_SEQUENCE)
    state = make_state(
        24,
        3,
        44,
        11,
        party=[{"slot": 1, "species_id": 155, "species": "Cyndaquil", "level": 5, "hp": 11, "max_hp": 19}],
        flags={"derived_story_flags": {"has_zephyr_badge": False, "gave_mystery_egg_to_elm": True, "learned_to_catch_pokemon": True}},
        bag=[{"slot": 1, "pocket": "balls", "item_id": 0x05, "item": "Poke Ball", "quantity": 5, "trusted": True}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 16, "enemy_level": 3, "enemy_hp": 11, "enemy_max_hp": 16, "enemy_hp_trusted": True, "trusted": True},
        menu={"active": False, "confidence": "none", "needs_stronger_decode": True, "blocked_reason": "implausible_window_stack", "window_stack_plausible": False},
        dialog={"active": False},
        visual={"screen_class": "overworld_or_battle", "visual_textbox_active": False},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_b"]
    assert nav["battle_policy"] == "capture_abandoned_to_fight_missing_menu_sequence"
    assert nav["controller_state"] == "fallback_capture_abandoned_to_fight_sequence"
    assert nav["blocked_reason"] == "implausible_window_stack"
    assert nav["capture_safety"][-1]["reason"] == "suppress_blind_throw_after_ambiguous_pack_open"


def test_v2_capture_throw_index_does_not_advance_during_open_pack_sequence():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        3,
        44,
        11,
        party=[{"slot": 1, "species_id": 155, "species": "Cyndaquil", "level": 5, "hp": 11, "max_hp": 19}],
        flags={"derived_story_flags": {"has_zephyr_badge": False, "gave_mystery_egg_to_elm": True, "learned_to_catch_pokemon": True}},
        bag=[{"slot": 1, "pocket": "balls", "item_id": 0x05, "item": "Poke Ball", "quantity": 5, "trusted": True}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 16, "enemy_level": 3, "enemy_hp": 11, "enemy_max_hp": 16, "enemy_hp_trusted": True, "trusted": True},
        menu={"active": False, "confidence": "none", "needs_stronger_decode": True, "blocked_reason": "implausible_window_stack", "window_stack_plausible": False},
        dialog={"active": False},
        visual={"screen_class": "overworld_or_battle", "visual_textbox_active": False},
    )

    for action in BATTLE_CAPTURE_OPEN_PACK_SEQUENCE:
        player.record_action_outcome(action, verified=False, reason="no_semantic_progress", post_result="ok", before_state=state)

    assert player.battle_capture_sequence_index == len(BATTLE_CAPTURE_OPEN_PACK_SEQUENCE)
    assert player.battle_capture_throw_sequence_index == 0


def test_v2_resets_capture_sequence_when_new_battle_identity_detected():
    player = GoldAutoplayerV2()
    player.battle_capture_sequence_index = 3
    player.battle_capture_throw_sequence_index = 2
    first = make_state(
        24,
        3,
        44,
        11,
        party=[{"slot": 1, "species_id": 155, "species": "Cyndaquil", "level": 5, "hp": 11, "max_hp": 19}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 16, "enemy_level": 3, "enemy_hp": 11, "enemy_max_hp": 16, "trusted": True},
    )
    second = make_state(
        24,
        3,
        44,
        11,
        party=[{"slot": 1, "species_id": 155, "species": "Cyndaquil", "level": 5, "hp": 11, "max_hp": 19}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 19, "enemy_level": 4, "enemy_hp": 13, "enemy_max_hp": 13, "trusted": True},
    )

    player.sync_battle_identity(first)
    player.battle_capture_sequence_index = 3
    player.battle_capture_throw_sequence_index = 2
    player.sync_battle_identity(second)

    assert player.battle_capture_sequence_index == 0
    assert player.battle_capture_throw_sequence_index == 0
    assert player.last_battle_identity_change["reason"] == "battle_identity_changed"


def test_v2_capture_fallback_abandons_to_fight_after_repeated_noop_cycles():
    player = GoldAutoplayerV2()
    player.battle_capture_fallback_cycles = 2
    state = make_state(
        24,
        3,
        44,
        11,
        party=[{"slot": 1, "species_id": 155, "species": "Cyndaquil", "level": 5, "hp": 11, "max_hp": 19, "moves": [33, 43], "pp": [32, 30]}],
        flags={"derived_story_flags": {"has_zephyr_badge": False, "gave_mystery_egg_to_elm": True, "learned_to_catch_pokemon": True}},
        bag=[{"slot": 1, "pocket": "balls", "item_id": 0x05, "item": "Poke Ball", "quantity": 5, "trusted": True}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 16, "enemy_level": 3, "enemy_hp": 11, "enemy_max_hp": 16, "enemy_hp_trusted": True, "trusted": True},
        menu={"active": False, "confidence": "none", "needs_stronger_decode": True, "blocked_reason": "implausible_window_stack", "window_stack_plausible": False},
        dialog={"active": False},
        visual={"screen_class": "overworld_or_battle", "visual_textbox_active": False},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_b"]
    assert nav["battle_policy"] == "capture_abandoned_to_fight_missing_menu_sequence"
    assert nav["controller_state"] == "fallback_capture_abandoned_to_fight_sequence"


def test_v2_capture_fallback_abandons_to_fight_after_no_progress_count_even_when_recovery_open():
    player = GoldAutoplayerV2()
    player.recovery_level = 2
    player.battle_no_progress_count = 999
    state = make_state(
        24,
        3,
        44,
        11,
        party=[{"slot": 1, "species_id": 155, "species": "Cyndaquil", "level": 5, "hp": 11, "max_hp": 19, "moves": [33, 43], "pp": [32, 30]}],
        flags={"derived_story_flags": {"has_zephyr_badge": False, "gave_mystery_egg_to_elm": True, "learned_to_catch_pokemon": True}},
        bag=[{"slot": 1, "pocket": "balls", "item_id": 0x05, "item": "Poke Ball", "quantity": 5, "trusted": True}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 16, "enemy_level": 3, "enemy_hp": 11, "enemy_max_hp": 16, "enemy_hp_trusted": True, "trusted": True},
        menu={"active": False, "confidence": "none", "needs_stronger_decode": True, "blocked_reason": "implausible_window_stack", "window_stack_plausible": False},
        dialog={"active": False},
        visual={"screen_class": "overworld_or_battle", "visual_textbox_active": False},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_b"]
    assert nav["battle_policy"] == "capture_abandoned_to_fight_missing_menu_sequence"
    assert nav["controller_state"] == "fallback_capture_abandoned_to_fight_sequence"
    assert nav["battle_no_progress_count"] == 999

    player.record_action_outcome("press_b", verified=False, reason="battle_no_semantic_progress", post_result="ok", before_state=state)
    assert player.trainer_missing_menu_fight_index == 1


def test_trainer_battle_with_enemy_species_does_not_use_wild_run():
    state = make_state(
        24,
        3,
        50,
        8,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        battle={"in_battle": True, "type_id": 2, "wild_species_id": 0xB3, "enemy_level": 6},
        menu={"active": True, "name": "battle_main", "cursor": "fight", "confidence": "medium"},
    )

    actions, nav = choose_battle_actions(state)

    assert actions == ["press_a"]
    assert nav["battle_policy"] == "trainer_fight"


def test_v2_wild_low_hp_battle_runs():
    state = make_state(
        24,
        3,
        50,
        8,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 3, "max_hp": 20}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 16, "enemy_level": 3},
    )

    actions, nav = choose_battle_actions(state)

    assert actions == ["press_b"]
    assert nav["battle_policy"] == "wild_run_low_hp"


def test_run_controller_uses_confirmed_run_cursor():
    state = make_state(
        24,
        3,
        50,
        8,
        menu={"active": True, "name": "battle_main", "cursor": "run", "confidence": "medium"},
    )

    actions, nav = choose_run_actions(state)

    assert actions == ["press_a"]
    assert nav["controller"] == "run"
    assert nav["controller_state"] == "confirm"


def test_run_controller_moves_toward_run_cursor():
    state = make_state(
        24,
        3,
        50,
        8,
        menu={"active": True, "name": "battle_main", "cursor": "pokemon", "confidence": "medium"},
    )

    actions, nav = choose_run_actions(state)

    assert actions == ["walk_right"]
    assert nav["controller_state"] == "move_to_run"


def test_run_controller_preserves_legacy_b_fallback_without_menu():
    actions, nav = choose_run_actions(make_state(24, 3, 50, 8))

    assert actions == ["press_b"]
    assert nav["controller_state"] == "fallback_press_b_missing_menu"


def test_v2_untrusted_battle_ram_blocks_battle_fallback_actions():
    state = make_state(
        24,
        3,
        50,
        8,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        battle={"in_battle": True, "wild_species_id": 16, "enemy_level": 3, "trusted": False, "type_id": 255},
    )

    actions, nav = choose_battle_actions(state)

    assert actions == []
    assert nav["battle_policy"] == "battle_blocked_untrusted_ram"
    assert nav["battle_type_id"] == 255


def test_v2_battle_resume_recovery_after_recovery_circuit():
    player = GoldAutoplayerV2()
    player.recovery_level = 2
    state = make_state(
        24,
        3,
        50,
        8,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 16, "enemy_level": 3, "trusted": True},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_b"]
    assert nav["path_source"] == "battle_resume_recovery"
    assert nav["battle_policy"] == "resume_after_no_progress"


def test_v2_ambiguous_dialogue_uses_bounded_resume_probe():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        5,
        5,
        5,
        dialog={"active": True, "window_stack_plausible": False, "visual_active": False},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["wait_300"]
    assert nav["path_source"] == "ambiguous_dialogue_recovery"
    assert nav["dialogue_reason"] == "window_stack_implausible"


def test_observed_menu_rejects_explicit_no_confidence_ram_menu():
    state = make_state(
        24,
        3,
        50,
        8,
        menu={"active": False, "name": "battle_main", "cursor": "pack", "confidence": "none"},
    )

    assert observed_menu(state) == {}


def test_observed_menu_accepts_confident_ram_menu():
    state = make_state(
        24,
        3,
        50,
        8,
        menu={"active": True, "name": "battle_main", "cursor": "pack", "confidence": "medium"},
    )

    assert observed_menu(state)["cursor"] == "pack"


def test_observed_menu_rejects_menu_needing_stronger_decode():
    state = make_state(
        24,
        3,
        50,
        8,
        menu={"active": True, "name": "battle_main", "cursor": "pack", "confidence": "medium", "needs_stronger_decode": True},
    )

    assert observed_menu(state) == {}


def test_observed_menu_rejects_implausible_window_stack():
    state = make_state(
        24,
        3,
        50,
        8,
        menu={"active": True, "name": "battle_main", "cursor": "pack", "confidence": "medium", "window_stack_plausible": False},
    )

    assert observed_menu(state) == {}


def test_observed_menu_rejects_legacy_battle_menu_without_confidence():
    state = make_state(
        24,
        3,
        50,
        8,
        battle={"in_battle": True, "menu": "battle_main", "cursor": "pack"},
    )

    assert observed_menu(state) == {}


def test_capture_does_not_press_a_on_low_confidence_pack_cursor():
    state = make_state(
        24,
        3,
        50,
        8,
        bag=[{"item_id": 0x05, "item": "Poke Ball", "quantity": 1, "trusted": True}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 0xB3, "enemy_level": 6, "trusted": True},
        menu={"active": True, "name": "battle_main", "cursor": "pack", "confidence": "low"},
    )

    actions, nav = choose_capture_actions(state)

    assert actions == []
    assert nav["battle_policy"] == "capture_blocked_missing_menu_state"
    assert nav["blocked_reason"] == "low_confidence"


def test_v2_wild_priority_species_with_ball_uses_capture_controller():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        3,
        50,
        8,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        bag=[{"item_id": 0x05, "item": "Poke Ball", "quantity": 2}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 0xB3, "enemy_level": 6, "enemy_hp": 8, "enemy_max_hp": 20, "enemy_hp_trusted": True},
        menu={"active": True, "name": "battle_main", "cursor": "pack", "confidence": "medium"},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["controller"] == "capture"
    assert nav["battle_policy"] == "capture_open_pack"


def test_capture_unknown_menu_blocks_without_running():
    state = make_state(
        24,
        3,
        50,
        8,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        bag=[{"item_id": 0x05, "item": "Poke Ball", "quantity": 2}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 0xB3, "enemy_level": 6},
    )

    actions, nav = choose_capture_actions(state)

    assert actions == []
    assert nav["battle_policy"] == "capture_blocked_missing_menu_state"
    assert nav["blocked_reason"] == "missing_menu_state"


def test_v2_priority_species_full_hp_uses_weaken_policy_with_trusted_menu():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        3,
        50,
        8,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        bag=[{"item_id": 0x05, "item": "Poke Ball", "quantity": 2}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 0xB3, "enemy_level": 6, "enemy_hp": 20, "enemy_max_hp": 20},
        menu={"active": True, "name": "battle_main", "cursor": "pack", "confidence": "medium"},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["walk_left"]
    assert nav["controller"] == "fight"
    assert nav["battle_policy"] == "wild_weaken"


def test_v2_dangerous_priority_species_throws_instead_of_weakens():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        3,
        50,
        8,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        bag=[{"item_id": 0x05, "item": "Poke Ball", "quantity": 2}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 0xB3, "enemy_level": 6, "enemy_hp": 20, "enemy_max_hp": 20, "enemy_moves": [120]},
        menu={"active": True, "name": "battle_main", "cursor": "pack", "confidence": "medium"},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["controller"] == "capture"


def test_capture_battle_main_moves_toward_pack_cursor():
    state = make_state(
        24,
        3,
        50,
        8,
        menu={"active": True, "name": "battle_main", "cursor": "fight", "confidence": "medium"},
    )

    actions, nav = choose_capture_actions(state)

    assert actions == ["walk_right"]
    assert nav["battle_policy"] == "capture_move_to_pack"


def test_capture_bag_visible_poke_ball_presses_a():
    state = make_state(
        24,
        3,
        50,
        8,
        menu={"active": True, "name": "bag_pocket", "cursor": 0, "confidence": "medium", "visible_items": [{"item_id": 0x05, "name": "Poke Ball"}]},
    )

    actions, nav = choose_capture_actions(state)

    assert actions == ["press_a"]
    assert nav["battle_policy"] == "capture_select_ball"


def test_capture_bag_moves_toward_visible_ball():
    state = make_state(
        24,
        3,
        50,
        8,
        menu={
            "name": "bag_pocket",
            "active": True,
            "confidence": "medium",
            "cursor": 0,
            "visible_items": [{"item_id": 0x12, "name": "Potion"}, {"item_id": 0x05, "name": "Poke Ball"}],
        },
    )

    actions, nav = choose_capture_actions(state)

    assert actions == ["walk_down"]
    assert nav["battle_policy"] == "capture_move_to_ball"


def test_capture_confirm_throw_presses_a():
    state = make_state(24, 3, 50, 8, menu={"active": True, "name": "bag_item_confirm", "confidence": "medium"})

    actions, nav = choose_capture_actions(state)

    assert actions == ["press_a"]
    assert nav["battle_policy"] == "capture_confirm_throw"


def test_v2_battle_status_phase_and_actions():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        3,
        50,
        8,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 16, "enemy_level": 3},
    )

    status = player.build_status({"enabled": True, "engine": "v2"}, state)

    assert status["phase"] == "BATTLE"
    assert status["actions"] == ["press_b"]
    assert status["navigation"]["path_source"] == "battle_fallback"


def test_v2_status_exposes_ram_health():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        7,
        4,
        2,
        position_extra={"trusted": True, "in_bounds": True, "confidence": "high"},
        dialog={"window_stack_plausible": False, "window_stack_size": 138},
        battle={"in_battle": False, "trusted": True, "type_id": 0},
        menu={
            "active": False,
            "confidence": "none",
            "needs_stronger_decode": True,
            "blocked_reason": "implausible_window_stack",
            "window_stack_plausible": False,
            "cursor_xy": {"x": 2, "y": 9},
            "visible_items": [],
            "selected_item_id": None,
        },
        flags={"party_count_trusted": True, "party_terminator_present": True, "bag_count_trusted": True, "bag_terminator_present": True},
    )

    status = player.build_status({"enabled": True, "engine": "v2"}, state)
    ram_health = status["gameplay"]["ram_health"]

    assert ram_health["position_trusted"] is True
    assert ram_health["position_confidence"] == "high"
    assert ram_health["dialog_window_stack_size"] == 138
    assert ram_health["menu_confidence"] == "none"
    assert ram_health["menu_needs_stronger_decode"] is True
    assert ram_health["menu_blocked_reason"] == "implausible_window_stack"
    assert ram_health["menu_window_stack_plausible"] is False
    assert ram_health["menu_cursor_xy"] == {"x": 2, "y": 9}
    assert ram_health["menu_visible_item_count"] == 0
    assert ram_health["menu_selected_item_id"] is None
    assert ram_health["bag_terminator_present"] is True


def test_trainer_low_hp_with_potion_uses_healing_controller():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        3,
        50,
        8,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 5, "max_hp": 30}],
        bag=[{"item_id": 0x12, "item": "Potion", "quantity": 1}],
        battle={"in_battle": True, "type_id": 2, "wild_species_id": 0, "enemy_level": 6},
        menu={"active": True, "name": "battle_main", "cursor": "pack", "confidence": "medium"},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["controller"] == "healing"
    assert nav["battle_policy"] == "healing_open_pack"


def test_trainer_critical_hp_without_potion_blocks():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        3,
        50,
        8,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 5, "max_hp": 30}],
        battle={"in_battle": True, "type_id": 2, "wild_species_id": 0, "enemy_level": 6},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == []
    assert nav["battle_policy"] == "trainer_blocked_critical_hp_no_healing"


def test_low_hp_healing_ignores_untrusted_potion_quantity():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        3,
        50,
        8,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 5, "max_hp": 30, "trusted": True}],
        bag=[{"item_id": 0x12, "item": "Potion", "quantity": 250, "trusted": False}],
        battle={"in_battle": True, "type_id": 2, "wild_species_id": 0, "enemy_level": 6, "trusted": True},
        menu={"active": True, "name": "battle_main", "cursor": "pack", "confidence": "medium"},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == []
    assert nav["battle_policy"] == "trainer_blocked_critical_hp_no_healing"


def test_healing_visible_potion_selects_potion():
    state = make_state(
        24,
        3,
        50,
        8,
        menu={"active": True, "name": "bag_items", "cursor": 0, "confidence": "medium", "visible_items": [{"item_id": 0x12, "name": "Potion"}]},
    )

    actions, nav = choose_healing_item_actions(state)

    assert actions == ["press_a"]
    assert nav["battle_policy"] == "healing_select_item"


def test_healing_unknown_menu_returns_no_action():
    state = make_state(24, 3, 50, 8)

    actions, nav = choose_healing_item_actions(state)

    assert actions == []
    assert nav["battle_policy"] == "healing_blocked_missing_menu_state"
    assert nav["blocked_reason"] == "missing_menu_state"


def test_v2_disabled_suppresses_battle_actions():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        3,
        50,
        8,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        battle={"in_battle": True, "type_id": 1, "wild_species_id": 16, "enemy_level": 3},
    )

    status = player.build_status({"enabled": False, "engine": "v2"}, state)

    assert status["phase"] == "BATTLE"
    assert status["actions"] == []


def test_v2_ambiguous_dialogue_uses_resume_probe_without_pressing_a_first():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        3,
        50,
        8,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        dialog={"active": True, "window_stack_plausible": False, "window_stack_size": 138},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["wait_300"]
    assert nav["path_source"] == "ambiguous_dialogue_recovery"


def test_v2_disabled_status_does_not_expose_actions():
    player = GoldAutoplayerV2()
    status = player.build_status({"enabled": False, "engine": "v2"}, make_state(24, 7, 4, 2))

    assert status["phase"] == "PAUSED"
    assert status["actions"] == []


def test_verify_single_walk_action_accepts_expected_tile_change():
    verified, reason = verify_single_action(
        make_state(24, 7, 4, 2),
        make_state(24, 7, 5, 2),
        "walk_right",
    )

    assert verified is True
    assert reason == "walked_expected_tile"


def test_verify_single_walk_action_accepts_multiple_tile_progress():
    verified, reason = verify_single_action(
        make_state(10, 5, 18, 18),
        make_state(10, 5, 16, 18),
        "walk_left",
    )

    assert verified is True
    assert reason == "walked_progress_in_direction"


def test_verify_single_walk_action_accepts_map_transition():
    verified, reason = verify_single_action(
        make_state(24, 7, 7, 0),
        make_state(24, 6, 9, 0),
        "walk_up",
    )

    assert verified is True
    assert reason == "map_transition_observed"


def test_verify_single_walk_action_accepts_step_onto_warp_with_live_arrival_tile():
    verified, reason = verify_single_action(
        make_state(24, 7, 7, 1),
        make_state(24, 6, 9, 1),
        "walk_up",
    )

    assert verified is True
    assert reason == "map_transition_observed"


def test_verify_single_walk_action_rejects_no_movement():
    verified, reason = verify_single_action(
        make_state(24, 7, 4, 2),
        make_state(24, 7, 4, 2),
        "walk_right",
    )

    assert verified is False
    assert reason == "position_did_not_advance"


def test_verify_single_walk_action_accepts_facing_change_without_movement():
    verified, reason = verify_single_action(
        make_state(24, 5, 6, 4, facing="down"),
        make_state(24, 5, 6, 4, facing="up"),
        "walk_up",
    )

    assert verified is True
    assert reason == "facing_changed_after_direction"


def test_verify_battle_menu_direction_accepts_cursor_change():
    before = make_state(
        24,
        3,
        50,
        8,
        battle={"in_battle": True, "type_id": 1, "trusted": True},
        menu={"active": True, "name": "battle_main", "cursor": "fight", "confidence": "medium"},
    )
    after = make_state(
        24,
        3,
        50,
        8,
        battle={"in_battle": True, "type_id": 1, "trusted": True},
        menu={"active": True, "name": "battle_main", "cursor": "pack", "confidence": "medium"},
    )

    verified, reason = verify_single_action(before, after, "walk_right")

    assert verified is True
    assert reason == "menu_state_changed_after_direction"


def test_v2_visual_dialogue_overrides_implausible_ram():
    player = GoldAutoplayerV2()
    state = make_state(
        24,
        6,
        9,
        1,
        dialog={"active": False, "visual_active": True, "window_stack_plausible": False, "window_stack_size": 131},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "fresh_run_intro_dialogue"


def test_v2_detects_dim_indoor_textbox_as_dialogue():
    player = GoldAutoplayerV2()
    state = make_state(
        26,
        10,
        2,
        7,
        party=[{"slot": 1, "species_id": 158, "level": 5, "hp": 17, "max_hp": 20}],
        visual={"screen_class": "menu_or_text", "visual_textbox_active": False, "bright_lower": 0.749, "dark_lower": 0.251, "lower_contrast_gap": 53.919},
    )

    actions, nav = player.choose_overworld_actions(state)

    assert actions == ["press_a"]
    assert nav["path_source"] == "dialogue"


def test_verify_walk_rejects_untrusted_after_position():
    verified, reason = verify_single_action(
        make_state(24, 7, 4, 2, position_extra={"trusted": True}),
        make_state(24, 7, 5, 2, position_extra={"trusted": False, "in_bounds": False}),
        "walk_right",
    )

    assert verified is False
    assert reason == "after_position_untrusted"


def test_verify_walk_rejects_unexpected_map_transition():
    verified, reason = verify_single_action(
        make_state(24, 7, 4, 2),
        make_state(10, 5, 39, 24),
        "walk_right",
    )

    assert verified is False
    assert reason == "unexpected_map_transition"


def test_verify_press_b_in_battle_without_state_change_is_unverified():
    verified, reason = verify_single_action(
        make_state(24, 3, 50, 8, battle={"in_battle": True, "trusted": True}),
        make_state(24, 3, 50, 8, battle={"in_battle": True, "trusted": True}),
        "press_b",
    )

    assert verified is False
    assert reason == "press_b_no_progress"


def test_verify_press_a_visual_only_diff_in_implausible_textbox_is_unverified():
    before = make_state(
        10,
        10,
        3,
        3,
        party=[{"slot": 1, "species_id": 155, "level": 10, "hp": 31, "max_hp": 31}],
        dialog={"active": True, "ram_active": False, "visual_active": True, "window_stack_size": 138, "window_stack_plausible": False},
        visual={"screen_class": "menu_or_text", "visual_textbox_active": True, "lower_edge_mean": 73.0},
    )
    after = make_state(
        10,
        10,
        3,
        3,
        party=[{"slot": 1, "species_id": 155, "level": 10, "hp": 31, "max_hp": 31}],
        dialog={"active": True, "ram_active": False, "visual_active": True, "window_stack_size": 138, "window_stack_plausible": False},
        visual={"screen_class": "menu_or_text", "visual_textbox_active": True, "lower_edge_mean": 74.0},
    )

    verified, reason = verify_single_action(before, after, "press_a")

    assert verified is False
    assert reason == "press_a_visual_only_no_semantic_progress"


def test_verify_unknown_action_is_not_verified():
    verified, reason = verify_single_action(make_state(24, 7, 4, 2), make_state(24, 7, 4, 2), "spin")

    assert verified is False
    assert reason == "unknown_action"


def test_posted_actions_adds_wait_after_button_press():
    assert posted_actions_for("press_a") == ["press_a", "wait_30"]
    assert posted_actions_for("walk_right") == ["hold_right_48", "wait_12"]
    assert posted_actions_for("walk_right", 24) == ["hold_right_24", "wait_12"]
    assert posted_actions_for("press_down") == ["hold_down_12", "wait_72"]


def test_battle_fallback_button_actions_use_longer_settle_wait():
    assert posted_actions_for_context("press_a", {"path_source": "battle_fallback"}, "BATTLE") == ["press_a", "wait_60"]
    assert posted_actions_for_context("press_a", {"path_source": "dialogue"}, "OVERWORLD") == ["press_a", "wait_30"]


def test_doorway_navigation_uses_short_walk_hold():
    assert walk_hold_frames_for_navigation("walk_left", {"transition": {"kind": "warp"}}) == 24
    assert walk_hold_frames_for_navigation("walk_left", {"path_source": "route31_gate_live_entry", "planned_path_length": 2}) == 24
    assert walk_hold_frames_for_navigation("walk_left", {"path_source": "cross_map_static_registry", "planned_path_length": 2}) is None
    assert walk_hold_frames_for_navigation("walk_left", {"path_source": "same_map_static_registry", "planned_path_length": 5}) is None


def test_recovery_state_increments_after_failed_walk_verification():
    player = GoldAutoplayerV2()

    player.record_action_outcome("walk_right", False, "position_did_not_advance")

    assert player.stuck_counter == 1
    assert player.recovery_level == 1
    assert player.blocked_edges_by_map == {}


def test_recovery_records_blocked_edge_with_state_context():
    player = GoldAutoplayerV2()

    player.record_action_outcome("walk_right", False, "position_did_not_advance", before_state=make_state(24, 7, 4, 2))

    assert player.blocked_edges_by_map == {(24, 7): {((4, 2), "right")}}
    assert player.recovery_level == 1


def test_recent_failures_records_failed_walk_context():
    player = GoldAutoplayerV2()

    player.record_action_outcome("walk_right", False, "position_did_not_advance", before_state=make_state(24, 7, 4, 2))
    status = player.build_status({"enabled": True, "engine": "v2"}, make_state(24, 7, 4, 2))

    failure = status["navigation"]["recent_failures"][-1]
    assert failure["action"] == "walk_right"
    assert failure["reason"] == "position_did_not_advance"
    assert failure["tile"] == {"x": 4, "y": 2}
    assert failure["blocked_edge"] is True


def test_failed_battle_menu_direction_does_not_record_blocked_edge():
    player = GoldAutoplayerV2()

    player.record_action_outcome(
        "walk_right",
        False,
        "position_did_not_advance",
        before_state=make_state(24, 3, 50, 8, battle={"in_battle": True, "type_id": 1, "trusted": True}),
    )

    assert player.blocked_edges_by_map == {}
    assert player.stuck_counter == 1


def test_recovery_does_not_record_blocked_edge_for_visual_textbox_failure():
    player = GoldAutoplayerV2()
    state = make_state(24, 6, 9, 1)
    state["visual"] = {"screen_class": "menu_or_text", "visual_textbox_active": True}

    for _ in range(3):
        player.record_action_outcome("walk_down", False, "position_did_not_advance", before_state=state)

    assert player.blocked_edges_by_map == {}
    assert player.stuck_counter == 0
    assert player.recovery_level == 0


def test_recovery_records_blocked_edge_for_bright_overworld_false_positive():
    player = GoldAutoplayerV2()
    state = make_state(24, 4, 6, 7)
    state["visual"] = {"screen_class": "menu_or_text", "visual_textbox_active": False, "bright_dialogue_panel": False}

    player.record_action_outcome("walk_down", False, "position_did_not_advance", before_state=state)

    assert player.blocked_edges_by_map == {(24, 4): {((6, 7), "down")}}
    assert player.recovery_level == 1


def test_recovery_circuit_resets_after_manual_progress():
    player = GoldAutoplayerV2()
    for _ in range(3):
        player.record_action_outcome("walk_right", False, "position_did_not_advance", before_state=make_state(24, 7, 4, 2))

    actions, nav = player.choose_overworld_actions(make_state(24, 7, 5, 2))

    assert nav["path_source"] != "safety_circuit_breaker"
    assert player.stuck_counter == 0
    assert player.recovery_level == 0


def test_recovery_does_not_record_blocked_edge_for_untrusted_ram_failure():
    player = GoldAutoplayerV2()

    player.record_action_outcome("walk_right", False, "after_position_untrusted", before_state=make_state(24, 7, 4, 2))

    assert player.blocked_edges_by_map == {}
    assert player.stuck_counter == 1


def test_recovery_circuit_breaker_pauses_after_repeated_failed_walks():
    player = GoldAutoplayerV2()
    for _ in range(3):
        player.record_action_outcome("walk_right", False, "position_did_not_advance")

    actions, nav = player.choose_overworld_actions(make_state(24, 7, 4, 2))

    assert actions == []
    assert nav["path_source"] == "safety_circuit_breaker"
    assert player.recovery_level == 2
    assert nav["blocked_edges"] == 0


def test_recovery_state_resets_after_verified_walk():
    player = GoldAutoplayerV2()
    for _ in range(3):
        player.record_action_outcome("walk_right", False, "position_did_not_advance")

    player.record_action_outcome("walk_right", True, "walked_expected_tile")

    assert player.stuck_counter == 0
    assert player.recovery_level == 0


def test_status_includes_schema_runner_and_blocked_edges():
    player = GoldAutoplayerV2()
    player.blocked_edges_by_map = {(24, 7): {((4, 2), "right")}}

    status = player.build_status({"enabled": True, "engine": "v2"}, make_state(24, 7, 4, 2))

    assert status["schema_version"] == 2
    assert status["runner"]["event_log"].endswith("gold_autoplayer_v2.jsonl")
    assert status["navigation"]["blocked_edges"] == 1


def test_status_exposes_activation_readiness_gates():
    player = GoldAutoplayerV2()

    status = player.build_status(
        {"enabled": True, "engine": "v2", "dry_run": True, "allow_overworld_movement": False, "allow_battle_actions": False},
        make_state(24, 7, 4, 2, position_extra={"trusted": True}),
    )

    assert status["runner"]["dry_run"] is True
    assert status["runner"]["allow_overworld_movement"] is False
    assert status["readiness"]["safe_to_post_actions"] is False
    assert "dry_run_enabled" in status["readiness"]["blockers"]
    assert "overworld_movement_disabled" in status["readiness"]["blockers"]


def test_status_exposes_decision_inspector_payload_and_resource_facts(tmp_path):
    player = GoldAutoplayerV2(data_dir=tmp_path)
    state = make_state(
        26,
        1,
        7,
        27,
        party=[{"slot": 1, "species_id": 155, "level": 9, "hp": 26, "max_hp": 26}],
        flags={"derived_story_flags": {"learned_to_catch_pokemon": True, "gave_mystery_egg_to_elm": True}},
    )
    state["player"]["money"] = 13

    status = player.build_status({"enabled": True, "engine": "adaptive", "dry_run": False, "allow_overworld_movement": True, "allow_battle_actions": True}, state)

    assert status["intent"]["goal"]["type"] == "route31_grind"
    assert status["resource_accounting"]["broke_no_balls"] is True
    assert "lead_level_below_falkner_floor" in status["resource_accounting"]["readiness_blockers"]
    assert status["policy_candidates"]
    assert status["decision_trace"][0]["step"] == "observe"
    memory = json.loads((tmp_path / "pokemon_learning_memory.json").read_text())
    assert any(fact["category"] == "PKM:RESOURCE" for fact in memory["facts"])


def test_status_softens_roster_gate_when_broke_no_balls_and_lead_ready(tmp_path):
    player = GoldAutoplayerV2(data_dir=tmp_path)
    state = make_state(
        26,
        2,
        17,
        12,
        party=[{"slot": 1, "species_id": 155, "level": 13, "hp": 35, "max_hp": 35}],
        bag=[{"item_id": 0x18, "item": "Potion", "quantity": 1}],
        flags={"derived_story_flags": {"learned_to_catch_pokemon": True, "gave_mystery_egg_to_elm": True}},
    )
    state["player"]["money"] = 52

    status = player.build_status({"enabled": True, "engine": "adaptive", "dry_run": False, "allow_overworld_movement": True, "allow_battle_actions": True}, state)

    assert status["intent"]["goal"]["type"] == "violet_gate"
    assert status["resource_accounting"]["broke_no_balls"] is True
    assert status["resource_accounting"]["roster_blocked_by_resources"] is True
    assert "party_count_below_falkner_floor" not in status["resource_accounting"]["readiness_blockers"]
    assert "backup_levels_below_falkner_floor" not in status["resource_accounting"]["readiness_blockers"]


def test_status_prioritizes_catching_tutorial_with_money_no_balls_before_tutorial_flag(tmp_path):
    player = GoldAutoplayerV2(data_dir=tmp_path)
    state = make_state(
        10,
        7,
        4,
        1,
        party=[{"slot": 1, "species_id": 155, "level": 12, "hp": 25, "max_hp": 34}],
        bag=[{"item_id": 0x18, "item": "Potion", "quantity": 1}],
        flags={"derived_story_flags": {"event_flags_available": True, "gave_mystery_egg_to_elm": True, "learned_to_catch_pokemon": False}},
    )
    state["player"]["money"] = 307

    status = player.build_status({"enabled": True, "engine": "adaptive", "dry_run": False, "allow_overworld_movement": True, "allow_battle_actions": True}, state)

    assert status["intent"]["goal"]["type"] == "learn_catching_tutorial"
    assert status["resource_accounting"]["balls"] == 0
    assert status["resource_accounting"]["can_restock_balls"] is True


def test_verify_press_a_rejects_no_progress():
    before = make_state(24, 5, 4, 11)
    after = make_state(24, 5, 4, 11)

    verified, reason = verify_single_action(before, after, "press_a")

    assert verified is False
    assert reason == "press_a_no_progress"


def test_verify_press_a_accepts_menu_state_change():
    before = make_state(
        24,
        3,
        50,
        8,
        battle={"in_battle": True, "trusted": True},
        menu={"active": True, "name": "battle_main", "cursor": "pack", "confidence": "medium"},
    )
    after = make_state(
        24,
        3,
        50,
        8,
        battle={"in_battle": True, "trusted": True},
        menu={"active": True, "name": "bag_balls", "cursor": 0, "confidence": "medium", "visible_items": []},
    )

    verified, reason = verify_single_action(before, after, "press_a")

    assert verified is True
    assert reason == "menu_state_changed_after_press_a"


def test_reward_for_transition_accepts_badge_name_lists():
    player = GoldAutoplayerV2()
    before = make_state(10, 7, 5, 2, party=[{"slot": 1, "species_id": 156, "level": 15, "hp": 20, "max_hp": 45}])
    after = make_state(10, 7, 5, 2, party=[{"slot": 1, "species_id": 156, "level": 15, "hp": 20, "max_hp": 45}])
    before["player"]["badges"] = []
    after["player"]["badges"] = ["Zephyr"]

    reward = player.reward_for_transition(before, after, "press_a", True, "visual_state_changed_after_press_a", "ok")

    assert reward > 20


def test_verify_press_a_rejects_low_confidence_menu_noise():
    before = make_state(24, 3, 50, 8, menu={"active": True, "name": "battle_main", "cursor": "pack", "confidence": "low"})
    after = make_state(24, 3, 50, 8, menu={"active": True, "name": "battle_main", "cursor": "run", "confidence": "low"})

    verified, reason = verify_single_action(before, after, "press_a")

    assert verified is False
    assert reason == "press_a_no_progress"


class FakeRunOnceBot(GoldAutoplayerV2):
    def __init__(self, tmp_path, before_state, after_state, control=None, post_success=True):
        super().__init__(data_dir=tmp_path)
        self.before_state = before_state
        self.after_state = after_state
        self.control = control or {"enabled": True, "engine": "v2", "dry_run": False, "allow_overworld_movement": True, "allow_battle_actions": True}
        self.post_success = post_success
        self.requests = 0
        self.posted_actions = []

    def read_control(self):
        return self.control

    def request_json(self, path: str, timeout: float = 5.0):
        self.requests += 1
        return self.before_state if self.requests == 1 else self.after_state

    def post_actions(self, actions: list[str], timeout: float = 10.0):
        self.posted_actions.append(actions)
        return {"success": self.post_success}


def test_run_once_posts_action_records_verification_and_logs_event(tmp_path):
    before = make_state(24, 7, 4, 2)
    after = make_state(24, 7, 5, 2)
    bot = FakeRunOnceBot(tmp_path, before, after)

    status = bot.run_once()

    assert bot.posted_actions == [["hold_right_24", "wait_12"]]
    assert status["navigation"]["last_step_verified"] is True
    assert status["navigation"]["verification_reason"] == "walked_expected_tile"
    rows = [json.loads(line) for line in bot.event_log_path.read_text().splitlines()]
    assert rows[0]["event"] == "turn"
    assert rows[0]["navigation"]["last_step_action"] == "walk_right"


def test_run_once_records_failed_walk_and_blocks_edge(tmp_path):
    before = make_state(24, 7, 4, 2)
    after = make_state(24, 7, 4, 2)
    bot = FakeRunOnceBot(tmp_path, before, after)

    status = bot.run_once()

    assert status["navigation"]["last_step_verified"] is False
    assert status["navigation"]["verification_reason"] == "position_did_not_advance"
    assert status["navigation"]["stuck_counter"] == 1
    assert bot.blocked_edges_by_map == {(24, 7): {((4, 2), "right")}}


def test_run_once_dry_run_does_not_post_actions(tmp_path):
    bot = FakeRunOnceBot(tmp_path, make_state(24, 7, 4, 2), make_state(24, 7, 5, 2), control={"enabled": True, "engine": "v2", "dry_run": True})

    status = bot.run_once()

    assert bot.posted_actions == []
    assert status["navigation"]["last_step_result"] == "dry_run"
    assert status["navigation"]["verification_reason"] == "dry_run_no_action_posted"
    assert bot.stuck_counter == 0
    assert bot.blocked_edges_by_map == {}


def test_run_once_repeated_dry_run_does_not_open_recovery_circuit(tmp_path):
    bot = FakeRunOnceBot(tmp_path, make_state(24, 7, 4, 2), make_state(24, 7, 5, 2), control={"enabled": True, "engine": "v2", "dry_run": True})

    for _ in range(4):
        status = bot.run_once()

    assert bot.posted_actions == []
    assert status["navigation"]["last_step_result"] == "dry_run"
    assert bot.stuck_counter == 0
    assert bot.recovery_level == 0
    assert bot.blocked_edges_by_map == {}


def test_run_once_post_failure_does_not_mark_verified(tmp_path):
    bot = FakeRunOnceBot(tmp_path, make_state(24, 7, 4, 2), make_state(24, 7, 5, 2), post_success=False)

    status = bot.run_once()

    assert bot.posted_actions == [["hold_right_24", "wait_12"]]
    assert status["navigation"]["last_step_result"] == "post_failed"
    assert status["navigation"]["last_step_verified"] is False
    assert status["navigation"]["verification_reason"] == "post_actions_unsuccessful"
    assert bot.stuck_counter == 0


def test_run_once_control_can_block_overworld_movement(tmp_path):
    bot = FakeRunOnceBot(
        tmp_path,
        make_state(24, 7, 4, 2),
        make_state(24, 7, 5, 2),
        control={"enabled": True, "engine": "v2", "dry_run": False, "allow_overworld_movement": False},
    )

    status = bot.run_once()

    assert bot.posted_actions == []
    assert status["navigation"]["last_step_result"] == "blocked_by_control"
    assert status["navigation"]["verification_reason"] == "overworld_movement_disabled"
    assert bot.stuck_counter == 0
    assert bot.blocked_edges_by_map == {}


class UnsafeReadinessBot(FakeRunOnceBot):
    def build_status(self, control, state):
        status = super().build_status(control, state)
        status["actions"] = ["walk_right"]
        status["navigation"] = {"path_source": "forced_unsafe_walk", "next_step": "walk_right"}
        status["readiness"] = status.get("readiness", {}) | {
            "safe_to_post_actions": False,
            "blockers": ["position_untrusted"],
        }
        return status


def test_run_once_blocks_actions_when_readiness_is_unsafe(tmp_path):
    bot = UnsafeReadinessBot(tmp_path, make_state(24, 7, 4, 2), make_state(24, 7, 5, 2))

    status = bot.run_once()

    assert bot.posted_actions == []
    assert status["actions"] == []
    assert status["navigation"]["last_step_result"] == "blocked_by_readiness"
    assert status["navigation"]["blocked_reason"] == "readiness_blocked"


class FailingStateBot(FakeRunOnceBot):
    def request_json(self, path: str, timeout: float = 5.0):
        self.requests += 1
        raise urllib.error.URLError("down")


def test_run_once_state_fetch_failure_records_api_backoff(tmp_path):
    bot = FailingStateBot(tmp_path, make_state(24, 7, 4, 2), make_state(24, 7, 5, 2))

    status = bot.run_once()

    assert status["phase"] == "API_UNAVAILABLE"
    assert status["runner"]["api_failure_count"] == 1
    assert status["runner"]["api_backoff_seconds"] >= 1.0
    assert "URLError" in status["runner"]["last_error"]
    assert bot.posted_actions == []


def test_run_once_api_backoff_suppresses_state_fetch(tmp_path):
    bot = FakeRunOnceBot(tmp_path, make_state(24, 7, 4, 2), make_state(24, 7, 5, 2))
    bot.api_failure_count = 1
    bot.api_backoff_seconds = 10
    bot.next_api_retry_at = time.time() + 10

    status = bot.run_once()

    assert bot.requests == 0
    assert status["phase"] == "API_BACKOFF"
    assert status["actions"] == []


def test_run_once_api_success_resets_backoff(tmp_path):
    bot = FakeRunOnceBot(tmp_path, make_state(24, 7, 4, 2), make_state(24, 7, 5, 2))
    bot.api_failure_count = 3
    bot.api_backoff_seconds = 8
    bot.next_api_retry_at = 1
    bot.last_api_error = "URLError: old"

    bot.run_once()

    assert bot.api_failure_count == 0
    assert bot.api_backoff_seconds == 0
    assert bot.next_api_retry_at == 0
    assert bot.last_api_error is None


class FailingPostBot(FakeRunOnceBot):
    def post_actions(self, actions: list[str], timeout: float = 10.0):
        self.posted_actions.append(actions)
        raise urllib.error.URLError("post down")


def test_run_once_post_exception_records_api_error_without_blocked_edge(tmp_path):
    bot = FailingPostBot(tmp_path, make_state(24, 7, 4, 2), make_state(24, 7, 5, 2))

    status = bot.run_once()

    assert status["navigation"]["last_step_result"] == "error:URLError"
    assert status["navigation"]["last_step_verified"] is False
    assert status["navigation"]["verification_reason"] == "verification_state_unavailable"
    assert bot.blocked_edges_by_map == {}
    assert bot.api_failure_count == 1


class FakeMilestoneSaveManager(MilestoneSaveStateManager):
    def __init__(self, data_dir, *, dry_run=False):
        super().__init__(data_dir, "http://example.invalid", dry_run=dry_run, clock=lambda: 1779237691.0)
        self.saved_names = []

    def _save_exists(self, save_name: str) -> bool:
        return save_name in self.saved_names

    def _post_save(self, save_name: str):
        self.saved_names.append(save_name)
        return {"success": True, "name": save_name, "path": f"/tmp/{save_name}.state"}


class ExplodingDryRunMilestoneSaveManager(MilestoneSaveStateManager):
    def __init__(self, data_dir):
        super().__init__(data_dir, "http://example.invalid", dry_run=True, clock=lambda: 1779237691.0)

    def _save_exists(self, save_name: str) -> bool:
        raise AssertionError("dry-run milestone saves must not query /saves")

    def _post_save(self, save_name: str):
        raise AssertionError("dry-run milestone saves must not POST /save")


def trusted_state(*args, **kwargs):
    position_extra = kwargs.pop("position_extra", {}) or {}
    position_extra.update({"trusted": True, "in_bounds": True})
    state = make_state(*args, position_extra=position_extra, **kwargs)
    state["map"] = {
        "map_group": args[0],
        "map_number": args[1],
        "map_name": state["player"]["position"].get("map_name", "test"),
        "trusted": True,
        "position_in_bounds": True,
    }
    state["metadata"] = {"frame_count": 16969}
    return state


def test_milestone_policy_detects_current_starter_acquired():
    state = trusted_state(
        24,
        5,
        5,
        3,
        party=[{"slot": 1, "species_id": 155, "species": "Cyndaquil", "level": 5, "hp": 20, "max_hp": 20}],
        flags={"party_count": 1, "has_starter": True},
        position_extra={"map_name": "Elm's Lab"},
    )

    requests = evaluate_milestone_requests(state)

    assert any(request.milestone_key == "gold_030_starter_acquired" for request in requests)


def test_milestone_manager_saves_only_once_and_persists_ledger(tmp_path):
    state = trusted_state(
        24,
        5,
        5,
        3,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        flags={"party_count": 1, "has_starter": True},
        position_extra={"map_name": "Elm's Lab"},
    )
    manager = FakeMilestoneSaveManager(tmp_path)

    first = manager.maybe_save(state, {}, max_saves=1)
    second = manager.maybe_save(state, {}, max_saves=1)

    assert first["created"][0]["milestone_key"] == "gold_030_starter_acquired"
    assert first["created"][0]["safety"] == "save_only"
    assert first["created"][0]["load_policy"] == "manual_only"
    assert second["created"] == []
    assert len(manager.saved_names) == 1
    ledger = json.loads((tmp_path / "save_state_milestones.json").read_text())
    assert "gold_030_starter_acquired" in ledger["created"]


def test_milestone_manager_dry_run_does_not_save_or_write_ledger(tmp_path):
    state = trusted_state(
        24,
        5,
        5,
        3,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        flags={"party_count": 1, "has_starter": True},
        position_extra={"map_name": "Elm's Lab"},
    )
    manager = FakeMilestoneSaveManager(tmp_path, dry_run=True)

    result = manager.maybe_save(state, {}, max_saves=1)

    assert result["dry_run"] is True
    assert result["created"][0]["dry_run"] is True
    assert manager.saved_names == []
    assert not (tmp_path / "save_state_milestones.json").exists()


def test_milestone_manager_dry_run_has_zero_http_side_effects(tmp_path):
    state = trusted_state(
        24,
        5,
        5,
        3,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        flags={"party_count": 1, "has_starter": True},
        position_extra={"map_name": "Elm's Lab"},
    )
    manager = ExplodingDryRunMilestoneSaveManager(tmp_path)

    result = manager.maybe_save(state, {}, max_saves=1)

    assert result["dry_run"] is True
    assert result["created"][0]["dry_run"] is True
    assert not (tmp_path / "save_state_milestones.json").exists()
    assert not (tmp_path / "saves").exists()


def test_run_once_control_dry_run_propagates_to_milestone_manager(monkeypatch, tmp_path):
    monkeypatch.setenv("POKEMON_MILESTONE_SAVE_STATES_DRY_RUN", "0")
    state = trusted_state(
        24,
        5,
        5,
        3,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        flags={"party_count": 1, "has_starter": True},
        position_extra={"map_name": "Elm's Lab"},
    )
    bot = FakeRunOnceBot(
        tmp_path,
        state,
        state,
        control={"enabled": True, "engine": "v2", "dry_run": True, "allow_overworld_movement": True, "allow_battle_actions": True},
    )
    bot.save_state_manager = ExplodingDryRunMilestoneSaveManager(tmp_path)
    bot.save_state_manager.dry_run = False

    status = bot.run_once()

    assert status["save_states"]["dry_run"] is True
    assert status["save_states"]["created"][0]["dry_run"] is True
    assert bot.posted_actions == []
    assert not (tmp_path / "save_state_milestones.json").exists()


def test_milestone_manager_uses_most_advanced_current_milestone_without_backfill(tmp_path):
    state = trusted_state(
        30,
        1,
        1,
        1,
        party=[{"slot": 1, "species_id": 155, "level": 8, "hp": 25, "max_hp": 25}],
        flags={
            "party_count": 1,
            "has_starter": True,
            "derived_story_flags": {"has_starter": True, "got_mystery_egg_from_mr_pokemon": True},
        },
    )
    manager = FakeMilestoneSaveManager(tmp_path)

    result = manager.maybe_save(state, {}, max_saves=1)

    assert [entry["milestone_key"] for entry in result["created"]] == ["gold_040_mystery_egg_acquired"]


def test_milestone_policy_emergency_snapshot_is_bucketed():
    state = trusted_state(
        24,
        5,
        5,
        3,
        party=[{"slot": 1, "species_id": 155, "level": 5, "hp": 20, "max_hp": 20}],
        flags={"party_count": 1, "has_starter": True},
    )
    status = {"navigation": {"same_state_press_a_count": 4, "recent_failures": [{} for _ in range(5)]}}

    requests = evaluate_milestone_requests(state, status)

    assert any(request.milestone_key.startswith("gold_emergency_before_recovery_") for request in requests)
