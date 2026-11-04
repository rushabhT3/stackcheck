from pathlib import Path

import pytest

from stackcheck.errors import ModelError
from stackcheck.llm.openai_compat import OpenAICompatClient
from tests.fakes import FakeChatSDK


async def test_empty_reply_raises_model_error(tmp_path: Path) -> None:
    """Three None content replies in a row are a model failure, not a validation crash."""
    sdk = FakeChatSDK(texts=[None, None, None])  # type: ignore[list-item]
    client = OpenAICompatClient(
        provider="baseten",
        base_url="https://x",
        api_key="k",
        model="meta-models/muse-spark-open-1026",
        data_dir=tmp_path,
        sdk=sdk,
    )
    with pytest.raises(ModelError, match="empty reply"):
        await client.complete(system="sys", text="hi", purpose="t", check_id=None)
    assert len(sdk.calls) == 3


async def test_empty_reply_retried_then_succeeds(tmp_path: Path) -> None:
    """A transient empty reply is retried; the good reply is returned uncached."""
    sdk = FakeChatSDK(texts=[None, "ok"])  # type: ignore[list-item]
    client = OpenAICompatClient(
        provider="baseten",
        base_url="https://x",
        api_key="k",
        model="meta-models/muse-spark-open-1026",
        data_dir=tmp_path,
        sdk=sdk,
    )
    reply = await client.complete(system="sys", text="hi", purpose="t", check_id=None)
    assert reply.text == "ok"
    assert reply.cache_hit is False
    assert len(sdk.calls) == 2


async def test_message_shape_matches_contract(tmp_path: Path) -> None:
    sdk = FakeChatSDK()
    client = OpenAICompatClient(
        provider="baseten",
        base_url="https://x",
        api_key="k",
        model="meta-models/muse-spark-open-1026",
        data_dir=tmp_path,
        sdk=sdk,
    )
    reply = await client.complete(system="sys", text="hi", purpose="t", check_id=None)
    assert reply.text == "ok"
    assert reply.provider == "baseten"
    assert (reply.input_tokens, reply.output_tokens) == (11, 7)
    (kwargs,) = sdk.calls
    assert kwargs["model"] == "meta-models/muse-spark-open-1026"
    system_msg, user_msg = kwargs["messages"]
    assert system_msg == {"role": "system", "content": "sys"}
    assert user_msg["role"] == "user"
    (text_part,) = user_msg["content"]
    assert text_part == {"type": "text", "text": "hi"}


async def test_reader_calls_run_at_temperature_zero(tmp_path: Path) -> None:
    """Deterministic reads: temperature 0 goes to the API and into the cache key."""
    sdk = FakeChatSDK()
    client = OpenAICompatClient(
        provider="baseten",
        base_url="https://x",
        api_key="k",
        model="meta-models/muse-spark-open-1026",
        data_dir=tmp_path,
        sdk=sdk,
    )
    await client.complete(system="sys", text="hi", purpose="t", check_id=None)
    assert sdk.calls[0]["temperature"] == 0.0


async def test_cache_miss_logs_key_components(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A miss logs the stored key plus each component sha so two runs diff."""
    sdk = FakeChatSDK()
    client = OpenAICompatClient(
        provider="baseten",
        base_url="https://x",
        api_key="k",
        model="meta-models/muse-spark-open-1026",
        data_dir=tmp_path,
        sdk=sdk,
    )
    with caplog.at_level("INFO", logger="stackcheck.llm.openai_compat"):
        await client.complete(system="sys", text="hi", purpose="t", check_id=None)
    stored = [p.stem for p in (tmp_path / "cache").rglob("*.json")]
    assert len(stored) == 1
    assert "cache miss" in caplog.text
    assert f"key={stored[0]}" in caplog.text
    assert "text_sha=" in caplog.text
    assert "system_sha=" in caplog.text


async def test_different_code_sha_misses_cache(tmp_path: Path) -> None:
    """The extractor sha pins the key: same text, different sha, second call runs."""
    sdk = FakeChatSDK()
    client = OpenAICompatClient(
        provider="baseten",
        base_url="https://x",
        api_key="k",
        model="meta-models/muse-spark-open-1026",
        data_dir=tmp_path,
        sdk=sdk,
    )
    first = await client.complete(system="s", text="t", purpose="p", check_id=None, code_sha="aaa")
    second = await client.complete(system="s", text="t", purpose="p", check_id=None, code_sha="aaa")
    assert (first.cache_hit, second.cache_hit) == (False, True)
    assert len(sdk.calls) == 1
    third = await client.complete(system="s", text="t", purpose="p", check_id=None, code_sha="bbb")
    assert third.cache_hit is False
    assert len(sdk.calls) == 2


async def test_usage_mapped_and_cache_hit(tmp_path: Path) -> None:
    sdk = FakeChatSDK(prompt_tokens=20, completion_tokens=3)
    client = OpenAICompatClient(
        provider="fireworks",
        base_url="https://x",
        api_key="k",
        model="accounts/fireworks/models/muse-spark-open",
        data_dir=tmp_path,
        sdk=sdk,
    )
    first = await client.complete(system="s", text="t", purpose="p", check_id=None)
    second = await client.complete(system="s", text="t", purpose="p", check_id=None)
    assert (first.input_tokens, first.output_tokens) == (20, 3)
    assert (first.cache_hit, second.cache_hit) == (False, True)
    assert len(sdk.calls) == 1
