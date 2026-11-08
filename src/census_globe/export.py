"""Export governed Florida ACS marts to the globe's static files.

The web app reads these files. It does not open DuckDB and it does not accept SQL.
Missing estimates stay null. Sentinel codes are kept only as flags, never as values.

Rebuild after a fresh ingest and `dbt build`:

    uv run census-globe-export
    uv run census-globe-export --download-geography   # only if the zips are absent
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import yaml

from . import geometry
from .paths import (
    COUNTY_ZIP,
    COUNTY_ZIP_URL,
    COUNTIES_PATH,
    DATA_DIR,
    DB_PATH,
    EXPECTED_COUNTIES,
    EXPORT_PATH,
    GROWTH_KEY,
    LAND_PATH,
    LAND_ZIP,
    LAND_ZIP_URL,
    QC_PATH,
    ROOT,
    STATE_FIPS,
    YEARS,
)

SENTINELS = {
    -666666666: "The estimate could not be computed (too few sample observations).",
    -999999999: "Too few sample observations to compute the estimate or margin of error.",
    -888888888: "Not applicable or not available.",
    -555555555: "The estimate is controlled, so a margin of error is not appropriate.",
    -333333333: "The median falls in an open-ended interval, so a margin of error is not appropriate.",
    -222222222: "The margin of error could not be computed.",
}

# Choropleth layers. Other governed metrics stay available in the detail and ask panels.
LAYER_NAMES = (
    "total_population",
    "median_household_income",
    "median_age",
    "hispanic_share",
    "population_growth",
    "median_household_income_growth",
)

MEASURES = (
    ("total_population", "total_population", "total_population_moe", "total_population_flag", "total_population_moe_flag"),
    ("median_household_income", "median_household_income", "median_household_income_moe",
     "median_household_income_flag", "median_household_income_moe_flag"),
    ("median_age", "median_age", "median_age_moe", "median_age_flag", "median_age_moe_flag"),
    ("hispanic_population", "pop_hispanic", "pop_hispanic_moe", "pop_hispanic_flag", "pop_hispanic_moe_flag"),
)


class ExportError(RuntimeError):
    pass


def as_number(value):
    """Return a JSON number, or None. Never turns None into 0. Rejects sentinels."""
    if value is None:
        return None
    if isinstance(value, bool) or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        raise ExportError(f"unusable number {value!r}")
    number = float(value)
    if number.is_integer() and abs(number) < 10**15:
        number = int(number)
    if isinstance(number, int) and number in SENTINELS:
        raise ExportError(f"sentinel {number} was about to be stored as a value")
    return number


def as_flag(value):
    if value is None:
        return None
    flag = int(value)
    if flag not in SENTINELS:
        raise ExportError(f"unexpected annotation code {flag}")
    return flag


def uncertainty(moe, estimate_flag, moe_flag, ratio: bool = False) -> str:
    if moe is not None:
        return "A 90% margin of error is published for this estimate."
    if moe_flag in SENTINELS:
        return SENTINELS[moe_flag]
    if estimate_flag in SENTINELS:
        return SENTINELS[estimate_flag]
    if ratio:
        return "This is a ratio of two estimates. Its margin of error is not in the governed metric."
    return "No margin of error is stored for this value."


def measure(row: dict, name: str, value_col: str, moe_col: str, flag_col: str, moe_flag_col: str, extra: dict | None = None) -> dict:
    value = as_number(row.get(value_col))
    moe = as_number(row.get(moe_col))
    estimate_flag = as_flag(row.get(flag_col))
    moe_flag = as_flag(row.get(moe_flag_col))
    out = {
        "value": value,
        "moe": moe,
        "estimate_flag": estimate_flag,
        "moe_flag": moe_flag,
        "uncertainty": uncertainty(moe, estimate_flag, moe_flag),
    }
    if extra:
        out.update(extra)
    return out


def income_extra(row: dict) -> dict:
    cv = as_number(row.get("median_household_income_cv"))
    low = row.get("median_household_income_low_reliability")
    return {"cv": cv, "low_reliability": bool(low) if low is not None else None}


def share_measure(value) -> dict:
    number = as_number(value)
    return {
        "value": number,
        "moe": None,
        "estimate_flag": None,
        "moe_flag": None,
        "uncertainty": uncertainty(None, None, None, ratio=True),
    }


def growth_measure(value) -> dict:
    return {
        "value": as_number(value),
        "moe": None,
        "estimate_flag": None,
        "moe_flag": None,
        "uncertainty": "Growth is (end − start) / start. It is null when either vintage is missing or the start value is 0. No margin of error is stored for the rate.",
    }


def estimates_from_row(row: dict) -> dict:
    out = {}
    for name, value_col, moe_col, flag_col, moe_flag_col in MEASURES:
        extra = income_extra(row) if name == "median_household_income" else None
        out[name] = measure(row, name, value_col, moe_col, flag_col, moe_flag_col, extra)
    out["hispanic_share"] = share_measure(row.get("pct_hispanic"))
    return out


def _rows(con, sql: str) -> list[dict]:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


COUNTY_SQL = """
select
  f.geoid, f.county_name, f.state_fips, f.acs_year, f.acs_period,
  f.total_population, f.total_population_moe,
  s.total_population_flag, s.total_population_moe_flag,
  f.median_household_income, f.median_household_income_moe,
  f.median_household_income_flag, s.median_household_income_moe_flag,
  f.median_household_income_cv, f.median_household_income_low_reliability,
  f.median_age, f.median_age_moe, f.median_age_flag, s.median_age_moe_flag,
  f.pop_hispanic, f.pop_hispanic_moe, s.pop_hispanic_flag, s.pop_hispanic_moe_flag,
  f.pct_hispanic
