from pokemon_agent.memory.gold import PokemonGoldReader
from pokemon_agent.state.builder import build_game_state


class FakeEmulator:
    def __init__(self, memory: dict[int, int] | None = None):
        self.memory = memory or {}

    def read_u8(self, addr: int) -> int:
        return self.memory.get(addr, 0)

    def read_range(self, addr: int, length: int) -> bytes:
        return bytes(self.read_u8(addr + offset) for offset in range(length))


def reader(memory: dict[int, int]) -> PokemonGoldReader:
    return PokemonGoldReader(FakeEmulator(memory))


def test_gold_reader_exposes_menu_section_in_state_builder():
    state = build_game_state(reader({0xD116: 1, 0xD0ED: 0xB3, 0xD0FC: 6, 0xCF81: 1, 0xCFA9: 1, 0xCFAA: 2}))

    assert state["menu"]["name"] == "battle_main"
    assert state["menu"]["source"] == "gold_ram"
    assert state["menu"]["cursor"] == "pack"
    assert state["menu"]["confidence"] == "medium"
    assert state["menu"]["selected_item_id"] is None
    assert state["menu"]["visible_items"] == []
    assert state["menu"]["needs_stronger_decode"] is False
    assert state["menu"]["blocked_reason"] is None
    assert state["menu"]["window_stack_plausible"] is True


def test_gold_menu_decoder_rejects_stale_window_stack():
    menu = reader({0xD116: 1, 0xD0ED: 0xB3, 0xD0FC: 6, 0xCF81: 138, 0xCFA9: 1, 0xCFAA: 2}).read_menu()

    assert menu["active"] is False
    assert menu["name"] is None
    assert menu["cursor"] is None
    assert menu["confidence"] == "none"
    assert menu["needs_stronger_decode"] is True
    assert menu["window_stack_plausible"] is False
    assert menu["blocked_reason"] == "implausible_window_stack"


def test_gold_menu_decoder_requires_known_battle_cursor_grid():
    menu = reader({0xD116: 1, 0xD0ED: 0xB3, 0xD0FC: 6, 0xCF81: 1, 0xCFA9: 7, 0xCFAA: 9}).read_menu()

    assert menu["active"] is False
    assert menu["confidence"] == "none"
    assert menu["cursor_xy"] == {"x": 9, "y": 7}
    assert menu["blocked_reason"] == "unknown_battle_cursor"


def test_gold_menu_decoder_rejects_untrusted_battle_ram():
    menu = reader({0xD116: 0xFF, 0xD0ED: 0xB3, 0xD0FC: 6, 0xCF81: 1, 0xCFA9: 1, 0xCFAA: 2}).read_menu()

    assert menu["active"] is False
    assert menu["confidence"] == "none"
    assert menu["blocked_reason"] == "untrusted_battle_ram"


def test_gold_dialog_exposes_raw_ui_bytes():
    dialog = reader({0xCF81: 138, 0xCFA9: 3, 0xCFAA: 4}).read_dialog()

    assert dialog["active"] is False
    assert dialog["window_stack_plausible"] is False
    assert dialog["raw"] == {"wWindowStackSize": 138, "wMenuCursorY": 3, "wMenuCursorX": 4}


def test_gold_reader_rejects_corrupt_party_and_bag_counts():
    gold = reader({0xDA22: 127, 0xD5B7: 255})

    assert gold.read_party() == []
    assert gold.read_bag() == []
    flags = gold.read_flags()
    assert flags["party_count"] == 0
    assert flags["party_count_raw"] == 127
    assert flags["party_count_trusted"] is False


def test_gold_battle_decoder_exposes_confidence_and_raw_fields():
    battle = reader({0xD116: 1, 0xD0ED: 0xB3, 0xD0FC: 6}).read_battle()

    assert battle["in_battle"] is True
    assert battle["wild"] is True
    assert battle["enemy_level"] == 6
    assert battle["trusted"] is True
    assert battle["confidence"] == "high"
    assert battle["type"] == "wild"
    assert battle["raw"]["wBattleMode"] == 1
    assert battle["raw"]["wTempEnemyMonSpecies"] == 0xB3
    assert battle["raw"]["wCurPartyLevel"] == 6


def test_gold_battle_decoder_exposes_normalized_enemy_object():
    battle = reader({0xD116: 1, 0xD0ED: 0xA1, 0xD0FC: 3, 0xD0FF: 0, 0xD100: 9, 0xD101: 0, 0xD102: 15}).read_battle()

    assert battle["enemy_species_id"] == 0xA1
    assert battle["enemy_species"] == "Sentret"
    assert battle["wild_species_id"] == 0xA1
    assert battle["enemy"]["species"] == "Sentret"
    assert battle["enemy"]["level"] == 3
    assert battle["enemy"]["hp_percent"] == 60


