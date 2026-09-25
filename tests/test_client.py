"""Request policies of the client, exercised offline against a scripted fake citilink.ru."""
import json
import os
import stat

import pytest

from citilink_mcp import client as client_mod
from citilink_mcp.client import (Blocked, CitilinkClient, CitilinkError, CityMismatch, RateLimited, SchemaChanged,
                                 SignatureRequired, analyse)
from conftest import FakeResponse, fixture_router, html, load

CHALLENGE = load("challenge_429_redacted.html")
CARD = load("product_1896626.json")


def ok(body=None):
    return FakeResponse(200, body if body is not None else CARD)


def msk(body):
    body = json.loads(json.dumps(body))
    body["data"]["viewer"]["currentCity"] = {"id": "msk_cl", "name": "Москва", "spaceId": "msk_cl"}
    return FakeResponse(200, body)


# ---- analyse(): pure classification -----------------------------------------------------------

def test_validation_error_names_the_rejected_fields():
    v = analyse(400, "application/json", json.dumps(load("validation_error_400.json")), "ekat_cl", "product")
    assert v.action == "fail" and isinstance(v.error, SchemaChanged)
    assert "Catalog_ProductVariant.label" in str(v.error) and "Catalog_ProductVariant.isStrictMatch" in str(v.error)


def test_other_json_4xx_looks_like_a_signature_demand():
    body = json.dumps({"errors": [{"message": "Forbidden", "extensions": {"code": "FORBIDDEN"}}]})
    v = analyse(403, "application/json", body, "ekat_cl", "product")
    assert isinstance(v.error, SignatureRequired)
    assert "x-ad-mark" in str(v.error) and "HTTP 403" in str(v.error)
    assert str(v.error).startswith("Ситилинк начал требовать подпись запроса (x-ad-mark); нужен запасной путь из README")


def test_null_data_mentioning_a_signature_is_a_signature_demand():
    body = json.dumps({"data": None, "errors": [{"message": "x-ad-mark header is required"}]})
    v = analyse(200, "application/json", body, "ekat_cl", "product")
    assert isinstance(v.error, SignatureRequired)


def test_challenge_is_solved_for_one_retry():
    v = analyse(429, "text/html; charset=utf-8", CHALLENGE, "ekat_cl", "product")
    assert v.action == "retry" and v.kind == "challenge" and v.solution.name == "_pcl"


def test_unsolvable_challenge_is_an_error():
    broken = CHALLENGE.replace("new Int8Array(", "new Uint8Array(")
    v = analyse(429, "text/html", broken, "ekat_cl", "product")
    assert v.action == "fail" and isinstance(v.error, Blocked)


def test_partial_errors_pass_through_with_the_data():
    body = load("product_M00295786_partial_error.json")
    v = analyse(200, "application/json", json.dumps(body), "ekat_cl", "product")
    assert v.action == "ok"
    assert v.result.partial_errors == [{"path": "product.summary.ratingCounters.0.percentage",
                                        "message": "Subgraph errors redacted"}]


def test_answer_for_another_city_is_not_data():
    v = analyse(200, "application/json", json.dumps(msk(CARD).json()), "ekat_cl", "product")
    assert v.action == "retry" and v.kind == "city" and isinstance(v.error, CityMismatch)


def test_answer_without_city_echo_is_implausible():
    body = {"data": {"product": CARD["data"]["product"]}}
    assert analyse(200, "application/json", json.dumps(body), "ekat_cl", "product").kind == "implausible"


def test_truncated_json_is_retried():
    text = json.dumps(CARD)[:5000]
    assert analyse(200, "application/json", text, "ekat_cl", "product").kind == "implausible"


def test_missing_root_is_retried_then_reported():
    body = {"data": {"viewer": CARD["data"]["viewer"], "product": None},
            "errors": [{"message": "Subgraph errors redacted", "path": ["product"]}]}
    v = analyse(200, "application/json", json.dumps(body), "ekat_cl", "product")
    assert v.kind == "missing" and v.result.data["product"] is None


