from pokemon_agent.navigation import (
    CHERRYGROVE_TARGETS,
    GOLD_MAP_REGISTRY,
    ROUTE30,
    ROUTE31_TARGETS,
    RouteTarget,
    find_map_path,
    plan_route_to_target,
    plan_to_any,
    transition_between,
)


def test_route30_can_plan_to_route31_from_lower_entrance():
    plan = plan_to_any(ROUTE30, (7, 53), ROUTE31_TARGETS)

    assert plan is not None
    assert plan.goal in ROUTE31_TARGETS
    assert plan.next_step == "walk_up"
    assert plan.planned_path_length > 40


def test_cross_map_plan_at_blocked_connection_exit_uses_alternate_source_tile():
    target = RouteTarget(map_key=(24, 3), tiles=frozenset({(50, 8)}), name="route29")

    plan = plan_route_to_target(
        GOLD_MAP_REGISTRY,
        (24, 4),
        (0, 8),
        target,
        {(24, 4): frozenset({((0, 8), "left")})},
    )

    assert plan is not None
    assert plan.next_action != "walk_left"
    assert plan.same_map_plan is not None
    assert plan.same_map_plan.goal == (0, 9)


def test_cross_map_plan_returns_none_when_all_connection_exits_blocked():
    target = RouteTarget(map_key=(24, 3), tiles=frozenset({(50, 8)}), name="route29")

    plan = plan_route_to_target(
        GOLD_MAP_REGISTRY,
        (24, 4),
        (0, 8),
        target,
        {(24, 4): frozenset({((0, 8), "left"), ((0, 9), "left")})},
    )

    assert plan is None


def test_cross_map_plan_on_bottom_edge_warp_emits_exit_walk():
    target = RouteTarget(map_key=(24, 5), tiles=frozenset({(6, 4)}), name="starter")

    plan = plan_route_to_target(GOLD_MAP_REGISTRY, (24, 6), (6, 7), target)

    assert plan is not None
    assert plan.next_action == "walk_down"


def test_route30_can_plan_to_cherrygrove_from_upper_route():
    plan = plan_to_any(ROUTE30, (5, 0), CHERRYGROVE_TARGETS)

    assert plan is not None
    assert plan.goal in CHERRYGROVE_TARGETS
    assert plan.next_step == "walk_down"


def test_route30_rejects_blocked_start():
    plan = plan_to_any(ROUTE30, (0, 0), ROUTE31_TARGETS)

    assert plan is None


def test_route30_dead_end_pocket_has_escape_path():
    plan = plan_to_any(ROUTE30, (19, 5), ROUTE31_TARGETS)

    assert plan is not None
    assert plan.goal in ROUTE31_TARGETS
    assert plan.planned_path_length > 0


def test_imported_registry_contains_full_gold_map_metadata():
    maps = GOLD_MAP_REGISTRY.all_maps()

    assert len(maps) >= 380
    assert GOLD_MAP_REGISTRY.validate_warp_destinations() == []


def test_imported_players_house_2f_warp_matches_pokecrystal():
    map_spec = GOLD_MAP_REGISTRY.require((24, 7))

    assert map_spec.map_const == "PLAYERS_HOUSE_2F"
    assert map_spec.width == 8
    assert map_spec.height == 6
    assert len(map_spec.warps) == 1
    warp = map_spec.warps[0]
    assert warp.source == (7, 0)
    assert warp.dest_key == (24, 6)
    assert warp.dest == (9, 0)
    assert map_spec.metadata["block_path"] == "maps/PlayersHouse2F.blk"
    assert map_spec.metadata["collision_path"] == "data/tilesets/players_room_collision.asm"
    assert len(map_spec.metadata["blockdata_hex"]) == 24
    assert len(map_spec.terrain_rows) == map_spec.height
    assert all(len(row) == map_spec.width for row in map_spec.terrain_rows)
    assert map_spec.is_walkable((7, 0))


