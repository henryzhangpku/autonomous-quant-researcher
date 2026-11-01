# Research state

Repository-level state is intentionally small. Mission runtime state lives in
ignored `.research/runs/<mission-id>/` and must include mission/policy hashes,
status, run count, best accepted metric, candidate hashes, pause reason, and an
append-only `runs.jsonl`.

Current repository phase: autonomous research platform foundation.

Active evaluator: chronological options engine over staged, hashed data.

Active providers: free-tier market-data adapters plus the public CSV example;
licensed providers are interface-only and refuse until an adapter is injected.

No active mission is implied by this file.
