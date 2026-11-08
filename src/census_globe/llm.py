"""Language-model adapter for the globe.

The default is disabled. This repository does not contain the local LLM Lab's
URL, authentication, model id, or request schema, so the adapter will not claim
a live Lab connection.

Optional protocols, both off unless the environment says so:

- mock: deterministic test transport, not a model
- ollama_chat: HTTP POST to a localhost Ollama /api/chat endpoint

ollama_chat is the shape used by other local GeoKnight tools. It is not verified
as the LLM Lab contract. External and paid endpoints are refused.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

from .paths import USER_AGENT

SETTING_KEYS = (
    "CENSUS_LLM_ENABLED",
    "CENSUS_LLM_LAB_PROTOCOL",
    "CENSUS_LLM_LAB_URL",
    "CENSUS_LLM_LAB_MODEL",
    "CENSUS_LLM_TIMEOUT_S",
)

MISSING = [
    "LLM Lab base URL",
    "LLM Lab request and response schema",
    "LLM Lab model id",
    "LLM Lab authentication scheme, if it uses one",
]


class LlmError(RuntimeError):
    pass


class LlmDisabled(LlmError):
    pass


def load_settings(root: Path) -> tuple[dict, str]:
    """Process environment wins. A .env that is not mode 600 is not read."""
    settings = {key: os.environ.get(key, "").strip() for key in SETTING_KEYS}
    path = root / ".env"
    if not path.exists():
        return settings, "absent"
    if path.stat().st_mode & 0o077:
        return settings, "insecure"
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in SETTING_KEYS and not settings[key]:
            settings[key] = value.strip().strip('"').strip("'")
    return settings, "ok"


class DisabledTransport:
    kind = "disabled"

    def __init__(self, reason: str, missing: list[str] | None = None):
        self.reason = reason
        self.missing = list(missing or MISSING)

    def describe(self) -> dict:
        return {
            "status": "disabled",
            "live": False,
            "contract_verified": False,
            "answered": False,
            "reason": self.reason,
            "missing": self.missing,
        }

    def complete(self, question: str, governed_text: str, evidence: dict) -> str:
        raise LlmDisabled(self.reason)


class MockTransport:
    """Repeats the governed text. Used by tests. It is not a model and adds no figures."""

    kind = "mock"

    def describe(self) -> dict:
        return {
            "status": "mock",
            "live": False,
            "contract_verified": False,
            "answered": False,
            "reason": "Mock transport is enabled. It repeats the governed text and does not call a model.",
            "missing": MISSING,
        }

    def complete(self, question: str, governed_text: str, evidence: dict) -> str:
        return "Mock transport, not a model. Governed text: " + governed_text


class OllamaChatTransport:
    """Localhost Ollama /api/chat. Not a verified LLM Lab client."""

    kind = "ollama_chat"

    def __init__(self, url: str, model: str, timeout: float):
        self.url = _chat_url(url)
        self.model = model
        self.timeout = timeout

    def describe(self) -> dict:
        return {
            "status": "configured",
            "live": False,
            "contract_verified": False,
            "answered": False,
            "reason": "A localhost ollama_chat endpoint is configured. It has not been verified as the LLM Lab.",
            "missing": MISSING,
        }

    def complete(self, question: str, governed_text: str, evidence: dict) -> str:
        evidence_json = json.dumps(evidence, default=str)
        if len(evidence_json) > 8000:
            evidence_json = evidence_json[:8000]
        body = {
            "model": self.model,
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You may only use numbers that appear in the evidence JSON or the governed text. "
                        "If a figure is absent, say it is not available. Do not invent counties, years, or sources. "
                        "Do not claim to be the U.S. Census Bureau."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Question: {question[:480]}\n\nGoverned text:\n{governed_text[:4000]}\n\nEvidence JSON:\n{evidence_json}",
                },
            ],
            "options": {"temperature": 0},
        }
        try:
            response = requests.post(
                self.url,
                json=body,
                timeout=self.timeout,
                headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
                allow_redirects=False,
            )
        except requests.Timeout as exc:
            raise LlmError("The local endpoint timed out.") from exc
        except requests.RequestException as exc:
            raise LlmError("The local endpoint could not be reached.") from exc
        if response.status_code != 200:
            raise LlmError(f"The local endpoint returned HTTP {response.status_code}.")
        try:
            payload = response.json()
            text = payload.get("message", {}).get("content") or ""
        except ValueError as exc:
            raise LlmError("The local endpoint did not return JSON.") from exc
        text = text.strip()
        if not text:
            raise LlmError("The local endpoint returned an empty answer.")
        return text[:2000]


def _chat_url(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    if cleaned.endswith("/api/chat"):
        return cleaned
    return cleaned + "/api/chat"


def _timeout(raw: str) -> float:
    try:
        value = float(raw) if raw else 20.0
    except ValueError:
        value = 20.0
    return min(30.0, max(1.0, value))


def build_transport(settings: dict, file_status: str):
    if file_status == "insecure":
        return DisabledTransport("The .env file is not mode 600, so its LLM settings were not read.")
    enabled = settings.get("CENSUS_LLM_ENABLED") == "1"
    protocol = settings.get("CENSUS_LLM_LAB_PROTOCOL", "")
    if not enabled:
        return DisabledTransport("CENSUS_LLM_ENABLED is not 1. Answers come from governed metrics only.")
    if protocol == "mock":
        return MockTransport()
    if protocol != "ollama_chat":
        return DisabledTransport(
            "CENSUS_LLM_ENABLED is 1, but CENSUS_LLM_LAB_PROTOCOL is not mock or ollama_chat.",
            MISSING + ["CENSUS_LLM_LAB_PROTOCOL"],
        )
    url = settings.get("CENSUS_LLM_LAB_URL", "")
    model = settings.get("CENSUS_LLM_LAB_MODEL", "")
    missing = []
    if not url:
        missing.append("CENSUS_LLM_LAB_URL")
    if not model:
        missing.append("CENSUS_LLM_LAB_MODEL")
    if missing:
        return DisabledTransport("ollama_chat is missing " + ", ".join(missing) + ".", MISSING + missing)
    if not (url.startswith("http://127.0.0.1") or url.startswith("http://localhost")):
        return DisabledTransport(
            "The optional endpoint must stay on 127.0.0.1 or localhost. External model services are not called."
        )
    return OllamaChatTransport(url, model, _timeout(settings.get("CENSUS_LLM_TIMEOUT_S", "")))
