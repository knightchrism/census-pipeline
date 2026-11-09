"""Local Florida census globe. Binds to 127.0.0.1 only.

The browser can request governed metrics, county comparisons, and a grounded
question. It cannot submit SQL. LLM settings stay on the server.
"""
from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .llm import build_transport, load_settings
from .paths import DEFAULT_PORT, HOST, ROOT, STATIC_DIR
from .retrieval import RetrievalError, narrate, public_evidence, retrieve
from .store import StoreError, data_file, load_store

MAX_BODY = 4096
RATE_LIMIT = 10
RATE_WINDOW = 60.0
GEOID = re.compile(r"^\d{5}$")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' blob:; "
        "worker-src 'self' blob:; style-src 'self'; "
        "img-src 'self' data: blob: https://gibs.earthdata.nasa.gov; "
        "connect-src 'self' https://gibs.earthdata.nasa.gov; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
    ),
    "Cache-Control": "no-store",
}


class RateLimiter:
    def __init__(self, limit: int = RATE_LIMIT, window: float = RATE_WINDOW):
        self.limit = limit
        self.window = window
        self.hits: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            recent = [stamp for stamp in self.hits.get(key, []) if now - stamp < self.window]
            if len(recent) >= self.limit:
                self.hits[key] = recent
                return False
            recent.append(now)
            self.hits[key] = recent
            return True


class GlobeApp:
    def __init__(self, store=None, transport=None, limiter: RateLimiter | None = None):
        self.store = store or load_store()
        if transport is None:
            settings, status = load_settings(ROOT)
            transport = build_transport(settings, status)
        self.transport = transport
        self.limiter = limiter or RateLimiter()

    def ask(self, body: dict) -> dict:
        question = body.get("question", "")
        if not isinstance(question, str):
            raise RetrievalError("Question must be text.")
        year = body.get("year")
        if year is not None and year != "":
            try:
                year = int(year)
            except (TypeError, ValueError) as exc:
                raise RetrievalError("Year must be 2019 or 2024.") from exc
        else:
            year = None
        geoid = _geoid(body.get("geoid"))
        compare = _geoid(body.get("compare_geoid"))
        packet = retrieve(self.store, question, year, geoid, compare)
        answer = narrate(packet)
        info = self.transport.describe()
        model_text = None
        if self.transport.kind != "disabled":
            try:
                model_text = self.transport.complete(question, answer, public_evidence(packet))
                info = dict(info)
                info["answered"] = True
                info["live"] = False
                info["contract_verified"] = False
            except Exception as exc:
                info = dict(info)
                info["status"] = "endpoint_error"
                info["answered"] = False
                info["live"] = False
                info["error"] = str(exc)[:180]
                model_text = None
        return {
            "generator": "governed_template",
            "answer": answer,
            "model_text": model_text,
            "evidence": public_evidence(packet),
            "llm": info,
        }


