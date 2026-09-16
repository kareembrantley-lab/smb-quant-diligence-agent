"""
Chart-of-accounts normalization.

A small business's export almost never uses the same labels as the business
next door -- "Officer Compensation," "Owner Salary," and "Guaranteed
Payments to Member" are the same line item wearing three different QuickBooks
templates. This module resolves every raw label onto the canonical schema in
three passes, cheapest first:

  1. Exact match against the seed alias list (schema.ALIAS_SEED).
  2. Fuzzy match (token-sort ratio) against the same alias list. Anything
     clearing FUZZY_CONFIDENCE_THRESHOLD is accepted automatically.
  3. LLM-assisted match for whatever is left, if ANTHROPIC_API_KEY is set.
     Anything the LLM can't place, or that arrives with no LLM configured,
     is left unmapped and routed to the human-reviewable mapping table with
     ``needs_review = True``.

The output mapping table is written to disk as CSV specifically so a human
(the analyst, or the business owner) can correct it before the ratios are
computed on top of it -- this is the auditability seam the build spec asks
for. Nothing downstream trusts a mapping the table doesn't contain.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz, process

from .llm import classify_line_item, llm_available
from .schema import ALIAS_SEED, LineItem, Statement

FUZZY_CONFIDENCE_THRESHOLD = 87  # 0-100 rapidfuzz score


def _alias_index(statement: Statement) -> dict[str, LineItem]:
    index = {}
    for entry in ALIAS_SEED:
        if entry.statement != statement:
            continue
        for alias in entry.aliases:
            index[alias] = entry.item
    return index


def _candidates_for(statement: Statement) -> list[str]:
    return sorted({entry.item.value for entry in ALIAS_SEED if entry.statement == statement})


def build_mapping_table(raw: pd.DataFrame) -> pd.DataFrame:
    """Map every distinct (statement, raw_label) pair onto the canonical schema.

    Returns one row per distinct raw label with: statement, raw_label,
    canonical_item, confidence (0-100), method (exact/fuzzy/llm/unmapped),
    rationale, needs_review.
    """
    results = []
    for statement_value, group in raw.groupby("statement"):
        try:
            statement = Statement(statement_value)
        except ValueError:
            continue
        if statement == Statement.CUSTOMER_DETAIL:
            # Customer names are not chart-of-accounts labels -- pass through.
            for label in sorted(group["raw_label"].unique()):
                results.append({
                    "statement": statement.value,
                    "raw_label": label,
                    "canonical_item": label,
                    "confidence": 100,
                    "method": "passthrough",
                    "rationale": "Customer identifier, not a line item.",
                    "needs_review": False,
                })
            continue

        alias_index = _alias_index(statement)
        candidates = _candidates_for(statement)
        alias_labels = list(alias_index.keys())

        for label in sorted(group["raw_label"].unique()):
            lowered = label.strip().lower()

            # Pass 1: exact match.
            if lowered in alias_index:
                results.append({
                    "statement": statement.value,
                    "raw_label": label,
                    "canonical_item": alias_index[lowered].value,
                    "confidence": 100,
                    "method": "exact",
                    "rationale": "Exact alias match.",
                    "needs_review": False,
                })
                continue

            # Pass 2: fuzzy match.
            match = process.extractOne(lowered, alias_labels, scorer=fuzz.token_sort_ratio)
            if match and match[1] >= FUZZY_CONFIDENCE_THRESHOLD:
                matched_alias, score, _ = match
                results.append({
                    "statement": statement.value,
                    "raw_label": label,
                    "canonical_item": alias_index[matched_alias].value,
                    "confidence": round(score, 1),
                    "method": "fuzzy",
                    "rationale": f"Fuzzy match to alias '{matched_alias}' ({score:.0f}/100).",
                    "needs_review": score < 95,
                })
                continue

            # Pass 3: LLM assist.
            if llm_available():
                item, rationale = classify_line_item(label, statement.value, candidates)
                if item:
                    results.append({
                        "statement": statement.value,
                        "raw_label": label,
                        "canonical_item": item,
                        "confidence": 75.0,
                        "method": "llm",
                        "rationale": rationale or "LLM-assisted classification.",
                        "needs_review": True,
                    })
                    continue

            # Unresolved -- excluded from the financials until a human fills
            # in canonical_item in the CSV. We still surface the closest
            # fuzzy guess in the rationale so the review is a five-second
            # confirm/correct, not a cold start.
            best_guess = match[0] if match else None
            suggestion = f" Closest guess: '{best_guess}' ({match[1]:.0f}/100)." if best_guess else ""
            results.append({
                "statement": statement.value,
                "raw_label": label,
                "canonical_item": None,
                "confidence": round(match[1], 1) if match else 0.0,
                "method": "unmapped",
                "rationale": "No confident match; excluded pending manual mapping." + suggestion,
                "needs_review": True,
            })

    return pd.DataFrame(results)


def write_mapping_table(mapping: pd.DataFrame, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mapping.to_csv(out_path, index=False)
    return out_path


def apply_mapping(raw: pd.DataFrame, mapping: pd.DataFrame) -> pd.DataFrame:
    """Join raw line items to their (possibly human-corrected) canonical items.

    Rows that are still unmapped after human review are dropped from the
    financial dataset but retained in the mapping table itself, so the memo
    can disclose exactly what was excluded and why.
    """
    key_cols = ["statement", "raw_label"]
    merged = raw.merge(
        mapping[key_cols + ["canonical_item", "needs_review"]],
        on=key_cols,
        how="left",
    )
    unresolved = merged[merged["canonical_item"].isna()]
    resolved = merged.dropna(subset=["canonical_item"]).copy()
    resolved = (
        resolved.groupby(["statement", "canonical_item", "fiscal_year"], as_index=False)["amount"]
        .sum()
    )
    return resolved, unresolved
