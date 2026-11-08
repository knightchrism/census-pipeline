"""Build GeoJSON from official boundary files.

County lines are the Census Bureau 2024 cartographic boundary (500k), Florida only.
World land is Natural Earth 1:110m and is context for the globe, not a census geography.
Coordinates are rounded for the web file. The join key is the 5-digit county GEOID.
"""
from __future__ import annotations

import json
import tempfile
import time
import zipfile
from pathlib import Path

from .paths import COUNTY_ZIP, COUNTY_ZIP_URL, LAND_ZIP, LAND_ZIP_URL, USER_AGENT


class GeometryError(RuntimeError):
    pass


def _reader(zip_path: Path, stem: str):
    try:
        import shapefile
    except ImportError as e:
        raise GeometryError(
            "pyshp is required to rebuild geometry. Install the dev dependency group and rerun."
        ) from e
    if not zip_path.exists():
        raise GeometryError(f"missing {zip_path.name}")
    tmp = Path(tempfile.mkdtemp(prefix="census-globe-"))
    with zipfile.ZipFile(zip_path) as zipped:
        names = set(zipped.namelist())
        for ext in (".shp", ".shx", ".dbf", ".cpg"):
            name = stem + ext
            if name not in names:
                if ext == ".cpg":
                    continue
                raise GeometryError(f"{zip_path.name} has no {name}")
            (tmp / name).write_bytes(zipped.read(name))
    return shapefile.Reader(str(tmp / stem))


def _round_coords(coords, digits: int):
    if not coords:
        return coords
    if isinstance(coords[0], (int, float)):
        return [round(float(coords[0]), digits), round(float(coords[1]), digits)]
    return [_round_coords(part, digits) for part in coords]


def _round_geom(geom: dict, digits: int) -> dict:
    return {"type": geom["type"], "coordinates": _round_coords(geom["coordinates"], digits)}


def florida_counties(zip_path: Path = COUNTY_ZIP) -> dict:
    reader = _reader(zip_path, "cb_2024_us_county_500k")
    features = []
    try:
        for shape_record in reader.iterShapeRecords():
            rec = shape_record.record
            if rec["STATEFP"] != "12":
                continue
            geom = shape_record.shape.__geo_interface__
            features.append({
                "type": "Feature",
                "properties": {"geoid": rec["GEOID"], "name": rec["NAMELSAD"]},
                "geometry": _round_geom(geom, 5),
            })
    finally:
        reader.close()
    features.sort(key=lambda f: f["properties"]["geoid"])
    return {
        "type": "FeatureCollection",
        "name": "cb_2024_us_county_500k",
        "source": COUNTY_ZIP_URL,
        "features": features,
    }


def natural_earth_land(zip_path: Path = LAND_ZIP) -> dict:
    reader = _reader(zip_path, "ne_110m_land")
    features = []
    try:
        for shape_record in reader.iterShapeRecords():
            geom = shape_record.shape.__geo_interface__
            features.append({
                "type": "Feature",
                "properties": {},
                "geometry": _round_geom(geom, 2),
            })
    finally:
        reader.close()
    return {
        "type": "FeatureCollection",
        "name": "ne_110m_land",
        "source": LAND_ZIP_URL,
        "features": features,
    }


def bbox(collection: dict) -> list[float]:
    xs: list[float] = []
    ys: list[float] = []

    def walk(coords):
        if not coords:
            return
        if isinstance(coords[0], (int, float)):
            xs.append(coords[0])
            ys.append(coords[1])
            return
        for part in coords:
            walk(part)

    for feature in collection["features"]:
        walk(feature["geometry"]["coordinates"])
    if not xs:
        raise GeometryError("geometry has no coordinates")
    return [min(xs), min(ys), max(xs), max(ys)]


def download_sources() -> None:
    """Download the two boundary zips with an honest user agent.

    These are published data files, not HTML pages. Requests are spaced by 20 seconds.
    A non-200 response stops the download. Nothing here retries through a proxy.
    """
    import requests

    jobs = [(COUNTY_ZIP_URL, COUNTY_ZIP), (LAND_ZIP_URL, LAND_ZIP)]
    for index, (url, dest) in enumerate(jobs):
        if dest.exists() and dest.stat().st_size > 0:
            continue
        if index:
            time.sleep(20)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=120, headers={"User-Agent": USER_AGENT}, allow_redirects=False) as resp:
            if resp.status_code != 200:
                raise GeometryError(f"download stopped at HTTP {resp.status_code} for {url}")
            tmp = dest.with_suffix(".part")
            with open(tmp, "wb") as handle:
                for chunk in resp.iter_content(1 << 20):
                    if chunk:
                        handle.write(chunk)
            tmp.replace(dest)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
