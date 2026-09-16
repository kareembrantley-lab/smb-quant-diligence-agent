"""
Pipeline orchestrator: ingest -> map -> compute ratios -> benchmark ->
detect anomalies -> render memo. This is the one function the CLI (and the
tests) call; every step it wires together lives in its own module so each
can be tested, read, and reasoned about independently.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import benchmarks as bm
from .anomalies import detect_all
from .coa_mapper import apply_mapping, build_mapping_table, write_mapping_table
from .ingest import read_source_set
from .memo import build_memo
from .ratios import Financials, compute_all_years, customer_concentration
from .schema import LineItem, Statement


@dataclass
class CompanyConfig:
    company_name: str
    industry: str            # must match a value in industry_benchmarks.csv
    industry_label: str      # human-readable label for the memo
    source_files: list[str]
    owner_market_comp: float = 120_000.0
    synthetic_data_note: str | None = None


BENCHMARK_METRICS = ["gross_margin", "ebitda_margin", "current_ratio", "dso_days"]


def run_diligence(config: CompanyConfig, out_dir: str | Path, render_pdf: bool = True) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Ingest -----------------------------------------------------------
    raw = read_source_set(config.source_files)
    if raw.empty:
        raise ValueError(f"No line items parsed from source files: {config.source_files}")

    financial_raw = raw[raw["statement"] != Statement.CUSTOMER_DETAIL.value]
    customer_raw = raw[raw["statement"] == Statement.CUSTOMER_DETAIL.value]

    # 2. Chart-of-accounts mapping (human-reviewable) ----------------------
    mapping = build_mapping_table(raw)
    mapping_path = write_mapping_table(mapping, out_dir / "mapping_table.csv")
    resolved, unresolved = apply_mapping(raw, mapping)

    financial_resolved = resolved[resolved["statement"] != Statement.CUSTOMER_DETAIL.value]
    customer_resolved = resolved[resolved["statement"] == Statement.CUSTOMER_DETAIL.value]

    needs_review_count = int(mapping["needs_review"].sum())
    unresolved_labels = sorted(unresolved["raw_label"].unique().tolist()) if not unresolved.empty else []

    # 3. Ratio engine -------------------------------------------------------
    fin = Financials(financial_resolved)
    years = compute_all_years(fin, owner_market_comp=config.owner_market_comp)

    related_party_by_year = {
        y.fiscal_year: fin.get(LineItem.RELATED_PARTY_EXPENSE, y.fiscal_year) for y in years
    }
    distributions_by_year = {
        y.fiscal_year: fin.get(LineItem.OWNER_DISTRIBUTIONS, y.fiscal_year) for y in years
    }

    concentration_df = customer_concentration(customer_resolved)

    # 4. Benchmarks -----------------------------------------------------------
    benchmark_table = bm.load_benchmarks()
    latest = years[-1]
    latest_values = {
        "gross_margin": latest.gross_margin,
        "ebitda_margin": latest.ebitda_margin,
        "current_ratio": latest.current_ratio,
        "dso_days": latest.dso,
    }
    benchmark_comparisons = {
        metric: bm.compare(config.industry, metric, value, benchmark_table)
        for metric, value in latest_values.items()
    }

    # 5. Anomaly detection -----------------------------------------------------
    flags = detect_all(years, related_party_by_year, concentration_df, distributions_by_year)

    # 6. Memo -------------------------------------------------------------------
    memo_path = build_memo(
        company_name=config.company_name,
        industry_label=config.industry_label,
        years=years,
        flags=flags,
        benchmark_comparisons=benchmark_comparisons,
        concentration_df=concentration_df,
        mapping_needs_review_count=needs_review_count,
        unresolved_labels=unresolved_labels,
        synthetic_data_note=config.synthetic_data_note,
        out_path=out_dir / f"{_slug(config.company_name)}_diligence_memo.docx",
    )

    pdf_path = None
    if render_pdf:
        pdf_path = _convert_to_pdf(memo_path)

    return {
        "mapping_path": mapping_path,
        "memo_path": memo_path,
        "pdf_path": pdf_path,
        "years": years,
        "flags": flags,
        "concentration": concentration_df,
        "benchmark_comparisons": benchmark_comparisons,
        "needs_review_count": needs_review_count,
        "unresolved_labels": unresolved_labels,
    }


def _slug(name: str) -> str:
    return "".join(c.lower() if c.isalnum() else "_" for c in name).strip("_")


def _convert_to_pdf(docx_path: Path) -> Path | None:
    """Best-effort docx -> pdf conversion via headless LibreOffice, if installed."""
    soffice = _find_soffice()
    if not soffice:
        return None
    try:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(docx_path.parent), str(docx_path)],
            check=True,
            capture_output=True,
            timeout=90,
        )
    except Exception:
        return None
    pdf_path = docx_path.with_suffix(".pdf")
    return pdf_path if pdf_path.exists() else None


def _find_soffice() -> str | None:
    import shutil
    for candidate in ("soffice", "libreoffice"):
        path = shutil.which(candidate)
        if path:
            return path
    return None
