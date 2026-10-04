# hmm-regime: plan (checkable), per SPEC.md

Approved by Henry 2026-10-04 ("Approve, start phase 1"). Each phase ends with
a review against the spec before the next starts; the backtest result decides
whether anything goes further.

## Phase 1: data + features
- [x] lake backfill: BTC/USD + ETH/USD hourly 2021-01 onward (~50k bars each), SPY/QQQ daily 2015 onward, SPY hourly 2021 onward
- [x] `research/hmm/data.py` reads the lake read-only
- [x] `research/hmm/features.py`: ret, rvol, range, vol_ratio, EMA-spread trend; standardized on the past only
- [x] tests: no feature at t depends on candle t+1; truncation equality; standardization strictly before t; warm-up is NaN, never filled; scale-free
- [ ] review: feature list vs spec, timestamp audit on real BTC candles

## Phase 2: model + filter
- [x] `research/hmm/model.py`: fit (seed, best of 8 restarts), BIC + OOS selection with the simpler-within-2% rule, labels from statistics, durations, label matching across refits
- [x] `research/hmm/filter.py`: forward filtering only; next-state probabilities; entropy; live continuation from a prior
- [x] tests: planted regimes recovered and labelled; simpler model preferred; filtered probabilities independent of future candles; filter != smoother on an overlapping tape; continuation equals whole-series filtering; labels survive a refit
- [ ] `fit_report.py` on BTC/USD hourly (fit to 2024-01, validate to 2024-07, filter forward): states, matrix, durations, per-state next-candle returns by segment
- [ ] same on SPY daily
- [ ] PAUSE: Henry reviews the states before any playbook is written

## Phase 3: playbooks + switching + sizing + risk
- [ ] playbooks/CALM_UP.md, CHOP.md, STRESS.md, CRASH.md with entry, exit, stop, TP, max size, invalidation; frozen before the backtest
- [ ] `switching.py` hysteresis (0.70, hold 3, cooldown, early cut 0.25, uncertain 0.15, stale -> flat) with tests
- [ ] `sizing.py` quarter-Kelly cap x probability x (1 - entropy); calibration gate (Brier, reliability, ECE < 0.05)
- [ ] `risk.py` hard limits + kill switch, checked before every order, with tests

## Phase 4: walk-forward backtest with costs (the gate)
- [ ] 30-day expanding refits, label matching, drift freeze
- [ ] four arms: HMM / Jev battery / both / both + confidence gating (Jev calls on historical snapshots, version pinned)
- [ ] baselines: buy-and-hold, best single static playbook
- [ ] gates verbatim: Sharpe > 1.5, DD < 15%, hit > 55%, t > 2.0, beats both baselines after costs; per state and whole system
- [ ] RESULTS.md; strategy.md ONLY if the gates pass; otherwise the rejection is the result

## Phase 5: calibration + drift + dashboard
- [ ] per-state Brier / reliability on the backtest; Jev reliability + Platt if bent
- [ ] drift monitor (state means/vol, transition rows, live log-likelihood)
- [ ] dashboard from the ledger

## Phase 6: paper (only after phase 4 passes)
- [ ] Alpaca paper loop on the Mac Mini, Telegram alerts, daily report; runs until paper matches backtest

## Phase 7: final check
- [ ] the prompt's questions answered in writing, ending with WHAT COULD BLOW UP THIS ACCOUNT?

## Queue (hypothesis library, not in this mission's scope)
- [ ] je-suis-tm/quant-trading (cloned to D:\repo\quant-trading): MACD, Bollinger patterns, Dual Thrust, Heikin-Ashi, London breakout, Parabolic SAR, RSI patterns, shooting star, pair trading, options straddle, awesome oscillator. Candidate single-rule playbooks for the researcher loop under the bars-rules gate set; each becomes a frozen cell, none is hand-tuned.
