"""Synthetic data generator imitating a translation agency's XTRF exports.

Deterministic: the same --seed produces byte-identical files. Everything is
built from fixed fictional word lists — no real companies, brands or people.

Time model: every job/project carries a hidden ``visible_from`` date (when it
would appear in XTRF) that is never written to any export. The snapshot for
month M is taken 3-6 days into M+1 and is a *full-history* dump of everything
with ``visible_from <= snapshot date``.

Planted defects (recoverable by the ETL, see etl/enrich.py):

* late jobs   — Deadline in month M, visible only after M's snapshot but before
  a later snapshot of the same year (never planted in December or in the final
  month of the period — unrecoverable by design).
* orphans     — projects with booked revenue and zero jobs; some visible before
  their month's snapshot, some after (same same-year constraint).

CLI:
    python -m generator.generate --seed 42 --out data
"""
from __future__ import annotations

import argparse
import calendar
import csv
import json
import math
import random
import re
import sys
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# Fixed fictional vocabularies
# ---------------------------------------------------------------------------

COMPANY_WORDS = [
    "Northwind", "Bluecrest", "Solstice", "Harborview", "Meridian", "Cobalt",
    "Verdant", "Larkspur", "Granite", "Silverline", "Thistledown", "Pinewood",
    "Redrock", "Clearwater", "Summit", "Eastvale", "Kingsford", "Brightpath",
    "Oakford", "Willowmere", "Falconridge", "Trueline", "Marigold", "Stonebridge",
    "Quillstone", "Lantern", "Cedarhurst", "Windrose", "Foxglove", "Ivoryton",
    "Bellhaven", "Driftwood", "Amberline", "Copperfield", "Juniper", "Mistral",
    "Novastrand", "Palewood", "Riverstone", "Sablecourt",
]
COMPANY_SUFFIXES = [
    "Localization", "Language Services", "Translations", "Global Content",
    "Linguistic Services", "Media Localization",
]
FIRST_NAMES = [
    "Amina", "Edda", "Chidi", "Dara", "Elif", "Farid", "Greta", "Hana", "Ivo",
    "Jana", "Kwame", "Lena", "Milo", "Oren", "Omar", "Petra", "Odile", "Sami",
    "Saskia", "Ulf", "Vera", "Waleed", "Yara", "Zane", "Ines", "Tomas", "Tobin",
    "Soren", "Sofia", "Dmitri",
]
LAST_NAMES = [
    "Brennholt", "Okafor", "Lindqvist", "Marchetti", "Novak", "Haddad", "Petrov",
    "Quarrington", "Kowalski", "Tanaka", "Abebe", "Duarte", "Ivanova", "Reyes",
    "Bauer", "Costa", "Aziz", "Lindgren", "Moreau", "Sato",
]
LANG_PAIRS = [
    "EN-US » AR-EG", "EN-US » FR-FR", "EN-US » DE-DE", "EN-GB » ES-419",
    "EN-US » JA-JP", "EN-US » PT-BR", "EN-US » ZH-CN", "DE-DE » EN-US",
    "FR-FR » EN-US", "AR-EG » EN-US",
]
SPECIALIZATIONS = [
    "Software", "Marketing", "Legal", "Medical", "Technical", "Financial",
    "eLearning", "Media",
]
JOB_TYPES = ["translation", "tep", "editing", "mtpe", "lqa", "proofreading"]

JOBS_HEADER = [
    "ID", "Client > Legal Name", "Project Name", "Task > Language Combination > Short Name",
    "Project > Specialization > Name", "Job Type", "Project > Volume", "Deadline", "Job Status",
    "Project > Project Manager > Name", "Vendor > Name", "Vendor > Completed Jobs", "Task > ROI",
    "Total Cost [€]", "Project > Total Agreed [€]", "Project > Margin",
    "Task > Invoice > Invoice No.", "Total Cost", "Project > Total Agreed",
]
PROJECTS_HEADER = [
    "ID", "Name", "Client > Legal Name", "Languages", "Service", "Project Manager > Name",
    "Volume", "Total Cost", "Total Agreed", "Start Date and Time", "Deadline",
    "Total Agreed (without currency)", "Total Agreed [€]", "Margin",
]
LINGUIST_EXPORT_HEADER = [
    "ID", "Client, Legal Name", "Project Name", "Task, Language Combination, Short Name",
    "Project, Specialization, Name", "Job Type", "Project, Volume", "Deadline", "Job Status",
    "Project, Project Manager, Name", "Vendor, Name", "Vendor, Completed Jobs", "Task, ROI",
    "Total Cost [€]", "Project, Total Agreed [€]", "Project, Margin",
]

