import json

from gold_autoplayer_service import AutoplayerSupervisor


class FakeChild:
    def __init__(self, command):
        self.command = list(command)
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.waits = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        self.waits += 1
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class FakeFactory:
    def __init__(self):
        self.children = []

    def __call__(self, command):
        child = FakeChild(command)
        self.children.append(child)
        return child


class FailingFactory:
    def __call__(self, command):
        raise RuntimeError("boom")


def write_control(tmp_path, engine):
    payload = {} if engine is None else {"engine": engine}
    (tmp_path / "gold_autoplayer_control.json").write_text(json.dumps(payload))


def write_status(tmp_path, payload):
    (tmp_path / "gold_autoplayer_status.json").write_text(json.dumps(payload))


def supervisor(tmp_path, factory=None):
    return AutoplayerSupervisor(
        base_url="http://127.0.0.1:9879",
        data_dir=tmp_path,
        delay=1.25,
        python="python-test",
        process_factory=factory or FakeFactory(),
    )


def test_supervisor_defaults_missing_control_to_v1(tmp_path):
    service = supervisor(tmp_path)

    assert service.read_engine() == "v1"


def test_supervisor_treats_invalid_engine_as_v1(tmp_path):
    write_control(tmp_path, "bad")
    service = supervisor(tmp_path)

    assert service.read_engine() == "v1"


def test_supervisor_builds_v1_command_with_shared_data_dir_and_delay(tmp_path):
    service = supervisor(tmp_path)

    command = service.command_for_engine("v1")

    assert command[0] == "python-test"
    assert command[1].endswith("gold_autoplayer.py")
    assert "--base-url" in command
    assert "http://127.0.0.1:9879" in command
    assert "--data-dir" in command
    assert str(tmp_path) in command
    assert "--delay" in command
    assert "1.25" in command


def test_supervisor_builds_v2_command_with_shared_data_dir(tmp_path):
    service = supervisor(tmp_path)

    command = service.command_for_engine("v2")

    assert command[0] == "python-test"
    assert command[1].endswith("gold_autoplayer_v2.py")
    assert "--data-dir" in command
    assert str(tmp_path) in command
    assert "--delay" not in command


def test_supervisor_builds_unified_command_with_shared_data_dir(tmp_path):
    service = supervisor(tmp_path)

    command = service.command_for_engine("unified")

    assert command[0] == "python-test"
    assert command[1].endswith("pokemon_autoplayer.py")
    assert "--base-url" in command
    assert "http://127.0.0.1:9879" in command
    assert "--data-dir" in command
    assert str(tmp_path) in command


def test_supervisor_builds_adaptive_command_with_v2_runner(tmp_path):
    service = supervisor(tmp_path)

    command = service.command_for_engine("adaptive")

    assert command[0] == "python-test"
    assert command[1].endswith("gold_autoplayer_v2.py")
    assert "--base-url" in command
    assert "http://127.0.0.1:9879" in command
    assert "--data-dir" in command
    assert str(tmp_path) in command


def test_supervisor_accepts_unified_engine(tmp_path):
    write_control(tmp_path, "unified")
    service = supervisor(tmp_path)

    assert service.read_engine() == "unified"


def test_supervisor_accepts_adaptive_engine(tmp_path):
    write_control(tmp_path, "adaptive")
    service = supervisor(tmp_path)

    assert service.read_engine() == "adaptive"


def test_supervisor_restarts_child_when_engine_changes(tmp_path):
    factory = FakeFactory()
    service = supervisor(tmp_path, factory)
    write_control(tmp_path, "v1")

    service.reconcile_once()
    first = factory.children[-1]
    write_control(tmp_path, "v2")
    service.reconcile_once()

    assert first.terminated is True
    assert first.waits >= 1
    assert len(factory.children) == 2
    assert service.active_engine == "v2"
    assert factory.children[-1].command[1].endswith("gold_autoplayer_v2.py")


