"""Recount sheet for the 25 bench photos.

Writes one local HTML file (default data/bench/recount.html) that shows each
bench image one at a time with a floors number box and a "can't tell" box.
It never shows the existing AI label or any model output: the person counts
blind. A download button saves data/bench/labels_human.csv with columns
image_id, floors, cannot_tell, counted_by, counted_on.
"""

import argparse
import csv
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

BRAND_GREEN = "#0f7a45"
BRAND_DEEP = "#0b5a34"
BRAND_WASH = "#f8fdf9"


def read_bench_files(labels_path: Path) -> list[str]:
    """File names from the bench labels CSV, in row order."""
    try:
        text = labels_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        logger.warning("Skipping unreadable %s: %s", labels_path, exc)
        return []
    files: list[str] = []
    for row in csv.DictReader(text.splitlines()):
        name = (row.get("file") or "").strip()
        if name:
            files.append(name)
    return files


RECOUNT_VIDEOS = ("V01", "V02", "V03", "V05", "V07", "V08")


def read_sheet_files(sheets_dir: Path) -> list[str]:
    """Contact sheets to count, as page-relative paths. Missing sheets are skipped."""
    if not sheets_dir.is_dir():
        return []
    return [f"sheets/{stem}.png" for stem in RECOUNT_VIDEOS if (sheets_dir / f"{stem}.png").is_file()]


def build_page(files: list[str]) -> str:
    """Standalone recount page. Only page-relative paths are embedded, no counts."""
    payload = json.dumps(files)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Bench recount &mdash; StackCheck</title>
<style>
body {{ font-family: system-ui, sans-serif; background: {BRAND_WASH}; color: #0f271a;
  max-width: 760px; margin: 2rem auto; padding: 0 1rem; }}
h1 {{ color: {BRAND_DEEP}; font-size: 1.4rem; }}
img {{ max-width: 100%; max-height: 60vh; border: 1px solid #d9eadf; border-radius: 12px; background: #fff; }}
.row {{ margin: 0.8rem 0; }}
label {{ display: block; margin: 0.4rem 0; }}
input[type="number"] {{ font-size: 1.2rem; width: 6rem; padding: 0.3rem; }}
button {{ background: {BRAND_GREEN}; color: #fff; border: 0; border-radius: 999px;
  padding: 0.6rem 1.4rem; font-size: 1rem; margin-right: 0.5rem; }}
button:disabled {{ opacity: 0.4; }}
.meta {{ color: #3a5a45; }}
</style>
</head>
<body>
<h1>Bench recount</h1>
<p class="meta">Count the visible floors in each photo. Tick "can't tell" when the photo
cannot be counted. Nothing here shows the earlier count.</p>
<div class="row">
<label>Your name (counted_by) <input id="who" type="text" autocomplete="name"></label>
<label>Date (counted_on) <input id="when" type="date"></label>
</div>
<p class="meta" id="progress"></p>
<img id="photo" alt="bench photo to count">
<div class="row">
<label>Floors <input id="floors" type="number" min="0" max="200"></label>
<label><input id="cannot_tell" type="checkbox"> can't tell</label>
</div>
<div class="row">
<button id="prev">Back</button>
<button id="next">Next</button>
<button id="download">Download labels_human.csv</button>
</div>
<script>
const files = {payload};
const answers = files.map((f) => ({{image_id: f, floors: "", cannot_tell: ""}}));
let i = 0;
const img = document.getElementById("photo");
const num = document.getElementById("floors");
const cant = document.getElementById("cannot_tell");
const prog = document.getElementById("progress");
function show() {{
  img.src = files[i];
  num.value = answers[i].floors;
  cant.checked = answers[i].cannot_tell === "yes";
  prog.textContent = (i + 1) + " of " + files.length;
  document.getElementById("prev").disabled = i === 0;
  document.getElementById("next").disabled = i === files.length - 1;
}}
function save() {{
  answers[i].floors = num.value;
  answers[i].cannot_tell = cant.checked ? "yes" : "";
}}
document.getElementById("prev").onclick = () => {{ save(); if (i > 0) i--; show(); }};
document.getElementById("next").onclick = () => {{ save(); if (i < files.length - 1) i++; show(); }};
document.getElementById("download").onclick = () => {{
  save();
  const who = document.getElementById("who").value;
  const when = document.getElementById("when").value;
  let csv = "image_id,floors,cannot_tell,counted_by,counted_on\\n";
  for (const a of answers) {{
    csv += [a.image_id, a.floors, a.cannot_tell, who, when].map((v) =>
      /[",\\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v).join(",") + "\\n";
  }}
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], {{type: "text/csv"}}));
  a.download = "labels_human.csv";
  a.click();
}};
show();
</script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    """Write the recount page."""
    parser = argparse.ArgumentParser(description="Write the bench recount HTML page.")
    parser.add_argument("--labels", default="data/bench/labels.csv")
    parser.add_argument("--sheets", default="data/eval/bench_video_sheets")
    parser.add_argument("--out", default="data/bench/recount.html")
    args = parser.parse_args(argv)
    files = [f"images/{name}" for name in read_bench_files(Path(args.labels))]
    sheets_dir = Path(args.sheets)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    for sheet in read_sheet_files(sheets_dir):
        target = out.parent / sheet
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((sheets_dir / Path(sheet).name).read_bytes())
        files.append(sheet)
    out.write_text(build_page(files), encoding="utf-8")
    print(f"Wrote {out} ({len(files)} items)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
