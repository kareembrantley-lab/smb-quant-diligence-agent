# Architecture notes

This is the module-by-module detail behind the diagram in the [README](../README.md). Each section lists the data contract in and out, so you can unit-test or swap any one stage without reading the rest of the pipeline.

## 1. `ingest.py`

**In:** file paths (`.xlsx`, `.csv`, `.pdf`).
**Out:** a single "raw long" `DataFrame`:

| column | meaning |
|---|---|
| `source_file` | which file this row came from |
| `statement` | `income_statement`, `balance_sheet`, or `customer_detail` (inferred from the sheet/file name) |
| `raw_label` | the label exactly as it appeared in the source, e.g. `"Officer Compensation"` |
| `fiscal_year` | parsed from the column header, e.g. `"FY2024"` -> `2024` |
| `amount` | parsed float, handling `$`, commas, and accounting-style `(1,234)` negatives |

Nothing here interprets meaning. A label that isn't recognizable text, or a cell that isn't a parseable number, is dropped rather than guessed at.

## 2. `coa_mapper.py`

**In:** the raw long table.
**Out:** `mapping_table.csv` (one row per distinct `(statement, raw_label)` pair) and the resolved/unresolved split.

Three passes, cheapest first:

1. **Exact match** against `schema.ALIAS_SEED` — case-insensitive, no fuzziness. Confidence 100.
2. **Fuzzy match** (`rapidfuzz.fuzz.token_sort_ratio`) against the same alias list. Anything scoring `>= FUZZY_CONFIDENCE_THRESHOLD` (87) is accepted; anything scoring `< 95` is still flagged `needs_review = True` even though it was auto-applied, so a human can spot-check near-misses without them blocking the run.
3. **LLM-assisted** (only if `ANTHROPIC_API_KEY` is set): asks Claude to pick the best canonical item from the fixed candidate list for that statement type, or `null` if none fit. Always flagged `needs_review = True`.

Anything still unresolved after all three passes gets `canonical_item = None` in the mapping table and is **excluded** from `apply_mapping()`'s resolved output — the pipeline will not silently guess. The memo's Methodology section discloses exactly which raw labels were excluded this way.

## 3. `ratios.py`

**In:** the resolved (mapped) table, wrapped in a `Financials` convenience accessor.
**Out:** one `YearResult` per fiscal year, plus `customer_concentration()` for the top-1/top-3/HHI table.

Key design choices:

- **EBITDA** is derived as `net_income + interest + tax + D&A`, which by construction also equals `revenue - COGS - operating_expenses(excl. D&A) + other_income`. The test suite checks both formulations agree.
- **Adjusted EBITDA** only includes addbacks with a documented, itemized rationale: full one-time/non-recurring expenses (confidence: high) and the excess of owner compensation over an assumed market-replacement rate (confidence: requires verification, in either direction — an owner paid *below* market gets a negative addback, because a buyer will have to pay a real replacement more).
- **Related-party expense is never auto-added-back.** It's excluded from the QoE worksheet on purpose and routed to `anomalies.py` instead — see the README for why.
- **Operating working capital** (the M&A-standard NWC cut: AR + inventory + other current assets − AP − accrued − other current liabilities, excluding cash and debt) is reported alongside total working capital, since the two answer different questions.
- **`Financials.has_item()`** distinguishes "this line item is genuinely $0" from "this line item was never reported." The owner-compensation addback uses this to avoid manufacturing a phantom "underpaid owner" addback when a source simply doesn't report a compensation line at all (common for certain pass-through structures).

## 4. `benchmarks.py`

**In:** an industry key (must match a row in `data/reference/industry_benchmarks.csv`) and a metric value.
**Out:** a `BenchmarkComparison` with `position` = `below_range` / `within_range` / `above_range`, plus the source note.

The reference table is intentionally small (4 industries × 4 metrics) and every row carries a `source_note` disclosing it as directional, compiled from RMA/IBISWorld-style public category medians. It is not a substitute for a licensed benchmarking subscription — see "What I'd do with more time" in the README.

## 5. `anomalies.py`

**In:** the list of `YearResult`, a `{year: related_party_expense}` dict, a `{year: owner_distributions}` dict, and the customer concentration table.
**Out:** a list of `Flag` objects (`category`, `severity`, `title`, `evidence` dict, `narrative`).

Every threshold is a named module-level constant (`AR_GROWTH_GAP_YELLOW`, `COGS_PCT_DROP_RED`, etc.) specifically so the judgment calls are visible and arguable in one place, not buried inside a function body. Six checks run:

1. **Revenue recognition risk** — AR growth outpacing revenue growth by more than the threshold gap.
2. **Expense-timing shift** — COGS-as-%-of-revenue improving faster than the threshold, year over year.
3. **Related-party transaction** — any related-party expense above the threshold % of revenue.
4. **Working capital near the transaction date** — payables stretched (DPO spike) or an owner distribution spike, both measured in the *final* year of the dataset (treated as the transaction/diligence year).
5. **Inventory / COGS coherence** — a DIO swing too large to be explained by normal operations.
6. **Customer concentration** — top single customer above the threshold % of revenue (an "All Other Customers" aggregate bucket is deliberately excluded from this ranking — see the regression test in `tests/test_ratios.py`).

`resolve_narrative()` tries the LLM for a plain-English paragraph and falls back to the rule's own templated narrative if the LLM isn't configured or the call fails for any reason — the rules layer never depends on the network.

## 6. `memo.py`

**In:** everything above.
**Out:** a `.docx` at Minto Pyramid structure: governing thought first, MECE flag summary second (grouped, not narrated), full backup (QoE worksheet, anomaly detail, benchmark table, customer concentration, methodology disclosure, ratio appendix) after.

`pipeline.py` optionally shells out to headless LibreOffice (`soffice --headless --convert-to pdf`) to also produce a PDF; if LibreOffice isn't on `PATH`, this step is skipped and the `.docx` alone is still delivered.

## 7. `pipeline.py`

The orchestrator. `run_diligence(config, out_dir)` calls each stage above in order and returns a dict with every intermediate artifact (`mapping_path`, `years`, `flags`, `concentration`, `benchmark_comparisons`, plus the memo paths) so a caller — the CLI, a test, or a future API wrapper — can inspect any of it without re-parsing the memo.
