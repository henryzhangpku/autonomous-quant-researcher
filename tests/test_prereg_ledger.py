"""The committed pre-registration verifies, and any edit to it fails closed."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from research.prereg import gpu_signal, ledger
from research.prereg.gpu_leads_revisions import BOUND_CODE, MISSION_DIR, load_spec
from research.v3.campaign import _sha256


def committed_event() -> dict:
    lines = (MISSION_DIR / ledger.LEDGER_NAME).read_bytes().splitlines()
    assert lines, "the mission ledger opens with the primary freeze event"
    return json.loads(lines[0])


def test_recomputed_hashes_match_the_committed_ledger_entry() -> None:
    event = committed_event()
    payload = event["payload"]
    assert event["type"] == ledger.FREEZE_EVENT and event["previous_event_hash"] is None
    assert payload["spec_sha256"] == _sha256(json.loads(
        (MISSION_DIR / "preregistration.json").read_text(encoding="utf-8")))
    assert payload["document_sha256"] == ledger.text_sha256(MISSION_DIR / "PREREGISTRATION.md")
    assert sorted(payload["code_sha256"]) == sorted(BOUND_CODE)
    body = {key: value for key, value in event.items() if key != "event_hash"}
    assert event["event_hash"] == _sha256(body)
    frozen = ledger.verify(MISSION_DIR)
    assert frozen.event_hash == event["event_hash"]
    assert event["created_at"].startswith("2026-10-08")


def test_signal_constants_are_the_ones_frozen_in_the_spec() -> None:
    signal = load_spec()["signal"]
    assert signal["lookback_sessions"] == gpu_signal.LOOKBACK_SESSIONS
    assert signal["anchor_tolerance_sessions"] == gpu_signal.ANCHOR_TOLERANCE_SESSIONS
    assert signal["min_window_coverage"] == gpu_signal.MIN_WINDOW_COVERAGE
    assert signal["max_staleness_sessions"] == gpu_signal.MAX_STALENESS_SESSIONS


@pytest.fixture()
def mission_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch copy of the mission and its bound code, so edits stay local."""
    root = tmp_path / "repo"
    target = root / "research" / "missions" / MISSION_DIR.name
    shutil.copytree(MISSION_DIR, target)
    for name in BOUND_CODE:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ledger.ROOT / name, root / name)
    monkeypatch.setattr(ledger, "ROOT", root)
    ledger.verify(target)
    return target


@pytest.mark.parametrize("name", ["preregistration.json", "PREREGISTRATION.md"])
def test_editing_a_frozen_file_breaks_the_check(mission_copy: Path, name: str) -> None:
    path = mission_copy / name
    if name.endswith(".json"):
        spec = json.loads(path.read_text(encoding="utf-8"))
        spec["campaign_1"]["primary_outcome"]["horizon_weeks"] = 4
        path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    else:
        path.write_text(path.read_text(encoding="utf-8").replace("8-week", "4-week"),
                        encoding="utf-8")
    with pytest.raises(ledger.PreregistrationError, match=name):
        ledger.verify(mission_copy)


