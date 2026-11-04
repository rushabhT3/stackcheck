# Labelling filing truth (human job)

`scripts/eval_extract.py --make-template <pdf path or doc_id>` extracts one filing
and writes `data/truth/<doc_id>.template.json`. Correct every field by hand against
the PDF, then rename the file to `data/truth/<doc_id>.json`.

## What to check

Copy values exactly as printed. Only these fields are scored:

- `project_reg_no`, `quarter_end`
- each block's `floors_above_ground_sanctioned`, `slabs_claimed_count`, `slabs_claimed_percent`
- every `financials` value and `currency_unit`
- `has_architect_certificate`, `has_engineer_certificate`, `has_ca_certificate`
- `photo_section_present`
- each activity's `percent_done` (matched by name after stripping extra spaces)

Truth `null` matched by extracted `null` counts as correct, so leave a field null
when the filing really does not state it. Never guess.

## Three rules for ambiguous filings

1. **Sub-rows.** Every printed row, numbered or lettered, is its own activity,
   named as printed. Never merge sub-rows, even when several rows share one number.
2. **Units.** A unit written on the row beats the column heading. If neither the
   row nor the heading states one, use INR.
3. **Conflicting values.** If the filing gives two different values for one field,
   use the value from the row whose label matches the field definition (for example
   the "Cumulative expenditure" row for `cumulative_spent`), and add a note naming
   both rows and their values. R7 will flag the contradiction downstream.

## v4 additions (filing_v4)

4. **Sub-rows are keyed by row number.** Name each activity with its S. No., a space,
   then the task text as printed ("9.2 Lifts Wells and Lobbies"). One numbered row
   with several percentages becomes one activity per percentage, keyed
   `<row>.<1,2,3...>` top to bottom. Text inside a numbered row's cell (like "Any
   other activities") belongs to that row, never its own activity. A bare number in
   a column whose heading says percentage is a percentage; null only for N/A, "-",
   blank, or words with no number.
5. **Units and zeroes.** A unit written on the row beats the column heading; if
   neither states one, use INR. When a row's unit differs from the heading, keep the
   heading unit in `currency_unit` and put the row's unit in `unit_overrides` under
   that money field's name. "No Collection", "Nil", "NIL" and "00=00" mean 0;
   "-" and "N/A" mean null.
6. **Subsets.** "In N Units ... 90%" keeps the printed percentage; add a note naming
   the subset.

## Privacy rule

A truth file must never contain names or registration numbers. The template already
sets `project_name`, `promoter_name` and `project_reg_no` to null: keep them null.
`project_reg_no` is scored only when it is present in truth, so a null there only
skips that one field. Keep any registration numbers in your private notes.