# Daily per-linguist capacity by task type, before / from the Sep 2026 cutover.
CAPACITY_BEFORE = {"Shooting": 1500, "Revision": 3000, "MTPE": 3000, "QA": 8000,
                   "Translation": 2000}
CAPACITY_AFTER = {"Shooting": 1500, "Revision": 4000, "MTPE": 4000, "QA": 8000,
                  "Translation": 3000}


def month_range(first: tuple[int, int], num_months: int) -> list[tuple[int, int]]:
    y, m = first
    out = []
    for _ in range(num_months):
        out.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def next_month(ym: tuple[int, int]) -> tuple[int, int]:
    y, m = ym
    return (y + 1, 1) if m == 12 else (y, m + 1)


# ---------------------------------------------------------------------------
# World building
# ---------------------------------------------------------------------------

def _draw_people(rng: random.Random, count: int) -> list[dict]:
    """Draw `count` unique people once from one pool.

    Everyone gets both an XTRF display handle (initial + surname, e.g.
    "KOkafor" — used by PMs and account managers) and a full name
    ("Kwame Okafor" — used by freelance vendors and in-house linguists).
    Because the (first, last) pairs are unique, no two people share a handle
    or a full name, and a handle can never equal another role's name.
    """
    pairs = rng.sample([(f, ln) for f in FIRST_NAMES for ln in LAST_NAMES], count)
    return [{"handle": f"{p[0][0]}{p[1]}", "full": f"{p[0]} {p[1]}"} for p in pairs]


def build_world(rng: random.Random) -> dict:
    company_bases = rng.sample(COMPANY_WORDS, len(COMPANY_WORDS))
    clients = []
    for i in range(40):
        name = f"{company_bases[i]} {rng.choice(COMPANY_SUFFIXES)}"
        n_accounts = rng.randint(1, 3)
        accounts = [f"{name.split()[0]} Account {chr(65 + j)}" for j in range(n_accounts)]
        clients.append({
            "name": name,
            "accounts": accounts,
        })
    # Pareto revenue weights: top ~8 clients ~= 70%.
    ranks = list(range(1, len(clients) + 1))
    rng.shuffle(ranks)
    weights = {clients[i]["name"]: 1.0 / ranks[i] ** 1.3 for i in range(len(clients))}

    profiles = (
        ["growing"] * 8 + ["declining"] * 6 + ["stable"] * 20
        + ["churned"] * 3 + ["new2026"] * 3
    )
    rng.shuffle(profiles)
    for client, profile in zip(clients, profiles, strict=False):
        client["profile"] = profile

    people = _draw_people(rng, 6 + 10 + 60 + 12)
    ams = [p["handle"] for p in people[:6]]
    pms = [p["handle"] for p in people[6:16]]
    vendors = [p["full"] for p in people[16:76]]
    linguists = [p["full"] for p in people[76:]]
    # Balance AM books by revenue weight (largest client first, to the lightest
    # book). A uniform pick piled the four biggest clients on one AM. The draws
    # are kept so every later RNG value, and so the planted defects, stay put.
    for _ in clients:
        rng.choice(ams)
    load = dict.fromkeys(ams, 0.0)
    for client in sorted(clients, key=lambda c: -weights[c["name"]]):
        am = min(ams, key=lambda a: load[a])
        client["am"] = am
        load[am] += weights[client["name"]]

    # Two clients + one PM where late jobs cluster (mirrors the real gap).
    # Churned clients are excluded too: they produce no work in 2026 and would
    # shrink the cluster below its 70% share.
    late_cluster = rng.sample(
        [c["name"] for c in clients if c["profile"] not in ("new2026", "churned")], 2)
    late_pm = rng.choice(pms)

    return {
        "clients": clients,
        "client_map": {c["name"]: c for c in clients},
        "weights": weights,
        "ams": ams,
        "pms": pms,
        "vendors": vendors,
        "linguists": linguists,
        "late_cluster": late_cluster,
        "late_pm": late_pm,
    }


def project_name(world: dict, client: str, pid: str) -> str:
    """Project name carrying the end-account token, as PMs name them in XTRF.

    The Power BI model derives `account` from a token in the project name plus
    the client (anything unmatched is "Generic"). The account is picked from the
    project serial rather than the RNG, so adding it leaves every other draw
    unchanged; every fourth project carries no token and lands in "Generic".
    """
    serial = int(pid.split("/")[1])
    accounts = world["client_map"][client]["accounts"]
    if serial % 4 == 0:
        return f"{client.split()[0]} Project {serial}"
    return f"{accounts[serial % len(accounts)]} Project {serial}"


# ---------------------------------------------------------------------------
# Formatting helpers (XTRF export formats)
# ---------------------------------------------------------------------------

