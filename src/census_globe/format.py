"""Format governed values for display. None stays 'not available' and is never shown as zero."""
from __future__ import annotations


def format_value(unit: str, value) -> str:
    if value is None:
        return "not available"
    if unit == "people":
        return f"{int(round(value)):,}"
    if unit == "USD":
        return f"${int(round(value)):,}"
    if unit == "years":
        return f"{float(value):.1f}"
    if "0.10" in unit or unit == "ratio":
        return f"{float(value) * 100:+.2f}%"
    if str(unit).startswith("ratio"):
        return f"{float(value) * 100:.2f}%"
    return str(value)


def format_moe(unit: str, moe) -> str | None:
    if moe is None:
        return None
    return "±" + format_value(unit, moe).lstrip("+")


def period_label(year: int, period: str) -> str:
    pretty = str(period).replace("-", "–")
    return f"ACS 5-year {year} ({pretty})"
