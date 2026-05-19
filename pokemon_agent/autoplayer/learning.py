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
