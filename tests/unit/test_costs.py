import json
from decimal import Decimal
from pathlib import Path

import pytest

from stackcheck import costs
from stackcheck.config import Settings
from stackcheck.errors import BudgetExceeded, ConfigError


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    kwargs: dict[str, object] = {
        "data_dir": tmp_path,
        "meta_api_key": "test",
        "baseten_api_key": "test",
        "fireworks_api_key": "test",
    }
    kwargs.update(overrides)
    return Settings(**kwargs)  # type: ignore[arg-type]


def test_contributor_prices(tmp_path: Path) -> None:
    assert costs.record(
        provider="meta",
        model="muse-spark-1.3-contributor",
        purpose="t",
        check_id=None,
        input_tokens=1_000_000,
        data_dir=tmp_path,
    ) == Decimal("0.10")
    assert costs.record(
        provider="meta",
        model="muse-spark-1.3-contributor",
        purpose="t",
        check_id=None,
        input_tokens=1_000_000,
        cached_input_tokens=1_000_000,
        data_dir=tmp_path,
    ) == Decimal("0.002")


def test_standard_output_price(tmp_path: Path) -> None:
    assert costs.record(
        provider="meta",
        model="muse-spark-1.3",
        purpose="t",
        check_id=None,
        output_tokens=1_000_000,
        data_dir=tmp_path,
    ) == Decimal("4.25")


def test_sam_image_and_video_prices(tmp_path: Path) -> None:
    assert costs.record(
        provider="meta", model="sam-3.1", purpose="t", check_id=None, units=4, data_dir=tmp_path
    ) == Decimal("0.01")
    assert costs.record(
        provider="meta", model="sam-3.1-video", purpose="t", check_id=None, units=10, data_dir=tmp_path
    ) == Decimal("0.01")


def test_cache_hit_costs_zero(tmp_path: Path) -> None:
    assert costs.record(
        provider="meta",
        model="muse-spark-1.3",
        purpose="t",
        check_id=None,
        input_tokens=1000,
        output_tokens=500,
        cache_hit=True,
        data_dir=tmp_path,
    ) == Decimal("0")


def test_unknown_model_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        costs.record(
            provider="meta", model="nope", purpose="t", check_id=None, input_tokens=10, data_dir=tmp_path
        )


def test_check_budget_raises_at_limit(tmp_path: Path) -> None:
    settings = _settings(tmp_path, budget_usd=Decimal("0.05"))
    costs.record(
        provider="meta",
        model="muse-spark-1.3-contributor",
        purpose="t",
        check_id=None,
        input_tokens=1_000_000,
        data_dir=tmp_path,
    )
    with pytest.raises(BudgetExceeded):
        costs.check_budget(settings)


def test_alert_logs_once(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    old, costs._alerted = costs._alerted, False
    try:
        settings = _settings(tmp_path, budget_usd=Decimal("150"), alert_usd=Decimal("0.01"))
        costs.record(
            provider="meta",
            model="muse-spark-1.3-contributor",
            purpose="t",
            check_id=None,
            input_tokens=1_000_000,
            data_dir=tmp_path,
        )
        with caplog.at_level("WARNING", logger="stackcheck.costs"):
            costs.check_budget(settings)
            costs.check_budget(settings)
        assert len([r for r in caplog.records if "budget" in r.message.lower()]) == 1
    finally:
        costs._alerted = old


def test_ledger_has_one_json_line_per_record(tmp_path: Path) -> None:
    costs.record(
        provider="meta",
        model="muse-spark-1.3",
        purpose="a",
        check_id="chk_1",
        input_tokens=10,
        output_tokens=5,
        data_dir=tmp_path,
    )
    costs.record(
        provider="meta",
        model="muse-spark-1.3",
        purpose="b",
        check_id="chk_1",
        cache_hit=True,
        data_dir=tmp_path,
    )
    lines = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert first["purpose"] == "a" and first["cache_hit"] is False
    assert set(first) == {
        "ts",
        "provider",
        "model",
        "purpose",
        "check_id",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "units",
        "usd",
        "cache_hit",
        "estimated_price",
    }
    assert second["cache_hit"] is True and Decimal(second["usd"]) == Decimal("0")


def test_record_error_costs_nothing_and_shows_in_ledger(tmp_path: Path) -> None:
    costs.record(
        provider="meta",
        model="sam-3.1",
        purpose="sam:building",
        check_id="chk_1",
        error="RuntimeError: boom",
        data_dir=tmp_path,
    )
    lines = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["error"] == "RuntimeError: boom"
    assert Decimal(row["usd"]) == Decimal("0")
    summary = costs.summarize(tmp_path, "chk_1")
    assert summary.calls == 1 and summary.usd_total == Decimal("0")
