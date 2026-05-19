"""Unified autoplayer runner scaffold.

Gold/Silver currently delegates to the mature Gold V2 runner. Gen 1 and generic
GB modes use a conservative learning fallback until Kanto maps/story plugins are
implemented.
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any

from .learning import LearningFact, LearningMemory, import_gold_v1_teacher_snapshot
from .profiles import GameProfile, profile_for_game_type


EXPLORE_ACTIONS = ("walk_up", "walk_right", "walk_down", "walk_left")


class UniversalAutoplayer:
    def __init__(self, base_url: str, data_dir: Path):
        self.base_url = base_url.rstrip("/")
        self.data_dir = Path(data_dir)
        self.memory = LearningMemory(self.data_dir / "pokemon_learning_memory.json")
        self.teacher_import = import_gold_v1_teacher_snapshot(self.data_dir, self.memory, force=True)
        self.turn = 0

    @property
    def control_path(self) -> Path:
        return self.data_dir / "gold_autoplayer_control.json"

    @property
    def status_path(self) -> Path:
        return self.data_dir / "gold_autoplayer_status.json"

    @property
    def event_log_path(self) -> Path:
        return self.data_dir / "pokemon_autoplayer.jsonl"

    def read_control(self) -> dict[str, Any]:
        defaults = {
            "enabled": True,
            "engine": "unified",
            "objective": "learn to make progress fairly from current observations",
            "dry_run": True,
            "allow_overworld_movement": False,
            "allow_battle_actions": False,
        }
        try:
            payload = json.loads(self.control_path.read_text(encoding="utf-8"))
        except Exception:
            return defaults
        if isinstance(payload, dict):
            defaults.update(payload)
        return defaults

    def write_status(self, status: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(status, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.status_path)

    def log_event(self, event: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = dict(event)
        payload.setdefault("engine", "unified")
        payload.setdefault("turn", self.turn)
        payload.setdefault("updated_at", time.time())
        with self.event_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def request_json(self, path: str, timeout: float = 5.0) -> dict[str, Any]:
        with urllib.request.urlopen(f"{self.base_url}{path}", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_actions(self, actions: list[str], timeout: float = 10.0) -> dict[str, Any]:
        payload = json.dumps({"actions": actions}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/action",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def detect_profile(self, state: dict[str, Any]) -> GameProfile:
        game = str(((state.get("metadata") or {}).get("game") or "")).lower()
        if "gold" in game or "silver" in game:
            return profile_for_game_type("gold")
        if "yellow" in game:
            return profile_for_game_type("yellow")
        if "red" in game or "blue" in game:
            return profile_for_game_type("red")
        return profile_for_game_type(None)

    def choose_fallback_actions(self, state: dict[str, Any], profile: GameProfile) -> tuple[list[str], str]:
        battle = state.get("battle") or {}
        dialog = state.get("dialog") or {}
        visual = state.get("visual") or {}
        if battle.get("in_battle") is True:
            return ["press_a", "wait_30"], "battle_fallback_press_a"
        if dialog.get("active") is True or visual.get("visual_textbox_active") is True or visual.get("screen_class") == "dialogue":
            return ["press_a", "wait_30"], "dialogue_fallback_press_a"

        action = EXPLORE_ACTIONS[self.turn % len(EXPLORE_ACTIONS)]
        direction = action.removeprefix("walk_")
        return [f"hold_{direction}_48", "wait_12"], f"{profile.game_id}_explore_{action}"

    def record_turn(
        self,
        profile: GameProfile,
        before: dict[str, Any],
        after: dict[str, Any],
        reason: str,
        actions: list[str],
    ) -> None:
        before_pos = ((before.get("player") or {}).get("position") or {})
        after_pos = ((after.get("player") or {}).get("position") or {})
        if before_pos != after_pos:
            self.memory.add_fact(LearningFact(
                category="PKM:MAP",
                game_id=profile.game_id,
                text=f"{reason}: moved from {before_pos} to {after_pos}",
                data={"actions": actions, "before": before_pos, "after": after_pos},
            ))
        elif actions and actions[0].startswith("hold_"):
            self.memory.add_fact(LearningFact(
                category="PKM:STUCK",
                game_id=profile.game_id,
                text=f"{reason}: no position change from {before_pos}",
                confidence="suspected",
                data={"actions": actions, "position": before_pos},
            ))

    def run_once(self) -> dict[str, Any]:
        control = self.read_control()
        if control.get("engine") != "unified":
            status = {
                "schema_version": 2,
                "engine": "unified",
                "selected_engine": control.get("engine"),
                "enabled": False,
                "phase": "IDLE",
                "turn": self.turn,
                "message": "Unified learner idle while another bot engine is selected.",
                "updated_at": time.time(),
            }
            self.write_status(status)
            self.log_event({"event": "idle", "selected_engine": control.get("engine")})
            self.turn += 1
            return status
        state = self.request_json("/state")
        profile = self.detect_profile(state)
        if profile.game_id == "gold_silver":
            return {"delegate": "gold_v2", "profile": profile.game_id}

        actions, reason = self.choose_fallback_actions(state, profile)
        posted = False
        if control.get("enabled") is not False and control.get("dry_run") is False:
            in_battle = (state.get("battle") or {}).get("in_battle") is True
            can_move = control.get("allow_battle_actions") is True if in_battle else control.get("allow_overworld_movement") is True
            if can_move:
                result = self.post_actions(actions)
                posted = result.get("success") is not False
        after_state = self.request_json("/state")
        self.record_turn(profile, state, after_state, reason, actions)
        self.turn += 1
        status = {
            "schema_version": 2,
            "engine": "unified",
            "enabled": control.get("enabled") is not False,
            "phase": "LEARNING_FALLBACK",
            "turn": self.turn,
            "profile": profile.game_id,
            "actions": actions,
            "reason": reason,
            "posted": posted,
            "runner": {
                "engine": "unified",
                "mode": "active",
                "event_log": str(self.event_log_path),
                "learning_file": str(self.memory.path),
                "teacher_import": self.teacher_import,
                "dry_run": control.get("dry_run") is not False,
                "allow_overworld_movement": control.get("allow_overworld_movement") is True,
                "allow_battle_actions": control.get("allow_battle_actions") is True,
            },
            "memory": self.memory.summary(game_id=profile.game_id),
            "updated_at": time.time(),
        }
        self.write_status(status)
        self.log_event({"event": "turn", "status": status})
        return status

    def run_forever(self, sleep_seconds: float = 0.25) -> None:
        while True:
            self.run_once()
            time.sleep(sleep_seconds)


def run_unified_autoplayer(base_url: str, data_dir: Path) -> None:
    state = UniversalAutoplayer(base_url, data_dir).request_json("/state")
    profile = UniversalAutoplayer(base_url, data_dir).detect_profile(state)
    if profile.game_id == "gold_silver":
        from gold_autoplayer_v2 import GoldAutoplayerV2

        GoldAutoplayerV2(data_dir=data_dir, base_url=base_url).run_forever()
        return
    UniversalAutoplayer(base_url, data_dir).run_forever()
