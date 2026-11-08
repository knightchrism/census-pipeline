"""Florida census globe: real export QC, governed questions, and the LLM adapter."""
import json
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from census_globe.classify import NO_DATA, classify
from census_globe.export import ExportError, as_number
from census_globe.llm import MockTransport, OllamaChatTransport, build_transport, load_settings
from census_globe.retrieval import evidence_number_tokens, narrate, retrieve
from census_globe.server import GlobeApp, RateLimiter, make_handler
from census_globe.store import load_store
from census_mcp.metrics import load_metrics


@pytest.fixture(scope="module")
def store():
    return load_store()


def _tokens(text):
    import re
    return set(re.findall(r"\d[\d,]*(?:\.\d+)?", text))


def test_null_is_not_zero_and_sentinels_are_rejected():
    assert as_number(None) is None
    with pytest.raises(ExportError):
        as_number(-555555555)


def test_missing_class_is_not_zero():
    painted = classify({"a": 10, "b": None, "c": 30})
    assert painted["colors"]["b"] == NO_DATA
    assert painted["class_index"]["b"] is None
    assert all(item["min"] != 0 for item in painted["classes"])


def test_export_has_67_counties_and_exact_population_reconciliation(store):
    payload = store.payload
    assert payload["qc"]["issues"] == []
    assert payload["qc"]["county_count"] == 67
    assert payload["years"] == [2019, 2024]
    assert {m["name"] for m in payload["metrics"]} == set(load_metrics()["metrics"])
    for row in payload["state"]["reconciliation"]:
        assert row["county_count"] == 67
        assert row["total_population"] == row["county_sum_total_population"]
        assert row["total_population"] != 0
    assert payload["state"]["reconciliation"][0]["total_population"] == 20901636
    assert payload["state"]["reconciliation"][1]["total_population"] == 22416077
    growth = payload["state"]["growth"]["2019-2024"]["population_growth"]["value"]
    assert round(growth, 4) == 0.0725
    miami = store.county("12086")
    income = miami["estimates"]["2024"]["median_household_income"]
    assert income["value"] == 71753
    assert income["moe"] == 708
    assert round(miami["estimates"]["2024"]["hispanic_share"]["value"], 4) == 0.6932
    assert miami["estimates"]["2024"]["total_population"]["moe"] is None
    assert miami["estimates"]["2024"]["total_population"]["moe_flag"] == -555555555
    ranked = sorted(
        store.counties.values(),
        key=lambda county: county["growth"]["2019-2024"]["population_growth"]["value"],
        reverse=True,
    )
    assert ranked[0]["name"] == "St. Johns County"
    assert round(ranked[0]["growth"]["2019-2024"]["population_growth"]["value"], 4) == 0.2545
    assert payload["state"]["estimates"]["2019"]["median_age"]["value"] == 42
    assert payload["state"]["estimates"]["2024"]["median_age"]["value"] == 42.6


def test_geometry_joins_every_county(store):
    import json
    from census_globe.paths import COUNTIES_PATH
    geo = json.loads(COUNTIES_PATH.read_text())
    assert {feature["properties"]["geoid"] for feature in geo["features"]} == set(store.counties)


def test_question_uses_only_packet_numbers(store):
    packet = retrieve(store, "What is the median household income in Miami-Dade County in 2024?")
    text = narrate(packet)
    assert packet["reading"]["value"] == 71753
    assert "not available" not in text
    extra = _tokens(text) - evidence_number_tokens(packet)
    assert extra == set()
    assert "71,753" in text


def test_fastest_growth_names_st_johns_and_skips_sql(store):
    packet = retrieve(store, "Which counties grew fastest from 2019 to 2024?")
    assert packet["rows"][0]["name"] == "St. Johns County"
    assert packet["excluded_null"] == 0
    text = narrate(packet)
    assert _tokens(text) <= evidence_number_tokens(packet)
    refused = retrieve(store, "select * from main.fct_county_demographics")
    assert refused["tool"] == "clarify"
    assert refused["rows"] == []
    assert "SQL" in narrate(refused)


def test_compare_with_does_not_look_like_sql(store):
    packet = retrieve(store, "Compare Miami-Dade with Orange County on income in 2024")
    assert packet["tool"] == "compare"
    assert {place["name"] for place in packet["places"]} == {"Miami-Dade County", "Orange County"}


def test_missing_value_is_not_narrated_as_zero():
    packet = {
        "tool": "county_metric",
        "place": {"name": "Example County", "kind": "county"},
        "reading": {
            "label": "Median household income", "display": "not available", "period": "ACS 5-year 2024 (2020–2024)",
            "moe_display": None, "uncertainty": "No margin of error is stored for this value.",
            "low_reliability": None, "cv": None, "additive": False, "formula": "B19013_001E",
        },
        "state_reading": None,
        "source": {"program": "American Community Survey 5-year estimates"},
    }
    text = narrate(packet)
    assert "not available" in text
    assert " $0" not in text


