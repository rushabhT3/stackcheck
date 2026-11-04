# S3 (stretch): Mask overlays with Meta's official parser

**Owner:** Muse Code. **Only after gate G6 passes.** **Read:** ARCHITECTURE D11; CONTRACTS C10, C13.

## Goal

Draw SAM's actual masks (not just boxes) on evidence photos, using Meta's own TypeScript parser so the mask codec is never re-implemented.

## Steps

1. `tools/sam-masks/` (a separate tiny Node package, not part of `web/`): `package.json` with `@meta-sam/parser` `0.1.0` only. Script `decode.mjs` reads a cached SAM `output_text` file, calls `parseImageStream` on a synthetic event stream built from the text (one `response.output_text.delta`, one `response.output_text.done`, one `response.completed`), and for each mask record writes `<out>/<objectId>.bin` (raw 0/1 bytes from `decodeMaskToRaster`) plus `masks.json` with object id, bounds and raster width and height.
2. Python `vision/overlay.py`: `draw_mask_overlay(image_path, masks_dir, out_path)` reads `masks.json` and the `.bin` files with numpy, places each raster at its bounds, tints mask pixels amber at 40% opacity, then draws the row lines as in C13.
3. Pipeline: only for photos used as evidence in a FLAG (a few per project), run the Node decoder via `subprocess.run(["node", "tools/sam-masks/decode.mjs", ...], check=True, timeout=30)`. If Node isn't available (for example in the Modal image), keep the box overlay.

## Done when

One FLAG photo shows tinted masks; the box overlay is still used everywhere else; no new Python dependency.

## Traps

- Check the `@meta-sam/parser` licence file (SAM License) allows this use before shipping; it's Meta's licence for SAM materials, so read it and note the decision in the decision log.
- The README example output in Meta's package is under that licence: write your own test fixtures.
