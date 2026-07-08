import os
import time

import pytest

from formfiller.batch_state import load_ledger, save_ledger, acquire_lock, release_lock


def test_load_ledger_missing_returns_empty(tmp_path):
    assert load_ledger(tmp_path / "nope.json") == set()


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "ids.json"
    save_ledger(p, {"E1", "E2"})
    assert load_ledger(p) == {"E1", "E2"}


def test_save_ledger_raises_after_retries(tmp_path, monkeypatch):
    from pathlib import Path
    import formfiller.batch_state as batch_state

    def boom(self, *a, **k):
        raise OSError("locked (OneDrive/AV)")

    monkeypatch.setattr(Path, "write_text", boom)
    monkeypatch.setattr(batch_state.time, "sleep", lambda s: None)   # test rapide

    with pytest.raises(OSError):
        save_ledger(tmp_path / "ids.json", {"E1"})


def test_load_ledger_corrupt_returns_empty(tmp_path):
    p = tmp_path / "ids.json"
    p.write_text("{ not json", encoding="utf-8")
    assert load_ledger(p) == set()


def test_acquire_lock_on_free_path_succeeds(tmp_path):
    p = tmp_path / ".lock"
    assert acquire_lock(p, stale_seconds=3600) is True
    assert p.exists()


def test_acquire_lock_fails_when_fresh_lock_present(tmp_path):
    p = tmp_path / ".lock"
    assert acquire_lock(p, stale_seconds=3600) is True
    assert acquire_lock(p, stale_seconds=3600) is False   # 2e run concurrent


def test_acquire_lock_overrides_stale_lock(tmp_path):
    p = tmp_path / ".lock"
    p.write_text("123", encoding="utf-8")
    old = time.time() - 10_000
    os.utime(p, (old, old))
    assert acquire_lock(p, stale_seconds=3600) is True   # verrou périmé -> repris


def test_release_lock_removes_file_and_tolerates_missing(tmp_path):
    p = tmp_path / ".lock"
    acquire_lock(p, stale_seconds=3600)
    release_lock(p)
    assert not p.exists()
    release_lock(p)   # ne lève pas


def test_acquire_lock_does_not_clobber_fresh_lock(tmp_path):
    p = tmp_path / ".lock"
    assert acquire_lock(p, stale_seconds=3600) is True
    content_before = p.read_text(encoding="utf-8")
    assert acquire_lock(p, stale_seconds=3600) is False
    assert p.read_text(encoding="utf-8") == content_before


def test_acquire_lock_returns_false_if_stale_unlink_blocked(tmp_path, monkeypatch):
    from pathlib import Path

    p = tmp_path / ".lock"
    p.write_text("999", encoding="utf-8")
    old = time.time() - 10_000
    os.utime(p, (old, old))   # verrou périmé

    def boom(self):
        raise PermissionError("in use")

    monkeypatch.setattr(Path, "unlink", boom)
    assert acquire_lock(p, stale_seconds=3600) is False   # un autre process le détient
