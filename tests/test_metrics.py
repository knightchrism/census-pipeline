"""Metrics: the governed YAML is well-formed and SQL generation is safe/correct."""
import pytest

from census_mcp.metrics import MetricError, build_metric_sql, load_metrics

SPEC = load_metrics()


def test_every_metric_has_required_fields():
    for name, m in SPEC["metrics"].items():
        for key in ("label", "description", "unit", "formula", "additive", "sources"):
            assert key in m, (name, key)
        assert set(m["sources"]) <= {"county", "state"}
        for src in m["sources"].values():
            assert src["table"].startswith("main.") and src["expression"] and src["year_column"]


def test_county_sql_uses_bound_params():
    sql, params = build_metric_sql(SPEC, "median_household_income", "county", 2024, "Orange")
    assert "?" in sql and "Orange" not in sql
    assert params == [2024, "Orange", "Orange", "Orange"]


def test_growth_metric_applies_non_overlap_filter():
    sql, _ = build_metric_sql(SPEC, "population_growth", "state")
    assert "windows_non_overlapping" in sql and "start_year" in sql


@pytest.mark.parametrize("args", [("nope", "county"), ("total_population", "tract"),
                                  ("total_population", "state", None, "Orange")])
def test_bad_requests(args):
    with pytest.raises(MetricError):
        build_metric_sql(SPEC, *args)


def test_medians_are_flagged_non_additive():
    assert SPEC["metrics"]["median_household_income"]["additive"] is False
    assert SPEC["metrics"]["total_population"]["additive"] is True


def test_metrics_run_against_warehouse_if_present():
    """Integration: every metric/group_by executes against the real DuckDB (skipped if not built)."""
    from census_mcp import server
    from pathlib import Path
    if not Path(server.DB_PATH).exists():
        pytest.skip("warehouse not built")
    for name, m in SPEC["metrics"].items():
        for gb in m["sources"]:
            out = server.get_metric(name=name, group_by=gb)
            assert out["row_count"] > 0, (name, gb)
