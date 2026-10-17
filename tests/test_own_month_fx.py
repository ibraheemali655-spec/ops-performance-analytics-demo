"""Test 6: a late job converts at its own month's rate, not the target month's."""
from __future__ import annotations

from pathlib import Path

from etl.enrich import run as run_etl
from tests.snapshot_util import blank_job, write_fx, write_snapshot


def test_late_job_uses_own_month_rate(tmp_path: Path):
    snap = tmp_path / "snapshots" / "2026-04"
    write_snapshot(
        snap,
        jobs_rows=[
            blank_job("2026/4001/#3/1", "20-04-2026 20:00 GMT+03:00"),
            # deadline March 2026, arrived late (in this April-target snapshot)
            blank_job("2026/3990/#3/1", "15-03-2026 20:00 GMT+03:00",
                      cost="€1,000.00", agreed="€2,000.00"),
        ],
        projects_rows=[],
    )
    fx = tmp_path / "fx_rates.csv"
    write_fx(fx, {"2026-03": 1.1000, "2026-04": 1.2000})

    out = tmp_path / "out"
    run_etl(snap, 4, 2026, fx, out)

    files = [p for p in out.glob("XTRF-Job-*.csv") if p.name.endswith(" 04_2026.csv")]
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8-sig")
    assert "1100.00000" in text, "late job Total Cost must use March rate 1.10"
    assert "2200.00000" in text, "late job Agreed must use March rate 1.10"
    # the April base row uses the April rate (100*1.2, 500*1.2)
    assert "120.00000" in text and "600.00000" in text
