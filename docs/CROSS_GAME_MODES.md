# Cross-Game Modes

The codebase is Gold/Silver-first today, but the public control surface and
learning files are designed for Red, Blue, Yellow, Gold, and Silver.

## Capability Matrix

| Game | Profile | Current behavior | Gold-specific planner? |
| --- | --- | --- | --- |
| Gold/Silver | `gold_silver` | V2/adaptive planner, starter rotation/nicknames, V1 teacher import, unified delegation to V2 | Yes |
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
- Adaptive recovery, battle/dialogue resume probes, and stuck handoff.
- Live Elm Lab starter macro with persisted starter rotation and safe nickname
  rotation.
- V1 teacher import from `gold_world_model.json` and `gold_policy.json`.

Recommended mode for unattended Gold/Silver play is `adaptive` with live-action
gates enabled. `v2` is useful for deterministic planner debugging. `v1` remains
available as a legacy recovery engine and teacher source.

Fresh Gold/Silver runs rotate starters in V2/adaptive using
`gold_autoplayer_v2_learning.json:starter_selection`:

- Starter order: `cyndaquil -> totodile -> chikorita`.
- Nickname order: `A`, `AA`, `AAA`, `AAAA`, `AAAAA`.
- The live starter macro targets the selected Elm Lab ball using normalized live
  coordinates and short holds to avoid overstepping.
- If the nickname keyboard still appears active after the planned sequence, the
  bot stops typing instead of blindly entering more input.

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

## ROM Onboarding And Isolation

The dashboard/onboarding flow accepts user-provided `.gb`, `.gbc`, and `.gba`
files, records metadata through `/roms`, and returns a restart launch command for
the selected ROM. The server intentionally does not hot-swap emulator ROMs in
process. Switching games requires restarting the server so emulator state, save
files, run snapshots, and learning memory move together.

Uploaded ROMs are isolated under a profile data directory such as
`games/<game>-<romhash>`. This prevents Gold/Silver starter facts, map edges, and
policy weights from contaminating Red/Blue/Yellow/generic profiles.

Temporary public sharing should use read-only watch or upload links. Share
`/dashboard/watch.html` for viewing and avoid sharing `/dashboard/` unless full
control access is intentional.

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
