# PropertyData Copilot — Case Study

## Problem
Property listings describe prices using different units and bases. Mixing total sale prices, prices per square metre, and monthly rents can produce misleading search results.

## Solution
A personal Python/Streamlit prototype that normalizes Vietnamese listing text into structured fields, preserves price bases, flags ambiguous values, and requires human review before search or export. Optional Groq text calls propose facts or query filters; the user confirms filters before deterministic constraints run.

SQLite stores source text, extracted facts, review revisions, and events. Changes revoke approval. Duplicate candidates use text similarity and optional local image hashes, with no automatic merging. TF-IDF ranks eligible results after hard filters. Reviewed records export to CSV and audit JSON in a ZIP.

## Validation
The saved local report `reports/my-evaluation-v1.json` matched reference price, price-basis, area and bedroom fields for 16 of 16 synthetic listing cases. All four query fixtures matched their expected result IDs, covering strict versus inclusive budgets, monthly rent, and derived sale totals. The project includes 30 automated tests; see VALIDATION.md for their scope.

The evaluator uses synthetic review eligibility, not independent human annotation. These results measure local rules and filter fixtures, not live AI extraction accuracy. No duplicate precision/recall study or real-client outcome is claimed.

## Design decisions
- Preserve explicit price units and require review of ambiguous or conflicting values.
- Apply confirmed hard constraints before lexical ranking.
- Revoke approval after edits or source changes; reject stale review revisions.
- Keep photos local; optional provider requests send selected text only.
- Escape spreadsheet formulas in CSV exports.

## Limits
Synthetic examples and a local single-user workflow. Local review aliases are not authentication. No scraping, live market feeds, official geocoding, valuation, ownership verification, or production deployment is included. Image hashes can collide, and duplicate suggestions require human judgment.

## Reproduce
Install requirements, run `python -m unittest discover -s tests -v`, then `python evaluate.py --output reports/my-evaluation-v1.json`. Follow HUONG_DAN.md for interactive review and search.