def fmt_eur(v: float) -> str:
    return f"€{v:,.2f}"


def fmt_volume(v: float) -> str:
    return f"{v:,.2f}"


def fmt_dt(d: date, hour: int, minute: int) -> str:
    return f"{d.day:02d}-{d.month:02d}-{d.year:04d} {hour:02d}:{minute:02d} GMT+03:00"


def tracker_date(d: date, with_time: bool) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year}" + (" 0:00" if with_time else "")


def fx_walk(rng: random.Random, months: list[tuple[int, int]]) -> dict[tuple[int, int], float]:
    rate = rng.uniform(1.06, 1.10)
    out = {}
    for ym in months:
        rate = min(1.18, max(1.02, rate + rng.uniform(-0.012, 0.012)))
        out[ym] = round(rate, 4)
    return out


# ---------------------------------------------------------------------------
# Core generation
# ---------------------------------------------------------------------------

class Generator:
    def __init__(self, rng: random.Random, world: dict, months: list[tuple[int, int]],
                 jobs_per_month: int, late_jobs: int, orphans: int):
        self.rng = rng
        self.world = world
        self.months = months
        self.jobs_per_month = jobs_per_month
        self.late_target = late_jobs
        self.orphan_target = orphans
        self.snapshots = {ym: self._snapshot_date(ym) for ym in months}
        self.rates = fx_walk(rng, months)
        self.serial = 4000
        self.jobs: list[dict] = []       # every job ever created
        self.projects: list[dict] = []   # every non-orphan project
        self.orphan_projects: list[dict] = []
        self.planted: list[dict] = []

    # -- dates -------------------------------------------------------------
    def _snapshot_date(self, ym: tuple[int, int]) -> date:
        ny, nm = next_month(ym)
        return date(ny, nm, 3 + self.rng.randint(0, 3))

    def _rand_date(self, ym: tuple[int, int]) -> date:
        y, m = ym
        return date(y, m, self.rng.randint(1, calendar.monthrange(y, m)[1]))

    def _rand_dt_in_month(self, ym: tuple[int, int]) -> date:
        return self._rand_date(ym)

    def _deadline_dt(self, ym: tuple[int, int], day: int) -> date:
        y, m = ym
        day = min(day, calendar.monthrange(y, m)[1])
        return date(y, m, day)

    def _weight_for(self, client: dict, ym: tuple[int, int]) -> float:
        w = self.world["weights"][client["name"]]
        profile = client["profile"]
        if profile == "churned" and ym >= (2026, 1):
            return 0.0
        if profile == "new2026":
            return w * 1.4 if ym >= (2026, 1) else 0.0
        if profile == "growing" and ym >= (2026, 1):
            w *= 1.35
        elif profile == "declining" and ym >= (2026, 1):
            w *= 0.6
        return w

    def _pick_client(self, ym: tuple[int, int]) -> dict:
        pool = [(c, self._weight_for(c, ym)) for c in self.world["clients"]]
        total = sum(w for _, w in pool)
        if total <= 0:
            return self.rng.choice(pool)[0]
        pick = self.rng.uniform(0, total)
        acc = 0.0
        for client, w in pool:
            acc += w
            if pick <= acc:
                return client
        return pool[-1][0]

    def _next_serial(self) -> int:
        self.serial += 1
        return self.serial

    # -- jobs / projects -----------------------------------------------------
    def generate_month(self, ym: tuple[int, int]) -> None:
        year = ym[0]
        base = self.jobs_per_month / 2.1
        # Seasonality and growth live in the project count (not in the client
        # weights, where the pick normalisation would cancel them): a sine of
        # ±8% over the calendar year plus a +6% year-over-year trend.
        seasonal = 1.0 + 0.08 * math.sin(2.0 * math.pi * (ym[1] - 1) / 12.0)
        trend = 1.06 ** (year - self.months[0][0])
        n_projects = max(1, round(base * seasonal * trend))
        snap = self.snapshots[ym]
        for _ in range(n_projects):
            # ~10% of projects are reserved for the two late-gap cluster
            # clients: pure Pareto weighting can give them zero work in short
            # or small runs, which would make the late-job cluster too small.
            if self.rng.random() < 0.10:
                client = self.world["client_map"][self.rng.choice(self.world["late_cluster"])]
            else:
                client = self._pick_client(ym)
            # The late-gap PM runs ~80% of the two cluster clients' projects,
            # so the late-job cluster (that client AND that PM) is big enough
            # to carry ~70% of the planted late jobs.
            if client["name"] in self.world["late_cluster"] and self.rng.random() < 0.8:
                pm = self.world["late_pm"]
            else:
                pm = self.rng.choice(self.world["pms"])
            serial = self._next_serial()
            pid = f"{year}/{serial}"
            langs = self.rng.choice(LANG_PAIRS)
            spec = self.rng.choice(SPECIALIZATIONS)
            # In-house work comes in small projects; its XTRF export rows must
            # land at roughly 30-50% of a linguist's capacity (the tracker
            # fills the rest), so full project volumes (500-50,000 words)
            # would be ~10x too large.
            inhouse = self.rng.random() < 0.30
            volume = (round(self.rng.uniform(150, 1_200), 2) if inhouse
                      else round(self.rng.uniform(500, 50_000), 2))
            agreed = round(self.rng.lognormvariate(7.2, 1.1), 2)
            deadline_day = self.rng.randint(5, 28)
            deadline = self._deadline_dt(ym, deadline_day)
            start = self._deadline_dt(ym, max(1, deadline_day - self.rng.randint(1, 10)))
            margin = round(self.rng.uniform(15, 55))
            visible = min(self._rand_date(ym), snap)
            n_jobs = self.rng.randint(1, 3)
            job_rows = []
            for n in range(1, n_jobs + 1):
                jid = f"{pid}/#3/{n}"
                job = {
                    "id": jid, "pid": pid, "client": client["name"], "pm": pm,
                    "langs": langs, "spec": spec, "volume": volume,
                    "deadline": deadline, "start": start, "visible": visible,
                    "agreed": agreed, "margin": margin, "ym": ym,
                    "job_type": self.rng.choice(JOB_TYPES),
                    "status": self.rng.choices(
                        ["Ready", "Cancelled", "Open"], weights=[86, 7, 7])[0],
                    "vendor": None, "cost": round(agreed * self.rng.uniform(0.35, 0.7), 2),
                    "late": False, "roi": self.rng.randint(90, 100),
                    "completed": self.rng.randint(1, 40),
                }
                if inhouse:
                    job["vendor"] = self.rng.choice(self.world["linguists"])
                elif self.rng.random() < 0.9:
                    job["vendor"] = self.rng.choice(self.world["vendors"])
                job_rows.append(job)
                self.jobs.append(job)
            self.projects.append({
                "pid": pid, "client": client["name"], "pm": pm, "langs": langs,
                "volume": volume, "agreed": agreed, "margin": margin,
                "start": start, "deadline": deadline, "visible": visible, "ym": ym,
            })

    # -- planting ------------------------------------------------------------
    def plant_late_jobs(self) -> None:
        # Never plant in the last month of the period, nor in any December
        # (a December late job could only be recovered next year, which the
        # year-to-date sweep cannot do).
        last = self.months[-1]
        eligible = [j for j in self.jobs if j["ym"] != last and j["ym"][1] != 12]
        if not eligible:
            return
        cluster = [j for j in eligible
                   if j["client"] in self.world["late_cluster"] and j["pm"] == self.world["late_pm"]]
        others = [j for j in eligible if j not in cluster]
        picks: list[dict] = []
        n_cluster = round(self.late_target * 0.7)
        if n_cluster > len(cluster):
            raise ValueError(
                f"late-job cluster too small: need {n_cluster} jobs from the two cluster "
                f"clients + PM '{self.world['late_pm']}', found only {len(cluster)}"
            )
        picks.extend(self.rng.sample(cluster, n_cluster))
        rest = self.late_target - len(picks)
        if rest > len(others):
            raise ValueError(
                f"not enough non-cluster jobs to plant late: need {rest}, found {len(others)}"
            )
        picks.extend(self.rng.sample(others, rest))
        # A late job must be visible by the last snapshot of its own year:
        # the recovery sweep is year-to-date, so a 2025-deadline job appearing
        # only in a 2026 snapshot would be unrecoverable.
        last_snap_by_year: dict[int, int] = {}
        for ym in self.months:
            last_snap_by_year[ym[0]] = self.snapshots[ym].toordinal()
        for job in picks:
            lo = self.snapshots[job["ym"]].toordinal() + 1
            hi = last_snap_by_year[job["ym"][0]]
            if hi < lo:
                continue
            # Visible after its own month's snapshot; whenever it lands, the
            # first later run whose snapshot includes it recovers it, and the
            # year-to-date sweep always places it back in its deadline month.
            job["visible"] = date.fromordinal(self.rng.randint(lo, hi))
            job["late"] = True
            # A project becomes visible on the day of its first job — move the
            # whole project with it, or the projects view would show revenue
            # with no job rows and be swept as unplanted orphans.
            for proj in self.projects:
                if proj["pid"] == job["pid"] and proj["visible"] < job["visible"]:
                    proj["visible"] = job["visible"]
            rate = self.rates[job["ym"]]
            cost2, agreed2 = round(job["cost"], 2), round(job["agreed"], 2)
            self.planted.append({
                "id": job["id"], "kind": "late_job",
                "month": f"{job['ym'][0]}-{job['ym'][1]:02d}",
                "eur": {"total_cost": cost2, "agreed": agreed2},
                "usd": {"total_cost": f"{cost2 * rate:.5f}",
                        "agreed": f"{agreed2 * rate:.5f}"},
            })

    def plant_orphans(self) -> None:
        last = self.months[-1]
        eligible = [ym for ym in self.months if not (ym[1] == 12 or ym == last)]
        # pre-snapshot orphans may sit in any month incl. the last
        all_months = list(self.months)
        n = self.orphan_target
        if not eligible:
            return
        picks: list[tuple[tuple[int, int], str]] = []
        # mandatory special shapes
        for _ in range(min(6, n)):
            picks.append((self.rng.choice(all_months), "empty_deadline"))
        for _ in range(min(4, max(0, n - len(picks)))):
            picks.append((self.rng.choice(eligible), "stale_deadline"))
        while len(picks) < n:
            late_ok = self.rng.random() < 0.4 and eligible
            month = self.rng.choice(eligible if late_ok else all_months)
            mode = "post_snapshot" if late_ok else "pre_snapshot"
            picks.append((month, mode))

        for month, requested_mode in picks:
            client = self._pick_client(month)
            pm = self.rng.choice(self.world["pms"])
            pid = f"{month[0]}/{self._next_serial()}"
            agreed = round(self.rng.lognormvariate(7.2, 1.1), 2)
            deadline_day = self.rng.randint(5, 28)
            deadline = self._deadline_dt(month, deadline_day)
            start = self._deadline_dt(month, max(1, deadline_day - self.rng.randint(1, 10)))
            stale_deadline = date(2024, self.rng.randint(1, 12), self.rng.randint(1, 28))
            snap = self.snapshots[month]
            mode = requested_mode
            if mode == "empty_deadline":
                deadline_out, visible = None, min(self._rand_date(month), snap)
            elif mode == "stale_deadline":
                deadline_out, visible = stale_deadline, min(self._rand_date(month), snap)
            elif mode == "post_snapshot":
                later = [ym for ym in eligible if ym > month and ym[0] == month[0]]
                deadline_out = deadline
                if not later:
                    # No later month of the same year to catch it — a true
                    # post-snapshot plant would be unrecoverable, so it becomes
                    # an ordinary pre-snapshot orphan. Recorded as such.
                    mode = "pre_snapshot"
                    visible = min(self._rand_date(month), snap)
                else:
                    lo = snap.toordinal() + 1
                    hi = self.snapshots[later[-1]].toordinal()
                    visible = date.fromordinal(self.rng.randint(lo, hi))
            else:
                deadline_out, visible = deadline, min(self._rand_date(month), snap)

            # matched month = the month the ETL sweep will match on
            matched_month = month
            self.orphan_projects.append({
                "pid": pid, "client": client["name"], "pm": pm,
                "langs": self.rng.choice(LANG_PAIRS),
                "volume": round(self.rng.uniform(500, 50_000), 2),
                "agreed": agreed, "margin": round(self.rng.uniform(15, 55)),
                "start": start, "deadline": deadline_out, "visible": visible,
                "matched_month": matched_month, "ym": month,
            })
            rate = self.rates[month]
            self.planted.append({
                "id": pid, "kind": "orphan", "mode": mode,
                "month": f"{month[0]}-{month[1]:02d}",
                "eur": {"total_cost": None, "agreed": agreed},
                "usd": {"total_cost": "", "agreed": f"{agreed * rate:.5f}"},
            })

    # -- rendering ------------------------------------------------------------
    def _project_name(self, client: str, pid: str) -> str:
        return project_name(self.world, client, pid)

    def _job_row(self, job: dict) -> list[str]:
        vendor = f"{job['vendor']} (In-House)" if job["vendor"] in self.world["linguists"] else (
            job["vendor"] or "")
        return [
            job["id"], job["client"], self._project_name(job["client"], job["pid"]),
            job["langs"], job["spec"], job["job_type"], fmt_volume(job["volume"]),
            fmt_dt(job["deadline"], 20, 0), job["status"], job["pm"], vendor,
            str(job["completed"]), f"{job['roi']} %",
            fmt_eur(round(job["cost"], 2)), fmt_eur(round(job["agreed"], 2)),
            f"{job['margin']} %", "", f"{job['cost']:.2f}", f"{job['agreed']:.2f}",
        ]

    def _project_row(self, proj: dict) -> list[str]:
        name = self._project_name(proj["client"], proj["pid"])
        return [
            proj["pid"], name, proj["client"], proj["langs"], "Translation",
            proj["pm"], fmt_volume(proj["volume"]),
            f"{proj['agreed']:.2f}", f"{proj['agreed']:.2f}",
            fmt_dt(proj["start"], 9, 0),
            fmt_dt(proj["deadline"], 20, 0) if proj["deadline"] else "",
            f"{proj['agreed']:.2f}", fmt_eur(round(proj["agreed"], 2)),
            f"{proj['margin']} %",
        ]

    def write_snapshot(self, out: Path, through_month: int) -> None:
        """Write the snapshot folder for self.months[through_month]."""
        ym = self.months[through_month]
        snap_ord = self.snapshots[ym].toordinal()
        snap_date = self.snapshots[ym]
        folder = out / "snapshots" / f"{ym[0]}-{ym[1]:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        ts = f"{snap_date.day:02d}-{snap_date.month:02d}-{snap_date.year:04d} 09:41"

        jobs = [j for j in self.jobs if j["visible"].toordinal() <= snap_ord]
        jobs.sort(key=lambda j: (j["deadline"], j["id"]))
        with open(folder / "jobs_analysis.csv", "w", encoding="utf-8-sig", newline="") as f:
            f.write(f"Generated by XTRF on {ts} GMT+03:00\n\n")
            w = csv.writer(f, delimiter=";")
            w.writerow(JOBS_HEADER)
            for job in jobs:
                w.writerow(self._job_row(job))

        projs = self.projects + self.orphan_projects
        projs = [p for p in projs if p["visible"].toordinal() <= snap_ord]
        projs.sort(key=lambda p: (p["deadline"] or p["start"], p["pid"]))
        with open(folder / "projects_default.csv", "w", encoding="utf-8-sig", newline="") as f:
            f.write(f"Generated by XTRF on {ts} GMT+03:00\n\n")
            w = csv.writer(f, delimiter=";")
            w.writerow(PROJECTS_HEADER)
            for proj in projs:
                w.writerow(self._project_row(proj))