def test_gold_battle_decoder_exposes_enemy_hp_status_and_moves():
    battle = reader({
        0xD116: 1,
        0xD0ED: 0xB3,
        0xD0F1: 33,
        0xD0F2: 45,
        0xD0FC: 6,
        0xD0FD: 0x08,
        0xD0FF: 0,
        0xD100: 12,
        0xD101: 0,
        0xD102: 20,
    }).read_battle()

    assert battle["enemy_moves"] == [33, 45]
    assert battle["enemy_status_raw"] == 0x08
    assert battle["enemy_status_condition"]["poison"] is True
    assert battle["enemy_hp"] == 12
    assert battle["enemy_max_hp"] == 20
    assert battle["enemy_hp_trusted"] is True
    assert battle["trusted"] is True


def test_gold_battle_decoder_rejects_implausible_enemy_hp():
    battle = reader({0xD116: 1, 0xD0ED: 0xB3, 0xD0FC: 6, 0xD0FF: 0, 0xD100: 40, 0xD101: 0, 0xD102: 20}).read_battle()

    assert battle["enemy_hp"] == 40
    assert battle["enemy_max_hp"] == 20
    assert battle["enemy_hp_trusted"] is False
    assert battle["trusted"] is False


def test_gold_battle_mode_type_two_is_trainer_not_wild():
    battle = reader({0xD116: 2, 0xD0ED: 0xB3, 0xD0FC: 6}).read_battle()

    assert battle["type"] == "trainer"
    assert battle["wild"] is False
    assert battle["enemy_species_id"] == 0xB3
    assert battle["enemy_species"] == "Mareep"
    assert battle["wild_species_id"] is None
    assert battle["trusted"] is True


def test_gold_trainer_battle_allows_missing_wild_species():
    battle = reader({0xD116: 2, 0xD0ED: 0x00, 0xD0FC: 6}).read_battle()

    assert battle["type"] == "trainer"
    assert battle["wild"] is False
    assert battle["wild_species_id"] is None
    assert battle["trusted"] is True


def test_gold_battle_marks_species_ff_untrusted():
    battle = reader({0xD116: 1, 0xD0ED: 0xFF, 0xD0FC: 6}).read_battle()

    assert battle["in_battle"] is True
    assert battle["wild"] is False
    assert battle["wild_species_id"] is None
    assert battle["trusted"] is False
    assert battle["confidence"] == "none"


def test_gold_battle_marks_implausible_level_untrusted():
    battle = reader({0xD116: 1, 0xD0ED: 0xB3, 0xD0FC: 255}).read_battle()

    assert battle["enemy_level"] is None
    assert battle["trusted"] is False
    assert battle["confidence"] == "none"


def test_gold_party_entries_mark_implausible_hp_untrusted():
    memory = {
        0xDA22: 1,
        0xDA23: 0x9B,
        0xDA2A + 0x1F: 5,
        0xDA2A + 0x22: 0,
        0xDA2A + 0x23: 40,
        0xDA2A + 0x24: 0,
        0xDA2A + 0x25: 20,
    }

    party = reader(memory).read_party()

    assert party[0]["hp"] == 40
    assert party[0]["max_hp"] == 20
    assert party[0]["trusted"] is False


def test_gold_position_metadata_uses_imported_map_bounds():
    player = reader({0xDA00: 24, 0xDA01: 7, 0xDA02: 0, 0xDA03: 7}).read_player()
    position = player["position"]

    assert position["map_known"] is True
    assert position["actual_x"] == 7
    assert position["actual_y"] == 0
    assert position["in_bounds"] is True
    assert position["trusted"] is True
    assert position["confidence"] == "high"


def test_gold_position_metadata_marks_out_of_bounds_coordinates_untrusted():
    state = reader({0xDA00: 24, 0xDA01: 7, 0xDA02: 0, 0xDA03: 99}).read_map_info()

    assert state["known"] is True
    assert state["position_in_bounds"] is False
    assert state["trusted"] is False
    assert state["confidence"] == "medium"
    assert state["raw"] == {"wMapGroup": 24, "wMapNumber": 7, "wXCoord": 0, "wYCoord": 99}


def test_gold_flags_expose_party_and_bag_terminators():
    flags = reader({
        0xDA22: 1,
        0xDA23: 0x9B,
        0xDA24: 0xFF,
        0xD5B7: 1,
        0xD5B8: 0x04,
        0xD5B9: 2,
        0xD5BA: 0xFF,
    }).read_flags()

    assert flags["party_terminator"] == 0xFF
    assert flags["party_terminator_present"] is True
    assert flags["bag_count"] == 1
    assert flags["bag_count_raw"] == 1
    assert flags["bag_count_trusted"] is True
    assert flags["bag_terminator"] == 0xFF
    assert flags["bag_terminator_present"] is True


