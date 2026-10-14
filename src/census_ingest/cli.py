"""Entry point: `uv run census-ingest [--config config.yaml] [--years 2019 2024]`.

WHY: orchestrates config -> fetch -> validate -> load for every (year, state),
with logging to logs/ingest.log. WHAT BREAKS if removed: no way to run ingestion.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from .client import BASE_URL, CensusAPIError, fetch
from .load import load_raw
from .summary_file import fetch_summary_file
from .validate import ValidationError, validate_rows

log = logging.getLogger("census_ingest")


def setup_logging(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in (logging.FileHandler(path), logging.StreamHandler(sys.stderr)):
        h.setFormatter(fmt)
        root.addHandler(h)
    # urllib3 debug logs would include full URLs (with the key) - keep them quiet.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def request_columns(cfg: dict) -> tuple[list[str], list[str]]:
    """Return (API 'get' list, numeric column list): NAME + each var's E and M."""
    numeric = [f"{v}{s}" for v in cfg["variables"] for s in ("E", "M")]
    return ["NAME", *numeric], numeric


def ingest(cfg: dict, years: list[int], db: str, source: str = "api") -> dict:
    get_cols, numeric = request_columns(cfg)
    http = cfg.get("http", {})
    kw = dict(timeout=http.get("timeout_seconds", 60), max_retries=http.get("max_retries", 5),
              backoff_base=http.get("backoff_base_seconds", 2))
    min_rate = cfg.get("validation", {}).get("min_numeric_parse_rate", 0.95)
    expected = cfg["geography"].get("expected_counties") or {}
    results = {}
    for year in years:
        for st in cfg["geography"]["states"]:
            st = str(st)
            # Two geographies per (year, state): all counties, and the state total itself
            # (the state row is the official estimate we reconcile county sums against).
            if source == "summary_file":
                c_rows, s_rows, url = fetch_summary_file(year, list(cfg["variables"]), st,
                                                         cfg.get("summary_file_cache", "data/cache"))
                fetched = {"acs5_county": (c_rows, url), "acs5_state": (s_rows, url)}
            else:
                url = f"{BASE_URL}/{year}/{cfg['dataset']}"
                fetched = {
                    "acs5_county": (fetch(cfg["dataset"], year, get_cols, "county:*", f"state:{st}", **kw),
                                    f"{url}?for=county:*&in=state:{st}"),
                    "acs5_state": (fetch(cfg["dataset"], year, get_cols, f"state:{st}", None, **kw),
                                   f"{url}?for=state:{st}"),
                }
            jobs = [("acs5_county", ["state", "county"], expected.get(st)),
                    ("acs5_state", ["state"], 1)]
            for table, geoid_cols, exp in jobs:
                rows, src_url = fetched[table]
                rep = validate_rows(rows, get_cols + geoid_cols, geoid_cols, numeric, exp, min_rate)
                n = load_raw(rows, db, cfg["raw_schema"], table, get_cols + geoid_cols,
                             cfg["dataset"], year, st, src_url)
                log.info("loaded %s year=%s state=%s rows=%d parse_rate=%.4f issues=%s",
                         table, year, st, n, rep.numeric_parse_rate, rep.issues)
                results[(table, year, st)] = n
    return results


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Load ACS data from the Census API into DuckDB")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--years", type=int, nargs="*", help="override config years")
    ap.add_argument("--source", choices=["api", "summary_file"], default=None,
                    help="api (default, needs CENSUS_API_KEY) or summary_file (bulk files, no key)")
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(open(args.config))
    setup_logging(cfg.get("log_path", "logs/ingest.log"))
    log.info("CENSUS_API_KEY set: %s", bool(os.getenv("CENSUS_API_KEY")))  # never the value
    db = os.getenv("CENSUS_DUCKDB_PATH", cfg["duckdb_path"])
    years = args.years or [int(y) for y in cfg["years"]]
    try:
        res = ingest(cfg, years, db, args.source or cfg.get("source", "api"))
    except (CensusAPIError, ValidationError, ValueError, OSError) as e:
        log.error("ingest failed: %s", e)
        sys.exit(1)
    log.info("done: %d loads into %s", len(res), db)


if __name__ == "__main__":
    main()
