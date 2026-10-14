"""MCP safety: the SQL guard and the read-only connection."""
import pytest

from census_mcp import server
from census_mcp.server import UnsafeQueryError, check_sql


@pytest.mark.parametrize("sql", [
    "SELECT 1", "with a as (select 1 x) select * from a", "select 'drop table x' as s;",
    "-- comment\nselect loaded_at, load_id from main.fct_county_demographics",
])
def test_allowed(sql):
    assert check_sql(sql)


@pytest.mark.parametrize("sql", [
    "", "drop table main.dim_county", "select 1; drop table x", "insert into t values (1)",
    "select * from read_csv('/etc/passwd')", "attach 'x.db'", "copy (select 1) to 'x.csv'",
    "pragma version", "/* x */ delete from t", "select getenv('CENSUS_API_KEY')",
    "with x as (select 1) update t set a=1", "install httpfs",
])
def test_blocked(sql):
    with pytest.raises(UnsafeQueryError):
        check_sql(sql)


def test_connection_is_read_only(tmp_path, monkeypatch):
    import duckdb
    db = tmp_path / "w.duckdb"
    duckdb.connect(str(db)).execute("create table t as select 1 a").close()
    monkeypatch.setattr(server, "DB_PATH", str(db))
    con = server._connect()
    with pytest.raises(duckdb.Error):
        con.execute("insert into t values (2)")  # even if the regex guard were bypassed
    with pytest.raises(duckdb.Error):
        con.execute("select * from read_csv('/etc/hostname')")
    con.close()


def test_row_cap(tmp_path, monkeypatch):
    import duckdb
    db = tmp_path / "w.duckdb"
    duckdb.connect(str(db)).close()
    monkeypatch.setattr(server, "DB_PATH", str(db))
    out = server.run_query(sql="select * from range(1000)", max_rows=5)
    assert out["row_count"] == 5 and out["truncated"] is True


def test_timeout(tmp_path, monkeypatch):
    import duckdb
    db = tmp_path / "w.duckdb"
    duckdb.connect(str(db)).close()
    monkeypatch.setattr(server, "DB_PATH", str(db))
    monkeypatch.setattr(server, "TIMEOUT_S", 0.2)
    with pytest.raises(TimeoutError):
        server.run_query(sql="select count(*) from range(10000000000) a", max_rows=1)
