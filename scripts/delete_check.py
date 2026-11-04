"""Remove one bad check (status, report, media) from the Modal Volume.

Run from the repo root, one check per call:
    uv run --with modal modal run scripts/delete_check.py --check-id chk_1cfe349842
"""

import modal

app = modal.App("stackcheck-maintenance")
volume = modal.Volume.from_name("stackcheck-data")


@app.function(volumes={"/data": volume}, timeout=120)
def delete_check(check_id: str) -> None:
    import re
    import shutil
    from pathlib import Path

    if re.fullmatch(r"chk_[0-9a-f]{10}", check_id) is None:
        raise ValueError(f"Refusing to delete {check_id!r}: not a check id")
    target = Path("/data/work") / check_id
    if not target.exists():
        print(f"no work dir for {check_id}; nothing to delete")
        return
    names = sorted(p.name for p in target.iterdir())
    shutil.rmtree(target)
    volume.commit()
    print(f"deleted /data/work/{check_id} ({', '.join(names)})")


@app.local_entrypoint()
def main(check_id: str) -> None:
    delete_check.remote(check_id)