def test_imported_new_bark_house_and_lab_warps_are_available():
    new_bark = GOLD_MAP_REGISTRY.require((24, 4))
    warp_destinations = {warp.dest_map_const: warp.source for warp in new_bark.warps}

    assert new_bark.map_const == "NEW_BARK_TOWN"
    assert new_bark.metadata["block_path"] == "maps/NewBarkTown.blk"
    assert warp_destinations["ELMS_LAB"] == (6, 3)
    assert warp_destinations["PLAYERS_HOUSE_1F"] == (13, 5)


def test_imported_blockdata_sizes_match_map_dimensions():
    for map_spec in GOLD_MAP_REGISTRY.all_maps():
        width_blocks = int(map_spec.metadata["width_blocks"])
        height_blocks = int(map_spec.metadata["height_blocks"])
        blockdata_hex = str(map_spec.metadata["blockdata_hex"])

        assert blockdata_hex, map_spec.map_const
        assert len(blockdata_hex) == width_blocks * height_blocks * 2


def test_imported_collision_can_plan_to_players_house_2f_stairs():
    map_spec = GOLD_MAP_REGISTRY.require((24, 7))
    plan = plan_to_any(map_spec, (4, 2), {(7, 0)})

    assert plan is not None
    assert plan.next_step == "walk_right"
    assert plan.actions == ("walk_right", "walk_right", "walk_up", "walk_right", "walk_up")


def test_imported_route30_collision_has_grass_and_walkable_path():
    map_spec = GOLD_MAP_REGISTRY.require((26, 1))
    plan = plan_to_any(map_spec, (7, 53), {(4, 0), (5, 0)})

    terrain = "".join(map_spec.terrain_rows)

    assert map_spec.metadata["collision_path"] == "data/tilesets/johto_collision.asm"
    assert "g" in terrain
    assert "w" in terrain
    assert plan is not None
    assert plan.next_step == "walk_up"


def test_find_map_path_from_players_room_to_elms_lab_uses_warps():
    path = find_map_path(GOLD_MAP_REGISTRY, (24, 7), {(24, 5)})

    assert path == ((24, 7), (24, 6), (24, 4), (24, 5))


def test_transition_between_players_house_2f_and_1f_is_warp():
    transition = transition_between(GOLD_MAP_REGISTRY, (24, 7), (24, 6))

    assert transition is not None
    assert transition.kind == "warp"
    assert transition.source_tiles == ((7, 0),)
    assert transition.dest_tiles == ((9, 0),)
    assert transition.warp is not None
    assert transition.warp.dest_map_const == "PLAYERS_HOUSE_1F"


def test_cross_map_plan_from_players_room_targets_first_warp():
    target = RouteTarget(map_key=(24, 5), tiles=frozenset({(6, 4)}), name="starter")

    plan = plan_route_to_target(GOLD_MAP_REGISTRY, (24, 7), (4, 2), target)

    assert plan is not None
    assert plan.map_path == ((24, 7), (24, 6), (24, 4), (24, 5))
    assert plan.next_transition is not None
    assert plan.next_transition.kind == "warp"
    assert plan.next_transition.dest_key == (24, 6)
    assert plan.same_map_plan is not None
    assert plan.same_map_plan.goal == (7, 0)
    assert plan.next_action == "walk_right"


def test_cross_map_plan_from_players_house_1f_targets_town_exit():
    target = RouteTarget(map_key=(24, 5), tiles=frozenset({(6, 4)}), name="starter")

    plan = plan_route_to_target(GOLD_MAP_REGISTRY, (24, 6), (9, 0), target)

    assert plan is not None
    assert plan.map_path == ((24, 6), (24, 4), (24, 5))
    assert plan.next_transition is not None
    assert plan.next_transition.dest_key == (24, 4)
    assert plan.same_map_plan is not None
    assert plan.same_map_plan.goal in {(6, 7), (7, 7)}
    assert plan.next_action is not None


