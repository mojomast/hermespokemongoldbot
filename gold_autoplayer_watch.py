#!/usr/bin/env python3
"""Read-only progress/stuckness watcher for the Gold autoplayer."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from collections import Counter, deque
from pathlib import Path
from typing import Any


def get_json(url: str, timeout: int = 5) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read())


def coord_from_state(state: dict[str, Any]) -> str | None:
    pos = ((state.get("player") or {}).get("position") or {})
    map_id = pos.get("map_id")
    x = pos.get("x")
    y = pos.get("y")
    if map_id is None or x is None or y is None:
        return None
    return f"{map_id}:{x}:{y}"


def coord_tuple(coord: str | None) -> tuple[int, int, int] | None:
    if not coord:
        return None
    parts = coord.split(":")
    if len(parts) != 3:
        return None
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None


def map_label(state: dict[str, Any]) -> str:
    pos = ((state.get("player") or {}).get("position") or {})
    return f"{pos.get('map_name')} ({pos.get('map_group')},{pos.get('map_number')})"


def tail_jsonl(path: str | None, since_ts: float, limit: int = 5000) -> list[dict[str, Any]]:
    if not path or not Path(path).exists():
        return []
    rows: list[dict[str, Any]] = []
    with Path(path).open() as handle:
        lines = deque(handle, maxlen=limit)
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if float(row.get("ts") or 0) >= since_ts:
            rows.append(row)
    return rows


def summarize(samples: list[dict[str, Any]], turns: list[dict[str, Any]], events: list[dict[str, Any]], window: int) -> int:
    first = samples[0]
    last = samples[-1]
    coords = [sample["coord"] for sample in samples if sample.get("coord")]
    maps = [sample["map"] for sample in samples if sample.get("map")]
    macros = [turn.get("macro") for turn in turns if turn.get("macro")]
    actions = [tuple(turn.get("actions") or []) for turn in turns]
    rewards = [float(turn.get("reward") or 0) for turn in turns]

    unique_coords = len(set(coords))
    coord_changes = sum(1 for a, b in zip(coords, coords[1:]) if a != b)
    same_coord_ratio = Counter(coords).most_common(1)[0][1] / len(coords) if coords else 1.0
    turn_delta = int(last.get("turn") or 0) - int(first.get("turn") or 0)

    planner_events = [event for event in events if str(event.get("event", "")).startswith("planner_")]
    warning_events = [
        event for event in events
        if event.get("event") in {"cycle_detected", "navigation_warning", "planner_no_safe_move"}
    ]
    self_recovery_events = [
        event for event in events
        if str(event.get("event", "")).startswith("self_recovery_")
    ]
    milestone_events = [
        event for event in events
        if event.get("event") in {"map_changed", "battle_started", "battle_ended", "party_count_changed", "badges_changed"}
    ]

    fail_reasons: list[str] = []
    if turn_delta < max(3, window // 10):
        fail_reasons.append("autoplayer_turns_not_advancing")
    if coords and unique_coords <= 1 and turn_delta >= 8:
        fail_reasons.append("same_coord_for_window")
    if same_coord_ratio >= 0.85 and turn_delta >= 12:
        fail_reasons.append("coord_dominates_window")
    if int(last.get("position_stuck") or 0) >= 20 or int(last.get("stuck") or 0) >= 20:
        fail_reasons.append("stuck_counter_high")
    if last.get("coord_oscillating"):
        fail_reasons.append("coord_oscillating")
    if macros and Counter(macros).most_common(1)[0][1] / len(macros) >= 0.85 and unique_coords <= 2:
        fail_reasons.append("same_macro_without_movement")
    coord_parts = [coord_tuple(coord) for coord in coords]
    coord_parts = [part for part in coord_parts if part is not None]
    if coord_parts and coord_parts[0][0] == 6657 and coord_parts[-1][0] == 6657:
        raw_xs = [part[1] for part in coord_parts]
        if min(raw_xs) > 1 and raw_xs[0] <= 10 and raw_xs[-1] >= raw_xs[0] and unique_coords <= 5:
            fail_reasons.append("route30_no_north_progress")

    pass_reasons: list[str] = []
    if coord_changes >= 3 or unique_coords >= 4:
        pass_reasons.append("coordinate_progress")
    if len(set(maps)) >= 2:
        pass_reasons.append("map_changed")
    if milestone_events:
        pass_reasons.append("structured_milestone_event")

    verdict = "PASS" if pass_reasons and not fail_reasons else "FAIL" if fail_reasons else "WATCH"
    print(f"verdict: {verdict}")
    print(f"window: {window}s samples={len(samples)} turns_delta={turn_delta}")
    print(f"map: {first.get('map')} -> {last.get('map')}")
    print(f"coord: {first.get('coord')} -> {last.get('coord')} unique={unique_coords} changes={coord_changes}")
    print(f"phase: {first.get('phase')} -> {last.get('phase')}")
    print(f"dialog_active: {first.get('dialog_active')} -> {last.get('dialog_active')}")
    print(f"hp: {first.get('hp')} -> {last.get('hp')}")
    print(f"stuck: visual={last.get('stuck')} position={last.get('position_stuck')} oscillating={last.get('coord_oscillating')}")
    print(f"reward_sum: {round(sum(rewards), 3)}")
    print("top_macros:", Counter(macros).most_common(5))
    print("top_actions:", [(list(action), count) for action, count in Counter(actions).most_common(5)])
    print("planner_events:", Counter(event.get("event") for event in planner_events).most_common())
    print("warning_events:", Counter(event.get("event") for event in warning_events).most_common())
    print("self_recovery_events:", Counter(event.get("event") for event in self_recovery_events).most_common())
    print("milestone_events:", Counter(event.get("event") for event in milestone_events).most_common())
    print("pass_reasons:", pass_reasons)
    print("fail_reasons:", fail_reasons)
    return 0 if verdict in {"PASS", "WATCH"} else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:9879")
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()

    start = time.time()
    samples: list[dict[str, Any]] = []
    while time.time() - start < args.seconds:
        state = get_json(args.base_url.rstrip("/") + "/state")
        auto = get_json(args.base_url.rstrip("/") + "/autoplayer/status")
        status = auto.get("status") or {}
        party = state.get("party") or []
        lead = party[0] if party and isinstance(party[0], dict) else {}
        dialog = state.get("dialog") or {}
        samples.append({
            "ts": time.time(),
            "turn": status.get("turn"),
            "phase": status.get("phase"),
            "coord": coord_from_state(state) or status.get("coord"),
            "map": map_label(state),
            "hp": lead.get("hp"),
            "dialog_active": dialog.get("active"),
            "stuck": status.get("stuck", 0),
            "position_stuck": status.get("position_stuck", 0),
            "coord_oscillating": status.get("coord_oscillating", False),
            "event_log": status.get("event_log"),
        })
        time.sleep(args.interval)

    auto = get_json(args.base_url.rstrip("/") + "/autoplayer/status")
    turns = [row for row in auto.get("recent", []) if float(row.get("ts") or 0) >= start]
    events = tail_jsonl(samples[-1].get("event_log"), start)
    return summarize(samples, turns, events, args.seconds)


if __name__ == "__main__":
    raise SystemExit(main())
