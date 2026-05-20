# Unified Learning Autoplayer Spec

This document defines the path from the Hermes `pokemon-player` skill plus the
Gold V2 bot into one learning autoplayer for the Game Boy Pokemon games this
repo can serve.

Scope note: Red, Blue, and Yellow are Generation 1. Gold and Silver are
Generation 2, but they share the same Game Boy style control surface and can use
the same server/action/learning loop with game-specific plugins.

## Goals

- One runner loop for Red, Blue, Yellow, Gold, and Silver.
- Per-game plugins for RAM normalization, map registry, story goals, menu/battle
  policies, and route targets.
- Durable learning memory that records objectives, progress, stuck cases, map
  discoveries, team notes, and strategy notes with `PKM:` prefixes.
- Verified short actions by default: observe, decide, act, verify, record.
- Visual fallback and screenshot-based uncertainty handling whenever RAM is
  missing or ambiguous.
- Fair-play learning from observed state, screenshots, action outcomes, and
  verified transitions only.
- Public read-only viewing by default; control dashboard is local/private unless
  intentionally exposed.

## What We Reuse

### From Upstream pokemon-agent

- FastAPI server and `/state`, `/action`, `/screenshot`, save/load APIs.
- PyBoy emulator wrapper.
- Red/Blue RAM reader as the Gen 1 starting point.
- Generic state builder and visual textbox features.
- Generic tile A* pathfinding primitives.

### From Hermes pokemon-player Skill

- Operational loop: Observe -> Orient -> Act -> Verify -> Record -> Save.
- Short action batches with frequent visual verification.
- Aggressive milestone saves.
- Persistent memory prefixes such as `PKM:OBJECTIVE`, `PKM:MAP`, `PKM:STUCK`,
  `PKM:PROGRESS`, `PKM:TEAM`, `PKM:STRATEGY`, `PKM:POLICY`, `PKM:RESOURCE`,
  `PKM:FAILURE`, and `PKM:OUTCOME`.
- ROM safety policy: never download or distribute ROMs.

### From Gold V2

- Verified action executor and smooth hold-based movement.
- RAM trust/readiness checks.
- Dialogue/menu/battle uncertainty gates.
- Blocked-edge learning, battle/dialogue resume probes, and stuck recovery.
- Cross-map route planner architecture.
- Story objective and gameplay policy shapes.

## Plugin Boundaries

The shared runner should depend on a `GameProfile` instead of hardcoding Gold or
Red/Blue behavior.

Each profile provides:

- `game_id`: stable identifier such as `red_blue`, `yellow`, or `gold_silver`.
- `generation`: numeric generation.
- `reader`: memory reader identifier.
- `map_registry`: optional map registry name.
- `story_module`: optional story planner name.
- `battle_policy`: optional battle policy name.
- `capabilities`: booleans for structured RAM, map registry, story planner,
  battle policy, menu decoder, and visual fallback.

## Current Capability Matrix

| Game family | Structured RAM | Map registry | Story planner | Battle policy | Learning memory |
| --- | --- | --- | --- | --- | --- |
| Red/Blue | Yes | Missing | Missing | Basic future work | Shared now |
| Yellow | Needs validation | Missing | Missing | Basic future work | Shared now |
| Gold/Silver | Yes | Yes | Yes | Partial | Shared now |

## Runner Loop

1. Read `/state` and `/screenshot` metadata.
2. Normalize state through the selected game profile.
3. Classify phase: boot, dialogue, menu, battle, overworld, uncertainty, stuck.
4. Pick the smallest safe logical action.
5. Expand action into smooth server actions such as `hold_right_48`, `wait_12`,
   or `press_a`, `wait_30`.
6. POST `/action`.
7. Read `/state` again and verify one logical action.
8. Record progress, failures, and stuck recovery to learning memory.
9. Save on milestones/risk/interval.

## Learning Memory Schema

The memory file is JSON and contains append-friendly categorized facts:

- `PKM:OBJECTIVE`: current and recent goals.
- `PKM:PROGRESS`: milestones and story flags observed.
- `PKM:MAP`: learned blocked edges, useful transitions, and landmarks.
- `PKM:STUCK`: stuck situations and successful fixes.
- `PKM:TEAM`: party, roles, weaknesses, and desired catches.
- `PKM:STRATEGY`: battle and routing notes.
- `PKM:POLICY`: mode handoffs, readiness gates, selected policies, and safety
  decisions.