def test_reformatting_the_spec_or_changing_line_endings_does_not(mission_copy: Path) -> None:
    spec_path = mission_copy / "preregistration.json"
    spec_path.write_text(json.dumps(json.loads(spec_path.read_text(encoding="utf-8"))),
                         encoding="utf-8")
    doc = mission_copy / "PREREGISTRATION.md"
    doc.write_bytes(doc.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    ledger.verify(mission_copy)


def test_editing_bound_code_breaks_the_check(mission_copy: Path) -> None:
    gates = ledger.ROOT / "research" / "prereg" / "gates.py"
    gates.write_text(gates.read_text(encoding="utf-8").replace(
        "BOOTSTRAP_ALPHA = 0.05", "BOOTSTRAP_ALPHA = 0.10"), encoding="utf-8")
    with pytest.raises(ledger.PreregistrationError, match="gates.py"):
        ledger.verify(mission_copy)


def test_editing_the_ledger_breaks_the_chain(mission_copy: Path) -> None:
    path = mission_copy / ledger.LEDGER_NAME
    lines = path.read_bytes().splitlines()
    event = json.loads(lines[0])
    event["created_at"] = "2026-01-01T00:00:00+00:00"
    lines[0] = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(b"\n".join(lines) + b"\n")
    with pytest.raises(ledger.PreregistrationError, match="integrity hash"):
        ledger.verify(mission_copy)


def test_a_preregistration_freezes_once(mission_copy: Path) -> None:
    with pytest.raises(ledger.AlreadyFrozen):
        ledger.freeze(mission_copy, BOUND_CODE)
    fresh = mission_copy.parent / "fresh"
    fresh.mkdir()
    for name in (ledger.SPEC_NAME, ledger.DOCUMENT_NAME):
        shutil.copy2(mission_copy / name, fresh / name)
    stamp = datetime(2026, 10, 8, tzinfo=timezone.utc)
    frozen = ledger.freeze(fresh, BOUND_CODE, created_at=stamp)
    assert ledger.verify(fresh) == frozen
    with pytest.raises(ledger.PreregistrationError, match="exactly one freeze event"):
        ledger.verify(mission_copy.parent)


# ── judged once ─────────────────────────────────────────────────────────────

def _ready(monkeypatch: pytest.MonkeyPatch, verdict: str) -> None:
    """Pretend every requirement is met and the locked evaluation returned ``verdict``.

    The result object is a TEST FIXTURE; no evaluation runs on any data.
    """
    from research.prereg import gpu_campaign
    from research.prereg import gpu_leads_revisions as mission
    from research.prereg.requirements import RequirementsReport

    monkeypatch.setattr(mission, "check_campaign_1",
                        lambda data, spec: RequirementsReport("campaign_1", ()))
    monkeypatch.setattr(gpu_campaign, "evaluate_campaign_1", lambda inputs: gpu_campaign.CampaignResult(
        "campaign_1", gpu_campaign.CampaignVerdict(verdict), "discovery", {}))


def _no_data():
    from datetime import date

    from research.prereg.gpu_leads_revisions import AvailableData
    return AvailableData(as_of=date(2026, 10, 8), tape=None)


def test_a_campaign_is_judged_once_and_the_verdict_is_returned_as_recorded(
        mission_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from research.prereg import gpu_campaign
    from research.prereg import gpu_leads_revisions as mission

    _ready(monkeypatch, "FAIL")
    first = mission.run(_no_data(), mission_copy)
    assert isinstance(first, mission.Judgement)
    assert first.campaign_1["verdict"] == "FAIL" and first.campaign_2 is None
    types = [event["type"] for event in ledger.read_events(mission_copy / ledger.LEDGER_NAME)]
    assert types == [ledger.FREEZE_EVENT, ledger.TRACK_EVENT, ledger.OPENED_EVENT, ledger.VERDICT_EVENT]

    def must_not_run(inputs):
        raise AssertionError("a recorded campaign must not be evaluated again")

    monkeypatch.setattr(gpu_campaign, "evaluate_campaign_1", must_not_run)
    assert mission.run(_no_data(), mission_copy) == first
    with pytest.raises(ledger.PreregistrationError, match="sealed until campaign_1 passes"):
        ledger.open_evaluation(mission_copy, "campaign_2", requires_pass="campaign_1")


def test_a_crash_after_the_receipt_consumes_the_evaluation(
        mission_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from research.prereg import gpu_campaign
    from research.prereg import gpu_leads_revisions as mission
    from research.prereg.requirements import Refusal

    _ready(monkeypatch, "PASS")

    def crash(inputs):
        raise RuntimeError("evaluator crashed")

    monkeypatch.setattr(gpu_campaign, "evaluate_campaign_1", crash)
    with pytest.raises(RuntimeError, match="evaluator crashed"):
        mission.run(_no_data(), mission_copy)
    again = mission.run(_no_data(), mission_copy)
    assert isinstance(again, Refusal) and "consumed" in again.reason
    with pytest.raises(ledger.AlreadyOpened):
        ledger.open_evaluation(mission_copy, "campaign_1")


def test_after_a_campaign_1_pass_campaign_2_still_refuses_without_its_data(
        mission_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from research.prereg import gpu_leads_revisions as mission
    from research.prereg.requirements import Refusal

    _ready(monkeypatch, "PASS")
    judged = mission.run(_no_data(), mission_copy)
    assert isinstance(judged, mission.Judgement) and judged.campaign_1["verdict"] == "PASS"
    assert isinstance(judged.campaign_2, Refusal)
    assert judged.campaign_2.missing_keys == ("campaign_2_total_return_closes",)
    assert ledger.opened(mission_copy) == {"campaign_1"}


def test_the_committed_demo_snapshot_carries_the_ledger_hash_and_no_result() -> None:
    snapshot = json.loads((ledger.ROOT / "demo" / "data" / "gpu-leads-revisions.json")
                          .read_text(encoding="utf-8"))
    frozen = ledger.verify(MISSION_DIR)
    assert snapshot["preregistration"]["ledger_event_hash"] == frozen.event_hash
    assert snapshot["preregistration"]["spec_sha256"] == frozen.spec_sha256
    assert snapshot["spec"] == load_spec()
    assert snapshot["campaign_1"]["refused"] is True and snapshot["campaign_2"]["refused"] is True
    assert "verdict" not in json.dumps(snapshot["campaign_1"])
