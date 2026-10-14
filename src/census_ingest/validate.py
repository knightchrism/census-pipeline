"""Data-quality gates that run BEFORE anything is written to DuckDB.

WHY: catching a bad API response at ingest is cheaper than debugging a wrong
mart later. If a check fails we raise, and the year's existing raw data stays
untouched. WHAT BREAKS if removed: a truncated or malformed response (e.g. 40
counties instead of 67, or a renamed variable) would silently flow into dbt.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class ValidationError(ValueError):
    pass


@dataclass
class ValidationReport:
    row_count: int
    numeric_parse_rate: float
    issues: list[str] = field(default_factory=list)


def _is_number(v: object) -> bool:
    if v is None:
        return False
    try:
        float(v)  # Census sentinels like -666666666 ARE numbers; dbt turns them into nulls.
        return True
    except (TypeError, ValueError):
        return False


def validate_rows(
    rows: list[dict],
    required_columns: list[str],
    geoid_columns: list[str],
    numeric_columns: list[str],
    expected_count: int | None = None,
    min_parse_rate: float = 0.95,
) -> ValidationReport:
    issues: list[str] = []
    if not rows:
        raise ValidationError("no rows returned")

    missing = [c for c in required_columns if c not in rows[0]]
    if missing:
        raise ValidationError(f"missing required columns: {missing}")

    if expected_count is not None and len(rows) != expected_count:
        raise ValidationError(f"expected {expected_count} rows, got {len(rows)}")

    geoids = ["".join(str(r[c]) for c in geoid_columns) for r in rows]
    dupes = sorted({g for g in geoids if geoids.count(g) > 1})
    if dupes:
        raise ValidationError(f"duplicate geoids: {dupes[:10]}")

    cells = [r.get(c) for r in rows for c in numeric_columns]
    rate = sum(_is_number(v) for v in cells) / len(cells) if cells else 1.0
    if rate < min_parse_rate:
        raise ValidationError(f"numeric parse rate {rate:.3f} < {min_parse_rate}")
    if rate < 1.0:
        issues.append(f"{(1 - rate) * len(cells):.0f} non-numeric cells")
    return ValidationReport(len(rows), rate, issues)
