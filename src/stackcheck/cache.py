"""Disk cache for anything that costs money. Keyed on the exact request."""

import hashlib
import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path


def cache_key(parts: dict[str, object]) -> str:
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _path(data_dir: Path, key: str) -> Path:
    return data_dir / "cache" / key[:2] / f"{key}.json"


async def get_or_compute(
    parts: dict[str, object], compute: Callable[[], Awaitable[dict]], *, data_dir: Path
) -> tuple[dict, bool]:
    """Return (value, cache_hit). Failed computes are never stored.

    STACKCHECK_FRESH=1 skips the read so the call really runs (still writes).
    """
    key = cache_key(parts)
    path = _path(data_dir, key)
    fresh = os.environ.get("STACKCHECK_FRESH") == "1"
    if not fresh and path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            stored = None
        if isinstance(stored, dict) and isinstance(stored.get("value"), dict):
            return stored["value"], True
    value = await compute()
    payload = {"key_parts": parts, "created_at": datetime.now(UTC).isoformat(), "value": value}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)
    return value, False
