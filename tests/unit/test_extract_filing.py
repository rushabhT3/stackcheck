import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from eval_extract import score

from stackcheck.errors import IngestError, ModelError
from stackcheck.extract.filing import _value_on_page, extract_filing
from stackcheck.ingest.pdf import ingest_pdf
from stackcheck.models import ActivityProgress, BlockProgress, Financials, QuarterFiling
from tests.conftest import make_pdf
from tests.fakes import FakeModelClient
from tests.unit.test_ingest_pdf import _source


def _doc(tmp_path: Path, n_pages: int, scanned: bool) -> tuple:
    if scanned:
        pages = [{"text": "short"} for _ in range(n_pages)]
    else:
        pages = [{"text": "\n".join(f"line {i} of the report body" for i in range(30))}]
        pages = pages * n_pages
    pdf = make_pdf(tmp_path / "q.pdf", pages)
    source = _source()
    source.page_count = n_pages
    ingested = ingest_pdf(pdf, source, tmp_path)
    assert ingested.is_scanned is scanned
    return ingested, pdf


async def test_valid_json_uses_source_doc_id(tmp_path: Path) -> None:
    ingested, _ = _doc(tmp_path, 1, False)
    filing = await extract_filing(
        ingested, FakeModelClient('{"doc_id": "wrong", "project_reg_no": "R1"}'), check_id=None
    )
    assert isinstance(filing, QuarterFiling)
    assert (filing.doc_id, filing.project_reg_no) == (ingested.source.doc_id, "R1")


async def test_out_of_range_page_ref_dropped(tmp_path: Path) -> None:
    ingested, _ = _doc(tmp_path, 3, False)
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": doc_id,
            "refs": {"a": {"doc_id": doc_id, "page": 99}},
            "blocks": [{"block_label": "MAIN", "ref": {"doc_id": doc_id, "page": 99}}],
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert filing.refs == {}
    assert filing.blocks[0].ref is None


async def test_scanned_branch_sends_rules_with_image_stubs(tmp_path: Path) -> None:
    """Scanned batches carry the extraction rules, not just a pages sentence."""
    ingested, pdf = _doc(tmp_path, 2, True)
    client = FakeModelClient('{"doc_id": "x"}')
    await extract_filing(ingested, client, check_id=None, pdf_path=pdf)
    text = client.calls[0]["text"]
    assert "A unit written on the row beats the column heading" in text
    assert "[[PAGE 1]]" in text and "[[PAGE 2]]" in text
    assert ingested.source.doc_id in text


def test_merge_keeps_unit_overrides_from_money_batch() -> None:
    """Scanned batches: the lakh override must survive the merge with the value."""
    from stackcheck.extract.filing import _merge

    empty = QuarterFiling(doc_id="d", financials=Financials(currency_unit="INR"))
    money = QuarterFiling(
        doc_id="d",
        financials=Financials(
            currency_unit="INR",
            estimated_total_cost=7594,
            unit_overrides={"estimated_total_cost": "INR_LAKH"},
        ),
    )
    for batches in ([empty, money], [money, empty]):
        merged = _merge(batches)
        assert merged.financials.estimated_total_cost == 7594
        assert merged.financials.unit_overrides == {"estimated_total_cost": "INR_LAKH"}


def test_merge_unit_override_first_batch_wins() -> None:
    """Two batches disagreeing on one field's unit: page order decides."""
    from stackcheck.extract.filing import _merge

    first = QuarterFiling(
        doc_id="d",
        financials=Financials(currency_unit="INR", net_balance=1, unit_overrides={"net_balance": "INR"}),
    )
    second = QuarterFiling(
        doc_id="d",
        financials=Financials(currency_unit="INR", net_balance=1, unit_overrides={"net_balance": "INR_LAKH"}),
    )
    assert _merge([first, second]).financials.unit_overrides == {"net_balance": "INR"}


async def test_scanned_23_pages_makes_three_batched_calls(tmp_path: Path) -> None:
    ingested, pdf = _doc(tmp_path, 23, True)
    client = FakeModelClient('{"doc_id": "x"}')
    await extract_filing(ingested, client, check_id=None, pdf_path=pdf)
    assert len(client.calls) == 3
    assert all(1 <= len(call["images"]) <= 8 for call in client.calls)
    assert sum(len(call["images"]) for call in client.calls) == 23


async def test_scanned_16_pages_sends_at_most_8_images_per_call(tmp_path: Path) -> None:
    """Provider cap is 8 images: a 16-page scan reads in two chunks and merges."""
    ingested, pdf = _doc(tmp_path, 16, True)
    client = FakeModelClient('{"doc_id": "x"}')
    await extract_filing(ingested, client, check_id=None, pdf_path=pdf)
    assert len(client.calls) == 2
    assert all(1 <= len(call["images"]) <= 8 for call in client.calls)
    assert sum(len(call["images"]) for call in client.calls) == 16


class _TooManyImagesReader(FakeModelClient):
    """Mimics the provider 400 on an oversized image batch."""

    async def complete(self, **kwargs):  # type: ignore[no-untyped-def]
        raise ModelError(
            "baseten/meta-models/muse-spark-open-1026: Error code: 400 - Too many images: 10 (max 8)"
        )


async def test_reader_too_many_images_error_maps_to_plain_sentence(tmp_path: Path) -> None:
    """A reader that raises on a 16-page input yields a specific note, never raw text."""
    ingested, pdf = _doc(tmp_path, 16, True)
    with pytest.raises(IngestError, match="more pages than the reader can take"):
        await extract_filing(ingested, _TooManyImagesReader(), check_id=None, pdf_path=pdf)


