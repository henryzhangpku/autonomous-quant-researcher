# 0DTE both-sides + TA round — findings (2026-08-22)

One week of campaigns run through the autoresearch loop (local Qwen proposer,
frozen validators, append-only ledgers). This note covers: the engine work
that unblocked windowed datasets, the alpha campaigns, and two designed
verifications. Ledgers: `.research/runs/<slug>/`; findings:
`.research/verifications/`.

## Engine work (uncommitted patches — commit before the next big pull)

- `bars_universe.py`: candidate preflight now derives its feature whitelist
  from the hash-pinned dataset rows when available, falling back to the
  daily-bars fixture. Windowed crypto sessions and 0DTE spread grids became
  launchable. Regression: `test_preflight_accepts_dataset_native_features_when_rows_given`.
- `prepare_bars_universe.py`: crypto snapshots no longer drop the drift
  benchmark — it is fetched through the equity bar path and its closed
  sessions (weekends) take a zero return. Regression:
  `test_crypto_benchmark_fetched_via_equity_path_and_closed_sessions_zero_filled`.
- `prepare_intraday_sessions.py` (new): RTH session rows (decision 10:30 ET,
  VWAP distance, opening-range state, prior-day context), causality-tested.
  Staged SPY/QQQ/IWM 2023-08 → 2026-08 (2,232 rows).
- `pine_export.py` (new): AST translator from accepted candidates to
  installable Pine Script v5 strategies, evidence header included.
- `prepare_odte_credit.py`: `--side {put,call}` + pct-defined strike mode;
  put path byte-identical (proven by test against HEAD).
- `verify_odte_economics.py` (new): walks every staged odte snapshot and
  measures win rate / ROI / tails / splits.

## Alpha campaigns (all terminal, ledgers preserved)

| Mission | Trials | Discovery best | Validation | Verdict |
|---|---:|---:|---|---|
| spy-0dte-v2 (options_v2) | 40 | −1.46 | — | no survivor |
| overnight-spx-v2 (fresh ES tape) | 40 | −10.0 | — | no survivor |
| btc swing (absolute) | 24+19 | +4.64 / +1.23 accepted | −1.95 / −1.06 Sharpe | both rejected — bull-window beta |
| btc-swing-ibit-rel | 15 | +4.61 accepted | **passed, Sharpe 1.12** | holdout: positive economics (+12.9 bps/trade, Sharpe 1.11) but failed `positive_both_halves` + `survives_without_best_day` → not promotable |
| connors × 4 | 24 each | best +2.10 (oversold) | −2.19 Sharpe | book's 1995-2007 edge does not survive 2023-2026, benchmark-relative, 1-day horizon |
| ta × 4 daily | 24 each | best +1.37 (channel-reversion) | Sharpe 2.14 but breadth 5<6 | near-miss, not promotable |
| ta-intraday orb / vwap | 24 each | −0.25 / −36 | — | no survivors |
| btc overnight / weekend / monday-rth | 24-34 each | best +1.73 accepted | −5.9 Sharpe | no deployable BTC windowed edge in price features |
| **qqq-0dte-credit** | 13 | **+2.94 accepted** | **passed, Sharpe 3.34, all 9 gates** | **survivor — holdout sealed** |
| mag7-friday-0dte (v1+v2) | 4+12 | +2.18 / +2.14 | 8/9 gates twice, Sharpe ~1.0 | rejected `positive_both_halves` both times — real but uneven |
| iwm-0dte-credit | 19 | +1.92 accepted | −0.30 Sharpe | rejected |

**The one live candidate**: sell the QQQ 0DTE near-money put credit spread on
Monday/Tuesday when credit_frac > 0.10, flat otherwise. Real option bars,
+5.7% of width/trade discovery, +4.8% validation, survives best-day removal
and doubled costs in both. Holdout (2026-02 → 2026-08) unspent, awaiting an
explicit decision. Pine export: `.research/exports/qqq-0dte-credit-survivor.pine`.

**Recurring failure mode worth internalizing**: every BTC winner was long beta
in a bull window until the IBIT benchmark priced it out; every dip-buying rule
(Connors or otherwise) caught the knife out of sample. The gate battery earned
its keep three separate times this week.

## Verification: 0DTE credit spreads, both sides, 12 markets

`finding--verify--odte-credit-both-sides-v1`. Real OPRA bars (Alpaca),
2024-01 → 2026-08, 10:00 ET entry, settled at intrinsic, 50 bps cost, all
figures % of spread width per trade.

**No market or side clears +10% net expectancy.** Credits collected reach
20-32% of width on single names (AVGO/META/TSLA) but tails eat them (p05 ≈
−70% of width). Best net: GOOGL put +3.67%, AVGO put +3.64%, MSFT call
+3.03%. Best risk-adjusted: IBIT call (96% win, Sharpe 2.87, thin credits).

**Side asymmetry is name-specific**: puts win GOOGL/QQQ/IWM/AAPL/AVGO; calls
win AMZN/MSFT/NVDA/META/IBIT; TSLA is weak both ways. META puts are −1.96%
net while its calls are +2.84% — blind put-selling on the wrong name loses.

SPY geometry both sides (see also `strike-distance-balance-v1`): the w5o5
balance point holds (95% win, +0.70 put / +0.33 call); w2o5 call is the
quiet standout (96% win, +1.14 net, Sharpe 1.28); w5o10 wins 98% and loses
money after cost on both sides.

## Limitations

- 0DTE costs are a flat 50 bps of width assumption, not measured from fills.
- Single-name daily expiries were Mon/Wed/Fri-only for most of the window;
  ~460 no-option-bar weekdays per name are recorded as skips, not hidden.
- IBIT options listed 2024-11-19 — its sample is ~108 sessions.
- Loop candidates use dataset-native features only; the odte rows carry
  credit_frac + day_of_week (+ spy_session_ret unused by contract), so timing
  research on these grids is thin by construction.
- Backtests are hypothetical, research-only evidence and are not investment
  advice.

## Follow-ups

1. Commit the engine patches (they have survived one pull already by luck).
2. Decide the qqq-0dte-credit holdout spend.
3. If the single-name side asymmetry is real edge and not regime, a
   preregistered per-name side-selection campaign is the honest next test.
4. Restage odte snapshots with END=2026-08-22 to pick up the final Friday.
