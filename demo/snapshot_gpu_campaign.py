"""Write the static data snapshot behind demo/gpu-campaign.html.

Run locally (it imports the engine and reads the GPU index tape):

    uv run python demo/snapshot_gpu_campaign.py
    uv run python demo/snapshot_gpu_campaign.py --tape https://raw.githubusercontent.com/henryzhangpku/gpu-price-index/main/series/index_values.csv

The snapshot holds the frozen pre-registration and its ledger hashes, the
published H100/H200/B200 fixings, each signal's status, and the requirement
checklist with the campaign's refusal, for the primary track and the weaker
proxy track. It holds no metric and no result:
there are none. The Pages build (demo/build.py) only copies the committed file.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.prereg import gpu_leads_revisions as mission  # noqa: E402

OUT = ROOT / "demo" / "data" / "gpu-leads-revisions.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tape", help="path or https URL of series/index_values.csv")
    args = parser.parse_args(argv)
    data = mission.gather(args.tape)
    if data.tape is None:
        print(f"no tape: {data.tape_error}", file=sys.stderr)
        return 1
    snapshot = mission.status_snapshot(data)
    snapshot["proxy"] = mission.proxy_snapshot(mission.gather_proxy(tape_source=args.tape))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snapshot, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    signals = {code: block["signal"]["status"] for code, block in snapshot["series"].items()}
    print(f"wrote {OUT.relative_to(ROOT)} as of {snapshot['as_of_session']}: {signals}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
