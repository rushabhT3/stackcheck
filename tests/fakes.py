"""Fakes at the SDK boundary: no HTTP, no keys, no money."""

from types import SimpleNamespace
from typing import Any

from stackcheck.llm.base import ModelReply


class FakeResponsesSDK:
    """Mimics the slice of `openai.AsyncOpenAI` that `MetaClient` uses."""

    def __init__(
        self,
        texts: str | list[str] = "ok",
        *,
        input_tokens: int = 11,
        output_tokens: int = 7,
        cached_input_tokens: int = 0,
        include_token_details: bool = True,
        error: Exception | None = None,
    ) -> None:
        self._texts = [texts] if isinstance(texts, str) else list(texts)
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._cached_input_tokens = cached_input_tokens
        self._include_token_details = include_token_details
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self.responses = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        index = min(len(self.calls) - 1, len(self._texts) - 1)
        if self._include_token_details:
            usage = SimpleNamespace(
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
                input_tokens_details=SimpleNamespace(cached_tokens=self._cached_input_tokens),
            )
        else:
            usage = SimpleNamespace(input_tokens=self._input_tokens, output_tokens=self._output_tokens)
        return SimpleNamespace(output_text=self._texts[index], usage=usage)


class FakeSamSDK:
    """Mimics the slice of `openai.AsyncOpenAI` that `SamClient` uses."""

    def __init__(self, chunks: tuple = ("<0f>",), *, file_id: str = "file-xyz") -> None:
        self._chunks = chunks
        self._file_id = file_id
        self.calls: list[dict[str, Any]] = []
        self.uploads: list[dict[str, Any]] = []
        self.responses = SimpleNamespace(create=self._create)
        self.files = SimpleNamespace(create=self._upload)

    async def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)

        async def _gen() -> Any:
            for chunk in self._chunks:
                if isinstance(chunk, str):
                    yield SimpleNamespace(type="response.output_text.delta", delta=chunk)
                else:
                    yield chunk

        return _gen()

    async def _upload(self, **kwargs: Any) -> SimpleNamespace:
        self.uploads.append(kwargs)
        return SimpleNamespace(id=self._file_id)


class FakeModelClient:
    """Fake `ModelClient` returning prepared reply texts.

    `routes` maps a substring of the request text to its reply, so parallel
    calls get the right answer regardless of scheduling order.
    """

    provider = "meta"
    model = "muse-spark-1.3-contributor"

    def __init__(self, texts: str | list[str] = "{}", *, routes: dict[str, str] | None = None) -> None:
        self._texts = [texts] if isinstance(texts, str) else list(texts)
        self._routes = routes or {}
        self.calls: list[dict[str, Any]] = []

    def _reply(self, text: str) -> ModelReply:
        return ModelReply(
            text=text,
            provider=self.provider,
            model=self.model,
            input_tokens=10,
            cached_input_tokens=0,
            output_tokens=5,
            cache_hit=False,
        )

    async def complete(self, **kwargs: Any) -> ModelReply:
        self.calls.append(kwargs)
        request_text = kwargs.get("text", "")
        if isinstance(request_text, str):
            for key, reply in self._routes.items():
                if key in request_text:
                    return self._reply(reply)
        index = min(len(self.calls) - 1, len(self._texts) - 1)
        return self._reply(self._texts[index])


class FakeChatSDK:
    """Mimics the slice of `openai.AsyncOpenAI` that `OpenAICompatClient` uses."""

    def __init__(
        self,
        texts: str | list[str] = "ok",
        *,
        prompt_tokens: int = 11,
        completion_tokens: int = 7,
        error: Exception | None = None,
    ) -> None:
        self._texts = [texts] if isinstance(texts, str) else list(texts)
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        index = min(len(self.calls) - 1, len(self._texts) - 1)
        message = SimpleNamespace(content=self._texts[index])
        usage = SimpleNamespace(prompt_tokens=self._prompt_tokens, completion_tokens=self._completion_tokens)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)
