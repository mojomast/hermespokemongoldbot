#!/usr/bin/env bash
set -euo pipefail
PORT="${POKEMON_AGENT_PORT:-9876}"
LOG="${POKEMON_TUNNEL_LOG:-/tmp/pokemon-localhost-run.log}"
rm -f "$LOG"
exec ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -R 80:127.0.0.1:${PORT} nokey@localhost.run 2>&1 | tee "$LOG"
