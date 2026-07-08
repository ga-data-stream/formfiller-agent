from formfiller import triage
from formfiller.config import AppConfig
from formfiller.email_source import FakeEmailSource
from formfiller.models import EmailMessage


def _cfg():
    return AppConfig(excel_log_path="x.xlsx",
                     processed_subfolder="Traité", review_subfolder="Revue humaine")


def _msg(entry_id):
    return EmailMessage(entry_id=entry_id, sender="a@b.com", subject="s",
                        received="2026-07-07T09:00:00", body_text="t", body_html="<p>t</p>")


def test_target_subfolder_success_goes_to_processed():
    assert triage.target_subfolder("success", _cfg()) == "Traité"


def test_target_subfolder_manual_and_fail_go_to_review():
    assert triage.target_subfolder("manual", _cfg()) == "Revue humaine"
    assert triage.target_subfolder("fail", _cfg()) == "Revue humaine"


def test_route_moves_to_target_and_returns_result():
    source = FakeEmailSource([_msg("E1")])
    assert triage.route(source, "E1", "success", _cfg()) is True
    assert source.moves == [("E1", "Traité")]


def test_route_propagates_move_failure():
    source = FakeEmailSource([_msg("E1")], move_fails=True)
    assert triage.route(source, "E1", "manual", _cfg()) is False
