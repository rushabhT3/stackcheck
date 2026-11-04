# T03: SAM 3.1 client, output parser, input probe, prompt spike

**Owner:** Muse Code, with two live runs by the human. **Day:** D1. **Read:** AGENTS.md; CONTRACTS C1, C6, C7, C10; FACTS "SAM" lines and UNVERIFIED table.

## Goal

Call SAM 3.1 on an image with one noun phrase, get the raw output text (cached, costed), and parse it into `Detection` objects. Then find out by measurement (a) how SAM wants the image passed and (b) which prompts find repeated building elements best.

## Files

Create: `src/stackcheck/vision/sam.py`, `vision/sam_parse.py`, `scripts/probe_sam_input.py`, `scripts/spike_sam.py`, `tests/unit/test_sam_parse.py`, `tests/unit/test_sam_client.py`. Add `FakeSamSDK` to `tests/fakes.py`.

## Steps

1. `sam_parse.py`: `parse_sam_output(text, prompt)` exactly per C10 parsing rules. Pure.
2. `sam.py`: `SamClient.segment_image(image_path, prompt, check_id)`:
   - read bytes, compute SHA-256, build cache key per C6 (includes `settings.sam_image_mode`);
   - on miss: `check_budget`, build the request per C10, stream, join `response.output_text.delta` deltas, store `{"output_text": ...}`;
   - `record(provider="meta", model="sam-3.1", units=1, ...)` with `cache_hit` set correctly;
   - `file_id` mode: upload once per image SHA with `sdk.files.create(file=(name, bytes, mime), purpose="user_data")`, cache the returned id in the same cache under key parts `{"provider": "meta", "kind": "file_upload", "sha": ...}`;
   - prompts must be 1 to 6 words, lowercase letters and spaces only; anything else raises `ValueError` (SAM wants short noun phrases, never questions).
3. `scripts/probe_sam_input.py` (live, refuses without `STACKCHECK_LIVE=1`): takes one image path. Tries `data_url` mode with prompt "building"; on any API error prints the error class and message (no key) and tries `file_id`. Prints `SAM_IMAGE_MODE: data_url` or `file_id` or `NONE`, the first 300 characters of output, and the parsed detection count.
4. `scripts/spike_sam.py` (live): arguments `--images data/bench/images --limit 10 --prompts "building,balcony,window,window opening,floor slab,concrete column,storey"`. For each image and prompt, call `segment_image` and parse. Print a table: prompt, images with ≥ 1 detection, images with ≥ 6 detections, median detections per image, mean confidence. Write `data/eval/spike_sam.json`. Recommend the two non-"building" prompts with the most images having ≥ 6 detections (ties: higher mean confidence). Print the recommended `STACKCHECK_SAM_PROMPTS` value.

## Tests first

`test_sam_parse.py`:
- empty string and whitespace return `[]`;
- one frame with two records (use your own synthetic line: `<0f>0<|box;x1=10;y1=20;x2=30;y2=40;w=100;h=200|><|mask;x=0;y=0;data=5,5,~abc,de|>,1<|box;x1=50;y1=20;x2=70;y2=40;w=100;h=200;c=0.88|><|mask;x=0;y=0;data=5,5,~xyz|>`) gives 2 detections with correct boxes, ids, image size, confidence `None` and `0.88`;
- commas inside the mask payload don't create extra records;
- unknown box keys are ignored;
- a missing `y2` raises `SamParseError`;
- a line without `<Nf>` raises `SamParseError` naming the line number;
- two frame lines give detections with `frame` 0 and 3;
- blank lines between frames are skipped.

`test_sam_client.py` (with `FakeSamSDK` whose `responses.create` returns an async iterator of objects with `.type` and `.delta`):
- request uses model `sam-3.1`, `stream=True`, `metadata={"mask_encoding": "one_bit"}`, one `input_text` with the prompt, one `input_image` with a `data:image/png;base64,` URL;
- deltas split across events are joined in order; non-delta events are ignored;
- the second identical call is a cache hit (SDK not called) and records zero cost;
- `file_id` mode uploads once for two prompts on the same image;
- prompt "Segment the windows?" raises `ValueError`.

## Done when

Tests green, ruff clean. Human runs `probe_sam_input.py` on one benchmark image and `spike_sam.py` on 10 benchmark images with `STACKCHECK_LIVE=1`, pastes outputs. Agent records the image mode and the spike table in `PROGRESS.md`, updates `docs/FACTS.md` (move the data URL line to Verified or note the fallback), and sets the defaults `sam_image_mode` and `sam_prompts` in `config.py` to the measured winners. This is the only allowed change to C1 defaults, and it must be noted in the ARCHITECTURE decision log as D16.

Gate G1 needs at least one box on 2 of 3 images. If nothing works in either mode, write STUCK.md with both error messages.

## How an expert thinks about it

SAM finds things you can name. "Floor" isn't a thing with a clear outline; a balcony or a window opening is, and floors are just rows of them. That's why the spike measures elements, not floors. The spike costs about $0.18 for 10 images and 7 prompts, and it replaces an argument with a number. Cache everything: T07 reuses these exact outputs for free.

## Traps

- Don't send more than one concept per request ("balcony and window" is wrong).
- Don't decode masks (D11).
- Don't assume `c=` is present; it's optional in the grammar.
- Streaming events can arrive split anywhere, including inside a token. Join first, parse after.