- `PKM:RESOURCE`: money, balls, healing items, HP/level readiness, and restock
  gates observed from live state.
- `PKM:FAILURE`: no-action blockers, hard-stuck contexts, API/control failures,
  and actions that should not be repeated in the same context.
- `PKM:OUTCOME`: action outcomes, rewards, verification results, and policy
  effects that future runs may use as fallback/tie-breaker evidence.

The first shared implementation lives in `pokemon_agent.autoplayer.learning`.

V2 also writes a transparent transition-learning file named
`gold_autoplayer_v2_learning.json`. It tracks reward-shaped action statistics,
tile visits, and blocked-edge evidence. These values are diagnostic/tie-breaker
material, not an unchecked replacement for the verified route planner.

Supervisor mode handoffs are mirrored into shared memory as `PKM:POLICY` and, for
hard-stuck rescues, `PKM:FAILURE`. V2/adaptive also records resource/readiness
facts when current live state proves the bot is not ready for an objective. Future
runs may use these facts for explanations, conservative gates, and fallback
ranking, but current RAM/vision state remains authoritative.

V1 teacher evidence is imported through
`pokemon_agent.autoplayer.learning.import_gold_v1_teacher_snapshot()`. The import
mirrors bounded facts from `gold_world_model.json` and `gold_policy.json` into
`pokemon_learning_memory.json` with `source: "v1_teacher"`. Imported facts are
used as evidence for fallback decisions and diagnostics; they do not replace
verified route planning.

Gold/Silver starter selection is profile-local learning, not global policy.
`gold_autoplayer_v2_learning.json:starter_selection` rotates starters and safe
nicknames for fresh runs, then records the completed choice as `PKM:TEAM`.

## Learning Techniques

- Reward shaping: small rewards for verified actions, movement, map transitions,
  tile novelty, catches, badges, and penalties for blocked/unverified movement.
- Bandit/action-value tables: JSON-backed state/action counts and mean rewards so
  ambiguous recovery choices can eventually prefer what worked before.
- Resume probes: if verified progress stops, V2/adaptive use bounded recovery
  sequences for blocked navigation, battle fallback no-progress, and ambiguous
  text/menu states before escalating to supervisor handoff.
- Planner imitation: V2 can record planner-suggested actions and later reuse them
  only in matching map/objective contexts after verification.
- Curriculum: story goals remain explicit and observable; learned retry/failure
  counts should demote objectives that are temporarily blocked instead of
  skipping prerequisites.
- Per-ROM isolation: every ROM/profile should have a separate data directory so
  saves, runs, world models, and learned action values never cross-contaminate.

## Fair-Play Rules

- Learn only from `/state`, `/screenshot`, `/action` responses, and verified
  before/after transitions.
- Static map geometry may be used for pathfinding but must be treated as a
  declared source, not learned discovery.
- Do not inspect hidden future state, hidden RNG, encounter tables, enemy future
  moves, or save-state branches to choose actions.
- Do not promote V1 facts into V2 planner input without repeated verification and
  source/confidence metadata.
- User objective/guidance is authoritative and should override learned habits.

## Implementation Order

1. Add shared game profiles and learning memory primitives. Done in this branch.
2. Add a neutral `pokemon_autoplayer.py` runner that delegates Gold/Silver to
   current Gold V2 and uses a conservative learning fallback elsewhere. Done in
   this branch.
3. Add dashboard/API mode controls for V1, V2, unified, and live-action gates.
    Done in this branch.
4. Persist V2 transition rewards/action statistics. Done in this branch.
5. Import V1 teacher evidence into shared memory and add supervisor stuck handoff.
   Done in this branch.
6. Extract Gold V2 action verification/recovery into shared modules.
7. Add `red_blue` profile with Red/Blue state adapter and early Kanto goals.
8. Make `gold_silver` profile call current Gold V2 planner/policies through the
   same plugin interface instead of direct delegation.
9. Add `pokered` map importer or hand-authored early Kanto map registry.
10. Validate Yellow RAM offsets or add a `yellow` reader/profile.
11. Replace Gold-specific supervisor naming with neutral game-aware engine names.

## Non-Goals

- Do not batch long unverified route segments until multi-action verification is
  implemented.
- Do not expose the control dashboard publicly by default.
- Do not include or fetch ROMs.
