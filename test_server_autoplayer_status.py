import asyncio
import json
import time

import pytest

pytest.importorskip("fastapi")

import pokemon_agent.server as server
from pokemon_agent.server import GameConfig


def write_json(path, payload):
    path.write_text(json.dumps(payload))


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def configure_server_data_dir(tmp_path):
    previous = server._config
    server._config = GameConfig(rom_path="dummy.gb", game_type="gold", data_dir=str(tmp_path))
    return previous


def test_autoplayer_status_tails_v2_log_when_v2_active(tmp_path):
    previous = configure_server_data_dir(tmp_path)
    try:
        write_json(tmp_path / "gold_autoplayer_control.json", {"engine": "v2"})
        write_json(tmp_path / "gold_autoplayer_status.json", {"engine": "v2", "schema_version": 2})
        write_jsonl(tmp_path / "gold_autoplayer.jsonl", [{"engine": "v1", "event": "old"}])
        write_jsonl(tmp_path / "gold_autoplayer_v2.jsonl", [{"engine": "v2", "event": "turn"}])

        payload = asyncio.run(server.autoplayer_status())

        assert payload["status"]["engine"] == "v2"
        assert payload["recent"] == [{"engine": "v2", "event": "turn"}]
        assert payload["recent_v1"] == [{"engine": "v1", "event": "old"}]
        assert payload["logs"]["active"].endswith("gold_autoplayer_v2.jsonl")
    finally:
        server._config = previous


def test_autoplayer_status_warns_when_v2_selected_but_status_is_stale(tmp_path):
    previous = configure_server_data_dir(tmp_path)
    try:
        write_json(tmp_path / "gold_autoplayer_control.json", {"engine": "v2"})
        write_json(tmp_path / "gold_autoplayer_status.json", {"engine": "v1"})
        write_jsonl(tmp_path / "gold_autoplayer_v2.jsonl", [{"engine": "v2", "event": "idle"}])

        payload = asyncio.run(server.autoplayer_status())

        assert payload["status"]["visibility_warning"] == "V2 selected but latest status is not from V2 runner"
        assert payload["recent"] == [{"engine": "v2", "event": "idle"}]
    finally:
        server._config = previous


def test_autoplayer_status_includes_supervisor_health(tmp_path):
    previous = configure_server_data_dir(tmp_path)
    try:
        write_json(tmp_path / "gold_autoplayer_control.json", {"engine": "v2"})
        write_json(tmp_path / "gold_autoplayer_status.json", {"engine": "v2"})
        write_json(tmp_path / "gold_autoplayer_supervisor_status.json", {
            "active_engine": "v2",
            "desired_engine": "v2",
            "child_running": True,
            "restart_count": 0,
            "updated_at": time.time(),
        })

        payload = asyncio.run(server.autoplayer_status())

        assert payload["supervisor"]["active_engine"] == "v2"
        assert payload["supervisor_health"]["healthy"] is True
    finally:
        server._config = previous


def test_autoplayer_control_can_set_v2_safety_gates(tmp_path):
    previous = configure_server_data_dir(tmp_path)
    try:
        payload = asyncio.run(server.autoplayer_control(server.AutoplayerControlRequest(
            engine="v2",
            dry_run=True,
            allow_overworld_movement=False,
            allow_battle_actions=False,
        )))

        assert payload["control"]["engine"] == "v2"
        assert payload["control"]["dry_run"] is True
        assert payload["control"]["allow_overworld_movement"] is False
        assert payload["control"]["allow_battle_actions"] is False
    finally:
        server._config = previous


def test_autoplayer_control_accepts_unified_engine(tmp_path):
    previous = configure_server_data_dir(tmp_path)
    try:
        payload = asyncio.run(server.autoplayer_control(server.AutoplayerControlRequest(engine="unified")))

        assert payload["control"]["engine"] == "unified"
    finally:
        server._config = previous


def test_autoplayer_control_accepts_adaptive_engine(tmp_path):
    previous = configure_server_data_dir(tmp_path)
    try:
        payload = asyncio.run(server.autoplayer_control(server.AutoplayerControlRequest(engine="adaptive")))

        assert payload["control"]["engine"] == "adaptive"
    finally:
        server._config = previous


def test_autoplayer_control_accepts_handoff_settings(tmp_path):
    previous = configure_server_data_dir(tmp_path)
    try:
        payload = asyncio.run(server.autoplayer_control(server.AutoplayerControlRequest(
            engine="adaptive",
            auto_handoff_enabled=True,
            auto_handoff_v1_fallback="adaptive",
            auto_handoff_v2_fallback="v1",
        )))

        assert payload["control"]["auto_handoff_enabled"] is True
        assert payload["control"]["auto_handoff_v1_fallback"] == "adaptive"
        assert payload["control"]["auto_handoff_v2_fallback"] == "v1"
    finally:
        server._config = previous


