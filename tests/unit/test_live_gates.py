"""Every measurement script that spends money or hits the network refuses without the live flag."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from bench_floors import main as bench_main
from compare_providers import main as compare_main
from eval_extract import main as eval_extract_main
from eval_photos import main as eval_photos_main
from fetch_bench import main as fetch_main
from fetch_bench_more import main as fetch_more_main
from run_project import main as run_main
from smoke_meta import main as smoke_meta_main
from smoke_open import main as smoke_open_main
from spike_sam import main as spike_main


@pytest.mark.parametrize(
    "main",
    [
        bench_main,
        compare_main,
        eval_extract_main,
        eval_photos_main,
        fetch_main,
        fetch_more_main,
        run_main,
        smoke_meta_main,
        smoke_open_main,
        spike_main,
    ],
)
def test_live_scripts_refuse_without_flag(main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STACKCHECK_LIVE", raising=False)
    with pytest.raises(SystemExit, match="STACKCHECK_LIVE"):
        main()
