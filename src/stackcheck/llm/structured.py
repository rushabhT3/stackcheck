"""`ask_json`: one schema-validated answer, with a single repair attempt."""

import json
import logging
from collections.abc import Sequence

from pydantic import BaseModel

from stackcheck.errors import SchemaError
from stackcheck.llm.base import ImagePart, ModelClient

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in model output")
    return stripped[start : end + 1]


async def ask_json[T: BaseModel](
    client: ModelClient,
    *,
    prompt_name: str,
    system: str,
    text: str,
    schema: type[T],
    images: Sequence[ImagePart] = (),
    purpose: str,
    check_id: str | None,
) -> tuple[T, list]:
    suffix = "\n\nReturn only one JSON object that matches this JSON Schema:\n" + json.dumps(
        schema.model_json_schema()
    )
    first_text = text + suffix
    first = await client.complete(
        system=system, text=first_text, images=images, purpose=purpose, check_id=check_id
    )
    try:
        return schema.model_validate(json.loads(_extract_json(first.text))), [first]
    except Exception as exc:
        error = str(exc)[:1500]
    logger.info("ask_json repair for %s: %s", prompt_name, error[:200])
    repair_text = (
        first_text
        + "\n\nYour previous answer was invalid: "
        + error
        + "\nReturn only the corrected JSON object."
    )
    second = await client.complete(
        system=system, text=repair_text, images=images, purpose=purpose, check_id=check_id
    )
    try:
        return schema.model_validate(json.loads(_extract_json(second.text))), [first, second]
    except Exception as exc:
        raise SchemaError(f"{prompt_name}: could not parse model output: {second.text[:300]}") from exc
