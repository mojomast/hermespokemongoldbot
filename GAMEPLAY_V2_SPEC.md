# Pokemon Gold Gameplay V2 Spec

## Purpose

Navigation V2 must grow into a full-game automation stack. Movement alone is not enough: Pokemon Gold requires battle decisions, catching, roster planning, item usage, menu control, healing, HM gating, event flags, and story objectives.

## Architecture

Use this flow for every turn:

```text
Raw /state -> GameSnapshot -> PhaseDispatcher -> StoryPlanner -> Controller -> Executor -> Verification
```

Controllers should be separate:

- `NavigationController`: map/path/step execution.
- `BattleController`: fight/run/heal/catch decisions.
- `CatchController`: target species, ball budget, duplicate handling.
- `RosterController`: lead choice, HM roles, party capacity, PC policies.
- `BagController`: item counts, purchase/sell policy, menu interactions.
- `HealingController`: center routing and in-battle item policy.
- `StoryController`: badge/event/objective progression.
- `RecoveryController`: stuck, blackout, menu desync, wrong map, low resources.

## Required Data

Add incrementally, but keep IDs visible when names are unknown:

- Complete species table.
- Complete item table and bag pocket decoding.
- Move table with names, type, power, accuracy, PP.
- Type chart.
- Map collision, warps, map connections, ledges, water, cut trees, strength rocks.
- NPC/object blockers and trainer sight lines.
- Named event flags for story gates and defeated trainers.
- Battle RAM: enemy HP/status, active player mon, PP, battle menu cursor, outcome.
- Menu RAM: active menu identity, cursor positions, list scroll offsets.

## Baseline Policies

### Catching

- Catch useful progression species when balls are available.
- Avoid duplicates unless a task explicitly wants one.
- Avoid catching when lead HP is unsafe.
- If party is full, only catch high-priority/HM-role species until PC policy exists.

### Roster

- Keep a main attacker, ideally starter early.
- Maintain candidates for Fly, Surf, Strength, and Flash roles.
- Do not overwrite strong battle moves with HMs unless no mule exists.
- Before long dungeons/gyms, verify HP/PP/resources.

### Items

- Maintain balls, healing items, status heals, escape ropes/repels.
- Use centers before consumables when practical.
- Use in-battle healing only for trainer/gym/E4 or if escape is unsafe.
- Never sell key progression items, HMs, or unique TMs without explicit policy.

### Story

Story objectives should expose:

- prerequisites,
- target locations,
- interactions,
- success checks,
- fallback/recovery.

Initial objective skeleton lives in `pokemon_agent/gameplay/story.py`.

## Current Implementation

Implemented foundations and active policies:

- `pokemon_agent/gameplay/state_model.py`: normalized `GameSnapshot` from `/state`.
- `pokemon_agent/gameplay/gold_data.py`: partial trusted species/item/HM-role tables.
- `pokemon_agent/gameplay/policies.py`: inventory summary, catch policy, roster roles.
- `pokemon_agent/gameplay/story.py`: story objective planner, early-game resource
  gates, Falkner readiness, registry exploration fallback, and starter targets.
- `gold_autoplayer_v2.py`: V2/adaptive runner with verified actions,
  short-hold movement pacing, live Elm Lab starter macro, rotating starter choice,
  safe nickname rotation, battle fallback, catch/run/heal policies, blocked-edge
  recovery, battle no-progress resume probes, and ambiguous dialogue resume probes.
- `gold_autoplayer_service.py`: supervisor process selection and automatic
  V1/V2/adaptive handoff when local recovery cannot resume progress.

Current next implementation steps are to extract the working V2/adaptive recovery
controller into smaller reusable modules, keep adding route/story coverage beyond
the early game, and validate additional game profiles before enabling non-Gold
planners.
