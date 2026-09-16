"""
Industry benchmark comparison.

Loads data/reference/industry_benchmarks.csv -- a small, explicitly-cited
reference table -- and scores a company's computed ratios against it. These
ranges are compiled from publicly available small-business benchmarking
sources (RMA Annual Statement Studies category medians, IBISWorld-style
industry reports) and are directional, not authoritative. The memo repeats
that caveat next to every benchmark comparison; nothing here should be read
as a substitute for an industry-specific benchmarking subscription.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DEFAULT_BENCHMARK_PATH = Path(__file__).resolve().parents[2] / "data" / "reference" / "industry_benchmarks.csv"


@dataclass
class BenchmarkComparison:
    metric: str
    company_value: float
    low: float
    mid: float
    high: float
    position: str  # "below_range" | "within_range" | "above_range"
    source_note: str


def load_benchmarks(path: Path = DEFAULT_BENCHMARK_PATH) -> pd.DataFrame:
    return pd.read_csv(path)


def compare(industry: str, metric: str, value: float | None, benchmarks: pd.DataFrame) -> BenchmarkComparison | None:
    if value is None:
        return None
    row = benchmarks[(benchmarks["industry"] == industry) & (benchmarks["metric"] == metric)]
    if row.empty:
        return None
    r = row.iloc[0]
    if value < r["low"]:
        position = "below_range"
    elif value > r["high"]:
        position = "above_range"
    else:
        position = "within_range"
    return BenchmarkComparison(
        metric=metric,
        company_value=value,
        low=r["low"],
        mid=r["mid"],
        high=r["high"],
        position=position,
        source_note=r["source_note"],
    )