# ---------------------------------------------------------------------------
# Utilization files
# ---------------------------------------------------------------------------

def _capacity(ym: tuple[int, int]) -> dict[str, int]:
    return CAPACITY_AFTER if ym >= (2026, 9) else CAPACITY_BEFORE


# XTRF job types -> capacity task types, exactly as the utilization model's
# Power Query maps the per-linguist exports.
XTRF_TYPE_TO_TRACKER = {
    "translation": "Translation", "tep": "Shooting", "editing": "Revision",
    "mtpe": "MTPE", "lqa": "QA", "proofreading": "QA",
}
# Hand-kept sheets spell the types loosely; canonical -> variants actually written.
TRACKER_SPELLINGS = {
    "Translation": ["Trans", "Translation"],
    "Revision": ["Rev", "Revision"],
    "MTPE": ["MTPE", "MTP"],
    "QA": ["QA", "Proofreading", "Proof"],
    "Shooting": ["Shooting", "Shoot"],
}
# Tracker spellings the model's Power Query recognises (exact, case-insensitive).
TRACKER_LABELS = {
    "shoot": "Shooting", "shooting": "Shooting",
    "rev": "Revision", "revisio": "Revision", "revision": "Revision",
    "mtp": "MTPE", "mtpe": "MTPE",
    "qa": "QA", "visual qa": "QA", "proof": "QA", "pro": "QA", "proofreading": "QA",
    "proofreading (arabic)": "QA",
    "trans": "Translation", "translation": "Translation",
}
# Unit spellings as they appear in the hand-kept sheet; the model normalises them.
WORD_UNITS = ["Word (wwc)"] * 6 + ["word", "words"]
HOUR_UNITS = ["Hour", "Hour", "hour", "HOUR"]
TRACKER_TYPE_MIX = [("Translation", 0.45), ("Revision", 0.20), ("MTPE", 0.15),
                    ("QA", 0.12), ("Shooting", 0.08)]
