#!/usr/bin/env python3
"""
CLI entry point for the diligence agent.

Examples
--------
Run all three demo companies and write output/sample memos:

    python scripts/run_diligence.py --demo

Run against your own export:

    python scripts/run_diligence.py \\
        --name "Acme Distribution LLC" \\
        --industry wholesale_distribution \\
        --files path/to/export.xlsx \\
        --out-dir output/acme
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smb_diligence.pipeline import CompanyConfig, run_diligence  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "synthetic"

DEMO_COMPANIES = [
    CompanyConfig(
        company_name="Cascade Valley Wholesale Foods",
        industry="wholesale_distribution",
        industry_label="Wholesale / Perishable Distribution",
        source_files=[str(DATA_DIR / "clean_co" / "cascade_valley_wholesale_foods.xlsx")],
        owner_market_comp=120_000,
        synthetic_data_note=(
            "Uses synthetic data modeled on a small wholesale food distributor; "
            "no client or employer data included. See data/synthetic/clean_co/."
        ),
    ),
    CompanyConfig(
        company_name="Northgate Precision Components",
        industry="light_manufacturing",
        industry_label="Light / Precision Manufacturing",
        source_files=[
            str(DATA_DIR / "qoe_issue_co" / "income_statement.csv"),
            str(DATA_DIR / "qoe_issue_co" / "balance_sheet.csv"),
            str(DATA_DIR / "qoe_issue_co" / "customer_detail.csv"),
        ],
        owner_market_comp=120_000,
        synthetic_data_note=(
            "Uses synthetic data modeled on a small precision-manufacturing shop; "
            "no client or employer data included. See data/synthetic/qoe_issue_co/."
        ),
    ),
    CompanyConfig(
        company_name="Summit Retail Supply Co",
        industry="ecommerce_retail",
        industry_label="E-Commerce / Retail Supply",
        source_files=[
            str(DATA_DIR / "fraud_co" / "income_statement.pdf"),
            str(DATA_DIR / "fraud_co" / "balance_sheet.pdf"),
            str(DATA_DIR / "fraud_co" / "customer_detail.csv"),
        ],
        owner_market_comp=120_000,
        synthetic_data_note=(
            "Uses synthetic data modeled on a small e-commerce retailer; "
            "no client or employer data included. See data/synthetic/fraud_co/."
        ),
    ),
]


def _print_summary(label: str, result: dict) -> None:
    from smb_diligence.anomalies import overall_rating

    rating = overall_rating(result["flags"])
    print(f"\n=== {label} ===")
    print(f"Overall flag: {rating.upper()}")
    print(f"Flags raised: {len(result['flags'])}")
    for f in result["flags"]:
        print(f"  [{f.severity.upper():6s}] {f.category}: {f.title}")
    print(f"Mapping rows needing review: {result['needs_review_count']}")
    if result["unresolved_labels"]:
        print(f"Unresolved labels (excluded): {result['unresolved_labels']}")
    print(f"Memo: {result['memo_path']}")
    if result["pdf_path"]:
        print(f"PDF:  {result['pdf_path']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--demo", action="store_true", help="Run all three synthetic demo companies.")
    parser.add_argument("--name", help="Company name for a single custom run.")
    parser.add_argument("--industry", help="Industry key from data/reference/industry_benchmarks.csv.")
    parser.add_argument("--industry-label", help="Human-readable industry label for the memo header.")
    parser.add_argument("--files", nargs="+", help="Source statement file(s): .xlsx, .csv, and/or .pdf.")
    parser.add_argument("--owner-market-comp", type=float, default=120_000, help="Assumed market owner-replacement salary.")
    parser.add_argument("--out-dir", default=str(ROOT / "sample_output"), help="Output directory.")
    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF rendering of the memo (requires LibreOffice).")
    args = parser.parse_args()

    if args.demo:
        # Regenerate the synthetic source data fresh every demo run, so the
        # numbers in this run always match what's in scripts/generate_synthetic_data.py.
        import generate_synthetic_data
        generate_synthetic_data.main()

        for company, slug in zip(DEMO_COMPANIES, ["clean_co", "qoe_issue_co", "fraud_co"]):
            result = run_diligence(company, out_dir=Path(args.out_dir) / slug, render_pdf=not args.no_pdf)
            _print_summary(company.company_name, result)
        return

    if not (args.name and args.industry and args.files):
        parser.error("Provide --demo, or all of --name/--industry/--files for a custom run.")

    config = CompanyConfig(
        company_name=args.name,
        industry=args.industry,
        industry_label=args.industry_label or args.industry.replace("_", " ").title(),
        source_files=args.files,
        owner_market_comp=args.owner_market_comp,
    )
    result = run_diligence(config, out_dir=Path(args.out_dir), render_pdf=not args.no_pdf)
    _print_summary(config.company_name, result)


if __name__ == "__main__":
    main()
