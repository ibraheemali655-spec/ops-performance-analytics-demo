"""Tests 1 + 2: planted == recovered, no loss / no duplicates, and proof that
the planted defects were real (absences in the snapshots)."""
from __future__ import annotations

from pathlib import Path

from etl.enrich import run as run_etl
from generator.generate import run as generate
from run_demo import proof
from tests.conftest import coverage, load_planted, reconcile_planted


def test_planted_recovered_and_full_coverage(tmp_path: Path):
    data_dir = tmp_path / "data"
    out_dir = tmp_path / "out"
    generate(data_dir, seed=7, start=(2025, 1), num_months=6, jobs_per_month=80,
             late_jobs=6, orphans=4)
    for m in range(1, 7):
        run_etl(data_dir / "snapshots" / f"2025-{m:02d}", m, 2025,
                data_dir / "fx_rates.csv", out_dir)

    problems = reconcile_planted(data_dir, out_dir)
    assert not problems, "\n".join(problems)

    missing, extra = coverage(data_dir, out_dir)
    assert not missing, "\n".join(missing)
    assert not extra, "\n".join(extra)

    counts, proof_problems = proof(data_dir, out_dir)
    assert not proof_problems, "\n".join(proof_problems)
    assert counts["late_absent_from_own_snapshot"] == 6
    assert counts["late_recovered_by_later_run"] == 6
    assert counts["orphans_zero_job_rows_final"] == 4
    planted_post = sum(1 for i in load_planted(data_dir)
                       if i["kind"] == "orphan" and i.get("mode") == "post_snapshot")
    assert counts["post_snapshot_orphans_absent_own_snapshot"] == planted_post
