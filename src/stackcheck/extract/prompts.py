"""Prompt loader. Templates use plain `{key}` replacement, never `str.format`."""

from importlib.resources import files

from pydantic import BaseModel

PLACEHOLDERS: dict[str, tuple[str, ...]] = {
    "filing_v4": ("pages",),
    "filing_v5": ("pages",),
    "photo_v1": ("image_id", "quarter_end", "block_labels"),
    "investigate_v1": (
        "finding_title",
        "reason",
        "image_id",
        "quarter_end",
        "sam_rows",
        "vision_estimate",
        "top_obscured",
        "other_photos",
        "tried_prompts",
        "allowed_prompts",
        "history",
    ),
    "summary_v1": ("language", "headline", "findings"),
    "reread_v1": ("doc_id", "page"),
}


class Prompt(BaseModel):
    name: str
    version: str
    system: str
    user_template: str
    placeholders: tuple[str, ...] = ()

    def fill(self, **values: object) -> str:
        out = self.user_template
        for key, value in values.items():
            if key not in self.placeholders:
                raise ValueError(f"Unknown placeholder for {self.name}: {key}")
            out = out.replace("{" + key + "}", str(value))
        missing = [key for key in self.placeholders if "{" + key + "}" in out]
        if missing:
            raise ValueError(f"Unfilled placeholders for {self.name}: {', '.join(missing)}")
        return out


def load_prompt(name: str) -> Prompt:
    try:
        raw = files("stackcheck.extract").joinpath(f"prompts/{name}.md").read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"Unknown prompt: {name}") from None
    lines = raw.splitlines()
    if not lines or not lines[0].startswith("version:"):
        raise ValueError(f"Prompt {name} is missing its version first line")
    version = lines[0].split(":", 1)[1].strip()
    try:
        system_at = lines.index("SYSTEM:")
        user_at = lines.index("USER:")
    except ValueError:
        raise ValueError(f"Prompt {name} needs SYSTEM: and USER: lines") from None
    if user_at < system_at:
        raise ValueError(f"Prompt {name} needs SYSTEM: before USER:")
    system = "\n".join(lines[system_at + 1 : user_at]).strip()
    user_template = "\n".join(lines[user_at + 1 :]).strip()
    return Prompt(
        name=name,
        version=version,
        system=system,
        user_template=user_template,
        placeholders=PLACEHOLDERS.get(name, ()),
    )