def test_supervisor_does_not_start_second_child_before_old_child_exits(tmp_path):
    factory = FakeFactory()
    service = supervisor(tmp_path, factory)
    write_control(tmp_path, "v1")

    service.reconcile_once()
    first = factory.children[-1]
    write_control(tmp_path, "v2")

    service.reconcile_once()

    live_children = [child for child in factory.children if child.poll() is None]

    assert first.poll() == 0
    assert len(live_children) == 1


def test_supervisor_backs_off_after_child_crash(tmp_path):
    factory = FakeFactory()
    service = supervisor(tmp_path, factory)
    write_control(tmp_path, "v2")
    service.reconcile_once()
    first = factory.children[-1]
    first.returncode = 7

    service.reconcile_once()
    service.reconcile_once()

    assert service.last_exit_code == 7
    assert service.restart_count == 1
    assert service.active_engine is None
    assert len(factory.children) == 1
    status = json.loads((tmp_path / "gold_autoplayer_supervisor_status.json").read_text())
    assert status["child_exit_code"] == 7
    assert status["restart_delay_seconds"] >= 1.0


def test_supervisor_records_start_error_and_backs_off(tmp_path):
    service = supervisor(tmp_path, FailingFactory())
    write_control(tmp_path, "v2")

    service.reconcile_once()

    assert service.active_engine is None
    assert service.restart_count == 1
    assert "RuntimeError" in service.last_start_error
    status = json.loads((tmp_path / "gold_autoplayer_supervisor_status.json").read_text())
    assert "boom" in status["last_start_error"]
    assert status["child_running"] is False


def test_supervisor_writes_status_when_child_starts(tmp_path):
    factory = FakeFactory()
    service = supervisor(tmp_path, factory)
    write_control(tmp_path, "v1")

    service.reconcile_once()

    status = json.loads((tmp_path / "gold_autoplayer_supervisor_status.json").read_text())
    assert status["active_engine"] == "v1"
    assert status["desired_engine"] == "v1"
    assert status["child_running"] is True


def test_supervisor_request_stop_writes_stopped_status(tmp_path):
    factory = FakeFactory()
    service = supervisor(tmp_path, factory)
    write_control(tmp_path, "v1")
    service.reconcile_once()

    service.request_stop(15, None)

    status = json.loads((tmp_path / "gold_autoplayer_supervisor_status.json").read_text())
    assert status["child_running"] is False
    assert status["active_engine"] is None


def test_supervisor_hands_v2_to_v1_when_hard_stuck(tmp_path):
    factory = FakeFactory()
    service = supervisor(tmp_path, factory)
    (tmp_path / "gold_autoplayer_control.json").write_text(json.dumps({"engine": "v2", "auto_handoff_enabled": True}))
    write_status(tmp_path, {"engine": "v2", "navigation": {"recovery_level": 2, "path_source": "safety_circuit_breaker"}})

    service.reconcile_once()

    control = json.loads((tmp_path / "gold_autoplayer_control.json").read_text())
    supervisor_status = json.loads((tmp_path / "gold_autoplayer_supervisor_status.json").read_text())
    assert control["engine"] == "v1"
    assert control["auto_handoff"]["from"] == "v2"
    assert service.active_engine == "v1"
    assert supervisor_status["last_handoff"]["reason"] == "v2_hard_stuck"


def test_supervisor_persists_handoff_as_shared_learning_fact(tmp_path):
    service = supervisor(tmp_path, FakeFactory())
    (tmp_path / "gold_autoplayer_control.json").write_text(json.dumps({"engine": "v2", "auto_handoff_enabled": True}))
    write_status(tmp_path, {"engine": "v2", "navigation": {"recovery_level": 2, "path_source": "safety_circuit_breaker"}})

    service.reconcile_once()

    memory = json.loads((tmp_path / "pokemon_learning_memory.json").read_text())
    policy_facts = [fact for fact in memory["facts"] if fact["category"] == "PKM:POLICY"]
    failure_facts = [fact for fact in memory["facts"] if fact["category"] == "PKM:FAILURE"]
    assert policy_facts[-1]["data"]["kind"] == "mode_handoff"
    assert policy_facts[-1]["data"]["reason"] == "v2_hard_stuck"
    assert failure_facts[-1]["data"]["kind"] == "mode_handoff_failure"


