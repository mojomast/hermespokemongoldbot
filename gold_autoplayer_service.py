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

from pokemon_agent.autoplayer.learning import LearningFact, LearningMemory


VALID_ENGINES = {"v1", "v2", "unified", "adaptive"}
GOLD_ENGINES = {"v1", "v2", "adaptive"}
HANDOFF_COOLDOWN_SECONDS = 20.0


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
    last_handoff: dict[str, Any] | None = None

    @property
    def control_path(self) -> Path:
        return self.data_dir / "gold_autoplayer_control.json"

    @property
    def supervisor_status_path(self) -> Path:
        return self.data_dir / "gold_autoplayer_supervisor_status.json"

    @property
    def shared_memory_path(self) -> Path:
        return self.data_dir / "pokemon_learning_memory.json"

    def read_engine(self) -> str:
        return self.engine_for_control(self.read_control())

    def read_control(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.control_path.read_text())
        except Exception:
            payload = {}
        control = payload if isinstance(payload, dict) else {}
        control.setdefault("engine", "v1")
        control.setdefault("auto_handoff_enabled", True)
        control.setdefault("auto_handoff_v1_fallback", "adaptive")
        control.setdefault("auto_handoff_v2_fallback", "v1")
        return control

    def engine_for_control(self, control: dict[str, Any]) -> str:
        engine = control.get("engine") if isinstance(control, dict) else None
        return engine if engine in VALID_ENGINES else "v1"

    @property
    def runner_status_path(self) -> Path:
        return self.data_dir / "gold_autoplayer_status.json"

    def read_runner_status(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.runner_status_path.read_text())
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def write_control(self, control: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.control_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(control, indent=2, sort_keys=True))
        tmp.replace(self.control_path)

    def handoff_engine_for_status(self, engine: str, control: dict[str, Any], status: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        if control.get("auto_handoff_enabled") is not True:
            return engine, None
        now = time.time()
        handoff_state = control.get("auto_handoff") if isinstance(control.get("auto_handoff"), dict) else {}
        profile = status.get("profile") or ((status.get("runner") or {}).get("profile") if isinstance(status.get("runner"), dict) else None)
        if profile and profile not in {"gold_silver", "gold", "silver"}:
            return engine, None
        navigation = status.get("navigation") if isinstance(status.get("navigation"), dict) else {}
        actions = status.get("actions") if isinstance(status.get("actions"), list) else []
        battle_blocked = (
            status.get("phase") == "BATTLE"
            and not actions
            and isinstance(navigation.get("battle_policy"), str)
            and "blocked_missing_menu_state" in navigation.get("battle_policy")
        )
        hard_stuck = (
            navigation.get("path_source") in {"safety_circuit_breaker", "safety_button_circuit_breaker"}
            or int(navigation.get("recovery_level", 0) or 0) >= 2
            or int(navigation.get("stuck_counter", 0) or 0) >= 3
            or int(navigation.get("button_failures", 0) or 0) >= 3
            or battle_blocked
        )
        v1_stuck = bool(status.get("stuck") or status.get("position_stuck") or status.get("coord_oscillating"))
        if engine in {"v2", "adaptive", "unified"} and hard_stuck:
            target = str(control.get("auto_handoff_v2_fallback") or "v1")
            reason = f"{engine}_hard_stuck"
        elif engine == "v1" and v1_stuck:
            target = str(control.get("auto_handoff_v1_fallback") or "adaptive")
            reason = "v1_stuck"
        else:
            return engine, None
        if target not in VALID_ENGINES or target == engine:
            return engine, None
        if engine == "unified" and profile and profile != "gold_silver":
            return engine, None
        cooldown_until = handoff_state.get("cooldown_until")
        if (
            isinstance(cooldown_until, (int, float))
            and now < cooldown_until
            and handoff_state.get("from") == engine
            and handoff_state.get("to") == target
        ):
            return engine, None
        return target, {
            "from": engine,
            "to": target,
            "reason": reason,
            "path_source": navigation.get("path_source"),
            "updated_at": now,
            "cooldown_until": now + HANDOFF_COOLDOWN_SECONDS,
        }

    def command_for_engine(self, engine: str) -> list[str]:
        root = Path(__file__).resolve().parent
        if engine in {"v2", "adaptive"}:
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
            "last_handoff": self.last_handoff,
            "updated_at": time.time(),
        }
        tmp = self.supervisor_status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
        tmp.replace(self.supervisor_status_path)

    def record_handoff_learning(self, handoff: dict[str, Any]) -> None:
        try:
            memory = LearningMemory(self.shared_memory_path)
            memory.add_fact(LearningFact(
                category="PKM:POLICY",
                game_id="gold_silver",
                text=f"Auto handoff {handoff.get('from')} -> {handoff.get('to')} because {handoff.get('reason')}",
                confidence="observed",
                source="gold_autoplayer_service",
                data={"kind": "mode_handoff", **handoff, "evidence_count": 1},
            ))
            if str(handoff.get("reason") or "").endswith("hard_stuck") or handoff.get("reason") == "v1_stuck":
                memory.add_fact(LearningFact(
                    category="PKM:FAILURE",
                    game_id="gold_silver",
                    text=f"Mode {handoff.get('from')} needed rescue handoff at {handoff.get('path_source') or 'unknown context'}",
                    confidence="observed",
                    source="gold_autoplayer_service",
                    data={"kind": "mode_handoff_failure", **handoff, "evidence_count": 1},
                ))
        except Exception:
            return

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
        control = self.read_control()
        engine = self.engine_for_control(control)
        handoff_engine, handoff = self.handoff_engine_for_status(engine, control, self.read_runner_status())
        if handoff is not None:
            control["engine"] = handoff_engine
            control["auto_handoff"] = handoff
            self.write_control(control)
            self.last_handoff = handoff
            self.record_handoff_learning(handoff)
            engine = handoff_engine
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
