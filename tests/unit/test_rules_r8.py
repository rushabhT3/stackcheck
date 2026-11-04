"""R8: every quarter between the first and last filing must exist."""

from datetime import date

from stackcheck.models import Finding, ProjectEvidence
from stackcheck.rules.checks import RuleSettings, r8_missing_quarter
from tests.factories import make_project, make_quarter

SETTINGS = RuleSettings()
MAR = date(2025, 3, 31)
JUN = date(2025, 6, 30)
SEP = date(2025, 9, 30)
DEC = date(2025, 12, 31)


def _run(project: ProjectEvidence) -> list[Finding]:
    return r8_missing_quarter(project, SETTINGS)


def test_gap_flags() -> None:
    project = make_project([make_quarter(MAR), make_quarter(JUN), make_quarter(DEC)])
    findings = _run(project)
    assert len(findings) == 1
    assert (findings[0].status, findings[0].severity) == ("CANT_VERIFY", "info")
    assert findings[0].title == "Can't check for missing quarters"
    assert "3 quarters apart" in findings[0].explanation
    assert findings[0].explanation.endswith("Add the quarters in between to check them.")


def test_consecutive_ok() -> None:
    project = make_project([make_quarter(MAR), make_quarter(JUN), make_quarter(SEP)])
    findings = _run(project)
    assert [f.status for f in findings] == ["OK"]


def test_single_filing() -> None:
    findings = _run(make_project([make_quarter(MAR)]))
    assert [f.status for f in findings] == ["CANT_VERIFY"]


def test_undated_filing_ignored() -> None:
    project = make_project([make_quarter(MAR), make_quarter(None)])
    findings = _run(project)
    assert [f.status for f in findings] == ["CANT_VERIFY"]
