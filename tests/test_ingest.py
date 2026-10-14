"""Ingestion tests: pure parsing/validation (no network) + an idempotent load."""
import duckdb
import pytest
import requests

from census_ingest import client
from census_ingest.client import CensusAPIError, parse_payload
from census_ingest.load import load_raw
from census_ingest.summary_file import parse_table_file
from census_ingest.validate import ValidationError, validate_rows

PAYLOAD = [["NAME", "B01003_001E", "B01003_001M", "state", "county"],
           ["Alachua County, Florida", "284030", "-555555555", "12", "001"],
           ["Baker County, Florida", "28259", "-555555555", "12", "003"]]
COLS = ["NAME", "B01003_001E", "B01003_001M", "state", "county"]


def test_parse_payload_header_to_dicts():
    rows = parse_payload(PAYLOAD)
    assert rows[0] == {"NAME": "Alachua County, Florida", "B01003_001E": "284030",
                       "B01003_001M": "-555555555", "state": "12", "county": "001"}


@pytest.mark.parametrize("bad", [None, [], [["NAME"]], {"error": "x"}, [["a", "b"], ["1"]]])
def test_parse_payload_rejects_bad_shapes(bad):
    with pytest.raises(CensusAPIError):
        parse_payload(bad)


def test_summary_file_columns_renamed_to_api_style():
    lines = iter(["GEO_ID|B19013_E001|B19013_M001\n", "0500000US12001|60000|900\n", "0500000US13001|1|1\n"])
    out = parse_table_file(lines, {"0500000US12001"})
    assert out == {"0500000US12001": {"B19013_001E": "60000", "B19013_001M": "900"}}


def test_validate_ok_and_counts():
    rep = validate_rows(parse_payload(PAYLOAD), COLS, ["state", "county"], ["B01003_001E", "B01003_001M"], 2)
    assert rep.row_count == 2 and rep.numeric_parse_rate == 1.0  # sentinels count as numeric


def test_validate_wrong_count():
    with pytest.raises(ValidationError, match="expected 67"):
        validate_rows(parse_payload(PAYLOAD), COLS, ["state", "county"], [], 67)


def test_validate_duplicates_and_missing_cols():
    rows = parse_payload(PAYLOAD + [PAYLOAD[1]])
    with pytest.raises(ValidationError, match="duplicate"):
        validate_rows(rows, COLS, ["state", "county"], [])
    with pytest.raises(ValidationError, match="missing"):
        validate_rows(rows, COLS + ["B19013_001E"], ["state", "county"], [])


def test_validate_parse_rate():
    rows = parse_payload(PAYLOAD)
    rows[0]["B01003_001E"] = "N/A"
    with pytest.raises(ValidationError, match="parse rate"):
        validate_rows(rows, COLS, ["state", "county"], ["B01003_001E"], min_parse_rate=0.9)


def test_load_is_idempotent(tmp_path):
    db = str(tmp_path / "t.duckdb")
    rows = parse_payload(PAYLOAD)
    for _ in range(3):
        n = load_raw(rows, db, "raw", "acs5_county", COLS, "acs/acs5", 2024, "12", "u")
    assert n == 2
    load_raw(rows, db, "raw", "acs5_county", COLS, "acs/acs5", 2019, "12", "u")
    with duckdb.connect(db) as con:
        assert con.sql("select count(*) from raw.acs5_county").fetchone()[0] == 4


class FakeResp:
    def __init__(self, status, payload=None):
        self.status_code, self._p, self.headers, self.text = status, payload, {}, ""

    def json(self):
        return self._p


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), 0

    def get(self, *a, **k):
        self.calls += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_fetch_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(client.time, "sleep", lambda s: None)
    s = FakeSession([FakeResp(503), requests.ConnectionError(), FakeResp(200, PAYLOAD)])
    rows = client.fetch("acs/acs5", 2024, COLS[:3], "county:*", "state:12", api_key="", session=s)
    assert len(rows) == 2 and s.calls == 3


def test_fetch_bad_key_not_retried():
    r = FakeResp(302)
    r.headers = {"X-DataWebAPI-KeyError": "1"}
    s = FakeSession([r])
    with pytest.raises(CensusAPIError, match="key"):
        client.fetch("acs/acs5", 2024, COLS[:3], "state:12", api_key="x", session=s)
    assert s.calls == 1
