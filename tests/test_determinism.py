"""Test 7: determinism — same seed byte-identical, different seed different."""
from __future__ import annotations

import hashlib
from pathlib import Path

from generator.generate import run as generate


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(root)).encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def test_same_seed_identical_bytes(tmp_path: Path):
    a, b = tmp_path / "a", tmp_path / "b"
    generate(a, seed=42, start=(2025, 1), num_months=3, jobs_per_month=60,
             late_jobs=3, orphans=2)
    generate(b, seed=42, start=(2025, 1), num_months=3, jobs_per_month=60,
             late_jobs=3, orphans=2)
    assert _tree_hash(a) == _tree_hash(b)


def test_different_seed_different_bytes(tmp_path: Path):
    a, b = tmp_path / "a", tmp_path / "b"
    generate(a, seed=42, start=(2025, 1), num_months=3, jobs_per_month=60,
             late_jobs=3, orphans=2)
    generate(b, seed=43, start=(2025, 1), num_months=3, jobs_per_month=60,
             late_jobs=3, orphans=2)
    assert _tree_hash(a) != _tree_hash(b)
