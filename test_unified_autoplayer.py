import json

from pokemon_agent.autoplayer import LearningFact, LearningMemory, UniversalAutoplayer, import_gold_v1_teacher_snapshot, profile_for_game_type


def test_profile_matrix_marks_gold_more_capable_than_red_blue():
    gold = profile_for_game_type("gold")
    red = profile_for_game_type("red")

    assert gold.game_id == "gold_silver"
    assert gold.capabilities.map_registry is True
    assert gold.capabilities.story_planner is True
    assert red.game_id == "red_blue"
    assert red.capabilities.structured_ram is True
    assert red.capabilities.map_registry is False


def test_yellow_profile_is_explicitly_not_validated_as_red_blue():
    yellow = profile_for_game_type("yellow")

    assert yellow.game_id == "yellow"
    assert yellow.reader == "PokemonYellowReader"
    assert yellow.capabilities.structured_ram is False


def test_learning_memory_uses_pkm_categories_and_dedupes(tmp_path):
    memory = LearningMemory(tmp_path / "learning_memory.json")
    fact = LearningFact(
        category="PKM:STUCK",
        text="Route 29 east exit recovered by stepping down then right",
        game_id="gold_silver",
        data={"map": "Route 29"},
    )

    memory.add_fact(fact)
    memory.add_fact(fact)

    rows = memory.facts_for(category="PKM:STUCK", game_id="gold_silver")
    assert len(rows) == 1
    assert rows[0]["text"] == "Route 29 east exit recovered by stepping down then right"
    assert memory.summary("gold_silver")["PKM:STUCK"] == [rows[0]["text"]]


def test_learning_memory_normalizes_unknown_category(tmp_path):
    memory = LearningMemory(tmp_path / "learning_memory.json")

    memory.add_fact(LearningFact(category="NOTE", text="Got starter", game_id="red_blue"))

    rows = memory.facts_for(game_id="red_blue")
    assert rows[0]["category"] == "PKM:PROGRESS"


def test_shared_v1_teacher_import_writes_gold_facts(tmp_path):
    (tmp_path / "gold_world_model.json").write_text(json.dumps({
        "blocked_moves": {"24:7:4": ["walk_left"]},
        "directed_edges": {"6151:2:4|right": {"action": "walk_right", "from": "6151:2:4", "to": "6151:2:5", "state": "open", "open_count": 3}},
        "places": {"24:7": {"visits": 1}},
    }), encoding="utf-8")
    (tmp_path / "gold_policy.json").write_text(json.dumps({"phase": "overworld", "turn": 9}), encoding="utf-8")
    memory = LearningMemory(tmp_path / "pokemon_learning_memory.json")

    result = import_gold_v1_teacher_snapshot(tmp_path, memory)

    facts = memory.facts_for(game_id="gold_silver")
    assert result["counts"]["blocked_moves"] == 1
    assert {fact["source"] for fact in facts} == {"v1_teacher"}
    assert {fact["data"].get("kind") for fact in facts} >= {"v1_blocked_moves", "v1_open_edge", "v1_place", "v1_policy_snapshot"}


def test_universal_autoplayer_detects_profiles_from_state(tmp_path):
    player = UniversalAutoplayer("http://example.invalid", tmp_path)

    assert player.detect_profile({"metadata": {"game": "Pokemon Gold/Silver (GBC)"}}).game_id == "gold_silver"
    assert player.detect_profile({"metadata": {"game": "Pokemon Red"}}).game_id == "red_blue"
    assert player.detect_profile({"metadata": {"game": "Pokemon Yellow"}}).game_id == "yellow"


def test_universal_fallback_prefers_dialogue_and_short_exploration(tmp_path):
    player = UniversalAutoplayer("http://example.invalid", tmp_path)
    red = profile_for_game_type("red")

    assert player.choose_fallback_actions({"dialog": {"active": True}}, red) == (["press_a", "wait_30"], "dialogue_fallback_press_a")
    assert player.choose_fallback_actions({"battle": {"in_battle": True}}, red) == (["press_a", "wait_30"], "battle_fallback_press_a")
    assert player.choose_fallback_actions({}, red) == (["hold_up_48", "wait_12"], "red_blue_explore_walk_up")
