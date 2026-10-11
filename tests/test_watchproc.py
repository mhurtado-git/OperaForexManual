import sys
import time

import pytest

from forexplat.ui.watchproc import WatchController

SLEEPER = [sys.executable, "-u", "-c", "import time; print('vigilando', flush=True); time.sleep(60)"]
QUICK = [sys.executable, "-c", "print('adios')"]


def wait_for(condition, seconds=5):
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.05)
    return False


def test_start_stop_and_log(tmp_path):
    controller = WatchController(tmp_path, "demo", command=SLEEPER)
    assert not controller.is_running() and controller.log_tail() == ""
    pid = controller.start()
    try:
        assert controller.is_running() and pid > 0
        assert wait_for(lambda: "vigilando" in controller.log_tail())
        assert "--- inicio" in controller.log_tail()
        with pytest.raises(RuntimeError, match="ya está en marcha"):
            controller.start()
    finally:
        assert controller.stop() is True
    assert not controller.is_running() and not controller.pid_path.exists()
    assert "--- detenida" in controller.log_tail()
    assert controller.stop() is False  # detener dos veces no falla


def test_state_is_shared_between_controller_instances(tmp_path):
    first = WatchController(tmp_path, "demo", command=SLEEPER)
    first.start()
    try:
        assert WatchController(tmp_path, "demo").is_running()  # como tras recargar la página
    finally:
        first.stop()


def test_finished_process_is_detected_and_pid_file_cleaned(tmp_path):
    controller = WatchController(tmp_path, "demo", command=QUICK)
    controller.start()
    assert wait_for(lambda: not controller.is_running())
    assert not controller.pid_path.exists()
    assert "adios" in controller.log_tail()


def test_real_profile_cannot_be_started_from_the_ui(tmp_path):
    with pytest.raises(ValueError, match="solo se puede iniciar la vigilancia en demo"):
        WatchController(tmp_path, "real", command=SLEEPER).start()


def test_default_command_uses_the_cli_with_options(tmp_path):
    command = WatchController(tmp_path, "demo").build_command(["a", "b"], ["EURUSD"], 90)
    assert command[1:4] == ["-u", "-m", "forexplat"] and command[4] == "watch"
    assert command.count("--strategy") == 2 and "EURUSD" in command and "90" in command


def test_corrupt_pid_file_is_treated_as_not_running(tmp_path):
    controller = WatchController(tmp_path, "demo", command=QUICK)
    controller.pid_path.parent.mkdir(parents=True)
    controller.pid_path.write_text("basura", encoding="utf-8")
    assert not controller.is_running()