def test_gold_flags_expose_decoded_story_flags():
    flags = reader({0xDA22: 1, 0xDA23: 0x9B, 0xDA24: 0xFF, 0xD57C: 0x01, 0xD7BA: 0x44, 0xD7BF: 0x04}).read_flags()

    story_flags = flags["derived_story_flags"]
    assert story_flags["source"] == "gold_event_flags_party_and_badges"
    assert story_flags["event_flags_available"] is True
    assert story_flags["has_starter"] is True
    assert story_flags["has_starter_source"] == "event_flag"
    assert story_flags["got_mystery_egg_from_mr_pokemon"] is True
    assert story_flags["gave_mystery_egg_to_elm"] is False
    assert story_flags["learned_to_catch_pokemon"] is True
    assert story_flags["has_zephyr_badge"] is True
    assert story_flags["badge_count"] == 1


def test_gold_flags_decode_early_event_flag_bits():
    flags = reader({0xD7BA: 0xBC, 0xD7BF: 0x0E, 0xD7B8: 0x01}).read_flags()

    story_events = flags["story_event_flags"]
    assert story_events["got_pokemon_from_elm"] is True
    assert story_events["got_cyndaquil_from_elm"] is True
    assert story_events["got_totodile_from_elm"] is True
    assert story_events["got_chikorita_from_elm"] is True
    assert story_events["got_mystery_egg_from_mr_pokemon"] is False
    assert story_events["gave_mystery_egg_to_elm"] is True
    assert story_events["got_tm31_mud_slap"] is True
    assert story_events["dude_talked_to_you"] is True
    assert story_events["learned_to_catch_pokemon"] is True
    assert story_events["elm_called_about_stolen_pokemon"] is True


def test_gold_bag_entry_with_implausible_quantity_is_untrusted():
    bag = reader({0xD5B7: 1, 0xD5B8: 0x04, 0xD5B9: 250}).read_bag()

    assert bag[0]["quantity"] == 250
    assert bag[0]["trusted"] is False


def test_gold_bag_entries_expose_slot_metadata():
    bag = reader({0xD5B7: 2, 0xD5B8: 0x04, 0xD5B9: 2, 0xD5BA: 0x12, 0xD5BB: 1}).read_bag()

    assert bag[0]["slot"] == 1
    assert bag[0]["item_id"] == 0x04
    assert bag[1]["slot"] == 2
    assert bag[1]["item_id"] == 0x12


def test_gold_bag_skips_empty_items_but_keeps_count_diagnostics():
    gold = reader({0xD5B7: 1, 0xD5B8: 0x00, 0xD5B9: 5, 0xD5BA: 0xFF})

    assert gold.read_bag() == []
    flags = gold.read_flags()
    assert flags["bag_count"] == 1
    assert flags["bag_count_trusted"] is True
    assert flags["bag_terminator"] == 0xFF
    assert flags["bag_terminator_present"] is True


def test_gold_ball_pocket_uses_pokegold_w_num_balls_address():
    bag = reader({
        0xD5B7: 1,
        0xD5B8: 0x12,
        0xD5B9: 1,
        0xD5BA: 0xFF,
        0xD5FC: 1,
        0xD5FD: 0x05,
        0xD5FE: 4,
        0xD5FF: 0xFF,
        # Old bug: these bytes are in/near key-item data and must not define balls.
        0xD5F7: 0,
    }).read_bag()

    assert bag == [
        {"slot": 1, "pocket": "items", "item_id": 0x12, "item": "Potion", "quantity": 1, "trusted": True},
        {"slot": 1, "pocket": "balls", "item_id": 0x05, "item": "Poke Ball", "quantity": 4, "trusted": True},
    ]


def test_gold_flags_expose_ball_pocket_diagnostics():
    flags = reader({0xD5FC: 2, 0xD5FD: 0x05, 0xD5FE: 4, 0xD5FF: 0x04, 0xD600: 1, 0xD601: 0xFF}).read_flags()

    assert flags["balls_count"] == 2
    assert flags["balls_count_raw"] == 2
    assert flags["balls_count_trusted"] is True
    assert flags["balls_terminator"] == 0xFF
    assert flags["balls_terminator_present"] is True


def test_gold_money_exposes_raw_bcd_and_trust():
    player = reader({0xD573: 0x00, 0xD574: 0x18, 0xD575: 0x80}).read_player()

    assert player["money"] == 1880
    assert player["money_raw_bcd"] == [0x00, 0x18, 0x80]
    assert player["money_trusted"] is True
