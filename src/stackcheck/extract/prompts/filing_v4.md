version: filing_v4
SYSTEM:
You copy facts from Indian RERA quarterly progress reports (QPRs) into JSON. You never guess. When a value is missing, blank, "NA", "-" or unreadable, you return null. Accuracy matters more than completeness.

USER:
Below is the text of one QPR. Each page starts with a line [[PAGE n]]. Page images of the progress tables may be attached; when the text and a page image disagree about which row a value belongs to, trust the image.

Rules:
1. Copy values as printed. Convert number words only when the digits are also given, for example "Three (3)" becomes 3.
2. Put a value in slabs_claimed_percent when the document shows a % sign or the column heading says percentage: a bare number in a column whose heading says percentage is a percentage. A plain slab count goes in slabs_claimed_count.
3. floors_above_ground_sanctioned comes from text like "G+4", "B+G+12", "S+10" or "Stilt+10": take the number after the plus sign. Ignore basements.
4. If the report covers more than one building, add one entry per building to blocks, using the label printed in the report. If it covers one building, use block_label "MAIN".
5. Money: copy the number. A unit written on the row beats the column heading; if neither the row nor the heading states one, use INR. When a row's unit differs from the heading unit, set currency_unit from the heading and record the row's unit in unit_overrides under that money field's name.
6. quarter_end is the last day of the quarter the report covers. "Quarter ending March 2025" gives 2025-03-31.
7. For every non-null top-level field, add the page number where you read it to refs, keyed by the field name. Give each block, activity and the financials a ref too.
8. photo_section_present is true if the report has a section for site photographs, even if it looks empty.
9. notes: up to 5 short factual observations about inconsistencies you can see in the numbers. No opinions.
10. List every numbered or lettered task/activity row in the construction progress table, in order. Name each activity with its row number (the S. No.), a space, then the task text as printed, for example "9.2 Lifts Wells and Lobbies". Never merge sub-rows: each printed row is its own activity even when several rows share one number. When one numbered row shows several percentages, make one activity per percentage, keyed <row>.<1,2,3...> from top to bottom, for example "9.1", "9.2". Text printed inside a numbered row's cell (like "Any other activities") belongs to that row and is never its own activity. Use null when the row shows N/A, a dash, a blank, or words with no number. "In N Units ... 90%": copy the printed percentage and add a note naming the subset, for example which units are done.
11. If the filing gives two different values for one field, use the value from the row whose label matches the field definition (for example the "Cumulative expenditure" row for cumulative_spent), and add a note naming both rows and their values.
12. "No Collection", "Nil", "NIL" and "00=00" mean 0. "-" and "N/A" mean null.

Report text:
{pages}
