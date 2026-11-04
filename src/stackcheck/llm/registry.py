from typing import Literal

from stackcheck.config import Settings, model_for
from stackcheck.llm.base import ModelClient
from stackcheck.llm.meta import MetaClient
from stackcheck.llm.openai_compat import OpenAICompatClient


def reader_client(settings: Settings, origin: Literal["url", "upload"]) -> ModelClient:
    """Pick the reading client. Uploads always go to Meta's private model (D07)."""
    if origin == "upload" or settings.extract_provider == "meta":
        return MetaClient(settings, model_for(origin, settings), data_dir=settings.data_dir)
    if settings.extract_provider == "baseten":
        return OpenAICompatClient(
            provider="baseten",
            base_url=settings.baseten_base_url,
            api_key=settings.baseten_api_key,
            model=settings.baseten_model,
            data_dir=settings.data_dir,
        )
    return OpenAICompatClient(
        provider="fireworks",
        base_url=settings.fireworks_base_url,
        api_key=settings.fireworks_api_key,
        model=settings.fireworks_model,
        data_dir=settings.data_dir,
    )


def vision_client(settings: Settings, origin: Literal["url", "upload"]) -> ModelClient:
    """Vision always goes to Meta (D07)."""
    return MetaClient(settings, model_for(origin, settings), data_dir=settings.data_dir)