def test_cross_map_plan_same_map_inside_elms_lab_targets_starter_tile():
    target = RouteTarget(map_key=(24, 5), tiles=frozenset({(6, 4)}), name="starter")

    plan = plan_route_to_target(GOLD_MAP_REGISTRY, (24, 5), (4, 11), target)

    assert plan is not None
    assert plan.next_transition is None
    assert plan.path_source == "same_map_static_collision"
    assert plan.same_map_plan is not None
    assert plan.same_map_plan.goal == (6, 4)
    assert plan.next_action is not None


def test_new_bark_to_route29_connection_tiles():
    transition = transition_between(GOLD_MAP_REGISTRY, (24, 4), (24, 3))

    assert transition is not None
    assert transition.kind == "connection"
    assert transition.source_tiles == ((0, 8), (0, 9))
    assert transition.dest_tiles == ((59, 8), (59, 9))
    assert transition.exit_action == "walk_left"


def test_route29_to_new_bark_connection_tiles():
    transition = transition_between(GOLD_MAP_REGISTRY, (24, 3), (24, 4))

    assert transition is not None
    assert transition.source_tiles == ((59, 8), (59, 9))
    assert transition.dest_tiles == ((0, 8), (0, 9))
    assert transition.exit_action == "walk_right"


def test_route29_to_cherrygrove_connection_tiles():
    transition = transition_between(GOLD_MAP_REGISTRY, (24, 3), (26, 3))

    assert transition is not None
    assert transition.source_tiles == ((0, 6), (0, 7))
    assert transition.dest_tiles == ((39, 6), (39, 7))


def test_cherrygrove_to_route29_connection_tiles():
    transition = transition_between(GOLD_MAP_REGISTRY, (26, 3), (24, 3))

    assert transition is not None
    assert transition.source_tiles == ((39, 6), (39, 7))
    assert transition.dest_tiles == ((0, 6), (0, 7))


def test_route30_to_route31_connection_tiles_use_block_offset():
    transition = transition_between(GOLD_MAP_REGISTRY, (26, 1), (26, 2))

    assert transition is not None
    assert transition.source_tiles == ((4, 0), (5, 0), (6, 0), (7, 0))
    assert transition.dest_tiles == ((24, 17), (25, 17), (26, 17), (27, 17))
    assert transition.exit_action == "walk_up"


def test_route31_to_route30_connection_tiles_use_block_offset():
    transition = transition_between(GOLD_MAP_REGISTRY, (26, 2), (26, 1))

    assert transition is not None
    assert transition.source_tiles == ((24, 17), (25, 17), (26, 17), (27, 17))
    assert transition.dest_tiles == ((4, 0), (5, 0), (6, 0), (7, 0))
    assert transition.exit_action == "walk_down"


def test_cross_map_plan_new_bark_to_route29_walks_to_west_exit():
    target = RouteTarget(map_key=(24, 3), tiles=frozenset({(50, 8)}), name="route29")

    plan = plan_route_to_target(GOLD_MAP_REGISTRY, (24, 4), (6, 8), target)

    assert plan is not None
    assert plan.next_transition is not None
    assert plan.next_transition.kind == "connection"
    assert plan.same_map_plan is not None
    assert plan.same_map_plan.goal in {(0, 8), (0, 9)}
    assert plan.next_action is not None


def test_cross_map_plan_at_new_bark_exit_executes_walk_left():
    target = RouteTarget(map_key=(24, 3), tiles=frozenset({(50, 8)}), name="route29")

    plan = plan_route_to_target(GOLD_MAP_REGISTRY, (24, 4), (0, 8), target)

    assert plan is not None
    assert plan.same_map_plan is not None
    assert plan.same_map_plan.planned_path_length == 0
    assert plan.next_action == "walk_left"


def test_cross_map_plan_at_route30_north_exit_executes_walk_up():
    target = RouteTarget(map_key=(26, 2), tiles=frozenset({(24, 17)}), name="route31")

    plan = plan_route_to_target(GOLD_MAP_REGISTRY, (26, 1), (4, 0), target)

    assert plan is not None
    assert plan.same_map_plan is not None
    assert plan.same_map_plan.planned_path_length == 0
    assert plan.next_action == "walk_up"