async def test_read_failure_log_names_doc_id(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """The read-failure log carries the doc id so a cold 8-PDF failure names its filing."""
    ingested, pdf = _doc(tmp_path, 2, True)
    with caplog.at_level("INFO"), pytest.raises(IngestError, match="more pages"):
        await extract_filing(ingested, _TooManyImagesReader(), check_id="chk_d", pdf_path=pdf)
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name == "stackcheck.extract.filing")
    assert ingested.source.doc_id in ours
    assert "chk_d" in ours
    assert "ModelError" in ours


def _doc_with_text(tmp_path: Path, lines: list[str]) -> tuple:
    pdf = make_pdf(tmp_path / "q.pdf", [{"text": "\n".join(lines)}])
    source = _source()
    ingested = ingest_pdf(pdf, source, tmp_path)
    assert ingested.is_scanned is False
    return ingested, pdf


async def test_invented_money_value_is_nulled(tmp_path: Path) -> None:
    ingested, _ = _doc_with_text(tmp_path, [f"row {i} of the report body" for i in range(30)])
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": doc_id,
            "financials": {
                "spent_this_quarter": "99999999",
                "ref": {"doc_id": doc_id, "page": 1},
            },
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert filing.financials.spent_this_quarter is None
    assert filing.notes == ["1 value could not be matched on the cited page"]


async def test_same_block_spellings_merge_to_one_block(tmp_path: Path) -> None:
    """'Block A' and 'BLOCK-A' are one block, shown once per quarter."""
    ingested, _ = _doc_with_text(tmp_path, [f"row {i} of the report body" for i in range(30)])
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": doc_id,
            "blocks": [
                {"block_label": "Block A", "ref": {"doc_id": doc_id, "page": 4}},
                {"block_label": "BLOCK-A", "ref": {"doc_id": doc_id, "page": 4}},
            ],
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert len(filing.blocks) == 1


async def test_many_unmatched_values_collapse_to_one_line(tmp_path: Path) -> None:
    """129 unmatched values become one count line, not 129 note lines."""
    ingested, _ = _doc_with_text(tmp_path, [f"row {i} of the report body" for i in range(30)])
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": doc_id,
            "financials": {
                "spent_this_quarter": "99999999",
                "cumulative_spent": "88888888",
                "ref": {"doc_id": doc_id, "page": 1},
            },
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert filing.financials.spent_this_quarter is None
    assert filing.financials.cumulative_spent is None
    assert filing.notes == ["2 values could not be matched on the cited page"]


async def test_single_unmatched_value_uses_singular_note(tmp_path: Path) -> None:
    """One unmatched value collapses to a grammatical singular count line."""
    ingested, _ = _doc_with_text(tmp_path, [f"row {i} of the report body" for i in range(30)])
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": doc_id,
            "financials": {
                "spent_this_quarter": "99999999",
                "ref": {"doc_id": doc_id, "page": 1},
            },
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert filing.financials.spent_this_quarter is None
    assert filing.notes == ["1 value could not be matched on the cited page"]


async def test_block_label_spellings_normalize_to_one_block(tmp_path: Path) -> None:
    """'Block A', 'BLOCK - A (ORION)' and 'BLOCK-A' are one stored block 'A'."""
    ingested, _ = _doc_with_text(tmp_path, [f"row {i} of the report body" for i in range(30)])
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": doc_id,
            "blocks": [
                {"block_label": "Block A", "ref": {"doc_id": doc_id, "page": 4}},
                {"block_label": "BLOCK - A (ORION)", "ref": {"doc_id": doc_id, "page": 4}},
                {"block_label": "BLOCK-A", "ref": {"doc_id": doc_id, "page": 4}},
            ],
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert [b.block_label for b in filing.blocks] == ["A"]


async def test_block_labels_main_and_dash_number_kept(tmp_path: Path) -> None:
    """'MAIN' stays 'MAIN' and 'Block B-2' normalizes to 'B-2', kept distinct."""
    ingested, _ = _doc_with_text(tmp_path, [f"row {i} of the report body" for i in range(30)])
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": doc_id,
            "blocks": [
                {"block_label": "MAIN", "ref": {"doc_id": doc_id, "page": 1}},
                {"block_label": "Block B-2", "ref": {"doc_id": doc_id, "page": 1}},
            ],
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert [b.block_label for b in filing.blocks] == ["MAIN", "B-2"]


async def test_block_dedupe_fills_null_fields_from_later_dupe(tmp_path: Path) -> None:
    """First 'Block A' keeps its slot; a later 'BLOCK-A' fills its null slab count."""
    lines = [f"row {i} of the report body" for i in range(30)] + ["Slabs 7 complete"]
    ingested, _ = _doc_with_text(tmp_path, lines)
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": doc_id,
            "blocks": [
                {"block_label": "Block A", "ref": {"doc_id": doc_id, "page": 1}},
                {
                    "block_label": "BLOCK-A",
                    "slabs_claimed_count": 7,
                    "ref": {"doc_id": doc_id, "page": 1},
                },
            ],
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert len(filing.blocks) == 1
    assert filing.blocks[0].block_label == "A"
    assert filing.blocks[0].slabs_claimed_count == 7


async def test_activity_block_label_backfilled_from_page(tmp_path: Path) -> None:
    """An activity with a null label takes the label of its page's only block."""
    lines = [f"row {i} of the report body" for i in range(30)] + ["Slabs 40% complete"]
    ingested, _ = _doc_with_text(tmp_path, lines)
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": doc_id,
            "blocks": [{"block_label": "MAIN", "ref": {"doc_id": doc_id, "page": 1}}],
            "activities": [
                {
                    "name": "Slabs work",
                    "percent_done": 40.0,
                    "block_label": None,
                    "ref": {"doc_id": doc_id, "page": 1},
                }
            ],
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert filing.activities[0].block_label == "MAIN"


