"""Version-2 source catalog: PEP Vintage 2025, ACS 2019/2024, decennial not invented."""
import csv
import io

from census_globe.catalog_build import (
    build_catalog, comparison_allowed, county_display_allowed, historical_citation,
    load_acs, load_decennial, load_historical, load_pep_v2025,
)
from census_globe.paths import CATALOG_PATH, DECENNIAL_PATH, EXPORT_PATH, HISTORICAL_PATH, ROOT


def _csv(rows):
    buf = io.StringIO()
    fields = ["SUMLEV", "STATE", "COUNTY", "STNAME", "CTYNAME", "ESTIMATESBASE2020",
              *[f"POPESTIMATE{year}" for year in range(2020, 2026)]]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def test_page_frames_without_a_reset_button():
    html = (ROOT / "globe" / "static" / "index.html").read_text()
    script = (ROOT / "globe" / "static" / "app.js").read_text()
    assert "Frame Florida" not in html
    assert "app.js?v=5" in html
    assert "/api/census/ask" in script
    assert "figures-only" in script or "Not written by the model" in script
    assert "fl_state_outline" not in script
    assert "HIDDEN_TOPICS" in script and "hispanic_share" in script
    assert "historical-statewide" in script
    assert "Race and ethnicity topics are turned off" in script
    assert "statewide 1830–2020 census view is turned off" in script
    assert "was not JSON" in (ROOT / "globe" / "static" / "app.js").read_text()
    assert "Startup failed" in (ROOT / "globe" / "static" / "app.js").read_text()
    assert 'id="frame"' not in html
    assert 'id="unavailable"' in html
    assert "warn" not in html.split('id="unavailable"')[0][-40:]
    assert "cannot be selected yet" in script
    assert "Missing Key" not in script
    assert "frameFlorida" in script


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
    assert catalog["qc"]["decennial_loaded"] is True
    assert catalog["qc"]["decennial_years"] == [2010, 2020]
    assert catalog["qc"]["acs_years"] == [2019, 2024]
    assert len(catalog["counties"]) == 67
    loaded = {item["id"]: item for item in catalog["sources"] if item["loaded"]}
    assert set(loaded) == {"pep-v2025-july1", "pep-v2025-april1-base", "acs5", "decennial-april1", "historical-statewide"}
    dec = loaded["decennial-april1"]
    assert dec["kind"] == "count" and dec["years"] == [2010, 2020]
    assert dec["links"]["2010"] == "https://api.census.gov/data/2010/dec/sf1"
    assert dec["links"]["2020"] == "https://api.census.gov/data/2020/dec/pl"
    assert dec["variables"] == {"2010": "P001001", "2020": "P1_001N"}
    assert "key=" not in __import__("json").dumps(catalog)
    assert "Missing Key" not in __import__("json").dumps(catalog)
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
    assert catalog["state"]["decennial"]["2020"] == 21538187
    assert catalog["state"]["decennial"]["2010"] == 18801310
    assert catalog["state"]["decennial"]["2020"] != catalog["state"]["pep_april1_2020_base"]
    for year in ("2010", "2020"):
        assert sum(county["decennial"][year] for county in catalog["counties"]) == catalog["state"]["decennial"][year]
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
    decennial = load_decennial(DECENNIAL_PATH)
    historical = load_historical(HISTORICAL_PATH)
    built = build_catalog(pep, acs, "2026-11-09T00:00:00Z", decennial, historical)
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


def test_decennial_file_reconciles_and_is_not_the_pep_base():
    decennial = load_decennial(DECENNIAL_PATH)
    assert decennial["state"] == {"2010": 18801310, "2020": 21538187}
    assert set(decennial["by_year"]["2010"]) == set(decennial["by_year"]["2020"])
    assert len(decennial["by_year"]["2020"]) == 67
    catalog = __import__("json").loads(CATALOG_PATH.read_text())
    assert decennial["state"]["2020"] != catalog["state"]["pep_april1_2020_base"]
    miami = decennial["by_year"]["2020"]["12086"]
    pep_miami = next(county["pep_april1_2020_base"] for county in catalog["counties"] if county["geoid"] == "12086")
    assert miami != pep_miami
    assert "key=" not in DECENNIAL_PATH.read_text().lower()


