# S1 (stretch): Buyer's own video, with SAM 3.1 video tracking

**Owner:** Muse Code. **Only after gate G6 passes.** **Read:** AGENTS.md; CONTRACTS C10, C11; FACTS UNVERIFIED row "SAM input_video".

## Goal

A buyer uploads a short phone video of their tower (MP4 or MOV, H.264/H.265, ≤ 50 MB, ≤ 30 s). StackCheck counts floors across the pan and shows the result next to the latest filing's claim. For the demo, use an openly licensed clip (Wikimedia Commons, or one Pexels clip downloaded by hand and not committed).

## Steps

1. Probe (human runs, live): `scripts/probe_sam_video.py <clip>`: send `{"type": "input_video", ...}` with a data URL, then with `file_id` from the Files API; print which works, the first 300 characters, the number of frame lines, and the ledger cost. Record the field name in FACTS. If neither works, stop: S1 is dropped.
2. `vision/sam.py`: `segment_video(path, prompt)` mirrors `segment_image`; units = frame lines returned (bill at least 50 per request in the ledger).
3. `vision/floors.py`: `count_floors_video(per_frame_building, per_frame_elements, vision_estimate)`: run `group_rows` per frame with that frame's building box; `sam_rows` = the largest row count seen in any frame where the building box height is ≥ 60% of the frame height; then the same decision rule as C11.
4. Vision estimate: Muse Spark video understanding (standard tier, since this is an upload) with a prompt adapted from `photo_v1` item 5. If video input to Muse Spark fails, use `CANT_VERIFY` with reason "only one method".
5. API: `POST /api/videos` (upload) and an optional `video_upload_id` on `POST /api/checks`; the report gets one extra R1-style finding for the video, titled "Your video shows N floors; latest report claims S slabs".
6. UI: a "Your video" card with the frame that had the most rows (store that frame index; show the uploaded video seeked to that time with a caption, since there's no local video decoding).

## Done when

One Commons or hand-downloaded clip produces a decided count that matches a hand count within 1, end to end, with the ledger showing the video cost.

## Traps

- No local video decoding libraries (not approved). SAM and Muse Spark take the file as is.
- Uploads only ever go to the standard (non-training) tier.