# ---- the client with a fake site ---------------------------------------------------------------------

def test_city_cookie_is_sent_every_time_and_server_cookie_cannot_override(site):
    c = CitilinkClient()
    site.script += [FakeResponse(200, CARD, cookies={"_space": "msk_cl", "qrator_msid2": "q1"}), ok()]
    c.product("1896626", True, "ekat_cl")
    c.product("1896626", True, "ekat_cl")
    assert site.cookies_sent(0)["_space"] == "ekat_cl"
    second = site.cookies_sent(1)
    assert second["_space"] == "ekat_cl" and second["qrator_msid2"] == "q1"


def test_challenge_is_solved_once_and_the_cookie_is_kept(site, tmp_path):
    c = CitilinkClient()
    site.script += [html(429, CHALLENGE), ok(), ok()]
    res = c.product("1896626", True, "ekat_cl")
    assert res.city["id"] == "ekat_cl"
    assert "_pcl" not in site.cookies_sent(0) and site.cookies_sent(1)["_pcl"]
    c.product("1896626", True, "ekat_cl")
    assert site.cookies_sent(2)["_pcl"] == site.cookies_sent(1)["_pcl"]
    session = tmp_path / "cache" / "session.json"
    assert stat.S_IMODE(os.stat(session).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(tmp_path / "cache").st_mode) == 0o700
    # a restarted server reuses the saved cookie instead of solving again
    c2 = CitilinkClient()
    site.script += [ok()]
    c2.product("1896626", True, "ekat_cl")
    assert site.cookies_sent(-1)["_pcl"] == site.cookies_sent(1)["_pcl"]


def test_repeated_challenge_is_an_error_not_a_loop(site):
    c = CitilinkClient()
    site.script += [html(429, CHALLENGE), html(429, CHALLENGE), html(429, CHALLENGE)]
    with pytest.raises(Blocked):
        c.product("1896626", True, "ekat_cl")
    assert len(site.sent) == 2


def test_throttle_gets_one_pause_and_one_retry(site):
    c = CitilinkClient()
    site.script += [FakeResponse(429, {"message": "slow down"}), ok()]
    assert c.product("1896626", True, "ekat_cl").data["product"]["id"] == "1896626"
    site.script += [FakeResponse(429, {}), FakeResponse(503, {}), ok()]
    with pytest.raises(RateLimited):
        c.product("1896626", True, "ekat_cl")
    assert len(site.sent) == 4


def test_edge_403_is_blocked_after_one_retry(site):
    c = CitilinkClient()
    site.script += [html(403, "<html>Forbidden</html>"), html(403, "<html>Forbidden</html>")]
    with pytest.raises(Blocked, match="VPN"):
        c.product("1896626", True, "ekat_cl")
    assert len(site.sent) == 2


def test_wrong_city_twice_is_refused(site):
    c = CitilinkClient()
    site.script += [msk(CARD), msk(CARD)]
    with pytest.raises(CityMismatch, match="Москва"):
        c.product("1896626", True, "ekat_cl")
    assert len(site.sent) == 2


def test_wrong_city_once_is_retried(site):
    c = CitilinkClient()
    site.script += [msk(CARD), ok()]
    assert c.product("1896626", True, "ekat_cl").city["id"] == "ekat_cl"


def test_schema_change_is_not_retried(site):
    c = CitilinkClient()
    site.script += [FakeResponse(400, load("validation_error_400.json"))]
    with pytest.raises(SchemaChanged):
        c.product("1896626", True, "ekat_cl")
    assert len(site.sent) == 1


