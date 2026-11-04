"""Cost ledger: every paid call is priced and appended to data/ledger.jsonl."""

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from stackcheck.config import Settings
from stackcheck.errors import BudgetExceeded, ConfigError
from stackcheck.models import CostSummary

logger = logging.getLogger(__name__)

_TOKEN_PRICES: dict[str, dict[str, Decimal]] = {
    "meta/muse-spark-1.3": {"input": Decimal("1.25"), "cached": Decimal("0.15"), "output": Decimal("4.25")},
    "meta/muse-spark-1.3-contributor": {
        "input": Decimal("0.10"),
        "cached": Decimal("0.002"),
        "output": Decimal("0.20"),
    },
    "fireworks/accounts/fireworks/models/muse-spark-open": {
        "input": Decimal("1.25"),
        "cached": Decimal("0.15"),
        "output": Decimal("4.25"),
    },
    "baseten/meta-models/muse-spark-open-1026": {
        "input": Decimal("1.25"),
        "cached": Decimal("0.15"),
        "output": Decimal("4.25"),
    },
}

_ESTIMATED_PRICES: set[str] = set()  # Baseten row verified Oct 29, 2026; was estimated until T12

_SAM_IMAGE_PRICE = Decimal("0.0025")
_SAM_VIDEO_PRICE_PER_FRAME = Decimal("0.0002")
_SAM_VIDEO_MIN_FRAMES = 50

_alerted = False


def _sam_usd(model: str, units: int) -> Decimal:
    if model == "sam-3.1":
        return _SAM_IMAGE_PRICE * units
    if model == "sam-3.1-video":
        return _SAM_VIDEO_PRICE_PER_FRAME * max(units, _SAM_VIDEO_MIN_FRAMES)
    raise ConfigError(f"Unknown priced model: meta/{model}")


def record(
    *,
    provider: str,
    model: str,
    purpose: str,
    check_id: str | None,
    input_tokens: int = 0,
    cached_input_tokens: int = 0,
    output_tokens: int = 0,
    units: int = 0,
    cache_hit: bool = False,
    error: str | None = None,
    data_dir: Path,
) -> Decimal:
    """Price one call, append it to the ledger, return the USD cost.

    A failed call records with error set and costs nothing, so failures
    show in the ledger without inflating spend.
    """
    key = f"{provider}/{model}"
    if error is not None or cache_hit:
        usd = Decimal("0")
    elif units:
        if provider != "meta":
            raise ConfigError(f"Unknown priced model: {key}")
        usd = _sam_usd(model, units)
    elif key in _TOKEN_PRICES:
        prices = _TOKEN_PRICES[key]
        uncached = input_tokens - cached_input_tokens
        usd = (
            prices["input"] * uncached
            + prices["cached"] * cached_input_tokens
            + prices["output"] * output_tokens
        ) / Decimal("1000000")
    else:
        raise ConfigError(f"Unknown priced model: {key}")
    usd = usd.quantize(Decimal("0.000001"))
    line: dict[str, object] = {
        "ts": datetime.now(UTC).isoformat(),
        "provider": provider,
        "model": model,
        "purpose": purpose,
        "check_id": check_id,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "units": units,
        "usd": str(usd),
        "cache_hit": cache_hit,
        "estimated_price": key in _ESTIMATED_PRICES,
    }
    if error is not None:
        line["error"] = error
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "ledger.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    return usd


def total_usd(data_dir: Path) -> Decimal:
    ledger = data_dir / "ledger.jsonl"
    if not ledger.exists():
        return Decimal("0")
    total = Decimal("0")
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if line.strip():
            total += Decimal(json.loads(line)["usd"])
    return total


def summarize(data_dir: Path, check_id: str) -> CostSummary:
    """CostSummary for one check from the ledger. A missing ledger means zeros."""
    summary = CostSummary(usd_total=Decimal("0"), by_model={}, calls=0, cache_hits=0)
    ledger = data_dir / "ledger.jsonl"
    if not ledger.exists():
        return summary
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("check_id") != check_id:
            continue
        summary.calls += 1
        if row.get("cache_hit"):
            summary.cache_hits += 1
        usd = Decimal(row["usd"])
        summary.usd_total += usd
        key = f"{row['provider']}/{row['model']}"
        summary.by_model[key] = summary.by_model.get(key, Decimal("0")) + usd
    return summary


def check_budget(settings: Settings) -> None:
    """Raise before a paid call would cross the budget. Log once at the alert line."""
    global _alerted
    total = total_usd(settings.data_dir)
    if total >= settings.budget_usd:
        raise BudgetExceeded(f"Spent {total} USD, budget is {settings.budget_usd} USD")
    if total >= settings.alert_usd and not _alerted:
        _alerted = True
        logger.warning("Approaching budget: spent %s USD of %s USD", total, settings.budget_usd)
