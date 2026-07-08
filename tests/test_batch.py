from formfiller.batch import run_batch, BatchSummary
from formfiller.batch_state import load_ledger, save_ledger
from formfiller.config import AppConfig
from formfiller.email_source import FakeEmailSource
from formfiller.models import EmailMessage


class _Result:
    def __init__(self, status, overall_confidence=0.9):
        self.status = status
        self.overall_confidence = overall_confidence


def _msg(entry_id):
    return EmailMessage(entry_id=entry_id, sender="a@b.com", subject="s",
                        received="2026-07-07T09:00:00", body_text="t", body_html="<p>t</p>")


def _cfg(tmp_path):
    return AppConfig(excel_log_path=str(tmp_path / "log.xlsx"),
                     processed_ledger_path=str(tmp_path / "ids.json"),
                     inbox_list_count=10,
                     processed_subfolder="Traité", review_subfolder="Revue humaine")


def _process_from(mapping):
    return lambda email: _Result(mapping[email.entry_id])


def test_run_batch_routes_each_status_to_its_folder(tmp_path):
    source = FakeEmailSource([_msg("E1"), _msg("E2"), _msg("E3")])
    process = _process_from({"E1": "success", "E2": "manual", "E3": "fail"})
    summary = run_batch(source=source, process=process, config=_cfg(tmp_path), log=lambda m: None)
    assert summary.processed == 1 and summary.review == 1 and summary.failed == 1
    assert ("E1", "Traité") in source.moves
    assert ("E2", "Revue humaine") in source.moves
    assert ("E3", "Revue humaine") in source.moves


def test_run_batch_isolates_exceptions_as_fail(tmp_path):
    source = FakeEmailSource([_msg("E1"), _msg("E2")])
    def process(email):
        if email.entry_id == "E1":
            raise RuntimeError("boom")
        return _Result("success")
    summary = run_batch(source=source, process=process, config=_cfg(tmp_path), log=lambda m: None)
    assert summary.failed == 1 and summary.processed == 1
    assert ("E1", "Revue humaine") in source.moves   # l'exception -> revue humaine


def test_run_batch_skips_entries_already_in_ledger(tmp_path):
    cfg = _cfg(tmp_path)
    save_ledger(cfg.processed_ledger_path, {"E1"})
    source = FakeEmailSource([_msg("E1"), _msg("E2")])
    process = _process_from({"E1": "success", "E2": "success"})
    summary = run_batch(source=source, process=process, config=cfg, log=lambda m: None)
    assert summary.skipped == 1 and summary.processed == 1
    assert source.moves == [("E2", "Traité")]         # E1 non retraité


def test_run_batch_records_ledger_after_processing(tmp_path):
    cfg = _cfg(tmp_path)
    source = FakeEmailSource([_msg("E1")])
    run_batch(source=source, process=_process_from({"E1": "success"}), config=cfg, log=lambda m: None)
    assert "E1" in load_ledger(cfg.processed_ledger_path)


def test_run_batch_move_failure_still_ledgers_so_no_reprocess(tmp_path):
    cfg = _cfg(tmp_path)
    source = FakeEmailSource([_msg("E1")], move_fails=True)
    summary = run_batch(source=source, process=_process_from({"E1": "success"}), config=cfg, log=lambda m: None)
    assert summary.not_moved == 1
    assert "E1" in load_ledger(cfg.processed_ledger_path)   # filet anti-double-soumission
    # un 2e run ne retraite pas E1
    source2 = FakeEmailSource([_msg("E1")])
    summary2 = run_batch(source=source2, process=_process_from({"E1": "success"}), config=cfg, log=lambda m: None)
    assert summary2.skipped == 1 and summary2.processed == 0


def test_run_batch_aborts_when_ledger_save_fails(tmp_path, monkeypatch):
    # Le registre ne peut pas être écrit (verrou OneDrive/AV) après le 1er mail :
    # le batch doit s'arrêter net plutôt que risquer un retraitement / double
    # soumission sur les mails suivants.
    import formfiller.batch as batch_mod

    calls = []

    def process(email):
        calls.append(email.entry_id)
        return _Result("success")

    def fail_save(path, ids):
        raise OSError("locked (OneDrive/AV)")

    monkeypatch.setattr(batch_mod, "save_ledger", fail_save)
    source = FakeEmailSource([_msg("E1"), _msg("E2")])
    run_batch(source=source, process=process, config=_cfg(tmp_path), log=lambda m: None)

    assert calls == ["E1"]     # E2 jamais traité : arrêt après l'échec d'écriture
    assert source.moves == []  # arrêt avant tout déplacement


def test_auto_confirm_always_true():
    from formfiller.batch import _auto_confirm
    assert _auto_confirm("prêt à soumettre") is True
