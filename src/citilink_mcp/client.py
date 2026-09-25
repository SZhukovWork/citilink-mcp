"""HTTP client for Citilink's storefront GraphQL endpoint.

One endpoint, ``POST https://www.citilink.ru/graphql/``, plain ``requests``,
no browser. Policies:

- **City on every request.** The city is the ``_space`` cookie; it is "sticky"
  in a browser, and a bogus value silently yields Moscow. So the cookie is set
  explicitly on every call, every query asks for ``viewer.currentCity`` and an
  answer for another city is retried once and then refused.
- **Pacing.** Calls are serialised (``RLock``) and spaced by
  ``CITILINK_MIN_INTERVAL`` seconds (2.5 by default) plus jitter.
- **Anti-bot.** Citilink's own JS challenge (HTTP 429 + "Загрузка..." page)
  is solved in Python (``antibot``) once per call; the resulting ``_pcl``
  cookie is kept for its hour. Other 403/429/503 answers get one pause and
  one retry, then a clear error — never a retry loop.
- **Sanity checks.** Non-JSON, truncated or foreign answers are retried once
  and then turned into an error rather than passed on as data. Partial
  GraphQL errors (data plus ``errors``) are passed on as ``partial_errors``.
- **Schema drift.** ``GRAPHQL_VALIDATION_FAILED`` becomes ``SchemaChanged``
  naming the rejected fields. A refusal that looks like a demand for the
  site's request signature (``x-ad-mark``) becomes ``SignatureRequired``.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests

from . import antibot, cities, queries

log = logging.getLogger(__name__)

SITE = "https://www.citilink.ru"
GRAPHQL_URL = f"{SITE}/graphql/"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)
PER_PAGE = 48          # Citilink silently cuts bigger pages to 48 and then lies in totalPages
BATCH_MAX = 20         # products(ids): 20 verified live in one request
CITIES_TTL = 7 * 86400
PCL_TTL = 55 * 60      # the challenge script sets the cookie for 60 minutes
SESSION_TTL = 60 * 60
THROTTLE_PAUSE = 20.0
MIN_CITIES = 1000     # the real directory has ~8500; far fewer = a broken answer
NETWORK_PAUSE = 3.0

SORTS: dict[str, tuple[str, str]] = {
    "relevance": ("relevance", "SORT_DIRECTION_DESC"),
    "price_asc": ("price", "SORT_DIRECTION_ASC"),
    "price_desc": ("price", "SORT_DIRECTION_DESC"),
    "discount": ("discount", "SORT_DIRECTION_DESC"),
    "rating": ("rating", "SORT_DIRECTION_DESC"),
    "opinions": ("opinions", "SORT_DIRECTION_DESC"),
}
AVAILABILITY = {
    "in_store_now": "available.instore",   # «Забрать через 5 минут»
    "pickup": "available.all",              # «Доступен самовывоз»
    "delivery": "available.delivery",       # «Доступен для доставки»
}


class CitilinkError(RuntimeError):
    """Anything that stops a tool from returning trustworthy data."""


class RateLimited(CitilinkError):
    pass


class Blocked(CitilinkError):
    pass


class SignatureRequired(CitilinkError):
    pass


class SchemaChanged(CitilinkError):
    pass


class CityMismatch(CitilinkError):
    pass


class NotFound(CitilinkError):
    pass


class BadRequest(CitilinkError):
    pass


def cache_dir() -> Path:
    root = os.environ.get("CITILINK_CACHE_DIR") or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "citilink-mcp"
    )
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def write_private(path: Path, text: str) -> None:
    """Write a file readable by the owner only (created 0600, never world-readable in between)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


@dataclass
class Result:
    data: dict
    city: dict | None
    partial_errors: list[dict] = field(default_factory=list)


# ---- response analysis (pure; exercised offline) ------------------------------------------

@dataclass
class Verdict:
    """What to do with one HTTP answer."""
    action: str                      # "ok" | "retry" | "fail"
    kind: str = ""                   # retry bucket: challenge | throttle | implausible | server | city | missing
    reason: str = ""
    result: Result | None = None
    error: CitilinkError | None = None
    solution: antibot.Solution | None = None


_SIGNATURE_HINT = re.compile(r"ad.?mark|signature|\bsign(ed)?\b|подпис", re.IGNORECASE)
_FIELD = re.compile(r'Cannot query field "([^"]+)" on type "([^"]+)"')


