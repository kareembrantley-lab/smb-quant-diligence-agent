"""
Diligence memo generator.

Builds a Minto Pyramid-structured Word document: the governing thought (the
answer) leads, the red/yellow/green flag summary follows immediately as the
MECE grouping of supporting arguments, and every number behind that
judgment is available as backup further down for whoever wants to check it.
Nobody reading page one should have to guess what this memo concludes.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from .anomalies import Flag, overall_rating
from .benchmarks import BenchmarkComparison
from .ratios import YearResult

RED = RGBColor(0xC0, 0x30, 0x30)
YELLOW = RGBColor(0xB8, 0x86, 0x00)
GREEN = RGBColor(0x2E, 0x7D, 0x32)
GREY = RGBColor(0x55, 0x55, 0x55)
NAVY = RGBColor(0x1B, 0x2A, 0x4A)

_COLOR_BY_RATING = {"red": RED, "yellow": YELLOW, "green": GREEN}
_LABEL_BY_RATING = {"red": "RED", "yellow": "YELLOW", "green": "GREEN"}
_FILL_BY_RATING = {"red": "F8D7DA", "yellow": "FFF3CD", "green": "D4EDDA"}


def _shade_cell(cell, hex_color: str) -> None:
    shading = cell._tc.get_or_add_tcPr()
    shd = shading.makeelement(qn("w:shd"), {
        qn("w:val"): "clear", qn("w:color"): "auto", qn("w:fill"): hex_color,
    })
    shading.append(shd)


def _money(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.0f}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _set_cell_text(cell, text, bold=False, color=None, size=10, align=None):
    cell.text = ""
    p = cell.paragraphs[0]
    if align:
        p.alignment = align
    run = p.add_run(text)
    run.bold = bold
    run.font.size = Pt(size)
    if color:
        run.font.color.rgb = color


def build_memo(
    *,
    company_name: str,
    industry_label: str,
    years: list[YearResult],
    flags: list[Flag],
    benchmark_comparisons: dict[str, BenchmarkComparison],
    concentration_df,
    mapping_needs_review_count: int,
    unresolved_labels: list[str],
    synthetic_data_note: str | None,
    out_path: str | Path,
    prepared_for: str = "Buy-Side Diligence File",
) -> Path:
    doc = Document()
    _set_base_styles(doc)

    rating = overall_rating(flags)
    latest = years[-1]

    # ---- Header -------------------------------------------------------
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = title.add_run(f"{company_name} — Quantitative Diligence Memo")
    run.bold = True
    run.font.size = Pt(20)
    run.font.color.rgb = NAVY

    sub = doc.add_paragraph()
    sub_run = sub.add_run(
        f"Prepared for: {prepared_for}    |    {date.today().strftime('%B %d, %Y')}    |    "
        f"Industry benchmark set: {industry_label}"
    )
    sub_run.font.size = Pt(10)
    sub_run.font.color.rgb = GREY

    if synthetic_data_note:
        note = doc.add_paragraph()
        note_run = note.add_run(synthetic_data_note)
        note_run.italic = True
        note_run.font.size = Pt(9)
        note_run.font.color.rgb = GREY

    doc.add_paragraph()

    # ---- Governing thought (Minto: answer first) -----------------------
    banner = doc.add_table(rows=1, cols=1)
    banner.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = banner.rows[0].cells[0]
    _shade_cell(cell, _FILL_BY_RATING[rating])
    p = cell.paragraphs[0]
    run = p.add_run(f"OVERALL FLAG: {_LABEL_BY_RATING[rating]}")
    run.bold = True
    run.font.size = Pt(13)
    run.font.color.rgb = _COLOR_BY_RATING[rating]

    doc.add_heading("Governing Thought", level=1)
    doc.add_paragraph(_governing_thought(company_name, rating, latest, flags))

    # ---- MECE flag summary (Situation / Complication / grouped support) --
    doc.add_heading("Flag Summary (Situation → Complication → What It Means)", level=1)
    if not flags:
        doc.add_paragraph(
            "No rules-based anomalies cleared threshold across the reviewed periods. This is a "
            "clean read against the checks run, not a certification -- it does not replace "
            "confirmatory procedures (bank confirmations, legal search, customer/vendor calls)."
        )
    else:
        by_category: dict[str, list[Flag]] = {}
        for f in flags:
            by_category.setdefault(f.category, []).append(f)
        table = doc.add_table(rows=1, cols=3)
        table.style = "Light Grid Accent 1"
        hdr = table.rows[0].cells
        _set_cell_text(hdr[0], "Flag", bold=True)
        _set_cell_text(hdr[1], "Severity", bold=True)
        _set_cell_text(hdr[2], "Headline", bold=True)
        for category, items in by_category.items():
            for f in items:
                row = table.add_row().cells
                _set_cell_text(row[0], category)
                _set_cell_text(row[1], _LABEL_BY_RATING[f.severity], bold=True, color=_COLOR_BY_RATING[f.severity])
                _set_cell_text(row[2], f.title)

    # ---- Financial snapshot ---------------------------------------------
    doc.add_heading("Financial Snapshot", level=1)
    _snapshot_table(doc, years)

    # ---- Quality of Earnings / Adjusted EBITDA bridge --------------------
    doc.add_heading("Quality-of-Earnings Addback Worksheet", level=1)
    doc.add_paragraph(
        "Adjusted EBITDA below reflects only the addbacks with a documented, itemized rationale. "
        "Related-party expense is intentionally excluded from this worksheet and handled instead "
        "as a flag requiring verification -- see Flag Summary."
    )
    for yr in years:
        doc.add_heading(f"FY{yr.fiscal_year}", level=2)
        table = doc.add_table(rows=1, cols=3)
        table.style = "Light List Accent 1"
        hdr = table.rows[0].cells
        _set_cell_text(hdr[0], "Line", bold=True)
        _set_cell_text(hdr[1], "Amount", bold=True)
        _set_cell_text(hdr[2], "Confidence / Rationale", bold=True)
        row = table.add_row().cells
        _set_cell_text(row[0], "EBITDA", bold=True)
        _set_cell_text(row[1], _money(yr.ebitda), bold=True)
        _set_cell_text(row[2], "Net income + interest + tax + D&A")
        for addback in yr.addbacks:
            row = table.add_row().cells
            _set_cell_text(row[0], addback.label)
            _set_cell_text(row[1], _money(addback.amount))
            _set_cell_text(row[2], f"[{addback.confidence}] {addback.rationale}", size=9)
        row = table.add_row().cells
        _set_cell_text(row[0], "Adjusted EBITDA", bold=True)
        _set_cell_text(row[1], _money(yr.adjusted_ebitda), bold=True)
        _set_cell_text(row[2], f"Adjusted EBITDA margin: {_pct(yr.adjusted_ebitda_margin)}")

    # ---- Anomaly detail ----------------------------------------------------
    doc.add_heading("Anomaly Detail", level=1)
    if not flags:
        doc.add_paragraph("No flags to detail.")
    for f in flags:
        h = doc.add_heading(f"{_LABEL_BY_RATING[f.severity]} — {f.title}", level=2)
        for run in h.runs:
            run.font.color.rgb = _COLOR_BY_RATING[f.severity]
        doc.add_paragraph(f.narrative)
        ev_table = doc.add_table(rows=0, cols=2)
        ev_table.style = "Light List"
        for key, value in f.evidence.items():
            row = ev_table.add_row().cells
            _set_cell_text(row[0], key.replace("_", " "), bold=True, size=9)
            _set_cell_text(row[1], str(value), size=9)
        doc.add_paragraph()

    # ---- Benchmark positioning ----------------------------------------------
    doc.add_heading("Benchmark Positioning", level=1)
    doc.add_paragraph(
        "Ranges are compiled from publicly available small-business benchmarking sources "
        "(RMA Annual Statement Studies category medians, IBISWorld-style industry reports) and "
        "are directional only. Being outside a range is context, not automatically a flag."
    )
    table = doc.add_table(rows=1, cols=5)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    for i, label in enumerate(["Metric", "Company (latest FY)", "Benchmark low", "Benchmark high", "Position"]):
        _set_cell_text(hdr[i], label, bold=True)
    for metric, comp in benchmark_comparisons.items():
        if comp is None:
            continue
        row = table.add_row().cells
        _set_cell_text(row[0], metric.replace("_", " "))
        is_days = "days" in metric
        _set_cell_text(row[1], f"{comp.company_value:.0f} days" if is_days else _pct(comp.company_value))
        _set_cell_text(row[2], f"{comp.low:.0f} days" if is_days else _pct(comp.low))
        _set_cell_text(row[3], f"{comp.high:.0f} days" if is_days else _pct(comp.high))
        position_label = comp.position.replace("_", " ")
        _set_cell_text(row[4], position_label)

    # ---- Customer concentration --------------------------------------------
    if concentration_df is not None and not concentration_df.empty:
        doc.add_heading("Customer Concentration", level=1)
        table = doc.add_table(rows=1, cols=5)
        table.style = "Light Grid Accent 1"
        hdr = table.rows[0].cells
        for i, label in enumerate(["FY", "Top customer %", "Top 3 %", "HHI", "# Customers"]):
            _set_cell_text(hdr[i], label, bold=True)
        for _, r in concentration_df.iterrows():
            row = table.add_row().cells
            _set_cell_text(row[0], str(int(r["fiscal_year"])))
            _set_cell_text(row[1], f"{r['top1_pct']:.1f}%")
            _set_cell_text(row[2], f"{r['top3_pct']:.1f}%")
            _set_cell_text(row[3], f"{r['hhi']:.0f}")
            _set_cell_text(row[4], str(int(r["customer_count"])))

    # ---- Methodology / data lineage disclosure -----------------------------
    doc.add_heading("Methodology and Data Lineage", level=1)
    doc.add_paragraph(
        "Source statements were parsed and mapped onto a fixed canonical chart of accounts "
        "using exact-match, fuzzy-match, and (where configured) LLM-assisted classification. "
        f"{mapping_needs_review_count} line item(s) across the reviewed periods were mapped with "
        "less than full confidence or require manual review; see mapping_table.csv in this run's "
        "output folder for the complete, human-auditable mapping."
    )
    if unresolved_labels:
        doc.add_paragraph(
            "The following source labels could not be confidently mapped and were EXCLUDED from "
            "the figures above pending manual review: " + ", ".join(unresolved_labels)
        )
    doc.add_paragraph(
        "This memo is a quantitative first pass. It does not substitute for legal review, "
        "bank/lender confirmations, tax return reconciliation, or management interviews."
    )

    # ---- Backup schedule -----------------------------------------------------
    doc.add_heading("Appendix: Full Ratio Backup", level=1)
    _backup_table(doc, years)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
    return out_path


def _governing_thought(company_name: str, rating: str, latest: YearResult, flags: list[Flag]) -> str:
    red_flags = [f for f in flags if f.severity == "red"]
    yellow_flags = [f for f in flags if f.severity == "yellow"]
    if rating == "red":
        lead = (
            f"{company_name} shows {len(red_flags)} red-severity flag(s) that should be resolved "
            "before this deal proceeds on its current terms."
        )
    elif rating == "yellow":
        lead = (
            f"{company_name} is directionally investable, but {len(yellow_flags)} flag(s) need "
            "targeted follow-up diligence before price or terms are finalized."
        )
    else:
        lead = (
            f"{company_name} clears the quantitative checks run in this pass with no red or "
            "yellow flags outstanding."
        )
    financials = (
        f" On the numbers: FY{latest.fiscal_year} revenue of {_money(latest.revenue)}, gross margin "
        f"{_pct(latest.gross_margin)}, and Adjusted EBITDA of {_money(latest.adjusted_ebitda)} "
        f"({_pct(latest.adjusted_ebitda_margin)} margin)."
    )
    return lead + financials


def _snapshot_table(doc, years: list[YearResult]) -> None:
    table = doc.add_table(rows=1, cols=len(years) + 1)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    _set_cell_text(hdr[0], "Metric", bold=True)
    for i, yr in enumerate(years, start=1):
        _set_cell_text(hdr[i], f"FY{yr.fiscal_year}", bold=True)

    rows = [
        ("Revenue", lambda y: _money(y.revenue)),
        ("Gross margin", lambda y: _pct(y.gross_margin)),
        ("EBITDA", lambda y: _money(y.ebitda)),
        ("EBITDA margin", lambda y: _pct(y.ebitda_margin)),
        ("Adjusted EBITDA", lambda y: _money(y.adjusted_ebitda)),
        ("Adjusted EBITDA margin", lambda y: _pct(y.adjusted_ebitda_margin)),
        ("Net income", lambda y: _money(y.net_income)),
        ("Working capital", lambda y: _money(y.working_capital)),
        ("Operating working capital", lambda y: _money(y.operating_working_capital)),
        ("Current ratio", lambda y: f"{y.current_ratio:.2f}x" if y.current_ratio else "n/a"),
        ("DSCR (Adj. EBITDA basis)", lambda y: f"{y.dscr:.2f}x" if y.dscr else "n/a"),
    ]
    for label, fn in rows:
        row = table.add_row().cells
        _set_cell_text(row[0], label, bold=True)
        for i, yr in enumerate(years, start=1):
            _set_cell_text(row[i], fn(yr))


def _backup_table(doc, years: list[YearResult]) -> None:
    table = doc.add_table(rows=1, cols=len(years) + 1)
    table.style = "Light List Accent 1"
    hdr = table.rows[0].cells
    _set_cell_text(hdr[0], "Metric", bold=True)
    for i, yr in enumerate(years, start=1):
        _set_cell_text(hdr[i], f"FY{yr.fiscal_year}", bold=True)
    rows = [
        ("COGS", lambda y: _money(y.cogs)),
        ("Gross profit", lambda y: _money(y.gross_profit)),
        ("Operating expenses (incl. D&A)", lambda y: _money(y.operating_expenses)),
        ("Current assets", lambda y: _money(y.current_assets)),
        ("Current liabilities", lambda y: _money(y.current_liabilities)),
        ("Days sales outstanding", lambda y: f"{y.dso:.0f}" if y.dso else "n/a"),
        ("Days payable outstanding", lambda y: f"{y.dpo:.0f}" if y.dpo else "n/a"),
        ("Days inventory outstanding", lambda y: f"{y.dio:.0f}" if y.dio else "n/a"),
    ]
    for label, fn in rows:
        row = table.add_row().cells
        _set_cell_text(row[0], label, bold=True)
        for i, yr in enumerate(years, start=1):
            _set_cell_text(row[i], fn(yr))


def _set_base_styles(doc: Document) -> None:
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)
    for section in doc.sections:
        section.left_margin = Inches(0.8)
        section.right_margin = Inches(0.8)
        section.top_margin = Inches(0.7)
        section.bottom_margin = Inches(0.7)
