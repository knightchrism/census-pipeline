"""MCP server exposing the modeled Census warehouse to AI agents (stdio transport).

Run: uv run census-mcp

WHY: lets an agent (Claude Desktop, Cursor, ...) explore and query the data
through a small set of safe, logged tools instead of raw database access.

Safety layers (each one exists because the others can fail):
  1. DuckDB opened read_only=True     -> no writes even if SQL validation is bypassed
  2. enable_external_access = false   -> no read_csv('/etc/passwd'), no HTTP, no ATTACH of files
  3. run_query allows one SELECT/WITH statement only, blocks DDL/DML keywords
  4. row cap + timeout                -> a runaway query can't hang the agent
Every tool call is logged to logs/mcp.log (NEVER stdout: stdout is the MCP protocol channel;
printing there corrupts the stream).
"""
from __future__ import annotations

import functools
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

import duckdb
from mcp.server.fastmcp import FastMCP

from .metrics import MetricError, build_metric_sql, load_metrics

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = os.getenv("CENSUS_DUCKDB_PATH", str(ROOT / "data" / "census.duckdb"))
MANIFEST = ROOT / "dbt" / "target" / "manifest.json"
LOG_PATH = Path(os.getenv("CENSUS_MCP_LOG", ROOT / "logs" / "mcp.log"))
MAX_ROWS = int(os.getenv("CENSUS_MCP_MAX_ROWS", "500"))
TIMEOUT_S = float(os.getenv("CENSUS_MCP_TIMEOUT_S", "10"))

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
log = logging.getLogger("census_mcp")
log.setLevel(logging.INFO)
if not log.handlers:
    _h = logging.FileHandler(LOG_PATH)
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(_h)
    log.propagate = False

mcp = FastMCP("census-data")


