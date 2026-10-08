"""Pre-registered research campaigns: an idea frozen into a test before its data exists.

A pre-registration is a machine-readable specification, a human-readable
document and the trusted code that will compute the verdict, all bound by
content hash into a hash-chained ledger (``ledger.py``). A campaign declares
its data requirements (``requirements.py``) and returns a typed refusal until
every one is met. The gates (``gates.py``) are pure functions over weekly
outcomes.
"""
