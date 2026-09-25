"""Parsers on recorded citilink.ru answers: one test per trap from the research report."""
import json

import pytest

from citilink_mcp import cities, parse
from conftest import load


def product(name):
    return load(name)["data"]["product"]


def search_products(name):
    return load(name)["data"]["fullSearchFilter"]["record"]["products"]


# ---- converters ------------------------------------------------------------------------

@pytest.mark.parametrize("raw, rub", [("26990", 26990), ("", None), ("0", None), (None, None), ("abc", None),
                                      ("26 990", 26990), (0, None), (305, 305)])
def test_rub(raw, rub):
    assert parse.rub(raw) == rub


def test_delivery_fee_zero_is_free_not_unknown():
    assert parse.amount("0") == 0 and parse.amount("") is None and parse.amount("750") == 750


def test_dates_are_calendar_days_without_time_zone_shift():
    assert parse.calendar_date("2026-09-26T00:00:00.000Z") == "2026-09-26"
    # a naive UTC->Yekaterinburg conversion would move this to the 26th
    assert parse.calendar_date("2026-09-25T21:00:00.000Z") == "2026-09-25"
    assert parse.calendar_date("1970-01-01T00:00:00.000Z") is None   # Citilink's "no date"


def test_stock_count_keeps_the_sites_words_for_big_stock():
    assert parse.count("24") == 24 and parse.count("0") == 0
    assert parse.count(">50") == ">50"
    assert parse.count("много") is None


def test_zero_rating_means_no_reviews():
    assert parse.rating_value("0.0") is None and parse.rating_value(0) is None
    assert parse.rating_value("4.9") == 4.9


# ---- ids -------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, pid", [
    ("1896626", "1896626"),
    ("M00295786", "M00295786"),
    ("m00295786", "M00295786"),
    ("https://www.citilink.ru/product/ssd-nakopitel-samsung-990-pro-mz-v9p1t0bw-1tb-m-2-2280-pcie-4-0-x4-nvm-1896626/", "1896626"),
    ("https://www.citilink.ru/product/ssd-samsung-1896626/otzyvy/?utm=1", "1896626"),
    ("www.citilink.ru/product/noutbuk-hp-probook-450-g8-M00295786/", "M00295786"),
])
def test_product_id(text, pid):
    assert parse.product_id(text) == pid


@pytest.mark.parametrize("text", ["123456789012", "https://www.ozon.ru/product/x-123/", "abc",
                                  "https://www.citilink.ru/catalog/noutbuki/", ""])
def test_bad_product_ids_are_rejected_before_sending(text):
    # an over-long id makes Citilink drop the whole products(ids) batch
    with pytest.raises(ValueError):
        parse.product_id(text)


def test_archived_card_has_no_url():
    assert parse.product_url("", "99999999") is None


# ---- prices ------------------------------------------------------------------------------------

def test_card_prices_club_shown_and_bonuses_separate():
    block = parse.price_block(product("product_1896626.json")["price"], 1)
    assert block["price_rub"] == 26990
    assert "without signing in" in block["price_label"]
    assert block["club_price_rub"] == 26700 and "Club" in block["club_price_label"]
    assert "old_price_rub" not in block and "lot" not in block
    b = block["bonuses"]
    assert (b["base"], b["extra_for_sbp_payment"], b["site_shows_up_to"]) == (267, 270, 537)  # site: «До 537 бонусов»
    assert "not a discount" in b["label"].lower() or "Not a discount" in b["label"]


def test_skip_club_price_is_not_exposed():
    # TV 2096672 in the research: club 64350 in the API, the page shows only 64 990
    block = parse.price_block({"current": "64990", "old": "", "club": "64350", "clubPriceViewType": "SKIP",
                               "bonusPoints": "643", "sbpBonus": 650, "discount": {"percent": 0}})
    assert block["price_rub"] == 64990
    assert not any(k.startswith("club") for k in block)
    assert "64350" not in json.dumps(block)


def test_invalid_view_type_hides_club_too():
    block = parse.price_block({"current": "1000", "old": "", "club": "900", "clubPriceViewType": "INVALID"})
    assert "club_price_rub" not in block


def test_empty_price_is_null_with_reason_not_zero():
    block = parse.price_block({"current": "", "old": "", "club": "", "clubPriceViewType": "INVALID",
                               "bonusPoints": "", "sbpBonus": 0, "discount": {"percent": 0}})
    assert block["price_rub"] is None and "no price" in block["price_note"]
    assert "bonuses" not in block and "old_price_rub" not in block


