import pytest

from stackcheck.extract.prompts import load_prompt

INVESTIGATE_KEYS = {
    "finding_title": "R1 undecided",
    "reason": "counts differ",
    "image_id": "img1",
    "quarter_end": "2025-03-31",
    "sam_rows": "5",
    "vision_estimate": "6",
    "top_obscured": "false",
    "other_photos": "img2, img3",
    "tried_prompts": "balcony",
    "allowed_prompts": "balcony, window",
    "history": "none",
}


def test_all_four_prompt_files_load() -> None:
    assert load_prompt("filing_v5").version == "filing_v5"
    assert load_prompt("photo_v1").version == "photo_v1"
    assert load_prompt("investigate_v1").version == "investigate_v1"
    assert load_prompt("summary_v1").version == "summary_v1"


def test_templates_contain_their_placeholders() -> None:
    assert "{pages}" in load_prompt("filing_v5").user_template
    photo = load_prompt("photo_v1").user_template
    assert "{image_id}" in photo and "{quarter_end}" in photo and "{block_labels}" in photo
    summary = load_prompt("summary_v1").user_template
    assert "{language}" in summary and "{headline}" in summary and "{findings}" in summary


def test_investigate_fill_keeps_literal_braces() -> None:
    filled = load_prompt("investigate_v1").fill(**INVESTIGATE_KEYS)
    assert "R1 undecided" in filled
    assert '{"action":' in filled
    assert "{" + "finding_title" + "}" not in filled


def test_fill_with_unknown_key_raises() -> None:
    with pytest.raises(ValueError, match="nope"):
        load_prompt("filing_v5").fill(pages="x", nope="y")


def test_fill_with_unfilled_placeholder_raises() -> None:
    with pytest.raises(ValueError, match="pages"):
        load_prompt("filing_v5").fill()


def test_filing_v5_has_row_alignment_rule() -> None:
    """v5 pins the row fix: a value belongs to its own numbered row, never the next."""
    template = load_prompt("filing_v5").user_template
    assert "its own row" in template
    assert "never to this one" in template or "never to the" in template


def test_reread_v1_pin() -> None:
    """The R4 second read asks for every numbered row with its percentage as JSON."""
    prompt = load_prompt("reread_v1")
    assert prompt.version == "reread_v1"
    assert "{doc_id}" in prompt.user_template and "{page}" in prompt.user_template
    assert "its own row" in prompt.user_template
    filled = prompt.fill(doc_id="d" * 16, page=7)
    assert "{" + "doc_id}" not in filled
    assert '"rows"' in filled


def test_filing_v5_money_rule_demands_full_grouped_number() -> None:
    """v5 pins the fragment fix: copy the full grouped number, never a fragment."""
    template = load_prompt("filing_v5").user_template
    assert "in full, not" in template or "never a fragment" in template
    assert "28,10,56,195" in template
    assert "56195" in template
    assert "11,42,92,006" in template
    assert "4292" in template
