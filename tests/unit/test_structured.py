from pathlib import Path

import pytest
from pydantic import BaseModel

from stackcheck.config import Settings
from stackcheck.errors import SchemaError
from stackcheck.llm.meta import MetaClient
from stackcheck.llm.structured import ask_json
from tests.fakes import FakeResponsesSDK


class Item(BaseModel):
    name: str
    count: int


def _client(tmp_path: Path, sdk: FakeResponsesSDK) -> MetaClient:
    settings = Settings(
        data_dir=tmp_path, meta_api_key="test", baseten_api_key="test", fireworks_api_key="test"
    )
    return MetaClient(settings, settings.reader_model, data_dir=tmp_path, sdk=sdk)


async def _ask(tmp_path: Path, sdk: FakeResponsesSDK) -> tuple[Item, list]:
    return await ask_json(
        _client(tmp_path, sdk), prompt_name="p", system="s", text="t", schema=Item, purpose="p", check_id=None
    )


async def test_plain_json_parses(tmp_path: Path) -> None:
    item, replies = await _ask(tmp_path, FakeResponsesSDK('{"name": "a", "count": 1}'))
    assert item == Item(name="a", count=1)
    assert len(replies) == 1


async def test_fenced_json_parses(tmp_path: Path) -> None:
    item, _ = await _ask(tmp_path, FakeResponsesSDK('```json\n{"name": "a", "count": 1}\n```'))
    assert item == Item(name="a", count=1)


async def test_json_with_leading_prose_parses(tmp_path: Path) -> None:
    item, _ = await _ask(tmp_path, FakeResponsesSDK('Here it is: {"name": "a", "count": 1} done'))
    assert item == Item(name="a", count=1)


async def test_invalid_then_valid_returns_model_and_two_replies(tmp_path: Path) -> None:
    sdk = FakeResponsesSDK(["oops", '{"name": "a", "count": 2}'])
    item, replies = await _ask(tmp_path, sdk)
    assert item == Item(name="a", count=2)
    assert len(replies) == 2


async def test_two_invalid_answers_raise_schema_error(tmp_path: Path) -> None:
    with pytest.raises(SchemaError, match="p"):
        await _ask(tmp_path, FakeResponsesSDK(["oops", "still oops"]))


async def test_repair_text_contains_validation_error(tmp_path: Path) -> None:
    sdk = FakeResponsesSDK(['{"name": "a"}', '{"name": "a", "count": 1}'])
    await _ask(tmp_path, sdk)
    repair_text = sdk.calls[1]["input"][0]["content"][0]["text"]
    assert "Your previous answer was invalid" in repair_text
    assert "count" in repair_text
