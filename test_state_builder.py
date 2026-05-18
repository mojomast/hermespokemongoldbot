from pokemon_agent.memory.reader import GameMemoryReader
from pokemon_agent.state.builder import build_game_state
import pytest


class FakeEmulator:
    def __init__(self, frame_count: int = 100):
        self.frame_count = frame_count

    def get_screen(self):
        from PIL import Image, ImageDraw

        im = Image.new("RGB", (160, 144), "black")
        draw = ImageDraw.Draw(im)
        draw.rectangle((0, 96, 159, 143), fill="white")
        draw.rectangle((2, 98, 157, 141), outline="black", width=2)
        draw.line((8, 112, 130, 112), fill="black", width=2)
        draw.line((8, 128, 120, 128), fill="black", width=2)
        return im


class FakeReader(GameMemoryReader):
    def __init__(self, frame_count: int = 100, advance_on_party: bool = False):
        super().__init__(FakeEmulator(frame_count))
        self.advance_on_party = advance_on_party

    @property
    def game_name(self) -> str:
        return "Fake"

    def read_player(self):
        return {}

    def read_party(self):
        if self.advance_on_party:
            self.emu.frame_count += 1
        return []

    def read_bag(self):
        return []

    def read_battle(self):
        return {"in_battle": False}

    def read_dialog(self):
        return {"active": False}

    def read_map_info(self):
        return {}

    def read_flags(self):
        return {}


def test_build_game_state_marks_consistent_frame_reads():
    state = build_game_state(FakeReader(frame_count=100))

    assert state["metadata"]["read_started_frame"] == 100
    assert state["metadata"]["read_finished_frame"] == 100
    assert state["metadata"]["read_consistent"] is True
    assert isinstance(state["metadata"]["read_duration_ms"], float)


def test_build_game_state_marks_inconsistent_frame_reads():
    state = build_game_state(FakeReader(frame_count=100, advance_on_party=True))

    assert state["metadata"]["read_started_frame"] == 100
    assert state["metadata"]["read_finished_frame"] == 101
    assert state["metadata"]["read_consistent"] is False


def test_build_game_state_marks_visual_textbox_active_with_stale_ram():
    pytest.importorskip("PIL")
    state = build_game_state(FakeReader(frame_count=100))

    assert state["visual"]["visual_textbox_active"] is True
    assert state["dialog"]["visual_active"] is True
    assert state["dialog"]["ram_active"] is False
    assert state["dialog"]["active"] is True
