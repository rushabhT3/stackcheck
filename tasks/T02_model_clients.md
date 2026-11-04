# T02: Model clients, cache, cost ledger

**Owner:** Muse Code. **Day:** D1. **Read:** AGENTS.md; CONTRACTS C1, C4, C6, C7; FACTS "Meta Model API" and the UNVERIFIED table.

## Goal

One interface to call Muse Spark (Meta), Muse Spark Open (Baseten, Fireworks) with text and images, where every call is cached and costed, plus `ask_json` that returns validated Pydantic objects. After this card, no other module talks to an SDK directly.

## Files

Create: `src/stackcheck/cache.py`, `costs.py`, `llm/base.py`, `llm/meta.py`, `llm/openai_compat.py`, `llm/structured.py`, `llm/registry.py`, `extract/prompts.py` (prompt loader), `scripts/smoke_meta.py`, `scripts/smoke_open.py`, tests `tests/unit/test_cache.py`, `test_costs.py`, `test_llm_meta.py`, `test_llm_openai_compat.py`, `test_structured.py`, `test_registry.py`, `test_prompts.py`, and a `FakeResponsesSDK` / `FakeChatSDK` in `tests/fakes.py`.

## Steps

1. `cache.py` per C6. Atomic writes. `get_or_compute` returns `(value, hit)`.
2. `costs.py` per C7. Price table as a dict keyed by `"provider/model"`; unknown model raises `ConfigError` (better than silently costing zero). `record` returns the USD cost (`Decimal`, quantised to 6 places). `check_budget(settings)` raises `BudgetExceeded`; call it before every paid call. Alert log once.
3. `llm/base.py`: `ImagePart`, `ModelReply`, `ModelClient` Protocol, `to_data_url(img) -> str`.
4. `llm/meta.py`: `MetaClient.complete(...)`:
   - cache key parts per C6 (`system_sha` and `text_sha` are SHA-256 hex of the strings, image shas of the bytes, `params` = `{"reasoning_effort": settings.reasoning_effort}`);
   - on miss: `check_budget`, call `self.sdk.responses.create(...)` exactly as C4, build `ModelReply` (cached tokens from `usage.input_tokens_details.cached_tokens`, `0` if missing), store `reply.model_dump(mode="json")`;
   - always `record(...)` (with `cache_hit=True` and zero tokens on a hit);
   - SDK errors after retries become `ModelError` with the provider and model in the message.
5. `llm/openai_compat.py`: same shape for Chat Completions per C4.
6. `extract/prompts.py`: `load_prompt(name: str) -> Prompt` where `Prompt` has `version`, `system`, `user_template` and a `fill(**values) -> str` method that follows the C5 filling rules (plain replacement of `{key}`, never `str.format`). File format: first line `version: <v>`, a line `SYSTEM:`, the system text, a line `USER:`, the user template. Use `importlib.resources` to read from `stackcheck/extract/prompts/`. Copy the four prompt files from C5 into that folder now, exactly as written (only the text inside the code fences).
7. `llm/structured.py`: `ask_json` per C4, including the repair call and `SchemaError`.
8. `llm/registry.py`: `reader_client(settings, origin)`, `vision_client(settings, origin)` per C4. Missing key for the chosen provider: `ConfigError("Set BASETEN_API_KEY in .env")` style messages.
9. `scripts/smoke_meta.py` (live, refuses to run unless `STACKCHECK_LIVE=1`):
   - call `muse-spark-1.3-contributor` with system "Reply with one word." and text "Say OK.";
   - first with `reasoning={"effort": "low"}`; if that raises a 400, retry without it and print `REASONING_PARAM: rejected (set STACKCHECK_REASONING_EFFORT="")`; else print `REASONING_PARAM: accepted`;
   - if a 400 error mentions `instructions`, print `INSTRUCTIONS_PARAM: rejected` and retry with the system text as the first `input` item (`{"role": "developer", "content": system}`); else print `INSTRUCTIONS_PARAM: accepted`. If rejected, the agent switches `meta.py` to the developer-message form and records it in FACTS;
   - print the reply text, `usage` field names present, token counts, the ledger cost;
   - then one image call: a 64×64 PNG generated in memory (half black, half white) with text "Which half is white, left or right? One word." Print the answer.
   - Never print the key.
10. `scripts/smoke_open.py` (live): same text call against Baseten and Fireworks if their keys are set; print model id, reply, usage, latency. Print `SKIPPED` for a provider without a key.

## Tests first

- `test_cache.py`: same parts give the same key regardless of dict order; miss then hit; compute isn't called on a hit; the file is valid JSON with `key_parts`; a leftover `.tmp` file doesn't break reads.
- `test_costs.py`: 1,000,000 uncached input tokens on contributor = `0.10`; 1,000,000 cached = `0.002`; 1,000,000 output on standard = `4.25`; SAM image units 4 = `0.01`; SAM video 10 frames bills 50 frames = `0.01`; cache hit costs 0; unknown model raises `ConfigError`; `check_budget` raises at the limit; ledger has one JSON line per record.
- `test_llm_meta.py` (with `FakeResponsesSDK` capturing kwargs and returning an object with `output_text` and `usage`): request shape matches C4 (instructions, input roles and part types, data URL prefix); reasoning omitted when `reasoning_effort=""`; second identical call is a cache hit and doesn't call the SDK; missing `input_tokens_details` gives `cached_input_tokens == 0`; SDK exception becomes `ModelError`.
- `test_llm_openai_compat.py`: message shape matches C4; usage mapped; cache hit.
- `test_structured.py`: plain JSON parses; JSON in a ```json fence parses; JSON with leading prose parses; invalid first answer then valid repair returns the model and 2 replies; two invalid answers raise `SchemaError`; the repair text contains the validation error.
- `test_registry.py`: provider `baseten` + origin `url` gives `OpenAICompatClient` with the Baseten model; provider `baseten` + origin `upload` gives `MetaClient` with `private_model`; vision client is always Meta; missing key raises `ConfigError` naming the env var.
- `test_prompts.py`: all four prompt files load; version strings are `filing_v1`, `photo_v1`, `investigate_v1`, `summary_v1`; templates contain their placeholders; filling `investigate_v1` with all its keys works and keeps the literal `{"action": ...}` text intact; filling with an unknown key raises; leaving a listed placeholder unfilled raises.

## Done when

Tests green, ruff clean. Human runs `smoke_meta.py` and `smoke_open.py` with `STACKCHECK_LIVE=1` and pastes the outputs; the agent records the reasoning-parameter result and the usage fields in `docs/FACTS.md` (move the lines from UNVERIFIED to Verified with today's date) and sets the default for `reasoning_effort` accordingly.

## How an expert thinks about it

Every later bug report will start with "the model said something weird". The cache turns that into a file you can open: the exact request and the exact answer. The ledger turns "how much did that cost?" into one line of arithmetic. Building both before any real call means no money is spent twice and no cost is a mystery. Fakes at the SDK boundary keep tests fast and free, and the smoke scripts are the single place where reality is checked.

## Traps

- The OpenAI SDK reads `OPENAI_API_KEY` by default. Always pass `api_key` explicitly.
- `responses.create` returns an object; `output_text` is a convenience property. Don't parse `output` items by hand.
- Don't cache failed calls.
- Don't put the API key, the prompt text or the image bytes in the ledger.
- `Decimal` from a float carries float noise: build prices from strings.
