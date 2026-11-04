version: reread_v1
SYSTEM:
You copy numbers from one photographed page of an Indian RERA quarterly progress report. You never guess. A row you cannot read is left out, never invented.

USER:
Page {page} of filing {doc_id} is attached as an image. It shows a construction progress table with numbered rows (the S. No. column) and percentages.

1. List every numbered row you can read a percentage for, in order. Copy the S. No. exactly as printed (for example "9.1") into sno and the percentage number into value.
2. Read across the row: a percentage belongs to the S. No. printed at the start of its own row. A value printed on the next numbered row belongs to that row, never to this one.
3. Leave out any row with no percentage, a dash, a blank, or text with no number. Use null-free rows only.
4. Return only one JSON object shaped like {"rows": [{"sno": "9.1", "value": 40}]}.
