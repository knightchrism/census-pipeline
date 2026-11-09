"""Version-2 source catalog: PEP Vintage 2025, ACS 2019/2024, decennial not invented."""
import csv
import io

from census_globe.catalog_build import build_catalog, comparison_allowed, load_acs, load_pep_v2025
from census_globe.paths import CATALOG_PATH, EXPORT_PATH, ROOT


def _csv(rows):
    buf = io.StringIO()
    fields = ["SUMLEV", "STATE", "COUNTY", "STNAME", "CTYNAME", "ESTIMATESBASE2020",
              *[f"POPESTIMATE{year}" for year in range(2020, 2026)]]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def test_comparison_blocks_different_families():
    pep = {"id": "pep-v2025-july1", "loaded": True, "family": "pep_july1_v2025", "label": "PEP Vintage 2025"}
    acs = {"id": "acs5", "loaded": True, "family": "acs5", "label": "ACS 5-year"}
    base = {"id": "pep-v2025-april1-base", "loaded": True, "family": "pep_april1_base_v2025", "label": "April 1 base"}
    blocked = comparison_allowed(pep, acs)
    assert blocked["ok"] is False
    assert comparison_allowed(pep, base)["ok"] is False
    assert comparison_allowed(pep, pep)["ok"] is True
    assert comparison_allowed({"loaded": False, "family": "x", "id": "x", "label": "x"}, pep)["ok"] is False


def test_committed_catalog_is_one_pep_vintage_and_reconciles():
    catalog = __import__("json").loads(CATALOG_PATH.read_text())
    assert catalog["schema_version"] == 2
    assert catalog["qc"]["pep_vintages_mixed"] is False
    assert catalog["qc"]["decennial_loaded"] is False
    assert catalog["qc"]["acs_years"] == [2019, 2024]
    assert len(catalog["counties"]) == 67
    loaded = {item["id"]: item for item in catalog["sources"] if item["loaded"]}
    assert set(loaded) == {"pep-v2025-july1", "pep-v2025-april1-base", "acs5"}
    dec = next(item for item in catalog["sources"] if item["id"] == "decennial-april1")
    assert dec["loaded"] is False and "Missing Key" in dec["reason"]
    july = loaded["pep-v2025-july1"]
    assert july["years"] == [2020, 2021, 2022, 2023, 2024, 2025]
    assert july["reference_date"] == "July 1" and july["vintage"] == 2025
    assert "co-est2025-alldata.csv" in july["links"]["file"]
    for year in july["years"]:
        state = catalog["state"]["pep_july1"][str(year)]
        total = sum(county["pep_july1"][str(year)] for county in catalog["counties"])
        assert total == state
    assert catalog["state"]["pep_april1_2020_base"] == sum(
        county["pep_april1_2020_base"] for county in catalog["counties"]
    )
    assert catalog["state"]["pep_april1_2020_base"] != catalog["state"]["pep_july1"]["2020"]
    miami = next(county for county in catalog["counties"] if county["geoid"] == "12086")
    assert miami["acs5"]["2024"]["median_household_income"]["value"] == 71753
    assert miami["acs5"]["2024"]["median_household_income"]["moe"] == 708
    assert miami["acs5"]["2024"]["total_population"]["moe"] is None
    assert miami["pep_july1"]["2024"] != miami["acs5"]["2024"]["total_population"]["value"]
    # Vintage 2024's published July 1, 2024 Miami-Dade figure is not this series.
    assert miami["pep_july1"]["2024"] != 2838461


def test_real_pep_file_matches_catalog_when_cached():
    path = ROOT / "data" / "cache" / "sources" / "co-est2025-alldata.csv"
    if not path.exists():
        return
    pep = load_pep_v2025(path)
    acs = load_acs(EXPORT_PATH)
    built = build_catalog(pep, acs, "2026-11-09T00:00:00Z")
    committed = __import__("json").loads(CATALOG_PATH.read_text())
    assert built["state"]["pep_july1"] == committed["state"]["pep_july1"]
    assert built["counties"][0]["pep_july1"] == committed["counties"][0]["pep_july1"]
    v2024 = ROOT / "data" / "cache" / "sources" / "co-est2024-alldata.csv"
    if v2024.exists():
        import csv
        with v2024.open(newline="", encoding="latin-1") as handle:
            old = next(
                int(row["POPESTIMATE2024"])
                for row in csv.DictReader(handle)
                if row["STATE"] == "12" and row["SUMLEV"] == "050" and row["CTYNAME"] == "Miami-Dade County"
            )
        miami = next(county for county in committed["counties"] if county["geoid"] == "12086")
        assert miami["pep_july1"]["2024"] != old
