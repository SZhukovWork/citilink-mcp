"""Tool registration, schemas and tool outputs (real client, fake citilink.ru answering from fixtures)."""
import json

import anyio
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from citilink_mcp import server
from conftest import FakeResponse, fixture_router, load

TOOLS = {"search_products", "get_product", "get_reviews", "get_questions", "find_markdown",
         "compare_products", "resolve_city"}


def tools():
    return {t.name: t for t in anyio.run(server.mcp.list_tools)}


def call(fn, *args, **kwargs):
    return fn(*args, **kwargs).structured_content


@pytest.fixture
def srv(site, monkeypatch):
    monkeypatch.setattr(server, "_client", None)
    site.router = fixture_router
    return site


# ---- registration and schemas ---------------------------------------------------------------

def test_tools_are_registered_read_only():
    t = tools()
    assert set(t) == TOOLS
    for tool in t.values():
        assert tool.annotations.read_only_hint is True and tool.annotations.destructive_hint is False
        assert tool.description


def test_enumerations_and_limits_in_schemas():
    t = tools()
    s = t["search_products"].input_schema["properties"]
    assert set(s["sort"]["enum"]) == {"relevance", "price_asc", "price_desc", "discount", "rating", "opinions"}
    assert set(s["availability"]["enum"]) == {"any", "in_store_now", "pickup", "delivery"}
    assert s["limit"]["maximum"] == 48                        # never more than Citilink's page
    r = t["get_reviews"].input_schema["properties"]
    assert set(r["scope"]["enum"]) == {"product", "group"} and r["scope"]["default"] == "product"
    assert set(r["sort"]["enum"]) == {"helpful", "newest", "oldest", "best", "worst"}
    assert t["compare_products"].input_schema["properties"]["products"]["maxItems"] == 20
    assert set(t["get_questions"].input_schema["properties"]["sort"]["enum"]) == {"newest", "likes"}


def test_instructions_explain_prices_ratings_and_injection():
    text = server.INSTRUCTIONS
    for phrase in ("club_price_rub", "bonuses", "this_product", "partner_points", "never", "instructions"):
        assert phrase in text


# ---- errors reach the agent ----------------------------------------------------------------------

def test_error_reason_reaches_the_agent(srv):
    srv.router = None
    srv.script += [FakeResponse(429, {}), FakeResponse(429, {})]
    with pytest.raises(ToolError, match="rate limiting"):
        server.get_product("1896626")


def test_bad_id_is_a_clear_tool_error(srv):
    with pytest.raises(ToolError, match="not a Citilink product id"):
        server.get_product("123456789012")
    assert srv.sent == []


def test_unexpected_exception_is_a_tool_error(monkeypatch):
    class Broken:
        def resolve_city(self, city):
            raise KeyError("boom")

    monkeypatch.setattr(server, "_client", Broken())
    with pytest.raises(ToolError, match="Unexpected error"):
        server.search_products("ssd")


# ---- outputs --------------------------------------------------------------------------------------------

def test_search_output(srv):
    r = call(server.search_products, "samsung 990 pro")
    assert r["total_found"] == 4 and r["returned"] == 4 and r["has_more"] is False
    assert r["city"]["id"] == "ekat_cl" and r["city"]["name"] == "Екатеринбург"
    assert r["city"]["source"].startswith("default") and r["fetched_at"]
    item = next(i for i in r["items"] if i["id"] == "1896626")
    assert item["rating"]["this_product"]["value"] == 4.8
    assert item["rating"]["group_shown_on_site"]["scope"] == "group"
    assert item["url"].endswith("-1896626/")
    assert r["categories"][0]["slug"] == "ssd-nakopiteli"
    assert r["price_range_rub"]["min"] <= r["price_range_rub"]["max"]


def test_search_limit_is_reported(srv):
    r = call(server.search_products, "samsung 990 pro", limit=2)
    assert r["returned"] == 2 and r["cut_by_limit"] == 2


def test_has_more_is_computed_not_taken_from_citilink(srv):
    body = load("search_samsung_990_pro.json")
    info = body["data"]["fullSearchFilter"]["record"]["pageInfo"]
    info.update({"totalItems": 57, "totalPages": 1, "hasNextPage": False})   # what Citilink says after capping
    srv.router = lambda b: FakeResponse(200, body)
    assert call(server.search_products, "samsung 990 pro")["has_more"] is True


def test_price_window_validation(srv):
    with pytest.raises(ToolError, match="greater"):
        server.search_products("ssd", price_min=5000, price_max=100)


def test_get_product_output(srv):
    r = call(server.get_product, "https://www.citilink.ru/product/x-1896626/")
    assert r["id"] == "1896626" and r["mpn"] == "MZ-V9P1T0BW" and r["model"] == "MZ-V9P1T0BW"
    assert r["price_rub"] == 26990 and r["club_price_rub"] == 26700
    assert r["rating"]["this_product"]["value"] == 4.8 and r["rating"]["group"]["opinions"] == 106
    assert r["rating"]["shown_on_card"]["scope"] == "group"
    assert r["courier"][0]["price_from_rub"] == 750
    assert r["own_stores"][0]["units_in_store_now"] == 24
    assert r["partner_points"]["count"] > 1000
    assert r["warranty"] and r["country"] == "КОРЕЯ" and r["specs"]
    assert any(v["is_this"] for v in r["variants"])
    assert r["payment"]["prepayment_required"] is False
    assert r["city"]["id"] == "ekat_cl"


