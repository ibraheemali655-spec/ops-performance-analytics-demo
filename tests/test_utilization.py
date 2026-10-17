"""Utilization realism: for seed 42 at least 90% of linguist-months fall in
0.6-1.1, combining XTRF export rows and New Status1.csv tracker rows."""
from __future__ import annotations

from pathlib import Path

from generator.generate import compute_utilization
from generator.generate import run as generate


def test_utilization_in_band_for_seed_42(tmp_path: Path):
    data_dir = tmp_path / "data"
    generate(data_dir, seed=42)  # full default period and volumes

    util = compute_utilization(data_dir)
    assert util, "no linguist-months computed"
    in_band = sum(1 for v in util.values() if 0.6 <= v <= 1.1)
    share = in_band / len(util)
    low = [k for k, v in sorted(util.items()) if v < 0.6][:5]
    high = [k for k, v in sorted(util.items()) if v > 1.1][:5]
    assert share >= 0.90, (
        f"only {in_band}/{len(util)} linguist-months in 0.6-1.1 "
        f"(too low e.g. {[(k, round(util[k], 2)) for k in low]}, "
        f"too high e.g. {[(k, round(util[k], 2)) for k in high]})"
    )
