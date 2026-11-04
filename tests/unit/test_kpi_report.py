"""Unit tests for the T14 KPI report. Pure helpers only, no live calls."""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from kpi_report import (
    cost_per_check,
    count_open_stuck,
    daily_usd,
    kpi_status,
    parse_ledger,
    rerun_cache_share,
)


def test_status_higher_is_better() -> None:
    assert kpi_status(96.0, 95.0, True, 85.0) == "PASS"
    assert kpi_status(90.0, 95.0, True, 85.0) == "WARN"
    assert kpi_status(80.0, 95.0, True, 85.0) == "FAIL"
    assert kpi_status(85.0, 95.0, True, 85.0) == "FAIL"


def test_status_lower_is_better() -> None:
    assert kpi_status(0.4, 0.5, False, 2.0) == "PASS"
    assert kpi_status(1.0, 0.5, False, 2.0) == "WARN"
    assert kpi_status(2.5, 0.5, False, 2.0) == "FAIL"
    assert kpi_status(2.0, 0.5, False, 2.0) == "FAIL"


def test_status_no_data() -> None:
    assert kpi_status(None, 95.0, True, 85.0) == "NO DATA"
    assert kpi_status(None, 0.5, False, 2.0) == "NO DATA"


def _ledger_lines() -> list[str]:
    import json

    rows = [
        {"ts": "2026-10-28T10:00:00+00:00", "check_id": "chk_a", "usd": "0.01", "cache_hit": False},
        {"ts": "2026-10-28T20:00:00+00:00", "check_id": "chk_a", "usd": "0.02", "cache_hit": False},
        {"ts": "2026-10-29T01:00:00+00:00", "check_id": "chk_b", "usd": "0.05", "cache_hit": False},
        {"ts": "2026-10-29T02:00:00+00:00", "check_id": "chk_b", "usd": "0", "cache_hit": True},
        {"ts": "2026-10-28T10:30:00+00:00", "check_id": None, "usd": "0.10", "cache_hit": False},
    ]
    return [json.dumps(row) for row in rows]


def test_ledger_daily_sums_use_ist() -> None:
    rows = parse_ledger(_ledger_lines())
    assert daily_usd(rows) == {date(2026, 10, 28): Decimal("0.11"), date(2026, 10, 29): Decimal("0.07")}


def test_ledger_cost_per_check_skips_null() -> None:
    rows = parse_ledger(_ledger_lines())
    assert cost_per_check(rows) == {"chk_a": Decimal("0.03"), "chk_b": Decimal("0.05")}


def test_ledger_rerun_cache_share() -> None:
    rows = parse_ledger(_ledger_lines())
    assert rerun_cache_share(rows) == 0.5


def test_stuck_counts_open_only() -> None:
    text = "## T99: something broken\n\n## T98: other thing: RESOLVED\n"
    assert count_open_stuck(text) == 1
