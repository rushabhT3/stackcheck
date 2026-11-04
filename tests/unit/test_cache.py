import json
from pathlib import Path

import pytest

from stackcheck.cache import cache_key, get_or_compute


def test_same_parts_same_key_regardless_of_dict_order() -> None:
    assert cache_key({"b": 1, "a": 2}) == cache_key({"a": 2, "b": 1})


def test_different_code_sha_gives_different_key() -> None:
    """The extractor sha pins the key: same request, different code, different key."""
    base = {"provider": "meta", "model": "m", "system_sha": "s", "text_sha": "t"}
    assert cache_key({**base, "code_sha": "aaa"}) != cache_key({**base, "code_sha": "bbb"})
    assert cache_key({**base, "code_sha": "aaa"}) == cache_key({**base, "code_sha": "aaa"})
    assert cache_key({**base, "code_sha": "aaa"}) != cache_key(base)


async def test_miss_then_hit_and_compute_not_called_on_hit(tmp_path: Path) -> None:
    calls = 0

    async def compute() -> dict:
        nonlocal calls
        calls += 1
        return {"answer": 42}

    value, hit = await get_or_compute({"q": 1}, compute, data_dir=tmp_path)
    assert (value, hit) == ({"answer": 42}, False)
    value, hit = await get_or_compute({"q": 1}, compute, data_dir=tmp_path)
    assert (value, hit) == ({"answer": 42}, True)
    assert calls == 1


async def test_cache_file_is_valid_json_with_key_parts(tmp_path: Path) -> None:
    parts = {"provider": "meta", "model": "m"}

    async def compute() -> dict:
        return {"v": 1}

    await get_or_compute(parts, compute, data_dir=tmp_path)
    key = cache_key(parts)
    stored = json.loads((tmp_path / "cache" / key[:2] / f"{key}.json").read_text(encoding="utf-8"))
    assert stored["key_parts"] == parts
    assert stored["value"] == {"v": 1}
    assert "created_at" in stored


async def test_fresh_env_skips_cache_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def compute() -> dict:
        nonlocal calls
        calls += 1
        return {"answer": calls}

    await get_or_compute({"q": "fresh"}, compute, data_dir=tmp_path)
    monkeypatch.setenv("STACKCHECK_FRESH", "1")
    value, hit = await get_or_compute({"q": "fresh"}, compute, data_dir=tmp_path)
    assert hit is False
    assert calls == 2
    assert value == {"answer": 2}


async def test_leftover_tmp_file_does_not_break_reads(tmp_path: Path) -> None:
    parts = {"q": "tmp"}

    async def compute() -> dict:
        return {"v": 2}

    await get_or_compute(parts, compute, data_dir=tmp_path)
    key = cache_key(parts)
    cache_file = tmp_path / "cache" / key[:2] / f"{key}.json"
    cache_file.with_name(cache_file.name + ".tmp").write_text("garbage", encoding="utf-8")

    async def fail() -> dict:
        pytest.fail("compute must not run when the cache file exists")

    value, hit = await get_or_compute(parts, fail, data_dir=tmp_path)
    assert (value, hit) == ({"v": 2}, True)
