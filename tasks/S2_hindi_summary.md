# S2 (stretch): Plain-language summary in Hindi

**Owner:** Muse Code. **Only after gate G6 passes.** **Read:** CONTRACTS C5 (`summary_v1`), C16 (summary in the report step).

## Goal

Most buyers in Patna or Hyderabad read Hindi more easily than an English report. Add a Hindi summary generated from the same findings.

## Steps

1. Setting `summary_languages: str = "en"` (comma separated) in C1 (record as decision D17); deploy sets `"en,hi"`.
2. In the report step, call `summary_v1` once per language; store in `summary_en` / `summary_hi`.
3. UI: a language toggle shown only when `summary_hi` exists.
4. Human check: read three Hindi summaries for meaning and tone (no accusation, ends with the disclaimer sentence).

## Done when

Three real reports show a Hindi summary that a Hindi reader confirms is correct and neutral.

## Traps

- The summary must use only the findings given. If the Hindi text adds a fact, the prompt needs a stricter rule, not a post-edit.
