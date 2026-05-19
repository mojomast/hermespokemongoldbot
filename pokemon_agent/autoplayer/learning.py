"""Durable learning memory shared by game-specific autoplayer plugins."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MEMORY_CATEGORIES = frozenset({
    "PKM:OBJECTIVE",
    "PKM:PROGRESS",
    "PKM:MAP",
    "PKM:STUCK",
    "PKM:TEAM",
    "PKM:STRATEGY",
    "PKM:POLICY",
    "PKM:RESOURCE",
    "PKM:FAILURE",
    "PKM:OUTCOME",
})


@dataclass(frozen=True, slots=True)
class LearningFact:
    category: str
    text: str
    game_id: str
    confidence: str = "observed"
    source: str = "autoplayer"
    updated_at: float = 0.0
    data: dict[str, Any] | None = None

    def normalized(self) -> "LearningFact":
        category = self.category if self.category in MEMORY_CATEGORIES else "PKM:PROGRESS"
        return LearningFact(
            category=category,
            text=self.text.strip(),
            game_id=self.game_id,
            confidence=self.confidence or "observed",
            source=self.source or "autoplayer",
            updated_at=self.updated_at or time.time(),
            data=dict(self.data or {}),
        )


class LearningMemory:
    """Small JSON-backed memory store for cross-game Pokemon play.

    The store intentionally uses Hermes-style PKM prefixes while remaining local
    to the bot. A future Hermes bridge can mirror selected facts into Hermes
    memory without changing the autoplayer core.
    """

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("schema_version", 1)
        payload.setdefault("facts", [])
        payload.setdefault("updated_at", 0.0)
        return payload

    def save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def add_fact(self, fact: LearningFact) -> dict[str, Any]:
        normalized = fact.normalized()
        if not normalized.text:
            return self.load()
        payload = self.load()
        facts = [row for row in payload.get("facts", []) if isinstance(row, dict)]
        new_row = asdict(normalized)
        merged = False
        for index, row in enumerate(facts):
            if not _same_fact(row, new_row):
                continue
            row_data = row.get("data") if isinstance(row.get("data"), dict) else {}
            new_data = new_row.get("data") if isinstance(new_row.get("data"), dict) else {}
            evidence_count = int(row_data.get("evidence_count", 1) or 1) + int(new_data.get("evidence_count", 1) or 1)
            merged_data = {**row_data, **new_data, "evidence_count": evidence_count, "last_seen_at": normalized.updated_at}
            row.update(new_row)
            row["data"] = merged_data
            facts[index] = row
            merged = True
            break
        if not merged:
            data = new_row.get("data") if isinstance(new_row.get("data"), dict) else {}
            data.setdefault("evidence_count", 1)
            data.setdefault("first_seen_at", normalized.updated_at)
            data.setdefault("last_seen_at", normalized.updated_at)
            new_row["data"] = data
            facts.append(new_row)
        payload["facts"] = facts[-500:]
        payload["updated_at"] = normalized.updated_at
        self.save(payload)
        return payload

    def facts_for(self, category: str | None = None, game_id: str | None = None) -> list[dict[str, Any]]:
        rows = [row for row in self.load().get("facts", []) if isinstance(row, dict)]
        if category is not None:
            rows = [row for row in rows if row.get("category") == category]
        if game_id is not None:
            rows = [row for row in rows if row.get("game_id") == game_id]
        return rows

    def summary(self, game_id: str | None = None) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {category: [] for category in sorted(MEMORY_CATEGORIES)}
        for row in self.facts_for(game_id=game_id):
            category = str(row.get("category") or "PKM:PROGRESS")
            result.setdefault(category, []).append(str(row.get("text") or ""))
        return {key: values for key, values in result.items() if values}


def _same_fact(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left.get("category") == right.get("category")
        and left.get("game_id") == right.get("game_id")
        and left.get("text") == right.get("text")
    )


def read_optional_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def import_gold_v1_teacher_snapshot(
    data_dir: Path,
    memory: LearningMemory,
    *,
    previous_marker: dict[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Import bounded legacy Gold V1 evidence into shared teacher memory.

    The import is intentionally factual: V1 contributes observed map/stuck/progress
    evidence, while live runners still verify every action before trusting it.
    """
    data_dir = Path(data_dir)
    world_path = data_dir / "gold_world_model.json"
    policy_path = data_dir / "gold_policy.json"
    try:
        marker = {
            "world_mtime": world_path.stat().st_mtime if world_path.exists() else 0.0,
            "policy_mtime": policy_path.stat().st_mtime if policy_path.exists() else 0.0,
        }
    except Exception:
        return {"marker": previous_marker or {}, "counts": {}}
    if not force and previous_marker == marker:
        return {"marker": marker, "counts": {}, "skipped": True}

    world = read_optional_json(world_path)
    policy = read_optional_json(policy_path)
    counts = {"blocked_moves": 0, "open_edges": 0, "places": 0, "policy_snapshots": 0}

    blocked_moves = world.get("blocked_moves") if isinstance(world, dict) else {}
    if isinstance(blocked_moves, dict):
        imported = 0
        for coord_key, actions in sorted(blocked_moves.items())[:250]:
            if not isinstance(actions, list):
                continue
            walk_actions = [str(action) for action in actions if str(action).startswith("walk_")]
            if not walk_actions:
                continue
            memory.add_fact(LearningFact(
                category="PKM:STUCK",
                game_id="gold_silver",
                text=f"V1 observed blocked moves at {coord_key}: {', '.join(walk_actions[:6])}",
                confidence="observed",
                source="v1_teacher",
                data={"kind": "v1_blocked_moves", "coord_key": coord_key, "actions": walk_actions[:12], "evidence_count": 1},
            ))
            imported += 1
            counts["blocked_moves"] += 1
            if imported >= 80:
                break

    directed_edges = world.get("directed_edges") if isinstance(world, dict) else {}
    if isinstance(directed_edges, dict):
        imported = 0
        open_edges = [edge for edge in directed_edges.values() if isinstance(edge, dict) and edge.get("state") == "open"]
        open_edges.sort(key=lambda edge: int(edge.get("open_count", 0) or 0), reverse=True)
        for edge in open_edges[:120]:
            action = str(edge.get("action") or "")
            from_key = str(edge.get("from") or "")
            if not action.startswith("walk_") or not from_key:
                continue
            memory.add_fact(LearningFact(
                category="PKM:MAP",
                game_id="gold_silver",
                text=f"V1 found open edge {from_key} -> {edge.get('to')} via {action}",
                confidence="observed",
                source="v1_teacher",
                data={
                    "kind": "v1_open_edge",
                    "from": from_key,
                    "to": edge.get("to"),
                    "action": action,
                    "direction": edge.get("direction"),
                    "open_count": int(edge.get("open_count", 0) or 0),
                    "blocked_count": int(edge.get("blocked_count", 0) or 0),
                    "evidence_count": max(1, int(edge.get("open_count", 0) or 0)),
                },
            ))
            imported += 1
            counts["open_edges"] += 1
            if imported >= 80:
                break

    places = world.get("places") if isinstance(world, dict) else {}
    if isinstance(places, dict):
        for place_key, place in list(places.items())[:80]:
            if not isinstance(place, dict):
                continue
            memory.add_fact(LearningFact(
                category="PKM:MAP",
                game_id="gold_silver",
                text=f"V1 mapped place {place_key}",
                confidence="observed",
                source="v1_teacher",
                data={"kind": "v1_place", "place_key": place_key, "summary": {k: place.get(k) for k in ("visits", "important", "notes")}, "evidence_count": 1},
            ))
            counts["places"] += 1

    phase = policy.get("phase") if isinstance(policy, dict) else None
    turn = policy.get("turn") if isinstance(policy, dict) else None
    if phase or turn:
        memory.add_fact(LearningFact(
            category="PKM:PROGRESS",
            game_id="gold_silver",
            text=f"V1 policy snapshot phase={phase or 'unknown'} turn={turn if turn is not None else 'unknown'}",
            confidence="observed",
            source="v1_teacher",
            data={"kind": "v1_policy_snapshot", "phase": phase, "turn": turn, "evidence_count": 1},
        ))
        counts["policy_snapshots"] += 1

    return {"marker": marker, "counts": counts, "updated_at": time.time()}
