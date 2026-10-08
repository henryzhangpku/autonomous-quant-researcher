"""The campaign refuses, typed and specific, until every data requirement is met.

Tapes here are hand-written TEST FIXTURES in the published schema, not index
data. Licensed providers are never given fake rows: they refuse.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from research.prereg import gpu_leads_revisions as mission
from research.prereg import xnys
from research.prereg.requirements import Refusal, RequirementState
from research.providers import licensed
from research.providers.gpu_index import load_tape

HEADER = ("index_code,index_date,revision,status,value,provider_count,observation_count,"
          "dispersion,withheld_reason,methodology_version,published_at,superseded_at,"
          "revision_reason,snapshot")
CREDENTIALS = ("CAPIQ_API_KEY", "LSEG_API_KEY", "GPU_RENTAL_HISTORY_API_KEY")


@pytest.fixture(autouse=True)
def _no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CREDENTIALS:
        monkeypatch.delenv(name, raising=False)


def fixture_tape_file(tmp_path: Path, sessions: int, *, start: date = date(2026, 9, 17)) -> Path:
    days = xnys.sessions(start, start + timedelta(days=sessions * 2))[:sessions]
    rows = [HEADER]
    for code in ("GIX-H100", "GIX-H200", "GIX-B200"):
        for i, day in enumerate(days):
            rows.append(f"{code},{day},0,published,{3.0 + 0.01 * i},10,40,0.3,,1.1.0,"
                        f"{day}T21:00:00Z,,,fixture.jsonl.gz")
    path = tmp_path / "index_values.csv"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_today_the_campaign_refuses_and_names_every_missing_input(tmp_path: Path) -> None:
    data = mission.gather(str(fixture_tape_file(tmp_path, 16)), today=date(2026, 10, 8))
    outcome = mission.run(data)
    assert isinstance(outcome, Refusal)
    assert outcome.reason == "data requirements are not met"
    assert set(outcome.missing_keys) == {
        "gpu_rental_history_h100", "consensus_revenue_fy1", "consensus_capex_fy1",
        "index_h100_holdout", "index_h200_holdout", "index_b200_holdout"}
    states = {item.requirement.key: item.state for item in outcome.unmet}
    assert states["gpu_rental_history_h100"] is RequirementState.NOT_LICENSED
    assert states["consensus_revenue_fy1"] is RequirementState.NOT_LICENSED
    assert states["index_h100_holdout"] is RequirementState.MISSING
    history = next(item for item in outcome.unmet if item.requirement.key == "gpu_rental_history_h100")
    assert history.available == 0 and history.requirement.required > 700
    assert outcome.as_dict()["refused"] is True


def test_the_live_signal_is_flagged_insufficient_history(tmp_path: Path) -> None:
    data = mission.gather(str(fixture_tape_file(tmp_path, 16)), today=date(2026, 10, 8))
    snapshot = mission.series_snapshot(data)
    for code in ("H100", "H200", "B200"):
        signal = snapshot[code]["signal"]
        assert signal["status"] == "insufficient_history"
        assert signal["message"] == "insufficient history for the 3-month signal"
        assert (signal["history_sessions"], signal["required_sessions"]) == (16, 64)
        assert signal["earliest_live_session"] is not None


def test_a_missing_tape_is_a_refusal_not_a_crash(tmp_path: Path) -> None:
    data = mission.gather(str(tmp_path / "absent.csv"), today=date(2026, 10, 8))
    assert data.tape is None and "could not read" in (data.tape_error or "")
    outcome = mission.run(data)
    assert isinstance(outcome, Refusal)
    holdout = next(item for item in outcome.unmet if item.requirement.key == "index_h100_holdout")
    assert "could not read" in holdout.detail


def test_holdout_weeks_accrue_only_after_the_freeze_with_a_closed_window(tmp_path: Path) -> None:
    # A fixture tape long enough for the signal to be live through mid-2027.
    path = fixture_tape_file(tmp_path, 260, start=date(2026, 9, 17))
    report = mission.check_campaign_1(mission.gather(str(path), today=date(2027, 6, 30)),
                                      mission.load_spec())
    h100 = next(item for item in report.items if item.requirement.key == "index_h100_holdout")
    assert 0 < h100.available < h100.requirement.required
    assert h100.state is RequirementState.PARTIAL and not report.ready


def test_an_unverifiable_preregistration_is_refused_before_any_data_check(tmp_path: Path) -> None:
    data = mission.gather(str(fixture_tape_file(tmp_path, 16)), today=date(2026, 10, 8))
    outcome = mission.run(data, mission_dir=tmp_path)
    assert isinstance(outcome, Refusal)
    assert outcome.reason.startswith("the pre-registration does not verify") and not outcome.unmet


def test_campaign_2_is_sealed_until_campaign_1_passes(tmp_path: Path) -> None:
    data = mission.gather(str(fixture_tape_file(tmp_path, 16)), today=date(2026, 10, 8))
    report = mission.check_campaign_2(data, mission.load_spec(), None)
    sealed = report.items[0]
    assert sealed.requirement.key == "campaign_1_pass" and not sealed.met
    assert not report.ready


@pytest.mark.parametrize("provider", [licensed.CAPITAL_IQ, licensed.LSEG_IBES,
                                      licensed.GPU_RENTAL_HISTORY])
def test_licensed_providers_declare_a_schema_and_refuse_typed(
        provider: licensed.LicensedProviderStub, monkeypatch: pytest.MonkeyPatch) -> None:
    assert provider.schema and all(len(field) == 2 for field in provider.schema)
    with pytest.raises(licensed.ProviderNotLicensed) as missing:
        provider.fetch(("NVDA",), date(2024, 1, 1), date(2024, 2, 1))
    assert missing.value.credential_present is False
    secret = "fixture-not-a-real-key"
    monkeypatch.setenv(provider.credential_env, secret)
    with pytest.raises(licensed.ProviderNotAvailable) as unbundled:
        provider.fetch(("NVDA",), date(2024, 1, 1), date(2024, 2, 1))
    assert not isinstance(unbundled.value, licensed.ProviderNotLicensed)
    assert secret not in str(unbundled.value)
    availability = provider.availability()
    assert availability["available"] is False and secret not in repr(availability)


def test_the_estimate_schema_is_point_in_time_and_pins_the_fiscal_year() -> None:
    names = [name for name, _ in licensed.ESTIMATE_SCHEMA]
    assert {"as_of", "fiscal_year_end", "consensus_mean", "contributor_count"} <= set(names)


def test_status_snapshot_carries_the_ledger_hash_and_no_metrics(tmp_path: Path) -> None:
    data = mission.gather(str(fixture_tape_file(tmp_path, 16)), today=date(2026, 10, 8))
    snapshot = mission.status_snapshot(data)
    assert snapshot["preregistration"]["ledger_event_hash"] == mission.ledger.verify(
        mission.MISSION_DIR).event_hash
    assert snapshot["campaign_1"]["refused"] is True
    assert snapshot["campaign_2"]["reason"] == "sealed until campaign 1 passes"
    assert load_tape(data.tape.source).sha256 == snapshot["tape"]["sha256"]
