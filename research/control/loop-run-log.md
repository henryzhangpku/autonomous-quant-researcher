# Loop run log contract

The machine-readable log is `.research/runs/<mission-id>/runs.jsonl`. Each line
records at least: run number, UTC timestamp, mission/policy/prompt/candidate
hashes, rationale hash, validator command identity, return code, duration,
metric, acceptance, improvement, feedback, error, and boundary verdict.

This Markdown file defines the contract only. It must not be edited to conceal
or summarize away failed trials.
