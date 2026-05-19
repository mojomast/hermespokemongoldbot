# Hermes Pokemon Gold Bot

Hermes Pokemon Gold Bot is a Pokemon Gold autonomous gameplay stack built from
the `pokemon-agent` emulator/API project and evolved through Hermes Agent's
`pokemon-player` skill workflow. It runs Pokemon Gold headlessly with PyBoy,
serves game state and controls over HTTP, exposes a live dashboard/watch page,
and includes Gold-specific memory reading, navigation, battle/gameplay policy,
and V1/V2 autoplayer experiments.

This repository is focused on the Gold bot work published at
`github.com/mojomast/hermespokemongoldbot`. It is not a ROM distribution.

## Features

- Headless Game Boy Color emulation through PyBoy.
- FastAPI control server with state, screenshot, action, save/load, run, and bot
  control endpoints.
- Smooth WebRTC live stream plus screenshot fallback.
- Read-only public watch page with live viewer count, chat, bot telemetry, and a
  Game Boy Color-styled broadcast UI.
- Full local control dashboard for manual controls, bot guidance, saves, runs,
  inventory, team, battle state, diagnostics, and an AI Decision Inspector directly
  below the video output.
- Hermes Games tab metadata for opening upload, watch, and control pages either
  embedded or in separate browser tabs.
- Pokemon Gold RAM reader for structured state such as map, position, party,
  bag, battle, story flags, and visual/dialogue signals.
- Gold V1/V2 autoplayer runners, supervisor service, route planner, gameplay
  policies, and regression tests.
- Unified learning mode that can delegate Gold/Silver to V2 and use conservative
  fallback learning for other compatible Game Boy Pokemon ROMs.
- Automatic stuck handoff between V1, V2, and adaptive modes for Gold/Silver, with
  safeguards so Red/Blue/Yellow stay in their own unified profile mode.

## Documentation

- [Autoplayer Architecture](docs/AUTOPLAYER_ARCHITECTURE.md): services, engine
  modes, learning files, V1 teacher import, automatic handoff, and operations.
- [Cross-Game Modes](docs/CROSS_GAME_MODES.md): how Gold/Silver, Red/Blue,
  Yellow, and generic GB profiles are handled safely.
- [Unified Autoplayer Spec](UNIFIED_AUTOPLAYER_SPEC.md): long-term shared runner
  design and fair-play learning rules.
- [Navigation V2 Spec](NAVIGATION_V2_SPEC.md): Gold/Silver route-planning design.
- [Gameplay V2 Spec](GAMEPLAY_V2_SPEC.md): Gold/Silver gameplay policy design.

## Requirements

- Python 3.10+
- PyBoy-compatible Pokemon Gold `.gbc` ROM that you legally own
- Project dependencies from `pyproject.toml`

## Setup

```bash
git clone https://github.com/mojomast/hermespokemongoldbot.git
cd hermespokemongoldbot
python -m venv .venv
source .venv/bin/activate
pip install -e ".[pyboy,dashboard,dev]"
```

## Run Pokemon Gold

If you need a browser page for a user to upload their own ROM first, start
onboarding mode:

```bash
pokemon-agent onboard --port 9876 --data-dir /home/mojo/.pokemon-agent-gold
```

Then open `http://localhost:9876/dashboard/onboarding.html`. The page stores
uploaded `.gb`, `.gbc`, and `.gba` files under the configured data directory and
prints the exact launch command for the selected ROM.

```bash
./start_pokemon_gold.sh /path/to/pokemon_gold.gbc
```

Defaults used by the helper script:

- ROM: `/home/mojo/roms/pokemon_gold.gbc`
- Port: `9876` unless `POKEMON_AGENT_PORT` is set
- Data dir: `/home/mojo/.pokemon-agent-gold` unless `POKEMON_AGENT_DATA_DIR` is set

You can also run the server directly:

```bash
pokemon-agent serve \
  --rom /path/to/pokemon_gold.gbc \
  --port 9876 \
  --data-dir /home/mojo/.pokemon-agent-gold
```

## Watch And Control

Local endpoints after the server starts:

- Full control dashboard: `http://localhost:9876/dashboard/`
- ROM upload/onboarding: `http://localhost:9876/dashboard/onboarding.html`
- Read-only live viewer: `http://localhost:9876/dashboard/watch.html`
- Health: `http://localhost:9876/health`
- Structured state: `http://localhost:9876/state`
- Watch status: `http://localhost:9876/watch/status`
- Watch chat/viewer WebSocket: `ws://localhost:9876/watch/ws`
- WebRTC offer endpoint: `POST /rtc/offer`

When launched from Hermes Dashboard's Games tab, the Pokémon card exposes both
embedded buttons and separate-tab links for Upload, Watch, and Control pages.

For a temporary public read-only viewer tunnel:

```bash
POKEMON_AGENT_PORT=9876 ./start_pokemon_tunnel.sh
```

The tunnel helper uses `localhost.run`, writes its log to
`/tmp/pokemon-localhost-run.log` by default, and prints a temporary `*.lhr.life`
URL. Share the URL with `/dashboard/watch.html` appended. Do not share
`/dashboard/` publicly unless you intentionally want to expose controls.

## Autoplayer

The Gold bot has multiple cooperating runners:

- `gold_autoplayer.py`: V1 visual/RAM hybrid controller.
- `gold_autoplayer_v2.py`: V2 route-planning and gameplay-policy runner.
- `pokemon_autoplayer.py`: unified learning runner for cross-game profiles.
- `gold_autoplayer_service.py`: supervisor that starts/stops the selected engine.
- `gold_autoplayer_watch.py`: status watcher for terminal monitoring.

Dashboard modes:

- `Legacy V1`: older visual/RAM hybrid. Useful for title screens, menus, and
  unusual states where V2 is too conservative.
- `Navigation V2`: verified action loop with imported Gold/Silver map registry,
  route planning, story targets, blocked-edge recovery, and live-action gates.
- `Unified Learning`: neutral profile runner. For Gold/Silver it reuses V2's
  verified planner; for other ROMs it records learning facts while using a
  conservative fallback policy until their map/story plugins mature.
- `Adaptive Auto`: Gold/Silver meta-mode that uses V2 pathfinding as the optimal
  default, temporarily cascades into small recovery policies when V2 hits a
  safety circuit or detected two-state loop, and returns to V2 after verified
  progress.

The supervisor also supports automatic hard-stuck handoff:

- Gold/Silver `v2`, `adaptive`, or Gold `unified` hard-stuck -> `v1`.
- Gold/Silver `v1` stuck/oscillating -> `adaptive`.
- A cooldown prevents rapid ping-pong between engines.
- Red/Blue/Yellow/generic unified profiles do not hand off into Gold-specific
  engines.

Handoff controls are stored in `gold_autoplayer_control.json` and can be updated
through `POST /autoplayer/control`:

```json
{
  "auto_handoff_enabled": true,
  "auto_handoff_v1_fallback": "adaptive",
  "auto_handoff_v2_fallback": "v1"
}
```

Safety gates are explicit in the dashboard and API:

- `dry_run`: compute actions and learn from observations without posting input.
- `allow_overworld_movement`: permits walking outside battles.
- `allow_battle_actions`: permits battle/menu actions while in battle.

Use `/autoplayer/status` to compare the selected engine, supervisor active
engine, readiness blockers, V2 pathfinding diagnostics, and learning telemetry.
The control dashboard renders the same status as a decision inspector under the
video: mode switches and handoff reasons, current intent, selected policy,
resource/readiness blockers, policy candidates, and recent learning/failure facts.

Runtime control and status files are stored under the configured data directory,
typically `/home/mojo/.pokemon-agent-gold/`:

- `gold_autoplayer_control.json`
- `gold_autoplayer_status.json`
- `gold_autoplayer_supervisor_status.json`
- `gold_autoplayer_v2.jsonl`
- `gold_autoplayer_v2_learning.json`
- `pokemon_learning_memory.json`

See [Autoplayer Architecture](docs/AUTOPLAYER_ARCHITECTURE.md) for the full mode
selection, status, handoff, and learning-file flow.

## Fair-Play Learning

The bot is designed to learn from the same observations it can legitimately see:
`/state`, `/screenshot`, action results, and verified transitions. V2 may use the
public/static map registry for pathfinding, but learned facts remain tagged by
source and confidence and are stored under the ROM/profile data directory.

Current learning techniques are intentionally pragmatic and transparent:

- V1 persists bandit-like visual/action values and a world model.
- V2 persists transition rewards, action statistics, tile visits, and blocked-edge
  evidence in `gold_autoplayer_v2_learning.json`.