from main.fct_county_demographics f
join main.stg_acs__county s using (geoid, acs_year)
where f.state_fips = '12'
order by f.county_name, f.acs_year
"""

STATE_SQL = """
select
  f.state_fips, f.state_name, f.acs_year, f.acs_period,
  f.total_population, f.total_population_moe,
  s.total_population_flag, s.total_population_moe_flag,
  f.median_household_income, f.median_household_income_moe,
  s.median_household_income_flag, s.median_household_income_moe_flag,
  f.median_age, f.median_age_moe, s.median_age_flag, s.median_age_moe_flag,
  f.pop_hispanic, f.pop_hispanic_moe, s.pop_hispanic_flag, s.pop_hispanic_moe_flag,
  f.pct_hispanic, f.county_count,
  f.county_sum_total_population, f.county_sum_pop_hispanic
from main.fct_state_demographics f
join main.stg_acs__state s using (state_fips, acs_year)
where f.state_fips = '12'
order by f.acs_year
"""

COUNTY_GROWTH_SQL = """
select geoid, county_name, start_year, end_year, windows_non_overlapping,
       total_population_growth, median_household_income_growth_nominal
from main.fct_county_growth
where state_fips = '12' and start_year = 2019 and end_year = 2024
"""

STATE_GROWTH_SQL = """
select state_fips, state_name, start_year, end_year, windows_non_overlapping,
       total_population_growth, median_household_income_growth_nominal
