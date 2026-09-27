"""The scheduled scan's gate: exactly one of each pair of crons runs, however late."""

import subprocess
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parent.parent / "scripts" / "scan_gate.sh"


def gate(event: str, cron: str, offset: str) -> str:
    result = subprocess.run(["bash", str(GATE), event, cron, offset],
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.mark.parametrize("cron,offset,expected", [
    ("0 6 * * *", "+0100", "go=true"),    # 07:00 BST
    ("0 7 * * *", "+0100", "go=false"),   # 08:00 BST
    ("0 16 * * *", "+0100", "go=true"),   # 17:00 BST
    ("0 17 * * *", "+0100", "go=false"),
    ("0 6 * * *", "+0000", "go=false"),   # 06:00 GMT
    ("0 7 * * *", "+0000", "go=true"),    # 07:00 GMT
    ("0 16 * * *", "+0000", "go=false"),
    ("0 17 * * *", "+0000", "go=true"),
])
def test_one_cron_of_each_pair_runs(cron, offset, expected):
    assert gate("schedule", cron, offset) == expected


def test_manual_runs_always_go():
    assert gate("workflow_dispatch", "", "+0100") == "go=true"


def test_an_unknown_offset_runs_rather_than_silently_skipping():
    assert gate("schedule", "0 7 * * *", "+0200") == "go=true"
