"""Test 5: a stale prior-year Deadline orphan must carry the matched Start Date."""
from __future__ import annotations

from etl.enrich import find_orphan_project_rows, map_project_row

PROJECTS_HEADER = [
    "ID", "Name", "Client > Legal Name", "Languages", "Service", "Project Manager > Name",
    "Volume", "Total Cost", "Total Agreed", "Start Date and Time", "Deadline",
    "Total Agreed (without currency)", "Total Agreed [€]", "Margin",
]


def test_stale_deadline_orphan_uses_start_date():
    row = {
        "ID": "2026/5001",
        "Name": "Stale",
        "Client > Legal Name": "Test Client",
        "Languages": "EN-US » FR-FR",
        "Project Manager > Name": "PM One",
        "Volume": "1,000.00",
        "Start Date and Time": "10-03-2026 08:00 GMT+03:00",
        "Deadline": "15-11-2024 20:00 GMT+03:00",
        "Total Agreed [€]": "€1,234.56",
        "Margin": "35 %",
    }
    orphans = find_orphan_project_rows([row], set(), 3, 2026, set(), from_month=1)
    assert len(orphans) == 1
    mapped = map_project_row(orphans[0], 1.1)
    # Deadline field of the mapped row (index 7 in the 19-column jobs layout)
    assert mapped[7].startswith("10-03-2026"), mapped[7]
