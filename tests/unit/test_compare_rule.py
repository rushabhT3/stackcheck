"""Unit tests for the T12 provider choice rule. Pure function, no live calls."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from compare_providers import _refusal, choose_default


def _result(accuracy: float, cost: float, p50: float, cached: bool = False) -> dict:
    return {
        "model": "m",
        "accuracy": accuracy,
        "cost_per_filing": cost,
        "p50_seconds": p50,
        "cache_used": cached,
    }


def test_no_refusal_when_all_real() -> None:
    results = {"meta": _result(0.96, 0.01, 30.0), "baseten": _result(0.95, 0.02, 40.0)}
    assert _refusal(results) is None


def test_refusal_names_cached_provider() -> None:
    results = {"meta": _result(0.96, 0.0, 0.0, cached=True), "baseten": _result(0.95, 0.02, 40.0)}
    reason = _refusal(results)
    assert reason is not None and "meta" in reason


def test_refusal_when_nothing_succeeded() -> None:
    assert _refusal({}) is not None


def test_open_within_two_points_wins() -> None:
    results = {
        "meta": _result(0.96, 0.010, 30.0),
        "baseten": _result(0.95, 0.020, 40.0),
        "fireworks": _result(0.93, 0.005, 20.0),
    }
    assert choose_default(results) == "baseten"


def test_no_open_within_two_points_stays_meta() -> None:
    results = {
        "meta": _result(0.96, 0.010, 30.0),
        "baseten": _result(0.90, 0.001, 10.0),
        "fireworks": _result(0.90, 0.001, 10.0),
    }
    assert choose_default(results) == "meta"


def test_cheaper_open_wins() -> None:
    results = {
        "meta": _result(0.96, 0.010, 30.0),
        "baseten": _result(0.955, 0.030, 40.0),
        "fireworks": _result(0.95, 0.010, 20.0),
    }
    assert choose_default(results) == "fireworks"


def test_equal_cost_faster_open_wins() -> None:
    results = {
        "meta": _result(0.96, 0.010, 30.0),
        "baseten": _result(0.95, 0.020, 25.0),
        "fireworks": _result(0.95, 0.020, 60.0),
    }
    assert choose_default(results) == "baseten"


def test_missing_providers_handled() -> None:
    assert choose_default({"meta": _result(0.96, 0.010, 30.0)}) == "meta"
    assert choose_default({}) == "meta"