def test_autoplayer_control_rejects_invalid_handoff_engine(tmp_path):
    previous = configure_server_data_dir(tmp_path)
    try:
        with pytest.raises(server.HTTPException):
            asyncio.run(server.autoplayer_control(server.AutoplayerControlRequest(auto_handoff_v2_fallback="bad")))
    finally:
        server._config = previous


def test_autoplayer_status_includes_v2_readiness_even_when_v1_active(tmp_path):
    previous = configure_server_data_dir(tmp_path)
    try:
        write_json(tmp_path / "gold_autoplayer_control.json", {"engine": "v1", "dry_run": True})
        write_json(tmp_path / "gold_autoplayer_status.json", {"engine": "v1"})
        write_jsonl(tmp_path / "gold_autoplayer_v2.jsonl", [{"engine": "v2", "event": "turn"}])

        payload = asyncio.run(server.autoplayer_status())

        assert payload["v2_readiness"]["status_available"] is False
        assert "engine_not_selected" in payload["v2_readiness"]["blockers"]
        assert "dry_run_enabled" in payload["v2_readiness"]["blockers"]
        assert payload["v2_readiness"]["latest_event"] == {"engine": "v2", "event": "turn"}
    finally:
        server._config = previous


def test_autoplayer_status_tails_unified_log_when_unified_active(tmp_path):
    previous = configure_server_data_dir(tmp_path)
    try:
        write_json(tmp_path / "gold_autoplayer_control.json", {"engine": "unified"})
        write_json(tmp_path / "gold_autoplayer_status.json", {"engine": "v2", "schema_version": 2})
        write_jsonl(tmp_path / "pokemon_autoplayer.jsonl", [{"engine": "unified", "event": "turn"}])

        payload = asyncio.run(server.autoplayer_status())

        assert payload["recent"] == [{"engine": "unified", "event": "turn"}]
        assert payload["logs"]["active"].endswith("pokemon_autoplayer.jsonl")
        assert payload["logs"]["v2_learning"].endswith("gold_autoplayer_v2_learning.json")
    finally:
        server._config = previous


def test_autoplayer_status_marks_missing_supervisor_unhealthy(tmp_path):
    previous = configure_server_data_dir(tmp_path)
    try:
        write_json(tmp_path / "gold_autoplayer_control.json", {"engine": "v2"})
        write_json(tmp_path / "gold_autoplayer_status.json", {"engine": "v2"})

        payload = asyncio.run(server.autoplayer_status())

        assert payload["supervisor_health"]["available"] is False
        assert payload["supervisor_health"]["healthy"] is False
    finally:
        server._config = previous


def test_autoplayer_status_warns_on_supervisor_engine_mismatch(tmp_path):
    previous = configure_server_data_dir(tmp_path)
    try:
        write_json(tmp_path / "gold_autoplayer_control.json", {"engine": "v2"})
        write_json(tmp_path / "gold_autoplayer_status.json", {"engine": "v2"})
        write_json(tmp_path / "gold_autoplayer_supervisor_status.json", {
            "active_engine": "v1",
            "desired_engine": "v2",
            "child_running": True,
            "updated_at": time.time(),
        })

        payload = asyncio.run(server.autoplayer_status())

        assert any("engine" in warning.lower() for warning in payload["warnings"])
    finally:
        server._config = previous


def test_autoplayer_status_exposes_mode_switch_and_shared_learning(tmp_path):
    previous = configure_server_data_dir(tmp_path)
    try:
        write_json(tmp_path / "gold_autoplayer_control.json", {"engine": "adaptive"})
        write_json(tmp_path / "gold_autoplayer_status.json", {"engine": "v2"})
        handoff = {"from": "v1", "to": "adaptive", "reason": "v1_stuck", "path_source": "loop", "updated_at": time.time()}
        write_json(tmp_path / "gold_autoplayer_supervisor_status.json", {
            "active_engine": "adaptive",
            "desired_engine": "adaptive",
            "child_running": True,
            "last_handoff": handoff,
            "updated_at": time.time(),
        })
        write_json(tmp_path / "pokemon_learning_memory.json", {
            "schema_version": 1,
            "updated_at": time.time(),
            "facts": [
                {"category": "PKM:POLICY", "game_id": "gold_silver", "text": "Auto handoff v1 -> adaptive because v1_stuck", "updated_at": time.time(), "data": {"kind": "mode_handoff"}},
                {"category": "PKM:RESOURCE", "game_id": "gold_silver", "text": "No balls", "updated_at": time.time() - 1, "data": {"kind": "broke_no_balls"}},
            ],
        })

        payload = asyncio.run(server.autoplayer_status())

        assert payload["mode_switch"]["selected_engine"] == "adaptive"
        assert payload["mode_switch"]["active_engine"] == "adaptive"
        assert payload["mode_switch"]["last_handoff"]["reason"] == "v1_stuck"
        assert payload["shared_learning"]["counts"]["PKM:POLICY"] == 1
        assert payload["shared_learning"]["counts"]["PKM:RESOURCE"] == 1
        assert payload["shared_learning"]["recent_facts"][0]["category"] == "PKM:POLICY"
    finally:
        server._config = previous
