"""Citilink's city directory (citiesForSelect): parsing and lookup by name.

A city id doubles as the value of the ``_space`` cookie that selects the city:
``ekat_cl`` for a region's main city, ``ekat_cl:5x0029236`` for others
(Верхняя Пышма). The directory is ~1.2 MB (8465 cities, 49 delivery areas),
so the client keeps a compact copy on disk for a week.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

CITY_ID = re.compile(r"^[a-z][a-z0-9_]*_cl(?::[0-9a-z]+)?$")
DEFAULT_CITY = "ekat_cl"


def is_city_id(text: str) -> bool:
    return bool(CITY_ID.match((text or "").strip()))


def compact(payload: dict) -> list[dict]:
    """citiesForSelect answer -> [{id, name, region, space_id, main}] (deduplicated)."""
    groups = (((payload or {}).get("citiesForSelect") or {}).get("groupedByLetter")) or []
    seen, out = set(), []
    for g in groups:
        for c in g.get("cities") or []:
            cid = c.get("id")
            if not cid or cid in seen or not c.get("name"):
                continue
            seen.add(cid)
            out.append({
                "id": cid,
                "name": c["name"],
                "region": c.get("region") or None,
                "space_id": c.get("spaceId") or None,
                "main": bool(c.get("isMainInRegion")),
            })
    return out


def norm(text: str) -> str:
    return re.sub(r"[\s\-]+", " ", (text or "").lower().replace("ё", "е")).strip()


def find(cities: Iterable[dict], query: str) -> list[dict]:
    """Cities matching a name, optionally narrowed by region after a comma.

    "Екатеринбург" -> exact name matches; "Кировск, Мурманская" -> the one in
    Мурманская область. With no exact match, names that start with the query.
    The main city of a region comes first.
    """
    name, _, region = (query or "").partition(",")
    n, r = norm(name), norm(region)
    if not n:
        return []
    pool = list(cities)
    exact = [c for c in pool if norm(c["name"]) == n]
    hits = exact or [c for c in pool if norm(c["name"]).startswith(n)]
    if r:
        hits = [c for c in hits if r in norm(c.get("region") or "")]
    return sorted(hits, key=lambda c: (not c.get("main"), c["name"], c.get("region") or ""))


def public(c: dict) -> dict[str, Any]:
    return {k: v for k, v in {
        "id": c["id"], "name": c["name"], "region": c.get("region"),
        "delivery_area": c.get("space_id"), "main_city_of_region": c.get("main") or None,
    }.items() if v is not None}