async def test_activity_block_label_ambiguous_page_stays_null(tmp_path: Path) -> None:
    """Two blocks on one page: a null activity label cannot be backfilled."""
    lines = [f"row {i} of the report body" for i in range(30)] + ["Slabs 40% complete"]
    ingested, _ = _doc_with_text(tmp_path, lines)
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": doc_id,
            "blocks": [
                {"block_label": "Block A", "ref": {"doc_id": doc_id, "page": 1}},
                {"block_label": "Block B", "ref": {"doc_id": doc_id, "page": 1}},
            ],
            "activities": [
                {
                    "name": "Slabs work",
                    "percent_done": 40.0,
                    "block_label": None,
                    "ref": {"doc_id": doc_id, "page": 1},
                }
            ],
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert filing.activities[0].block_label is None


async def test_activity_block_label_is_normalized(tmp_path: Path) -> None:
    """A reader label of 'BLOCK-A' is stored as 'A', never repointed at the page block."""
    lines = [f"row {i} of the report body" for i in range(30)] + ["Slabs 40% complete"]
    ingested, _ = _doc_with_text(tmp_path, lines)
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": doc_id,
            "blocks": [{"block_label": "MAIN", "ref": {"doc_id": doc_id, "page": 1}}],
            "activities": [
                {
                    "name": "Slabs work",
                    "percent_done": 40.0,
                    "block_label": "BLOCK-A",
                    "ref": {"doc_id": doc_id, "page": 1},
                }
            ],
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert filing.activities[0].block_label == "A"


def _doc_with_section_ix(tmp_path: Path, lines: list[str]) -> tuple:
    """Eleven pages: filler plus a Section IX money table on page 11."""
    filler = [f"row {i} of the report body" for i in range(30)]
    pages = [{"text": "\n".join(filler)} for _ in range(10)]
    pages.append({"text": "\n".join([*lines, *filler])})
    pdf = make_pdf(tmp_path / "q.pdf", pages)
    source = _source()
    source.page_count = 11
    ingested = ingest_pdf(pdf, source, tmp_path)
    assert ingested.is_scanned is False
    assert len(ingested.pages) == 11
    return ingested, pdf


def _section_ix_lines(amounts: list[str]) -> list[str]:
    received, spent, collected, expenditure, net = amounts
    return [
        "SECTION IX FINANCIAL PROGRESS",
        f"Amount Received during the quarter : Rs. {received}/-",
        f"Actual Cost Incurred during the quarter : Rs. {spent}/-",
        f"Cumulative Fund Collected till the end of quarter : Rs. {collected}/-",
        f"Cumulative Expenditure done till the end of quarter : Rs. {expenditure}/-",
        f"Net Amount at end of the quarter : Rs. {net}/-",
    ]


async def test_money_fallback_sept_2024(tmp_path: Path) -> None:
    """Null money fields are backfilled from Section IX labels on page 11."""
    lines = _section_ix_lines(["9,63,37,925", "5,91,46,093", "11,42,92,006", "29,66,33,535", "28,10,56,195"])
    ingested, _ = _doc_with_section_ix(tmp_path, lines)
    doc_id = ingested.source.doc_id
    text = json.dumps({"doc_id": doc_id, "financials": {}})
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert filing.financials.received_this_quarter == 96337925
    assert filing.financials.spent_this_quarter == 59146093
    assert filing.financials.cumulative_received == 114292006
    assert filing.financials.cumulative_spent == 296633535
    assert filing.financials.net_balance == 281056195
    assert filing.financials.estimated_total_cost is None
    assert filing.financials.ref is not None and filing.financials.ref.page == 11
    assert "value not found on cited page" not in filing.notes
    assert "values could not be matched" not in " ".join(filing.notes)


async def test_money_fallback_dec_2024(tmp_path: Path) -> None:
    """Second synthetic quarter: same labels, December 2024 amounts."""
    lines = _section_ix_lines(
        ["10,92,41,442", "11,81,70,250", "11,30,89,183", "40,58,74,977", "39,92,26,445"]
    )
    ingested, _ = _doc_with_section_ix(tmp_path, lines)
    doc_id = ingested.source.doc_id
    text = json.dumps({"doc_id": doc_id, "financials": {}})
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert filing.financials.received_this_quarter == 109241442
    assert filing.financials.spent_this_quarter == 118170250
    assert filing.financials.cumulative_received == 113089183
    assert filing.financials.cumulative_spent == 405874977
    assert filing.financials.net_balance == 399226445
    assert filing.financials.ref is not None and filing.financials.ref.page == 11


async def test_money_fallback_mar_2025(tmp_path: Path) -> None:
    """Third synthetic quarter: same labels, March 2025 amounts."""
    lines = _section_ix_lines(["8,60,86,438", "14,91,74,357", "13,18,34,933", "49,19,61,415", "54,84,00,802"])
    ingested, _ = _doc_with_section_ix(tmp_path, lines)
    doc_id = ingested.source.doc_id
    text = json.dumps({"doc_id": doc_id, "financials": {}})
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert filing.financials.received_this_quarter == 86086438
    assert filing.financials.spent_this_quarter == 149174357
    assert filing.financials.cumulative_received == 131834933
    assert filing.financials.cumulative_spent == 491961415
    assert filing.financials.net_balance == 548400802
    assert filing.financials.ref is not None and filing.financials.ref.page == 11


