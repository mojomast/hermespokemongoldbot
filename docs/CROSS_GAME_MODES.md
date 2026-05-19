# Cross-Game Modes

The codebase is Gold/Silver-first today, but the public control surface and
learning files are designed for Red, Blue, Yellow, Gold, and Silver.

## Capability Matrix

| Game | Profile | Current behavior | Gold-specific planner? |
| --- | --- | --- | --- |
| Gold/Silver | `gold_silver` | V2/adaptive planner, V1 teacher import, unified delegation to V2 | Yes |
| Red/Blue | `red_blue` | Unified fallback learning with structured-state detection | No |
| Yellow | `yellow` | Unified fallback learning, RAM offsets not fully validated | No |
| Unknown GB/GBC | `generic_gb` | Unified visual/dialog/battle fallback learning | No |

## Profile Detection

`UniversalAutoplayer.detect_profile()` inspects `/state.metadata.game` and maps
the current ROM to a `GameProfile` from `pokemon_agent.autoplayer.profiles`.

Profiles declare capabilities such as structured RAM, map registry, story
planner, battle policy, menu decoder, and visual fallback. Code should check
capabilities before calling game-specific logic.

## Gold/Silver

Gold/Silver has the most mature stack:

- RAM reader with map/position/party/bag/battle/story signals.
- Static map registry and route planner.
- Story objective selector.
- V2 verified action loop.
- Adaptive recovery and stuck handoff.
- V1 teacher import from `gold_world_model.json` and `gold_policy.json`.

Recommended mode for unattended Gold/Silver play is `adaptive` with live-action
gates enabled. `v2` is useful for deterministic planner debugging. `v1` remains
available as a legacy recovery engine and teacher source.

## Red/Blue

Red/Blue should run through `unified` until Kanto-specific route/story plugins
exist. Unified mode currently uses conservative fallback actions:

- Press `A` for dialogue/textbox signals.
- Press `A` in battle as a minimal safe fallback.
- Explore with short directional holds outside battle.
- Record movement and no-progress facts into `pokemon_learning_memory.json` under
  `game_id: "red_blue"`.

Do not route Red/Blue through Gold V1/V2/adaptive. Their maps, story flags, RAM
offsets, and objectives differ.

## Yellow

Yellow is treated separately from Red/Blue because offsets and behavior can
differ. Until validated, use `unified` only:

- Conservative visual/dialog fallback.
- Local profile-scoped learning facts under `game_id: "yellow"`.
- No Gold/Silver story planner or map registry calls.

## Learning Across Games

Shared learning uses `pokemon_learning_memory.json`, but facts are scoped by
`game_id` and source. This allows common code to summarize learning without
mixing unsafe game-specific assumptions.

Safe to share as conventions:

- Dialogue often advances with `A`.
- Repeated unchanged position after a walk is a stuck signal.
- Verified before/after transitions are more trustworthy than raw action intent.

Not safe to share directly:

- Map coordinates.
- Story flags and objectives.
- Blocked edges.
- Battle menus or cursor offsets.
- Learned policy weights from one ROM/profile.

## Adding A New Game Plugin

1. Add or validate a `GameProfile` in `pokemon_agent.autoplayer.profiles`.
2. Add a reader or adapter that normalizes `/state` fields for that game.
3. Add map registry and story objective modules only after they are validated.
4. Keep the runner in dry-run/shadow mode until actions verify reliably.
5. Store learning under the ROM/profile data directory.
6. Add tests proving the profile does not call Gold-specific planners.

## Promotion Rules

A learned or game-specific policy should only replace fallback behavior after it
has profile-local evidence:

- Repeated verified progress in the same phase/map/screen class.
- Low recent stuck rate.
- No unresolved hard-stuck loops.
- Clear status telemetry showing policy source, confidence, and switch reason.

Until then, fallback should stay narrow, reversible, and easy to debug.