def test_network_error_is_retried_once(site, monkeypatch):
    import requests
    c = CitilinkClient()
    calls = []

    def flaky(session, url, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise requests.ConnectionError("reset")
        return site.post(url, **kw)

    monkeypatch.setattr(client_mod.requests.Session, "post", flaky)
    site.script += [ok()]
    assert c.product("1896626", True, "ekat_cl").data["product"]
    assert len(calls) == 2


def test_search_sends_slug_sort_and_48_per_page(site):
    site.router = fixture_router
    c = CitilinkClient()
    c.search("бумага a4", 1, "price_asc", 200, 3000, None, "any", "ekat_cl")
    v = site.sent[0]["variables"]["in"]
    assert v["pagination"] == {"page": 1, "perPage": 48}
    assert v["sorting"] == {"id": "price", "direction": "SORT_DIRECTION_ASC"}
    assert v["conditions"] == [{"filterGroupId": "price_filter_group_id", "range": {"fromValue": "200", "toValue": "3000"}}]


def test_search_that_ignored_the_sort_is_refused(site):
    body = load("search_samsung_990_pro.json")   # relevance selected
    site.script += [FakeResponse(200, body), FakeResponse(200, body)]
    c = CitilinkClient()
    with pytest.raises(CitilinkError, match="ignored the sort"):
        c.search("samsung 990 pro", 1, "price_asc", None, None, None, "any", "ekat_cl")
    assert len(site.sent) == 2


def test_search_that_ignored_the_price_window_is_refused(site):
    body = load("search_bumaga_a4_price_asc.json")   # applied window 310–3000
    site.script += [FakeResponse(200, body), FakeResponse(200, body)]
    c = CitilinkClient()
    with pytest.raises(CitilinkError, match="price window"):
        c.search("бумага a4", 1, "price_asc", 200, 1000, None, "any", "ekat_cl")


def test_availability_filter_echo_is_checked(site):
    site.router = fixture_router
    c = CitilinkClient()
    c.search("ssd samsung", 1, "rating", None, None, None, "in_store_now", "ekat_cl")
    assert site.sent[0]["variables"]["in"]["conditions"] == [
        {"filterGroupId": "availability_filter_group_id", "filterIds": ["available.instore"]}]
    body = load("search_ssd_samsung_instore.json")
    for g in body["data"]["fullSearchFilter"]["record"]["groups"]:
        for f in (g["filter"].get("filters") or []):
            f["isSelected"] = False
    site.router = None
    site.script += [FakeResponse(200, body), FakeResponse(200, body)]
    with pytest.raises(CitilinkError, match="availability"):
        c.search("ssd samsung", 1, "rating", None, None, None, "in_store_now", "ekat_cl")


def test_truncated_page_is_refused(site):
    body = load("search_samsung_990_pro.json")
    body["data"]["fullSearchFilter"]["record"]["products"] = []
    site.script += [FakeResponse(200, body), FakeResponse(200, body)]
    with pytest.raises(CitilinkError, match="empty page"):
        CitilinkClient().search("samsung 990 pro", 1, "relevance", None, None, None, "any", "ekat_cl")


# ---- city resolution -----------------------------------------------------------------------------------

def test_default_city_is_yekaterinburg_without_directory(site):
    assert CitilinkClient().resolve_city(None) == ("ekat_cl", "default (Екатеринбург)")
    assert site.sent == []


def test_city_names_resolve_through_the_cached_directory(site, monkeypatch):
    monkeypatch.setattr(client_mod, "MIN_CITIES", 5)   # the fixture is a 12-city excerpt
    site.router = fixture_router
    c = CitilinkClient()
    assert c.resolve_city("Верхняя Пышма") == ("ekat_cl:5x0029236", "parameter")
    assert c.resolve_city("Кировск, Мурманская")[0] == "spb_cl:5x0052133"
    with pytest.raises(client_mod.BadRequest, match="ambiguous"):
        c.resolve_city("Орёл")
    monkeypatch.setenv("CITILINK_CITY", "msk_cl")
    assert c.resolve_city(None) == ("msk_cl", "CITILINK_CITY")
    assert len(site.sent) == 1                         # directory fetched once
    assert "_space" not in site.cookies_sent(0)        # the directory is not city-specific
    assert CitilinkClient().city_directory()           # a new client reads the disk cache
    assert len(site.sent) == 1


def test_a_nearly_empty_directory_is_not_cached(site):
    site.router = fixture_router
    with pytest.raises(CitilinkError, match="nearly empty"):
        CitilinkClient().city_directory()