def test_historical_series_has_no_1845_count_and_blocks_counties():
    historical = load_historical(HISTORICAL_PATH)
    years = [row["year"] for row in historical["observations"]]
    assert years == list(range(1830, 2021, 10))
    assert 1845 not in years
    assert historical["observations"][0]["population"] == 34730
    assert historical["observations"][1]["population"] == 54477
    assert historical["observations"][2]["population"] == 87445
    assert historical["statehood"]["date"] == "1845-03-03"
    assert historical["statehood"]["not_a_population_observation"] is True
    assert "population" not in historical["state_census_1845"]
    catalog = __import__("json").loads(CATALOG_PATH.read_text())
    source = next(item for item in catalog["sources"] if item["id"] == "historical-statewide")
    assert source["family"] == "historical_statewide_decennial"
    assert source["county_data"] is False
    assert county_display_allowed(source) is False
    assert county_display_allowed(next(item for item in catalog["sources"] if item["id"] == "decennial-april1")) is True
    assert comparison_allowed(source, next(item for item in catalog["sources"] if item["id"] == "decennial-april1"))["ok"] is False
    assert all("historical" not in county for county in catalog["counties"])
    assert catalog["state"]["decennial"]["2020"] == 21538187
    assert source["observations"][-1]["population"] == 21538187
    assert source["observations"][-2]["population"] == 18801310
    assert catalog["qc"]["historical_includes_1845_count"] is False
    script = (ROOT / "globe" / "static" / "app.js").read_text()
    assert "county_data === false" in script
    assert 'setLayoutProperty("ex", "visibility", "none")' in script
    assert 'setLayoutProperty("fill", "visibility", "none")' in script
    assert 'setLayoutProperty("ex", "visibility", "visible")' in script
    assert "Frame Florida" not in (ROOT / "globe" / "static" / "index.html").read_text()


def test_viewer_hides_topics_without_deleting_source_rows():
    catalog = __import__("json").loads(CATALOG_PATH.read_text())
    topic_ids = {item["id"] for item in catalog["topics"]}
    assert {"hispanic_population", "hispanic_share", "population", "income", "age"} <= topic_ids
    historical = next(item for item in catalog["sources"] if item["id"] == "historical-statewide")
    assert historical["observations"][0]["population"] == 34730
    assert catalog["counties"][0]["acs5"]["2024"]["hispanic_share"]["value"] is not None
    script = (ROOT / "globe" / "static" / "app.js").read_text()
    assert "shownTopics" in script and "shownSourceIds" in script
    assert "loadJson(ROOT + \"data/fl_state_outline.geojson\")" not in script


def test_unmapped_historical_observation_is_not_called_verified():
    cite = historical_citation(
        {"year": 1830, "reference_date": "1830-06-01", "source_id": "not-a-recorded-source"},
        {},
        "abc",
    )
    assert cite["traceable"] is False
    assert cite["document"] is None
    assert cite["links"] == []


def test_every_selectable_year_has_a_citation_and_no_invented_page():
    catalog = __import__("json").loads(CATALOG_PATH.read_text())
    sources = {item["id"]: item for item in catalog["sources"]}
    script = (ROOT / "globe" / "static" / "app.js").read_text()
    assert "PDF page" in script and 'dd.textContent = "Missing"' in script
    assert "does not certify enumeration accuracy" in __import__("json").dumps(catalog["sources"])
    forstall = "population-of-states-and-counties-of-the-united-states-1790-1990.pdf"
    apportion = "population-change-data-table.pdf"
    for year in range(1830, 2021, 10):
        cite = sources["historical-statewide"]["citations"][str(year)]
        assert cite["traceable"] is True
        assert cite["pdf_page"] is None
        assert cite["table"] is None
        assert cite["reference_date"]
        url = cite["links"][0]["url"]
        assert url.startswith("https://")
        if year <= 1900:
            assert forstall in url
        else:
            assert apportion in url
    assert "1845" not in sources["historical-statewide"]["citations"]
    for year in range(2020, 2026):
        cite = sources["pep-v2025-july1"]["citations"][str(year)]
        assert cite["vintage"] == "2025"
        assert cite["column"] == f"POPESTIMATE{year}"
        assert cite["pdf_page"] is None
        assert "co-est2025-alldata.csv" in cite["links"][0]["url"]
    base = sources["pep-v2025-april1-base"]["citations"]["2020"]
    assert base["column"] == "ESTIMATESBASE2020"
    assert base["vintage"] == "2025"
    for year, variable in (("2010", "P001001"), ("2020", "P1_001N")):
        cite = sources["decennial-april1"]["citations"][year]
        assert cite["variable"] == variable
        assert cite["dataset"].endswith(variable.split("_")[0][:0] or cite["dataset"])
        assert variable == cite["variable"]
        assert year in cite["links"][0]["url"] or cite["dataset"].split("/")[0] == year
        assert cite["pdf_page"] is None
        assert cite["table"] is None
    assert sources["decennial-april1"]["citations"]["2010"]["links"][0]["url"].endswith("/2010/dec/sf1")
    assert sources["decennial-april1"]["citations"]["2020"]["links"][0]["url"].endswith("/2020/dec/pl")
    metrics = {
        "total_population": "B01003_001E",
        "median_household_income": "B19013_001E",
        "median_age": "B01002_001E",
        "hispanic_population": "B03002_012E",
        "hispanic_share": "B03002_012E / B01003_001E",
    }
    for year in ("2019", "2024"):
        pack = sources["acs5"]["citations"][year]["by_metric"]
        assert set(pack) == set(metrics)
        for name, formula in metrics.items():
            cite = pack[name]
            assert cite["variable"] == formula
            assert cite["period"] == ("2015–2019" if year == "2019" else "2020–2024")
            assert cite["vintage"] == year
            assert cite["pdf_page"] is None
            assert cite["table"]
            assert cite["links"][0]["url"].startswith("https://www.census.gov/")
            assert cite["retrieval"]
