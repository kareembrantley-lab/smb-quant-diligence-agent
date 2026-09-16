"""
Diligence ratio engine.

Takes the resolved (mapped) financial dataframe from coa_mapper.apply_mapping
and computes the standard buy-side quantitative diligence package: margins,
EBITDA and Adjusted EBITDA with an itemized quality-of-earnings addback
worksheet, working-capital trend, customer concentration, and debt-service
coverage. Every number here is a plain function of the canonical line
items -- nothing is hidden in a spreadsheet formula a reader can't see.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .schema import LineItem

OPEX_ITEMS = [
    LineItem.SALARIES_WAGES,
    LineItem.OWNER_OFFICER_COMP,
    LineItem.RENT_OCCUPANCY,
    LineItem.MARKETING_ADVERTISING,
    LineItem.PROFESSIONAL_FEES,
    LineItem.RELATED_PARTY_EXPENSE,
    LineItem.ONE_TIME_EXPENSE,
    LineItem.OTHER_OPEX,
]


class Financials:
    """Convenience accessor over the resolved (statement, item, year, amount) table."""

    def __init__(self, resolved: pd.DataFrame):
        self.table = resolved
        self.years = sorted(resolved["fiscal_year"].unique())
        self._index = {
            (row.canonical_item, int(row.fiscal_year)): row.amount
            for row in resolved.itertuples()
        }

    def get(self, item: LineItem, year: int) -> float:
        return self._index.get((item.value, year), 0.0)

    def has_item(self, item: LineItem, year: int) -> bool:
        """True if this line item was actually present in the mapped source
        data for this year (as opposed to defaulting to 0 because nothing
        mapped to it). Matters for the owner-comp QoE addback below: a
        missing owner-compensation line usually means the entity's
        structure doesn't report one separately (e.g. all-distribution pass
        -through pay), not that the owner is working for free, and those two
        cases should not be treated the same way."""
        return (item.value, year) in self._index

    def sum_items(self, items: list[LineItem], year: int) -> float:
        return sum(self.get(item, year) for item in items)


@dataclass
class QoEAddback:
    label: str
    amount: float
    confidence: str  # "high" | "medium" | "requires verification"
    rationale: str


@dataclass
class YearResult:
    fiscal_year: int
    revenue: float
    cogs: float
    gross_profit: float
    gross_margin: float | None
    operating_expenses: float
    ebitda: float
    ebitda_margin: float | None
    addbacks: list[QoEAddback] = field(default_factory=list)
    adjusted_ebitda: float = 0.0
    adjusted_ebitda_margin: float | None = None
    net_income: float = 0.0
    current_assets: float = 0.0
    current_liabilities: float = 0.0
    working_capital: float = 0.0
    operating_working_capital: float = 0.0
    current_ratio: float | None = None
    dso: float | None = None  # days sales outstanding
    dpo: float | None = None  # days payable outstanding
    dio: float | None = None  # days inventory outstanding
    dscr: float | None = None  # debt service coverage ratio


def _safe_div(numerator: float, denominator: float) -> float | None:
    if not denominator:
        return None
    return numerator / denominator


def compute_year(fin: Financials, year: int, owner_market_comp: float = 120_000.0) -> YearResult:
    revenue = fin.get(LineItem.REVENUE, year)
    cogs = fin.get(LineItem.COGS, year)
    gross_profit = revenue - cogs
    gross_margin = _safe_div(gross_profit, revenue)

    opex_ex_dep = fin.sum_items(OPEX_ITEMS, year)
    d_and_a = fin.get(LineItem.DEPRECIATION_AMORTIZATION, year)
    interest = fin.get(LineItem.INTEREST_EXPENSE, year)
    other_income = fin.get(LineItem.OTHER_INCOME, year)
    tax = fin.get(LineItem.INCOME_TAX_EXPENSE, year)

    operating_income = gross_profit - opex_ex_dep - d_and_a
    net_income = operating_income - interest + other_income - tax

    # EBITDA = net income with interest, tax, D&A added back.
    ebitda = net_income + interest + tax + d_and_a
    ebitda_margin = _safe_div(ebitda, revenue)

    # --- Quality-of-earnings addback worksheet -----------------------------
    addbacks: list[QoEAddback] = []

    one_time = fin.get(LineItem.ONE_TIME_EXPENSE, year)
    if one_time:
        addbacks.append(QoEAddback(
            label="Non-recurring / one-time expense",
            amount=one_time,
            confidence="high",
            rationale=(
                f"${one_time:,.0f} booked as a one-time item in FY{year}. Added back in full "
                "because it is separately identified in the source ledger and, by definition, "
                "should not recur under new ownership."
            ),
        ))

    owner_comp = fin.get(LineItem.OWNER_OFFICER_COMP, year)
    owner_comp_reported = fin.has_item(LineItem.OWNER_OFFICER_COMP, year)
    excess_comp = owner_comp - owner_market_comp
    if not owner_comp_reported:
        pass  # No owner-comp line in the source data at all -- don't guess; see Financials.has_item.
    elif excess_comp > 0:
        addbacks.append(QoEAddback(
            label="Owner/officer compensation above market replacement rate",
            amount=excess_comp,
            confidence="requires verification",
            rationale=(
                f"Owner compensation of ${owner_comp:,.0f} exceeds the assumed market "
                f"replacement rate of ${owner_market_comp:,.0f} by ${excess_comp:,.0f}. "
                "Addback assumes a new operator would be paid at market rate; confirm "
                "against a role-specific compensation benchmark before relying on this number."
            ),
        ))
    elif excess_comp < 0:
        addbacks.append(QoEAddback(
            label="Owner/officer compensation below market replacement rate",
            amount=excess_comp,
            confidence="requires verification",
            rationale=(
                f"Owner compensation of ${owner_comp:,.0f} is below the assumed market "
                f"replacement rate of ${owner_market_comp:,.0f}. A negative addback of "
                f"${excess_comp:,.0f} reflects the cost of hiring a market-rate replacement "
                "operator; do not drop this adjustment just because it reduces Adjusted EBITDA."
            ),
        ))

    # NOTE: related-party expense is deliberately NOT auto-added-back here.
    # It is routed to the anomaly detector instead -- an addback assumes the
    # expense is non-operating and fully avoidable, which is exactly the
    # assumption a related-party transaction should not get for free.

    adjusted_ebitda = ebitda + sum(a.amount for a in addbacks)
    adjusted_ebitda_margin = _safe_div(adjusted_ebitda, revenue)

    # --- Balance sheet / working capital ------------------------------------
    current_assets = fin.sum_items(
        [LineItem.CASH, LineItem.ACCOUNTS_RECEIVABLE, LineItem.INVENTORY, LineItem.OTHER_CURRENT_ASSETS],
        year,
    )
    current_liabilities = fin.sum_items(
        [LineItem.ACCOUNTS_PAYABLE, LineItem.ACCRUED_LIABILITIES,
         LineItem.OTHER_CURRENT_LIABILITIES, LineItem.CURRENT_PORTION_LTD],
        year,
    )
    working_capital = current_assets - current_liabilities

    ar = fin.get(LineItem.ACCOUNTS_RECEIVABLE, year)
    inventory = fin.get(LineItem.INVENTORY, year)
    ap = fin.get(LineItem.ACCOUNTS_PAYABLE, year)
    other_ca = fin.get(LineItem.OTHER_CURRENT_ASSETS, year)
    accrued = fin.get(LineItem.ACCRUED_LIABILITIES, year)
    other_cl = fin.get(LineItem.OTHER_CURRENT_LIABILITIES, year)
    # Operating (non-cash, non-debt) working capital -- the M&A-standard NWC cut.
    operating_working_capital = (ar + inventory + other_ca) - (ap + accrued + other_cl)

    current_ratio = _safe_div(current_assets, current_liabilities)
    dso = _safe_div(ar, revenue) * 365 if revenue else None
    dio = _safe_div(inventory, cogs) * 365 if cogs else None
    dpo = _safe_div(ap, cogs) * 365 if cogs else None

    current_portion_ltd = fin.get(LineItem.CURRENT_PORTION_LTD, year)
    debt_service = interest + current_portion_ltd
    dscr = _safe_div(adjusted_ebitda, debt_service)

    return YearResult(
        fiscal_year=year,
        revenue=revenue,
        cogs=cogs,
        gross_profit=gross_profit,
        gross_margin=gross_margin,
        operating_expenses=opex_ex_dep + d_and_a,
        ebitda=ebitda,
        ebitda_margin=ebitda_margin,
        addbacks=addbacks,
        adjusted_ebitda=adjusted_ebitda,
        adjusted_ebitda_margin=adjusted_ebitda_margin,
        net_income=net_income,
        current_assets=current_assets,
        current_liabilities=current_liabilities,
        working_capital=working_capital,
        operating_working_capital=operating_working_capital,
        current_ratio=current_ratio,
        dso=dso,
        dpo=dpo,
        dio=dio,
        dscr=dscr,
    )


def compute_all_years(fin: Financials, owner_market_comp: float = 120_000.0) -> list[YearResult]:
    return [compute_year(fin, year, owner_market_comp) for year in fin.years]


_AGGREGATE_BUCKET_PATTERN = "other customer"


def customer_concentration(customer_detail: pd.DataFrame) -> pd.DataFrame:
    """Top-customer %, top-3 %, and HHI by year from the customer revenue detail sheet.

    customer_detail columns: statement, canonical_item (= customer name),
    fiscal_year, amount (= revenue from that customer that year).

    A catch-all row such as "All Other Customers (41)" is common in a real
    export and must stay in the denominator (it's real revenue) but must NOT
    be treated as a single named customer for ranking purposes -- otherwise
    an unremarkable long tail of small accounts reads as one giant customer
    and trips the concentration flag for the wrong reason.
    """
    if customer_detail.empty:
        return pd.DataFrame(columns=["fiscal_year", "top1_pct", "top3_pct", "hhi", "customer_count"])

    is_aggregate = customer_detail["canonical_item"].str.lower().str.contains(_AGGREGATE_BUCKET_PATTERN)

    rows = []
    for year, group in customer_detail.groupby("fiscal_year"):
        total = group["amount"].sum()
        if total <= 0:
            continue
        named = group[~is_aggregate.loc[group.index]]
        if named.empty:
            continue
        shares = (named["amount"] / total).sort_values(ascending=False)
        top1 = shares.iloc[0] * 100
        top3 = shares.iloc[:3].sum() * 100
        # HHI computed over named accounts plus the aggregate bucket as one
        # residual "market participant" -- this keeps the index meaningful
        # (shares still sum to 1) without letting the bucket masquerade as a
        # single customer in the top1/top3 ranking above.
        all_shares = (group["amount"] / total)
        hhi = (all_shares ** 2).sum() * 10_000
        rows.append({
            "fiscal_year": int(year),
            "top1_pct": round(top1, 1),
            "top3_pct": round(top3, 1),
            "hhi": round(hhi, 0),
            "customer_count": int(named["canonical_item"].nunique()),
        })
    return pd.DataFrame(rows).sort_values("fiscal_year")
