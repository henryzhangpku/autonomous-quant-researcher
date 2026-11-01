import json
from pathlib import Path

import pytest

from research.prereg import ledger


def _mission(tmp_path: Path) -> Path:
    md = tmp_path / "m"
    md.mkdir()
    (md / ledger.SPEC_NAME).write_text(json.dumps({"mission": "m"}), encoding="utf-8")
    (md / ledger.DOCUMENT_NAME).write_text("doc\n", encoding="utf-8")
    ledger.freeze(md, [])
    return md


def test_stage_read_is_recorded_once_and_chained(tmp_path):
    md = _mission(tmp_path)
    ledger.record_stage_read(md, "c1", {"discovery": {"verdict": "passed"}}, inputs={"a": "x"},
                             sealed=["holdout"])
    events = ledger.read_events(md / ledger.LEDGER_NAME)
    assert events[-1]["type"] == ledger.STAGE_READ_EVENT
    assert events[-1]["payload"]["sealed"] == ["holdout"]
    with pytest.raises(ledger.PreregistrationError):
        ledger.record_stage_read(md, "c1", {}, inputs={}, sealed=["holdout"])


def test_stage_read_leaves_the_verdict_available(tmp_path):
    md = _mission(tmp_path)
    ledger.record_stage_read(md, "c1", {}, inputs={}, sealed=["holdout"])
    ledger.open_evaluation(md, "c1", inputs={})
    ledger.record_verdict(md, "c1", {"verdict": "PASS"})
    assert ledger.verdicts(md)["c1"]["verdict"] == "PASS"


def test_no_stage_read_after_opening(tmp_path):
    md = _mission(tmp_path)
    ledger.open_evaluation(md, "c1", inputs={})
    with pytest.raises(ledger.PreregistrationError):
        ledger.record_stage_read(md, "c1", {}, inputs={}, sealed=["holdout"])
