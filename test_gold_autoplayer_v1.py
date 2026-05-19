from pathlib import Path

import pytest

pytest.importorskip("PIL")

from gold_autoplayer import GoalDirectedGoldPlayer, Observation


def make_battle_obs(*, battle_type: int, hp: int = 5, max_hp: int = 24, potion_count: int = 0) -> Observation:
    bag = [{"item_id": 18, "quantity": potion_count}] if potion_count else []
    return Observation(
        visual={"screen_class": "overworld_or_battle", "visual_textbox_active": False},
        state={
            "battle": {"in_battle": True, "type_id": battle_type, "enemy_level": 2},
            "party": [{"species_id": 155, "hp": hp, "max_hp": max_hp}],
            "bag": bag,
            "player": {"position": {"map_id": 1, "x": 1, "y": 1}},
        },
    )


def make_mart_obs(*, map_key: tuple[int, int] = (10, 6), money: int = 27, x: int = 3, y: int = 3, screen_class: str = "menu_or_text") -> Observation:
    return Observation(
        visual={"screen_class": screen_class, "visual_textbox_active": False},
        state={
            "player": {"money": money, "position": {"map_id": 2566, "map_group": map_key[0], "map_number": map_key[1], "x": x, "y": y}},
            "party": [{"species_id": 155, "hp": 24, "max_hp": 26}],
            "bag": [],
            "battle": {"in_battle": False},
        },
    )


def test_v1_low_hp_trainer_battle_does_not_try_run_or_switch(tmp_path):
    player = GoalDirectedGoldPlayer("http://example.invalid", Path(tmp_path))
    player.repeated_position_count = 10

    macro, actions = player._battle_macro(make_battle_obs(battle_type=2, potion_count=0))

    assert macro != "battle_run_after_potion_stall"
    assert macro != "battle_run_low_hp"
    assert actions[:2] == ["press_a", "wait_30"]


def test_v1_single_pokemon_trainer_stuck_menu_avoids_switch_direction_inputs(tmp_path):
    player = GoalDirectedGoldPlayer("http://example.invalid", Path(tmp_path))
    player.repeated_position_count = 10

    macro, actions = player._battle_macro(make_battle_obs(battle_type=2, hp=10, max_hp=24, potion_count=0))

    assert macro == "battle_attack_single_party_stuck_menu"
    assert "walk_down" not in actions
    assert "hold_down_20" not in actions
    assert "walk_right" not in actions
    assert "hold_right_20" not in actions
    assert actions[:8] == ["press_b", "wait_30", "press_b", "wait_30", "hold_up_20", "wait_20", "hold_left_20", "wait_20"]


def test_v1_low_hp_wild_battle_can_still_escape(tmp_path):
    player = GoalDirectedGoldPlayer("http://example.invalid", Path(tmp_path))
    player.repeated_position_count = 10

    macro, actions = player._battle_macro(make_battle_obs(battle_type=1, potion_count=0))

    assert macro == "battle_run_after_potion_stall"
    assert "hold_right_20" in actions


def test_v1_violet_mart_without_money_backs_out_instead_of_buying(tmp_path):
    player = GoalDirectedGoldPlayer("http://example.invalid", Path(tmp_path))

    macro, actions = player._mart_macro(make_mart_obs())

    assert macro == "mart_back_out_menu"
    assert actions == ["press_b", "wait_30", "press_b", "wait_60"]


def test_v1_violet_mart_without_money_exits_floor(tmp_path):
    player = GoalDirectedGoldPlayer("http://example.invalid", Path(tmp_path))

    macro, actions = player._mart_macro(make_mart_obs(screen_class="overworld_or_battle"))

    assert macro == "mart_to_exit_down"
    assert actions == ["walk_down", "wait_30"]
