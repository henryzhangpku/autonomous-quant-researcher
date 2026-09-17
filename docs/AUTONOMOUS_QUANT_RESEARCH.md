# Autonomous Quant Researcher — design contract

Design contract for the schema-v3 loop. When the code and this file disagree, the code wins.

## Executive summary

Autonomous Quant Researcher runs a server-owned canonical SPY price-only mission as a bounded
campaign of declarative hypotheses. The LLM returns strict JSON data plus a rationale. It
does not write Python, imports, commands, file paths, or any other executable
candidate implementation.

Trusted code parses each hypothesis, interprets its signal and defined-risk
structure, evaluates it on immutable chronological data, and records every
formulation, admission decision, scientific trial, failure, and stage
transition. The process stops before the final holdout and requires a deliberate
user action to consume it exactly once.

Execution is a separate system's decision. Every output of this loop is
evidence with provenance, stamped with the authority it carries.

## Product contract

The human supplies a campaign title; the mission is displayed read-only and
cannot be changed. Before the first result, the system freezes:

- the canonical mission, structured scope, and policy hashes;
- the schema-v3 hypothesis vocabulary;
- the trusted interpreter and evaluator version;
- the discovery, validation, and holdout manifest hashes;
- base and elevated cost assumptions;
- the scientific-trial budget;
- diversity admission and family-quota rules; and
- the stage-specific acceptance gates.

Changing a frozen contract creates a new campaign. A nonterminal campaign
cannot silently continue with changed evaluator or data semantics.

### Declarative hypothesis boundary

A v3 proposal contains exactly two top-level fields: `hypothesis` and
`rationale`. The hypothesis contains exactly:

- `schema_version: 3`;
- an entry minute on the supported five-minute regular-session grid;
- a bounded Boolean signal expression over approved causal observations; and
- one approved defined-risk options structure with bounded dimensions.

The current observations are return from open, return between two earlier
times, and first touch before entry. The current structures are a call debit
spread and a put credit spread. Trusted functions in `research/v3/hypothesis.py`
interpret those declarations.

There is no executable-source field. A model response containing extra fields,
invalid JSON, unsupported vocabulary, malformed timing, excessive nesting, or
out-of-bounds parameters is rejected before scientific evaluation.

## Staged evidence lifecycle

```mermaid
flowchart LR
    Human["Researcher: campaign title"] --> Freeze["Server-owned canonical SPY mission and frozen contract"]
    Freeze --> Maker["LLM proposes JSON hypothesis + rationale"]
    Maker --> Admission["Schema, duplicate, diversity, and family admission"]
    Admission --> Discovery["2016-2021 discovery"]
    Discovery --> Shortlist["Freeze up to three survivors"]
    Shortlist --> Validation["2022-2024 validation"]
    Validation --> Ready["Finalist at holdout_ready"]
    Ready -->|explicit one-use action| Holdout["2025+ final holdout"]
    Holdout --> Actual["Separate typed OPRA lifecycle"]
    Actual --> Finding["Research-only portable finding"]
```

The stage stores are materialized together from one local historical source,
but each access capability can resolve only its own stage. Every store carries
a content hash and a manifest hash. Tampering fails closed.

| Stage | Dates | Role |
|---|---|---|
| Discovery | 2016-01-01 through 2021-12-31 | hypothesis exploration only; never accepted alpha |
| Validation | 2022-01-01 through 2024-12-31 | evaluate the frozen discovery shortlist |
| Final holdout | 2025-01-01 onward | one-use final modeled evaluation |

The ordinary worker drives discovery and validation, then returns at
`holdout_ready`. It cannot open the final stage. Holdout access is resolved only
after an irreversible receipt has been durably written. A failed callback after
that receipt still consumes the holdout; it cannot be retried.

## Admission, diversity, and shortlist rules

Formulation count and scientific-trial count are different:

- invalid JSON and invalid declarative documents remain in the formulation
  ledger but do not spend a scientific trial;
