"""Deterministic questions over the governed export.

The router picks one tool. It does not accept SQL, and it does not fill gaps with zero.
Numbers in the narrative are formatted from the evidence packet.
"""
from __future__ import annotations

import re

from .format import format_moe, format_value, period_label
from .store import GlobeStore, StoreError

MAX_QUESTION = 480
RANK_CAP = 10
RANK_WORDS = ("which", "fastest", "slowest", "highest", "lowest", "top", "bottom", "most", "least", "rank")
SQL_WORDS = re.compile(
    r"\b(select|insert|update|delete|drop|alter|attach|pragma)\b|\bwith\b.+\bselect\b",
    re.I | re.S,
)


class RetrievalError(ValueError):
    pass


def _norm(text: str) -> str:
    text = text.lower().replace("'", "").replace(".", "")
    text = text.replace("-", " ").replace("–", " ")
    text = re.sub(r"[^a-z0-9\s%]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\bcounty\b", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("saint ", "st ")


def _aliases(name: str) -> list[str]:
    base = _norm(name)
    found = [base, base.replace(" ", "")]
    if base.startswith("st "):
        found.append("saint " + base[3:])
    if base == "desoto":
        found.append("de soto")
    if base == "miami dade":
        found.append("miami")
    # unique, longest first so the caller can prefer a specific hit
    return sorted(set(a for a in found if a), key=len, reverse=True)


def counties_mentioned(store: GlobeStore, text: str) -> list[dict]:
    padded = f" {_norm(text)} "
    hits = []
    for county in store.counties.values():
        best = None
        for alias in _aliases(county["name"]):
            token = f" {alias} "
            at = padded.find(token)
            if at >= 0 and (best is None or len(alias) > best[0]):
                best = (len(alias), at, county)
        if best:
            hits.append(best)
    hits.sort(key=lambda item: item[1])
    return [item[2] for item in hits]


def years_mentioned(text: str) -> list[int]:
    found = []
    if re.search(r"2015\s*[-–]\s*2019", text):
        found.append(2019)
    if re.search(r"2020\s*[-–]\s*2024", text):
        found.append(2024)
    for year in (2019, 2024):
        if re.search(rf"\b{year}\b", text) and year not in found:
            found.append(year)
    return found


def _metric_from_text(text: str) -> str | None:
    q = _norm(text)
    growth = any(word in q for word in ("grow", "growth", "change", "changed", "faster", "fastest", "slowest", "increase", "decline"))
    if "income" in q or "household" in q or "earning" in q:
        return "median_household_income_growth" if growth else "median_household_income"
    if "age" in q:
        return "median_age"
    if "hispanic" in q or "latino" in q:
        if any(word in q for word in ("share", "percent", "%", "portion")):
            return "hispanic_share"
        if any(word in q for word in ("people", "population", "residents", "how many")):
            return "hispanic_population"
        return None
    if growth:
        return "population_growth"
    if any(word in q for word in ("population", "people", "residents", "pop")):
        return "total_population"
    return None


def _direction(text: str) -> str:
    q = _norm(text)
    if any(word in q for word in ("lowest", "smallest", "slowest", "bottom", "least", "decline")):
        return "asc"
    return "desc"


def _rank_limit(text: str) -> int:
    match = re.search(r"\btop\s+(\d{1,2})\b", text.lower())
    if not match:
        match = re.search(r"\b(\d{1,2})\s+counties\b", text.lower())
    if not match:
        return 5
    return max(1, min(int(match.group(1)), RANK_CAP))


def _mentions_state(text: str) -> bool:
    return bool(re.search(r"\b(florida|statewide|the state|state)\b", text.lower()))


def _blank_packet(question: str) -> dict:
    return {"tool": "clarify", "question": question, "rows": [], "caveats": [], "notes": []}


def retrieve(store: GlobeStore, question: str, year: int | None = None,
             geoid: str | None = None, compare_geoid: str | None = None) -> dict:
    text = (question or "").strip()
    if not text:
        raise RetrievalError("Enter a question.")
    if len(text) > MAX_QUESTION:
        raise RetrievalError(f"Question is limited to {MAX_QUESTION} characters.")
    packet = _blank_packet(text)
    if SQL_WORDS.search(text):
        packet["notes"].append("This panel does not run SQL. Ask for a governed metric by county, comparison, or rank.")
        packet["clarify"] = packet["notes"][-1]
        return _finish(store, packet)
    named = counties_mentioned(store, text)
    years = years_mentioned(text)
    chosen_year = years[0] if len(years) == 1 else (year if year in store.years else store.years[-1])
    if year is not None and year not in store.years and not years:
        raise RetrievalError(f"Year must be one of {list(store.years)}.")
    metric = _metric_from_text(text)
    q = _norm(text)
    wants_rank = any(word in q for word in RANK_WORDS) and "this county" not in q
    reliability = any(word in q for word in ("reliab", "margin", "moe", "uncertain", "caution"))
    state_named = _mentions_state(text)

    focus = None
    if geoid:
        try:
            focus = store.county(geoid)
        except StoreError as exc:
            raise RetrievalError(str(exc)) from exc
    other = None
    if compare_geoid:
        try:
            other = store.county(compare_geoid)
        except StoreError as exc:
            raise RetrievalError(str(exc)) from exc

    if metric is None and ("hispanic" in q or "latino" in q):
        packet["clarify"] = "Hispanic origin can be the population count or the share of population. Say which one."
        return _finish(store, packet)
    if any(word in q for word in ("what can you", "which metrics", "list metrics", "help")):
        packet["tool"] = "list_metrics"
        return _finish(store, packet)
    if metric is None and not wants_rank:
        packet["clarify"] = (
            "Ask for population, median household income, median age, Hispanic population, Hispanic share, "
            "or growth between the 2019 and 2024 vintages."
        )
        return _finish(store, packet)

    if wants_rank and (len(named) < 2) and not (len(named) == 1 and state_named):
        metric = metric or "population_growth"
        return _rank(store, packet, metric, _direction(text), _rank_limit(text), chosen_year)

    places = list(named)
    if not places and focus and not state_named and not wants_rank:
        places = [focus]
    if other and other not in places and len(places) == 1:
        places.append(other)
    if len(places) >= 2:
        return _compare(store, packet, metric or "total_population", chosen_year, places[0], places[1])
    if len(places) == 1 and state_named:
        return _compare_state(store, packet, metric or "total_population", chosen_year, places[0])
    if state_named and not places:
        return _one(store, packet, metric or "total_population", chosen_year, store.payload["state"], kind="state")
    if len(places) == 1:
        if reliability:
            metric = "median_household_income" if metric in (None, "median_household_income", "median_household_income_growth") else metric
        return _one(store, packet, metric or "total_population", chosen_year, places[0], kind="county", reliability=reliability)
    if focus:
        return _one(store, packet, metric or "total_population", chosen_year, focus, kind="county")
    packet["clarify"] = "Name a Florida county, or ask which counties rank highest or lowest."
    return _finish(store, packet)


def _finish(store: GlobeStore, packet: dict) -> dict:
    packet["source"] = {
        "publisher": store.payload["source"]["publisher"],
        "program": store.payload["source"]["program"],
        "access": store.payload["source"]["access"],
        "endorsement": store.payload["source"]["endorsement"],
    }
    packet["years"] = list(store.years)
    packet["periods"] = {"2019": "2015-2019", "2024": "2020-2024"}
    packet["county_count"] = len(store.counties)
    packet.setdefault("caveats", list(store.payload.get("default_caveats") or []))
    return packet


def _one(store, packet, metric, year, holder, kind: str, reliability: bool = False) -> dict:
    try:
        reading = store.reading(holder, metric, year)
    except StoreError as exc:
        raise RetrievalError(str(exc)) from exc
    state_reading = store.reading(store.payload["state"], metric, year) if kind == "county" else None
    reading = {**reading, "name": holder["name"]}
    if state_reading:
        state_reading = {**state_reading, "name": store.payload["state"]["name"]}
    packet.update({
        "tool": "state_metric" if kind == "state" else "county_metric",
        "metric": metric,
        "reliability": reliability,
        "place": {"kind": kind, "geoid": holder.get("geoid"), "name": holder["name"]},
        "reading": reading,
        "state_reading": state_reading,
        "rows": [reading] + ([state_reading] if state_reading else []),
        "caveats": reading["caveats"],
    })
    return _finish(store, packet)


def _compare(store, packet, metric, year, a, b) -> dict:
    return _compare_holders(store, packet, metric, year, a, b, "county", "county")


def _compare_state(store, packet, metric, year, county) -> dict:
    return _compare_holders(store, packet, metric, year, county, store.payload["state"], "county", "state")


def _compare_holders(store, packet, metric, year, a, b, kind_a, kind_b) -> dict:
    try:
        left = {**store.reading(a, metric, year), "name": a["name"]}
        right = {**store.reading(b, metric, year), "name": b["name"]}
    except StoreError as exc:
        raise RetrievalError(str(exc)) from exc
    diff = _difference(left, right)
    packet.update({
        "tool": "compare",
        "metric": metric,
        "places": [
            {"kind": kind_a, "geoid": a.get("geoid"), "name": a["name"]},
            {"kind": kind_b, "geoid": b.get("geoid"), "name": b["name"]},
        ],
        "readings": [left, right],
        "difference": diff,
        "rows": [left, right],
        "caveats": left["caveats"],
    })
    return _finish(store, packet)


def _difference(left: dict, right: dict) -> dict | None:
    if left["value"] is None or right["value"] is None:
        return {"note": "One estimate is not available, so no difference is calculated."}
    if not left["additive"] and left["unit"] in {"USD", "years"}:
        # Medians can be subtracted for display. They still cannot be summed across counties.
        pass
    delta = left["value"] - right["value"]
    out = {
        "value": delta,
        "display": format_value(left["unit"], delta) if left["unit"] != "years" else f"{delta:+.1f}",
        "note": "Calculated in this product from the two published estimates. It is not a Census Bureau published difference.",
        "significant_90": None,
    }
    if left.get("moe") is None or right.get("moe") is None or str(left["unit"]).startswith("ratio"):
        out["significance"] = "A 90% test is not shown because a margin of error is missing or the metric is a ratio or rate."
        return out
    import math
    moe = math.sqrt(left["moe"] ** 2 + right["moe"] ** 2)
    out["moe"] = moe
    out["moe_display"] = format_moe(left["unit"], moe)
    out["significant_90"] = abs(delta) > moe
    if out["significant_90"]:
        out["significance"] = "The gap is larger than the 90% margin of error of the difference, approximated as the square root of the summed squared margins."
    else:
        out["significance"] = "The gap is within the 90% margin of error of the difference, so it may not be statistically significant."
    return out


def _rank(store, packet, metric, direction: str, limit: int, year: int) -> dict:
    try:
        spec = store.metric(metric)
    except StoreError as exc:
        raise RetrievalError(str(exc)) from exc
    rows = []
    missing = 0
    for county in store.counties.values():
        reading = store.reading(county, metric, None if spec["growth"] else year)
        if reading["value"] is None:
            missing += 1
            continue
        rows.append({"geoid": county["geoid"], "name": county["name"], **reading})
    rows.sort(key=lambda row: row["value"], reverse=direction == "desc")
    kept = rows[:limit]
    packet.update({
        "tool": "rank",
        "metric": metric,
        "direction": direction,
        "limit": limit,
        "excluded_null": missing,
        "rows": kept,
        "period": kept[0]["period"] if kept else None,
        "caveats": (kept[0]["caveats"] if kept else spec["caveats"]),
    })
    return _finish(store, packet)


def narrate(packet: dict) -> str:
    tool = packet.get("tool")
    if tool == "clarify":
        return packet.get("clarify") or "Ask for a governed Florida county metric."
    if tool == "list_metrics":
        return packet.get("clarify") or (
            "The governed metrics are total population, median household income, median age, "
            "Hispanic population, Hispanic share, population growth, and nominal median household income growth. "
            "Vintages are ACS 5-year 2019 (2015–2019) and ACS 5-year 2024 (2020–2024)."
        )
    if tool == "rank":
        if not packet["rows"]:
            return "No county has a published estimate for that metric. Nothing was filled in as zero."
        verb = "highest" if packet["direction"] == "desc" else "lowest"
        lines = [
            f"{verb.capitalize()} {packet['rows'][0]['label']} among Florida counties, {packet['period']}.",
        ]
        for row in packet["rows"]:
            moe = f" ({row['moe_display']})" if row.get("moe_display") else ""
            lines.append(f"{row['name']}: {row['display']}{moe}.")
        if packet["excluded_null"] == 0:
            lines.append(f"Every county in this {packet['county_count']}-county export has a published estimate. None were treated as zero.")
        else:
            lines.append(f"{packet['excluded_null']} counties have no published estimate and are omitted, not treated as zero.")
        lines.append(packet["rows"][0]["uncertainty"])
        return " ".join(lines)
    if tool == "compare":
        left, right = packet["readings"]
        a, b = packet["places"]
        diff = packet.get("difference") or {}
        parts = [
            f"{a['name']} and {b['name']}, {left['label']}, {left['period']}.",
            f"{a['name']}: {left['display']}" + (f" ({left['moe_display']})" if left.get("moe_display") else "") + ".",
            f"{b['name']}: {right['display']}" + (f" ({right['moe_display']})" if right.get("moe_display") else "") + ".",
        ]
        if diff.get("display"):
            parts.append(f"Difference ({a['name']} minus {b['name']}): {diff['display']}. {diff.get('note', '')}")
        if diff.get("significance"):
            parts.append(diff["significance"])
        if not left["additive"]:
            parts.append("This metric is not additive across counties.")
        return " ".join(parts)
    reading = packet["reading"]
    place = packet["place"]
    moe = f" The 90% margin of error is {reading['moe_display']}." if reading.get("moe_display") else ""
    text = f"{place['name']}, {reading['label']}, {reading['period']}. The estimate is {reading['display']}.{moe} {reading['uncertainty']}"
    if reading.get("low_reliability") is True:
        text += f" The coefficient of variation is {reading['cv']:.3f}, above the 0.30 caution threshold in this pipeline."
    elif reading.get("cv") is not None:
        text += f" The coefficient of variation is {reading['cv']:.3f}, below the 0.30 caution threshold in this pipeline."
    state = packet.get("state_reading")
    if state:
        state_moe = f" ({state['moe_display']})" if state.get("moe_display") else ""
        text += f" Florida's official state value for the same metric and vintage is {state['display']}{state_moe}."
        if not reading["additive"]:
            text += " The state value is the published state estimate, not an average of the counties."
    text += f" Source: {packet['source']['program']}, formula {reading['formula']}."
    return " ".join(text.split())


def evidence_number_tokens(packet: dict) -> set[str]:
    """Every number token the narrator is allowed to print, drawn from the packet."""
    tokens: set[str] = set()

    def add_number(number) -> None:
        if isinstance(number, bool) or number is None:
            return
        if isinstance(number, float) and number.is_integer():
            number = int(number)
        if isinstance(number, int):
            tokens.update({
                str(number), f"{number:,}", f"{abs(number):,}", f"{number:+,}",
                f"{number:.1f}", f"{abs(number):.1f}",
            })
            return
        tokens.update({
            f"{number:.1f}", f"{number:.2f}", f"{number:.3f}", f"{number:.4f}",
            f"{abs(number):.1f}", f"{abs(number):.2f}", f"{abs(number):.3f}",
            f"{number * 100:.2f}", f"{abs(number) * 100:.2f}", f"{number * 100:+.2f}",
        })

    def walk(value) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str):
            tokens.update(re.findall(r"\d[\d,]*\.?\d*", value))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            add_number(value)

    walk(packet)
    return tokens


def public_evidence(packet: dict) -> dict:
    """Drop nothing sensitive — there are no secrets — but keep the payload small."""
    keep = ("tool", "question", "metric", "place", "places", "reading", "state_reading",
            "readings", "difference", "rows", "direction", "excluded_null", "period",
            "county_count", "reliability", "caveats", "source", "notes", "clarify")
    return {key: packet[key] for key in keep if key in packet}