def signature_message(status: int, detail: str) -> str:
    return (
        "Ситилинк начал требовать подпись запроса (x-ad-mark); нужен запасной путь из README. "
        f"Citilink now seems to require its request signature header (HTTP {status}: {detail[:200]}). "
        "This server cannot produce it yet — see README, 'If Citilink starts requiring x-ad-mark'."
    )


def _errors(body: Any) -> list[dict]:
    errs = body.get("errors") if isinstance(body, dict) else None
    return [e for e in errs if isinstance(e, dict)] if isinstance(errs, list) else []


def _codes(errs: list[dict]) -> set[str]:
    return {str((e.get("extensions") or {}).get("code") or "") for e in errs}


def schema_changed(errs: list[dict]) -> SchemaChanged:
    fields = sorted({f"{m.group(2)}.{m.group(1)}" for e in errs for m in [_FIELD.search(str(e.get("message")))] if m})
    detail = ", ".join(fields) if fields else "; ".join(str(e.get("message"))[:150] for e in errs[:3])
    return SchemaChanged(
        f"Citilink changed its API: the server rejected the query ({detail}). "
        "The citilink-mcp queries need an update; retrying will not help."
    )


def analyse(status: int, content_type: str, text: str, city_id: str | None, root: str | None,
            check: Callable[[dict], str | None] | None = None) -> Verdict:
    """Classify one answer of /graphql/ without side effects."""
    ctype = (content_type or "").lower()
    is_json = "json" in ctype
    if status == 429 and not is_json and antibot.is_challenge(text or ""):
        solution = antibot.solve(text)
        if solution is None:
            return Verdict("fail", error=Blocked(
                "Citilink answered with its anti-bot challenge and its layout changed, so it could "
                "not be solved. Wait an hour; if it persists the server needs an update."))
        return Verdict("retry", "challenge", "anti-bot challenge", solution=solution)
    if status in (429, 503):
        return Verdict("retry", "throttle", f"HTTP {status}",
                       error=RateLimited(f"Citilink is rate limiting this IP (HTTP {status}). Wait a few "
                                         "minutes before the next request; retrying in a loop makes it worse."))
    if status == 403 and not is_json:
        return Verdict("retry", "throttle", "HTTP 403",
                       error=Blocked("Citilink refused access (HTTP 403 from its edge). Usually a VPN, foreign "
                                     "or datacenter IP; use a Russian home IP or CITILINK_PROXY."))
    body: Any = None
    if is_json:
        try:
            body = json.loads(text)
        except ValueError:
            return Verdict("retry", "implausible", "truncated JSON body")
    errs = _errors(body)
    codes = _codes(errs)
    if codes & {"GRAPHQL_VALIDATION_FAILED", "GRAPHQL_PARSE_FAILED", "BAD_USER_INPUT"}:
        return Verdict("fail", error=schema_changed(errs))
    if 400 <= status < 500 and is_json:
        detail = "; ".join(str(e.get("message")) for e in errs[:2]) or (text or "")[:200]
        return Verdict("fail", error=SignatureRequired(signature_message(status, detail)))
    if status >= 500:
        return Verdict("retry", "server", f"HTTP {status}",
                       error=CitilinkError(f"Citilink's server failed (HTTP {status}); try again in a minute."))
    if status != 200 or not is_json:
        return Verdict("retry", "implausible", f"unexpected answer HTTP {status} {content_type}")
    if not isinstance(body, dict):
        return Verdict("retry", "implausible", "body is not a JSON object")
    data = body.get("data")
    if not isinstance(data, dict):
        messages = "; ".join(str(e.get("message")) for e in errs[:3])
        if codes & {"UNAUTHENTICATED", "FORBIDDEN"} or _SIGNATURE_HINT.search(messages):
            return Verdict("fail", error=SignatureRequired(signature_message(status, messages)))
        return Verdict("retry", "implausible", f"no data in the answer ({messages or 'no errors either'})")
    echo = None
    if city_id is not None:
        echo = ((data.get("viewer") or {}).get("currentCity")) or {}
        if not echo.get("id"):
            return Verdict("retry", "implausible", "the answer does not say which city it is for")
        if echo["id"] != city_id:
            return Verdict("retry", "city", f"answer is for city {echo.get('id')} ({echo.get('name')})",
                           error=CityMismatch(
                               f"Citilink answered for {echo.get('name')} ({echo.get('id')}) instead of the requested "
                               f"city {city_id}; not passing it on. Check the city id with resolve_city."))
    partial = [
        {"path": ".".join(str(x) for x in (e.get("path") or [])), "message": str(e.get("message"))[:200]}
        for e in errs
    ]
    if root is not None and data.get(root) is None:
        return Verdict("retry", "missing", f"no '{root}' in the answer",
                       result=Result(data, echo or None, partial))
    if check is not None:
        problem = check(data)
        if problem:
            return Verdict("retry", "implausible", problem)
    return Verdict("ok", result=Result(data, echo or None, partial))


