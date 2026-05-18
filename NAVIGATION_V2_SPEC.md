# Pokemon Gold Navigation V2 Spec

## Purpose

Redesign the Pokemon Gold autoplayer navigation system from scratch while preserving the existing implementation for historical comparison and fallback.

The current `gold_autoplayer.py` should be treated as **V1 legacy**. V2 must be implemented separately and selectable from the dashboard/frontend so we can compare behavior and switch back if needed.

## Background

The V1 bot made progress but is brittle on Route 30 and nearby Cherrygrove because navigation decisions are spread across:

- hardcoded route macros,
- handwritten waypoints,
- learned blocked/open edge overlays,
- stale RAM/dialog workarounds,
- visual screen classification,
- self-recovery logic,
- healing-specific duplicate route code.

These layers can conflict. Examples observed:

- Route 30 tile loops due to waypoint index drift.
- Stale RAM menu/window bytes causing overworld screens to be treated as dialogue.
- Visual classifier false positives causing the bot to press A on normal overworld screens.
- Cherrygrove route macros fighting center/sign/NPC handling.
- Battle recovery blocking movement by repeatedly trying a failed potion flow.

V2 should not add more one-off coordinate patches. It should be a clean architecture with explicit state gating, map/collision pathfinding, verified movement, and robust recovery.

## Keep V1 Separate

Do not overwrite or delete `gold_autoplayer.py`.

Recommended file layout:

- `gold_autoplayer.py`: legacy V1, keep runnable.
- `gold_autoplayer_v2.py`: new bot entrypoint.
- `pokemon_agent/navigation/`: reusable navigation package.
- `pokemon_agent/navigation/gold_maps.py`: Gold map specs/collision maps.
- `pokemon_agent/navigation/world_graph.py`: map/tile graph model.
- `pokemon_agent/navigation/navigator.py`: pathfinding and movement planning.
- `pokemon_agent/navigation/tasks.py`: high-level goals/tasks.
- `pokemon_agent/navigation/executor.py`: verified movement executor.
- `pokemon_agent/navigation/recovery.py`: stuck/replan logic.

The first implementation can be narrower if needed, but keep V2 in separate files.

## Frontend Bot Switching Requirement

The dashboard must let the user switch between V1 and V2.

Add a control to `/dashboard` near the existing bot controls:

- Label: `Bot Engine`
- Options:
  - `Legacy V1`
  - `Navigation V2`
- Display current engine in bot status.
- Changing engine should update server/autoplayer control state.

Recommended control JSON:

```json
{
  "enabled": true,
  "engine": "v1",
  "objective": "beat the first gym",
  "movement_bias": "balanced",
  "dialogue_speed": "fast",
  "guidance_prompt": ""
}
```

Allowed `engine` values:

- `v1`
- `v2`

Implementation options:

1. Single autoplayer service reads `engine` and dispatches to V1 or V2 classes.
2. Two user services exist, but only one is enabled by dashboard control.
3. Server exposes `/autoplayer/engine` and handles service switching.

Preferred first implementation: one service/entrypoint that reads `engine` from `gold_autoplayer_control.json`, then instantiates either legacy or V2 runner. This avoids systemd complexity.

## Dashboard Metrics Requirement

The dashboard should show richer game and bot state.

Existing dashboard files:

- `pokemon_agent/dashboard/static/index.html`
- `pokemon_agent/dashboard/static/app.js`
- `pokemon_agent/dashboard/static/style.css`
- `pokemon_agent/server.py` endpoints and websocket broadcasts.

Add dashboard panels/fields for:

### Game State

- Map name, map group/number, map id.
- Raw RAM position and normalized actual position.
- Facing direction.
- Play time.
- Money.
- Badge count and badge names.
- Current phase: `overworld`, `battle`, `dialogue`, `menu`, `transition`, `recovery`, `unknown`.
- Current route/goal target.
- Whether RAM state is considered reliable.
- Last map transition.

### Inventory

- Bag items with item id, decoded item name where available, quantity.
- Important counts:
  - Potions/healing items.
  - Pokeballs.
  - Escape/utility items if decoded later.
- Unknown items should display as `Item 0xNN` but keep IDs visible.

### Party Pokemon

For each party member:

