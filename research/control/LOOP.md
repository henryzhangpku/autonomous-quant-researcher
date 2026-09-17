# Research loop

`mission -> prepare -> propose -> gate -> simulate -> validate -> record -> revise`

## Lifecycle

- `ready`: frozen mission, policy, data, and evaluator hashes exist.
- `running`: one candidate is being proposed or validated.
- `complete`: the immutable validator passed and `result.json` records evidence.
- `paused`: budget exhausted, user pause, provider failure, or integrity drift.
- `failed`: unrecoverable configuration or safety violation.

Each run creates content-addressed candidate evidence and appends one JSON
record. Up to three candidate-formulation attempts may occur before that run;
every formulation, preflight result, and error is recorded in the candidate
admission ledger. If none passes the fixed contract preflight, the loop stops
without incrementing the scientific run count. A validator run always counts.
Only mechanical validation may mark completion; an LLM verdict cannot.

The maker proposes candidate code. The checker is the fixed simulator,
statistical gate, boundary tests, and completion-artifact validator. Data
capture is outside the candidate loop.

Every terminal stop writes `shutdown-report.md`: accepted result, exhausted
budget, and human pause alike. It lists every attempt, including proposal and
validator failures, so an interrupted run still leaves usable research
evidence.
