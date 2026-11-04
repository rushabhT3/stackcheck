import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from stackcheck.config import Settings
from stackcheck.vision.sam import SamClient
from tests.fakes import FakeSamSDK


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    kwargs: dict[str, object] = {
        "data_dir": tmp_path,
        "meta_api_key": "test",
        "baseten_api_key": "test",
        "fireworks_api_key": "test",
    }
    kwargs.update(overrides)
    return Settings(**kwargs)  # type: ignore[arg-type]


def _png(path: Path) -> Path:
    Image.new("RGB", (16, 16), (255, 255, 255)).save(path)
    return path


async def test_request_shape_matches_contract(tmp_path: Path) -> None:
    image = _png(tmp_path / "a.png")
    sdk = FakeSamSDK(chunks=("<0f>",))
    client = SamClient(_settings(tmp_path), data_dir=tmp_path, sdk=sdk)
    assert await client.segment_image(image, "balcony", check_id=None) == "<0f>"
    (kwargs,) = sdk.calls
    assert kwargs["model"] == "sam-3.1"
    assert kwargs["stream"] is True
    assert kwargs["metadata"] == {"mask_encoding": "one_bit"}
    (message,) = kwargs["input"]
    assert message["role"] == "user"
    text_part, image_part = message["content"]
    assert text_part == {"type": "input_text", "text": "balcony"}
    assert image_part["type"] == "input_image"
    assert image_part["image_url"].startswith("data:image/png;base64,")


async def test_deltas_joined_in_order_non_deltas_ignored(tmp_path: Path) -> None:
    image = _png(tmp_path / "a.png")
    other = SimpleNamespace(type="response.completed", delta="IGNORED")
    sdk = FakeSamSDK(chunks=("<0", other, "f>", "<0f>"))
    client = SamClient(_settings(tmp_path), data_dir=tmp_path, sdk=sdk)
    assert await client.segment_image(image, "window", check_id=None) == "<0f><0f>"


async def test_second_call_is_cached_and_free(tmp_path: Path) -> None:
    image = _png(tmp_path / "a.png")
    sdk = FakeSamSDK(chunks=("<0f>",))
    client = SamClient(_settings(tmp_path), data_dir=tmp_path, sdk=sdk)
    first = await client.segment_image(image, "balcony", check_id="chk_1")
    second = await client.segment_image(image, "balcony", check_id="chk_1")
    assert first == second == "<0f>"
    assert len(sdk.calls) == 1
    last = json.loads((tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert last["cache_hit"] is True and last["usd"] == "0.000000"


async def test_cache_miss_logs_key_components(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A SAM miss logs the stored key plus component shas so two runs diff."""
    image = _png(tmp_path / "a.png")
    sdk = FakeSamSDK(chunks=("<0f>",))
    client = SamClient(_settings(tmp_path), data_dir=tmp_path, sdk=sdk)
    with caplog.at_level("INFO", logger="stackcheck.vision.sam"):
        await client.segment_image(image, "balcony", check_id=None)
    stored = [p.stem for p in (tmp_path / "cache").rglob("*.json")]
    assert len(stored) == 1
    assert "cache miss" in caplog.text
    assert f"key={stored[0]}" in caplog.text
    assert "image_sha=" in caplog.text


async def test_file_id_mode_uploads_once_for_two_prompts(tmp_path: Path) -> None:
    image = _png(tmp_path / "a.png")
    sdk = FakeSamSDK(chunks=("<0f>",))
    client = SamClient(_settings(tmp_path, sam_image_mode="file_id"), data_dir=tmp_path, sdk=sdk)
    await client.segment_image(image, "balcony", check_id=None)
    await client.segment_image(image, "window", check_id=None)
    assert len(sdk.uploads) == 1
    assert len(sdk.calls) == 2
    for kwargs in sdk.calls:
        (_, image_part) = kwargs["input"][0]["content"]
        assert image_part == {"type": "input_image", "file_id": "file-xyz"}


async def test_question_prompt_raises(tmp_path: Path) -> None:
    image = _png(tmp_path / "a.png")
    client = SamClient(_settings(tmp_path), data_dir=tmp_path, sdk=FakeSamSDK())
    with pytest.raises(ValueError, match="noun phrase"):
        await client.segment_image(image, "Segment the windows?", check_id=None)