- Slot.
- Species id and decoded species name.
- Level.
- HP/max HP and HP percent.
- Status condition if available.
- Moves with ids and decoded names where available.
- PP if available.
- Type(s) if available.
- Held item if available.
- Experience/current exp if available.
- Next-level exp if available.

Do not block V2 on full decoding. Add fields progressively, but dashboard should render unknowns gracefully.

### Battle

- `in_battle`.
- Battle type id.
- Enemy species id/name.
- Enemy level.
- Enemy HP if readable.
- Last battle action.
- Battle policy: attack/run/heal/catch.

### Navigation V2 Metrics

Add these to V2 status JSON and render in dashboard:

- `engine`: `v2`.
- `phase`.
- `task_stack` or current task.
- `current_goal`.
- `target_location`: map id/name and tile.
- `planned_path_length`.
- `next_step`.
- `last_step_result`: `moved`, `blocked`, `warped`, `battle_started`, `dialogue_opened`, `timeout`, `unknown`.
- `last_blocked_direction`.
- `replan_count`.
- `stuck_counter`.
- `recovery_level`.
- `static_map_used`: true/false.
- `dynamic_blockers_count`.
- `path_source`: `static_collision`, `learned_graph`, `fallback_explore`, etc.

## Research Summary: Successful Pokemon Bot Patterns

Projects and patterns worth following:

- `40Cakes/pokebot-gen3`: mature map metadata, collision-aware A*, warps, map connections, dynamic blockers.
- `novaoc/pokemon-blue-autobot`: progression state machine plus navigation primitives.
- `PWhiddy/PokemonRedExperiments` / `pokegym`: memory-derived coordinates, event progress, exploration maps.
- BizHawk GSC bots: explicit phase state handling for `Battle`, `StartBattle`, `EndBattle`, `ChangeMap`, `Overworld`, menus.

Common successful architecture:

```text
Memory State -> Phase Dispatcher -> Goal Planner -> Collision/Map Graph -> Verified Step Executor -> Recovery/Replan
```

Core principle: navigation must be closed-loop. Every movement command must be verified against observed state before issuing the next command.

## V2 Architecture

### 1. Game State Reader

Use memory/RAM as source of truth where possible.

Normalize Gold coordinates in the reader or state builder so every downstream component uses actual map coordinates consistently.

Recommended position shape:

```json
{
  "position": {
    "raw_x": 48,
    "raw_y": 7,
    "x": 7,
    "y": 48,
    "map_group": 26,
    "map_number": 1,
    "map_id": 6657,
    "map_name": "Route 30"
  }
}
```

Current known issue fixed in V1 work:

- `pokemon_agent/memory/gold.py` used `window_stack_size > 0` as dialog active.
- This was wrong when `window_stack_size` was stale/implausible, e.g. `138` on overworld Route 30.
- V2 should treat only plausible stack depths as active and should expose raw values separately.

Do not rely on screen classification alone when coordinates and memory state are valid.

### 2. Phase Dispatcher

V2 must separate state handling from navigation.

Suggested phases:

- `BOOT_SYNC`
- `OVERWORLD`
- `FOLLOW_ROUTE`
- `TRANSITION`
- `DIALOGUE`
- `BATTLE`
- `MENU`
- `CUTSCENE_WAIT`
- `RECOVERY`
- `ERROR`

Rules:

- Navigation may send movement only in `OVERWORLD` or `FOLLOW_ROUTE`.
- Dialogue controller owns `DIALOGUE`.
- Battle controller owns `BATTLE`.
- Menu controller owns `MENU`.
- Transition controller waits for map/coordinate stabilization.
- Recovery can interrupt after timeout/stuck detection.

Important: valid coordinates + no visible/structured textbox should generally mean overworld navigation is allowed.

### 3. World Model / Map Graph

Represent each map as a typed graph.

```python
MapSpec(
    id=(26, 1),
    name="Route 30",
    width=20,
    height=54,
    walkable={(x, y), ...},
    edges=[...],
    warps=[...],
    connections=[...],
    ledges=[...],
)
```

Tiles/edges should encode:

- walkable/blocked,
- grass/encounter risk,
- water/surf requirements,
- ledges and one-way edges,
- door/warp transitions,
- map border connections,
- cut trees/strength rocks if needed later,
- trainer line-of-sight risk if available.