def test_get_product_lot_and_markdown(srv):
    lot = call(server.get_product, "417267")
    assert lot["lot"]["price_rub"] == 3050 and lot["price_rub"] == 305
    md = call(server.get_product, "M00286335", include_specs=False)
    assert md["is_markdown"] and md["original_product_id"] == "1856569"
    assert md["markdown"]["reason"] and md["markdown"]["warranty"] == "1 мес."
    assert "specs" not in md and "group" not in md["rating"]


def test_get_product_promo_codes_are_not_subtracted(srv):
    r = call(server.get_product, "2203394")
    assert r["promo_codes"][0]["code"] == "ИГРАЙ" and r["price_rub"] == 146990
    assert "not included" in r["promo_note"]


def test_compare_lists_unknown_and_malformed_ids(srv):
    r = call(server.compare_products, ["1896626", "417267", "M00286335", "123456789", "1896626", "не id"])
    assert [i["id"] for i in r["items"]] == ["1896626", "417267", "M00286335"]
    assert r["not_found"] == ["123456789"]
    assert r["invalid"][0]["input"] == "не id"
    assert srv.sent[0]["variables"]["ids"] == ["1896626", "417267", "M00286335", "123456789"]
    first = r["items"][0]
    assert first["rating"]["this_product"]["opinions"] == 41 and first["courier"][0]["type"] == "standard"


def test_reviews_output(srv):
    r = call(server.get_reviews, "1896626", scope="group", sort="worst", stars=[3, 1, 2])
    assert r["stars_filter"] == [1, 2, 3]
    assert r["matching_reviews"] == 2 and r["returned"] == 2
    # the headline ratings stay unfiltered even with a star filter
    assert r["rating"]["this_product"]["value"] == 4.8 and r["rating"]["group"]["value"] == 4.9
    assert srv.sent[0]["variables"]["in"] == {"pagination": {"page": 1, "perPage": 20}, "sort": "WORST",
                                             "withGroup": True, "ratings": [1, 2, 3]}
    assert all(rv["source"] == "CITILINK" for rv in r["reviews"])
    assert "nickname" not in json.dumps(r, ensure_ascii=False).lower()


def test_reviews_product_scope(srv):
    r = call(server.get_reviews, "1896626", limit=5, sort="newest")
    assert r["scope"] == "product" and r["returned"] == 5 and r["has_more"] is True
    assert srv.sent[0]["variables"]["in"]["withGroup"] is False


def test_questions_output(srv):
    r = call(server.get_questions, "1896626", limit=5)
    assert r["total_questions"] == 16 and r["returned_questions"] == 5 and r["has_more"] is True
    assert r["threads"][0]["answers"][0]["by_citilink_expert"] is True


def test_find_markdown_by_category(srv):
    r = call(server.find_markdown, category_slug="noutbuki")
    assert r["total_found"] == 4 and all(i["id"].startswith("M") for i in r["items"])
    assert r["items"][0]["condition"]["reason"] and r["items"][0]["original_product_id"]
    assert srv.sent[0]["variables"]["in"]["sorting"] == {"id": "price", "direction": "SORT_DIRECTION_ASC"}


def test_find_markdown_by_product(srv):
    r = call(server.find_markdown, product="1856569")
    assert r["items"][0]["id"] == "M00286335" and r["items"][0]["condition"]["warranty"] == "1 мес."


def test_find_markdown_needs_exactly_one_target(srv):
    with pytest.raises(ToolError, match="exactly one"):
        server.find_markdown()
    with pytest.raises(ToolError, match="exactly one"):
        server.find_markdown(category_slug="noutbuki", product="1856569")


def test_city_name_parameter_and_echo(srv, monkeypatch):
    from citilink_mcp import client as client_mod
    monkeypatch.setattr(client_mod, "MIN_CITIES", 5)
    body = load("product_1896626_msk_nostores.json")
    route = srv.router
    srv.router = lambda b: FakeResponse(200, body) if b["query"].startswith("query Product") else route(b)
    r = call(server.get_product, "1896626", include_stores=False, city="Москва")
    assert r["city"] == {"id": "msk_cl", "name": "Москва", "region": "Москва", "source": "parameter",
                         "note": r["city"]["note"]}
    assert srv.cookies_sent(-1)["_space"] == "msk_cl"
    assert r["courier"][0]["price_from_rub"] == 1300 and r["units_in_city_stores"] == 3
    assert "own_stores" not in r


def test_resolve_city(srv, monkeypatch):
    from citilink_mcp import client as client_mod
    monkeypatch.setattr(client_mod, "MIN_CITIES", 5)
    r = call(server.resolve_city, "Березовский")
    assert r["total_matches"] == 2
    assert {m["id"] for m in r["matches"]} == {"ekat_cl:ekatberez", "kmr_cl:5x0051323"}


def test_lists_explain_labels_once(srv):
    r = call(server.search_products, "samsung 990 pro")
    assert "without signing in" in r["legend"]["price_rub"]
    assert all("price_label" not in i for i in r["items"])
    c = call(server.compare_products, ["1896626"])
    assert "courier" in c["legend"]


def test_preorder_flag_is_passed_on(srv):
    body = load("product_1896626.json")
    body["data"]["product"]["isPreorderVAD"] = True
    srv.router = lambda b: FakeResponse(200, body)
    assert "pre-order" in call(server.get_product, "1896626")["preorder"]


def test_fixtures_hold_no_html_cookies_or_author_names():
    from conftest import FIXTURES
    for path in FIXTURES.glob("*.json"):
        text = path.read_text()
        for marker in ("__NEXT_DATA__", "x-forwarded-for", "qrator_msid2", "_pcl", "authorNickname\":\"",
                       "firstName", "userInfo"):
            assert marker not in text, (path.name, marker)
