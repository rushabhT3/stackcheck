from stackcheck.config import Settings, model_for


def test_env_aliases(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MODEL_API_KEY", "k1")
    monkeypatch.setenv("STACKCHECK_READER_MODEL", "x")
    settings = Settings()
    assert settings.meta_api_key == "k1"
    assert settings.reader_model == "x"


def test_model_for_privacy_rule() -> None:
    settings = Settings(
        data_dir="data",
        meta_api_key="test",
        baseten_api_key="test",
        fireworks_api_key="test",
    )
    assert model_for("url", settings) == settings.reader_model
    assert model_for("upload", settings) == settings.private_model
