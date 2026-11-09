"""Read the committed globe export. No database and no SQL."""
from __future__ import annotations

import json
from functools import lru_cache

from .classify import classify
from .format import format_moe, format_value, period_label
from .paths import CATALOG_PATH, COUNTIES_PATH, EXPORT_PATH, LAND_PATH

LAYER_ORDER = (
    "total_population",
    "median_household_income",
    "median_age",
    "hispanic_share",
    "population_growth",
    "median_household_income_growth",
)


class StoreError(LookupError):
    pass


class GlobeStore:
    def __init__(self, payload: dict):
        self.payload = payload
        self.metrics = {m["name"]: m for m in payload["metrics"]}
        self.counties = {c["geoid"]: c for c in payload["counties"]}
        self.years = tuple(payload["years"])

    def metric(self, name: str) -> dict:
        if name not in self.metrics:
            raise StoreError(f"unknown metric {name}")
        return self.metrics[name]

    def county(self, geoid: str) -> dict:
        if geoid not in self.counties:
            raise StoreError(f"unknown county {geoid}")
        return self.counties[geoid]

    def require_year(self, year: int) -> int:
        if int(year) not in self.years:
            raise StoreError(f"year must be one of {list(self.years)}")
        return int(year)

    def _stock(self, holder: dict, year: int, name: str) -> dict:
        block = holder["estimates"].get(str(year))
        if not block or name not in block:
            raise StoreError(f"no {name} for {year}")
        return block

    def _growth(self, holder: dict, name: str) -> dict:
        block = holder["growth"].get(self.payload["growth_window"])
        if not block or name not in block:
            raise StoreError("growth window is not in the export")
        return block

    def reading(self, holder: dict, name: str, year: int | None) -> dict:
        metric = self.metric(name)
        if metric["growth"]:
            block = self._growth(holder, name)
            measure = block[name]
            period = period_label(block["start_year"], block["start_period"]) + " to " + period_label(block["end_year"], block["end_period"])
            year_out = block["end_year"]
        else:
            year = self.require_year(year if year is not None else self.years[-1])
            block = self._stock(holder, year, name)
            measure = block[name]
            period = period_label(year, block["period"])
            year_out = year
        return {
            "metric": name,
            "label": metric["label"],
            "unit": metric["unit"],
            "formula": metric["formula"],
            "additive": metric["additive"],
            "year": year_out,
            "period": period,
            "value": measure["value"],
            "display": format_value(metric["unit"], measure["value"]),
            "moe": measure.get("moe"),
            "moe_display": format_moe(metric["unit"], measure.get("moe")),
            "uncertainty": measure.get("uncertainty"),
            "cv": measure.get("cv"),
            "low_reliability": measure.get("low_reliability"),
            "estimate_flag": measure.get("estimate_flag"),
            "moe_flag": measure.get("moe_flag"),
            "caveats": metric["caveats"] + self.payload.get("default_caveats", []),
        }

    def layer(self, name: str, year: int | None) -> dict:
        metric = self.metric(name)
        if name not in LAYER_ORDER:
            raise StoreError(f"{name} is not a map layer")
        values = {}
        rows = []
        for geoid, county in self.counties.items():
            reading = self.reading(county, name, year)
            values[geoid] = reading["value"]
            rows.append({"geoid": geoid, "name": county["name"], **reading})
        classes = classify(values)
        for row in rows:
            row["color"] = classes["colors"][row["geoid"]]
            row["class_index"] = classes["class_index"][row["geoid"]]
        legend = []
        for item in classes["classes"]:
            legend.append({
                **item,
                "min_display": format_value(metric["unit"], item["min"]),
                "max_display": format_value(metric["unit"], item["max"]),
            })
        state = self.reading(self.payload["state"], name, year)
        sample = rows[0]
        return {
            "metric": name,
            "label": metric["label"],
            "unit": metric["unit"],
            "formula": metric["formula"],
            "additive": metric["additive"],
            "growth": metric["growth"],
            "year": sample["year"],
            "period": sample["period"],
            "state": state,
            "legend": legend,
            "no_data_color": classes["no_data_color"],
            "missing_count": len(classes["missing"]),
            "caveats": metric["caveats"] + self.payload.get("default_caveats", []),
            "features": [{
                "geoid": r["geoid"],
                "name": r["name"],
                "value": r["value"],
                "display": r["display"],
                "moe_display": r["moe_display"],
                "uncertainty": r["uncertainty"],
                "low_reliability": r["low_reliability"],
                "color": r["color"],
                "class_index": r["class_index"],
            } for r in rows],
        }

    def county_profile(self, geoid: str, year: int) -> dict:
        year = self.require_year(year)
        county = self.county(geoid)
        metrics = []
        for name in self.metrics:
            metrics.append(self.reading(county, name, year))
        return {
            "geoid": geoid,
            "name": county["name"],
            "year": year,
            "period": county["estimates"][str(year)]["period"],
            "state": {"name": self.payload["state"]["name"], "metrics": [
                self.reading(self.payload["state"], name, year) for name in self.metrics
            ]},
            "metrics": metrics,
        }

    def meta(self) -> dict:
        return {
            "product": self.payload["product"],
            "generated_at": self.payload["generated_at"],
            "years": list(self.years),
            "growth_window": self.payload["growth_window"],
            "bbox": self.payload["bbox"],
            "county_count": len(self.counties),
            "counties": [{"geoid": c["geoid"], "name": c["name"]} for c in self.payload["counties"]],
            "metrics": [{
                "name": m["name"],
                "label": m["label"],
                "unit": m["unit"],
                "formula": m["formula"],
                "additive": m["additive"],
                "layer": m["layer"],
                "growth": m["growth"],
                "description": m["description"],
            } for m in self.payload["metrics"]],
            "source": {
                "publisher": self.payload["source"]["publisher"],
                "program": self.payload["source"]["program"],
                "access": self.payload["source"]["access"],
                "boundaries": self.payload["source"]["county_boundaries"]["title"],
                "boundary_url": self.payload["source"]["county_boundaries"]["url"],
                "land": self.payload["source"]["context_land"]["title"],
                "endorsement": self.payload["source"]["endorsement"],
            },
            "disclaimer": self.payload["disclaimer"],
            "qc": {
                "county_count": self.payload["qc"]["county_count"],
                "issues": self.payload["qc"]["issues"],
            },
        }


@lru_cache(maxsize=1)
def load_store() -> GlobeStore:
    if not EXPORT_PATH.exists():
        raise StoreError(f"missing {EXPORT_PATH.name}; run census-globe-export")
    return GlobeStore(json.loads(EXPORT_PATH.read_text()))


def data_file(name: str):
    allowed = {
        "florida.json": EXPORT_PATH,
        "fl_counties.geojson": COUNTIES_PATH,
        "ne_110m_land.geojson": LAND_PATH,
        "catalog.json": CATALOG_PATH,
    }
    path = allowed.get(name)
    if path is None or not path.exists():
        raise StoreError("unknown data file")
    return path