- semantic duplicates are rejected before evaluation;
- near duplicates—one small numeric permutation of the same idea—are rejected;
- the default family quota admits at most two candidates from one descriptor
  family; and
- every admitted evaluation, including a crash or failed economics, spends and
  records one scientific trial.

After the discovery budget closes, only candidates that passed all discovery
gates can enter the shortlist. The coordinator ranks eligible survivors by
their weakest gate component, prefers distinct clusters, fills any remaining
slots deterministically, freezes at most three candidates, and hashes that
shortlist. Validation evaluates only that frozen set.

Proprietary source families such as flow, GEX, and dark pool are coverage
requirements, not hypothesis-descriptor families. Their readiness cannot be
manufactured by the diversity quota.

## Modeled evaluator gates

The canonical evaluator starts from a gross trade ledger and applies costs once.
Base costs are 3% of spread width; elevated costs are 4.5%. Weekly-cluster
bootstrap uncertainty uses 2,000 deterministic resamples. Validation divides
the 5% lower-tail probability by the frozen shortlist size.

Every stage must pass all applicable gates. No single relative percentage-point
metric can accept a hypothesis.

### Overall hard gates

- absolute full-win edge after base costs must be strictly positive;
- base-cost net mean P&L must be strictly positive;
- the uncertainty-adjusted net P&L lower bound must be strictly positive;
- elevated-cost net mean P&L must be strictly positive;
- every required metric must be finite; and
- selected rows must have prices and meet the stage's sample floors.

### Sample and year gates

| Stage | Priced trades | Effective ISO weeks | Additional year gate |
|---|---:|---:|---|
| Discovery | at least 120 | at least 40 | none |
| Validation | at least 60 | at least 24 | evidence for 2022, 2023, and 2024; each year at least 15 trades and 8 weeks; no year may have negative base-cost mean P&L |
| Holdout | at least 30 | at least 12 | none |

The evaluator may calculate `control_relative_edge_pp`, but that value is
diagnostic. A bad control cannot rescue a candidate, and an absent or bad
control cannot veto otherwise valid absolute evidence.

For eligible candidates, `ranking_score_pp` is the minimum of absolute edge,
the uncertainty lower bound, elevated-cost mean P&L, and the worst available
year's base-cost mean P&L. The score chooses among gate passers; it is not a
substitute for the gates.

## Actual OPRA is a separate evidence gate

Modeled options evidence is not actual OPRA evidence. The OPRA boundary has
three distinct steps:

1. trusted code freezes a decision manifest containing the selected sessions,
   OCC option legs, raw-unadjusted underlying rule, requested provider/feed,
   contract hashes, and—on holdout—the durable receipt;
2. a privileged provider adapter checks OPRA entitlement and collects typed leg
   evidence at the entry time; and
3. deterministic replay consumes only the immutable, hash-verified quote
   artifact, with no provider callback and no fallback price source.

The validation OPRA window begins on 2024-02-01 and ends on 2024-12-31. Holdout
OPRA evidence begins on 2025-01-01 and must carry the matching holdout receipt.

Typed OPRA states are:

- `unavailable`: entitlement is unavailable; no economic metrics may appear;
- `insufficient_coverage`: entitlement exists, but coverage is below 70%, fewer
  than 30 selected sessions are priced, or fewer than 12 ISO weeks are priced;
- `failed_evidence`: coverage floors pass, but at least one actual-price
  economic gate is non-positive; and
- `passed`: coverage floors pass and base-cost mean P&L, the 95% weekly-cluster
  lower bound, and elevated-cost mean P&L are all strictly positive.

No privileged OPRA adapter is bundled in this repository. Without one the
holdout action is refused before a receipt is written, so 2025+ data stays
sealed; a typed adapter is injected by deployment code, never by a candidate.

## Proprietary flow, GEX, and dark-pool boundary

