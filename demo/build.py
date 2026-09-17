"""Assemble the static GitHub Pages demo into _site/.

The demo ships the REAL engine: research/v3 (declarative contract, campaign
coordinator, evaluator, ledger), research/backtest (options mechanics) and the
lab's proposer boundary, zipped for Pyodide. Nothing from experiments,
missions, validators or providers is needed to run one campaign in a browser.
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
SITE = ROOT / "_site"

PACKAGE_DIRS = ("research/v3", "research/backtest")
PACKAGE_FILES = (
    "research/lab/__init__.py", "research/lab/endpoints.py", "research/lab/gpu.py",
    "research/lab/loop.py", "research/lab/memory.py", "research/lab/mission.py",
    "research/lab/proposer.py",
)


def main() -> None:
    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir()
    for name in ("index.html", "shim.js", "worker.js", "engine.py"):
        shutil.copy2(DEMO / name, SITE / name)
    shutil.copytree(DEMO / "static", SITE / "static")
    with zipfile.ZipFile(SITE / "research.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for directory in PACKAGE_DIRS:
            for path in sorted((ROOT / directory).glob("*.py")):
                archive.write(path, path.relative_to(ROOT).as_posix())
        for name in PACKAGE_FILES:
            archive.write(ROOT / name, name)
    (SITE / ".nojekyll").write_text("")
    print(f"built {SITE} ({sum(1 for _ in SITE.rglob('*') if _.is_file())} files)")


if __name__ == "__main__":
    main()
