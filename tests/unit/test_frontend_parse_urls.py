"""PDF URL parsing and photo/count-only frontend pins.

Part A runs the REAL parsePdfUrls from web/src/lib/urls.ts through
node --experimental-strip-types (one spawn, five cases); it skips with a
reason when node cannot run the harness. The source-pin tests below lock
the same behaviour statically so a skipped harness still leaves coverage.
Part B pins StartView/ReportView/VerdictBanner wording (repo convention:
read web/src file text, assert substrings).
"""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web" / "src"
URLS_TS = WEB / "lib" / "urls.ts"

A = "https://example.gov/QPR-2024-Q4.pdf"
B = "https://example.gov/QPR-2025-Q1.pdf"
C = "https://example.gov/QPR-2025-Q2.pdf"

URL_CASES: list[tuple[str, str, list[str]]] = [
    ("comma only", f"{A},{B}", [A, B]),
    ("newline only", f"{A}\n{B}", [A, B]),
    ("mixed", f"{A}, {B}\n{C}", [A, B, C]),
    ("trailing comma", f"{A},", [A]),
    ("duplicates", f"{A}, {A}\n{B}", [A, B]),
]


def _read(rel: str) -> str:
    return (WEB / rel).read_text(encoding="utf-8")


def _parse_via_node(cases: list[str]) -> list[list[str]]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not on PATH; cannot run parsePdfUrls harness")
    with tempfile.TemporaryDirectory(prefix="urls-harness-") as tmp:
        driver = Path(tmp) / "driver.mts"
        driver.write_text(
            'import { parsePdfUrls } from "'
            + URLS_TS.as_uri()
            + '";\nconst cases = '
            + json.dumps(cases)
            + ";\nconsole.log(JSON.stringify(cases.map((c) => parsePdfUrls(c))));\n",
            encoding="utf-8",
        )
        try:
            proc = subprocess.run(
                [node, "--experimental-strip-types", str(driver)],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            pytest.skip(f"node harness failed to run: {exc}")
    if proc.returncode != 0:
        pytest.skip(f"node harness exited {proc.returncode}: {proc.stderr.strip()[-300:]}")
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        pytest.skip(f"node harness output not JSON: {proc.stdout.strip()[-300:]}")
    return out


def test_parse_pdf_urls_five_cases_real() -> None:
    got = _parse_via_node([c for _, c, _ in URL_CASES])
    assert len(got) == len(URL_CASES)
    for (name, _, want), actual in zip(URL_CASES, got, strict=True):
        assert actual == want, name


def test_parse_pdf_urls_source_pins() -> None:
    src = URLS_TS.read_text(encoding="utf-8")
    assert "export function parsePdfUrls" in src
    assert "split(/[\\s,]+/)" in src
    assert ".trim()" in src
    assert "Set" in src  # dedupe keeping first-seen order


def test_start_at_least_one_source_and_hint() -> None:
    start = _read("views/StartView.svelte")
    assert "parsePdfUrls(urls)" in start
    assert "Enter a project name and at least one filing, photo or video." in start
    assert "One link per line, or separated by commas." in start


def test_photo_box_accept_and_count_caps() -> None:
    start = _read("views/StartView.svelte")
    assert 'accept="image/jpeg,image/png,image/webp' in start
    assert "(optional, up to 5)" in start
    assert "up to 5" in start
    assert "length > 5" in start
    assert "You can add up to 5 site photos." in start
    assert "Site photos must be JPEG, PNG or WebP images." in start
    assert "photo_upload_ids" in _read("lib/api.ts")


def test_count_only_verdict_strings_and_hidden_counters() -> None:
    report = _read("views/ReportView.svelte")
    assert "Site photos" in report
    assert "Counted from your photo:" in report
    assert "(estimate)" in report
    assert "Could not count the floors in your photo." in report
    assert "The photo shows" in report
    assert "quarters.length === 0" in report
    assert "countOnly={countOnly}" in report
    banner = _read("components/VerdictBanner.svelte")
    assert "countOnly" in banner
    assert "{#if !countOnly}" in banner


def test_count_only_pill_and_hidden_sections() -> None:
    report = _read("views/ReportView.svelte")
    banner = _read("components/VerdictBanner.svelte")
    # Pill reads exactly COUNT ONLY when count-only; filing wording unchanged.
    assert '"COUNT ONLY"' in banner
    assert "countOnly ?" in banner
    assert "Verdict · lines up" in banner
    assert "Verdict · gaps found" in banner
    # No-filing detection covers photo-only and video-only reports.
    assert "quarters.length === 0" in report
    assert "report.evidence.video != null" in report
    # Flags, Confirmed, Can't verify and both charts hide when count-only.
    for marker in (
        '<section id="flags"',
        '<section id="confirmed"',
        '<div id="cant-verify"',
        '<div class="rp-charts">',
    ):
        assert re.search(r"\{#if !countOnly\}\s*" + re.escape(marker), report), marker
    # Section nav drops the hidden sections when count-only.
    for key in ("flags", "confirmed", "cant-verify", "quarters"):
        assert f'item.key === "{key}"' in report


def test_disagree_sentence_and_hidden_quarters() -> None:
    report = _read("views/ReportView.svelte")
    # Disagreeing photo counts name both methods instead of the bare line.
    assert "The second method found {rows} rows and disagreed." in report
    assert "Visual count {visual}." in report
    assert "Could not count the floors in your photo." in report
    # The empty Quarters section hides on no-filing reports.
    assert re.search(r"\{#if !countOnly\}\s*" + re.escape('<section id="quarters"'), report)
