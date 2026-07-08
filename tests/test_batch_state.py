import os
import time
from formfiller.batch_state import load_ledger, save_ledger, acquire_lock, release_lock


def test_load_ledger_missing_returns_empty(tmp_path):
    assert load_ledger(tmp_path / "nope.json") == set()


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "ids.json"
    save_ledger(p, {"E1", "E2"})
    assert load_ledger(p) == {"E1", "E2"}


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
