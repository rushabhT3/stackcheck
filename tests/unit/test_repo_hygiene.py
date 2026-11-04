import shutil
import subprocess
from pathlib import Path


def test_no_forbidden_files_tracked() -> None:
    if shutil.which("git") is None:
        import pytest

        pytest.skip("git not available")
    root = Path(__file__).resolve().parents[2]
    if not (root / ".git").exists():
        import pytest

        pytest.skip("no .git directory")
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    paths = out.stdout.splitlines()
    assert ".env" not in paths
    for path in paths:
        assert not path.endswith(".pdf"), path
        assert not path.startswith("data/work/"), path
        assert not path.startswith("data/cache/"), path
        assert not path.startswith("data/uploads/"), path