WORKDAYS = 21


def _canonical_type(label: str) -> str | None:
    """Tracker job-type label -> capacity task type, as the model maps it."""
    low = label.strip().lower()
    if low.endswith("_external"):
        low = low[: -len("_external")]
    return TRACKER_LABELS.get(low)


def _export_type(job_type: str) -> str | None:
    """XTRF export job type -> capacity task type, as the model maps it."""
    low = job_type.strip().lower()
    if low.startswith("mtpe"):
        return "MTPE"
    return XTRF_TYPE_TO_TRACKER.get(low)


def _tracker_rows(rng: random.Random, rows_out: list[list[str]], linguist: str,
                  ym: tuple[int, int], words_by_type: dict[str, float],
                  date_with_time: bool, allow_hours: bool = True) -> None:
    """Split per-type word volumes into hand-kept tracker rows.

    An occasional row is logged in Hours (1 hour = that type's daily capacity
    divided by 8), like the real sheet. The dedicated team's tracker is
    words-only: its query has no hour conversion.
    """
    for task_type, words in words_by_type.items():
        cap = _capacity(ym)[task_type]
        while words > 100:
            chunk = min(words, rng.uniform(500, 3000))
            words -= chunk
            day = rng.randint(1, 28)
            d = date(ym[0], ym[1], day)
            spelling = rng.choice(TRACKER_SPELLINGS[task_type])
            if rng.random() < 0.12 and allow_hours:
                rows_out.append([
                    linguist, spelling, f"{chunk / (cap / 8):.2f}", rng.choice(HOUR_UNITS),
                    tracker_date(d, date_with_time),
                ])
            else:
                rows_out.append([
                    linguist, spelling, f"{chunk:.0f}", rng.choice(WORD_UNITS),
                    tracker_date(d, date_with_time),
                ])


