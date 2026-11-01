"""Split a configured command string into argv on every platform."""

from __future__ import annotations

import os
import shlex


def split_command(raw: str, *, windows: bool | None = None) -> tuple[str, ...]:
    """Split ``raw`` like a shell would, keeping Windows paths intact.

    POSIX ``shlex`` treats backslashes as escapes, so ``C:\\Users\\x\\python.exe``
    collapses to ``C:Usersxpython.exe``. On Windows, split in non-POSIX mode
    (backslashes are literal) and drop the quotes that mode leaves on tokens.
    """
    if windows is None:
        windows = os.name == "nt"
    if not windows:
        return tuple(shlex.split(raw))
    tokens = []
    for token in shlex.split(raw, posix=False):
        if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
            token = token[1:-1]
        tokens.append(token)
    return tuple(tokens)
