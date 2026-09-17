"""Fixed SPY options validator for NLP-generated candidate modules."""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Sequence

from research.backtest import BacktestEngine, Result, Session, SessionStore, Structure
from research.backtest.data import CandidateSessionView


SPLITS = {
    "discovery": ("2016-01-01", "2021-12-31"),
    "validation": ("2022-01-01", "2024-12-31"),
    "holdout": ("2025-01-01", "9999-12-31"),
}
MIN_SIGNAL_TRADES = 50


@dataclass(frozen=True)
class CandidateContract:
    entry_hm: tuple[int, int]
    signal: object
    structure: object
    label: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate one generated options candidate.")
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--split", choices=tuple(SPLITS), default="validation")
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Check the generated contract without loading market data or scoring it.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.preflight_only:
            payload = preflight_candidate(args.candidate)
        else:
            sessions = SessionStore.load(args.symbol).sessions()
            payload = evaluate_candidate(args.candidate, sessions, split=args.split)
    except Exception as exc:  # noqa: BLE001 - candidate failures are validator evidence
        print(json.dumps({"error": str(exc), "status": "failed"}, sort_keys=True))
        return 2

    print(json.dumps(payload, sort_keys=True))
    if not args.preflight_only:
        print(f"score={payload['score']:.6f}")
    return 0


def preflight_candidate(candidate_path: Path) -> dict[str, object]:
    """Exercise the generated interface on deterministic, data-free sessions."""

    contract = _load_contract(candidate_path)
    _validate_callable_signature(contract.signal, "signal", ("session", "entry_minute"))
    _validate_callable_signature(contract.structure, "structure", ("session", "entry_price"))
    entry_minute = contract.entry_hm[0] * 60 + contract.entry_hm[1]
    if not 9 * 60 + 30 <= entry_minute <= 15 * 60 + 55 or entry_minute % 5:
        raise TypeError("candidate ENTRY_HM must identify a five-minute bar from 09:30 to 15:55 ET")

    for fixture_name, session in _preflight_sessions(entry_minute):
        entry_price = session.price_at(entry_minute)
        if entry_price is None:  # pragma: no cover - guarded by the fixture construction
            raise RuntimeError("preflight fixture is missing the candidate entry minute")
        try:
            candidate_session = CandidateSessionView.at(session, entry_minute)
            structure = contract.structure(candidate_session, entry_price)  # type: ignore[operator]
        except Exception as exc:  # noqa: BLE001 - convert generated-code errors to evidence
            raise TypeError(
                f"candidate structure failed preflight on {fixture_name} session: {exc}"
            ) from exc
        _validate_structure(structure)
        try:
            decision = contract.signal(candidate_session, entry_minute)  # type: ignore[operator]
        except Exception as exc:  # noqa: BLE001 - convert generated-code errors to evidence
            raise TypeError(
                f"candidate signal failed preflight on {fixture_name} session: {exc}"
            ) from exc
        if type(decision) is not bool:
            raise TypeError(
                "candidate signal must return bool on every session, "
                f"not {type(decision).__name__}"
            )

    return {"status": "preflight_ok", "label": contract.label}


def evaluate_candidate(
    candidate_path: Path,
    sessions: list[Session],
    *,
    split: str = "validation",
) -> dict[str, object]:
    if split not in SPLITS:
        raise ValueError(f"unknown split: {split}")
    contract = _load_contract(candidate_path)
    start, end = SPLITS[split]
    selected = [session for session in sessions if start <= session.day <= end]
    if not selected:
        raise ValueError(f"no sessions in fixed {split} split")

    result = BacktestEngine().run(
        sessions=selected,
        entry_hm=contract.entry_hm,
        signal=contract.signal,  # type: ignore[arg-type]
        structure=contract.structure,  # type: ignore[arg-type]
        label=contract.label,
    )
    if len(result.rows) < MIN_SIGNAL_TRADES:
        raise ValueError(
            f"candidate produced {len(result.rows)} signal trades; minimum is {MIN_SIGNAL_TRADES}"
        )
    signal_edge = _edge_pp(result, result.rows)
    control_edge = _edge_pp(result, result.control_rows)
    by_year = {
        year: round(_edge_pp(result, [row for row in result.rows if row["date"].startswith(year)]), 6)
        for year in sorted({row["date"][:4] for row in result.rows})
    }
    return {
        "status": "ok",
        "split": split,
        "label": contract.label,
        "signal_trades": len(result.rows),
        "control_trades": len(result.control_rows),
        "signal_edge_pp": round(signal_edge, 6),
        "control_edge_pp": round(control_edge, 6),
        "by_year_edge_pp": by_year,
        "score": round(signal_edge - control_edge, 6),
    }