def _geoid(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not GEOID.match(value):
        raise RetrievalError("County id must be a 5-digit GEOID.")
    return value


def make_handler(app: GlobeApp):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            return

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def _route(self, method: str):
            try:
                parsed = urlparse(self.path)
                path = parsed.path
                if method == "GET" and path in ("/", "/index.html"):
                    self._file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
                elif method == "GET" and path == "/card":
                    self._file(STATIC_DIR / "card.html", "text/html; charset=utf-8")
                elif method == "GET" and path.startswith("/static/"):
                    self._static(path[len("/static/"):])
                elif method == "GET" and path.startswith("/data/"):
                    self._data(path[len("/data/"):])
                elif method == "GET" and path == "/api/meta":
                    body = self_meta(app)
                    self._json(body)
                elif method == "GET" and path == "/api/layer":
                    self._json(self._layer(parse_qs(parsed.query)))
                elif method == "GET" and path == "/api/county":
                    self._json(self._county(parse_qs(parsed.query)))
                elif method == "GET" and path == "/api/compare":
                    self._json(self._compare(parse_qs(parsed.query)))
                elif method == "POST" and path == "/api/ask":
                    self._ask()
                else:
                    self._json({"error": "Not found."}, 404)
            except (RetrievalError, StoreError, ValueError) as exc:
                self._json({"error": str(exc)}, 400)
            except Exception:
                self._json({"error": "The globe could not complete that request."}, 500)

        def _layer(self, query: dict):
            metric = _one(query, "metric") or "total_population"
            year = _year(_one(query, "year"))
            return app.store.layer(metric, year)

        def _county(self, query: dict):
            geoid = _one(query, "geoid")
            if not geoid or not GEOID.match(geoid):
                raise RetrievalError("County id must be a 5-digit GEOID.")
            year = _year(_one(query, "year")) or app.store.years[-1]
            return app.store.county_profile(geoid, int(year))

        def _compare(self, query: dict):
            metric = _one(query, "metric") or "median_household_income"
            year = _year(_one(query, "year")) or app.store.years[-1]
            left = _one(query, "a")
            right = _one(query, "b")
            if not left or not GEOID.match(left) or not right or not GEOID.match(right):
                raise RetrievalError("Choose two county GEOIDs.")
            packet = retrieve(
                app.store,
                _compare_question(app, metric),
                int(year),
                left,
                right,
            )
            return {
                "answer": narrate(packet),
                "evidence": public_evidence(packet),
            }

        def _ask(self):
            client = self.client_address[0]
            if not app.limiter.allow(client):
                self._json({"error": "Too many questions. Wait a minute and try again."}, 429)
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0 or length > MAX_BODY:
                raise RetrievalError("Question payload is empty or too large.")
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RetrievalError("Send a JSON object.") from exc
            if not isinstance(body, dict):
                raise RetrievalError("Send a JSON object.")
            self._json(app.ask(body))

        def _static(self, rel: str):
            path = (STATIC_DIR / rel).resolve()
            if not str(path).startswith(str(STATIC_DIR.resolve())) or not path.is_file():
                self._json({"error": "Not found."}, 404)
                return
            self._file(path, _content_type(path.name))

        def _data(self, name: str):
            try:
                path = data_file(name)
            except StoreError:
                self._json({"error": "Not found."}, 404)
                return
            kind = "application/geo+json" if name.endswith(".geojson") else "application/json"
            self._file(path, kind)

        def _file(self, path, content_type: str):
            data = path.read_bytes()
            self._bytes(data, 200, content_type)

        def _json(self, payload, status: int = 200):
            data = json.dumps(payload).encode("utf-8")
            self._bytes(data, status, "application/json; charset=utf-8")

        def _bytes(self, data: bytes, status: int, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            for key, value in SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

    return Handler


def self_meta(app: GlobeApp) -> dict:
    meta = app.store.meta()
    meta["llm"] = app.transport.describe()
    return meta


def _one(query: dict, key: str) -> str | None:
    values = query.get(key) or []
    return values[0] if values else None


def _year(raw: str | None):
    if raw in (None, ""):
        return None
    return int(raw)


def _compare_question(app: GlobeApp, metric: str) -> str:
    label = app.store.metric(metric)["label"].lower()
    if "growth" in label:
        return f"Compare {label} from 2019 to 2024"
    return f"Compare {label}"


def _content_type(name: str) -> str:
    if name.endswith(".css"):
        return "text/css; charset=utf-8"
    if name.endswith(".js"):
        return "text/javascript; charset=utf-8"
    if name.endswith(".html"):
        return "text/html; charset=utf-8"
    if name.endswith(".txt"):
        return "text/plain; charset=utf-8"
    return "application/octet-stream"


def serve(port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    app = GlobeApp()
    httpd = ThreadingHTTPServer((HOST, port), make_handler(app))
    if httpd.server_address[0] != HOST:
        httpd.server_close()
        raise RuntimeError("refusing to bind anything except 127.0.0.1")
    return httpd


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Serve the Florida census globe on 127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    httpd = serve(args.port)
    print(f"Florida census globe at http://{HOST}:{httpd.server_address[1]}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()


if __name__ == "__main__":
    main()
