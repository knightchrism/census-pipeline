"""Thin client for the US Census Data API (https://api.census.gov/data/...).

WHY this module exists: it isolates *all* HTTP concerns (URL building, API key,
retries, error translation, JSON -> rows) from loading. WHAT BREAKS if removed:
nothing can talk to the Census API; ingestion has no data source.
"""
from __future__ import annotations

import logging
import os
import random
import time
from typing import Any

import requests

BASE_URL = "https://api.census.gov/data"
log = logging.getLogger(__name__)

# Status codes worth retrying: rate limiting and transient server errors.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class CensusAPIError(RuntimeError):
    """Raised for non-retryable problems (bad key, bad variable, bad payload)."""


def latest_vintage(dataset: str = "acs/acs5", timeout: int = 60) -> int:
    """Return the newest vintage for a dataset per the API's dataset list (data.json)."""
    resp = requests.get(f"{BASE_URL}.json", timeout=timeout)
    resp.raise_for_status()
    parts = dataset.split("/")
    years = [
        d["c_vintage"]
        for d in resp.json()["dataset"]
        if d.get("c_dataset") == parts and isinstance(d.get("c_vintage"), int)
    ]
    if not years:
        raise CensusAPIError(f"No vintages found for dataset {dataset!r}")
    return max(years)


def parse_payload(payload: Any) -> list[dict[str, str]]:
    """Convert the API's list-of-lists JSON (header row first) to a list of dicts.

    Kept as a pure function so it can be unit-tested without the network.
    """
    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[0], list):
        raise CensusAPIError(f"Unexpected payload shape: {str(payload)[:300]}")
    header, *rows = payload
    for r in rows:
        if len(r) != len(header):
            raise CensusAPIError(f"Row width {len(r)} != header width {len(header)}")
    return [dict(zip(header, r)) for r in rows]


def fetch(
    dataset: str,
    year: int,
    variables: list[str],
    geo_for: str,
    geo_in: str | None = None,
    api_key: str | None = None,
    timeout: int = 60,
    max_retries: int = 5,
    backoff_base: float = 2.0,
    session: requests.Session | None = None,
) -> list[dict[str, str]]:
    """GET one API query with retries + exponential backoff.

    WHY retries: the Census API intermittently returns 5xx/429 under load; without
    retries a whole multi-year load fails on one blip.
    """
    params: dict[str, str] = {"get": ",".join(variables), "for": geo_for}
    if geo_in:
        params["in"] = geo_in
    key = api_key if api_key is not None else os.getenv("CENSUS_API_KEY")
    if key:
        params["key"] = key
    url = f"{BASE_URL}/{year}/{dataset}"
    http = session or requests
    # Never log `params` directly: it contains the API key.
    safe_desc = f"{url} for={geo_for} in={geo_in} vars={len(variables)}"

    for attempt in range(max_retries + 1):
        try:
            # Don't follow redirects: an invalid/missing key redirects to an HTML page.
            resp = http.get(url, params=params, timeout=timeout, allow_redirects=False)
        except requests.RequestException as e:
            if attempt == max_retries:
                raise CensusAPIError(f"Network error after {attempt + 1} attempts: {type(e).__name__}") from None
            _sleep(attempt, backoff_base, f"network error {type(e).__name__}", safe_desc)
            continue

        if resp.headers.get("X-DataWebAPI-KeyError") or resp.status_code in (301, 302):
            raise CensusAPIError(
                f"Census API rejected the API key (HTTP {resp.status_code}). Check CENSUS_API_KEY."
            )
        if resp.status_code in RETRYABLE_STATUS and attempt < max_retries:
            _sleep(attempt, backoff_base, f"HTTP {resp.status_code}", safe_desc)
            continue
        if resp.status_code != 200:
            # 400/204 usually mean an unknown variable or geography for that vintage.
            raise CensusAPIError(f"HTTP {resp.status_code} for {safe_desc}: {resp.text[:300]}")
        try:
            payload = resp.json()
        except ValueError:
            raise CensusAPIError(f"Non-JSON response for {safe_desc}: {resp.text[:300]}") from None
        rows = parse_payload(payload)
        log.info("fetched %d rows: %s (attempt %d)", len(rows), safe_desc, attempt + 1)
        return rows
    raise CensusAPIError("unreachable")  # pragma: no cover


def _sleep(attempt: int, base: float, reason: str, desc: str) -> None:
    delay = base * (2**attempt) + random.uniform(0, 1)
    log.warning("retrying in %.1fs after %s: %s", delay, reason, desc)
    time.sleep(delay)
