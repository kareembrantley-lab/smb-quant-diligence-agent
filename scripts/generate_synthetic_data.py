"""
Generates three synthetic, multi-year SMB financial datasets used to demo
the diligence agent. Every dollar figure below is DERIVED from named
assumptions (growth rates, day-count metrics, percent-of-revenue ratios) in
this script rather than hand-typed, specifically so the numbers reconcile
and so the anomaly thresholds in src/smb_diligence/anomalies.py trip (or
don't) for a legible, checkable reason.

  1. clean_co     -- Cascade Valley Wholesale Foods (wholesale distribution)
                      Reconciles cleanly. Expected overall rating: GREEN.
  2. qoe_issue_co  -- Northgate Precision Components (light manufacturing)
                      A subtle earnings-quality issue: owner comp well above
                      market (caught as a "requires verification" addback,
                      not a hard rule) plus a quietly-improving gross margin
                      and a concentrated customer base. Expected: YELLOW.
  3. fraud_co      -- Summit Retail Supply Co (e-commerce / retail)
                      An obvious fraud pattern: receivables far outpacing
                      revenue, a COGS-driven margin jump right before the
                      transaction year, escalating undisclosed related-party
                      fees, payables stretched at year-end, a large owner
                      distribution the same year, and inventory that doesn't
                      reconcile with sales. Expected overall rating: RED.

No client or employer data of any kind is used anywhere in this repository.
Every number below is fabricated for this demo. Company names, addresses,
and customers are fictional.

Each company is exported in a DIFFERENT source format on purpose, to
exercise all three ingestion paths the agent supports:
  - clean_co     -> single Excel workbook (typical QuickBooks/Xero-style export)
  - qoe_issue_co -> three separate CSVs (one per statement)
  - fraud_co     -> two CPA-style PDF statements + one CSV (customer detail)

Run: python scripts/generate_synthetic_data.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "synthetic"
YEARS = [2022, 2023, 2024]


def _r(x: float) -> float:
    """Round to the nearest dollar for clean display."""
    return round(x)


def _days_to_balance(days_by_year: list[float], base_by_year: list[float]) -> list[float]:
    """Convert a days-outstanding assumption (DSO/DIO/DPO) into a period-end
    balance: balance = days / 365 * annual base (revenue or COGS)."""
    return [_r(d / 365 * b) for d, b in zip(days_by_year, base_by_year)]


def _pct(rate: float, base_by_year: list[float]) -> list[float]:
    return [_r(rate * b) for b in base_by_year]


def _by_year(values: list[float]) -> dict[int, float]:
    return dict(zip(YEARS, values))


# ---------------------------------------------------------------------------
# 1. CLEAN CO -- Cascade Valley Wholesale Foods (wholesale distribution)
# ---------------------------------------------------------------------------
def clean_co_data():
    revenue = [8_200_000, 8_850_000, 9_600_000]
    cogs_pct = [0.760, 0.758, 0.756]
    cogs = [_r(r * p) for r, p in zip(revenue, cogs_pct)]

    salaries = _pct(0.090, revenue)
    owner_comp = [150_000, 155_000, 160_000]
    rent = _pct(0.015, revenue)
    marketing = _pct(0.005, revenue)
    prof_fees = _pct(0.004, revenue)
    other_opex = _pct(0.030, revenue)
    d_and_a = [98_000, 106_000, 115_000]
    interest = [52_000, 44_000, 36_000]
    other_income = [9_000, 11_000, 12_000]
    tax = _pct(0.001, revenue)

    income = {
        "Net Sales": _by_year(revenue),
        "Cost of Goods Sold": _by_year(cogs),
        "Salaries and Wages": _by_year(salaries),
        "Officer Compensation": _by_year(owner_comp),
        "Rent Expense": _by_year(rent),
        "Marketing and Advertising": _by_year(marketing),
        "Professional Fees": _by_year(prof_fees),
        "General and Administrative": _by_year(other_opex),
        "Depreciation and Amortization": _by_year(d_and_a),
        "Interest Expense": _by_year(interest),
        "Other Income": _by_year(other_income),
        "State Franchise Tax": _by_year(tax),
    }

    dso = [35, 34, 34]
    dio = [58, 57, 57]
    dpo = [32, 32, 32]
    ar = _days_to_balance(dso, revenue)
    inventory = _days_to_balance(dio, cogs)
    ap = _days_to_balance(dpo, cogs)

    cash = [410_000, 468_000, 541_000]
    other_ca = _pct(0.0045, revenue)
    ppe = [890_000, 918_000, 946_000]
    other_lta = [18_000, 18_000, 18_000]
    accrued = _pct(0.0110, revenue)
    other_cl = _pct(0.0025, revenue)
    current_ltd = [110_000, 115_000, 120_000]
    long_term_debt = [612_000, 497_000, 377_000]
    distributions = [210_000, 235_000, 255_000]

    total_assets = [c + a + i + o + p + l for c, a, i, o, p, l in zip(cash, ar, inventory, other_ca, ppe, other_lta)]
    total_liabs = [ap_ + ac + ocl + cltd + ltd for ap_, ac, ocl, cltd, ltd in
                   zip(ap, accrued, other_cl, current_ltd, long_term_debt)]
    equity = [_r(a - l) for a, l in zip(total_assets, total_liabs)]

    balance = {
        "Cash and Cash Equivalents": _by_year(cash),
        "Trade Receivables": _by_year(ar),
        "Merchandise Inventory": _by_year(inventory),
        "Prepaid Expenses": _by_year(other_ca),
        "Fixed Assets Net": _by_year(ppe),
        "Deposits": _by_year(other_lta),
        "Accounts Payable": _by_year(ap),
        "Accrued Expenses": _by_year(accrued),
        "Customer Deposits": _by_year(other_cl),
        "Current Portion of Long-Term Debt": _by_year(current_ltd),
        "Term Loan Payable": _by_year(long_term_debt),
        "Owner Distributions": _by_year(distributions),
        "Owner's Equity": _by_year(equity),
    }

    shares = {
        "Riverside Grocers Inc": 0.150,
        "Pacific Corner Markets": 0.110,
        "Evergreen Bistro Group": 0.090,
        "Northshore Food Co-op": 0.070,
        "Cedar & Vine Restaurants": 0.060,
    }
    customers = {name: _by_year([_r(s * r) for r in revenue]) for name, s in shares.items()}
    other_share = 1 - sum(shares.values())
    customers["All Other Customers (41)"] = _by_year([_r(other_share * r) for r in revenue])

    return income, balance, customers


# ---------------------------------------------------------------------------
# 2. QOE ISSUE CO -- Northgate Precision Components (light manufacturing)
# ---------------------------------------------------------------------------
def qoe_issue_co_data():
    revenue = [5_400_000, 5_900_000, 6_450_000]
    cogs_pct = [0.670, 0.668, 0.600]  # FY23->FY24: 6.8-point drop -> yellow expense-timing flag
    cogs = [_r(r * p) for r, p in zip(revenue, cogs_pct)]

    salaries = _pct(0.170, revenue)
    owner_comp = [180_000, 260_000, 340_000]  # FY24 is $220k above the $120k market assumption
    rent = _pct(0.018, revenue)
    marketing = _pct(0.010, revenue)
    prof_fees = _pct(0.005, revenue)
    other_opex = _pct(0.030, revenue)
    contract_labor = _pct(0.0055, revenue)  # deliberately not in the alias seed -> exercises mapping review
    one_time = [0, 180_000, 0]  # legitimate litigation settlement, correctly added back at high confidence
    d_and_a = [81_000, 88_000, 97_000]
    interest = [38_000, 33_000, 27_000]
    other_income = [3_000, 4_000, 5_000]
    tax = _pct(0.001, revenue)

    income = {
        "Total Income": _by_year(revenue),
        "Cost of Revenue": _by_year(cogs),
        "Payroll Expense": _by_year(salaries),
        "Owner's Compensation": _by_year(owner_comp),
        "Lease Expense": _by_year(rent),
        "Sales and Marketing": _by_year(marketing),
        "Legal and Accounting": _by_year(prof_fees),
        "Litigation Settlement": _by_year(one_time),
        "Office Expense": _by_year(other_opex),
        "Contract Labor": _by_year(contract_labor),
        "Depreciation Expense": _by_year(d_and_a),
        "Interest Exp": _by_year(interest),
        "Interest Income": _by_year(other_income),
        "Provision for Income Taxes": _by_year(tax),
    }

    dso = [38, 39, 41]
    dio = [80, 79, 81]
    dpo = [38, 40, 42]
    ar = _days_to_balance(dso, revenue)
    inventory = _days_to_balance(dio, cogs)
    ap = _days_to_balance(dpo, cogs)

    cash = [295_000, 338_000, 401_000]
    other_ca = _pct(0.0040, revenue)
    ppe = [1_140_000, 1_205_000, 1_268_000]
    other_lta = [64_000, 58_000, 52_000]
    accrued = _pct(0.0130, revenue)
    other_cl = _pct(0.0025, revenue)
    current_ltd = [95_000, 98_000, 102_000]
    long_term_debt = [540_000, 442_000, 340_000]
    distributions = [140_000, 165_000, 205_000]  # kept below the Adjusted-EBITDA distribution threshold

    total_assets = [c + a + i + o + p + l for c, a, i, o, p, l in zip(cash, ar, inventory, other_ca, ppe, other_lta)]
    total_liabs = [ap_ + ac + ocl + cltd + ltd for ap_, ac, ocl, cltd, ltd in
                   zip(ap, accrued, other_cl, current_ltd, long_term_debt)]
    equity = [_r(a - l) for a, l in zip(total_assets, total_liabs)]

    balance = {
        "Cash in Bank": _by_year(cash),
        "A/R": _by_year(ar),
        "Finished Goods Inventory": _by_year(inventory),
        "Prepaid Insurance": _by_year(other_ca),
        "Equipment Net of Depreciation": _by_year(ppe),
        "Intangible Assets": _by_year(other_lta),
        "A/P": _by_year(ap),
        "Accrued Payroll": _by_year(accrued),
        "Sales Tax Payable": _by_year(other_cl),
        "Current Maturities of Long-Term Debt": _by_year(current_ltd),
        "Notes Payable Long Term": _by_year(long_term_debt),
        "Member Distributions": _by_year(distributions),
        "Member Equity": _by_year(equity),
    }

    shares = {
        "Alpine Fabrication Systems": [0.240, 0.260, 0.280],  # climbing toward the concentration flag
        "Redwood Industrial Supply": [0.170, 0.166, 0.161],
        "Kestrel Aerostructures": [0.140, 0.135, 0.130],
    }
    customers = {
        name: _by_year([_r(s * r) for s, r in zip(share_by_year, revenue)])
        for name, share_by_year in shares.items()
    }
    other_share = [1 - sum(s[i] for s in shares.values()) for i in range(3)]
    customers["All Other Customers (17)"] = _by_year([_r(s * r) for s, r in zip(other_share, revenue)])

    return income, balance, customers


# ---------------------------------------------------------------------------
# 3. FRAUD CO -- Summit Retail Supply Co (e-commerce / retail)
# ---------------------------------------------------------------------------
def fraud_co_data():
    revenue = [4_100_000, 4_520_000, 4_950_000]
    cogs_pct = [0.630, 0.620, 0.520]  # FY23->FY24: 10-point drop -> red expense-timing flag
    cogs = [_r(r * p) for r, p in zip(revenue, cogs_pct)]

    salaries = _pct(0.120, revenue)
    owner_comp = [145_000, 150_000, 155_000]
    rent = _pct(0.021, revenue)
    marketing = _pct(0.024, revenue)
    prof_fees = _pct(0.005, revenue)
    related_party = [40_000, 130_000, 210_000]  # escalating, undisclosed affiliate fulfillment fee
    insurance = _pct(0.015, revenue)
    d_and_a = [34_000, 37_000, 41_000]
    interest = [29_000, 24_000, 19_000]
    other_income = [6_000, 7_000, 8_000]
    tax = _pct(0.001, revenue)

    income = {
        "Gross Sales": _by_year(revenue),
        "Cost of Sales": _by_year(cogs),
        "Wages": _by_year(salaries),
        "Owner Salary": _by_year(owner_comp),
        "Occupancy Expense": _by_year(rent),
        "Advertising": _by_year(marketing),
        "Consulting Fees": _by_year(prof_fees),
        "Affiliate Management Fee": _by_year(related_party),
        "Insurance Expense": _by_year(insurance),
        "Amortization Expense": _by_year(d_and_a),
        "Loan Interest": _by_year(interest),
        "Miscellaneous Income": _by_year(other_income),
        "Franchise Tax": _by_year(tax),
    }

    dso = [10, 18, 35]     # far outpaces revenue growth -> red revenue-recognition flag, both periods
    dio = [45, 50, 95]     # FY23->24 swing >60% -> red inventory-coherence flag
    dpo = [28, 30, 68]     # FY24 spike vs. prior average -> red working-capital-timing flag
    ar = _days_to_balance(dso, revenue)
    inventory = _days_to_balance(dio, cogs)
    ap = _days_to_balance(dpo, cogs)

    cash = [210_000, 198_000, 162_000]  # cash actually falls despite "record" reported earnings
    other_ca = _pct(0.0046, revenue)
    ppe = [318_000, 329_000, 341_000]
    other_lta = [0, 0, 0]
    accrued = _pct(0.0093, revenue)
    other_cl = _pct(0.0029, revenue)
    current_ltd = [41_000, 43_000, 45_000]
    long_term_debt = [248_000, 205_000, 160_000]

    # Owner distributions are set to a target multiple of a simple EBITDA proxy
    # (revenue - cogs - opex-ex-D&A + other income) so the FY24 draw safely
    # clears the >=100%-of-Adjusted-EBITDA red threshold once the real
    # pipeline computes it (Adjusted EBITDA >= this proxy after addbacks).
    opex_ex_dep = [s + o + r + m + p + rp + ins for s, o, r, m, p, rp, ins in
                   zip(salaries, owner_comp, rent, marketing, prof_fees, related_party, insurance)]
    ebitda_proxy = [rev - c - opex + oi for rev, c, opex, oi in zip(revenue, cogs, opex_ex_dep, other_income)]
    distributions = [0, 0, _r(1.35 * ebitda_proxy[2])]

    total_assets = [c + a + i + o + p + l for c, a, i, o, p, l in zip(cash, ar, inventory, other_ca, ppe, other_lta)]
    total_liabs = [ap_ + ac + ocl + cltd + ltd for ap_, ac, ocl, cltd, ltd in
                   zip(ap, accrued, other_cl, current_ltd, long_term_debt)]
    equity = [_r(a - l) for a, l in zip(total_assets, total_liabs)]

    balance = {
        "Cash": _by_year(cash),
        "Trade Receivables": _by_year(ar),
        "Inventory - Net": _by_year(inventory),
        "Other Current Assets": _by_year(other_ca),
        "Fixed Assets Net": _by_year(ppe),
        "Goodwill": _by_year(other_lta),
        "Trade Payables": _by_year(ap),
        "Accrued Expenses": _by_year(accrued),
        "Deferred Revenue": _by_year(other_cl),
        "Current Portion of Long-Term Debt": _by_year(current_ltd),
        "SBA Loan Payable": _by_year(long_term_debt),
        "Shareholder Distributions": _by_year(distributions),
        "Shareholder Equity": _by_year(equity),
    }

    shares = {
        "MetroCart Marketplace": [0.400, 0.440, 0.480],  # climbing well past the concentration threshold
        "Bright Aisle Retail Group": [0.180, 0.175, 0.170],
        "Trailhead Outfitters": [0.110, 0.106, 0.100],
    }
    customers = {
        name: _by_year([_r(s * r) for s, r in zip(share_by_year, revenue)])
        for name, share_by_year in shares.items()
    }
    other_share = [1 - sum(s[i] for s in shares.values()) for i in range(3)]
    customers["All Other Customers (9)"] = _by_year([_r(s * r) for s, r in zip(other_share, revenue)])

    return income, balance, customers


def _flat(values_by_year: dict[str, dict[int, float]]) -> pd.DataFrame:
    rows = []
    for label, by_year in values_by_year.items():
        row = {"Line Item": label}
        for year in YEARS:
            row[f"FY{year}"] = by_year.get(year, 0)
        rows.append(row)
    return pd.DataFrame(rows)


def _write_xlsx(path: Path, income: dict, balance: dict, customers: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        _flat(income).to_excel(writer, sheet_name="Income Statement", index=False)
        _flat(balance).to_excel(writer, sheet_name="Balance Sheet", index=False)
        _flat(customers).to_excel(writer, sheet_name="Customer Detail", index=False)


def _write_csvs(out_dir: Path, income: dict, balance: dict, customers: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _flat(income).to_csv(out_dir / "income_statement.csv", index=False)
    _flat(balance).to_csv(out_dir / "balance_sheet.csv", index=False)
    _flat(customers).to_csv(out_dir / "customer_detail.csv", index=False)


def _write_pdf_statement(path: Path, title: str, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=letter, topMargin=0.6 * inch, bottomMargin=0.6 * inch)
    story = [Paragraph(title, styles["Title"]), Spacer(1, 12)]

    data = [list(df.columns)]
    for _, row in df.iterrows():
        formatted = [str(row["Line Item"])]
        for col in df.columns[1:]:
            val = row[col]
            formatted.append(f"{val:,.0f}" if isinstance(val, (int, float)) else str(val))
        data.append(formatted)

    table = Table(data, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1B2A4A")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F6FA")]),
    ]))
    story.append(table)
    doc.build(story)


def main() -> None:
    income, balance, customers = clean_co_data()
    _write_xlsx(DATA_DIR / "clean_co" / "cascade_valley_wholesale_foods.xlsx", income, balance, customers)

    income, balance, customers = qoe_issue_co_data()
    _write_csvs(DATA_DIR / "qoe_issue_co", income, balance, customers)

    income, balance, customers = fraud_co_data()
    _write_pdf_statement(
        DATA_DIR / "fraud_co" / "income_statement.pdf",
        "Summit Retail Supply Co -- Income Statement (CPA Compilation)",
        _flat(income),
    )
    _write_pdf_statement(
        DATA_DIR / "fraud_co" / "balance_sheet.pdf",
        "Summit Retail Supply Co -- Balance Sheet (CPA Compilation)",
        _flat(balance),
    )
    _flat(customers).to_csv(DATA_DIR / "fraud_co" / "customer_detail.csv", index=False)

    print("Synthetic datasets written to", DATA_DIR)


if __name__ == "__main__":
    main()
