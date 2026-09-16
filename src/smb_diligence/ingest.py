"""
Ingestion layer: turns whatever a small business handed over (an Excel
export, a QuickBooks/Xero CSV, or a CPA-compiled PDF) into one consistent
"raw long" table:

    source_file | statement | raw_label | fiscal_year | amount

Nothing here tries to be clever about *meaning* -- that is the mapper's job
(coa_mapper.py). This layer only has to survive messy formatting: merged
headers, a stray total row, a PDF table that pdfplumber slices oddly, extra
whitespace, parenthesized negatives, currency symbols.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .schema import Statement

_STATEMENT_HINTS = {
    Statement.INCOME_STATEMENT: ("income", "p&l", "profit", "operations"),
    Statement.BALANCE_SHEET: ("balance",),
    Statement.CUSTOMER_DETAIL: ("customer", "concentration"),
}

_YEAR_RE = re.compile(r"(19|20)\d{2}")


def _infer_statement(sheet_or_file_name: str) -> Statement | None:
    name = sheet_or_file_name.lower()
    for stmt, hints in _STATEMENT_HINTS.items():
        if any(h in name for h in hints):
            return stmt
    return None


def _clean_amount(value) -> float | None:
    """Parse a spreadsheet/PDF cell into a float, or None if it isn't one.

    Handles ``$12,345``, ``(1,234)`` (accounting negative), blank cells, and
    stray whitespace -- the kind of thing that shows up in a real export.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text in ("", "-", "--", "nan", "NaN"):
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    text = text.replace("$", "").replace(",", "").strip()
    if text in ("", "-"):
        return None
    try:
        num = float(text)
    except ValueError:
        return None
    return -num if negative else num


def _tidy_year_columns(columns: list[str]) -> dict[str, int]:
    """Map raw column headers like 'FY2023' or '2023 Actual' to a bare year."""
    mapping = {}
    for col in columns:
        match = _YEAR_RE.search(str(col))
        if match:
            mapping[col] = int(match.group())
    return mapping


def read_xlsx_statements(path: str | Path) -> pd.DataFrame:
    """Read every sheet in a workbook that looks like a financial statement."""
    path = Path(path)
    xls = pd.ExcelFile(path)
    rows = []
    for sheet_name in xls.sheet_names:
        statement = _infer_statement(sheet_name)
        if statement is None:
            continue
        raw = xls.parse(sheet_name, header=0)
        if raw.empty:
            continue
        label_col = raw.columns[0]
        year_cols = _tidy_year_columns(list(raw.columns[1:]))
        if statement == Statement.CUSTOMER_DETAIL:
            id_col = label_col
        else:
            id_col = label_col
        for _, row in raw.iterrows():
            raw_label = row[id_col]
            if pd.isna(raw_label) or str(raw_label).strip() == "":
                continue
            for col, year in year_cols.items():
                amount = _clean_amount(row.get(col))
                if amount is None:
                    continue
                rows.append({
                    "source_file": path.name,
                    "statement": statement.value,
                    "raw_label": str(raw_label).strip(),
                    "fiscal_year": year,
                    "amount": amount,
                })
    return pd.DataFrame(rows)


def read_csv_statements(path: str | Path, statement_hint: Statement | None = None) -> pd.DataFrame:
    """Read a single-statement CSV export (typical of a QuickBooks/Xero download)."""
    path = Path(path)
    statement = statement_hint or _infer_statement(path.stem)
    if statement is None:
        raise ValueError(
            f"Could not infer statement type for {path.name}; pass statement_hint explicitly."
        )
    raw = pd.read_csv(path)
    if raw.empty:
        return pd.DataFrame()
    label_col = raw.columns[0]
    year_cols = _tidy_year_columns(list(raw.columns[1:]))
    rows = []
    for _, row in raw.iterrows():
        raw_label = row[label_col]
        if pd.isna(raw_label) or str(raw_label).strip() == "":
            continue
        for col, year in year_cols.items():
            amount = _clean_amount(row.get(col))
            if amount is None:
                continue
            rows.append({
                "source_file": path.name,
                "statement": statement.value,
                "raw_label": str(raw_label).strip(),
                "fiscal_year": year,
                "amount": amount,
            })
    return pd.DataFrame(rows)


def read_pdf_statements(path: str | Path) -> pd.DataFrame:
    """Extract line-item tables from a CPA-compiled PDF financial statement.

    Assumes each page holds one statement with a title line containing
    "income"/"balance" and a simple label + year-columns table below it,
    which is the layout the vast majority of CPA compilation reports use.
    """
    import pdfplumber

    path = Path(path)
    rows = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            statement = _infer_statement(text.splitlines()[0] if text else "")
            if statement is None:
                continue
            table = page.extract_table()
            if not table:
                continue
            header, *body = table
            year_cols = _tidy_year_columns(header[1:])
            for line in body:
                raw_label = line[0]
                if not raw_label or not raw_label.strip():
                    continue
                for idx, col in enumerate(header[1:], start=1):
                    if col not in year_cols:
                        continue
                    amount = _clean_amount(line[idx] if idx < len(line) else None)
                    if amount is None:
                        continue
                    rows.append({
                        "source_file": path.name,
                        "statement": statement.value,
                        "raw_label": raw_label.strip(),
                        "fiscal_year": year_cols[col],
                        "amount": amount,
                    })
    return pd.DataFrame(rows)


def read_any(path: str | Path) -> pd.DataFrame:
    """Dispatch on file extension. This is the function the pipeline calls."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        return read_xlsx_statements(path)
    if suffix == ".csv":
        return read_csv_statements(path)
    if suffix == ".pdf":
        return read_pdf_statements(path)
    raise ValueError(f"Unsupported file type: {suffix}")


def read_source_set(paths: list[str | Path]) -> pd.DataFrame:
    """Read and concatenate every file a business handed over."""
    frames = [read_any(p) for p in paths]
    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame(columns=["source_file", "statement", "raw_label", "fiscal_year", "amount"])
    return pd.concat(frames, ignore_index=True)
