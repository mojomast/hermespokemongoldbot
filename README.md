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
  inventory, team, battle state, and diagnostics.
- Pokemon Gold RAM reader for structured state such as map, position, party,
  bag, battle, story flags, and visual/dialogue signals.
- Gold V1/V2 autoplayer runners, supervisor service, route planner, gameplay
  policies, and regression tests.

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
- Read-only live viewer: `http://localhost:9876/dashboard/watch.html`
- Health: `http://localhost:9876/health`
- Structured state: `http://localhost:9876/state`
- Watch status: `http://localhost:9876/watch/status`
- Watch chat/viewer WebSocket: `ws://localhost:9876/watch/ws`
- WebRTC offer endpoint: `POST /rtc/offer`

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
- `gold_autoplayer_service.py`: supervisor that starts/stops the selected engine.
- `gold_autoplayer_watch.py`: status watcher for terminal monitoring.

Runtime control and status files are stored under the configured data directory,
typically `/home/mojo/.pokemon-agent-gold/`:

- `gold_autoplayer_control.json`
- `gold_autoplayer_status.json`
- `gold_autoplayer_supervisor_status.json`
- `gold_autoplayer_v2.jsonl`

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
- `POST /autoplayer/control`: enable/disable the bot and select V1/V2 settings.
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
