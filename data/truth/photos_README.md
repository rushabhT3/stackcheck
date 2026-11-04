# Photo truth labels

One row per photo, keyed by SHA-256 of the image bytes. No image bytes or
filing names live in the repo; the hashes are enough to match files under
`data/work/**/images/`.

## Columns (`data/truth/photos.csv`)

`sha256,kind,stamp_date,storeys_visible,top_obscured`

- `sha256`: hex SHA-256 of the PNG bytes, 64 chars.
- `kind`: `elevation`, `floor_level`, `site_other` or `document_or_logo`.
  Elevation means the outside of a building from the side or front with
  several storeys visible. Scaffolding, nets or the photo edge hiding the
  top does not change the kind.
- `stamp_date`: what the stamp prints, `YYYY-MM-DD`, else empty. Stamps are
  day first: `05/03/2025` is 5 March 2025. When the order is ambiguous,
  leave it empty rather than guess.
- `storeys_visible`: elevations only, else empty. Count storeys you can see,
  including the ground floor. A storey counts when its floor level or its
  row of openings is visible. Do not count a level with only columns or rods
  and no slab above it.
- `top_obscured`: `true` when nets, scaffolding, sheets, trees or the photo
  edge hide the top of the building, else `false`.

## How to label

1. List candidate photos: hash every PNG under `data/work/**/images/` and
   pick 40 with buildings, stamps and varied kinds.
2. Look at each photo (use the PNG in `data/work`, never commit it) and fill
   one row. Leave a cell empty when the photo does not show it.
3. Count each building's storeys twice; on disagreement write the lower.

## Eval

`uv run python scripts/eval_photos.py` describes every labelled photo found
under `data/work/` and writes `data/eval/photos.json`. Targets: kind ≥ 90%,
stamp date ≥ 90%.
