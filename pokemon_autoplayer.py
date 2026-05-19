#!/usr/bin/env python3
"""Unified Pokemon autoplayer entrypoint."""

from __future__ import annotations

import argparse
from pathlib import Path

from pokemon_agent.autoplayer.runner import run_unified_autoplayer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:9879")
    parser.add_argument("--data-dir", default="/home/mojo/.pokemon-agent")
    args = parser.parse_args()
    run_unified_autoplayer(args.base_url, Path(args.data_dir).expanduser())


if __name__ == "__main__":
    main()
