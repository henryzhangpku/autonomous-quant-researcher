# Autonomous Quant Researcher

An agent-driven research system that generates hypotheses, runs experiments, applies
validation gates, and records verdicts — continuously, without a human prompting each step.

**386 research sessions to date.** Every trial stays on a permanent ledger, including the
ones that failed.

This repository documents the architecture and methodology. It does not contain strategy
code or research output.

---

## Why

Most "AI for quant research" tooling automates the wrong tenth of the problem. Running a
backtest is easy and getting easier. What's hard — and what actually determines whether a
result means anything — is everything around it: how hypotheses get proposed, what gates
they must survive, what stays frozen while they're tested, and whether the failures are
recorded honestly.

A system that reports only its winners is not a research system. It's a search for
confirmation with extra steps.

## The loop

```
Ask → Compile → Propose → Admit → Evaluate → Verdict → Learn
```

| Stage | What happens |
|---|---|
| **Ask** | A mission, written in plain language, defines the research question and the evaluation policy. |
| **Compile** | The mission is compiled into an executable contract: universe, horizon, data contracts, cost model, splits. |
| **Propose** | The agent generates candidate research code against that contract. |
| **Admit** | Structural validation. Candidates that violate the contract are rejected before they ever run. |
| **Evaluate** | Execution against immutable preparation, snapshots, and backtest mechanics. |
| **Verdict** | Research gates decide: pass, refute, or invalid. The evaluator is fixed for the mission's duration. |
| **Learn** | The result — whatever it is — is written to the ledger and informs the next question. |

The loop is the product. Individual experiments are disposable; the discipline that
governs them is not.

## Design rules

**The evaluator is immutable during a mission.**
Preparation, snapshots, backtest mechanics, validators, cost models, splits, and holdouts
are frozen before the first candidate runs. If the bar can move while you're testing
against it, you don't have a bar.

**Refutation is a result.**
Negative outcomes are recorded and published, not discarded. "We found something that
works" is close to meaningless without the count of what was tried first.

**No hand-authored strategy rules.**
The human writes missions and evaluation policy. The loop writes the candidate research
code. When results disappoint, the correct response is to improve the loop, the evaluator,
or the data integrity — never to hand-tune the strategy until it passes.

**Budget exhaustion is not success.**
Running out of trials pauses a mission. It never converts into a positive verdict.

**Point-in-time data or nothing.**
Explicit data contracts with session metadata. No silent provider fallback — a missing
source fails loudly rather than quietly substituting something that leaks the future.

**Credentials never reach candidate code.**
Data acquisition happens outside candidate execution. Validator children run stripped of
provider and model credentials.

## A worked example

> *Does META tend to move higher the day after a wide-range down day?*

- 400 parameter cells tested
- 1 candidate survived the research gates
- A low-realized-volatility filter was the decisive condition
- It also survived a frozen validation set

The same question, asked of TSLA and AVGO, produced **zero** surviving candidates.

Both outcomes went on the ledger. The refutations are the more useful half of that result,
because they're what tells you the surviving candidate wasn't just the luckiest of 1,200
attempts.

## What this is not

- Not a signal service, and not investment advice
- Not a claim that the surviving candidates are profitable after real-world costs
- Not open-source strategy code — the methodology is public, the research output is not

## Background

Built by [Henry Zhang](https://www.linkedin.com/in/henryzhang99/) — quantitative engineer,
fifteen years building research platforms and production trading systems at BlackRock,
PIMCO, and TCW.

Writing on the system:
[Meet AQR — Autonomous Quant Researcher](https://www.linkedin.com/feed/update/urn:li:activity:7495275557270614016/) ·
[Quant research has always been the bottleneck](https://www.linkedin.com/feed/update/urn:li:activity:7493897901451063296/)

---

*Research only. Not investment advice.*
