import asyncio

import pytest

fastapi = pytest.importorskip("fastapi")
HTTPException = fastapi.HTTPException

import pokemon_agent.server as server
from pokemon_agent.server import GameConfig


class FakeRequest:
    def __init__(self, name: str, body: bytes):
        self.headers = {"x-rom-filename": name}
        self._body = body

    async def body(self):
        return self._body


def test_safe_rom_name_accepts_only_supported_extensions():
    assert server._safe_rom_name("Pokemon Gold.gbc") == "Pokemon_Gold.gbc"
    with pytest.raises(HTTPException):
        server._safe_rom_name("notes.txt")


def test_rom_info_returns_launch_command(tmp_path):
    rom = tmp_path / "pokemon_gold.gbc"
    rom.write_bytes(b"rom-bytes")
    server.configure(GameConfig(rom_path="", port=9879, data_dir=str(tmp_path)))

    info = server._rom_info(rom)

    assert info["game_type"] == "gold"
    assert info["supported"] is True
    assert info["autoplayer_profile"] == "gold_silver"
    assert "pokemon-agent serve --rom" in info["launch_command"]
    assert str(rom) in info["launch_command"]
    assert info["profile_data_dir"].endswith("games/gold-" + info["sha256"][:12])
    assert info["profile_data_dir"] in info["launch_command"]


def test_upload_rom_saves_raw_request_body(tmp_path):
    server.configure(GameConfig(rom_path="", port=9879, data_dir=str(tmp_path)))

    result = asyncio.run(server.upload_rom(FakeRequest("blue.gb", b"fake-rom")))

    path = tmp_path / "roms" / "blue.gb"
    assert path.read_bytes() == b"fake-rom"
    assert result["ok"] is True
    assert result["rom"]["game_type"] == "red"


def test_public_path_normalizes_share_paths():
    assert server._public_path("dashboard/watch.html") == "/dashboard/watch.html"
    assert server._public_path("/dashboard/onboarding.html") == "/dashboard/onboarding.html"