@pytest.mark.parametrize("old", ["", "0"])
def test_no_strikethrough_price_when_old_empty_or_zero(old):
    block = parse.price_block({"current": "100", "old": old, "discount": {"percent": 0}})
    assert "old_price_rub" not in block and "discount_percent" not in block


def test_strikethrough_price_and_discount():
    block = parse.price_block(product("product_2203394.json")["price"])
    assert block["old_price_rub"] == 154490 and block["discount_percent"] == 5
    assert block["price_rub"] == 146990


@pytest.mark.parametrize("view, key, value", [
    ("DIFF_IN_RUB", "club_discount_rub", 500),
    ("DIFF_IN_PERCENT", "club_discount_percent", 5),
    ("WANT_TO_KNOW", "club_price_rub", 9500),
])
def test_other_club_display_types_are_labelled_as_unverified(view, key, value):
    block = parse.price_block({"current": "10000", "old": "", "club": "9500", "clubPriceViewType": view})
    assert block[key] == value and block["club_price_rub"] == 9500
    assert "not seen live" in block["club_price_label"]


def test_lot_prices_match_the_rendered_site():
    # DOM of 417267: «4300 −29% 3050₽ · 305 ₽×10 ШТ. В КОРОБКЕ · Клубная цена 2430 ₽ · До 50 бонусов»
    p = product("product_417267.json")
    block = parse.price_block(p["price"], p["multiplicity"])
    assert block["price_rub"] == 305 and "per 1 unit" in block["price_label"]
    assert block["lot"]["units"] == 10
    assert (block["lot"]["price_rub"], block["lot"]["old_price_rub"], block["lot"]["club_price_rub"]) == (3050, 4300, 2430)
    assert block["bonuses"]["site_shows_up_to"] == 50


def test_lot_in_search_results():
    # DOM of 817671: «2450₽ · 490 ₽×5 ШТ. В КОРОБКЕ · До 50 бонусов»
    paper = next(p for p in search_products("search_bumaga_a4_price_asc.json") if p["id"] == "817671")
    item = parse.snippet(paper)
    assert item["price_rub"] == 490 and item["lot"]["price_rub"] == 2450 and item["bonuses"]["site_shows_up_to"] == 50


def test_credit_is_payment_terms_not_price():
    c = parse.credit(product("product_1896626.json")["credit"])
    assert c["credit"]["monthly_rub"] > 0 and "not a price" in c["label"]


# ---- badges ----------------------------------------------------------------------------------------

def test_promo_code_badge_is_parsed_and_kept_apart():
    titles, promos = parse.labels(product("product_2203394.json")["labels"])
    assert promos == [{"badge": "Промокод ИГРАЙ -2000₽", "code": "ИГРАЙ", "discount": "-2000₽",
                       "terms": "Выгода до 5000 рублей на ноутбуки Thunderobot"}]
    assert "Скидка на комплект" in titles


def test_promotions_from_actions():
    promos = parse.promotions(product("product_2203394.json")["actions"])
    assert any("ИГРАЙ" in p["title"] for p in promos)


# ---- ratings -----------------------------------------------------------------------------------------

def test_card_rating_this_product_vs_group():
    r = parse.rating_block(product("product_1896626.json"))
    assert r["this_product"]["value"] == 4.8 and r["this_product"]["opinions"] == 41
    assert r["this_product"]["stars"] == {"5": 34, "4": 5, "3": 2, "2": 0, "1": 0}
    assert r["group"]["value"] == 4.9 and r["group"]["opinions"] == 106
    assert r["shown_on_card"] == {"value": 4.9, "opinions": 106, "scope": "group",
                                  "note": r["shown_on_card"]["note"]}


def test_product_without_reviews_or_group():
    r = parse.rating_block(product("product_M00286335.json"))
    assert r["this_product"]["opinions"] == 0 and "value" not in r["this_product"]
    assert "group" not in r
    assert r["shown_on_card"]["scope"] == "product" and "value" not in r["shown_on_card"]


def test_search_tile_rating_is_group_and_product_rating_is_separate():
    tile = next(p for p in search_products("search_samsung_990_pro.json") if p["id"] == "1896626")
    item = parse.snippet(tile)
    assert item["rating"]["group_shown_on_site"] == {"value": 4.9, "opinions": 106, "scope": "group"}
    assert item["rating"]["this_product"] == {"value": 4.8, "opinions": 41}


def test_site_review_digest_is_labelled():
    d = parse.ai_summary(product("product_1896626.json")["summary"])
    assert d["text"] and "machine-written" in d["label"]


