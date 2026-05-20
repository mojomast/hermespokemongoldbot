"""Save-only milestone snapshot policy for Pokemon autoplayers.

The policy deliberately creates save states only. It never loads them, and it
never overwrites an existing save-state file. Callers can run it in dry-run mode
for diagnostics before allowing POST /save side effects.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class SaveStateRequest:
    """A proposed milestone save-state."""

    milestone_key: str
    label: str
    reason: str
    priority: int = 100

    def save_name(self, state: JsonDict | None = None, *, now: float | None = None) -> str:
        """Return a stable, descriptive, server-safe save name <= 64 chars."""
        timestamp = datetime.fromtimestamp(now or time.time(), tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        frame = _frame_count(state)
        suffix = f"_f{frame}" if frame is not None else ""
        raw = f"{self.milestone_key}{suffix}_{timestamp}"
        return _safe_save_name(raw)


def _safe_save_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip()).strip("._")
    return (cleaned or "pokemon_milestone")[:64]


def _frame_count(state: JsonDict | None) -> int | None:
    metadata = (state or {}).get("metadata") or {}
    frame = metadata.get("frame_count")
    return frame if isinstance(frame, int) else None


def _nested_bool(mapping: JsonDict, *path: str) -> bool:
    current: Any = mapping
    for key in path:
        if not isinstance(current, dict):
            return False
        current = current.get(key)
    return current is True


def _map(state: JsonDict) -> JsonDict:
    return state.get("map") or ((state.get("player") or {}).get("position") or {}) or {}


def _position(state: JsonDict) -> JsonDict:
    return ((state.get("player") or {}).get("position") or {}) or {}


def _flags(state: JsonDict) -> JsonDict:
    return state.get("flags") or {}


def _story(state: JsonDict) -> JsonDict:
    flags = _flags(state)
    story = flags.get("derived_story_flags") if isinstance(flags.get("derived_story_flags"), dict) else None
    if story is None:
        story = flags.get("story_event_flags") if isinstance(flags.get("story_event_flags"), dict) else {}
    return story or {}


def _party_count(state: JsonDict) -> int:
    flags = _flags(state)
    raw = flags.get("party_count")
    if isinstance(raw, int):
        return raw
    party = state.get("party")
    return len(party) if isinstance(party, list) else 0


def _has_starter(state: JsonDict) -> bool:
    flags = _flags(state)
    story = _story(state)
    return bool(flags.get("has_starter") is True or story.get("has_starter") is True or _party_count(state) >= 1)


def _trusted_overworld(state: JsonDict) -> bool:
    pos = _position(state)
    map_info = _map(state)
    battle = state.get("battle") or {}
    return (
        bool(pos.get("trusted") is True or map_info.get("trusted") is True)
        and bool(pos.get("in_bounds") is not False and map_info.get("position_in_bounds") is not False)
        and battle.get("in_battle") is not True
    )


def _dialog_active(state: JsonDict) -> bool:
    dialog = state.get("dialog") or {}
    return bool(dialog.get("active") is True or dialog.get("visual_active") is True)


def _map_key(state: JsonDict) -> tuple[int | None, int | None]:
    map_info = _map(state)
    return map_info.get("map_group"), map_info.get("map_number")


def _ball_count(state: JsonDict) -> int:
    total = 0
    for item in state.get("bag") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("item") or "").lower()
        item_id = item.get("item_id", item.get("id"))
        qty = item.get("quantity", item.get("count", 0))
        try:
            qty_int = int(qty)
        except Exception:
            qty_int = 0
        if "ball" in name or item_id in {4, 157, 158, 159, 160, 161, 164, 165}:
            total += max(qty_int, 0)
    return total


def _lead_level(state: JsonDict) -> int:
    party = state.get("party") or []
    if party and isinstance(party[0], dict) and isinstance(party[0].get("level"), int):
        return int(party[0]["level"])
    return 0


def _backup_levels(state: JsonDict) -> list[int]:
    levels: list[int] = []
    for mon in (state.get("party") or [])[1:]:
        if isinstance(mon, dict) and isinstance(mon.get("level"), int):
            levels.append(int(mon["level"]))
    return levels


def _lead_hp_ratio(state: JsonDict) -> float:
    party = state.get("party") or []
    if not party or not isinstance(party[0], dict):
        return 0.0
    hp = party[0].get("hp")
    max_hp = party[0].get("max_hp")
    if not isinstance(hp, (int, float)) or not isinstance(max_hp, (int, float)) or max_hp <= 0:
        return 0.0
    return float(hp) / float(max_hp)


def evaluate_milestone_requests(state: JsonDict | None, status: JsonDict | None = None) -> list[SaveStateRequest]:
    """Return milestone save requests justified by the current state/status."""
    if not isinstance(state, dict) or not _trusted_overworld(state):
        return []

    requests: list[SaveStateRequest] = []
    party_count = _party_count(state)
    has_starter = _has_starter(state)
    story = _story(state)
    map_group, map_number = _map_key(state)
    map_name = str(_map(state).get("map_name") or "")
    dialog = _dialog_active(state)

    if party_count == 0 and not has_starter:
        if dialog:
            requests.append(SaveStateRequest("gold_000_intro_trusted_dialogue", "Intro/dialogue trusted", "trusted map/position before starter while dialogue is active", 10))
        else:
            requests.append(SaveStateRequest("gold_010_intro_cleared_overworld", "Intro cleared", "trusted overworld before starter and no dialogue/menu/battle", 20))
        if (map_group, map_number) == (24, 5) or "Elm" in map_name:
            requests.append(SaveStateRequest("gold_020_before_starter_choice", "Before starter choice", "in Elm's Lab with no starter and trusted state", 30))

    if has_starter or party_count >= 1:
        requests.append(SaveStateRequest("gold_030_starter_acquired", "Starter acquired", "party_count >= 1 or has_starter flag is true", 40))

    if story.get("got_mystery_egg_from_mr_pokemon") is True:
        requests.append(SaveStateRequest("gold_040_mystery_egg_acquired", "Mystery Egg acquired", "story flag got_mystery_egg_from_mr_pokemon is true", 50))

    if story.get("gave_mystery_egg_to_elm") is True:
        requests.append(SaveStateRequest("gold_050_egg_returned_to_elm", "Mystery Egg returned", "story flag gave_mystery_egg_to_elm is true", 60))

    if story.get("learned_to_catch_pokemon") is True:
        requests.append(SaveStateRequest("gold_060_catching_tutorial_complete", "Catching tutorial complete", "story flag learned_to_catch_pokemon is true", 70))
        if _ball_count(state) > 0:
            requests.append(SaveStateRequest("gold_070_pokeballs_acquired", "Poké Balls acquired", "catching tutorial is complete and bag contains balls", 80))

    if party_count >= 2:
        requests.append(SaveStateRequest("gold_080_first_catch", "First catch", "party_count >= 2", 90))

    if party_count >= 4:
        requests.append(SaveStateRequest("gold_090_roster_four_plus", "Roster four plus", "party_count >= 4", 100))
        backups = _backup_levels(state)
        if _lead_level(state) >= 12 and len(backups) >= 3 and min(backups[:3]) >= 8 and _lead_hp_ratio(state) >= 0.65:
            requests.append(SaveStateRequest("gold_100_falkner_ready", "Falkner ready", "party/readiness gates pass for first gym attempt", 110))

    emergency = _emergency_request(state, status)
    if emergency is not None:
        requests.append(emergency)

    return sorted(requests, key=lambda request: request.priority)


def _emergency_request(state: JsonDict, status: JsonDict | None) -> SaveStateRequest | None:
    if not isinstance(status, dict) or not _trusted_overworld(state):
        return None
    navigation = status.get("navigation") if isinstance(status.get("navigation"), dict) else {}
    recent = navigation.get("recent_failures") if isinstance(navigation.get("recent_failures"), list) else []
    stuck_counter = navigation.get("stuck_counter") if isinstance(navigation.get("stuck_counter"), int) else 0
    recovery_level = navigation.get("recovery_level") if isinstance(navigation.get("recovery_level"), int) else 0
    same_press_a = navigation.get("same_state_press_a_count") if isinstance(navigation.get("same_state_press_a_count"), int) else 0
    repeated_failures = len(recent) >= 5 or stuck_counter >= 2 or recovery_level >= 2 or same_press_a >= 4
    if not repeated_failures:
        return None
    frame = _frame_count(state) or int(time.time())
    bucket = frame // (60 * 60 * 5)  # roughly five minutes at 60fps; prevents unbounded emergency spam.
    return SaveStateRequest(
        f"gold_emergency_before_recovery_{bucket}",
        "Emergency before recovery",
        "trusted state with repeated failures/stuck signs before recovery escalation",
        1000,
    )


class MilestoneSaveStateManager:
    """Create save-only milestone snapshots through the Pokemon HTTP API."""

    def __init__(self, data_dir: Path, base_url: str, *, dry_run: bool = False, clock: Callable[[], float] | None = None):
        self.data_dir = Path(data_dir).expanduser()
        self.base_url = base_url.rstrip("/")
        self.dry_run = dry_run
        self.clock = clock or time.time
        self.ledger_path = self.data_dir / "save_state_milestones.json"
        self.ledger = self._load_ledger()

    def _load_ledger(self) -> JsonDict:
        try:
            payload = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("schema_version", 1)
        payload.setdefault("safety", {"mode": "save_only", "load_policy": "manual_only", "allow_overwrite": False})
        payload.setdefault("created", {})
        payload.setdefault("dry_runs", [])
        return payload

    def _persist_ledger(self) -> None:
        if self.dry_run:
            return
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.ledger_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.ledger, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.ledger_path)

    def evaluate(self, state: JsonDict | None, status: JsonDict | None = None) -> list[SaveStateRequest]:
        requests = evaluate_milestone_requests(state, status)
        created = self.ledger.get("created") if isinstance(self.ledger.get("created"), dict) else {}
        pending = [request for request in requests if request.milestone_key not in created]
        # If the policy is introduced mid-run, do not backfill every earlier
        # milestone using the current location. Save the most advanced current
        # story milestone plus a separately-bucketed emergency snapshot.
        normal = [request for request in pending if not request.milestone_key.startswith("gold_emergency_")]
        emergency = [request for request in pending if request.milestone_key.startswith("gold_emergency_")]
        selected: list[SaveStateRequest] = []
        if normal:
            selected.append(max(normal, key=lambda request: request.priority))
        selected.extend(emergency[:1])
        return sorted(selected, key=lambda request: request.priority)

    def maybe_save(self, state: JsonDict | None, status: JsonDict | None = None, *, max_saves: int = 1) -> JsonDict:
        """Evaluate and create up to max_saves pending snapshots.

        Returns a status payload suitable for embedding in the autoplayer status.
        """
        requests = self.evaluate(state, status)
        result: JsonDict = {
            "enabled": True,
            "dry_run": self.dry_run,
            "safety": "save_only",
            "load_policy": "manual_only",
            "pending": [request.__dict__ for request in requests],
            "created": [],
            "skipped": [],
            "ledger_path": str(self.ledger_path),
        }
        if not requests:
            return result
        now = self.clock()
        for request in requests[:max_saves]:
            save_name = request.save_name(state, now=now)
            record = {
                "milestone_key": request.milestone_key,
                "label": request.label,
                "reason": request.reason,
                "save_name": save_name,
                "frame": _frame_count(state),
                "created_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
                "safety": "save_only",
                "load_policy": "manual_only",
            }
            if self.dry_run:
                result["created"].append({**record, "dry_run": True})
                continue
            if self._save_exists(save_name):
                result["skipped"].append({**record, "reason": "save_already_exists"})
                continue
            response = self._post_save(save_name)
            record["path"] = response.get("path")
            record["server_name"] = response.get("name")
            self.ledger.setdefault("created", {})[request.milestone_key] = record
            result["created"].append(record)
        self.ledger["updated_at"] = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
        self._persist_ledger()
        return result

    def _save_exists(self, save_name: str) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base_url}/saves", timeout=5.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            return False
        for save in payload.get("saves") or []:
            if isinstance(save, dict) and save.get("name") == save_name:
                return True
        return False

    def _post_save(self, save_name: str) -> JsonDict:
        payload = json.dumps({"name": save_name}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/save",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20.0) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not isinstance(result, dict) or result.get("success") is not True:
            raise RuntimeError(f"save request failed: {result!r}")
        return result