`research/v3/features.py` is an import and derivation contract, not a provider
client. Raw proprietary rows stop at this module. The typed source export
requires:

- one of the `flow`, `gex`, or `dark_pool` families;
- canonical, sorted, unique records and explicit zero-event observation windows;
- source and content hashes plus a source-contract version;
- event, snapshot, availability, and ingestion timestamps;
- America/New_York / XNYS calendar semantics;
- a permitted private redistribution class; and
- family-specific causal and staleness checks.

Candidate-facing feature snapshots contain only derived aggregate metrics,
as-of time, staleness, source hash, export-manifest hash, and a snapshot hash.
They expose no raw rows, provider clients, credentials, paths, or network
handles.

Coverage is explicit for every required family and split. A report is `ready`
only when at least 90% of eligible sessions are covered and it meets the split's
week floor:

| Split | Minimum covered ISO weeks |
|---|---:|
| Discovery | 80 |
| Validation | 40 |
| Holdout | 20 |

Every required family must be ready in every split before proprietary evidence
can support paper/shadow review eligibility.

This repository bundles neither a historical proprietary-vendor export nor a
live vendor adapter. The canonical campaign contract sets `research_mode` to `price_only` and requires no proprietary families.
Adding an authorized source is separate privileged integration work; missing
coverage must remain unavailable or insufficient rather than being filled with
synthetic data.

## Portable finding contract

A survivor finding is portable JSON built only from trusted, frozen aggregate
evidence. It contains:

- campaign, policy, evaluator, feature-schema, and data-manifest hashes;
- the frozen candidate reference and semantic descriptor;
- positive modeled evidence for discovery, validation, and holdout;
- typed validation and holdout OPRA blocks;
- proprietary-feature coverage reports where required;
- derived-aggregate lineage hashes;
- limitations, invalidation conditions, and paper/shadow monitoring; and
- an identity-independent integrity hash.

The export boundary rejects secrets, raw provider payloads, raw rows, provider
records, and other private source material. Its authority is immutable:

```text
classification: research_only
execution_authorized: false
next_allowed_action: paper_shadow_review
```

`paper_shadow_review_eligible` is derived from evidence. It requires passed
validation and holdout modeled gates, passed holdout OPRA evidence, and complete
coverage for every required proprietary family. Eligibility still does not
authorize a live signal, trade, order, or deployment on its own.

A downstream signal registry is the intended consumer of portable findings;
the hand-off is a separate, deliberate step outside this repository.

## Current limits

- The canonical campaign contract covers one SPY 0DTE / weekly-options
  mission; any other mission text is a different contract and fails closed.
- Canonical campaigns are price-only; proprietary feature families are a
  typed import boundary here, not a bundled adapter.
- No privileged OPRA adapter is bundled.
- Crypto and futures need their own immutable data, calendar, cost, and
  evaluator contracts before a campaign can target them.

## Verification contract

```powershell
uv sync --locked
uv run pytest
uv run python -m compileall -q research tests
uv lock --check
git diff --check
```

Verification must cover declarative-schema rejection, stage isolation and hash
checks, duplicate/diversity admission, absolute and sample gates, one-use
holdout receipts, OPRA no-fallback replay, proprietary provenance and coverage,
portable-finding authority, and the credential-free candidate sandbox.

## Definition of success

Autonomous Quant Researcher succeeds when its fixed program can investigate falsifiable SPY price hypotheses while
making it difficult to mistake exploration for evidence:

1. the LLM proposes bounded declarative data only;
2. immutable trusted code controls interpretation, pricing, costs, and gates;
3. discovery, frozen validation, and one-use holdout remain separate;
4. duplicates, failures, and the full trial count remain visible;
5. absolute economics, samples, uncertainty, year stability, and higher costs
   all receive explicit gates;
6. actual OPRA and proprietary data remain separate typed evidence boundaries;
7. exported findings remain derived-only and research-only; and
8. no result becomes a signal or trade by implication.