# ---- client --------------------------------------------------------------------------------------

class CitilinkClient:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._min_interval = float(os.environ.get("CITILINK_MIN_INTERVAL", "2.5"))
        self._proxy = os.environ.get("CITILINK_PROXY") or None
        self._dir = cache_dir()
        self._session_file = self._dir / "session.json"
        self._cities_file = self._dir / "cities.json"
        self._http = requests.Session()
        self._http.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Content-Type": "application/json",
            "Origin": SITE,
            "Referer": SITE + "/",
        })
        if self._proxy:
            self._http.proxies.update({"http": self._proxy, "https": self._proxy})
        self._cookies: dict[str, str] = {}
        self._pcl: dict | None = None
        self._last = 0.0
        self._cities: list[dict] | None = None
        self._load_session()

    # ---- session state ----------------------------------------------------------------------

    def _load_session(self) -> None:
        try:
            saved = json.loads(self._session_file.read_text())
        except (OSError, ValueError):
            return
        now = time.time()
        if isinstance(saved.get("cookies"), dict) and now - float(saved.get("saved_at") or 0) < SESSION_TTL:
            self._cookies = {str(k): str(v) for k, v in saved["cookies"].items()}
        pcl = saved.get("pcl")
        if isinstance(pcl, dict) and float(pcl.get("expires") or 0) > now:
            self._pcl = pcl

    def _save_session(self) -> None:
        try:
            write_private(self._session_file, json.dumps({"saved_at": time.time(), "cookies": self._cookies,
                                                           "pcl": self._pcl}))
        except OSError as e:
            log.warning("Could not persist the Citilink session: %s", e)

    def _cookie_header(self, city_id: str | None) -> str:
        jar = dict(self._cookies)
        if self._pcl and float(self._pcl.get("expires") or 0) > time.time():
            jar[self._pcl["name"]] = self._pcl["value"]
        if city_id:
            jar["_space"] = city_id        # always ours: the site's own value is sticky
        else:
            jar.pop("_space", None)
        return "; ".join(f"{k}={v}" for k, v in jar.items())

    def _remember_cookies(self, resp: requests.Response) -> None:
        changed = False
        for c in resp.cookies:
            if c.name in ("_space",) or not c.value:
                continue
            if self._cookies.get(c.name) != c.value:
                self._cookies[c.name] = c.value
                changed = True
        self._http.cookies.clear()
        if changed:
            log.debug("Citilink set cookies: %s", sorted(c.name for c in resp.cookies))
            self._save_session()

    def _pace(self) -> None:
        wait = self._min_interval + random.uniform(0, 0.5) - (time.monotonic() - self._last)
        if wait > 0 and self._last:
            time.sleep(wait)

    # ---- the one request primitive ------------------------------------------------------------

    def gql(self, op: str, query: str, variables: dict | None, city_id: str | None,
            root: str | None, check: Callable[[dict], str | None] | None = None) -> Result:
        body = json.dumps({"query": query, "variables": variables or {}}, ensure_ascii=False).encode("utf-8")
        with self._lock:
            retried: set[str] = set()
            last: Verdict | None = None
            for _attempt in range(4):
                self._pace()
                started = time.monotonic()
                try:
                    resp = self._http.post(GRAPHQL_URL, data=body, timeout=(10, 30),
                                           headers={"Cookie": self._cookie_header(city_id)})
                except requests.RequestException as e:
                    self._last = time.monotonic()
                    if "network" in retried:
                        raise CitilinkError(f"Network error talking to Citilink: {e}") from e
                    retried.add("network")
                    log.warning("%s: network error (%s), one retry", op, type(e).__name__)
                    time.sleep(NETWORK_PAUSE)
                    continue
                self._last = time.monotonic()
                self._remember_cookies(resp)
                log.info("%s: HTTP %s, %d B, %.2fs", op, resp.status_code, len(resp.content),
                         self._last - started)
                verdict = analyse(resp.status_code, resp.headers.get("content-type", ""), resp.text,
                                  city_id, root, check)
                if verdict.action == "ok":
                    return verdict.result  # type: ignore[return-value]
                if verdict.action == "fail":
                    raise verdict.error  # type: ignore[misc]
                last = verdict
                if verdict.kind in retried:
                    break
                retried.add(verdict.kind)
                log.warning("%s: %s — one retry", op, verdict.reason)
                if verdict.kind == "challenge" and verdict.solution:
                    s = verdict.solution
                    self._pcl = {"name": s.name, "value": s.value, "domain": s.domain,
                                 "expires": time.time() + PCL_TTL}
                    self._save_session()
                    log.info("Solved Citilink's anti-bot challenge (cookie %s)", s.name)
                elif verdict.kind == "throttle":
                    time.sleep(THROTTLE_PAUSE)
                elif verdict.kind in ("server", "implausible", "city", "missing"):
                    time.sleep(2.0)
            return self._give_up(op, last)

    def _give_up(self, op: str, last: Verdict | None) -> Result:
        if last is None:
            raise CitilinkError(f"Citilink request {op} failed")
        if last.kind == "missing" and last.result is not None:
            return last.result          # the caller turns a missing root into NotFound
        if last.kind == "challenge":
            raise Blocked("Citilink keeps answering with its anti-bot challenge even after it was solved; "
                          "wait an hour before trying again.")
        if last.error is not None:
            raise last.error
        raise CitilinkError(
            f"Citilink returned an implausible answer ({last.reason}) and it did not go away on retry; "
            "not passing it on as data."
        )

    # ---- city ------------------------------------------------------------------------------

    def city_directory(self) -> list[dict]:
        with self._lock:
            if self._cities is not None:
                return self._cities
            try:
                saved = json.loads(self._cities_file.read_text())
                if time.time() - float(saved.get("fetched_at") or 0) < CITIES_TTL and saved.get("cities"):
                    self._cities = saved["cities"]
                    return self._cities
            except (OSError, ValueError):
                pass
            res = self.gql("cities", queries.CITIES, None, None, "citiesForSelect")
            directory = cities.compact(res.data)
            if len(directory) < MIN_CITIES:
                raise CitilinkError("Citilink's city directory came back nearly empty; not caching it")
            self._cities = directory
            try:
                self._cities_file.write_text(json.dumps({"fetched_at": time.time(), "cities": directory},
                                                        ensure_ascii=False))
            except OSError as e:
                log.warning("Could not cache the city directory: %s", e)
            return directory

    def resolve_city(self, city: str | None) -> tuple[str, str]:
        """(city id, where it came from). Names are looked up in the directory."""
        if city and city.strip():
            return self._city_id(city.strip(), "parameter"), "parameter"
        env = (os.environ.get("CITILINK_CITY") or "").strip()
        if env:
            return self._city_id(env, "CITILINK_CITY"), "CITILINK_CITY"
        return cities.DEFAULT_CITY, "default (Екатеринбург)"

    def _city_id(self, text: str, source: str) -> str:
        if cities.is_city_id(text):
            return text
        matches = cities.find(self.city_directory(), text)
        if not matches:
            raise BadRequest(f"No Citilink city matches '{text}' ({source}); use resolve_city to look it up.")
        exact = [c for c in matches if cities.norm(c["name"]) == cities.norm(text.partition(",")[0])]
        pool = exact or matches
        if len(pool) > 1:
            options = "; ".join(f"{c['name']}, {c.get('region')} → {c['id']}" for c in pool[:8])
            raise BadRequest(f"'{text}' ({source}) is ambiguous: {options}. Pass the id or 'Name, Region'.")
        return pool[0]["id"]

    # ---- data calls ------------------------------------------------------------------------------

    def search(self, query: str, page: int, sort: str, price_min: int | None, price_max: int | None,
               category_id: str | None, availability: str, city_id: str) -> Result:
        slug, direction = SORTS[sort]
        conditions: list[dict] = []
        window = None
        if price_min is not None or price_max is not None:
            window = (str(price_min if price_min is not None else 0),
                      str(price_max if price_max is not None else 99_999_999))
            conditions.append({"filterGroupId": "price_filter_group_id",
                               "range": {"fromValue": window[0], "toValue": window[1]}})
        avail_id = AVAILABILITY.get(availability)
        if avail_id:
            conditions.append({"filterGroupId": "availability_filter_group_id", "filterIds": [avail_id]})
        variables = {"in": {
            "searchText": query,
            "categoryId": category_id or "0",
            "pagination": {"page": page, "perPage": PER_PAGE},
            "sorting": {"id": slug, "direction": direction},
            "conditions": conditions,
            "popularitySegmentId": "THREE",      # what the site sends
        }}
        check = listing_check("fullSearchFilter", page, slug, direction, window, avail_id, category_id)
        return self.gql("search", queries.SEARCH, variables, city_id, "fullSearchFilter", check)

    def product(self, pid: str, stores: bool, city_id: str) -> Result:
        return self.gql("product", queries.PRODUCT, {"id": pid, "stores": stores}, city_id, "product")

    def products(self, ids: list[str], city_id: str) -> Result:
        if len(ids) > BATCH_MAX:
            raise BadRequest(f"At most {BATCH_MAX} products per call")
        return self.gql("products", queries.BATCH, {"ids": ids}, city_id, "products")

    def reviews(self, pid: str, page: int, per_page: int, sort: str, with_group: bool,
                ratings: list[int] | None, city_id: str) -> Result:
        opinions_in: dict[str, Any] = {"pagination": {"page": page, "perPage": per_page},
                                       "sort": sort, "withGroup": with_group}
        if ratings:
            opinions_in["ratings"] = ratings
        return self.gql("reviews", queries.REVIEWS, {"id": pid, "in": opinions_in}, city_id, "product")

    def questions(self, pid: str, page: int, per_page: int, sort: str, city_id: str) -> Result:
        variables = {"id": pid, "in": {"type": "PRODUCT", "pagination": {"page": page, "perPage": per_page},
                                       "sort": sort}}
        return self.gql("questions", queries.QUESTIONS, variables, city_id, "product")

    def markdown_listing(self, category_slug: str, page: int, sort: str, city_id: str) -> Result:
        slug, direction = SORTS[sort]
        variables = {"in": {"categorySlug": category_slug, "pagination": {"page": page, "perPage": PER_PAGE},
                            "sorting": {"id": slug, "direction": direction}}}
        check = listing_check("discountFilter", page, slug, direction, None, None, None)
        return self.gql("markdown", queries.DISCOUNT_FILTER, variables, city_id, "discountFilter", check)

    def markdown_copies(self, pid: str, city_id: str) -> Result:
        return self.gql("markdown_copies", queries.DISCOUNTED_COPIES, {"id": pid}, city_id, "product")


