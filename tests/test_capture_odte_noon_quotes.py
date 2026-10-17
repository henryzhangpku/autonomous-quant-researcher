"""The noon capture must price exactly the geometry the odte-intervention M0 cells were tested on."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research" / "experiments"))

import capture_odte_noon_quotes as cap  # noqa: E402


def test_geometry_matches_prepare_odte_path_convention():
    # offset = max(1, round(spot * 0.26%)), width = max(2, round(spot * 0.64%)), $1 grid
    assert cap.geometry(700.4, "put") == (698.0, 694.0, 4.0, 2)
    assert cap.geometry(600.2, "call") == (602.0, 606.0, 4.0, 2)
    # floors bind on a cheap underlying
    assert cap.geometry(100.0, "put") == (99.0, 97.0, 2.0, 1.0)


def test_cells_are_the_three_that_cleared():
    assert cap.CELLS == (("SPY", "put"), ("QQQ", "put"), ("QQQ", "call"))
    assert (cap.OFFSET_PCT, cap.WIDTH_PCT) == (0.0026, 0.0064)


def test_occ_symbol():
    assert cap.occ("SPY", date(2026, 10, 15), "put", 698.0) == "SPY261015P00698000"
    assert cap.occ("QQQ", date(2026, 10, 15), "call", 602.5) == "QQQ261015C00602500"
