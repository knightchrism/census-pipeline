"""Paths for the globe. Data files are committed exports; the warehouse stays gitignored."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "globe" / "data"
STATIC_DIR = ROOT / "globe" / "static"
EXPORT_PATH = DATA_DIR / "florida.json"
COUNTIES_PATH = DATA_DIR / "fl_counties.geojson"
LAND_PATH = DATA_DIR / "ne_110m_land.geojson"
QC_PATH = DATA_DIR / "QC.md"
COUNTY_ZIP = ROOT / "data" / "cache" / "geo" / "cb_2024_us_county_500k.zip"
LAND_ZIP = ROOT / "data" / "cache" / "geo" / "ne_110m_land.zip"
DB_PATH = ROOT / "data" / "census.duckdb"

COUNTY_ZIP_URL = "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_county_500k.zip"
LAND_ZIP_URL = "https://naciscdn.org/naturalearth/110m/physical/ne_110m_land.zip"
USER_AGENT = "GeoKnightGIS-census-pipeline/1.0 (Florida census globe; contact: info@geoknightgis.com)"

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
YEARS = (2019, 2024)
GROWTH_KEY = "2019-2024"
STATE_FIPS = "12"
EXPECTED_COUNTIES = 67
