import pytest

from stackcheck.errors import SamParseError
from stackcheck.vision.sam_parse import parse_sam_output

TWO_RECORDS = (
    "<0f>0<|box;x1=10;y1=20;x2=30;y2=40;w=100;h=200|><|mask;x=0;y=0;data=5,5,~abc,de|>,"
    "1<|box;x1=50;y1=20;x2=70;y2=40;w=100;h=200;c=0.88|><|mask;x=0;y=0;data=5,5,~xyz|>"
)


def test_empty_and_whitespace_return_empty() -> None:
    assert parse_sam_output("", "balcony") == []
    assert parse_sam_output("   \n  ", "balcony") == []


def test_one_frame_two_records() -> None:
    first, second = parse_sam_output(TWO_RECORDS, "balcony")
    assert (first.object_id, second.object_id) == (0, 1)
    assert (first.frame, second.frame) == (0, 0)
    assert (first.box.x1, first.box.y1, first.box.x2, first.box.y2) == (10, 20, 30, 40)
    assert (second.box.x1, second.box.y1, second.box.x2, second.box.y2) == (50, 20, 70, 40)
    assert (first.image_w, first.image_h) == (100, 200)
    assert (first.confidence, second.confidence) == (None, 0.88)
    assert first.prompt == "balcony"


def test_commas_in_mask_do_not_split_records() -> None:
    assert len(parse_sam_output(TWO_RECORDS, "balcony")) == 2


def test_unknown_box_keys_ignored() -> None:
    (det,) = parse_sam_output("<0f>0<|box;x1=1;y1=2;x2=3;y2=4;w=10;h=20;foo=1|>", "window")
    assert (det.box.x1, det.image_w) == (1, 10)


def test_missing_key_raises() -> None:
    with pytest.raises(SamParseError):
        parse_sam_output("<0f>0<|box;x1=1;y1=2;x2=3;w=10;h=20|>", "window")


def test_line_without_frame_marker_raises_with_line_number() -> None:
    with pytest.raises(SamParseError, match="line 1"):
        parse_sam_output("hello", "window")


def test_two_frames_keep_frame_numbers() -> None:
    text = "<0f>0<|box;x1=1;y1=2;x2=3;y2=4;w=10;h=20|>\n<3f>1<|box;x1=5;y1=6;x2=7;y2=8;w=10;h=20|>"
    first, second = parse_sam_output(text, "window")
    assert (first.frame, second.frame) == (0, 3)


def test_blank_lines_between_frames_skipped() -> None:
    text = "<0f>0<|box;x1=1;y1=2;x2=3;y2=4;w=10;h=20|>\n\n<1f>1<|box;x1=5;y1=6;x2=7;y2=8;w=10;h=20|>\n"
    assert len(parse_sam_output(text, "window")) == 2
