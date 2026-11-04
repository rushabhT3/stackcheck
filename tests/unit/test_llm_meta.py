from pathlib import Path

import pytest

from stackcheck.config import Settings
from stackcheck.errors import ModelError
from stackcheck.llm.base import ImagePart
from stackcheck.llm.meta import MetaClient
from tests.fakes import FakeResponsesSDK


async def test_empty_reply_raises_model_error(tmp_path: Path) -> None:
    """Three None output_text replies in a row are a model failure, not a crash."""
    sdk = FakeResponsesSDK(texts=[None, None, None])  # type: ignore[list-item]
    client = MetaClient(_settings(tmp_path), "muse-spark-1.3-contributor", data_dir=tmp_path, sdk=sdk)
    with pytest.raises(ModelError, match="empty reply"):
        await client.complete(system="s", text="t", purpose="p", check_id=None)
    assert len(sdk.calls) == 3


async def test_empty_reply_retried_then_succeeds(tmp_path: Path) -> None:
    """A transient empty output_text is retried; the good reply is returned."""
    sdk = FakeResponsesSDK(texts=[None, "ok"])  # type: ignore[list-item]
    client = MetaClient(_settings(tmp_path), "muse-spark-1.3-contributor", data_dir=tmp_path, sdk=sdk)
    reply = await client.complete(system="s", text="t", purpose="p", check_id=None)
    assert reply.text == "ok"
    assert len(sdk.calls) == 2


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    kwargs: dict[str, object] = {
        "data_dir": tmp_path,
        "meta_api_key": "test",
        "baseten_api_key": "test",
        "fireworks_api_key": "test",
    }
    kwargs.update(overrides)
    return Settings(**kwargs)  # type: ignore[arg-type]


async def test_request_shape_matches_contract(tmp_path: Path) -> None:
    sdk = FakeResponsesSDK()
    client = MetaClient(_settings(tmp_path), "muse-spark-1.3-contributor", data_dir=tmp_path, sdk=sdk)
    image = ImagePart(data=b"fakepng", mime="image/png")
    reply = await client.complete(system="sys", text="hi", images=[image], purpose="t", check_id=None)
    assert reply.text == "ok"
    assert len(sdk.calls) == 1
    kwargs = sdk.calls[0]
    assert kwargs["model"] == "muse-spark-1.3-contributor"
    assert kwargs["instructions"] == "sys"
    (message,) = kwargs["input"]
    assert message["role"] == "user"
    text_part, image_part = message["content"]
    assert text_part == {"type": "input_text", "text": "hi"}
    assert image_part["type"] == "input_image"
    assert image_part["image_url"].startswith("data:image/png;base64,")
    assert kwargs["reasoning"] == {"effort": "low"}


async def test_cache_miss_logs_key_components(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A miss logs the stored key plus each component sha so two runs diff."""
    sdk = FakeResponsesSDK()
    client = MetaClient(_settings(tmp_path), "muse-spark-1.3-contributor", data_dir=tmp_path, sdk=sdk)
    with caplog.at_level("INFO", logger="stackcheck.llm.meta"):
        await client.complete(system="sys", text="hi", purpose="t", check_id=None)
    stored = [p.stem for p in (tmp_path / "cache").rglob("*.json")]
    assert len(stored) == 1
    assert "cache miss" in caplog.text
    assert f"key={stored[0]}" in caplog.text
    assert "text_sha=" in caplog.text
    assert "system_sha=" in caplog.text


async def test_reader_calls_run_at_temperature_zero(tmp_path: Path) -> None:
    """Deterministic reads: temperature 0 goes to the API and into the cache key."""
    sdk = FakeResponsesSDK()
    client = MetaClient(_settings(tmp_path), "muse-spark-1.3-contributor", data_dir=tmp_path, sdk=sdk)
    await client.complete(system="sys", text="hi", purpose="t", check_id=None)
    assert sdk.calls[0]["temperature"] == 0.0


async def test_reasoning_omitted_when_effort_empty(tmp_path: Path) -> None:
    sdk = FakeResponsesSDK()
    settings = _settings(tmp_path, reasoning_effort="")
    client = MetaClient(settings, settings.reader_model, data_dir=tmp_path, sdk=sdk)
    await client.complete(system="sys", text="hi", purpose="t", check_id=None)
    assert "reasoning" not in sdk.calls[0]


async def test_second_identical_call_is_cache_hit(tmp_path: Path) -> None:
    sdk = FakeResponsesSDK()
    settings = _settings(tmp_path)
    client = MetaClient(settings, settings.reader_model, data_dir=tmp_path, sdk=sdk)
    first = await client.complete(system="s", text="t", purpose="p", check_id=None)
    second = await client.complete(system="s", text="t", purpose="p", check_id=None)
    assert (first.cache_hit, second.cache_hit) == (False, True)
    assert len(sdk.calls) == 1


async def test_missing_token_details_gives_zero_cached(tmp_path: Path) -> None:
    sdk = FakeResponsesSDK(include_token_details=False)
    settings = _settings(tmp_path)
    client = MetaClient(settings, settings.reader_model, data_dir=tmp_path, sdk=sdk)
    reply = await client.complete(system="s", text="t", purpose="p", check_id=None)
    assert reply.cached_input_tokens == 0


async def test_different_code_sha_misses_cache(tmp_path: Path) -> None:
    """The extractor sha pins the key: same text, different sha, second call runs."""
    import json

    sdk = FakeResponsesSDK()
    settings = _settings(tmp_path)
    client = MetaClient(settings, settings.reader_model, data_dir=tmp_path, sdk=sdk)
    first = await client.complete(system="s", text="t", purpose="p", check_id=None, code_sha="aaa")
    second = await client.complete(system="s", text="t", purpose="p", check_id=None, code_sha="aaa")
    assert (first.cache_hit, second.cache_hit) == (False, True)
    assert len(sdk.calls) == 1
    third = await client.complete(system="s", text="t", purpose="p", check_id=None, code_sha="bbb")
    assert third.cache_hit is False
    assert len(sdk.calls) == 2
    stored = [
        json.loads(p.read_text(encoding="utf-8"))["key_parts"] for p in (tmp_path / "cache").rglob("*.json")
    ]
    assert {parts.get("code_sha") for parts in stored} == {"aaa", "bbb"}


async def test_sdk_exception_becomes_model_error(tmp_path: Path) -> None:
    sdk = FakeResponsesSDK(error=RuntimeError("boom"))
    settings = _settings(tmp_path)
    client = MetaClient(settings, settings.reader_model, data_dir=tmp_path, sdk=sdk)
    with pytest.raises(ModelError, match=r"meta.*contributor.*boom"):
        await client.complete(system="s", text="t", purpose="p", check_id=None)
