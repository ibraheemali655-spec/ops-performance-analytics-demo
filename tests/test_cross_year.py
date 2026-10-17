"""Test 8: cross-year limitation (documents current behaviour).

A job whose Deadline falls in December 2025 but which only becomes visible in the
January 2026 snapshot is NOT recovered: the late-rows sweep only looks at earlier
months of the *target* year, and January's sweep starts at Jan 2026. The same
applies to the orphan-project year-to-date sweep. This is a known limitation of
the year-to-date design; the generator therefore never plants late jobs or
post-snapshot orphans in December.
"""
from __future__ import annotations

from pathlib import Path

from etl.enrich import run as run_etl
from tests.snapshot_util import blank_job, write_fx, write_snapshot


def test_december_late_job_not_recovered_in_january(tmp_path: Path):
    snap = tmp_path / "snapshots" / "2026-01"
    write_snapshot(
        snap,
        jobs_rows=[
            blank_job("2026/4001/#3/1", "20-01-2026 20:00 GMT+03:00"),
            # December deadline, visible only now — unrecoverable by design
            blank_job("2025/3980/#3/1", "15-12-2025 20:00 GMT+03:00"),
        ],
        projects_rows=[],
    )
    fx = tmp_path / "fx_rates.csv"
    write_fx(fx, {"2025-12": 1.0500, "2026-01": 1.1000})

    out = tmp_path / "out"
    run_etl(snap, 1, 2026, fx, out)

    files = list(out.glob("XTRF-Job-*.csv"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8-sig")
    assert "2025/3980/#3/1" not in text, (
        "cross-year late job must stay missing (documented year-to-date limitation)"
    )
    assert "2026/4001/#3/1" in text
