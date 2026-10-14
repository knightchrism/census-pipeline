"""Write validated API rows into DuckDB raw tables.

WHY raw tables keep everything as VARCHAR: the raw layer is an exact copy of
what the API said. Typing and sentinel cleanup belong in dbt staging, where
they are versioned, tested and documented. WHAT BREAKS if removed: dbt has no
source tables to build from.

Idempotency: each load DELETEs the rows for (year, state) and re-INSERTs them
inside one transaction, so re-running a year never duplicates data.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb

# Load metadata columns appended to every raw row (lineage / freshness).
META_COLS = ["_dataset", "_year", "_state_fips", "_load_id", "_loaded_at", "_source_url"]


def load_raw(
    rows: list[dict],
    db_path: str,
    schema: str,
    table: str,
    columns: list[str],
    dataset: str,
    year: int,
    state_fips: str,
    source_url: str,
) -> int:
    if not rows:
        raise ValueError("No rows to load")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    load_id = str(uuid.uuid4())
    loaded_at = datetime.now(timezone.utc).replace(tzinfo=None)  # stored as naive UTC
    con = duckdb.connect(db_path)
    try:
        con.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        col_ddl = ", ".join(f'"{c}" VARCHAR' for c in columns)
        con.execute(
            f'CREATE TABLE IF NOT EXISTS "{schema}"."{table}" ({col_ddl}, '
            "_dataset VARCHAR, _year INTEGER, _state_fips VARCHAR, _load_id VARCHAR, "
            "_loaded_at TIMESTAMP, _source_url VARCHAR)"
        )
        # If config gained a new variable, add the column instead of failing.
        existing = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=? AND table_name=?",
            [schema, table]).fetchall()}
        for c in columns:
            if c not in existing:
                con.execute(f'ALTER TABLE "{schema}"."{table}" ADD COLUMN "{c}" VARCHAR')

        names = ", ".join(f'"{c}"' for c in columns + META_COLS)
        placeholders = ", ".join(["?"] * (len(columns) + len(META_COLS)))
        con.execute("BEGIN")
        con.execute(f'DELETE FROM "{schema}"."{table}" WHERE _year = ? AND _state_fips = ?', [year, state_fips])
        con.executemany(
            f'INSERT INTO "{schema}"."{table}" ({names}) VALUES ({placeholders})',
            [[r.get(c) for c in columns] + [dataset, year, state_fips, load_id, loaded_at, source_url]
             for r in rows],
        )
        con.execute("COMMIT")
        return con.execute(
            f'SELECT count(*) FROM "{schema}"."{table}" WHERE _year = ? AND _state_fips = ?',
            [year, state_fips]).fetchone()[0]
    except Exception:
        try:
            con.execute("ROLLBACK")
        except duckdb.Error:
            pass
        raise
    finally:
        con.close()