def write_utilization(rng: random.Random, out: Path, world: dict,
                      months: list[tuple[int, int]], jobs: list[dict]) -> None:
    util = out / "utilization"
    (util / "Linguists").mkdir(parents=True, exist_ok=True)

    status_rows: list[list[str]] = []
    team_rows: list[list[str]] = []
    team_linguists = world["linguists"][:4]  # fixed subset: the second team
    for ym in months:
        caps = _capacity(ym)
        for linguist in world["linguists"]:
            # Export side: the linguist's XTRF jobs this month, converted to
            # capacity-days. Small in-house projects keep this at ~30-50%.
            export_cd = {t: 0.0 for t in caps}
            for job in jobs:
                # late jobs are left out of the per-linguist exports below
                if job["vendor"] != linguist or job["ym"] != ym or job["late"]:
                    continue
                t = XTRF_TYPE_TO_TRACKER[job["job_type"]]
                export_cd[t] += job["volume"] / caps[t]
            # draw high enough that the chunking remainder and Hour rounding
            # keep the computed utilization inside 0.6-1.1
            target = rng.uniform(0.72, 1.06) * WORKDAYS  # in capacity-days
            words_by_type = {}
            for t, share in TRACKER_TYPE_MIX:
                residual_cd = max(0.0, target * share - export_cd[t])
                words_by_type[t] = residual_cd * caps[t]
            _tracker_rows(rng, status_rows, linguist, ym, words_by_type, True)
        # dedicated team's own tracker (Team_Tracker.csv), realistic volumes
        for linguist in team_linguists:
            target = rng.uniform(0.6, 1.0) * WORKDAYS
            words_by_type = {t: target * share * caps[t]
                             for t, share in TRACKER_TYPE_MIX}
            _tracker_rows(rng, team_rows, linguist, ym, words_by_type, False,
                          allow_hours=False)

    with open(util / "New Status1.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["vendor", "Job Type", "Volume", "Calculation unit", "Date"])
        w.writerows(status_rows)
    with open(util / "Team_Tracker.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["vendor", "Job Type", "Volume", "Calculation unit", "Date", "Date M"])
        w.writerows(team_rows)

    # per-linguist XTRF exports, one per linguist per month
    for linguist in world["linguists"]:
        folder = util / "Linguists" / linguist
        folder.mkdir(parents=True, exist_ok=True)
        for ym in months:
            last_day = calendar.monthrange(ym[0], ym[1])[1]
            month_jobs = [j for j in jobs if j["vendor"] == linguist and j["ym"] == ym
                          and not j["late"]]
            month_jobs.sort(key=lambda j: (j["deadline"], j["id"]))
            fname = (f"XTRF-Job-05-{ym[1]:02d}-{ym[0]} 09_41 GMT+03_00 "
                     f"{ym[1]:02d}_{ym[0]}.csv")
            with open(folder / fname, "w", encoding="utf-8-sig", newline="") as f:
                f.write(
                    f'Deadline,"[{ym[1]}/1/{str(ym[0])[-2:]}, 12:00 PM, '
                    f'{ym[1]}/{last_day}/{str(ym[0])[-2:]}, 12:00 PM]"\n'
                )
                f.write(f"Vendor,{linguist} (In-House)\n\n")
                w = csv.writer(f)
                w.writerow(LINGUIST_EXPORT_HEADER)
                for job in month_jobs:
                    row = [
                        job["id"], job["client"],
                        project_name(world, job["client"], job["pid"]),
                        job["langs"], job["spec"], job["job_type"], fmt_volume(job["volume"]),
                        fmt_dt(job["deadline"], 20, 0), job["status"], job["pm"],
                        f"{linguist} (In-House)", str(job["completed"]), f"{job['roi']} %",
                        fmt_eur(round(job["cost"], 2)), fmt_eur(round(job["agreed"], 2)),
                        f"{job['margin']} %",
                    ]
                    w.writerow(row)


_MONTH_TAG_RE = re.compile(r" (\d{2})_(\d{4})\.csv$")


def compute_utilization(data_dir: Path) -> dict[tuple[str, str], float]:
    """Per-linguist-month utilization from the written files.

    Utilization = (sum over task types of volume / that type's daily capacity)
    / 21 workdays, combining each linguist's XTRF export rows with their
    New Status1.csv tracker rows. Returns {(linguist, 'YYYY-MM'): utilization}.
    """
    data_dir = Path(data_dir)
    util: dict[tuple[str, str], float] = {}
    ling_root = data_dir / "utilization" / "Linguists"
    for folder in sorted(ling_root.iterdir()):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("XTRF-Job-*.csv")):
            match = _MONTH_TAG_RE.search(path.name)
            if not match:
                continue
            key = (folder.name, f"{match.group(2)}-{match.group(1)}")
            caps = _capacity((int(match.group(2)), int(match.group(1))))
            with open(path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))
            header_idx = next(
                (i for i, r in enumerate(rows) if r and r[0].strip() == "ID"), None)
            if header_idx is None:
                continue
            total = util.get(key, 0.0)
            for r in rows[header_idx + 1:]:
                if len(r) < 7 or not r[0].strip():
                    continue
                t = _export_type(r[5])
                if t:
                    total += _parse_number(r[6]) / caps[t]
            util[key] = total

    with open(data_dir / "utilization" / "New Status1.csv", encoding="utf-8",
              newline="") as f:
        for row in csv.DictReader(f):
            day, mon, yr = row["Date"].split()[0].split("/")
            ym = (int(yr), int(mon))
            key = (row["vendor"], f"{yr}-{int(mon):02d}")
            t = _canonical_type(row["Job Type"])
            if not t:
                continue
            cap = _capacity(ym)[t]
            vol = _parse_number(row["Volume"])
            if row["Calculation unit"].strip().lower().startswith("hour"):
                vol = vol * cap / 8  # 1 hour = daily capacity / 8
            util[key] = util.get(key, 0.0) + vol / cap

    return {k: v / WORKDAYS for k, v in util.items()}


