from pathlib import Path

import pytest

from stackcheck.config import Settings
from stackcheck.errors import ConfigError
from stackcheck.llm.meta import MetaClient
from stackcheck.llm.openai_compat import OpenAICompatClient
from stackcheck.llm.registry import reader_client, vision_client


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    kwargs: dict[str, object] = {
        "data_dir": tmp_path,
        "meta_api_key": "test",
        "baseten_api_key": "test",
        "fireworks_api_key": "test",
    }
    kwargs.update(overrides)
    return Settings(**kwargs)  # type: ignore[arg-type]


def test_baseten_url_origin_gives_openai_compat(tmp_path: Path) -> None:
    settings = _settings(tmp_path, extract_provider="baseten")
    client = reader_client(settings, "url")
    assert isinstance(client, OpenAICompatClient)
    assert client.provider == "baseten"
    assert client.model == settings.baseten_model


def test_baseten_upload_origin_gives_meta_private(tmp_path: Path) -> None:
    settings = _settings(tmp_path, extract_provider="baseten")
    client = reader_client(settings, "upload")
    assert isinstance(client, MetaClient)
    assert client.model == settings.private_model


def test_vision_client_is_always_meta(tmp_path: Path) -> None:
    settings = _settings(tmp_path, extract_provider="fireworks")
    for origin in ("url", "upload"):
        client = vision_client(settings, origin)  # type: ignore[arg-type]
        assert isinstance(client, MetaClient)


def test_missing_key_raises_naming_env_var(tmp_path: Path) -> None:
    settings = _settings(tmp_path, extract_provider="baseten", baseten_api_key="")
    with pytest.raises(ConfigError, match="BASETEN_API_KEY"):
        reader_client(settings, "url")


def test_missing_meta_key_raises(tmp_path: Path) -> None:
    settings = _settings(tmp_path, meta_api_key="", extract_provider="meta")
    with pytest.raises(ConfigError, match="MODEL_API_KEY"):
        reader_client(settings, "url")
