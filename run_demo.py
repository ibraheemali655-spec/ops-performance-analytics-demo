"""End-to-end demo: generate synthetic exports, run the ETL month by month,
reconcile planted defects against recovered rows.

    python run_demo.py --seed 42

Exits non-zero on any mismatch.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

from etl.enrich import read_semicolon_dump
from etl.enrich import run as run_etl
from generator.generate import run as generate

FIRST_MONTH = (2025, 1)
LAST_MONTH = (2026, 9)


def next_month(ym: tuple[int, int]) -> tuple[int, int]:
    y, m = ym
    return (y + 1, 1) if m == 12 else (y, m + 1)


def read_enriched(out_dir: Path) -> dict[str, list[tuple[str, list[str]]]]:
    found: dict[str, list[tuple[str, list[str]]]] = {}
    for path in sorted(Path(out_dir).glob("XTRF-Job-*.csv")):
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        header_idx = next(
            (i for i, fields in enumerate(rows[:10]) if fields and fields[0].strip() == "ID"),
            None,
        )
        if header_idx is None:
            continue
        for fields in rows[header_idx + 1:]:
            if fields and fields[0].strip():
                found.setdefault(fields[0].strip(), []).append((path.name, fields))
    return found


def month_of_row(row: list[str]) -> str:
    day = row[7].split(" ")[0]  # DD-MM-YYYY HH:MM GMT+03:00
    _, mon, yr = day.split("-")
    return f"{yr}-{mon}"


_FILE_MONTH_RE = re.compile(r" (\d{2})_(\d{4})\.csv$")


def _snapshot_job_ids(data_dir: Path, month: str) -> set[str]:
    header, rows = read_semicolon_dump(data_dir / "snapshots" / month / "jobs_analysis.csv")
    return {r[0].strip() for r in rows if r and r[0].strip()}


def _snapshot_project_ids(data_dir: Path, month: str) -> set[str]:
    header, rows = read_semicolon_dump(data_dir / "snapshots" / month / "projects_default.csv")
    id_idx = header.index("ID")
    return {r[id_idx].strip() for r in rows if r and r[id_idx].strip()}


def proof(data_dir: Path, out_dir: Path) -> tuple[dict[str, int], list[str]]:
    """Prove the planted defects were real, not just reconcilable.

    - every planted late job is absent from its own month's jobs_analysis.csv
      and appears in a *later* month's output file (recovered by a later run);
    - every post_snapshot orphan is absent from its own month's
      projects_default.csv;
    - every orphan has zero job rows in the final snapshot.
    """
    data_dir, out_dir = Path(data_dir), Path(out_dir)
    planted = json.loads((data_dir / "planted.json").read_text(encoding="utf-8"))
    period = json.loads((data_dir / "period.json").read_text(encoding="utf-8"))
    by_id = read_enriched(out_dir)

    def file_month(filename: str) -> str:
        m = _FILE_MONTH_RE.search(filename)
        return f"{m.group(2)}-{m.group(1)}"

    counts = {
        "late_recovered_by_later_run": 0,
        "late_absent_from_own_snapshot": 0,
        "orphans_caught_own_month": 0,
        "orphans_caught_by_later_sweep": 0,
        "post_snapshot_orphans_absent_own_snapshot": 0,
        "orphans_zero_job_rows_final": 0,
    }
    problems: list[str] = []

    final_jobs = _snapshot_job_ids(data_dir, period["last_month"])
    for item in planted:
        jid, month = item["id"], item["month"]
        hits = by_id.get(jid, [])
        if item["kind"] == "late_job":
            if jid not in _snapshot_job_ids(data_dir, month):
                counts["late_absent_from_own_snapshot"] += 1
            else:
                problems.append(f"late job {jid} IS present in its own month's snapshot")
            if hits and all(file_month(f) > month for f, _ in hits):
                counts["late_recovered_by_later_run"] += 1
            elif hits:
                problems.append(f"late job {jid} appears in its own month's output")
        else:  # orphan
            if item.get("mode") == "post_snapshot":
                if jid not in _snapshot_project_ids(data_dir, month):
                    counts["post_snapshot_orphans_absent_own_snapshot"] += 1
                else:
                    problems.append(
                        f"post-snapshot orphan {jid} IS present in its own month's snapshot")
            if hits:
                got = file_month(hits[0][0])
                if got == month:
                    counts["orphans_caught_own_month"] += 1
                elif got > month:
                    counts["orphans_caught_by_later_sweep"] += 1
            if not any(full_id.startswith(jid + "/#") for full_id in final_jobs):
                counts["orphans_zero_job_rows_final"] += 1
            else:
                problems.append(f"orphan {jid} has job rows in the final snapshot")
    return counts, problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the generator + ETL demo end to end.")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workdir", type=Path, default=Path("out/demo"),
                    help="folder for generated data and enriched output")
    ap.add_argument("--jobs-per-month", type=int, default=1000)
    ap.add_argument("--late-jobs", type=int, default=320)
    ap.add_argument("--orphans", type=int, default=110)
    args = ap.parse_args(argv)

    data_dir = args.workdir / "data"
    out_dir = args.workdir / "jobs"
    # Start clean: files left by an earlier generator version would leak into
    # the reconciliation and the determinism hashes.
    for stale in (data_dir, out_dir):
        if stale.exists():
            shutil.rmtree(stale)
    print(f"Generating synthetic exports (seed {args.seed}) into {data_dir} ...")
    generate(data_dir, seed=args.seed, start=FIRST_MONTH,
             num_months=21, jobs_per_month=args.jobs_per_month,
             late_jobs=args.late_jobs, orphans=args.orphans)

    planted = json.loads((data_dir / "planted.json").read_text(encoding="utf-8"))
    planted_late_n = sum(1 for i in planted if i["kind"] == "late_job")
    planted_orphan_n = sum(1 for i in planted if i["kind"] == "orphan")
    planted_post_n = sum(1 for i in planted
                         if i["kind"] == "orphan" and i.get("mode") == "post_snapshot")
    planted_by_month: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for item in planted:
        planted_by_month[item["month"]][item["kind"]] += 1

    months = []
    ym = FIRST_MONTH
    while True:
        months.append(ym)
        if ym == LAST_MONTH:
            break
        ym = next_month(ym)

    print(f"Running the ETL for {len(months)} months in order ...")
    for y, m in months:
        run_etl(data_dir / "snapshots" / f"{y}-{m:02d}", m, y,
                data_dir / "fx_rates.csv", out_dir)

    print("Reconciling planted defects against recovered rows ...")
    by_id = read_enriched(out_dir)
    recovered_by_month: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    problems: list[str] = []
    for item in planted:
        hits = by_id.get(item["id"], [])
        kind = item["kind"]
        if len(hits) != 1:
            problems.append(f"{item['id']} ({kind}): {len(hits)} rows in outputs, expected 1")
            continue
        _, row = hits[0]
        if month_of_row(row) != item["month"]:
            problems.append(
                f"{item['id']} ({kind}): output month {month_of_row(row)}, "
                f"expected {item['month']}"
            )
            continue
        if row[13] != item["usd"]["total_cost"] or row[14] != item["usd"]["agreed"]:
            problems.append(
                f"{item['id']} ({kind}): USD ({row[13]!r}, {row[14]!r}) != "
                f"({item['usd']['total_cost']!r}, {item['usd']['agreed']!r})"
            )
            continue
        recovered_by_month[item["month"]][kind] += 1

    kinds = ["late_job", "orphan"]
    totals = {k: [0, 0] for k in kinds}
    print()
    print(f"{'month':<10} " + " ".join(f"{k + ' planted/recovered':>28}" for k in kinds))
    for month in sorted(set(planted_by_month) | {m for ym in recovered_by_month for m in [ym]}):
        cells = []
        for k in kinds:
            p = planted_by_month[month].get(k, 0)
            r = recovered_by_month[month].get(k, 0)
            totals[k][0] += p
            totals[k][1] += r
            cells.append(f"{p:>10} / {r:<10}")
        print(f"{month:<10} " + " ".join(f"{c:>28}" for c in cells))
    print("-" * 70)
    row = [f"{totals[k][0]:>10} / {totals[k][1]:<10}" for k in kinds]
    print(f"{'TOTAL':<10} " + " ".join(f"{c:>28}" for c in row))
    print()

    # coverage: nothing lost, nothing duplicated
    period = json.loads((data_dir / "period.json").read_text(encoding="utf-8"))
    expected: set[str] = set()
    snap = data_dir / "snapshots" / period["last_month"] / "jobs_analysis.csv"
    with open(snap, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))
    header_idx = next(i for i, r in enumerate(rows) if r and r[0].strip() == "ID")
    d_idx = rows[header_idx].index("Deadline")
    from etl.enrich import deadline_month_year
    for row in rows[header_idx + 1:]:
        if not row or not row[0].strip():
            continue
        my = deadline_month_year(row[d_idx])
        if my:
            ym_str = f"{my[1]}-{my[0]:02d}"
            if period["first_month"] <= ym_str <= period["last_month"]:
                expected.add(row[0].strip())
    expected |= {i["id"] for i in planted if i["kind"] == "orphan"}
    for jid, hits in by_id.items():
        if jid not in expected:
            problems.append(f"unexpected row in outputs: {jid}")
        elif len(hits) != 1:
            problems.append(f"{jid}: {len(hits)} copies in outputs, expected 1")
    for jid in expected:
        if len(by_id.get(jid, [])) != 1:
            problems.append(f"{jid}: {len(by_id.get(jid, []))} copies in outputs, expected 1")

    # Prove the defects were real (absences in snapshots), not just reconcilable.
    proof_counts, proof_problems = proof(data_dir, out_dir)
    problems.extend(proof_problems)

    print()
    print("Proof the planted defects were real:")
    print(f"  late jobs absent from own month's snapshot : "
          f"{proof_counts['late_absent_from_own_snapshot']}/{planted_late_n}")
    print(f"  late jobs recovered by a later run         : "
          f"{proof_counts['late_recovered_by_later_run']}/{planted_late_n}")
    print(f"  orphans caught in their own month's run    : "
          f"{proof_counts['orphans_caught_own_month']}")
    print(f"  orphans caught by a later year-to-date sweep: "
          f"{proof_counts['orphans_caught_by_later_sweep']}")
    print(f"  post-snapshot orphans absent from own month's projects dump: "
          f"{proof_counts['post_snapshot_orphans_absent_own_snapshot']}/"
          f"{planted_post_n}")
    print(f"  orphans with zero job rows in final snapshot: "
          f"{proof_counts['orphans_zero_job_rows_final']}/{planted_orphan_n}")

    if problems:
        print(f"MISMATCHES ({len(problems)}):")
        for p in problems[:50]:
            print(f"  - {p}")
        return 1
    print("OK: planted == recovered exactly, no loss, no duplicates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
