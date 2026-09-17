"""Compile a spoken trade idea into a runnable lab mission.

The human speaks an idea in natural language; this module grounds it into the
bars-universe family: a frozen data snapshot, a frozen evaluator spec, and a
mission + policy pair the existing lab loop runs unchanged. Compilation is
deterministic slot-filling -- no LLM sits between the human's words and the
evaluation surface, so the same idea always compiles to the same mission.

Ideas the family cannot ground (options flow, dark pool, 0DTE structures,
futures) are refused with an explanation instead of silently approximated,
per the no-silent-fallback rule.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from research.experiments.bars_feature_spec import FEATURE_NAMES

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MISSION_ROOT = REPOSITORY_ROOT / ".research" / "missions"
DEFAULT_DATA_ROOT = REPOSITORY_ROOT / ".research" / "data" / "bars"

# The features a candidate may read, straight from the data contract — never
# a second hand-kept copy.
FEATURES = FEATURE_NAMES

UNIVERSE_ALIASES: dict[str, tuple[str, ...]] = {
    "mag 7": ("AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"),
    "mag7": ("AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"),
    "magnificent 7": ("AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"),
    "magnificent seven": ("AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"),
    "faang": ("AAPL", "AMZN", "GOOGL", "META", "NFLX"),
    "index etfs": ("SPY", "QQQ", "IWM", "DIA"),
}

# SOL is in the house universe (the overnight/crypto mandate names BTC, ETH
# and SOL) but was missing here, so "does solana keep falling…" compiled to
# NO tradeable symbol and died at launch — a backlog question that could
# never run, failing three times into the engine's own stand-down latch
# (found by compiling the whole backlog, 2026-08-15).
CRYPTO_TOKENS = {
    "BTC": "BTC/USD", "BITCOIN": "BTC/USD",
    "ETH": "ETH/USD", "ETHEREUM": "ETH/USD",
    "SOL": "SOL/USD", "SOLANA": "SOL/USD",
}

# Words that look like tickers but are English. Extend as refusals surface.
TICKER_STOPWORDS = {
    "A", "I", "ON", "IN", "AT", "TO", "OF", "OR", "AND", "THE", "FOR", "BUY",
    "SELL", "LONG", "SHORT", "DIP", "DAY", "DAYS", "WEEK", "WITH", "WHEN",
    "IF", "IS", "IT", "UP", "DOWN", "GO", "GOES", "NEXT", "OPEN", "CLOSE",
    "HIGH", "LOW", "NEW", "ALL", "ETF", "ETFS", "VS", "VOL", "GAP", "RSI",
    "EW", "PM", "AM", "EST", "ET", "PT", "USD", "MA", "OVER", "UNDER", "TEST",
    "BPS", "PCT", "AVG", "MEAN", "STD", "DTE", "AFTER", "BEFORE",
    # Event vocabulary is a calendar reference, not a ticker — now that the
    # macro calendar compiles, "SPY after an FOMC day" must not buy FOMC.
    "FOMC", "CPI", "NFP",
}

UNSUPPORTED_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"options?\s+flow|\bcf\b|sweep|unusual\s+options",
     "options-flow features (proprietary flow families are not bundled in this repository)"),
    (r"dark\s*pool", "dark-pool features (proprietary flow families are not bundled in this repository)"),
    (r"\b0\s*dte\b|zero\s*dte|weekly\s+options?|option\s+spread|credit\s+spread|debit\s+spread|iron\s+condor|straddle|strangle",
     "option structures (use the canonical SPY v3 program for defined-risk 0DTE/weekly research)"),
    (r"\bfutures?\b|\b/es\b|\b/nq\b", "futures data (Polygon/Massive futures support is not wired into this family yet)"),
    (r"\bintraday\b|\bminute\b|\b5[- ]?min\b|\bhourly\b|\btick\b",
     "intraday decision times (this family decides once per session at the close; intraday entry windows are not built yet)"),
    # FOMC/CPI/NFP vocabulary compiled nothing before the macro calendar was
    # staged (research/data/macro_events.json, 2026-08-16); with event_today
    # and event_next_session in the contract, "the day after an FOMC day" is
    # expressible. Earnings and news/sentiment stay refused — no data.
    (r"\bearnings\b|\bnews\b|\bsentiment\b",
     "earnings/news/sentiment features (only the FOMC/CPI/NFP macro calendar "
     "is staged for this family, as event_today/event_next_session; there is "
     "no earnings calendar or news/sentiment feed)"),
    # Without this refusal, "long SPY under the call wall" compiled into a
    # plain daily-bars mission with NO gamma data in it at all — the silent
    # approximation this module exists to refuse (found 2026-08-15).
    (r"\bgex\b|gamma\s+exposure|gamma\s+wall|call\s+wall|put\s+wall|zero\s+gamma|gamma\s+flip|dealer\s+gamma",
     "gamma-exposure features (GEX has no historical archive and is never "
     "backfilled; it is captured forward — see the gex-regime forward "
     "verification and mission)"),
)


class IdeaCompileError(ValueError):
    """The idea cannot be grounded honestly; the message says exactly why."""


@dataclass(frozen=True)
class CompiledIdea:
    slug: str
    title: str
    idea_text: str
    universe: tuple[str, ...]
    asset_class: str
    start: str
    end: str
    payoff: str
    mission_dir: Path
    mission_path: Path
    policy_path: Path
    spec_path: Path
    data_dir: Path
    manifest: dict[str, Any]

    def preview(self) -> dict[str, Any]:
        return {
            "slug": self.slug, "title": self.title, "idea": self.idea_text,
            "universe": list(self.universe), "asset_class": self.asset_class,
            "data_range": [self.start, self.end], "payoff": self.payoff,
            "splits": self.manifest["suggested_splits"],
            "session_count": self.manifest["session_count"],
            "mission": self.mission_path.read_text(encoding="utf-8"),
            "mission_path": str(self.mission_path),
            "policy_path": str(self.policy_path),
        }


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:60] or "idea"


def _detect_unsupported(idea: str) -> None:
    lowered = idea.lower()
    problems = [reason for pattern, reason in UNSUPPORTED_PATTERNS if re.search(pattern, lowered)]
    if problems:
        raise IdeaCompileError(
            "This idea needs " + "; ".join(problems) + ". "
            "The daily-bars family evaluates end-of-day decisions on Alpaca "
            "equity/crypto bars only. Rephrase within that surface or request "
            "the named family."
        )


def _parse_universe(idea: str) -> tuple[tuple[str, ...], str]:
    lowered = idea.lower()
    symbols: list[str] = []
    for alias, members in UNIVERSE_ALIASES.items():
        if alias in lowered:
            symbols.extend(members)
    crypto: list[str] = []
    for token in re.findall(r"\$?\b[A-Z]{1,5}\b", idea):
        ticker = token.lstrip("$")
        if ticker in CRYPTO_TOKENS:
            crypto.append(CRYPTO_TOKENS[ticker])
        elif ticker not in TICKER_STOPWORDS and (token.startswith("$") or len(ticker) >= 2):
            symbols.append(ticker)
    for word, pair in (("bitcoin", "BTC/USD"), ("ethereum", "ETH/USD"), ("solana", "SOL/USD")):
        if word in lowered:
            crypto.append(pair)
    symbols = list(dict.fromkeys(symbols))
    crypto = list(dict.fromkeys(crypto))
    if symbols and crypto:
        raise IdeaCompileError(
            "The idea mixes equities and crypto; run them as two missions so "
            "each universe keeps its own calendar and cost model."
        )
    if crypto:
        return tuple(crypto), "crypto"
    if symbols:
        return tuple(symbols), "equity"
    raise IdeaCompileError(
        "No tradeable symbols recognized. Name tickers explicitly (SPY, QQQ, "
        "$NVDA, BTC) or a known group (mag 7)."
    )


def _parse_range(idea: str, today: date) -> tuple[str, str]:
    lowered = idea.lower()
    end = today - timedelta(days=1)
    match = re.search(r"(?:last|past|trailing)\s+(\d+)\s+year", lowered)
    if match:
        return (end - timedelta(days=365 * int(match.group(1)))).isoformat(), end.isoformat()
    match = re.search(r"(?:last|past|trailing)\s+(\d+)\s+month", lowered)
    if match:
        return (end - timedelta(days=30 * int(match.group(1)))).isoformat(), end.isoformat()
    match = re.search(r"since\s+(20\d{2})", lowered)
    if match:
        return f"{match.group(1)}-01-01", end.isoformat()
    return (end - timedelta(days=730)).isoformat(), end.isoformat()


def _parse_payoff(idea: str) -> str:
    if re.search(r"overnight|close[- ]to[- ]close|hold\s+overnight|swing", idea.lower()):
        return "next_close_to_close"
    return "next_open_to_close"


def _sample_gates(universe_size: int) -> dict[str, Any]:
    if universe_size == 1:
        return {
            "min_trades": {"discovery": 40, "validation": 15, "holdout": 15},
            "min_sessions": {"discovery": 20, "validation": 10, "holdout": 10},
            "min_symbols": 1, "max_concentration": 1.0,
        }
    return {
        "min_trades": {"discovery": 80, "validation": 35, "holdout": 35},
        "min_sessions": {"discovery": 20, "validation": 10, "holdout": 10},
        "min_symbols": min(6, universe_size),
        "max_concentration": max(0.35, round(1.5 / universe_size, 2)),
    }


def _prior_evidence_section(universe: tuple[str, ...]) -> str:
    """The lab's settled results, rendered into the mission the proposer
    reads. Empty when the lab knows nothing — and empty on ANY failure,
    because knowledge must never block a mission from compiling."""
    from research.lab.knowledge import prior_evidence

    try:
        block = prior_evidence(universe)
    except Exception:  # noqa: BLE001
        return ""
    return f"{block}\n\n" if block else ""


def _mission_text(idea: str, universe: tuple[str, ...], payoff: str, spec: dict[str, Any]) -> str:
    payoff_text = (
        "the next session, open to close"
        if payoff == "next_open_to_close"
        else "the next session, close to close"
    )
    return f"""The human's research idea, verbatim:

