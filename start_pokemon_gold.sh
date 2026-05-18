#!/usr/bin/env bash
set -euo pipefail

ROM="${1:-${POKEMON_GOLD_ROM:-/home/mojo/roms/pokemon_gold.gbc}}"
PORT="${POKEMON_AGENT_PORT:-9876}"
DATA_DIR="${POKEMON_AGENT_DATA_DIR:-/home/mojo/.pokemon-agent-gold}"

if [[ ! -f "$ROM" ]]; then
  cat >&2 <<EOF
Pokemon Gold ROM not found: $ROM

I can't download or provide copyrighted Pokemon ROMs. Put your legally-owned
Pokemon Gold .gbc file at /home/mojo/roms/pokemon_gold.gbc or pass its path:

  $0 /path/to/pokemon_gold.gbc
EOF
  exit 2
fi

cd /home/mojo/pokemon-agent
source .venv/bin/activate
exec pokemon-agent serve --rom "$ROM" --port "$PORT" --data-dir "$DATA_DIR"