def _load_contract(path: Path) -> CandidateContract:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    spec = importlib.util.spec_from_file_location("autonomous-quant-researcher_generated_candidate", resolved)
    if spec is None or spec.loader is None:
        raise ValueError(f"could not load candidate: {resolved}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return _contract_from_module(module)


def _contract_from_module(module: ModuleType) -> CandidateContract:
    entry = getattr(module, "ENTRY_HM", None)
    if (
        not isinstance(entry, tuple)
        or len(entry) != 2
        or not all(isinstance(value, int) for value in entry)
        or not (9 <= entry[0] <= 15)
        or not (0 <= entry[1] <= 59)
    ):
        raise TypeError("candidate ENTRY_HM must be a valid (hour, minute) tuple")
    signal = getattr(module, "signal", None)
    structure = getattr(module, "structure", None)
    if not callable(signal) or not callable(structure):
        raise TypeError("candidate must define callable signal(session, minute) and structure(session, price)")
    label = getattr(module, "LABEL", module.__name__)
    if not isinstance(label, str) or not label.strip():
        raise TypeError("candidate LABEL must be a non-empty string")
    return CandidateContract(entry_hm=entry, signal=signal, structure=structure, label=label.strip())


def _validate_callable_signature(function: object, name: str, expected: tuple[str, str]) -> None:
    try:
        parameters = tuple(inspect.signature(function).parameters.values())  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise TypeError(f"candidate {name} must have signature {name}{expected}") from exc
    if (
        tuple(parameter.name for parameter in parameters) != expected
        or any(
            parameter.kind
            not in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
            for parameter in parameters
        )
    ):
        rendered = ", ".join(expected)
        raise TypeError(f"candidate {name} must have exact signature {name}({rendered})")


def _validate_structure(value: object) -> None:
    if not isinstance(value, Structure):
        raise TypeError(
            "candidate structure must return one research.backtest.Structure, "
            f"not {type(value).__name__}"
        )
    if not value.legs:
        raise TypeError("candidate structure must contain at least one option leg")
    if not math.isfinite(value.width) or value.width <= 0:
        raise TypeError("candidate structure width must be a positive finite number")
    for leg in value.legs:
        if leg.cp not in {"C", "P"} or not math.isfinite(leg.strike) or not leg.qty:
            raise TypeError("candidate structure contains an invalid option leg")


def _preflight_sessions(entry_minute: int) -> tuple[tuple[str, Session], ...]:
    minutes = tuple(range(9 * 60 + 30, 16 * 60, 5))

    def make(day: str, slope: float, high_offset: float) -> Session:
        bars = []
        for index, minute in enumerate(minutes):
            price = 500.0 + slope * index
            bars.append((minute, price, price + high_offset, price - 0.2, price))
        return Session(day=day, bars=tuple(bars))

    sparse_prices = {
        9 * 60 + 30: 500.0,
        entry_minute: 499.5,
        15 * 60 + 55: 500.5,
    }
    sparse = Session(
        day="2024-01-04",
        bars=tuple(
            (minute, price, price + 0.1, price - 0.1, price)
            for minute, price in sorted(sparse_prices.items())
        ),
    )

    return (
        ("rising", make("2024-01-02", 0.05, 1.0)),
        ("falling", make("2024-01-03", -0.05, 0.01)),
        ("sparse", sparse),
    )


def _edge_pp(result: Result, rows: list[dict]) -> float:
    if not rows:
        return float("-inf")
    full_win = sum(bool(row["full_win"]) for row in rows) / len(rows)
    mean_dw = sum(float(row["dw"]) for row in rows) / len(rows)
    return (full_win - mean_dw - float(result.meta["cost_frac"])) * 100.0


if __name__ == "__main__":
    sys.exit(main())
