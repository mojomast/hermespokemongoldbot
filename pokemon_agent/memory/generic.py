"""Generic Game Boy/Game Boy Color memory reader.

This reader intentionally does not claim structured Pokemon RAM knowledge.
It keeps the HTTP API usable for games whose memory maps are not yet
implemented (for example Pokemon Gold/Silver): screenshots, input actions,
saves, loads, and dashboard streaming still work, while structured fields are
reported as unavailable instead of mis-decoded as Pokemon Red.
"""

from __future__ import annotations

from typing import Any, Dict, List

from pokemon_agent.memory.reader import GameMemoryReader


class GenericGameBoyReader(GameMemoryReader):
    """Fallback reader for playable-but-unstructured GB/GBC ROMs."""

    def __init__(self, emulator, game_name: str = "Generic Game Boy/Game Boy Color") -> None:
        super().__init__(emulator)
        self._game_name = game_name

    @property
    def game_name(self) -> str:
        return self._game_name

    def read_player(self) -> Dict[str, Any]:
        return {
            "name": None,
            "money": None,
            "badges": [],
            "position": {"map_id": None, "map_name": "Unknown", "x": None, "y": None},
            "facing": None,
            "play_time": None,
            "note": "Structured RAM decoding is not implemented for this ROM; use screenshots/vision.",
        }

    def read_party(self) -> List[Dict[str, Any]]:
        return []

    def read_bag(self) -> List[Dict[str, Any]]:
        return []

    def read_battle(self) -> Dict[str, Any]:
        return {"in_battle": None, "note": "Unknown in generic mode; inspect screenshot."}

    def read_dialog(self) -> Dict[str, Any]:
        return {"active": None, "text": None, "note": "Unknown in generic mode; inspect screenshot."}

    def read_map_info(self) -> Dict[str, Any]:
        return {"map_id": None, "map_name": "Unknown", "note": "Unknown in generic mode."}

    def read_flags(self) -> Dict[str, Any]:
        return {"generic_mode": True, "structured_state_available": False}