def test_supervisor_hands_v2_to_v1_when_battle_missing_menu_blocks(tmp_path):
    service = supervisor(tmp_path, FakeFactory())
    (tmp_path / "gold_autoplayer_control.json").write_text(json.dumps({"engine": "adaptive", "auto_handoff_enabled": True}))
    write_status(tmp_path, {
        "engine": "v2",
        "phase": "BATTLE",
        "actions": [],
        "navigation": {"path_source": "battle_fallback", "battle_policy": "trainer_fight_blocked_missing_menu_state"},
    })

    service.reconcile_once()

    control = json.loads((tmp_path / "gold_autoplayer_control.json").read_text())
    assert control["engine"] == "v1"
    assert control["auto_handoff"]["reason"] == "adaptive_hard_stuck"


def test_supervisor_hands_v1_to_adaptive_when_v1_stuck(tmp_path):
    service = supervisor(tmp_path, FakeFactory())
    (tmp_path / "gold_autoplayer_control.json").write_text(json.dumps({"engine": "v1", "auto_handoff_enabled": True}))
    write_status(tmp_path, {"engine": "v1", "stuck": True})

    service.reconcile_once()

    control = json.loads((tmp_path / "gold_autoplayer_control.json").read_text())
    assert control["engine"] == "adaptive"
    assert control["auto_handoff"]["reason"] == "v1_stuck"
    assert service.active_engine == "adaptive"


def test_supervisor_does_not_handoff_red_blue_unified_to_gold_engine(tmp_path):
    service = supervisor(tmp_path, FakeFactory())
    (tmp_path / "gold_autoplayer_control.json").write_text(json.dumps({"engine": "unified", "auto_handoff_enabled": True}))
    write_status(tmp_path, {"engine": "unified", "profile": "red_blue", "navigation": {"recovery_level": 2}})

    service.reconcile_once()

    control = json.loads((tmp_path / "gold_autoplayer_control.json").read_text())
    assert control["engine"] == "unified"
    assert service.active_engine == "unified"


def test_supervisor_handoff_respects_cooldown(tmp_path):
    service = supervisor(tmp_path, FakeFactory())
    (tmp_path / "gold_autoplayer_control.json").write_text(json.dumps({
        "engine": "v2",
        "auto_handoff_enabled": True,
        "auto_handoff": {"from": "v2", "to": "v1", "cooldown_until": 9999999999},
    }))
    write_status(tmp_path, {"engine": "v2", "navigation": {"recovery_level": 2}})

    service.reconcile_once()

    control = json.loads((tmp_path / "gold_autoplayer_control.json").read_text())
    assert control["engine"] == "v2"
    assert service.active_engine == "v2"


def test_supervisor_cooldown_allows_reciprocal_rescue_handoff(tmp_path):
    service = supervisor(tmp_path, FakeFactory())
    (tmp_path / "gold_autoplayer_control.json").write_text(json.dumps({
        "engine": "adaptive",
        "auto_handoff_enabled": True,
        "auto_handoff": {"from": "v1", "to": "adaptive", "cooldown_until": 9999999999},
    }))
    write_status(tmp_path, {"engine": "v2", "navigation": {"recovery_level": 2}})

    service.reconcile_once()

    control = json.loads((tmp_path / "gold_autoplayer_control.json").read_text())
    assert control["engine"] == "v1"
    assert control["auto_handoff"]["from"] == "adaptive"
    assert control["auto_handoff"]["to"] == "v1"