async def test_indian_grouping_matches_plain_digits(tmp_path: Path) -> None:
    lines = [f"row {i} of the report body" for i in range(30)] + ["Total 1,81,50,755 received"]
    ingested, _ = _doc_with_text(tmp_path, lines)
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": doc_id,
            "financials": {
                "received_this_quarter": "18150755",
                "ref": {"doc_id": doc_id, "page": 1},
            },
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert filing.financials.received_this_quarter == 18150755


async def test_present_percent_is_kept(tmp_path: Path) -> None:
    lines = [f"row {i} of the report body" for i in range(30)] + ["Slabs 40% complete"]
    ingested, _ = _doc_with_text(tmp_path, lines)
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": doc_id,
            "activities": [
                {
                    "name": "Slabs of Super Structure",
                    "percent_done": 40.0,
                    "ref": {"doc_id": doc_id, "page": 1},
                }
            ],
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert filing.activities[0].percent_done == 40.0


async def test_row_aligned_percent_is_kept(tmp_path: Path) -> None:
    """Second read: 100 sits on the item 9 row, so it survives."""
    lines = [f"row {i} of the report body" for i in range(30)] + [
        "9. 7th Slabs of Super Structure 100",
        "11. Other works 70",
    ]
    ingested, _ = _doc_with_text(tmp_path, lines)
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": doc_id,
            "activities": [
                {
                    "name": "9. 7th Slabs of Super Structure",
                    "percent_done": 100.0,
                    "ref": {"doc_id": doc_id, "page": 1},
                }
            ],
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert filing.activities[0].percent_done == 100.0


async def test_misaligned_percent_is_kept_fallback(tmp_path: Path) -> None:
    """FIX A fallback: value-on-page is the only gate, so 70 survives even off its row."""
    lines = [f"row {i} of the report body" for i in range(30)] + [
        "9. 7th Slabs of Super Structure 100",
        "11. Other works 70",
    ]
    ingested, _ = _doc_with_text(tmp_path, lines)
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": doc_id,
            "activities": [
                {
                    "name": "9. 7th Slabs of Super Structure",
                    "percent_done": 70.0,
                    "ref": {"doc_id": doc_id, "page": 1},
                }
            ],
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert filing.activities[0].percent_done == 70.0
    assert "value not found on cited page" not in filing.notes


async def test_no_collection_row_keeps_zero(tmp_path: Path) -> None:
    lines = ["lorem ipsum dolor sit amet"] * 30 + ["Amount received: No Collection"]
    ingested, _ = _doc_with_text(tmp_path, lines)
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": doc_id,
            "financials": {
                "received_this_quarter": "0",
                "ref": {"doc_id": doc_id, "page": 1},
            },
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert filing.financials.received_this_quarter == 0
    assert "value not found on cited page" not in filing.notes


async def test_equals_zero_zero_suffix_matches(tmp_path: Path) -> None:
    lines = [f"row {i} of the report body" for i in range(30)] + ["Total 17,78,000=00 spent"]
    ingested, _ = _doc_with_text(tmp_path, lines)
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": doc_id,
            "financials": {
                "spent_this_quarter": "1778000",
                "ref": {"doc_id": doc_id, "page": 1},
            },
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert filing.financials.spent_this_quarter == 1778000


async def test_other_state_text_pdf_reads_without_error(tmp_path: Path) -> None:
    """Synthetic Karnataka-style update: percent complete, floor-wise table, collected vs withdrawn."""
    lines = [
        "Karnataka RERA quarterly update",
        "Overall percent complete 62%",
        "Floor 1 slab 100%, Floor 2 slab 100%, Floor 3 slab 40%",
        "Amount collected 2500000, Amount withdrawn 2100000",
        *[f"row {i} of the report body" for i in range(30)],
    ]
    ingested, _ = _doc_with_text(tmp_path, lines)
    assert ingested.is_scanned is False
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": doc_id,
            "activities": [
                {
                    "name": "Overall percent complete 62%",
                    "percent_done": 62.0,
                    "ref": {"doc_id": doc_id, "page": 1},
                }
            ],
            "financials": {
                "cumulative_received": "2500000",
                "cumulative_spent": "2100000",
                "ref": {"doc_id": doc_id, "page": 1},
            },
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert filing.activities[0].percent_done == 62.0
    assert filing.financials.cumulative_received == 2500000
    assert filing.financials.cumulative_spent == 2100000


async def test_value_without_ref_is_nulled(tmp_path: Path) -> None:
    ingested, _ = _doc_with_text(tmp_path, [f"row {i} of the report body" for i in range(30)])
    text = json.dumps({"doc_id": ingested.source.doc_id, "financials": {"received_this_quarter": "100"}})
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert filing.financials.received_this_quarter is None
    assert filing.notes == ["1 value could not be matched on the cited page"]


def _doc_with_tables(tmp_path: Path) -> tuple:
    body = "\n".join(f"row {i} of the report body" for i in range(30))
    pdf = make_pdf(
        tmp_path / "q.pdf",
        [
            {"text": body},
            {"text": body + "\nCONSTRUCTION PROGRESS TABLE\nSlabs 40% complete"},
            {"text": body + "\nFINANCIAL PROGRESS\nTotal 100 received"},
        ],
    )
    source = _source()
    source.page_count = 3
    ingested = ingest_pdf(pdf, source, tmp_path)
    assert ingested.is_scanned is False
    return ingested, pdf