def logged(fn):
    """Decorator: log tool name, args, duration, row count / error for every call."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        t0 = time.perf_counter()
        try:
            out = fn(*args, **kwargs)
            n = len(out.get("rows", [])) if isinstance(out, dict) else (len(out) if isinstance(out, list) else None)
            log.info("tool=%s args=%s ok rows=%s ms=%.0f", fn.__name__, json.dumps(kwargs, default=str), n,
                     (time.perf_counter() - t0) * 1000)
            return out
        except Exception as e:
            log.warning("tool=%s args=%s error=%s ms=%.0f", fn.__name__, json.dumps(kwargs, default=str),
                        e, (time.perf_counter() - t0) * 1000)
            raise
    return wrapper


def _connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(DB_PATH, read_only=True, config={"enable_external_access": False})


# ---------- SQL guard (pure function, unit tested) ----------
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|drop|create|alter|attach|detach|copy|export|import|install|load|"
    r"pragma|set|reset|call|checkpoint|vacuum|truncate|grant|revoke|begin|commit|rollback|use)\b",
    re.IGNORECASE,
)
FORBIDDEN_FUNCS = re.compile(r"\b(read_\w+|glob|getenv|sniff_csv|query_table|query)\s*\(", re.IGNORECASE)


class UnsafeQueryError(ValueError):
    pass


def check_sql(sql: str) -> str:
    """Return a normalized single statement or raise UnsafeQueryError."""
    s = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.S).strip().rstrip(";").strip()
    if not s:
        raise UnsafeQueryError("empty query")
    # strip string literals before scanning so 'drop' inside a string is allowed
    scan = re.sub(r"'(?:[^']|'')*'", "''", s)
    if ";" in scan:
        raise UnsafeQueryError("only one statement allowed")
    if not re.match(r"^(select|with)\b", scan, re.IGNORECASE):
        raise UnsafeQueryError("only SELECT / WITH queries are allowed")
    m = FORBIDDEN.search(scan) or FORBIDDEN_FUNCS.search(scan)
    if m:
        raise UnsafeQueryError(f"forbidden keyword: {m.group(0).strip()}")
    return s


def _execute(sql: str, params: list | None = None, max_rows: int = MAX_ROWS) -> dict:
    con = _connect()
    timer = threading.Timer(TIMEOUT_S, con.interrupt)  # cancels the query if it runs too long
    timer.start()
    try:
        cur = con.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        data = cur.fetchmany(max_rows + 1)
    except duckdb.InterruptException:
        raise TimeoutError(f"query exceeded {TIMEOUT_S}s timeout") from None
    finally:
        timer.cancel()
        con.close()
    rows = [dict(zip(cols, [v if isinstance(v, (int, float, str, bool, type(None))) else str(v) for v in r]))
            for r in data[:max_rows]]
    return {"columns": cols, "rows": rows, "row_count": len(rows), "truncated": len(data) > max_rows}


def _dbt_docs() -> dict:
    if not MANIFEST.exists():
        return {}
    nodes = json.loads(MANIFEST.read_text())["nodes"]
    return {n["name"]: n for n in nodes.values() if n["resource_type"] == "model"}


# ---------- tools ----------
@mcp.tool()
@logged
def list_tables() -> list[dict]:
    """List schemas/tables in the warehouse. Query `main.*` marts; `raw.*` is unmodeled API data."""
    docs = _dbt_docs()
    res = _execute("SELECT table_schema, table_name, table_type FROM information_schema.tables "
                   "WHERE table_schema NOT IN ('information_schema','pg_catalog') ORDER BY 1, 2")
    return [{"schema": r["table_schema"], "table": r["table_name"], "type": r["table_type"],
             "description": (docs.get(r["table_name"], {}).get("description") or "").strip()}
            for r in res["rows"]]


@mcp.tool()
@logged
def describe_table(table: str) -> dict:
    """Columns, types and dbt documentation for a table, e.g. 'fct_county_demographics' or 'main.dim_county'."""
    schema, _, name = table.rpartition(".")
    schema = schema or "main"
    res = _execute("SELECT column_name, data_type FROM information_schema.columns "
                   "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position", [schema, name])
    if not res["rows"]:
        raise ValueError(f"table {schema}.{name} not found; call list_tables")
    doc = _dbt_docs().get(name, {})
    cdocs = doc.get("columns", {})
    return {"table": f"{schema}.{name}", "description": (doc.get("description") or "").strip(),
            "columns": [{"name": r["column_name"], "type": r["data_type"],
                         "description": cdocs.get(r["column_name"], {}).get("description", "")}
                        for r in res["rows"]]}


@mcp.tool()
@logged
def run_query(sql: str, max_rows: int = 100) -> dict:
    """Run ONE read-only SELECT/WITH query. Results capped (default 100, max 500 rows), 10s timeout."""
    safe = check_sql(sql)
    return _execute(safe, max_rows=max(1, min(int(max_rows), MAX_ROWS)))


@mcp.tool()
@logged
def list_metrics() -> list[dict]:
    """List governed metrics (name, label, unit, description, supported group_by)."""
    spec = load_metrics()
    return [{"name": k, "label": m["label"], "unit": m["unit"], "description": m["description"],
             "additive": m["additive"], "group_by": list(m["sources"])} for k, m in spec["metrics"].items()]


@mcp.tool()
@logged
def get_metric(name: str, group_by: str = "county", year: int | None = None, county: str | None = None) -> dict:
    """Compute a governed metric. group_by: 'county' or 'state'. year: ACS vintage (end year for growth
    metrics). county: FIPS geoid or name (e.g. 'Orange'). Returns rows, the SQL used, and caveats."""
    spec = load_metrics()
    try:
        sql, params = build_metric_sql(spec, name, group_by, year, county)
    except MetricError as e:
        raise ValueError(str(e)) from None
    res = _execute(sql, params)
    m = spec["metrics"][name]
    return {"metric": name, "label": m["label"], "unit": m["unit"], "formula": m["formula"],
            "sql": sql, "params": params, **res,
            "caveats": m.get("caveats", []) + spec.get("default_caveats", [])}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