def listing_check(root: str, page: int, sort_slug: str, direction: str, window: tuple[str, str] | None,
                  availability_id: str | None, category_id: str | None) -> Callable[[dict], str | None]:
    """Sanity checks for a search/markdown page: did Citilink apply what we asked for?"""

    def check(data: dict) -> str | None:
        block = data.get(root) or {}
        if block.get("error"):
            return None  # reported by the caller as a clear error
        record = block.get("record")
        if not isinstance(record, dict):
            return "no result record"
        info = record.get("pageInfo") or {}
        products = record.get("products")
        total = info.get("totalItems")
        if not isinstance(products, list) or not isinstance(total, int):
            return "no products or totals in the answer"
        if len(products) > PER_PAGE:
            return f"{len(products)} items on a {PER_PAGE}-item page"
        if total > (page - 1) * PER_PAGE and not products:
            return f"empty page {page} although {total} items were found (truncated answer?)"
        if products and info.get("page") != page:
            return f"asked for page {page}, got page {info.get('page')}"
        selected = {(s.get("slug"), d.get("id")) for s in record.get("sortings") or []
                    for d in s.get("directions") or [] if d.get("isSelected")}
        if total and selected and (sort_slug, direction) not in selected:
            return f"Citilink ignored the sort order {sort_slug}/{direction}"
        groups = {g.get("id"): g.get("filter") or {} for g in record.get("groups") or []}
        if window and total and "price_filter_group_id" in groups:
            # The echo is the applied window clamped to the matches' price range
            # (asked 200–3000, cheapest match 310 -> "310"–"3000"); an ignored
            # window echoes the full range instead.
            f = groups["price_filter_group_id"]
            try:
                low, high = int(f.get("fromValue")), int(f.get("toValue"))
            except (TypeError, ValueError):
                return "no price-window echo in the answer"
            if low < int(window[0]) or high > int(window[1]):
                return f"Citilink ignored the price window {window[0]}–{window[1]} (applied {low}–{high})"
        if availability_id and total and "availability_filter_group_id" in groups:
            flags = {x.get("id"): x.get("isSelected") for x in groups["availability_filter_group_id"].get("filters") or []}
            if not flags.get(availability_id):
                return f"Citilink ignored the availability filter {availability_id}"
        if category_id and total:
            chosen = {str((c.get("category") or {}).get("id")) for c in record.get("categories") or [] if c.get("isSelected")}
            if str(category_id) not in chosen:
                return f"Citilink ignored category_id {category_id}"
        return None

    return check