async def test_text_mode_attaches_table_page_images(tmp_path: Path) -> None:
    ingested, pdf = _doc_with_tables(tmp_path)
    client = FakeModelClient('{"doc_id": "x"}')
    await extract_filing(ingested, client, check_id=None, pdf_path=pdf)
    assert len(client.calls[0]["images"]) == 2
    assert "trust the image" in client.calls[0]["text"]


async def test_text_mode_without_pdf_path_sends_no_images(tmp_path: Path) -> None:
    ingested, _ = _doc_with_tables(tmp_path)
    client = FakeModelClient('{"doc_id": "x"}')
    await extract_filing(ingested, client, check_id=None)
    assert client.calls[0]["images"] == []


async def test_table_pages_sent_as_images_with_text_omitted(tmp_path: Path) -> None:
    """Money and activity pages go as images instead of text; markers stay."""
    ingested, pdf = _doc_with_tables(tmp_path)
    client = FakeModelClient('{"doc_id": "x"}')
    await extract_filing(ingested, client, check_id=None, pdf_path=pdf)
    assert len(client.calls[0]["images"]) == 2
    text = client.calls[0]["text"]
    assert "[[PAGE 2]]" in text and "[[PAGE 3]]" in text
    assert "Slabs 40% complete" not in text
    assert "Total 100 received" not in text
    assert "row 0 of the report body" in text
    assert "trust the image" in text


def _doc_with_garbled_tables(tmp_path: Path) -> tuple:
    """OCR-garbled headings: exact matching misses, tolerant matching hits."""
    body = "\n".join(f"row {i} of the report body" for i in range(30))
    pdf = make_pdf(
        tmp_path / "q.pdf",
        [
            {"text": body},
            {"text": body + "\nCONSTRUCTION PRCGRESS OF THE PROJECT\nSlabs 40% complete"},
            {
                "text": body
                + "\nTINANLIAL t't(t\nAmount Received during the quarter\nNet Amount at end of the quarter"
            },
        ],
    )
    source = _source()
    source.page_count = 3
    ingested = ingest_pdf(pdf, source, tmp_path)
    assert ingested.is_scanned is False
    return ingested, pdf


async def test_garbled_table_heading_page_still_gets_image(tmp_path: Path) -> None:
    """Letter-corrupted headings must not silently drop the table images."""
    ingested, pdf = _doc_with_garbled_tables(tmp_path)
    client = FakeModelClient('{"doc_id": "x"}')
    await extract_filing(ingested, client, check_id=None, pdf_path=pdf)
    assert len(client.calls[0]["images"]) == 2
    text = client.calls[0]["text"]
    assert "Slabs 40% complete" not in text
    assert "Amount Received during the quarter" not in text


async def test_money_labels_detect_page_without_heading(tmp_path: Path) -> None:
    """A money page with no heading at all is found by its money labels."""
    body = "\n".join(f"row {i} of the report body" for i in range(30))
    pdf = make_pdf(
        tmp_path / "q.pdf",
        [
            {"text": body},
            {"text": body + "\nAmount Received\n8,60,86,438\nNet Amount\n1,31,83,493"},
        ],
    )
    source = _source()
    source.page_count = 2
    ingested = ingest_pdf(pdf, source, tmp_path)
    assert ingested.is_scanned is False
    client = FakeModelClient('{"doc_id": "x"}')
    await extract_filing(ingested, client, check_id=None, pdf_path=pdf)
    assert len(client.calls[0]["images"]) == 1
    assert "8,60,86,438" not in client.calls[0]["text"]


def test_fallback_rejects_corrupted_number_run() -> None:
    """Letter-corrupted digit runs ('14,9a,74,357.OO') never fill money."""
    from stackcheck.extract.filing import _fallback_money

    texts = {11: "Actual Cost Incurred during the quarter\n14,9a,74,357.OO"}
    assert _fallback_money("spent_this_quarter", texts) == (None, None)
    texts = {11: "Net Amount at end of the quarter\n6\nTotal Expenditure\n54,84,OO,8O2.OO"}
    assert _fallback_money("net_balance", texts) == (None, None)


def test_fallback_keeps_clean_number_with_noisy_paise() -> None:
    """.OO paise noise after a clean number is not corruption."""
    from decimal import Decimal

    from stackcheck.extract.filing import _fallback_money

    texts = {11: "Amount Received during the quarter\n8,60,86,438.OO"}
    assert _fallback_money("received_this_quarter", texts) == (Decimal("86086438"), 11)
    texts = {11: "Net Amount at end of the quarter\n13,18,34,933.00"}
    assert _fallback_money("net_balance", texts) == (Decimal("131834933"), 11)


async def test_image_only_values_exempt_from_nulling(tmp_path: Path) -> None:
    """Values read from the image-only pages are kept without page matching."""
    ingested, pdf = _doc_with_tables(tmp_path)
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": doc_id,
            "activities": [
                {
                    "name": "1. Slabs",
                    "percent_done": 62.0,
                    "ref": {"doc_id": doc_id, "page": 2},
                }
            ],
            "financials": {
                "net_balance": "777777",
                "ref": {"doc_id": doc_id, "page": 3},
            },
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None, pdf_path=pdf)
    assert filing.activities[0].percent_done == 62.0
    assert filing.financials.net_balance == 777777
    assert "could not be matched" not in " ".join(filing.notes)


async def test_scanned_without_pdf_path_raises(tmp_path: Path) -> None:
    ingested, _ = _doc(tmp_path, 2, True)
    with pytest.raises(IngestError, match="scanned PDF needs pdf_path"):
        await extract_filing(ingested, FakeModelClient('{"doc_id": "x"}'), check_id=None)