def test_llm_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in ("CENSUS_LLM_ENABLED", "CENSUS_LLM_LAB_PROTOCOL", "CENSUS_LLM_LAB_URL", "CENSUS_LLM_LAB_MODEL"):
        monkeypatch.delenv(key, raising=False)
    settings, status = load_settings(tmp_path)
    transport = build_transport(settings, status)
    described = transport.describe()
    assert described["status"] == "disabled"
    assert described["live"] is False
    assert described["contract_verified"] is False
    assert described["missing"]


def test_insecure_env_is_not_read(tmp_path, monkeypatch):
    monkeypatch.delenv("CENSUS_LLM_ENABLED", raising=False)
    monkeypatch.delenv("CENSUS_LLM_LAB_MODEL", raising=False)
    env = tmp_path / ".env"
    env.write_text("CENSUS_LLM_ENABLED=1\nCENSUS_LLM_LAB_MODEL=super-secret-model\n")
    env.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
    settings, status = load_settings(tmp_path)
    assert status == "insecure"
    assert settings["CENSUS_LLM_LAB_MODEL"] == ""
    described = build_transport(settings, status).describe()
    assert described["status"] == "disabled"
    assert "super-secret-model" not in json.dumps(described)
    assert "600" in described["reason"]


def test_external_endpoint_is_refused():
    transport = build_transport({
        "CENSUS_LLM_ENABLED": "1",
        "CENSUS_LLM_LAB_PROTOCOL": "ollama_chat",
        "CENSUS_LLM_LAB_URL": "https://api.example.com",
        "CENSUS_LLM_LAB_MODEL": "some-model",
        "CENSUS_LLM_TIMEOUT_S": "5",
    }, "ok")
    described = transport.describe()
    assert described["live"] is False
    assert described["status"] == "disabled"
    assert "localhost" in described["reason"]


def test_mock_transport_adds_no_figures(store):
    packet = retrieve(store, "What is the median age in Orange County in 2024?")
    governed = narrate(packet)
    text = MockTransport().complete("age", governed, {})
    assert text.startswith("Mock transport, not a model.")
    assert _tokens(text) <= evidence_number_tokens(packet) | _tokens(governed)


def test_ollama_transport_timeout_and_success():
    class Quiet(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            if "slow" in self.path:
                import time
                time.sleep(1.5)
            body = json.dumps({"message": {"content": "Uses only the governed text."}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    try:
        fast = OllamaChatTransport(f"http://127.0.0.1:{port}", "not-a-real-model", 5)
        assert fast.complete("q", "governed", {"value": 1}) == "Uses only the governed text."
        slow = OllamaChatTransport(f"http://127.0.0.1:{port}/slow", "not-a-real-model", 0.2)
        with pytest.raises(Exception, match="timed out"):
            slow.complete("q", "governed", {})
    finally:
        httpd.shutdown()
        httpd.server_close()


def _start(app):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def _get(httpd, path):
    try:
        with urlopen(f"http://127.0.0.1:{httpd.server_address[1]}{path}") as response:
            return response.status, response.headers, json.loads(response.read().decode())
    except HTTPError as exc:
        raw = exc.read().decode()
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"error": raw[:160]}
        return exc.code, exc.headers, body


def _post(httpd, payload):
    data = json.dumps(payload).encode()
    request = Request(
        f"http://127.0.0.1:{httpd.server_address[1]}/api/ask",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request) as response:
            return response.status, json.loads(response.read().decode())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def test_server_layer_headers_and_no_sql(store):
    app = GlobeApp(store=store, transport=build_transport({}, "absent"))
    httpd = _start(app)
    try:
        assert httpd.server_address[0] == "127.0.0.1"
        status, headers, layer = _get(httpd, "/api/layer?metric=total_population&year=2024")
        assert status == 200
        assert len(layer["features"]) == 67
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert "Access-Control-Allow-Origin" not in headers
        assert "script-src 'self'" in headers["Content-Security-Policy"]
        status, body = _post(httpd, {"question": "drop table main.dim_county"})
        assert status == 200
        assert body["llm"]["live"] is False
        assert body["evidence"]["rows"] == []
        status, _, missing = _get(httpd, "/data/QC.md")
        assert status == 404 or missing.get("error")
        code, _headers, blocked = _get(httpd, "/static/../../pyproject.toml")
        assert code == 404 or blocked.get("error")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_ask_rate_limit(store):
    app = GlobeApp(store=store, transport=MockTransport(), limiter=RateLimiter(limit=2, window=60))
    httpd = _start(app)
    try:
        assert _post(httpd, {"question": "What is the median age in Florida in 2024?"})[0] == 200
        assert _post(httpd, {"question": "What is the median age in Florida in 2024?"})[0] == 200
        status, body = _post(httpd, {"question": "What is the median age in Florida in 2024?"})
        assert status == 429
        assert "minute" in body["error"]
        huge = {"question": "x" * 500}
        status, body = _post(httpd, huge)
        assert status in (400, 429)
    finally:
        httpd.shutdown()
        httpd.server_close()