def _parse_number(text: str) -> float:
    return float(text.replace(",", "").strip())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(out: Path, seed: int, start: tuple[int, int] = (2025, 1), num_months: int = 21,
        jobs_per_month: int = 1000, late_jobs: int = 320, orphans: int = 110) -> Path:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    months = month_range(start, num_months)
    world = build_world(rng)
    gen = Generator(rng, world, months, jobs_per_month, late_jobs, orphans)

    for ym in months:
        gen.generate_month(ym)
    gen.plant_late_jobs()
    gen.plant_orphans()

    # FX rates
    with open(out / "fx_rates.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["month", "eur_usd"])
        for ym in months:
            w.writerow([f"{ym[0]}-{ym[1]:02d}", f"{gen.rates[ym]:.4f}"])

    # world.json
    (out / "world.json").write_text(json.dumps(world, indent=2), encoding="utf-8")

    # snapshots: month M's dump is taken 3-6 days into M+1
    for i in range(len(months)):
        gen.write_snapshot(out, i)

    # planted.json + period metadata
    gen.planted.sort(key=lambda p: (p["kind"], p["month"], p["id"]))
    (out / "planted.json").write_text(json.dumps(gen.planted, indent=2), encoding="utf-8")
    (out / "period.json").write_text(json.dumps({
        "first_month": f"{months[0][0]}-{months[0][1]:02d}",
        "last_month": f"{months[-1][0]}-{months[-1][1]:02d}",
    }), encoding="utf-8")

    write_utilization(rng, out, world, months, gen.jobs)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate synthetic XTRF-style exports.")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--start-year", type=int, default=2025)
    ap.add_argument("--start-month", type=int, default=1)
    ap.add_argument("--num-months", type=int, default=21)
    ap.add_argument("--jobs-per-month", type=int, default=1000)
    ap.add_argument("--late-jobs", type=int, default=320)
    ap.add_argument("--orphans", type=int, default=110)
    args = ap.parse_args(argv)
    run(args.out, args.seed, (args.start_year, args.start_month), args.num_months,
        args.jobs_per_month, args.late_jobs, args.orphans)
    print(f"Generated {args.num_months} months into {args.out} (seed {args.seed})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
