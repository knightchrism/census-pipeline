"""Build the version-2 source catalog from verified official files.

PEP Vintage 2025 is one revision series (July 1, 2020–2025) plus the April 1, 2020
estimates base in that same file. Vintage 2024 is not copied in. ACS 5-year values
come from the existing tested export. Decennial counts come from the checked public
extract of the 2010 SF1 and 2020 PL queries. That extract contains no API key.
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
    formulas = {
        item["name"]: item.get("formula")
        for item in payload.get("metrics", [])
        if item.get("name") in ACS_METRICS
    }
    return {
        "state": state,
        "counties": counties,
        "generated_at": payload["generated_at"],
        "source_files": payload["source"].get("files", []),
        "formulas": formulas,
        "sequence_hashes": _acs_sequence_hashes(payload["source"].get("files", [])),
        "summary_file_index": payload["source"].get("summary_file_index"),
    }


def load_decennial(path: Path) -> dict:
    """Florida April 1 census counts. Rejects a file that does not reconcile."""
    payload = json.loads(path.read_text())
    years = payload["years"]
    if set(years) != {"2010", "2020"}:
        raise ValueError("decennial file must contain 2010 and 2020 only")
    expected = {
        "2010": {"total": 18_801_310, "variable": "P001001", "dataset": "2010/dec/sf1"},
        "2020": {"total": 21_538_187, "variable": "P1_001N", "dataset": "2020/dec/pl"},
    }
    by_year: dict[str, dict[str, int]] = {}
    geoids = None
    for year, spec in expected.items():
        block = years[year]
        if block["dataset"] != spec["dataset"] or block["variable"] != spec["variable"]:
            raise ValueError(f"{year} dataset/variable does not match the official series")
        if block["reference_date"] != f"{year}-04-01":
            raise ValueError(f"{year} reference date is not April 1")
        counties = block["counties"]
        if len(counties) != 67 or block["state_total"] != spec["total"] or block["county_sum"] != spec["total"]:
            raise ValueError(f"{year} decennial reconciliation failed")
        values = {}
        for county in counties:
            geoid = county["geoid"]
            population = county["population"]
            if geoid in values or not isinstance(population, int) or population <= 0:
                raise ValueError(f"bad decennial county {geoid}")
            if geoid != "12" + county["county"] or county["state"] != "12":
                raise ValueError(f"decennial GEOID mismatch {geoid}")
            values[geoid] = population
        if sum(values.values()) != spec["total"]:
            raise ValueError(f"{year} county sum does not equal the state total")
        by_year[year] = values
        geoids = set(values) if geoids is None else geoids
        if set(values) != geoids:
            raise ValueError("2010 and 2020 county GEOIDs differ")
    text = path.read_text()
    if "key=" in text.lower() or "REDACTED" in text:
        raise ValueError("decennial extract must not contain an API key or a key placeholder")
    return {
        "by_year": by_year,
        "state": {year: expected[year]["total"] for year in expected},
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
    }


HISTORICAL_TOTALS = {
    1830: 34730, 1840: 54477, 1850: 87445, 1860: 140424, 1870: 187748,
    1880: 269493, 1890: 391422, 1900: 528542, 1910: 752619, 1920: 968470,
    1930: 1468211, 1940: 1897414, 1950: 2771305, 1960: 4951560, 1970: 6789443,
    1980: 9746324, 1990: 12937926, 2000: 15982378, 2010: 18801310, 2020: 21538187,
}


def load_historical(path: Path) -> dict:
    """Statewide decennial counts, 1830–2020. Rejects an invented 1845 population."""
    payload = json.loads(path.read_text())
    rows = payload["statewide_decennial_series"]
    years = [row["year"] for row in rows]
    if years != list(range(1830, 2021, 10)):
        raise ValueError("historical series must be the decennial years 1830 through 2020")
    if any(row["year"] == 1845 or "1845" in str(row.get("population")) for row in rows):
        raise ValueError("1845 must not be a population observation")
    statehood = payload["statehood"]
    if statehood.get("date") != "1845-03-03" or not statehood.get("not_a_population_observation"):
        raise ValueError("statehood must stay an event, not a count")
    if statehood.get("bracketing_federal_censuses") != {"before": 1840, "after": 1850}:
        raise ValueError("statehood must sit between the 1840 and 1850 censuses")
    census_1845 = payload["state_census_1845"]
    if "population" in census_1845 or census_1845.get("no_invented_statewide_total_saved_here") is not True:
        raise ValueError("1845 state census must not carry a statewide total")
    observations = []
    previous = 0
    for row in rows:
        year = row["year"]
        population = row["population"]
        if population != HISTORICAL_TOTALS[year] or not isinstance(population, int) or population <= previous:
            raise ValueError(f"historical total for {year} failed validation")
        if row["status"] not in {"territory", "state"}:
            raise ValueError(f"missing territory/state status for {year}")
        if year < 1850 and row["status"] != "territory":
            raise ValueError(f"{year} is territorial")
        if year >= 1850 and row["status"] != "state":
            raise ValueError(f"{year} is a state census")
        observations.append({
            "year": year,
            "population": population,
            "status": row["status"],
            "reference_date": row["reference_date"],
            "source_id": row["source_id"],
        })
        previous = population
    text = path.read_text()
    if "key=" in text.lower() or "REDACTED" in text:
        raise ValueError("historical file must not contain an API key")
    return {
        "observations": observations,
        "statehood": {
            "date": "1845-03-03",
            "event": statehood["event"],
            "not_a_population_observation": True,
            "before": 1840,
            "after": 1850,
            "sources": [{"org": item["org"], "url": item["url"]} for item in statehood["sources"]],
        },
        "state_census_1845": {
            "series": census_1845["official_archive"]["series"],
            "url": census_1845["official_archive"]["url"],
            "note": census_1845["reason"],
        },
        "caveats": payload["caveats"],
        "sources": {
            key: {k: value[k] for k in ("title", "publisher", "url", "sha256", "used_for_years") if k in value}
            for key, value in payload["sources"].items()
        },
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
    }


def historical_source(historical: dict) -> dict:
    return {
        "id": "historical-statewide",
        "loaded": True,
        "family": "historical_statewide_decennial",
        "kind": "count",
        "granularity": "state",
        "county_data": False,
        "topic": "population",
        "label": "Historical statewide census count",
        "short": "Statewide census, 1830–2020",
        "reference_date": "Census day",
        "years": [row["year"] for row in historical["observations"]],
        "metric": "population",
        "unit": "people",
        "moe": False,
        "note": (
            "Statewide decennial counts from 1830 through 2020. 1830 and 1840 are territorial. "
            "March 3, 1845, when Florida became the 27th state, is marked between the 1840 and 1850 counts. "
            "It is not a population. No year between censuses is estimated."
        ),
        "caveat": (
            "Enslaved people were counted in the early federal censuses, and free Black residents were counted. "
            "Before 1890, American Indians generally were not counted unless they were considered part of the non-Indian population. "
            "The 1870 Southern count is a known undercount; Forstall does not publish a corrected Florida total."
        ),
        "boundary_note": (
            "The outline on the globe is today's state. Historical county lines are not in this build, "
            "and these counts are not drawn on the modern 67 counties."
        ),
        "observations": historical["observations"],
        "statehood": historical["statehood"],
        "state_census_1845": historical["state_census_1845"],
        "links": {
            "forstall1996": historical["sources"]["forstall1996"]["url"],
            "apportionment_1910_2020": historical["sources"]["census_apportionment_change_1910_2020"]["url"],
            "statehood": historical["statehood"]["sources"][0]["url"],
        },
        "retrieval": {"sha256": historical["sha256"], "bytes": historical["bytes"]},
        "citations": {
            str(row["year"]): historical_citation(row, historical["sources"], historical["sha256"])
            for row in historical["observations"]
        },
    }


def county_display_allowed(source: dict) -> bool:
    """Historical statewide counts have no county figures to draw."""
    return bool(source.get("loaded") and source.get("county_data", True))


def decennial_source(decennial: dict, provenance: dict | None) -> dict:
    """Loaded April 1 census counts. Separate from the PEP estimates base."""
    requests = []
    if provenance:
        for item in provenance.get("requests", []):
            requests.append({
                "dataset": item["dataset"],
                "variable": item["variable"],
                "reference_date": item["reference_date"],
                "http_status": item["http_status"],
                "sha256": item["sha256"],
                "retrieved_at_utc": item["retrieved_at_utc"],
            })
    return {
        "id": "decennial-april1",
        "loaded": True,
        "family": "decennial_april1",
        "kind": "count",
        "topic": "population",
        "label": "Decennial census count, April 1",
        "short": "Decennial census count",
        "reference_date": "April 1",
        "years": [2010, 2020],
        "metric": "population",
        "unit": "people",
        "moe": False,
        "note": (
            "April 1 census counts: 2010 Summary File 1 P001001 and 2020 PL 94-171 P1_001N. "
            "These are counts, not estimates. The 2020 count is not the Vintage 2025 April 1 estimates base."
        ),
        "links": {
            "documentation": DEC_PAGE,
            "2020": DEC_2020_URL,
            "2010": DEC_2010_URL,
            "2010_examples": "https://api.census.gov/data/2010/dec/sf1/examples.html",
        },
        "citations": _decennial_citations(decennial, provenance),
        "variables": {"2010": "P001001", "2020": "P1_001N"},
        "retrieval": {
            "sha256": decennial["sha256"],
            "bytes": decennial["bytes"],
            "requests": requests,
            "api_key_in_repository": False,
        },
    }


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


def _cite(**fields) -> dict:
    citation = {
        "traceable": True,
        "document": None,
        "links": [],
        "reference_date": None,
        "period": None,
        "vintage": None,
        "dataset": None,
        "variable": None,
        "column": None,
        "table": None,
        "pdf_page": None,
        "retrieval": None,
        "validation": None,
    }
    citation.update(fields)
    return citation


def historical_citation(obs: dict, docs: dict, extract_sha: str) -> dict:
    """Map a recorded source id to its PDF. Page numbers were not recorded."""
    mapped = {
        "forstall1996_part3_fl_1830_1880": "forstall1996",
        "forstall1996_part3_fl_1890_1990": "forstall1996",
        "census_apportionment_change_1910_2020": "census_apportionment_change_1910_2020",
    }.get(obs.get("source_id"))
    doc = docs.get(mapped) if mapped else None
    if not doc or not doc.get("url"):
        return _cite(
            traceable=False,
            reference_date=obs.get("reference_date"),
            retrieval=None,
            validation=(
                "No official document URL is recorded for this observation. "
                "Do not treat the number as verified."
            ),
        )
    return _cite(
        document=doc.get("title"),
        links=[{"label": doc.get("title") or "Official PDF", "url": doc["url"]}],
        reference_date=obs.get("reference_date"),
        dataset=obs.get("source_id"),
        retrieval=(
            f"PDF SHA-256 recorded with the research file: {doc.get('sha256')}. "
            f"Extract SHA-256 {extract_sha}."
        ),
        validation=(
            "The stored total matches the figure recorded from that PDF for this year. "
            "No PDF page or table number was recorded. "
            "This check does not certify enumeration accuracy."
        ),
    )


def _decennial_citations(decennial: dict, provenance: dict | None) -> dict:
    specs = {
        "2010": {
            "dataset": "2010/dec/sf1",
            "variable": "P001001",
            "url": DEC_2010_URL,
            "label": "2010 Census SF1 API",
            "extra": {"label": "2010 SF1 examples", "url": "https://api.census.gov/data/2010/dec/sf1/examples.html"},
        },
        "2020": {
            "dataset": "2020/dec/pl",
            "variable": "P1_001N",
            "url": DEC_2020_URL,
            "label": "2020 Census PL 94-171 API",
            "extra": None,
        },
    }
    requests = (provenance or {}).get("requests", [])
    out = {}
    for year, spec in specs.items():
        hashes = [
            item["sha256"] for item in requests
            if item.get("dataset") == spec["dataset"] and item.get("variable") == spec["variable"] and item.get("sha256")
        ]
        links = [
            {"label": spec["label"], "url": spec["url"]},
            {"label": "Decennial census documentation", "url": DEC_PAGE},
        ]
        if spec["extra"]:
            links.append(spec["extra"])
        out[year] = _cite(
            document=f"Decennial census {spec['dataset']}",
            links=links,
            reference_date=f"April 1, {year}",
            dataset=spec["dataset"],
            variable=spec["variable"],
            retrieval=("Response SHA-256 " + "; ".join(hashes)) if hashes else f"Extract SHA-256 {decennial['sha256']}.",
            validation=(
                "County counts sum to the state total returned for the same query. "
                "That check does not certify enumeration accuracy."
            ),
        )
    return out


def _acs_sequence_hashes(files: list) -> dict:
    import re
    found = {}
    for item in files:
        match = re.search(r"/(\d{4})/acsdt5y\d{4}-([a-z0-9]+)\.", item.get("path", ""))
        if match:
            found.setdefault(match.group(1), {})[match.group(2)] = item["sha256"]
    return found


def _acs_citations(acs: dict) -> dict:
    """One citation per release year and metric. Page numbers were not recorded."""
    formulas = acs.get("formulas", {})
    hashes = acs.get("sequence_hashes", {})
    out = {}
    for year in (2019, 2024):
        by_metric = {}
        for name in ACS_METRICS:
            formula = formulas.get(name)
            tables = []
            if formula:
                import re
                tables = sorted(set(re.findall(r"B\d{5}", formula)))
            stems = [table.lower() for table in tables]
            recorded = [hashes.get(str(year), {}).get(stem) for stem in stems]
            recorded = [item for item in recorded if item]
            by_metric[name] = _cite(
                document="American Community Survey 5-year estimates, table-based Summary File",
                links=[
                    {"label": "ACS 5-year documentation", "url": ACS_PAGE},
                    {"label": "ACS Summary File index", "url": "https://www2.census.gov/programs-surveys/acs/summary_file"},
                ],
                reference_date=None,
                period="2015–2019" if year == 2019 else "2020–2024",
                vintage=str(year),
                dataset="acs/acs5 summary file",
                variable=formula,
                table=" and ".join(tables) if tables else None,
                pdf_page=None,
                retrieval=("Sequence-file SHA-256 " + ", ".join(recorded)) if recorded else None,
                validation=(
                    "The export's population county sums match the state total. "
                    "Medians are not summed. This check does not certify enumeration accuracy."
                ),
            )
        out[str(year)] = {"by_metric": by_metric}
    return out


def build_catalog(pep: dict, acs: dict, generated_at: str, decennial: dict, historical: dict, provenance: dict | None = None) -> dict:
    by_geo = {row["geoid"]: row for row in pep["counties"]}
    acs_by_geo = {row["geoid"]: row for row in acs["counties"]}
    if set(by_geo) != set(acs_by_geo):
        raise ValueError("PEP and ACS county GEOIDs differ")
    if set(by_geo) != set(decennial["by_year"]["2020"]):
        raise ValueError("Decennial and PEP county GEOIDs differ")
    if decennial["state"]["2020"] == pep["state"]["april1_2020_base"]:
        raise ValueError("2020 census count must stay distinct from the PEP estimates base")
    by_hist = {row["year"]: row["population"] for row in historical["observations"]}
    if by_hist[2010] != decennial["state"]["2010"] or by_hist[2020] != decennial["state"]["2020"]:
        raise ValueError("historical 2010/2020 statewide totals differ from the county decennial counts")
    if 1845 in by_hist:
        raise ValueError("historical series must not include 1845")
    places = []
    for geoid in sorted(by_geo):
        pep_row = by_geo[geoid]
        acs_row = acs_by_geo[geoid]
        places.append({
            "geoid": geoid,
            "name": acs_row["name"],
            "pep_july1": pep_row["july1"],
            "pep_april1_2020_base": pep_row["april1_2020_base"],
            "decennial": {year: decennial["by_year"][year][geoid] for year in ("2010", "2020")},
            "acs5": acs_row["years"],
        })
    state = {
        "name": "Florida",
        "pep_july1": pep["state"]["july1"],
        "pep_april1_2020_base": pep["state"]["april1_2020_base"],
        "decennial": decennial["state"],
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
            "kind": "estimate",
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
            "citations": {
                str(year): _cite(
                    document="County Population Totals: 2020–2025, Vintage 2025 (co-est2025-alldata.csv)",
                    links=[
                        {"label": "Vintage 2025 county totals CSV", "url": PEP_V2025_URL},
                        {"label": "Population Estimates product page", "url": PEP_PAGE},
                    ],
                    reference_date=f"July 1, {year}",
                    vintage="2025",
                    dataset="co-est2025-alldata.csv",
                    column=f"POPESTIMATE{year}",
                    retrieval=f"SHA-256 {pep['sha256']} of the CSV used for this build.",
                    validation=(
                        "County values in this column sum to the Florida state row. "
                        "That check does not certify enumeration accuracy."
                    ),
                )
                for year in range(2020, 2026)
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
            "kind": "estimate",
            "metric": "population",
            "unit": "people",
            "moe": False,
            "note": (
                "ESTIMATESBASE2020 from the Vintage 2025 file. It is the April 1, 2020 estimates base, "
                "not a July 1 estimate and not the 2020 Census redistricting count."
            ),
            "links": {"product": PEP_PAGE, "file": PEP_V2025_URL},
            "retrieval": {"retrieved_for_build": generated_at, "sha256": pep["sha256"], "bytes": pep["bytes"]},
            "citations": {
                "2020": _cite(
                    document="County Population Totals: 2020–2025, Vintage 2025 (co-est2025-alldata.csv)",
                    links=[
                        {"label": "Vintage 2025 county totals CSV", "url": PEP_V2025_URL},
                        {"label": "Population Estimates product page", "url": PEP_PAGE},
                    ],
                    reference_date="April 1, 2020",
                    vintage="2025",
                    dataset="co-est2025-alldata.csv",
                    column="ESTIMATESBASE2020",
                    retrieval=f"SHA-256 {pep['sha256']} of the CSV used for this build.",
                    validation=(
                        "County values in ESTIMATESBASE2020 sum to the Florida state row. "
                        "That check does not certify enumeration accuracy."
                    ),
                )
            },
        },
        {
            "id": "acs5",
            "loaded": True,
            "family": "acs5",
            "topic": "demographics",
            "label": "ACS 5-year estimates",
            "short": "ACS 5-year",
            "kind": "estimate",
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
            "links": {
                "documentation": ACS_PAGE,
                "summary_file": acs.get("summary_file_index") or "https://www2.census.gov/programs-surveys/acs/summary_file",
            },
            "retrieval": {"acs_export_generated_at": acs["generated_at"]},
            "citations": _acs_citations(acs),
        },
        decennial_source(decennial, provenance),
        historical_source(historical),
    ]
    topics = [
        {
            "id": "population",
            "label": "Population",
            "sources": ["pep-v2025-july1", "pep-v2025-april1-base", "acs5", "decennial-april1", "historical-statewide"],
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
            "decennial_loaded": True,
            "decennial_years": [2010, 2020],
            "decennial_2020_state": decennial["state"]["2020"],
            "decennial_2010_state": decennial["state"]["2010"],
            "decennial_2020_differs_from_pep_april1_base": True,
            "historical_years": [row["year"] for row in historical["observations"]],
            "historical_includes_1845_count": False,
            "historical_county_data": False,
        },
        "source_years": [
            {"id": "pep-v2025-july1", "kind": "estimate", "granularity": "county", "years": [2020, 2021, 2022, 2023, 2024, 2025], "reference_date": "July 1"},
            {"id": "pep-v2025-april1-base", "kind": "estimate", "granularity": "county", "years": [2020], "reference_date": "April 1, 2020"},
            {"id": "acs5", "kind": "estimate", "granularity": "county", "years": [2019, 2024], "reference_date": "5-year period"},
            {"id": "decennial-april1", "kind": "count", "granularity": "county", "years": [2010, 2020], "reference_date": "April 1"},
            {"id": "historical-statewide", "kind": "count", "granularity": "state", "years": [row["year"] for row in historical["observations"]], "reference_date": "Census day"},
        ],
        "map": {
            "ground": "NASA GIBS Blue Marble Next Generation",
            "tile_url": "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_NextGeneration/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg",
            "extrusion": "Column height is the selected value scaled among the 67 counties. It is not terrain elevation.",
        },
    }


def main() -> None:
    from .paths import CATALOG_PATH, DECENNIAL_PATH, DECENNIAL_PROVENANCE_PATH, EXPORT_PATH, HISTORICAL_PATH, ROOT
    pep = ROOT / "data" / "cache" / "sources" / "co-est2025-alldata.csv"
    if not pep.exists():
        raise SystemExit(f"missing {pep}")
    catalog = write_catalog(pep, EXPORT_PATH, DECENNIAL_PATH, DECENNIAL_PROVENANCE_PATH, HISTORICAL_PATH, CATALOG_PATH)
    print(f"wrote {CATALOG_PATH} counties={catalog['qc']['pep_county_count']}")


def write_catalog(
    pep_csv: Path, acs_json: Path, decennial_json: Path, provenance_json: Path, historical_json: Path, dest: Path,
) -> dict:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    provenance = json.loads(provenance_json.read_text()) if provenance_json.exists() else None
    catalog = build_catalog(
        load_pep_v2025(pep_csv),
        load_acs(acs_json),
        generated_at,
        load_decennial(decennial_json),
        load_historical(historical_json),
        provenance,
    )
    dest.write_text(json.dumps(catalog, indent=2) + "\n")
    return catalog


if __name__ == "__main__":
    main()