> {idea.strip()}

Ground this idea in evidence. Do not force a result or optimize one ticker;
prefer simple hypotheses that survive across symbols, time halves, best-day
removal, and doubled costs. A robust negative is a valid finding.

Universe: {", ".join(universe)}. The candidate decides once per session using
only that session's closing feature snapshot; the position is held for
{payoff_text}. The evaluator forms an equal-weight daily portfolio so
correlated same-day observations do not masquerade as independent samples.

Return one Python candidate implementing exactly:

- `LABEL`: a short label.
- `signal(symbol, features) -> int`: `1` for long, `-1` for short, or `0` for
  no position.

Candidate code may import only `math` or `statistics`. It cannot load data,
access files or networks, calculate returns, choose costs or splits, or score
itself.

Available features (computed strictly from bars up to the decision session,
plus the published-in-advance macro event calendar): {", ".join(FEATURES)}. Returns and distances are fractions
(0.01 = 1%). `range_pos` is the close's position inside the session range
(0 = low, 1 = high). `day_of_week` is 0.0 (Monday) through 4.0 (Friday) for
equities. `event_today` is 1.0 when the decision session itself has a
scheduled FOMC/CPI/NFP release — known in advance, not a lookahead.
`event_next_session` is 1.0 when the next session has a scheduled FOMC/CPI/NFP
release — known in advance, not a lookahead.

