from pathlib import Path
from formfiller.models import DecisionRecord
from formfiller.decision_log import write_decisions_md


def _rec(**kw):
    base = dict(question_id="q1", label="Quel est votre SIREN ?", type="text",
                required=True, profile_field="siren", value="987654321",
                propose_status="matched", propose_confidence=0.9,
                propose_rationale="SIREN question -> siren",
                final_status="matched", final_confidence=0.97,
                verify_rationale="correct; not SIRET", final_action="fill")
    base.update(kw)
    return DecisionRecord(**base)


def test_writes_markdown_with_reasoning(tmp_path):
    path = write_decisions_md(str(tmp_path), "E1", "Adisseo", "https://x", (_rec(),))
    assert path is not None and Path(path).exists()
    text = Path(path).read_text(encoding="utf-8")
    assert "Quel est votre SIREN ?" in text
    assert "siren" in text
    assert "correct; not SIRET" in text
    assert "fill" in text


def test_write_failure_returns_none_does_not_raise(tmp_path):
    # point at a path that cannot be a directory (a file occupies it)
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    result = write_decisions_md(str(blocker), "E1", "t", "u", (_rec(),))
    assert result is None
