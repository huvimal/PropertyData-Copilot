# Initial local validation — 2026-11-13

30 automated tests passed using the compatible Huni Catalog AI Python 3.11 environment with
Streamlit 1.67, Pydantic 2.14 and Pillow 12.3. Setup instructions create a separate project environment.

Coverage includes price magnitude/basis, ambiguous separators, title/body conflict abstention,
missing-value handling, evidence checks, sale/rent isolation, derived totals, exact budget boundaries,
unknown-constraint exclusion, approved-only search, stale-write protection, review invalidation,
zero-bedroom export, persistence, image-hash collisions, mocked provider failures and UI filter confirmation.

| Authored fixture scope | Exact cases |
|---|---:|
| Four reference fields across 16 synthetic listings | 16/16 |
| Strict/inclusive budget, rent and per-m² query cases | 4/4 |

Listing reference fields are price_vnd, price_basis, area_m2 and bedrooms. These figures do not
measure accuracy across every field, live LLM extraction, natural-language interpretation in general,
real market data or duplicate-detection precision/recall. References were written alongside the data.
Query fixtures simulate review eligibility; no independent human-labeling study is claimed.

The dHash collision test intentionally demonstrates that solid white and solid black photos share
the same gradient hash. Photo similarity is a review candidate, not property identity evidence.
TF-IDF is lexical retrieval, not semantic embedding search. Human confirmation and hard constraints
are tested separately from provider adapters, which use mocks rather than live API calls.

Before stronger claims: obtain permitted independent listings, label units/missing facts and duplicate
groups, freeze prompts/rules, evaluate field errors and query constraints separately, and track manual
corrections. Do not infer valuation, legal verification, listing authenticity or business savings.
