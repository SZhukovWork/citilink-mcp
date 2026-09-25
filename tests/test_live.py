"""End-to-end check over the real MCP stdio transport against live citilink.ru (Yekaterinburg).

Deselected by default; run with:  pytest -m live
Needs a Russian IP. Makes 11-12 requests to citilink.ru (12 when the city directory is not
cached yet), spaced by the server.
"""
import json
import sys

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

pytestmark = pytest.mark.live

PRODUCT = "1896626"          # Samsung 990 Pro 1 TB: a product in a 3-variant group
TOOLS = {"search_products", "get_product", "get_reviews", "get_questions", "find_markdown",
         "compare_products", "resolve_city"}


async def _run(calls):
    params = StdioServerParameters(command=sys.executable, args=["-m", "citilink_mcp"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            results = {}
            for key, name, args in calls:
                res = await session.call_tool(name, args)
                assert not res.is_error, (key, res.content)
                results[key] = res.structured_content or json.loads(res.content[0].text)
                if key == "markdown_category":
                    first = next((i for i in results[key]["items"] if i.get("original_product_id")), None)
                    if first:
                        res = await session.call_tool("find_markdown", {"product": first["original_product_id"]})
                        assert not res.is_error, res.content
                        results["markdown_product"] = res.structured_content
            return tools, results


def test_all_tools_over_stdio():
    tools, r = anyio.run(_run, [
        ("search", "search_products", {"query": "холодильник", "sort": "price_asc",
                                       "price_min": 20000, "price_max": 30000}),
        ("search_p2", "search_products", {"query": "холодильник", "sort": "price_asc",
                                          "price_min": 20000, "price_max": 30000, "page": 2}),
        ("product", "get_product", {"product": PRODUCT}),
        ("product_msk", "get_product", {"product": PRODUCT, "city": "msk_cl", "include_stores": False,
                                        "include_specs": False}),
        ("reviews_group", "get_reviews", {"product": PRODUCT, "scope": "group", "sort": "worst", "limit": 5}),
        ("reviews_product", "get_reviews", {"product": PRODUCT, "limit": 5}),
        ("questions", "get_questions", {"product": PRODUCT, "limit": 3}),
        ("markdown_category", "find_markdown", {"category_slug": "noutbuki"}),
        ("compare", "compare_products", {"products": [PRODUCT, "2007107", "123456789"]}),
        ("city", "resolve_city", {"name": "Верхняя Пышма"}),
    ])
    assert tools == TOOLS

    s1, s2 = r["search"], r["search_p2"]
    assert s1["city"]["id"] == "ekat_cl" and s1["city"]["name"] == "Екатеринбург"
    assert s1["returned"] == 48 and s1["has_more"] is True
    ids1, ids2 = {i["id"] for i in s1["items"]}, {i["id"] for i in s2["items"]}
    assert not ids1 & ids2
    if s1["total_found"] <= 96:                       # both pages together are the whole result
        assert s1["returned"] + s2["returned"] == s1["total_found"] and s2["has_more"] is False
    priced = [i["price_rub"] for i in s1["items"] + s2["items"] if i["price_rub"] is not None]
    assert priced == sorted(priced) and 20000 <= min(priced) and max(priced) <= 30000
    assert all(i["price_rub"] is None and i["price_note"] for i in s1["items"] + s2["items"] if "price_note" in i)

    p = r["product"]
    assert p["id"] == PRODUCT and p["price_rub"] and p["mpn"] == "MZ-V9P1T0BW"
    assert p["city"]["id"] == "ekat_cl"
    this, group = p["rating"]["this_product"], p["rating"]["group"]
    assert group["opinions"] >= this["opinions"] > 0
    assert p["rating"]["shown_on_card"]["opinions"] == group["opinions"]
    assert p["own_stores"] and all(s["kind"].startswith("Citilink") for s in p["own_stores"])
    assert p["partner_points"]["count"] > len(p["own_stores"])
    assert p["courier"] and p["courier"][0]["date"] >= p["fetched_at"][:10]

    m = r["product_msk"]
    assert m["city"]["id"] == "msk_cl" and m["city"]["source"] == "parameter"
    assert "own_stores" not in m and "specs" not in m

    rg, rp = r["reviews_group"], r["reviews_product"]
    assert rg["matching_reviews"] >= rp["matching_reviews"] > 0
    assert rp["rating"]["this_product"]["opinions"] == this["opinions"]
    assert all(rv.get("product_id") == PRODUCT for rv in rp["reviews"])

    q = r["questions"]
    assert q["total_questions"] > 0 and q["threads"] and q["threads"][0]["question"]["text"]

    md = r["markdown_category"]
    assert md["city"]["id"] == "ekat_cl"
    assert all(i["id"].startswith("M") and i["is_markdown"] for i in md["items"])
    if md["items"]:
        assert "markdown_product" in r and r["markdown_product"]["city"]["id"] == "ekat_cl"

    c = r["compare"]
    assert [i["id"] for i in c["items"]] == [PRODUCT, "2007107"] and c["not_found"] == ["123456789"]

    assert r["city"]["matches"][0]["id"] == "ekat_cl:5x0029236"