async def test_merge_first_scalar_wins_and_activities_dedup(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "q.pdf", [{"text": "short note 50"} for _ in range(12)])
    source = _source()
    source.page_count = 12
    ingested = ingest_pdf(pdf, source, tmp_path)
    assert ingested.is_scanned is True
    doc_id = ingested.source.doc_id
    batch1 = json.dumps(
        {
            "doc_id": doc_id,
            "project_reg_no": "R1",
            "activities": [
                {
                    "name": "Slabs of Super Structure",
                    "percent_done": 50,
                    "ref": {"doc_id": doc_id, "page": 1},
                }
            ],
        }
    )
    batch2 = json.dumps(
        {
            "doc_id": doc_id,
            "project_reg_no": "R2",
            "activities": [
                {
                    "name": "Slabs of Super Structure",
                    "percent_done": 60,
                    "ref": {"doc_id": doc_id, "page": 11},
                }
            ],
            "notes": ["n1"],
        }
    )
    filing = await extract_filing(ingested, FakeModelClient([batch1, batch2]), check_id=None, pdf_path=pdf)
    assert filing.project_reg_no == "R1"
    assert [(a.name, a.percent_done) for a in filing.activities] == [("Slabs of Super Structure", 50)]
    assert filing.notes == ["n1"]


async def test_request_text_has_page_markers_and_doc_id(tmp_path: Path) -> None:
    ingested, _ = _doc(tmp_path, 2, False)
    client = FakeModelClient('{"doc_id": "x"}')
    await extract_filing(ingested, client, check_id=None)
    text = client.calls[0]["text"]
    assert "[[PAGE 1]]" in text
    assert ingested.source.doc_id in text


def test_score_null_vs_null_correct() -> None:
    truth = QuarterFiling(doc_id="d", quarter_end=None)
    assert score(truth, QuarterFiling(doc_id="d"))["fields"]["quarter_end"] is True


def test_score_value_match_and_mismatch() -> None:
    truth = QuarterFiling(
        doc_id="d",
        blocks=[BlockProgress(block_label="MAIN", slabs_claimed_count=3)],
    )
    assert score(truth, truth.model_copy())["fields"]["blocks[MAIN].slabs_claimed_count"] is True
    other = QuarterFiling(
        doc_id="d",
        blocks=[BlockProgress(block_label="MAIN", slabs_claimed_count=4)],
    )
    assert score(truth, other)["fields"]["blocks[MAIN].slabs_claimed_count"] is False


def test_score_splits_left_null_and_wrong_value() -> None:
    truth = QuarterFiling(
        doc_id="d",
        activities=[
            ActivityProgress(name="Gone", percent_done=10),
            ActivityProgress(name="Changed", percent_done=10),
        ],
    )
    extracted = QuarterFiling(
        doc_id="d",
        activities=[ActivityProgress(name="Changed", percent_done=20)],
    )
    result = score(truth, extracted)
    assert result["left_null"] == ["activities[Gone].percent_done"]
    assert result["wrong_value"] == ["activities[Changed].percent_done"]


def test_score_activity_matched_by_normalised_name() -> None:
    truth = QuarterFiling(
        doc_id="d",
        activities=[ActivityProgress(name="Slabs  of Super Structure", percent_done=50)],
    )
    good = QuarterFiling(
        doc_id="d",
        activities=[ActivityProgress(name="Slabs of Super Structure", percent_done=50)],
    )
    key = "activities[Slabs of Super Structure].percent_done"
    assert score(truth, good)["fields"][key] is True
    result = score(truth, QuarterFiling(doc_id="d", activities=[]))
    assert result["fields"][key] is False
    assert result["total"] >= 1


def test_value_fragment_does_not_match_inside_longer_number() -> None:
    """Whole-token matching: a fragment never verifies against a longer number."""
    assert _value_on_page(56195, "Net Amount 28,10,56,195") is False
    assert _value_on_page(5484, "Net Amount 54,84,00,802") is False
    assert _value_on_page(74357, "Actual Cost 14,91,74,357") is False
    assert _value_on_page(40, "140 flats sanctioned") is False


def test_whole_token_value_still_matches() -> None:
    assert _value_on_page(40, "Slabs 40 percent complete") is True
    assert _value_on_page(100, "100 percent") is True


async def test_scanned_mismatch_values_kept_with_single_note(tmp_path: Path) -> None:
    """Scanned OCR text is unreliable: mismatched values are kept, one note total."""
    ingested, pdf = _doc(tmp_path, 2, True)
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": doc_id,
            "blocks": [
                {"block_label": "MAIN", "slabs_claimed_count": 7, "ref": {"doc_id": doc_id, "page": 1}}
            ],
            "activities": [
                {
                    "name": "Slabs work",
                    "percent_done": 40.0,
                    "ref": {"doc_id": doc_id, "page": 1},
                }
            ],
            "financials": {
                "spent_this_quarter": "99999999",
                "cumulative_spent": "88888888",
                "ref": {"doc_id": doc_id, "page": 1},
            },
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None, pdf_path=pdf)
    assert filing.blocks[0].slabs_claimed_count == 7
    assert filing.activities[0].percent_done == 40.0
    assert filing.financials.spent_this_quarter == 99999999
    assert filing.financials.cumulative_spent == 88888888
    assert filing.notes == ["scanned page text unreliable, values kept without page match"]


