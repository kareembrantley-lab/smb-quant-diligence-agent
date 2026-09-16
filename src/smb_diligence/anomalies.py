"""
Anomaly detection: a rules layer that always runs, plus an optional LLM
narrative layer that turns a rule's raw evidence into a plain-English
paragraph when ANTHROPIC_API_KEY is set.

The rules are deliberately transparent and threshold-based rather than a
black-box model -- in a diligence context, "the model flagged it" is not an
acceptable answer to "why." Every flag below carries the exact numbers that
tripped it, and the severity thresholds are named constants so a reviewer
can see (and argue with) the judgment calls directly in this file.

Flag categories map onto the four patterns named in the build spec:
  - revenue recognition risk       -> _flag_revenue_recognition
  - expense-timing shifts          -> _flag_expense_timing
  - related-party transactions     -> _flag_related_party
  - working-capital moves clustered
    near the transaction date      -> _flag_transaction_date_working_capital
Two supporting checks (inventory/COGS coherence and customer concentration)
round out what a real associate would sanity-check in the first pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .llm import narrate_anomaly
from .ratios import YearResult

Severity = str  # "red" | "yellow"


@dataclass
class Flag:
    category: str
    severity: Severity
    title: str
    evidence: dict
    template_narrative: str
    narrative: str = field(default="")

    def resolve_narrative(self) -> "Flag":
        llm_text = narrate_anomaly(self.title, self.evidence)
        self.narrative = llm_text or self.template_narrative
        return self


# --- Thresholds (named so they're easy to find and argue with) -------------
AR_GROWTH_GAP_YELLOW = 10.0   # percentage points AR growth may exceed revenue growth
AR_GROWTH_GAP_RED = 20.0
COGS_PCT_DROP_YELLOW = 5.0    # percentage-point drop in COGS-as-%-of-revenue, YoY
COGS_PCT_DROP_RED = 9.0
RELATED_PARTY_PCT_YELLOW = 1.0  # related-party expense as % of revenue
RELATED_PARTY_PCT_RED = 3.0
DPO_SPIKE_YELLOW = 12.0       # days
DPO_SPIKE_RED = 22.0
DISTRIBUTION_PCT_YELLOW = 0.60  # owner distributions as % of Adjusted EBITDA in final year
DISTRIBUTION_PCT_RED = 1.00
DIO_SWING_YELLOW = 35.0       # % change in DIO, YoY
DIO_SWING_RED = 60.0
TOP1_CUSTOMER_YELLOW = 25.0
TOP1_CUSTOMER_RED = 40.0


def _pct_change(new: float, old: float) -> float | None:
    if not old:
        return None
    return (new - old) / abs(old) * 100


def _flag_revenue_recognition(years: list[YearResult]) -> list[Flag]:
    flags = []
    for prev, curr in zip(years, years[1:]):
        rev_growth = _pct_change(curr.revenue, prev.revenue)
        ar_curr = curr.dso * curr.revenue / 365 if curr.dso else 0
        ar_prev = prev.dso * prev.revenue / 365 if prev.dso else 0
        ar_growth = _pct_change(ar_curr, ar_prev)
        if rev_growth is None or ar_growth is None:
            continue
        gap = ar_growth - rev_growth
        if gap >= AR_GROWTH_GAP_YELLOW:
            severity = "red" if gap >= AR_GROWTH_GAP_RED else "yellow"
            flags.append(Flag(
                category="Revenue recognition risk",
                severity=severity,
                title=f"Receivables growing faster than revenue, FY{prev.fiscal_year}→FY{curr.fiscal_year}",
                evidence={
                    "fiscal_years": f"FY{prev.fiscal_year}→FY{curr.fiscal_year}",
                    "revenue_growth_pct": round(rev_growth, 1),
                    "ar_growth_pct": round(ar_growth, 1),
                    "gap_points": round(gap, 1),
                    "dso_prior": round(prev.dso, 0) if prev.dso else None,
                    "dso_current": round(curr.dso, 0) if curr.dso else None,
                },
                template_narrative=(
                    f"Accounts receivable grew {ar_growth:.0f}% while revenue grew {rev_growth:.0f}% "
                    f"from FY{prev.fiscal_year} to FY{curr.fiscal_year}, a gap of {gap:.0f} points. Days "
                    f"sales outstanding moved from {prev.dso:.0f} to {curr.dso:.0f}. Confirm collections "
                    "activity and test a sample of invoices dated late in the period against cash receipts "
                    "before accepting reported revenue at face value."
                ),
            ))
    return flags


def _flag_expense_timing(years: list[YearResult]) -> list[Flag]:
    flags = []
    for prev, curr in zip(years, years[1:]):
        prev_cogs_pct = (prev.cogs / prev.revenue * 100) if prev.revenue else None
        curr_cogs_pct = (curr.cogs / curr.revenue * 100) if curr.revenue else None
        if prev_cogs_pct is None or curr_cogs_pct is None:
            continue
        drop = prev_cogs_pct - curr_cogs_pct
        if drop >= COGS_PCT_DROP_YELLOW:
            severity = "red" if drop >= COGS_PCT_DROP_RED else "yellow"
            flags.append(Flag(
                category="Expense-timing shift",
                severity=severity,
                title=f"COGS-to-revenue ratio dropped sharply, FY{prev.fiscal_year}→FY{curr.fiscal_year}",
                evidence={
                    "fiscal_years": f"FY{prev.fiscal_year}→FY{curr.fiscal_year}",
                    "cogs_pct_prior": round(prev_cogs_pct, 1),
                    "cogs_pct_current": round(curr_cogs_pct, 1),
                    "drop_points": round(drop, 1),
                },
                template_narrative=(
                    f"Cost of goods sold fell from {prev_cogs_pct:.1f}% of revenue in FY{prev.fiscal_year} "
                    f"to {curr_cogs_pct:.1f}% in FY{curr.fiscal_year}, an improvement of {drop:.1f} points with "
                    "no documented pricing or sourcing change on file. Gross margin expansion this sharp, "
                    "right before a sale process, warrants verifying that period-end costs were not deferred "
                    "or reclassified below the line."
                ),
            ))
    return flags


def _flag_related_party(years: list[YearResult], related_party_by_year: dict[int, float]) -> list[Flag]:
    flags = []
    for year in years:
        rp = related_party_by_year.get(year.fiscal_year, 0.0)
        if not rp or not year.revenue:
            continue
        pct = rp / year.revenue * 100
        if pct >= RELATED_PARTY_PCT_YELLOW:
            severity = "red" if pct >= RELATED_PARTY_PCT_RED else "yellow"
            flags.append(Flag(
                category="Related-party transaction",
                severity=severity,
                title=f"Related-party expense of ${rp:,.0f} in FY{year.fiscal_year}",
                evidence={
                    "fiscal_year": year.fiscal_year,
                    "related_party_expense": round(rp, 0),
                    "pct_of_revenue": round(pct, 2),
                },
                template_narrative=(
                    f"FY{year.fiscal_year} includes ${rp:,.0f} in related-party expense "
                    f"({pct:.1f}% of revenue). This was intentionally left out of the Adjusted EBITDA "
                    "addback worksheet -- related-party terms may or may not be at arm's length, and "
                    "assuming they're fully avoidable would overstate normalized earnings. Obtain the "
                    "underlying agreement and confirm pricing against a market comparable before any "
                    "addback is considered."
                ),
            ))
    return flags


def _flag_transaction_date_working_capital(years: list[YearResult]) -> list[Flag]:
    """Checks the FINAL year in the dataset (treated as the transaction/diligence year)
    for payables stretching and distribution spikes -- both classic ways an owner
    inflates trailing cash or strips value right before a sale."""
    flags = []
    if len(years) < 2:
        return flags
    prior_years = years[:-1]
    final = years[-1]
    avg_prior_dpo = sum(y.dpo for y in prior_years if y.dpo) / max(
        1, len([y for y in prior_years if y.dpo])
    )
    if final.dpo and avg_prior_dpo:
        spike = final.dpo - avg_prior_dpo
        if spike >= DPO_SPIKE_YELLOW:
            severity = "red" if spike >= DPO_SPIKE_RED else "yellow"
            flags.append(Flag(
                category="Working capital near transaction date",
                severity=severity,
                title=f"Payables stretched in the final period (FY{final.fiscal_year})",
                evidence={
                    "fiscal_year": final.fiscal_year,
                    "dpo_final_year": round(final.dpo, 0),
                    "avg_dpo_prior_years": round(avg_prior_dpo, 0),
                    "spike_days": round(spike, 0),
                },
                template_narrative=(
                    f"Days payable outstanding reached {final.dpo:.0f} in FY{final.fiscal_year} versus "
                    f"a {avg_prior_dpo:.0f}-day average in prior years, a stretch of {spike:.0f} days. "
                    "Delaying vendor payments right before a sale inflates trailing cash and reported "
                    "working capital without changing the underlying business -- confirm the post-close "
                    "working capital peg accounts for this, or expect a true-up."
                ),
            ))
    return flags


def _flag_inventory_coherence(years: list[YearResult]) -> list[Flag]:
    flags = []
    for prev, curr in zip(years, years[1:]):
        if not prev.dio or not curr.dio:
            continue
        swing = _pct_change(curr.dio, prev.dio)
        if swing is None:
            continue
        if abs(swing) >= DIO_SWING_YELLOW:
            severity = "red" if abs(swing) >= DIO_SWING_RED else "yellow"
            direction = "increased" if swing > 0 else "decreased"
            flags.append(Flag(
                category="Inventory / COGS coherence",
                severity=severity,
                title=f"Days inventory outstanding {direction} {abs(swing):.0f}%, FY{prev.fiscal_year}→FY{curr.fiscal_year}",
                evidence={
                    "fiscal_years": f"FY{prev.fiscal_year}→FY{curr.fiscal_year}",
                    "dio_prior": round(prev.dio, 0),
                    "dio_current": round(curr.dio, 0),
                    "pct_change": round(swing, 1),
                },
                template_narrative=(
                    f"Days inventory outstanding moved from {prev.dio:.0f} to {curr.dio:.0f} days "
                    f"({swing:+.0f}%) between FY{prev.fiscal_year} and FY{curr.fiscal_year} without a "
                    "matching revenue or sourcing change on file. This size of swing is more consistent "
                    "with a count adjustment, a costing change, or unrecorded shrink than with normal "
                    "operations -- a physical count reconciliation is warranted before closing."
                ),
            ))
    return flags


def _flag_distribution_spike(years: list[YearResult], distributions_by_year: dict[int, float]) -> list[Flag]:
    """Flags owner distributions taken out right before/at the transaction-year
    cutoff that are large relative to Adjusted EBITDA -- a classic way to strip
    cash out of a business immediately ahead of a sale."""
    flags = []
    final = years[-1]
    dist = distributions_by_year.get(final.fiscal_year, 0.0)
    if not dist or final.adjusted_ebitda <= 0:
        return flags
    pct = dist / final.adjusted_ebitda
    if pct >= DISTRIBUTION_PCT_YELLOW:
        severity = "red" if pct >= DISTRIBUTION_PCT_RED else "yellow"
        flags.append(Flag(
            category="Working capital near transaction date",
            severity=severity,
            title=f"Owner distributions of ${dist:,.0f} in FY{final.fiscal_year} ({pct * 100:.0f}% of Adjusted EBITDA)",
            evidence={
                "fiscal_year": final.fiscal_year,
                "owner_distributions": round(dist, 0),
                "adjusted_ebitda": round(final.adjusted_ebitda, 0),
                "pct_of_adjusted_ebitda": round(pct * 100, 1),
            },
            template_narrative=(
                f"Owner distributions of ${dist:,.0f} in FY{final.fiscal_year} equal {pct * 100:.0f}% "
                "of that year's Adjusted EBITDA, taken in the same period the business is being "
                "marketed for sale. This does not change normalized earnings power, but it does mean "
                "cash was pulled out ahead of close -- confirm the purchase agreement's cash-free, "
                "debt-free mechanics and working capital peg reflect this, not the pre-distribution balance."
            ),
        ))
    return flags


def _flag_customer_concentration(concentration_df) -> list[Flag]:
    flags = []
    if concentration_df is None or concentration_df.empty:
        return flags
    latest = concentration_df.sort_values("fiscal_year").iloc[-1]
    top1 = latest["top1_pct"]
    if top1 >= TOP1_CUSTOMER_YELLOW:
        severity = "red" if top1 >= TOP1_CUSTOMER_RED else "yellow"
        flags.append(Flag(
            category="Customer concentration",
            severity=severity,
            title=f"Top customer is {top1:.0f}% of FY{int(latest['fiscal_year'])} revenue",
            evidence={
                "fiscal_year": int(latest["fiscal_year"]),
                "top1_pct": top1,
                "top3_pct": latest["top3_pct"],
                "hhi": latest["hhi"],
            },
            template_narrative=(
                f"The largest customer represented {top1:.0f}% of FY{int(latest['fiscal_year'])} revenue "
                f"(top three: {latest['top3_pct']:.0f}%, HHI {latest['hhi']:.0f}). Retention risk on this "
                "account should be underwritten explicitly -- get contract terms, tenure, and a reference "
                "call before assuming this revenue transfers with the business."
            ),
        ))
    return flags


def detect_all(
    years: list[YearResult],
    related_party_by_year: dict[int, float],
    concentration_df,
    distributions_by_year: dict[int, float] | None = None,
) -> list[Flag]:
    flags: list[Flag] = []
    flags += _flag_revenue_recognition(years)
    flags += _flag_expense_timing(years)
    flags += _flag_related_party(years, related_party_by_year)
    flags += _flag_transaction_date_working_capital(years)
    flags += _flag_distribution_spike(years, distributions_by_year or {})
    flags += _flag_inventory_coherence(years)
    flags += _flag_customer_concentration(concentration_df)
    return [f.resolve_narrative() for f in flags]


def overall_rating(flags: list[Flag]) -> str:
    if any(f.severity == "red" for f in flags):
        return "red"
    if any(f.severity == "yellow" for f in flags):
        return "yellow"
    return "green"