# ---- availability ----------------------------------------------------------------------------------------

def test_own_stores_are_citilinks_with_dates():
    p = product("product_1896626.json")
    rows = parse.own_stores(p["delivery"]["self"]["own"])
    shop = rows[0]
    assert shop["kind"] == "Citilink store" and shop["units_in_store_now"] == 24
    assert shop["pickup_date"] == "2026-09-25" and "Фан-Фан" in shop["store"]
    assert all(r["kind"] in ("Citilink store", "Citilink pickup point") for r in rows)
    assert [r["pickup_date"] for r in rows] == sorted(r["pickup_date"] for r in rows)


def test_partner_points_are_a_separate_summary():
    p = product("product_1896626.json")
    partner = parse.partner_points(p["delivery"]["self"]["partner"])
    own = parse.own_stores(p["delivery"]["self"]["own"])
    shown = sum(d["storeCount"] for d in p["delivery"]["self"]["availabilityByDays"])
    assert partner["count"] + len(own) == shown           # the site's «из 1560 магазинов и пунктов выдачи»
    assert partner["count"] > 1000 and partner["paid_count"] > 0
    assert partner["paid_fee_range_rub"][0] >= 1000
    assert "not Citilink" in partner["label"]


def test_courier_is_from_price():
    rows = parse.courier(product("product_1896626.json")["delivery"]["courier"])
    assert rows == [{"type": "standard", "price_from_rub": 750, "date": "2026-09-26"}]


def test_search_availability_fields():
    items = {p["id"]: parse.snippet(p) for p in search_products("search_ssd_samsung_instore.json")}
    assert items["1896626"]["units_in_city_stores"] == 24
    assert items["1469104"]["units_in_city_stores"] == ">50"
    assert items["1896626"]["pickup_earliest_date"] == "2026-09-25"


# ---- markdown, variants, specs ---------------------------------------------------------------------------

def test_markdown_condition():
    cond = parse.markdown_condition(product("product_M00286335.json")["propertiesGroup"])
    assert cond["reason"] == "Товар с механическими повреждениями"
    assert cond["warranty"] == "1 мес." and cond["wear"] and cond["damage"]


def test_markdown_snippet_points_to_new_product():
    md = load("markdown_noutbuki.json")["data"]["discountFilter"]["record"]["products"]
    item = parse.snippet(md[0])
    assert item["id"].startswith("M") and item["is_markdown"] and item["original_product_id"]
    assert "(Б/У)" in item["name"]


def test_variants_mark_this_product_by_id_not_by_strict_match():
    p = product("product_1896626.json")
    rows = parse.variants(p["variants"], "1896626")
    assert {r["label"]: r["is_this"] for r in rows if r["group"] == "Объем накопителя"} == {"4 ТБ": False, "1024 ГБ": True}


def test_specs_model_warranty_country():
    groups = product("product_1896626.json")["propertiesGroup"]
    assert parse.prop(groups, "Модель") == "MZ-V9P1T0BW"
    assert parse.prop(groups, "Страна производства") == "КОРЕЯ"
    specs = parse.specs(groups)
    assert specs["Основные характеристики"]["Объем накопителя"] == "1024 ГБ"


def test_archived_snippet():
    raw = {"id": "99999999", "name": "Процессор AMD Sempron", "shortName": "Процессор AMD Sempron", "slug": "",
           "isAvailable": False, "isDiscount": False, "originalProductId": None, "multiplicity": 1,
           "price": {"current": "", "old": "", "club": "", "clubPriceViewType": "INVALID", "bonusPoints": "",
                     "sbpBonus": 0, "discount": {"percent": 0}},
           "rating": "0.0", "counters": {"opinions": 0}, "brand": {"name": "AMD"}, "category": None, "labels": [],
           "stock": {"countInStores": "0"}}
    item = parse.snippet(raw)
    assert item["archived"] and item["price_rub"] is None and "url" not in item


# ---- reviews and questions ---------------------------------------------------------------------------------

def test_group_reviews_name_the_other_variant_and_import_source():
    item = {"displayedDate": "2025-06-02T14:27:39.000Z", "rating": 5, "pros": "", "cons": "", "text": "Быстрый",
            "fromGroup": True, "isBest": False,
            "source": {"sourceType": "YAMARKET", "description": "Отзыв из открытых источников"},
            "product": {"id": "1899984", "shortName": "2ТБ SSD накопитель Samsung 990 Pro"},
            "voteInfo": {"info": {"counters": {"likes": 0, "dislikes": 0}}}}
    r = parse.review(item, "1896626")
    assert r["other_variant"] == "2ТБ SSD накопитель Samsung 990 Pro" and r["product_id"] == "1899984"
    assert r["source"] == "YAMARKET" and "not a verified" in r["source_label"]
    # fromGroup is true for every review in group mode — even this product's own
    own = parse.review({**item, "product": {"id": "1896626"}}, "1896626")
    assert "other_variant" not in own