async def test_scanned_matching_values_add_no_note(tmp_path: Path) -> None:
    """Scanned values that do match the page text get no extra note."""
    pdf = make_pdf(tmp_path / "q.pdf", [{"text": "Net Amount 99999999"} for _ in range(2)])
    source = _source()
    source.page_count = 2
    ingested = ingest_pdf(pdf, source, tmp_path)
    assert ingested.is_scanned is True
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": doc_id,
            "financials": {
                "spent_this_quarter": "99999999",
                "ref": {"doc_id": doc_id, "page": 1},
            },
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None, pdf_path=pdf)
    assert filing.financials.spent_this_quarter == 99999999
    assert filing.notes == []


def test_code_sha_of_tmp_file(tmp_path: Path) -> None:
    """code_sha_of returns the live sha256 of the file bytes."""
    import hashlib

    from stackcheck.extract.filing import code_sha_of

    target = tmp_path / "snippet.py"
    target.write_bytes(b"print('hi')\n")
    assert code_sha_of(target) == hashlib.sha256(b"print('hi')\n").hexdigest()
    assert code_sha_of(target) == code_sha_of(target)


async def test_extract_passes_filing_code_sha(tmp_path: Path) -> None:
    """Every model call from extraction carries this file's sha in code_sha."""
    from pathlib import Path as _Path

    from stackcheck.extract import filing as filing_mod

    ingested, _ = _doc_with_text(tmp_path, [f"row {i} of the report body" for i in range(30)])
    client = FakeModelClient('{"doc_id": "x"}')
    await extract_filing(ingested, client, check_id=None)
    expected = filing_mod.code_sha_of(_Path(filing_mod.__file__))
    assert client.calls and all(call.get("code_sha") == expected for call in client.calls)


async def test_fragment_money_value_is_nulled(tmp_path: Path) -> None:
    """56195 is only a fragment of 281056195 on the page, so it is nulled."""
    lines = [f"row {i} of the report body" for i in range(30)] + ["Net Amount 28,10,56,195"]
    ingested, _ = _doc_with_text(tmp_path, lines)
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": doc_id,
            "financials": {
                "net_balance": "56195",
                "ref": {"doc_id": doc_id, "page": 1},
            },
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert filing.financials.net_balance is None
    assert filing.notes == ["1 value could not be matched on the cited page"]


async def test_invented_ref_doc_ids_rewritten_to_source_doc_id(tmp_path: Path) -> None:
    """Reader-made doc ids (VEENA_VATIKA_...) never survive; pages are kept."""
    ingested, _ = _doc_with_text(tmp_path, [f"row {i} of the report body" for i in range(30)])
    doc_id = ingested.source.doc_id
    text = json.dumps(
        {
            "doc_id": "VEENA_VATIKA_SEP2023",
            "refs": {"a": {"doc_id": "KKT/21/23-24", "page": 1}},
            "blocks": [{"block_label": "MAIN", "ref": {"doc_id": "VEENA_VATIKA_SEP2023", "page": 1}}],
            "activities": [{"name": "1. Excavation", "ref": {"doc_id": "VEENA_VATIKA_SEP2023", "page": 1}}],
            "financials": {"ref": {"doc_id": "VEENA_VATIKA_SEP2023", "page": 1}},
        }
    )
    filing = await extract_filing(ingested, FakeModelClient(text), check_id=None)
    assert filing.doc_id == doc_id
    assert filing.refs["a"].page == 1 and filing.refs["a"].doc_id == doc_id
    assert filing.blocks[0].ref is not None
    assert (filing.blocks[0].ref.page, filing.blocks[0].ref.doc_id) == (1, doc_id)
    assert filing.activities[0].ref is not None
    assert (filing.activities[0].ref.page, filing.activities[0].ref.doc_id) == (1, doc_id)
    assert filing.financials.ref is not None
    assert (filing.financials.ref.page, filing.financials.ref.doc_id) == (1, doc_id)


GARBLED_ACTIVITY_LINES = [
    "3. BLOCK-B-2",
    "S. NO. Tasks/ Activitv Percentage of Actual Work Done in Quarter Ending March of 2O25",
    "1 Ercavation 100%",
    "3 Pile Foundation 101o/a",
    "6 Stilt Floor (Wing-A) 700o/o",
    "7 Stilt Floor (Wing-B) 1A0o/o",
    "9 7th Slabs of Super Structure (Wing-B) 70Ao/o",
    "11 Sanitary Fittings 7 0o/o",
]

CLEAN_ACTIVITY_LINES = [
    "3. BLOCK-B-2",
    "S. NO. Tasks/ Activity Percentage of Actual Work Done in Quarter Ending March 2025",
    "1 Excavation 100%",
    "3 Pile Foundation 100%",
    "6 Stilt Floor (Wing-A) 100%",
    "9 7th Slabs of Super Structure (Wing-B) 100%",
    "11 Sanitary Fittings 70%",
]


def test_layer_garbled_flags_ocr_slash_tokens() -> None:
    """The Mar 2025 p6 value column (70Ao/o and friends) fails the gate."""
    from stackcheck.extract.filing import _layer_garbled

    reason = _layer_garbled("\n".join(GARBLED_ACTIVITY_LINES))
    assert reason is not None
    assert "70Ao/o" in reason or "slash" in reason


def test_layer_garbled_keeps_clean_value_text() -> None:
    """Percents, lakh money, and/or and Flat/Premises are not garble."""
    from stackcheck.extract.filing import _layer_garbled

    clean = "\n".join([*CLEAN_ACTIVITY_LINES, "cost 1,81,50,755 and/or Flat/Premises N/A"])
    assert _layer_garbled(clean) is None


