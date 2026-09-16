"""
Canonical chart of accounts for the diligence agent.

Every source file (regardless of whether it came out of QuickBooks, Xero, a
CPA-prepared PDF, or a messy Excel workbook) gets mapped onto this fixed set
of line items before any ratio is computed. Keeping the canonical schema
small and explicit is what makes the mapping step auditable: a human
reviewing ``mapping_table.csv`` only ever has to check a source label against
~30 possible targets, not against arbitrary downstream code.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Statement(str, Enum):
    INCOME_STATEMENT = "income_statement"
    BALANCE_SHEET = "balance_sheet"
    CUSTOMER_DETAIL = "customer_detail"


class LineItem(str, Enum):
    # --- Income statement ---------------------------------------------
    REVENUE = "revenue"
    COGS = "cogs"
    SALARIES_WAGES = "salaries_wages"
    OWNER_OFFICER_COMP = "owner_officer_comp"
    RENT_OCCUPANCY = "rent_occupancy"
    MARKETING_ADVERTISING = "marketing_advertising"
    PROFESSIONAL_FEES = "professional_fees"
    RELATED_PARTY_EXPENSE = "related_party_expense"
    ONE_TIME_EXPENSE = "one_time_expense"
    OTHER_OPEX = "other_opex"
    DEPRECIATION_AMORTIZATION = "depreciation_amortization"
    INTEREST_EXPENSE = "interest_expense"
    OTHER_INCOME = "other_income"
    INCOME_TAX_EXPENSE = "income_tax_expense"

    # --- Balance sheet ---------------------------------------------------
    CASH = "cash"
    ACCOUNTS_RECEIVABLE = "accounts_receivable"
    INVENTORY = "inventory"
    OTHER_CURRENT_ASSETS = "other_current_assets"
    PPE_NET = "ppe_net"
    OTHER_LONG_TERM_ASSETS = "other_long_term_assets"
    ACCOUNTS_PAYABLE = "accounts_payable"
    ACCRUED_LIABILITIES = "accrued_liabilities"
    OTHER_CURRENT_LIABILITIES = "other_current_liabilities"
    CURRENT_PORTION_LTD = "current_portion_long_term_debt"
    LONG_TERM_DEBT = "long_term_debt"
    OTHER_LONG_TERM_LIABILITIES = "other_long_term_liabilities"
    OWNER_DISTRIBUTIONS = "owner_distributions"
    EQUITY = "equity"


# Line items that belong on each statement, in display order. Used both to
# validate a mapping and to lay out the backup schedules in the memo.
INCOME_STATEMENT_ITEMS = [
    LineItem.REVENUE,
    LineItem.COGS,
    LineItem.SALARIES_WAGES,
    LineItem.OWNER_OFFICER_COMP,
    LineItem.RENT_OCCUPANCY,
    LineItem.MARKETING_ADVERTISING,
    LineItem.PROFESSIONAL_FEES,
    LineItem.RELATED_PARTY_EXPENSE,
    LineItem.ONE_TIME_EXPENSE,
    LineItem.OTHER_OPEX,
    LineItem.DEPRECIATION_AMORTIZATION,
    LineItem.INTEREST_EXPENSE,
    LineItem.OTHER_INCOME,
    LineItem.INCOME_TAX_EXPENSE,
]

BALANCE_SHEET_ITEMS = [
    LineItem.CASH,
    LineItem.ACCOUNTS_RECEIVABLE,
    LineItem.INVENTORY,
    LineItem.OTHER_CURRENT_ASSETS,
    LineItem.PPE_NET,
    LineItem.OTHER_LONG_TERM_ASSETS,
    LineItem.ACCOUNTS_PAYABLE,
    LineItem.ACCRUED_LIABILITIES,
    LineItem.OTHER_CURRENT_LIABILITIES,
    LineItem.CURRENT_PORTION_LTD,
    LineItem.LONG_TERM_DEBT,
    LineItem.OTHER_LONG_TERM_LIABILITIES,
    LineItem.OWNER_DISTRIBUTIONS,
    LineItem.EQUITY,
]

# Expense line items that reduce operating income; used by the mapper /
# anomaly detector to sanity-check sign conventions.
EXPENSE_ITEMS = {
    LineItem.COGS,
    LineItem.SALARIES_WAGES,
    LineItem.OWNER_OFFICER_COMP,
    LineItem.RENT_OCCUPANCY,
    LineItem.MARKETING_ADVERTISING,
    LineItem.PROFESSIONAL_FEES,
    LineItem.RELATED_PARTY_EXPENSE,
    LineItem.ONE_TIME_EXPENSE,
    LineItem.OTHER_OPEX,
    LineItem.DEPRECIATION_AMORTIZATION,
    LineItem.INTEREST_EXPENSE,
    LineItem.INCOME_TAX_EXPENSE,
}


@dataclass(frozen=True)
class CanonicalLabel:
    item: LineItem
    statement: Statement
    aliases: tuple[str, ...]


# Seed aliases used by the rules-based fuzzy matcher. These are real label
# variants pulled from QuickBooks, Xero, and typical CPA-compiled statement
# formats. The mapper does substring + fuzzy matching against this list
# first; anything left unresolved falls to the LLM-assisted step (or to a
# manual review row) when no fuzzy match clears the confidence threshold.
ALIAS_SEED: list[CanonicalLabel] = [
    CanonicalLabel(LineItem.REVENUE, Statement.INCOME_STATEMENT, (
        "revenue", "total revenue", "net sales", "sales", "gross sales",
        "total income", "service revenue", "product revenue",
    )),
    CanonicalLabel(LineItem.COGS, Statement.INCOME_STATEMENT, (
        "cost of goods sold", "cogs", "cost of sales", "cost of revenue",
        "direct costs", "cost of services",
    )),
    CanonicalLabel(LineItem.SALARIES_WAGES, Statement.INCOME_STATEMENT, (
        "salaries and wages", "payroll expense", "wages", "salaries",
        "staff compensation", "employee compensation",
    )),
    CanonicalLabel(LineItem.OWNER_OFFICER_COMP, Statement.INCOME_STATEMENT, (
        "officer compensation", "owner salary", "owner's compensation",
        "executive compensation", "member draw - guaranteed payment",
        "shareholder salary", "principal compensation",
    )),
    CanonicalLabel(LineItem.RENT_OCCUPANCY, Statement.INCOME_STATEMENT, (
        "rent expense", "rent", "occupancy expense", "facility costs",
        "lease expense",
    )),
    CanonicalLabel(LineItem.MARKETING_ADVERTISING, Statement.INCOME_STATEMENT, (
        "marketing", "advertising", "marketing and advertising",
        "sales and marketing", "promotion expense",
    )),
    CanonicalLabel(LineItem.PROFESSIONAL_FEES, Statement.INCOME_STATEMENT, (
        "professional fees", "legal and accounting", "consulting fees",
        "legal fees", "accounting fees",
    )),
    CanonicalLabel(LineItem.RELATED_PARTY_EXPENSE, Statement.INCOME_STATEMENT, (
        "related party expense", "affiliate management fee",
        "management fee - related party", "intercompany expense",
    )),
    CanonicalLabel(LineItem.ONE_TIME_EXPENSE, Statement.INCOME_STATEMENT, (
        "one-time expense", "nonrecurring expense", "litigation settlement",
        "restructuring costs", "moving and relocation expense",
    )),
    CanonicalLabel(LineItem.OTHER_OPEX, Statement.INCOME_STATEMENT, (
        "other operating expenses", "general and administrative",
        "g&a expense", "office expense", "insurance expense",
        "utilities", "supplies expense", "repairs and maintenance",
    )),
    CanonicalLabel(LineItem.DEPRECIATION_AMORTIZATION, Statement.INCOME_STATEMENT, (
        "depreciation", "amortization", "depreciation and amortization",
        "depreciation expense", "amortization expense",
    )),
    CanonicalLabel(LineItem.INTEREST_EXPENSE, Statement.INCOME_STATEMENT, (
        "interest expense", "interest exp", "loan interest",
    )),
    CanonicalLabel(LineItem.OTHER_INCOME, Statement.INCOME_STATEMENT, (
        "other income", "gain on sale of asset", "miscellaneous income",
        "interest income",
    )),
    CanonicalLabel(LineItem.INCOME_TAX_EXPENSE, Statement.INCOME_STATEMENT, (
        "income tax expense", "provision for income taxes", "state taxes",
        "franchise tax",
    )),
    CanonicalLabel(LineItem.CASH, Statement.BALANCE_SHEET, (
        "cash", "cash and cash equivalents", "cash in bank", "checking account",
    )),
    CanonicalLabel(LineItem.ACCOUNTS_RECEIVABLE, Statement.BALANCE_SHEET, (
        "accounts receivable", "trade receivables", "a/r", "receivables net",
    )),
    CanonicalLabel(LineItem.INVENTORY, Statement.BALANCE_SHEET, (
        "inventory", "merchandise inventory", "finished goods inventory",
        "inventory - net",
    )),
    CanonicalLabel(LineItem.OTHER_CURRENT_ASSETS, Statement.BALANCE_SHEET, (
        "other current assets", "prepaid expenses", "prepaid insurance",
    )),
    CanonicalLabel(LineItem.PPE_NET, Statement.BALANCE_SHEET, (
        "property plant and equipment", "fixed assets net", "ppe net",
        "equipment net of depreciation",
    )),
    CanonicalLabel(LineItem.OTHER_LONG_TERM_ASSETS, Statement.BALANCE_SHEET, (
        "other long-term assets", "intangible assets", "goodwill", "deposits",
    )),
    CanonicalLabel(LineItem.ACCOUNTS_PAYABLE, Statement.BALANCE_SHEET, (
        "accounts payable", "trade payables", "a/p",
    )),
    CanonicalLabel(LineItem.ACCRUED_LIABILITIES, Statement.BALANCE_SHEET, (
        "accrued liabilities", "accrued expenses", "accrued payroll",
    )),
    CanonicalLabel(LineItem.OTHER_CURRENT_LIABILITIES, Statement.BALANCE_SHEET, (
        "other current liabilities", "sales tax payable", "customer deposits",
        "deferred revenue",
    )),
    CanonicalLabel(LineItem.CURRENT_PORTION_LTD, Statement.BALANCE_SHEET, (
        "current portion of long-term debt", "current portion lt debt",
        "current maturities of long-term debt",
    )),
    CanonicalLabel(LineItem.LONG_TERM_DEBT, Statement.BALANCE_SHEET, (
        "long-term debt", "notes payable long term", "term loan payable",
        "sba loan payable",
    )),
    CanonicalLabel(LineItem.OTHER_LONG_TERM_LIABILITIES, Statement.BALANCE_SHEET, (
        "other long-term liabilities", "deferred tax liability",
    )),
    CanonicalLabel(LineItem.OWNER_DISTRIBUTIONS, Statement.BALANCE_SHEET, (
        "owner distributions", "member distributions", "dividends paid",
        "shareholder distributions",
    )),
    CanonicalLabel(LineItem.EQUITY, Statement.BALANCE_SHEET, (
        "owner's equity", "retained earnings", "member equity",
        "shareholder equity", "paid-in capital",
    )),
]