{_prior_evidence_section(universe)}The block below shows only the required shape. It is deliberately unrelated
to the idea above: do not submit it, and do not submit a rewording of it.
Every trial must be a materially different rule -- different features,
thresholds, or direction logic. Repeating an earlier formulation is rejected
without spending a trial, and three such rejections end the run.

```python
LABEL = "shape example only -- do not submit"

def signal(symbol, features):
    if features["vol_ratio_5_20"] > 1.4 and features["day_of_week"] == 0.0:
        return -1
    return 0
```

Sample gates for this mission: at least {spec["min_trades"]["discovery"]}
discovery trades across at least {spec["min_sessions"]["discovery"]} sessions,
{spec["min_symbols"]}+ active symbols, and no symbol above
{spec["max_concentration"]:.0%} of trades. Performance gates: positive net
mean, positive in both halves, survives best-day removal and doubled costs.
"""


def compile_idea(
    idea: str,
    *,
    title: str | None = None,
    mission_root: Path = DEFAULT_MISSION_ROOT,
    data_root: Path = DEFAULT_DATA_ROOT,
    today: date | None = None,
    prepare: Callable[..., dict[str, Any]] | None = None,
    target_sharpe: float = 0.75,
    cost_bps: float = 5.0,
    max_attempts: int = 24,
    min_attempts: int = 12,
) -> CompiledIdea:
    idea = " ".join(idea.split())
    if len(idea) < 12:
        raise IdeaCompileError("Describe the idea in at least a short sentence.")
    _detect_unsupported(idea)
    universe, asset_class = _parse_universe(idea)
    start, end = _parse_range(idea, today or date.today())
    payoff = _parse_payoff(idea)
    clean_title = " ".join((title or idea).split())[:120]
    slug = _slugify(clean_title)

    if prepare is None:
        from research.experiments.prepare_bars_universe import prepare as prepare_snapshot
        prepare = prepare_snapshot
    data_dir = data_root / slug
    manifest = prepare(symbols=universe, start=start, end=end, out=data_dir,
                       asset_class=asset_class)

    gates = _sample_gates(len(universe))
    spec_payload = {
        "name": slug,
        "universe": list(universe),
        "splits": manifest["suggested_splits"],
        **gates,
        "cost_bps": cost_bps,
        "target_sharpe": target_sharpe,
        "payoff": payoff,
    }
    mission_dir = mission_root / slug
    mission_dir.mkdir(parents=True, exist_ok=True)
    spec_path = mission_dir / "spec.json"
    spec_bytes = (json.dumps(spec_payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    spec_path.write_bytes(spec_bytes)
    spec_hash = hashlib.sha256(spec_bytes).hexdigest()

    mission_path = mission_dir / "mission.md"
    mission_path.write_text(_mission_text(idea, universe, payoff, spec_payload), encoding="utf-8")

    data_path = data_dir / "sessions.jsonl"
    command = [
        "python", "-m", "research.validators.bars_universe",
        "--candidate", "{candidate}",
        "--spec", str(spec_path), "--spec-hash", spec_hash,
        "--data", str(data_path), "--data-hash", manifest["data_sha256"],
        "--split", "discovery",
    ]
    policy_path = mission_dir / "policy.json"
    policy_path.write_text(json.dumps({
        "name": slug,
        "validator": {
            "version": "bars-universe-portfolio-v1",
            "command": command,
            "pattern": r"score=(?P<value>-?[0-9]+(?:\.[0-9]+)?)",
            "name": "robust_portfolio_score",
            "direction": "maximize",
            "target_metric": target_sharpe,
        },
        "budget": {
            "timeout_seconds": 60,
            "max_attempts": max_attempts,
            "min_attempts": min_attempts,
        },
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return CompiledIdea(
        slug=slug, title=clean_title, idea_text=idea, universe=universe,
        asset_class=asset_class, start=start, end=end, payoff=payoff,
        mission_dir=mission_dir, mission_path=mission_path,
        policy_path=policy_path, spec_path=spec_path, data_dir=data_dir,
        manifest=manifest,
    )