def test_layer_garbled_flags_letter_substituted_money() -> None:
    """Sept 2024 p11 money column (s,97,,46,O93,OO) fails the gate."""
    from stackcheck.extract.filing import _layer_garbled

    reason = _layer_garbled(
        "Amount Received during the quarter 9,63,37,925.0O\n"
        "Actual Cost Incurred during the quarter s,97,,46,O93,OO\n"
        "Net Amount at end of the quarter LL,42,92,O06.OO\n"
        "Cumulative Expenditure 54,84,OO,8O2.OO"
    )
    assert reason is not None
    assert "letter" in reason


def test_layer_garbled_keeps_clean_money_text() -> None:
    """Rs. amounts, reg ids and dates are not letter-substitution garble."""
    from stackcheck.extract.filing import _layer_garbled

    clean = (
        "Amount Received 9,63,37,925.00\n"
        "Cost Rs. 5,91,46,093 and 1,81,50,755\n"
        "RERAP08022024125722-104141030122025.pdf ref Q2 FY2024-25 G+4\n"
        "Dated 30-Dec-2025"
    )
    assert _layer_garbled(clean) is None


def test_page_kind_finds_activity_pages_exact_and_fuzzy() -> None:
    from stackcheck.extract.filing import _page_kind

    assert _page_kind("Percentage of Actual Work Done in Quarter Ending March 2025") == "activity"
    assert _page_kind("Percenta-ge ot Actual Work Done in Quarter Ending March 2O25") == "activity"
    assert _page_kind("QUARTERLY PROGRESS REPORT cover page") is None


def _two_page_doc(tmp_path: Path, first_lines: list[str]):
    second = [f"row {i} of the report narrative body text" for i in range(30)]
    pdf = make_pdf(tmp_path / "q.pdf", [{"text": "\n".join(first_lines)}, {"text": "\n".join(second)}])
    source = _source()
    ingested = ingest_pdf(pdf, source, tmp_path)
    assert ingested.is_scanned is False
    return ingested, pdf


async def test_garbled_activity_page_sent_as_image(tmp_path: Path, caplog) -> None:
    """Garbled activity page: layer dropped, stub + image sent, decision logged."""
    import logging

    from stackcheck.extract.filing import IMAGE_STUB

    ingested, pdf = _two_page_doc(tmp_path, GARBLED_ACTIVITY_LINES)
    client = FakeModelClient('{"doc_id": "x"}')
    with caplog.at_level(logging.INFO, logger="stackcheck.extract.filing"):
        await extract_filing(ingested, client, check_id=None, pdf_path=pdf)
    text = client.calls[0]["text"]
    assert "70Ao/o" not in text
    assert f"[[PAGE 1]]\n{IMAGE_STUB}" in text
    assert "row 0 of the report narrative" in text
    assert len(client.calls[0]["images"]) == 1
    assert "page 1" in caplog.text and "dropped" in caplog.text


async def test_clean_activity_page_keeps_text(tmp_path: Path) -> None:
    """Clean activity page: text kept, no image attached."""
    ingested, pdf = _two_page_doc(tmp_path, CLEAN_ACTIVITY_LINES)
    client = FakeModelClient('{"doc_id": "x"}')
    await extract_filing(ingested, client, check_id=None, pdf_path=pdf)
    text = client.calls[0]["text"]
    assert "7th Slabs of Super Structure (Wing-B) 100%" in text
    assert client.calls[0]["images"] == []


def test_select_images_caps_eight_money_first() -> None:
    """9 candidates: dropped money outranks dropped activity; lowest cut."""
    from stackcheck.extract.filing import _select_images

    dropped = {9: ("money", "digit-letter garble")}
    dropped.update({n: ("activity", "slash-o garble") for n in (5, 6, 7, 10)})
    selected, cut = _select_images({1, 2, 3, 4}, dropped)
    assert selected == {1, 2, 3, 4, 5, 6, 7, 9}
    assert cut == [10]


def test_select_images_under_cap_keeps_all() -> None:
    from stackcheck.extract.filing import _select_images

    selected, cut = _select_images({1, 4}, {6: ("activity", "slash-o garble")})
    assert selected == {1, 4, 6} and cut == []


async def test_ninth_image_cut_logged(tmp_path: Path, caplog) -> None:
    """4 table + 5 dropped: 8 images sent, page 9 keeps text, cut logged."""
    import logging

    ingested, pdf = _nine_page_doc(tmp_path)
    client = FakeModelClient('{"doc_id": "x"}')
    with caplog.at_level(logging.INFO, logger="stackcheck.extract.filing"):
        await extract_filing(ingested, client, check_id=None, pdf_path=pdf)
    assert len(client.calls[0]["images"]) == 8
    assert "70Ao/o" in client.calls[0]["text"]
    assert "cut" in caplog.text and "page 9" in caplog.text


def _nine_page_doc(tmp_path: Path):
    money_clean = "\n".join(
        ["Amount Received during the quarter", "Net Amount at end of the quarter"]
        + [f"narrative filler line {i} with plain words" for i in range(20)]
    )
    money_garbled = "\n".join(
        [
            "Amount Received during the quarter 9,63,37,925.0O",
            "Actual Cost Incurred s,97,,46,O93,OO",
            "Net Amount LL,42,92,O06.OO",
            "Cumulative Expenditure 54,84,OO,8O2.OO",
        ]
    )
    activity = "\n".join(GARBLED_ACTIVITY_LINES)
    pages = [{"text": money_clean}] * 4 + [{"text": money_garbled}] + [{"text": activity}] * 4
    pdf = make_pdf(tmp_path / "q.pdf", pages)
    source = _source()
    ingested = ingest_pdf(pdf, source, tmp_path)
    assert ingested.is_scanned is False
    return ingested, pdf
