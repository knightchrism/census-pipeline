"""Alternative source: the official ACS *table-based Summary File* on www2.census.gov.

WHY this exists: the Data API requires a valid, activated key. The Summary File
publishes the SAME official estimates/MOEs (including the same sentinel codes)
as bulk pipe-delimited files that need no key. This module reshapes those files
into exactly the rows the API would return (NAME, B01003_001E, B01003_001M, ...,
state, county) so validation, loading and dbt are identical for both sources.
WHAT BREAKS if removed: nothing in the API path; you just lose the no-key fallback.

Availability: table-based files exist for 2019 (published as a "prototype") and
2021+; earlier vintages (e.g. 2014) only exist in the older sequence-file format,
which this module does not parse.
"""
from __future__ import annotations

import csv
import io
import logging
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)
SF_BASE = "https://www2.census.gov/programs-surveys/acs/summary_file"

# Where each vintage's 5-year table files and geography (names) file live.
SF_LAYOUT = {
    2019: {"data": "2019/prototype/5YRData", "geos": "2019/prototype/Geos20195YR.csv"},
}


def _layout(year: int) -> dict:
    if year in SF_LAYOUT:
        return SF_LAYOUT[year]
    if year >= 2021:
        return {"data": f"{year}/table-based-SF/data/5YRData",
                "geos": f"{year}/table-based-SF/documentation/Geos{year}5YR.txt"}
    raise ValueError(f"No table-based Summary File for ACS 5-year {year} (use the API source)")


def _download(url: str, dest: Path, retries: int = 4) -> Path:
    """Download once into a local cache (re-runs are fast and offline-friendly)."""
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries + 1):
        try:
            with requests.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                tmp = dest.with_suffix(".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                tmp.rename(dest)
                log.info("downloaded %s (%d bytes)", url, dest.stat().st_size)
                return dest
        except requests.RequestException as e:
            if attempt == retries:
                raise
            delay = 2 * 2**attempt
            log.warning("retry %s in %ss after %s", url, delay, type(e).__name__)
            time.sleep(delay)
    raise RuntimeError("unreachable")  # pragma: no cover


def parse_table_file(text_lines, geo_ids: set[str]) -> dict[str, dict[str, str]]:
    """Parse a pipe-delimited table file, keeping only `geo_ids`.

    Column 'B01003_E001' is renamed to the API style 'B01003_001E'.
    """
    header = next(text_lines).rstrip("\r\n").split("|")
    out: dict[str, dict[str, str]] = {}
    for line in text_lines:
        gid = line.split("|", 1)[0]
        if gid in geo_ids:
            vals = line.rstrip("\r\n").split("|")
            row = {}
            for col, v in zip(header[1:], vals[1:]):
                table, cell = col.split("_")          # B01003, E001
                row[f"{table}_{cell[1:]}{cell[0]}"] = v  # B01003_001E
            out[gid] = row
    return out


def parse_geos(path: Path, state: str) -> dict[str, str]:
    """GEO_ID -> NAME for the state (sumlevel 040) and its counties (sumlevel 050)."""
    raw = path.read_bytes().decode("latin-1")
    delim = "|" if raw.split("\n", 1)[0].count("|") > 3 else ","
    names = {}
    for rec in csv.DictReader(io.StringIO(raw), delimiter=delim):
        # 2024+ files call the full id GEO_ID; the 2019 prototype calls it DADSID.
        gid = rec.get("GEO_ID") or rec.get("DADSID") or ""
        if gid == f"0400000US{state}" or gid.startswith(f"0500000US{state}"):
            names[gid] = rec["NAME"]
    return names


def fetch_summary_file(year: int, variables: list[str], state: str, cache_dir: str) -> tuple[list[dict], list[dict], str]:
    """Return (county_rows, state_rows, source_url) shaped like API responses."""
    lay = _layout(year)
    cache = Path(cache_dir) / str(year)
    geos = parse_geos(_download(f"{SF_BASE}/{lay['geos']}", cache / Path(lay["geos"]).name), state)
    wanted = set(geos)
    merged: dict[str, dict[str, str]] = {g: {} for g in wanted}
    for table in sorted({v.split("_")[0] for v in variables}):
        fname = f"acsdt5y{year}-{table.lower()}.dat"
        path = _download(f"{SF_BASE}/{lay['data']}/{fname}", cache / fname)
        with open(path, encoding="latin-1") as f:
            for gid, row in parse_table_file(iter(f), wanted).items():
                merged[gid].update(row)
    counties, states = [], []
    for gid, row in merged.items():
        rec = {"NAME": geos[gid]}
        for v in variables:
            rec[f"{v}E"] = row.get(f"{v}E")
            rec[f"{v}M"] = row.get(f"{v}M")
        if gid.startswith("0500000US"):
            rec.update(state=gid[9:11], county=gid[11:14])
            counties.append(rec)
        else:
            rec.update(state=gid[9:11])
            states.append(rec)
    counties.sort(key=lambda r: r["county"])
    return counties, states, f"{SF_BASE}/{lay['data']}"
