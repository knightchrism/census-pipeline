"""Build the version-2 source catalog from verified official files.

PEP Vintage 2025 is one revision series (July 1, 2020–2025) plus the April 1, 2020
estimates base in that same file. Vintage 2024 is not copied in. ACS 5-year values
come from the existing tested export. Decennial queries are recorded only when the
API actually returns rows.
"""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

PEP_V2025_URL = (
    "https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/counties/totals/co-est2025-alldata.csv"
)
PEP_PAGE = "https://www.census.gov/data/datasets/time-series/demo/popest/2020s-counties-total.html"
ACS_PAGE = "https://www.census.gov/data/developers/data-sets/acs-5year.html"
DEC_PAGE = "https://www.census.gov/data/developers/data-sets/decennial-census.html"
DEC_2020_URL = "https://api.census.gov/data/2020/dec/pl"
DEC_2010_URL = "https://api.census.gov/data/2010/dec/sf1"

ACS_METRICS = (
    "total_population",
    "median_household_income",
    "median_age",
    "hispanic_population",
    "hispanic_share",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _int(text: str) -> int:
    if text is None or str(text).strip() == "":
        raise ValueError("blank PEP population")
    return int(text)


def load_pep_v2025(path: Path) -> dict:
    """Florida state and county rows from Vintage 2025 county totals."""
    with path.open(newline="", encoding="latin-1") as handle:
        rows = [row for row in csv.DictReader(handle) if row["STATE"] == "12" and row["SUMLEV"] in {"040", "050"}]
    state = next(row for row in rows if row["SUMLEV"] == "040")
    counties = [row for row in rows if row["SUMLEV"] == "050"]
    if len(counties) != 67:
        raise ValueError(f"expected 67 Florida counties, found {len(counties)}")
    july_cols = [f"POPESTIMATE{year}" for year in range(2020, 2026)]
    for col in ["ESTIMATESBASE2020", *july_cols]:
        state_value = _int(state[col])
        county_sum = sum(_int(row[col]) for row in counties)
        if state_value != county_sum:
            raise ValueError(f"{col} county sum {county_sum} != state {state_value}")

    def pack(row: dict) -> dict:
        geoid = row["STATE"] + row["COUNTY"]
        return {
            "geoid": geoid,
            "name": "Florida" if row["SUMLEV"] == "040" else row["CTYNAME"],
            "april1_2020_base": _int(row["ESTIMATESBASE2020"]),
            "july1": {str(year): _int(row[f"POPESTIMATE{year}"]) for year in range(2020, 2026)},
        }

    return {
        "state": pack(state),
        "counties": [pack(row) for row in counties],
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
    }


def _acs_measure(block: dict) -> dict:
    out = {}
    for name in ACS_METRICS:
        item = block[name]
        out[name] = {
            "value": item["value"],
            "moe": item.get("moe"),
            "uncertainty": item.get("uncertainty"),
            "low_reliability": item.get("low_reliability"),
        }
    return out


def load_acs(path: Path) -> dict:
    payload = json.loads(path.read_text())
    if payload["qc"]["issues"]:
        raise ValueError("ACS export failed QC: " + "; ".join(payload["qc"]["issues"]))
    counties = []
    for county in payload["counties"]:
        counties.append({
            "geoid": county["geoid"],
            "name": county["name"],
            "years": {year: _acs_measure(county["estimates"][year]) for year in ("2019", "2024")},
        })
    state = {
        "name": payload["state"]["name"],
        "years": {year: _acs_measure(payload["state"]["estimates"][year]) for year in ("2019", "2024")},
    }
    return {"state": state, "counties": counties, "generated_at": payload["generated_at"],
            "source_files": payload["source"].get("files", [])}


def decennial_status(attempt: dict | None) -> dict:
    """Record a real fetch attempt. Never invent counts."""
    if not attempt or not attempt.get("ok"):
        return {
            "id": "decennial-april1",
            "loaded": False,
            "family": "decennial_april1",
            "label": "Decennial census (April 1)",
            "short": "Decennial census",
            "reason": (
                "Not loaded. The Census Data API variable pages confirmed 2020 PL P1_001N and 2010 SF1 P001001, "
                "but county queries returned Missing Key. No API key was supplied and no counts were filled in."
            ),
            "links": {
                "documentation": DEC_PAGE,
                "2020": DEC_2020_URL + "?get=NAME,P1_001N&for=county:*&in=state:12",
                "2010": DEC_2010_URL + "?get=NAME,P001001&for=county:*&in=state:12",
            },
            "variables": {"2010": "P001001", "2020": "P1_001N"},
            "reference_date": "April 1",
        }
    return attempt


def comparison_allowed(left: dict, right: dict) -> dict:
    """Whether two selections can be compared. Different families are blocked."""
    if not left.get("loaded") or not right.get("loaded"):
        return {"ok": False, "reason": "One of the selections is not loaded."}
    if left["family"] != right["family"]:
        return {
            "ok": False,
            "reason": (
                f"{left['label']} and {right['label']} are different official series. "
                "They are not compared as if they measured the same thing."
            ),
        }
    if left["id"] != right["id"]:
        return {"ok": False, "reason": "Those releases are separate vintages and are not mixed."}
    return {"ok": True, "reason": "Same source, geography, and reference-date family."}


def build_catalog(pep: dict, acs: dict, generated_at: str, decennial: dict | None = None) -> dict:
    by_geo = {row["geoid"]: row for row in pep["counties"]}
    acs_by_geo = {row["geoid"]: row for row in acs["counties"]}
    if set(by_geo) != set(acs_by_geo):
        raise ValueError("PEP and ACS county GEOIDs differ")
    places = []
    for geoid in sorted(by_geo):
        pep_row = by_geo[geoid]
        acs_row = acs_by_geo[geoid]
        places.append({
            "geoid": geoid,
            "name": acs_row["name"],
            "pep_july1": pep_row["july1"],
            "pep_april1_2020_base": pep_row["april1_2020_base"],
            "acs5": acs_row["years"],
        })
    state = {
        "name": "Florida",
        "pep_july1": pep["state"]["july1"],
        "pep_april1_2020_base": pep["state"]["april1_2020_base"],
        "acs5": acs["state"]["years"],
    }
    sources = [
        {
            "id": "pep-v2025-july1",
            "loaded": True,
            "family": "pep_july1_v2025",
            "topic": "population",
            "label": "Population Estimates, Vintage 2025",
            "short": "PEP Vintage 2025",
            "reference_date": "July 1",
            "vintage": 2025,
            "years": [2020, 2021, 2022, 2023, 2024, 2025],
            "metric": "population",
            "unit": "people",
            "moe": False,
            "note": (
                "One Vintage 2025 revision series. July 1 estimates for 2020 through 2025. "
                "Earlier PEP vintages are not mixed in, because a new vintage revises the whole series."
            ),
            "links": {"product": PEP_PAGE, "file": PEP_V2025_URL},
            "retrieval": {
                "retrieved_for_build": generated_at,
                "sha256": pep["sha256"],
                "bytes": pep["bytes"],
                "user_agent": "GeoKnightGIS-census-pipeline/1.0 (Florida census source catalog; contact: info@geoknightgis.com)",
            },
        },
        {
            "id": "pep-v2025-april1-base",
            "loaded": True,
            "family": "pep_april1_base_v2025",
            "topic": "population",
            "label": "Vintage 2025 estimates base, April 1, 2020",
            "short": "PEP April 1, 2020 base",
            "reference_date": "April 1, 2020",
            "vintage": 2025,
            "years": [2020],
            "metric": "population",
            "unit": "people",
            "moe": False,
            "note": (
                "ESTIMATESBASE2020 from the Vintage 2025 file. It is the April 1, 2020 estimates base, "
                "not a July 1 estimate and not the 2020 Census redistricting count."
            ),
            "links": {"product": PEP_PAGE, "file": PEP_V2025_URL},
            "retrieval": {"retrieved_for_build": generated_at, "sha256": pep["sha256"], "bytes": pep["bytes"]},
        },
        {
            "id": "acs5",
            "loaded": True,
            "family": "acs5",
            "topic": "demographics",
            "label": "ACS 5-year estimates",
            "short": "ACS 5-year",
            "reference_date": "5-year period",
            "years": [2019, 2024],
            "periods": {"2019": "2015-2019", "2024": "2020-2024"},
            "metrics": list(ACS_METRICS),
            "moe": True,
            "note": (
                "Loaded vintages are 2019 (2015–2019) and 2024 (2020–2024). Those windows do not overlap. "
                "Other ACS release years are not in this file. Income change is nominal. "
                "A 90% margin of error is shown only when the export has one."
            ),
            "links": {"documentation": ACS_PAGE},
            "retrieval": {"acs_export_generated_at": acs["generated_at"]},
        },
        decennial_status(decennial),
    ]
    topics = [
        {
            "id": "population",
            "label": "Population",
            "sources": ["pep-v2025-july1", "pep-v2025-april1-base", "acs5", "decennial-april1"],
            "acs_metric": "total_population",
        },
        {"id": "income", "label": "Median household income", "sources": ["acs5"], "acs_metric": "median_household_income"},
        {"id": "age", "label": "Median age", "sources": ["acs5"], "acs_metric": "median_age"},
        {"id": "hispanic_share", "label": "Hispanic share", "sources": ["acs5"], "acs_metric": "hispanic_share"},
        {"id": "hispanic_population", "label": "Hispanic population", "sources": ["acs5"], "acs_metric": "hispanic_population"},
    ]
    return {
        "schema_version": 2,
        "generated_at": generated_at,
        "topics": topics,
        "sources": sources,
        "state": state,
        "counties": places,
        "qc": {
            "pep_vintage": 2025,
            "pep_county_count": 67,
            "pep_sums_match_state": True,
            "pep_vintages_mixed": False,
            "acs_years": [2019, 2024],
            "decennial_loaded": False,
        },
        "map": {
            "ground": "NASA GIBS Blue Marble Next Generation",
            "tile_url": "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_NextGeneration/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg",
            "extrusion": "Column height is the selected value scaled among the 67 counties. It is not terrain elevation.",
        },
    }


def main() -> None:
    from .paths import CATALOG_PATH, EXPORT_PATH, ROOT
    pep = ROOT / "data" / "cache" / "sources" / "co-est2025-alldata.csv"
    if not pep.exists():
        raise SystemExit(f"missing {pep}")
    catalog = write_catalog(pep, EXPORT_PATH, CATALOG_PATH)
    print(f"wrote {CATALOG_PATH} counties={catalog['qc']['pep_county_count']}")


def write_catalog(pep_csv: Path, acs_json: Path, dest: Path) -> dict:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    catalog = build_catalog(load_pep_v2025(pep_csv), load_acs(acs_json), generated_at)
    dest.write_text(json.dumps(catalog, indent=2) + "\n")
    return catalog


if __name__ == "__main__":
    main()
