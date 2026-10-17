"""Test 3: idempotent re-run — one file per month, identical content."""
from __future__ import annotations

from pathlib import Path

from etl.enrich import run as run_etl
from generator.generate import run as generate
from tests.conftest import reconcile_planted


def test_rerun_is_idempotent(tmp_path: Path):
    data_dir = tmp_path / "data"
    out_dir = tmp_path / "out"
    generate(data_dir, seed=3, start=(2025, 1), num_months=3, jobs_per_month=200,
             late_jobs=3, orphans=2)
    for m in range(1, 4):
        run_etl(data_dir / "snapshots" / f"2025-{m:02d}", m, 2025,
                data_dir / "fx_rates.csv", out_dir)
    files = [p for p in out_dir.glob("XTRF-Job-*.csv") if p.name.endswith(" 02_2025.csv")]
    assert len(files) == 1
    first = files[0].read_bytes()

    run_etl(data_dir / "snapshots" / "2025-02", 2, 2025, data_dir / "fx_rates.csv", out_dir)
    files = [p for p in out_dir.glob("XTRF-Job-*.csv") if p.name.endswith(" 02_2025.csv")]
    assert len(files) == 1, "re-run must not create a second file for the month"
    assert files[0].read_bytes() == first, "re-run must produce identical content"

    # planted items recovered in month 2 must still be there exactly once
    for item in reconcile_planted(data_dir, out_dir):
        raise AssertionError(item)
