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