Use directional edges, not only passable cells, because Pokemon maps include ledges, doors, warps, spinners, and NPC blockers.

### 4. Static Collision + Dynamic Overlay

Use static map/collision data as base truth. Add runtime overlays:

- learned blocked/open edges,
- NPC/object blockers,
- temporary blocked edges from failed steps,
- trainer lines or avoid zones,
- danger/grass cost when low HP.

Learned blocks should be advisory over known static collision unless repeatedly verified. Avoid permanently blocking known route-critical tiles due to UI/battle/transition confusion.

### 5. Navigator

Use existing `pokemon_agent/pathfinding.py` where possible, but wrap it in Pokemon-specific map handling.

Use:

- BFS for nearest matching tile or equal-cost short paths.
- A* for known destinations.
- Hierarchical routing for cross-map travel.

Navigation API sketch:

```python
class Navigator:
    def plan_to(self, state: GameState, goal: Location) -> Plan:
        ...

    def next_step(self, state: GameState, plan: Plan) -> Step:
        ...

    def replan(self, state: GameState, reason: str) -> Plan:
        ...
```

`Plan` should include:

- path nodes,
- next edge/action,
- destination,
- expected map/position after the next step,
- path source,
- cost/path length.

### 6. Verified Step Executor

Do not fire fixed scripts and assume success.

Every step should produce a result:

```python
class StepResult(Enum):
    MOVED = "moved"
    BLOCKED = "blocked"
    WARPED = "warped"
    BATTLE_STARTED = "battle_started"
    DIALOGUE_OPENED = "dialogue_opened"
    MENU_OPENED = "menu_opened"
    TRANSITION = "transition"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"
```

Step procedure:

1. Read state before input.
2. Send one movement action.
3. Wait until one of these is true:
   - coordinate changed as expected,
   - map changed,
   - battle starts,
   - dialogue/menu opens,
   - timeout.
4. Return `StepResult` and observed state delta.
5. Update world overlay.
6. Replan if actual state diverges from expected state.

### 7. Goal/Task Planner

Separate story goals from movement.

Example task stack:

```text
BeatFalkner
  -> EnsureHealthy
    -> GoToNearestPokemonCenter
    -> HealAtNurse
  -> ReachVioletCity
  -> EnterVioletGym
  -> TalkToFalkner
  -> WinBattle
```

Tasks should have:

- preconditions,
- success conditions,
- target locations,
- allowed interaction behavior,
- recovery fallback.

Do not mark a story step complete only because a coordinate changed. Use map, event flags, badges, item/state, or battle outcome when available.

## Initial V2 Scope

Keep the first V2 milestone focused.

### Milestone 1: Route 30 Reliability

Implement:

- V2 entrypoint/service selected by dashboard.
- Gold position normalization.
- Phase dispatcher using memory-first state.
- Static `MapSpec` for:
  - Route 29 if needed to get back from loaded saves,
  - Cherrygrove City,
  - Route 30,
  - Route 31,
  - Cherrygrove Pokemon Center 1F.
- Same-map BFS/A* navigation.
- Route 30 to Route 31 planning using collision map.
- Low-HP route to Cherrygrove Pokemon Center using the same navigator.
- Verified movement step result.
- Dashboard engine selector and V2 metrics.

### Milestone 2: Violet City / First Gym

Add:

- Route 31 to Violet gate.
- Violet City map spec.
- Violet Pokemon Center map spec.
- Violet Gym map spec.
- Falkner interaction task.
- Battle policy refinements.

## API / Status JSON

Add V2 status endpoint or extend existing `/autoplayer/status`.

Recommended status shape:

```json
{
  "engine": "v2",
  "enabled": true,
  "phase": "FOLLOW_ROUTE",
  "objective": "beat the first gym",
  "current_task": "ReachRoute31",
  "task_stack": ["BeatFalkner", "ReachVioletCity", "ReachRoute31"],
  "current_goal": {
    "type": "reach_tile",
    "map_group": 26,
    "map_number": 2,
    "map_name": "Route 31",
    "x": 4,
    "y": 6
  },
  "navigation": {
    "map_spec": "Route 30",
    "path_source": "static_collision",
    "planned_path_length": 42,
    "next_step": "walk_up",
    "expected_after": {"map_group": 26, "map_number": 1, "x": 5, "y": 25},
    "last_step_result": "moved",
    "replan_count": 3,
    "stuck_counter": 0,
    "recovery_level": 0
  },
  "last_action": ["walk_up", "wait_80"],
  "updated_at": 1779040000.0
}
```

