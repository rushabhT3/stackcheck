import base64
from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import BaseModel


class ImagePart(BaseModel):
    data: bytes
    mime: Literal["image/png", "image/jpeg"]


class ModelReply(BaseModel):
    text: str
    provider: str
    model: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cache_hit: bool


class ModelClient(Protocol):
    provider: str
    model: str

    async def complete(
        self,
        *,
        system: str,
        text: str,
        images: Sequence[ImagePart] = (),
        purpose: str,
        check_id: str | None,
        code_sha: str | None = None,
    ) -> ModelReply: ...


def to_data_url(img: ImagePart) -> str:
    return f"data:{img.mime};base64," + base64.b64encode(img.data).decode("ascii")
