#!/usr/bin/env python3
"""Single-child supervisor for Pokemon Gold autoplayer engines."""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


VALID_ENGINES = {"v1", "v2", "unified"}


class ChildProcess(Protocol):
    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


ProcessFactory = Callable[[Sequence[str]], ChildProcess]


def default_process_factory(command: Sequence[str]) -> ChildProcess:
    return subprocess.Popen(list(command))


@dataclass
class AutoplayerSupervisor:
    base_url: str
    data_dir: Path
    delay: float = 1.25
    python: str = sys.executable
    process_factory: ProcessFactory = default_process_factory

    active_engine: str | None = None
    child: ChildProcess | None = None
    stopping: bool = False
    restart_count: int = 0
    last_exit_code: int | None = None
    last_start_error: str | None = None
    next_restart_at: float = 0.0
    restart_delay_seconds: float = 0.0
    min_restart_delay: float = 1.0
    max_restart_delay: float = 30.0

    @property
    def control_path(self) -> Path:
        return self.data_dir / "gold_autoplayer_control.json"

    @property
    def supervisor_status_path(self) -> Path:
        return self.data_dir / "gold_autoplayer_supervisor_status.json"

    def read_engine(self) -> str:
        try:
            payload = json.loads(self.control_path.read_text())
        except Exception:
            return "v1"
        engine = payload.get("engine") if isinstance(payload, dict) else None
        return engine if engine in VALID_ENGINES else "v1"

    def command_for_engine(self, engine: str) -> list[str]:
        root = Path(__file__).resolve().parent
        if engine == "v2":
            return [
                self.python,
                str(root / "gold_autoplayer_v2.py"),
                "--base-url",
                self.base_url,
                "--data-dir",
                str(self.data_dir),
            ]
        if engine == "unified":
            return [
                self.python,
                str(root / "pokemon_autoplayer.py"),
                "--base-url",
                self.base_url,
                "--data-dir",
                str(self.data_dir),
            ]
        return [
            self.python,
            str(root / "gold_autoplayer.py"),
            "--base-url",
            self.base_url,
            "--data-dir",
            str(self.data_dir),
            "--delay",
            str(self.delay),
        ]

    def start_engine(self, engine: str) -> None:
        try:
            self.child = self.process_factory(self.command_for_engine(engine))
        except Exception as exc:  # noqa: BLE001
            self.child = None
            self.active_engine = None
            self.last_start_error = f"{type(exc).__name__}: {exc}"
            self.schedule_restart()
            self.write_status(engine)
            return
        self.active_engine = engine
        self.last_start_error = None
        self.last_exit_code = None
        self.write_status(engine)

    def schedule_restart(self) -> None:
        self.restart_count += 1
        self.restart_delay_seconds = self.min_restart_delay if self.restart_delay_seconds <= 0 else min(self.restart_delay_seconds * 2, self.max_restart_delay)
        self.next_restart_at = time.time() + self.restart_delay_seconds

    def reset_backoff(self) -> None:
        self.restart_delay_seconds = 0.0
        self.next_restart_at = 0.0

    def write_status(self, desired_engine: str | None = None) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "supervisor": "gold_autoplayer_service",
            "active_engine": self.active_engine,
            "desired_engine": desired_engine,
            "child_running": self.child is not None and self.child.poll() is None,
            "child_exit_code": self.last_exit_code,
            "restart_count": self.restart_count,
            "restart_delay_seconds": self.restart_delay_seconds,
            "next_restart_at": self.next_restart_at,
            "last_start_error": self.last_start_error,
            "updated_at": time.time(),
        }
        tmp = self.supervisor_status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
        tmp.replace(self.supervisor_status_path)

    def stop_child(self, timeout: float = 5.0) -> None:
        child = self.child
        if child is None:
            self.active_engine = None
            return
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=timeout)
            except TimeoutError:
                child.kill()
                child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=timeout)
        self.child = None
        self.active_engine = None

    def reconcile_once(self) -> None:
        engine = self.read_engine()
        if self.child is not None and self.child.poll() is not None:
            self.last_exit_code = self.child.poll()
            self.child = None
            self.active_engine = None
            self.schedule_restart()
            self.write_status(engine)
        if self.child is None and self.next_restart_at and time.time() < self.next_restart_at:
            self.write_status(engine)
            return
        if self.active_engine == engine and self.child is not None:
            self.write_status(engine)
            return
        if self.child is not None:
            self.stop_child()
            self.reset_backoff()
        self.start_engine(engine)

    def request_stop(self, signum: int, _frame: Any) -> None:
        self.stopping = True
        desired_engine = self.read_engine()
        self.stop_child()
        self.write_status(desired_engine)

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        while not self.stopping:
            self.reconcile_once()
            time.sleep(1.0)
        self.stop_child()
        self.write_status(self.read_engine())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:9879")
    parser.add_argument("--data-dir", default="~/.pokemon-agent-gold")
    parser.add_argument("--delay", type=float, default=1.25)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    AutoplayerSupervisor(
        base_url=args.base_url,
        data_dir=Path(args.data_dir).expanduser(),
        delay=args.delay,
        python=args.python,
    ).run_forever()


if __name__ == "__main__":
    main()
