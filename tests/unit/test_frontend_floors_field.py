"""The floors field is a number-or-null binding: no string methods on its value."""

from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "web" / "src"


def _start() -> str:
    return (SRC / "views" / "StartView.svelte").read_text(encoding="utf-8")


def test_floors_value_never_trimmed() -> None:
    assert "floorsAboveGround.trim()" not in _start()


def test_floors_validated_as_number_or_null() -> None:
    src = _start()
    assert "Number.isInteger" in src
    assert "Floors above ground must be a whole number of 1 or more." in src
    assert "createCheck(projectLabel.trim(), sources, video, floors)" in src
