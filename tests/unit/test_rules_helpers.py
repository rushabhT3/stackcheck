"""Unit tests for rule helpers: dates, money, claims, windows, formatting."""

from datetime import date
from decimal import Decimal

import pytest

from stackcheck.models import BlockProgress
from stackcheck.rules.checks import (
    apply_user_floors,
    claimed_slabs,
    format_day,
    format_inr,
    photo_in_window,
    quarter_start,
    to_inr,
    user_floors_inputs,
)
from tests.factories import make_quarter


def _block(
    slabs: int | None = None,
    percent: float | None = None,
    floors: int | None = None,
) -> BlockProgress:
    quarter = make_quarter(date(2025, 3, 31), slabs=slabs, percent=percent, floors=floors)
    return quarter.filing.blocks[0]


def test_quarter_start() -> None:
    assert quarter_start(date(2025, 3, 31)) == date(2025, 1, 1)
    assert quarter_start(date(2025, 6, 30)) == date(2025, 4, 1)
    assert quarter_start(date(2025, 12, 31)) == date(2025, 10, 1)
    assert quarter_start(date(2025, 1, 31)) == date(2024, 11, 1)


def test_to_inr() -> None:
    assert to_inr(Decimal("2.5"), "INR_LAKH") == Decimal("250000")
    assert to_inr(Decimal("1.2"), "INR_CRORE") == Decimal("12000000")
    assert to_inr(Decimal("999"), "INR") == Decimal("999")
    assert to_inr(Decimal("999"), None) == Decimal("999")
    assert to_inr(None, "INR_LAKH") is None


def test_to_inr_unknown_unit_raises() -> None:
    with pytest.raises(ValueError, match="unit"):
        to_inr(Decimal("1"), "NOPE")


def test_claimed_slabs() -> None:
    assert claimed_slabs(_block(slabs=3)) == 3
    assert claimed_slabs(_block(percent=40, floors=4)) == 2
    assert claimed_slabs(_block(percent=40)) is None
    assert claimed_slabs(_block(slabs=5, percent=40, floors=4)) == 5


def test_photo_in_window() -> None:
    quarter_end = date(2025, 3, 31)
    assert photo_in_window(None, quarter_end) is None
    assert photo_in_window(date(2024, 11, 17), quarter_end) is True
    assert photo_in_window(date(2024, 11, 16), quarter_end) is False
    assert photo_in_window(date(2025, 4, 15), quarter_end) is True
    assert photo_in_window(date(2025, 4, 16), quarter_end) is False


def test_format_inr() -> None:
    assert format_inr(Decimal("10105126")) == "₹1,01,05,126"
    assert format_inr(Decimal("999")) == "₹999"
    assert format_inr(Decimal("100000")) == "₹1,00,000"


def test_format_day() -> None:
    assert format_day(date(2025, 3, 31)) == "31 Mar 2025"


def test_apply_user_floors_fills_missing() -> None:
    """The user counts the ground floor too: 5 for G+4 fills sanctioned 4."""
    quarter = make_quarter(date(2025, 3, 31), percent=80.0)
    apply_user_floors([quarter], 5)
    block = quarter.filing.blocks[0]
    assert block.floors_above_ground_sanctioned == 4
    assert block.floors_entered_by_user == 5
    assert claimed_slabs(block) == 4


def test_apply_user_floors_skips_filing_floors() -> None:
    quarter = make_quarter(date(2025, 3, 31), percent=80.0, floors=10)
    apply_user_floors([quarter], 5)
    block = quarter.filing.blocks[0]
    assert block.floors_above_ground_sanctioned == 10
    assert block.floors_entered_by_user is None


def test_apply_user_floors_none_is_noop() -> None:
    quarter = make_quarter(date(2025, 3, 31), percent=80.0)
    apply_user_floors([quarter], None)
    assert quarter.filing.blocks[0].floors_above_ground_sanctioned is None


def test_user_floors_inputs_marks_percent_claims_only() -> None:
    entered = make_quarter(date(2025, 3, 31), percent=80.0)
    apply_user_floors([entered], 5)
    assert user_floors_inputs(entered.filing.blocks[0]) == {"floors_entered_by_you": "5"}
    counted = make_quarter(date(2025, 3, 31), slabs=3)
    apply_user_floors([counted], 5)
    assert user_floors_inputs(counted.filing.blocks[0]) == {}