- Unified mode persists categorized `PKM:` facts in `pokemon_learning_memory.json`.
- V2/adaptive also writes verified progress and stuck facts into
  `pokemon_learning_memory.json` so other modes can reuse the same evidence.
- V2/adaptive and the supervisor also write policy, resource, failure, and outcome
  facts such as mode handoffs, no-balls/no-money readiness gates, Falkner prep
  blockers, action rewards, and control/readiness failures.
- V1 teacher evidence is imported into `pokemon_learning_memory.json` with
  `source: "v1_teacher"`; V2/adaptive only use those facts in fallback contexts,
  not as an unchecked override of healthy route planning.
- Unified mode imports the same teacher facts while keeping Red/Blue/Yellow facts
  scoped by their own `game_id`.
- Learned values are used for diagnostics and future tie-breaking; they do not
  override V2's verified planner unless explicitly implemented and tested.
- Live state always overrides stale memory. Learned resource and failure facts are
  reused as explanations, readiness gates, and fallback/tie-breaker evidence, not
  as blind walkthrough commands.

Do not use save-state search, future hidden information, hidden RNG reads, or ROM
data not surfaced through the server state/vision APIs to pick actions. User
objective/guidance in the dashboard should override learned preferences.

## API Quick Reference

- `GET /health`: server health and emulator readiness.
- `GET /state`: structured game state.
- `GET /screenshot`: current frame as PNG.
- `POST /action`: execute actions such as `press_a`, `walk_right`,
  `hold_right_48`, or `wait_60`.
- `POST /save`, `POST /load`, `GET /saves`: raw emulator save states.
- `GET /runs`, `POST /runs/save`, `POST /runs/load`, `POST /runs/new`: named run
  snapshots.
- `GET /autoplayer/status`: bot control/status/telemetry payload.
- `POST /autoplayer/control`: enable/disable the bot, select V1/V2/unified mode,
  set objective/guidance, update live-action safety gates, and configure automatic
  handoff.
- `GET /rtc/debug/perf`: WebRTC media pump diagnostics.
- `GET /watch/status`, `WS /watch/ws`: viewer count and shared watch chat.

## Tests

```bash
python -m pytest
```

Useful targeted runs:

```bash
python -m pytest test_gold_memory.py test_navigation_v2.py test_gameplay_v2.py
python -m pytest test_gold_autoplayer_v2.py
python -m py_compile gold_autoplayer_v2.py pokemon_agent/server.py
```

## ROM And Trademark Disclaimer

This repository does not include, download, distribute, or provide Pokemon ROMs,
BIOS files, save files containing proprietary game data, or other copyrighted
game assets. You must supply your own legally obtained Pokemon Gold ROM.

Pokemon, Pokemon Gold, Game Boy, and related names/assets are owned by Nintendo,
Game Freak, Creatures, and/or The Pokemon Company. This project is an independent
automation/emulation tool and is not affiliated with or endorsed by those
companies.

## Attribution And Credits

This project stands on several layers of prior work:

- [NousResearch/pokemon-agent](https://github.com/NousResearch/pokemon-agent):
  the upstream emulator/API/dashboard project this repository is based on. The
  original project is MIT licensed, Copyright (c) 2026 Nous Research. The MIT
  license notice is preserved in `LICENSE`.
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) and its built-in
  `pokemon-player` skill: the agent workflow, dashboard integration, memory
  conventions, operational playbook, and starting point that this Gold bot grew
  out of.
- [PyBoy](https://github.com/Baekalfen/PyBoy): the Game Boy/Game Boy Color
  emulator used by the server.
- [FastAPI](https://fastapi.tiangolo.com/) and Uvicorn: the HTTP/WebSocket server
  foundation.
- [pret/pokecrystal](https://github.com/pret/pokecrystal) and public Pokemon
  Gold/Silver RAM/map research: references used for Gold memory/navigation work.
- [pret/pokered](https://github.com/pret/pokered) and
  [pret/pokefirered](https://github.com/pret/pokefirered): upstream references
  credited by the original `pokemon-agent` project.
- [gpt-play-pokemon-firered](https://github.com/Clad3815/gpt-play-pokemon-firered):
  architecture inspiration credited by the original `pokemon-agent` project.

## License

MIT. See [LICENSE](LICENSE).
