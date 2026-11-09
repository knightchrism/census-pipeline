"""Equal-count classes for choropleth legends.

Breaks are taken from the published values. Missing estimates are their own
class and are not placed at zero.
"""
from __future__ import annotations

import math

RAMP = ("#1f4f78", "#4e7ea3", "#c4a15a", "#e4c56a", "#ffe7a3")
NO_DATA = "#314255"


def _edges(sorted_vals: list[float], k: int) -> list[float]:
    n = len(sorted_vals)
    distinct = len(set(sorted_vals))
    k = max(1, min(k, distinct, n))
    raw = [sorted_vals[min(n - 1, math.ceil(i * n / k) - 1)] for i in range(1, k + 1)]
    edges: list[float] = []
    for edge in raw:
        if not edges or edge > edges[-1]:
            edges.append(edge)
    return edges


def classify(values: dict[str, float | None], k: int = 5) -> dict:
    present = sorted(v for v in values.values() if v is not None)
    if not present:
        return {
            "colors": {gid: NO_DATA for gid in values},
            "classes": [],
            "missing": sorted(values),
            "no_data_color": NO_DATA,
        }
    edges = _edges(present, k)
    colors = RAMP[-len(edges):]
    assignment: dict[str, int | None] = {}
    for gid, value in values.items():
        if value is None:
            assignment[gid] = None
            continue
        for index, edge in enumerate(edges):
            if value <= edge:
                assignment[gid] = index
                break
    classes = []
    for index, color in enumerate(colors):
        members = [values[gid] for gid, cls in assignment.items() if cls == index]
        classes.append({
            "index": index,
            "color": color,
            "min": min(members) if members else None,
            "max": max(members) if members else None,
            "count": len(members),
        })
    return {
        "colors": {gid: NO_DATA if assignment[gid] is None else colors[assignment[gid]] for gid in values},
        "class_index": assignment,
        "classes": classes,
        "missing": sorted(gid for gid, cls in assignment.items() if cls is None),
        "no_data_color": NO_DATA,
    }
