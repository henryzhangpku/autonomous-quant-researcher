"""Candidate 1: fade a 3-hour up streak.

Rationale: three consecutive up hours mark short-horizon overextension;
the next hourly window mean-reverts more often than not.
"""
LABEL = "hourly streak-fade: short after streak_up >= 3"


def signal(symbol, features):
    if features["streak_up"] >= 3:
        return -1
    return 0
