"""Turn a governed metric definition (semantic/metrics.yml) into safe SQL.

WHY separate from the server: pure functions are easy to unit test, and the
server stays a thin MCP wrapper. WHAT BREAKS if removed: get_metric/list_metrics.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

METRICS_PATH = Path(os.getenv("CENSUS_METRICS_PATH", Path(__file__).resolve().parents[2] / "semantic" / "metrics.yml"))
GROUP_BYS = ("county", "state")


class MetricError(ValueError):
    pass


def load_metrics(path: Path | str = METRICS_PATH) -> dict:
    return yaml.safe_load(Path(path).read_text())


def build_metric_sql(spec: dict, name: str, group_by: str = "county",
                     year: int | None = None, county: str | None = None) -> tuple[str, list]:
    """Return (sql, params). User input only ever goes into bound parameters;
    table/column names come from the governed YAML, never from the caller."""
    metrics = spec["metrics"]
    if name not in metrics:
        raise MetricError(f"unknown metric {name!r}; available: {sorted(metrics)}")
    if group_by not in GROUP_BYS:
        raise MetricError(f"group_by must be one of {GROUP_BYS}")
    if county and group_by != "county":
        raise MetricError("county filter requires group_by='county'")
    src = metrics[name]["sources"][group_by]
    ycol = src["year_column"]
    cols = []
    if group_by == "county":
        cols += ["geoid", "county_name"]
    else:
        cols += ["state_fips", "state_name"]
    if "start_year_column" in src:
        cols.append(f"{src['start_year_column']} AS start_year")
    cols.append(f"{ycol} AS year")
    cols.append(f"{src['expression']} AS {name}")
    if src.get("moe_expression"):
        cols.append(f"{src['moe_expression']} AS {name}_moe")
    where, params = [], []
    if src.get("filter"):
        where.append(src["filter"])
    if year is not None:
        where.append(f"{ycol} = ?")
        params.append(int(year))
    if county:
        # Accept a FIPS geoid ('12086') or a name ('Miami-Dade' / 'Miami-Dade County').
        where.append("(geoid = ? OR lower(county_name) = lower(?) OR lower(county_name) = lower(?) || ' county')")
        params += [county, county, county]
    sql = f"SELECT {', '.join(cols)} FROM {src['table']}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    order = "county_name" if group_by == "county" else "state_fips"
    sql += f" ORDER BY {ycol}, {order}"
    return sql, params