def test_reviews_never_carry_author_data():
    item = {"id": 1, "displayedDate": "2026-09-15T21:48:00.000Z", "rating": 1,
            "authorNickname": "Иван Петров", "author": {"b2c": {"userInfo": {"firstName": "Иван", "nickname": "ivan_p"}}},
            "pros": "Пишите на ivan.petrov@example.com", "cons": "звоните +7 (900) 000-00-00 или 8 900 000 00 00",
            "text": "Заказ оформлял сам", "source": {"sourceType": "CITILINK"}, "product": {"id": "1"}}
    out = json.dumps(parse.review(item, "1"), ensure_ascii=False)
    for secret in ("Иван", "Петров", "ivan", "example.com", "000-00-00", "000 00 00"):
        assert secret not in out
    assert "[e-mail removed]" in out and "[phone removed]" in out


def test_question_threads():
    items = load("questions_1896626.json")["data"]["product"]["discussion"]["items"]
    threads = parse.threads(items)
    assert len(threads) == 5 and all(t["question"] for t in threads)
    assert all(a.get("by_citilink_expert") for t in threads for a in t["answers"])
    assert "nickname" not in json.dumps(threads, ensure_ascii=False).lower()


def test_threads_reply_to_answer_and_orphans():
    items = [
        {"id": "q", "depth": 0, "text": "Подойдёт?", "creationDate": "2026-09-09T17:09:54.000Z"},
        {"id": "a", "depth": 1, "parent": {"id": "q"}, "text": "Да", "creationDate": "2026-09-09T18:00:00.000Z",
         "author": {"b2c": {"expert": {"isExpert": True}}}},
        {"id": "b", "depth": 2, "parent": {"id": "a"}, "text": "Спасибо", "isBuyer": True,
         "creationDate": "2026-09-10T08:00:00.000Z"},
        {"id": "c", "depth": 1, "parent": {"id": "elsewhere"}, "text": "Ответ на вопрос с другой страницы"},
        {"id": "d", "depth": 1, "parent": {"id": "q"}, "text": "Совместим", "author": {"vendor": {"brand": {"name": "SAMSUNG"}}}},
    ]
    t = parse.threads(items)
    assert [a["text"] for a in t[0]["answers"]] == ["Совместим", "Да", "Спасибо"]
    assert any(a.get("reply_to_answer") for a in t[0]["answers"])
    assert any(a.get("by_brand") == "SAMSUNG" for a in t[0]["answers"])
    assert t[-1]["question"] is None and t[-1]["answers"][0]["text"].startswith("Ответ")


# ---- cities -----------------------------------------------------------------------------------------------------

def directory():
    return cities.compact(load("cities_sample.json")["data"])


def test_city_directory_and_lookup():
    d = directory()
    assert cities.find(d, "Екатеринбург")[0]["id"] == "ekat_cl"
    assert [c["id"] for c in cities.find(d, "Кировск, Мурманская")] == ["spb_cl:5x0052133"]
    assert len(cities.find(d, "Березовский")) == 2
    assert {c["id"] for c in cities.find(d, "орел")} == {"msk_cl:tulorel2", "kzn_cl:ya0017807"}   # ё = е
    assert cities.find(d, "верхняя пышма")[0]["space_id"] == "ekat_cl"


def test_city_ids():
    assert cities.is_city_id("ekat_cl") and cities.is_city_id("ekat_cl:5x0029236")
    assert not cities.is_city_id("Екатеринбург") and not cities.is_city_id("ekat")


def test_listing_items_carry_no_repeated_labels_but_keep_unusual_ones():
    tile = next(p for p in search_products("search_samsung_990_pro.json") if p["id"] == "1896626")
    item = parse.snippet(tile)
    assert "price_label" not in item and "club_price_label" not in item and "label" not in item["bonuses"]
    diff = dict(tile, price={**tile["price"], "clubPriceViewType": "DIFF_IN_RUB"})
    assert "not seen live" in parse.snippet(diff)["club_price_label"]
    assert set(parse.LEGEND) >= {"price_rub", "club_price_rub", "bonuses", "rating", "lot"}