## Server / Dashboard Work

### Server

Modify `pokemon_agent/server.py`:

- Add `engine` to `AutoplayerControlRequest`.
- Persist `engine` in `gold_autoplayer_control.json`.
- Ensure `/autoplayer/control` accepts and returns it.
- Ensure `/autoplayer/status` exposes engine-specific status.

If using one entrypoint, V2 runner reads the same control file and switches internally.

### Dashboard

Modify:

- `pokemon_agent/dashboard/static/index.html`
- `pokemon_agent/dashboard/static/app.js`
- `pokemon_agent/dashboard/static/style.css`

Add:

- bot engine selector,
- richer metrics panel,
- inventory panel,
- expanded party cards,
- navigation diagnostics panel.

Dashboard should degrade gracefully when V1 is active and V2-specific fields are unavailable.

## Testing / Verification

Add pure tests where possible before emulator tests.

Recommended test cases:

- Gold coordinate normalization from raw RAM fields.
- Route 30 path from every known stuck tile to Route 31 target.
- Route 30 path from upper dead-end pockets replans correctly.
- Route 30 low-HP path to Cherrygrove Pokemon Center target.
- Static collision path avoids known ledges/tree walls.
- Step executor classifies moved vs blocked vs battle vs dialogue.
- Phase dispatcher chooses overworld when coordinates exist and no real textbox/menu is active.

Suggested manual verification commands:

```bash
python3 -m py_compile gold_autoplayer_v2.py pokemon_agent/navigation/*.py
systemctl --user restart pokemon-gold-autoplayer.service
python3 gold_autoplayer_watch.py --seconds 300 --interval 3
```

Success criteria for Milestone 1:

- Dashboard can switch `Legacy V1`/`Navigation V2`.
- V2 status shows current goal, planned path length, next step, last step result.
- Bot can leave Cherrygrove, enter Route 30, and make monotonic progress toward Route 31 without repeated same-tile/dialogue loops.
- If battle interrupts, battle controller exits/handles it, then navigator replans from current position.
- If low HP, bot navigates to Pokemon Center using the same navigation layer.

## Important Constraints

- Do not include ROMs.
- Do not delete V1 files or historical logs.
- Do not make V2 depend on visual-only navigation when RAM coordinates are available.
- Do not mix route-specific hacks into the generic navigator.
- Prefer small, testable modules over a single giant controller.
- Use map/collision data and verified movement instead of fixed input scripts.

## Current Known Files

- Legacy bot: `gold_autoplayer.py`
- Watcher: `gold_autoplayer_watch.py`
- Memory reader: `pokemon_agent/memory/gold.py`
- Existing generic A*: `pokemon_agent/pathfinding.py`
- Server/API: `pokemon_agent/server.py`
- Dashboard: `pokemon_agent/dashboard/static/index.html`, `app.js`, `style.css`
- Current control file at runtime: `/home/mojo/.pokemon-agent-gold/gold_autoplayer_control.json`
- Current status file at runtime: `/home/mojo/.pokemon-agent-gold/gold_autoplayer_status.json`
- World model/logs at runtime:
  - `/home/mojo/.pokemon-agent-gold/gold_world_model.json`
  - `/home/mojo/.pokemon-agent-gold/gold_events.jsonl`
  - `/home/mojo/.pokemon-agent-gold/gold_autoplayer.jsonl`

## Recommended Next-Agent First Steps

1. Read this spec fully.
2. Inspect `gold_autoplayer.py`, but do not modify it except for a thin compatibility wrapper if absolutely necessary.
3. Create `gold_autoplayer_v2.py` and `pokemon_agent/navigation/` modules.
4. Add `engine` to server control and dashboard selector.
5. Implement a small V2 loop that only handles: phase dispatch, Route 30/Cherrygrove/Route 31 map specs, route-to-goal, verified step result.
6. Add dashboard V2 metrics.
7. Run pure tests, then live watcher verification.
