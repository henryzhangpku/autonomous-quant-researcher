"""Runtime discovery helpers for the research lab."""

from __future__ import annotations

import subprocess
import time
from typing import Any

_GPU_CACHE: dict[str, Any] | None = None
_GPU_CACHE_AT: float = 0.0
_GPU_CACHE_TTL_SECONDS = 30.0


def gpu_diagnostic(timeout_seconds: int = 5) -> dict[str, Any]:
    """Return a best-effort NVIDIA GPU diagnostic without adding dependencies.

    Cached for a short TTL, mirroring the model probe in the private web gateway:
    /api/health calls this on every poll, and an uncached nvidia-smi spawn
    burns its full timeout while the driver is busy grinding on research,
    keeping the health endpoint at multi-second latency (2026-08-16). A
    30s-late GPU pill is invisible; a slow health endpoint is not.
    """
    global _GPU_CACHE, _GPU_CACHE_AT
    if _GPU_CACHE is not None and time.monotonic() - _GPU_CACHE_AT < _GPU_CACHE_TTL_SECONDS:
        return dict(_GPU_CACHE)
    result = _probe_gpu(timeout_seconds)
    _GPU_CACHE = result
    _GPU_CACHE_AT = time.monotonic()
    return result


def _probe_gpu(timeout_seconds: int) -> dict[str, Any]:
    command = [
        "nvidia-smi",
        "--query-gpu=name,memory.total,driver_version",
        "--format=csv,noheader",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {
            "available": False,
            "command": command,
            "error": str(exc),
        }

    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    devices: list[dict[str, str]] = []
    for line in lines:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) >= 3:
            devices.append(
                {
                    "name": parts[0],
                    "memory_total": parts[1],
                    "driver_version": parts[2],
                }
            )
    return {
        "available": completed.returncode == 0 and bool(devices),
        "command": command,
        "returncode": completed.returncode,
        "devices": devices,
        "stderr": completed.stderr.strip(),
    }
