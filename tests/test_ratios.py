"""
Unit tests for the ratio engine, the QoE addback logic, the chart-of-accounts
mapper, and the customer-concentration aggregate-bucket fix. These are the
places where a silent arithmetic or logic bug would quietly produce a wrong
number in a memo someone might actually rely on -- worth pinning down.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smb_diligence.coa_mapper import apply_mapping, build_mapping_table
from smb_diligence.ratios import Financials, compute_year, customer_concentration
from smb_diligence.schema import LineItem, Statement


def _resolved_frame(rows: list[tuple[LineItem, int, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"statement": Statement.INCOME_STATEMENT.value if item in
          {LineItem.REVENUE, LineItem.COGS, LineItem.SALARIES_WAGES, LineItem.OWNER_OFFICER_COMP,
           LineItem.DEPRECIATION_AMORTIZATION, LineItem.INTEREST_EXPENSE, LineItem.OTHER_INCOME,
           LineItem.INCOME_TAX_EXPENSE, LineItem.ONE_TIME_EXPENSE, LineItem.RELATED_PARTY_EXPENSE,
           LineItem.RENT_OCCUPANCY, LineItem.MARKETING_ADVERTISING, LineItem.PROFESSIONAL_FEES,
           LineItem.OTHER_OPEX}
          else Statement.BALANCE_SHEET.value,
          "canonical_item": item.value, "fiscal_year": year, "amount": amount}
         for item, year, amount in rows]
    )


def test_gross_margin_and_ebitda_basic():
    rows = [
        (LineItem.REVENUE, 2024, 1_000_000),
        (LineItem.COGS, 2024, 600_000),
        (LineItem.SALARIES_WAGES, 2024, 200_000),
        (LineItem.DEPRECIATION_AMORTIZATION, 2024, 20_000),
        (LineItem.INTEREST_EXPENSE, 2024, 10_000),
        (LineItem.INCOME_TAX_EXPENSE, 2024, 5_000),
    ]
    fin = Financials(_resolved_frame(rows))
    result = compute_year(fin, 2024, owner_market_comp=120_000)

    assert result.gross_profit == 400_000
    assert result.gross_margin == pytest.approx(0.4)
    # EBITDA = revenue - cogs - opex(ex D&A) + other_income = 1,000,000 - 600,000 - 200,000 = 200,000
    assert result.ebitda == pytest.approx(200_000)
    # Sanity check: EBITDA should equal net_income + interest + tax + D&A
    assert result.ebitda == pytest.approx(result.net_income + 10_000 + 5_000 + 20_000)


def test_qoe_addback_one_time_expense_added_back_in_full():
    rows = [
        (LineItem.REVENUE, 2024, 1_000_000),
        (LineItem.COGS, 2024, 600_000),
        (LineItem.OWNER_OFFICER_COMP, 2024, 120_000),  # == market rate -> no owner-comp addback noise
        (LineItem.ONE_TIME_EXPENSE, 2024, 50_000),
    ]
    fin = Financials(_resolved_frame(rows))
    result = compute_year(fin, 2024)
    assert result.adjusted_ebitda == pytest.approx(result.ebitda + 50_000)
    assert any("one-time" in a.label.lower() for a in result.addbacks)
    assert all(a.confidence == "high" for a in result.addbacks if "one-time" in a.label.lower())


def test_qoe_addback_owner_comp_above_market_flagged_for_verification():
    rows = [
        (LineItem.REVENUE, 2024, 1_000_000),
        (LineItem.COGS, 2024, 600_000),
        (LineItem.OWNER_OFFICER_COMP, 2024, 300_000),
    ]
    fin = Financials(_resolved_frame(rows))
    result = compute_year(fin, 2024, owner_market_comp=120_000)
    comp_addback = next(a for a in result.addbacks if "above market" in a.label)
    assert comp_addback.amount == pytest.approx(180_000)
    assert comp_addback.confidence == "requires verification"


def test_missing_owner_comp_line_does_not_synthesize_an_addback():
    """If the source statements never report an owner-compensation line at
    all (common for a pure pass-through/all-distribution structure), the
    engine must not silently assume a $0 salary and back into a phantom
    'underpaid owner' addback -- see Financials.has_item."""
    rows = [
        (LineItem.REVENUE, 2024, 1_000_000),
        (LineItem.COGS, 2024, 600_000),
    ]
    fin = Financials(_resolved_frame(rows))
    result = compute_year(fin, 2024, owner_market_comp=120_000)
    assert not any("compensation" in a.label.lower() for a in result.addbacks)
    assert result.adjusted_ebitda == pytest.approx(result.ebitda)


def test_related_party_expense_is_never_auto_added_back():
    rows = [
        (LineItem.REVENUE, 2024, 1_000_000),
        (LineItem.COGS, 2024, 600_000),
        (LineItem.RELATED_PARTY_EXPENSE, 2024, 40_000),
    ]
    fin = Financials(_resolved_frame(rows))
    result = compute_year(fin, 2024)
    assert not any("related" in a.label.lower() for a in result.addbacks)
    # It should, however, still reduce EBITDA like any other operating expense.
    assert result.ebitda == pytest.approx(1_000_000 - 600_000 - 40_000)


def test_working_capital_and_days_metrics():
    rows = [
        (LineItem.REVENUE, 2024, 730_000),   # 730,000 / 365 = 2,000/day, easy DSO math
        (LineItem.COGS, 2024, 365_000),      # 365,000 / 365 = 1,000/day, easy DIO/DPO math
        (LineItem.CASH, 2024, 50_000),
        (LineItem.ACCOUNTS_RECEIVABLE, 2024, 70_000),   # -> DSO = 35 days
        (LineItem.INVENTORY, 2024, 60_000),              # -> DIO = 60 days
        (LineItem.ACCOUNTS_PAYABLE, 2024, 40_000),        # -> DPO = 40 days
    ]
    fin = Financials(_resolved_frame(rows))
    result = compute_year(fin, 2024)
    assert result.dso == pytest.approx(35.0, abs=0.1)
    assert result.dio == pytest.approx(60.0, abs=0.1)
    assert result.dpo == pytest.approx(40.0, abs=0.1)
    assert result.current_assets == pytest.approx(180_000)
    assert result.working_capital == pytest.approx(180_000 - 40_000)


def test_customer_concentration_excludes_aggregate_bucket_from_ranking():
    """Regression test: an 'All Other Customers' remainder bucket must count
    toward the revenue total but must NOT be ranked as though it were one
    giant named customer -- this was a real bug caught while building the
    synthetic demo data (see ratios.py customer_concentration docstring)."""
    df = pd.DataFrame([
        {"canonical_item": "Big Client Co", "fiscal_year": 2024, "amount": 300_000},
        {"canonical_item": "Mid Client Co", "fiscal_year": 2024, "amount": 150_000},
        {"canonical_item": "All Other Customers (30)", "fiscal_year": 2024, "amount": 550_000},
    ])
    result = customer_concentration(df)
    row = result.iloc[0]
    assert row["top1_pct"] == pytest.approx(30.0)  # Big Client Co / 1,000,000, NOT the 55% bucket
    assert row["customer_count"] == 2


def test_coa_mapper_exact_and_fuzzy_and_unmapped():
    raw = pd.DataFrame([
        {"statement": "income_statement", "raw_label": "Net Sales", "fiscal_year": 2024, "amount": 1_000_000},
        {"statement": "income_statement", "raw_label": "Cost of Revenue", "fiscal_year": 2024, "amount": 600_000},
        {"statement": "income_statement", "raw_label": "Bigfoot Sighting Fees", "fiscal_year": 2024, "amount": 500},
    ])
    mapping = build_mapping_table(raw)
    by_label = mapping.set_index("raw_label")

    assert by_label.loc["Net Sales", "method"] == "exact"
    assert by_label.loc["Net Sales", "canonical_item"] == "revenue"

    assert by_label.loc["Cost of Revenue", "method"] in ("exact", "fuzzy")
    assert by_label.loc["Cost of Revenue", "canonical_item"] == "cogs"

    assert by_label.loc["Bigfoot Sighting Fees", "method"] == "unmapped"
    assert pd.isna(by_label.loc["Bigfoot Sighting Fees", "canonical_item"])
    assert by_label.loc["Bigfoot Sighting Fees", "needs_review"] == True  # noqa: E712

    resolved, unresolved = apply_mapping(raw, mapping)
    assert "Bigfoot Sighting Fees" in unresolved["raw_label"].values
    assert resolved[resolved["canonical_item"] == "revenue"]["amount"].iloc[0] == 1_000_000
