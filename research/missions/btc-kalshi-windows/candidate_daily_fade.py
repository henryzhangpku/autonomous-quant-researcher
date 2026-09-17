"""Candidate 2: fade a large trailing 24h move.

Rationale: a trailing daily move beyond 2% overshoots at the hourly
horizon and partially retraces in the next window.
"""
LABEL = "hourly daily-move fade: oppose ret_24h when |ret_24h| > 0.02"


def signal(symbol, features):
    r = features["ret_24h"]
    if r > 0.02:
        return -1
    if r < -0.02:
        return 1
    return 0
