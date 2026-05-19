# Autoplayer Architecture

This repository runs a small hierarchy of Pokemon bot controllers behind one HTTP
server and one supervisor service. The design goal is simple: use the strongest
controller available, verify every short action, learn from the result, and swap
controllers only when the current one is stuck.

## Runtime Services

- `pokemon-agent serve`: starts the FastAPI/PyBoy server. It owns the emulator,
  `/state`, `/action`, screenshots, WebRTC, dashboards, ROM onboarding, and bot
  control/status APIs.
- `gold_autoplayer_service.py`: starts exactly one bot child process at a time.
  It reads `gold_autoplayer_control.json`, launches the selected engine, watches
  for crashes, and performs automatic stuck handoff.
- `gold_autoplayer.py`: legacy V1 Gold/Silver controller.
- `gold_autoplayer_v2.py`: Gold/Silver V2 planner, adaptive recovery, and shared
  learning bridge.
- `pokemon_autoplayer.py`: unified cross-game runner. Gold/Silver delegates to
  V2; Red/Blue/Yellow use conservative profile-aware fallback learning until
  their own Kanto planners are implemented.

## Control Flow

```text
dashboard/API -> gold_autoplayer_control.json
             -> gold_autoplayer_service.py
             -> selected child runner
             -> /state -> decide -> /action -> verify -> learn -> status JSON
```

The server does not run bot policy directly. It exposes the emulator and stores
control/status files. The supervisor owns process switching so individual bot
runners never need to kill or replace themselves.

## Engine Modes

| Engine | Runner | Best use |
| --- | --- | --- |
| `v1` | `gold_autoplayer.py` | Legacy visual/RAM behavior, title/menu oddities, fallback when V2/adaptive hard-stuck |
| `v2` | `gold_autoplayer_v2.py` | Gold/Silver route planning, story goals, verified movement, battle/gameplay policy |
| `adaptive` | `gold_autoplayer_v2.py` | V2 as default plus short recovery policies when stuck or oscillating |
| `unified` | `pokemon_autoplayer.py` | Cross-game profile runner; Gold/Silver delegates to V2, Gen 1 uses safe fallback learning |

`adaptive` is not a separate algorithm from V2. It is V2 with extra recovery
gates enabled by `control.engine == "adaptive"`.

## Automatic Handoff

The supervisor can automatically swap engines when a runner reports hard stuck.
Defaults live in `gold_autoplayer_service.py` and are also exposed through
`POST /autoplayer/control`:

```json
{
  "auto_handoff_enabled": true,
  "auto_handoff_v1_fallback": "adaptive",
  "auto_handoff_v2_fallback": "v1"
}
```

Gold/Silver handoff rules:

- `v2`, `adaptive`, or Gold `unified` hard-stuck -> `v1`.
- `v1` stuck/oscillating -> `adaptive`.
- A cooldown prevents ping-pong between engines.
- Handoff details are written to `control.auto_handoff` and
  `gold_autoplayer_supervisor_status.json:last_handoff`.

Hard stuck signals include V2 recovery level, repeated failed verification,
button failure circuits, and safety-circuit path sources. V1 stuck signals come
from its existing status fields such as `stuck`, `position_stuck`, and
`coord_oscillating`.

Red/Blue/Yellow behavior:

- Unified mode detects `red_blue`, `yellow`, or generic profiles.
- The supervisor does not hand Gen 1 profiles to Gold-specific V1/V2/adaptive
  engines.
- Gen 1 modes currently learn safely from observations and fallback actions, but
  they do not use Gold/Silver map/story planners.

## Learning Files

All runtime learning is stored under the selected data directory. For the default
Gold service this is `/home/mojo/.pokemon-agent-gold`.

| File | Owner | Purpose |
| --- | --- | --- |
| `gold_policy.json` | V1 | Legacy action values and current V1 phase |
| `gold_world_model.json` | V1 | V1 visual/map world evidence, blocked moves, open edges, places |
| `gold_autoplayer_v2_learning.json` | V2/adaptive | Verified transition rewards, tile visits, blocked-edge stats, V1 import marker |
| `pokemon_learning_memory.json` | shared | Categorized `PKM:` facts from V1 teacher, V2/adaptive, and unified profiles |
| `gold_autoplayer.jsonl` | V1 | Legacy event/action log |
| `gold_autoplayer_v2.jsonl` | V2/adaptive | V2 turn/status event log |
| `pokemon_autoplayer.jsonl` | unified | Unified profile turn/status event log |

## Teacher Learning

V1 is treated as a teacher, not as an unchecked action oracle. Its durable world
model is imported into shared memory as facts with `source: "v1_teacher"`:

- `PKM:STUCK`: V1-observed blocked moves.
- `PKM:MAP`: V1-observed open edges and mapped places.
- `PKM:PROGRESS`: V1 policy phase/turn snapshots.

V2/adaptive can use V1-proven open edges only in fallback contexts such as
`no_route`, `no_goal`, or `blocked_edges_exhausted`. Healthy V2 route planning
still wins by default.

Unified imports the same V1 teacher facts so future Red/Blue/Yellow plugins can
share the same learning conventions without using Gold-specific actions.

## Safety Model

The bot executes short logical actions and verifies before trusting them:

```text
observe -> choose one logical action -> expand to paced button holds -> act
        -> observe again -> verify -> record progress/stuck evidence
```

Live action gates remain explicit:

- `dry_run`: decide and learn without posting inputs.
- `allow_overworld_movement`: allow walking outside battle.
- `allow_battle_actions`: allow battle/menu actions while in battle.

Fair-play constraints:

- Learn from `/state`, `/screenshot`, `/action` responses, and verified
  before/after transitions.
- Static map registries may be used as declared route-planning sources.
- Do not use save-state search, hidden future information, hidden RNG, or bundled
  ROM data to choose actions.

## Operational Checks

Useful commands:

```bash
curl http://127.0.0.1:9879/autoplayer/status
systemctl --user status pokemon-gold-server.service pokemon-gold-autoplayer.service --no-pager
python3 -m pytest -q test_gold_autoplayer_v2.py test_autoplayer_service.py test_unified_autoplayer.py
```

Key status fields:

- `control.engine`: requested engine.
- `supervisor.active_engine`: child process actually running.
- `supervisor.last_handoff`: last automatic stuck handoff.
- `v2_readiness.blockers`: why V2/adaptive is not ready for live actions.
- `status.learning.v1_teacher_import`: V1 teacher import counts.
- `status.navigation.path_source`: planner/recovery source for the latest action.
