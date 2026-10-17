"""Shared helpers for the generator/ETL demo tests."""
from __future__ import annotations

import csv
import json
from pathlib import Path


def read_enriched(out_dir: Path) -> dict[str, list[tuple[str, list[str]]]]:
    """Every enriched CSV in out_dir -> {job ID: [(filename, row fields), ...]}."""
    found: dict[str, list[tuple[str, list[str]]]] = {}
    for path in sorted(Path(out_dir).glob("XTRF-Job-*.csv")):
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
    # locate header by first field "ID" (separator line is bare commas)
        header_idx = None
        for i, fields in enumerate(rows[:10]):
            if fields and fields[0].strip() == "ID":
                header_idx = i
                break
        assert header_idx is not None, f"no header in {path}"
        for fields in rows[header_idx + 1:]:
            if not fields or not fields[0].strip():
                continue
            found.setdefault(fields[0].strip(), []).append((path.name, fields))
    return found


def load_planted(data_dir: Path) -> list[dict]:
    return json.loads((Path(data_dir) / "planted.json").read_text(encoding="utf-8"))


def reconcile_planted(data_dir: Path, out_dir: Path) -> list[str]:
    """Planted == recovered exactly once, in the expected month, at the expected USD."""
    problems: list[str] = []
    by_id = read_enriched(out_dir)
    for item in load_planted(data_dir):
        hits = by_id.get(item["id"], [])
        if len(hits) != 1:
            problems.append(f"{item['id']}: found {len(hits)} rows, expected 1")
            continue
        _, row = hits[0]
        my = (row[7].split(" ")[0] if " " in row[7] else row[7])  # DD-MM-YYYY ...
        mon, yr = my.split("-")[1], my.split("-")[2]
        if f"{yr}-{mon}" != item["month"]:
            problems.append(f"{item['id']}: output month {yr}-{mon}, expected {item['month']}")
        if row[13] != item["usd"]["total_cost"]:
            problems.append(f"{item['id']}: Total Cost USD {row[13]!r} != {item['usd']['total_cost']!r}")
        if row[14] != item["usd"]["agreed"]:
            problems.append(f"{item['id']}: Agreed USD {row[14]!r} != {item['usd']['agreed']!r}")
    return problems


def coverage(data_dir: Path, out_dir: Path) -> tuple[list[str], list[str]]:
    """Returns (missing_or_dup_problems, extra_problems).

    Every job in the final snapshot with a Deadline in the run period must appear
    exactly once across outputs; every planted orphan exactly once; nothing else.
    """
    from etl.enrich import deadline_month_year

    data_dir = Path(data_dir)
    period = json.loads((data_dir / "period.json").read_text(encoding="utf-8"))
    snap_jobs = data_dir / "snapshots" / period["last_month"] / "jobs_analysis.csv"
    missing: list[str] = []
    extra: list[str] = []
    with open(snap_jobs, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))
    header_idx = next(i for i, r in enumerate(rows) if r and r[0].strip() == "ID")
    header = rows[header_idx]
    d_idx = header.index("Deadline")
    expected: set[str] = set()
    for row in rows[header_idx + 1:]:
        if not row or not row[0].strip():
            continue
        my = deadline_month_year(row[d_idx])
        if my and (f"{my[1]}-{my[0]:02d}" >= period["first_month"]) and (
            f"{my[1]}-{my[0]:02d}" <= period["last_month"]
        ):
            expected.add(row[0].strip())
    for item in load_planted(data_dir):
        if item["kind"] == "orphan":
            expected.add(item["id"])
    by_id = read_enriched(out_dir)
    got: dict[str, int] = {k: len(v) for k, v in by_id.items()}
    for jid in expected:
        if got.get(jid, 0) != 1:
            missing.append(f"{jid}: {got.get(jid, 0)} copies in outputs, expected 1")
    for jid in got:
        if jid not in expected:
            extra.append(f"unexpected extra row in outputs: {jid}")
    return missing, extra
