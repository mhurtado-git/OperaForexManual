import threading

import pytest

from forexplat.safeio import atomic_write_text, file_lock


def test_atomic_write_replaces_content_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "sub" / "a.json"
    atomic_write_text(path, "uno")
    atomic_write_text(path, "dos")
    assert path.read_text(encoding="utf-8") == "dos"
    assert [p.name for p in path.parent.iterdir()] == ["a.json"]


def test_lock_blocks_a_second_holder_until_released(tmp_path):
    target = tmp_path / "diario.csv"
    order = []

    def second():
        with file_lock(target, timeout=3):
            order.append("segundo")

    with file_lock(target):
        thread = threading.Thread(target=second)
        thread.start()
        thread.join(0.3)
        assert thread.is_alive()  # espera mientras el primero lo tiene
        order.append("primero")
    thread.join(3)
    assert order == ["primero", "segundo"]
    assert not (tmp_path / "diario.csv.lock").exists()


def test_lock_times_out_and_stale_locks_are_ignored(tmp_path):
    target = tmp_path / "d.csv"
    lock = tmp_path / "d.csv.lock"
    lock.write_text("", encoding="utf-8")
    with pytest.raises(TimeoutError, match="otro proceso"):
        with file_lock(target, timeout=0.2):
            pass
    with file_lock(target, timeout=1, stale_after=0):  # un bloqueo viejo se descarta
        pass