from main.fct_state_growth
where state_fips = '12' and start_year = 2019 and end_year = 2024
"""


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_files() -> list[dict]:
    found = []
    cache = ROOT / "data" / "cache"
    if not cache.exists():
        return found
    for path in sorted(cache.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".dat", ".csv", ".txt", ".zip"}:
            found.append({
                "path": str(path.relative_to(ROOT)),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            })
    return found


def load_metrics() -> dict:
    return yaml.safe_load((ROOT / "semantic" / "metrics.yml").read_text())


def metric_catalog(spec: dict) -> list[dict]:
    out = []
    for name, metric in spec["metrics"].items():
        out.append({
            "name": name,
            "label": metric["label"],
            "description": metric["description"],
            "unit": metric["unit"],
            "formula": metric["formula"],
            "additive": bool(metric["additive"]),
            "caveats": list(metric.get("caveats") or []),
            "layer": name in LAYER_NAMES,
            "growth": "start_year_column" in metric["sources"]["county"],
        })
    return out


def build_payload(county_rows, state_rows, county_growth, state_growth, spec, counties_geo, land_note: str) -> dict:
    by_geoid: dict[str, dict] = {}
    for row in county_rows:
        geoid = str(row["geoid"])
        year = str(int(row["acs_year"]))
        slot = by_geoid.setdefault(geoid, {
            "geoid": geoid,
            "name": row["county_name"],
            "state_fips": STATE_FIPS,
            "estimates": {},
            "growth": {},
        })
        slot["estimates"][year] = {
            "year": int(row["acs_year"]),
            "period": row["acs_period"],
            **estimates_from_row(row),
        }
    for row in county_growth:
        geoid = str(row["geoid"])
        if geoid not in by_geoid:
            raise ExportError(f"growth row without a county: {geoid}")
        by_geoid[geoid]["growth"][GROWTH_KEY] = {
            "start_year": int(row["start_year"]),
            "end_year": int(row["end_year"]),
            "start_period": "2015-2019",
            "end_period": "2020-2024",
            "windows_non_overlapping": bool(row["windows_non_overlapping"]),
            "population_growth": growth_measure(row["total_population_growth"]),
            "median_household_income_growth": growth_measure(row["median_household_income_growth_nominal"]),
        }
    state = {
        "state_fips": STATE_FIPS,
        "name": state_rows[0]["state_name"] if state_rows else "Florida",
        "estimates": {},
        "growth": {},
        "reconciliation": [],
    }
    for row in state_rows:
        year = str(int(row["acs_year"]))
        state["estimates"][year] = {
            "year": int(row["acs_year"]),
            "period": row["acs_period"],
            "county_count": int(row["county_count"]) if row["county_count"] is not None else None,
            **estimates_from_row(row),
        }
        state["reconciliation"].append({
            "year": int(row["acs_year"]),
            "county_count": int(row["county_count"]) if row["county_count"] is not None else None,
            "total_population": as_number(row["total_population"]),
            "county_sum_total_population": as_number(row["county_sum_total_population"]),
            "pop_hispanic": as_number(row["pop_hispanic"]),
            "county_sum_pop_hispanic": as_number(row["county_sum_pop_hispanic"]),
        })
    for row in state_growth:
        state["growth"][GROWTH_KEY] = {
            "start_year": int(row["start_year"]),
            "end_year": int(row["end_year"]),
            "start_period": "2015-2019",
            "end_period": "2020-2024",
            "windows_non_overlapping": bool(row["windows_non_overlapping"]),
            "population_growth": growth_measure(row["total_population_growth"]),
            "median_household_income_growth": growth_measure(row["median_household_income_growth_nominal"]),
        }
    counties = [by_geoid[gid] for gid in sorted(by_geoid)]
    geo_ids = [f["properties"]["geoid"] for f in counties_geo["features"]]
    return {
        "schema_version": 1,
        "product": "florida-census-globe",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "state_fips": STATE_FIPS,
        "years": list(YEARS),
        "growth_window": GROWTH_KEY,
        "null_policy": "Missing estimates and margins of error are JSON null. They are not stored as zero.",
        "source": {
            "publisher": "U.S. Census Bureau",
            "program": "American Community Survey 5-year estimates",
            "access": "Official ACS table-based Summary File on www2.census.gov (no API key).",
            "dataset": "acs/acs5",
            "ingest": "uv run census-ingest --source summary_file --years 2019 2024",
            "summary_file_index": "https://www2.census.gov/programs-surveys/acs/summary_file",
            "county_boundaries": {
                "title": "2024 Cartographic Boundary File, counties, 1:500,000",
                "file": "cb_2024_us_county_500k.zip",
                "url": COUNTY_ZIP_URL,
                "filter": "STATEFP = 12",
                "note": "Generalized for display. Joined on the 5-digit county GEOID. Not a survey boundary.",
            },
            "context_land": {
                "title": "Natural Earth 1:110m land",
                "url": LAND_ZIP_URL,
                "note": land_note,
            },
            "endorsement": "This product uses Census Bureau data but is not endorsed or certified by the Census Bureau.",
            "files": _source_files(),
        },
        "disclaimer": (
            "ACS figures are survey estimates with margins of error. Five-year vintages are period estimates "
            "(2019 covers 2015–2019 and 2024 covers 2020–2024). Dollar comparisons across vintages are nominal. "
            "State medians are the official state estimates, not averages of counties."
        ),
        "default_caveats": list(spec.get("default_caveats") or []),
        "metrics": metric_catalog(spec),
        "bbox": geometry.bbox(counties_geo),
        "state": state,
        "counties": counties,
        "qc": {},
        "geometry_count": len(geo_ids),
    }


def _sum(values: list) -> float:
    if any(v is None for v in values):
        raise ExportError("refusing to treat a missing county estimate as zero")
    return float(sum(values))


def qc_payload(payload: dict, counties_geo: dict) -> list[str]:
    issues = []
    counties = payload["counties"]
    if len(counties) != EXPECTED_COUNTIES:
        issues.append(f"expected {EXPECTED_COUNTIES} counties, found {len(counties)}")
    geoids = [c["geoid"] for c in counties]
    if len(set(geoids)) != len(geoids):
        issues.append("duplicate county GEOIDs")
    names = [c["name"] for c in counties]
    if len(set(names)) != len(names):
        issues.append("duplicate county names")
    for county in counties:
        if not county["geoid"].startswith("12") or len(county["geoid"]) != 5:
            issues.append(f"bad geoid {county['geoid']}")
        for year in YEARS:
            if str(year) not in county["estimates"]:
                issues.append(f"{county['name']} missing {year}")
        growth = county["growth"].get(GROWTH_KEY)
        if not growth or not growth["windows_non_overlapping"]:
            issues.append(f"{county['name']} missing a non-overlapping 2019–2024 growth row")
    for year in YEARS:
        block = payload["state"]["estimates"].get(str(year))
        if not block:
            issues.append(f"state missing {year}")
            continue
        pops = [c["estimates"][str(year)]["total_population"]["value"] for c in counties]
        hisp = [c["estimates"][str(year)]["hispanic_population"]["value"] for c in counties]
        try:
            pop_sum = _sum(pops)
            hisp_sum = _sum(hisp)
        except ExportError as exc:
            issues.append(str(exc))
            continue
        state_pop = block["total_population"]["value"]
        state_hisp = block["hispanic_population"]["value"]
        if pop_sum != state_pop:
            issues.append(f"{year} county population sum {pop_sum} != state {state_pop}")
        if state_hisp in (None, 0):
            issues.append(f"{year} state Hispanic population missing")
        elif abs(hisp_sum - state_hisp) > 0.001 * state_hisp:
            issues.append(f"{year} Hispanic county sum {hisp_sum} differs from state {state_hisp}")
        unexplained = 0
        for county in counties:
            moe = county["estimates"][str(year)]["total_population"]["moe"]
            flag = county["estimates"][str(year)]["total_population"]["moe_flag"]
            if moe is None and flag is None:
                unexplained += 1
        if unexplained:
            issues.append(f"{year}: {unexplained} population margins of error are null without an annotation code")
    geo_ids = [f["properties"]["geoid"] for f in counties_geo["features"]]
    if set(geo_ids) != set(geoids):
        issues.append(
            "geometry GEOIDs do not match the mart "
            f"(only geometry {sorted(set(geo_ids) - set(geoids))[:5]}, "
            f"only mart {sorted(set(geoids) - set(geo_ids))[:5]})"
        )
    payload["qc"] = {
        "county_count": len(counties),
        "years": list(YEARS),
        "geometry_features": len(geo_ids),
        "issues": issues,
        "reconciliation": payload["state"]["reconciliation"],
        "population_moe_null_without_flag": 0 if not issues else None,
    }
    return issues


def write_qc_markdown(payload: dict) -> None:
    state = payload["state"]
    lines = [
        "# Florida census globe — measured QC",
        "",
        "Generated by `census-globe-export` from the local DuckDB marts. "
        "Figures below were read at generation time. Missing values were not filled with zero.",
        "",
        f"- Generated at: {payload['generated_at']}",
        f"- Counties: {payload['qc']['county_count']}",
        f"- Geography features: {payload['qc']['geometry_features']}",
        f"- Vintages: {', '.join(str(y) for y in payload['years'])}",
        f"- QC issues: {len(payload['qc']['issues'])}",
        "",
        "## State reconciliation",
        "",
        "| Vintage | Period | State population | Sum of counties | Counties | State median income | State median age | Population growth to 2024 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    growth = state["growth"].get(GROWTH_KEY, {})
    pop_growth = (growth.get("population_growth") or {}).get("value")
    for year in YEARS:
        est = state["estimates"][str(year)]
        rec = next(r for r in state["reconciliation"] if r["year"] == year)
        growth_cell = f"{pop_growth:.4f}" if year == 2024 and pop_growth is not None else "—"
        lines.append(
            f"| {year} | {est['period']} | {rec['total_population']} | {rec['county_sum_total_population']} | "
            f"{rec['county_count']} | {est['median_household_income']['value']} | {est['median_age']['value']} | {growth_cell} |"
        )
    lines += [
        "",
        "Population county sums are exact against the official state row. "
        "Hispanic population uses the pipeline tolerance of 0.1 percent.",
        "",
        "## Source files hashed at export",
        "",
        "| File | Bytes | SHA-256 |",
        "| --- | ---: | --- |",
    ]
    for item in payload["source"]["files"]:
        lines.append(f"| `{item['path']}` | {item['bytes']} | `{item['sha256']}` |")
    lines += [
        "",
        "County geography: " + payload["source"]["county_boundaries"]["url"],
        "",
        "Land context: " + payload["source"]["context_land"]["url"],
        "",
        payload["source"]["endorsement"],
        "",
    ]
    QC_PATH.write_text("\n".join(lines))


def _apply_names(counties_geo: dict, payload: dict) -> None:
    names = {c["geoid"]: c["name"] for c in payload["counties"]}
    for feature in counties_geo["features"]:
        geoid = feature["properties"]["geoid"]
        if geoid in names:
            feature["properties"]["name"] = names[geoid]


def export(db_path: Path | None = None, download: bool = False) -> dict:
    db = Path(os.getenv("CENSUS_DUCKDB_PATH", str(db_path or DB_PATH)))
    if not db.exists():
        raise ExportError(f"warehouse not found at {db}. Run census-ingest and dbt build first.")
    if download:
        geometry.download_sources()
    if not COUNTIES_PATH.exists() and not COUNTY_ZIP.exists():
        raise ExportError(f"county boundary zip missing. Download {COUNTY_ZIP_URL} or pass --download-geography.")
    if not LAND_PATH.exists() and not LAND_ZIP.exists():
        raise ExportError(f"land zip missing. Download {LAND_ZIP_URL} or pass --download-geography.")
    counties_geo = geometry.florida_counties() if COUNTY_ZIP.exists() else json.loads(COUNTIES_PATH.read_text())
    if LAND_ZIP.exists():
        land = geometry.natural_earth_land()
        land_note = "Public-domain context layer for the globe. Not a census boundary."
    else:
        land = json.loads(LAND_PATH.read_text())
        land_note = "Reused the land file already in globe/data."
    con = duckdb.connect(str(db), read_only=True)
    try:
        payload = build_payload(
            _rows(con, COUNTY_SQL),
            _rows(con, STATE_SQL),
            _rows(con, COUNTY_GROWTH_SQL),
            _rows(con, STATE_GROWTH_SQL),
            load_metrics(),
            counties_geo,
            land_note,
        )
    finally:
        con.close()
    _apply_names(counties_geo, payload)
    issues = qc_payload(payload, counties_geo)
    if issues:
        raise ExportError("QC failed: " + "; ".join(issues))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    geometry.write_json(COUNTIES_PATH, counties_geo)
    geometry.write_json(LAND_PATH, land)
    write_qc_markdown(payload)
    return payload


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Export Florida ACS marts for the census globe")
    parser.add_argument("--download-geography", action="store_true",
                        help="download Census and Natural Earth zips if they are not cached")
    args = parser.parse_args(argv)
    payload = export(download=args.download_geography)
    print(f"wrote {EXPORT_PATH.relative_to(ROOT)} counties={payload['qc']['county_count']} "
          f"issues={len(payload['qc']['issues'])}")


if __name__ == "__main__":
    main()
