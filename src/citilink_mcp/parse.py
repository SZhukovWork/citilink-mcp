"""Pure functions: Citilink GraphQL payloads -> honest, labelled dicts. No I/O here.

Conventions:
- money is whole rubles (``int``); a missing price is ``None`` with a reason,
  never 0. Citilink sends prices as strings where ``""`` means "no price" and
  ``"0"`` means "no strikethrough price".
- dates are calendar dates ``YYYY-MM-DD``: Citilink encodes a day as
  midnight UTC (``2026-09-26T00:00:00.000Z``) and means that very day, so the
  date part is taken as is, without any time-zone shift.
- ratings: ``0``/``"0.0"`` means "no reviews", reported as ``None``.
"""
from __future__ import annotations

import re
from typing import Any, Iterable
from urllib.parse import urlparse

SITE = "https://www.citilink.ru"

# ---- small converters ----------------------------------------------------------


def rub(value: Any) -> int | None:
    """A price string -> rubles; "" / "0" / garbage -> None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    text = str(value).replace(" ", "").replace(" ", "").strip()
    if not text.isdigit():
        return None
    number = int(text)
    return number if number > 0 else None


def amount(value: Any) -> int | None:
    """A delivery fee: "" -> None (unknown), "0" -> 0 (the site says free)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value >= 0 else None
    text = str(value).replace(" ", "").replace(" ", "").strip()
    return int(text) if text.isdigit() else None


def count(value: Any) -> int | str | None:
    """A unit count as Citilink sends it: "24" -> 24; ">50" stays ">50" (the site's own
    words for a big stock — not turned into a made-up number); anything else -> None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    return text if re.fullmatch(r"[<>]\s*\d+", text) else None


_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def calendar_date(value: Any) -> str | None:
    """'2026-09-26T00:00:00.000Z' -> '2026-09-26' (no time-zone shift, see module doc).

    Citilink's "no date" placeholder (the Unix epoch) is None.
    """
    m = _DATE.match(str(value or ""))
    if not m or m.group(1).startswith("1970-"):
        return None
    return m.group(1)


def rating_value(value: Any) -> float | None:
    """'4.9' / 4.9 -> 4.9; '0.0' / 0 / '' -> None (no reviews, not a zero rating)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 2) if number > 0 else None


def clean(text: Any) -> str | None:
    if text is None:
        return None
    out = re.sub(r"\s+", " ", str(text)).strip()
    return out or None


# ---- identifiers and URLs --------------------------------------------------------

_ID = re.compile(r"^(\d{1,9}|M\d{1,9})$")


def product_id(text: str) -> str:
    """Product id from an id, a markdown id (M00295786) or a citilink.ru product URL.

    Ids are validated before they are sent: an over-long number makes Citilink
    drop a whole batch (``products: null``), not just the bad id.
    """
    raw = (text or "").strip()
    if raw.lower().startswith(("http://", "https://", "www.", "citilink.ru")):
        url = raw if "://" in raw else "https://" + raw
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not (host == "citilink.ru" or host.endswith(".citilink.ru")):
            raise ValueError(f"Not a citilink.ru URL: {raw}")
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2 or parts[0] != "product":
            raise ValueError(f"Not a citilink.ru product URL (expected /product/<name>-<id>/): {raw}")
        candidate = parts[1].rsplit("-", 1)[-1]
    else:
        candidate = raw
    if candidate[:1] in ("m",):
        candidate = "M" + candidate[1:]
    if not _ID.match(candidate):
        raise ValueError(
            f"'{text}' is not a Citilink product id: expected digits (e.g. 1896626), "
            "a markdown id (e.g. M00295786) or a product URL"
        )
    return candidate


def product_url(slug: str | None, pid: str) -> str | None:
    """Canonical product URL; None for archived cards, which have no slug (and no page)."""
    return f"{SITE}/product/{slug}-{pid}/" if slug else None


# ---- prices ------------------------------------------------------------------------

PRICE_LABEL = "regular price on the site for everyone, without signing in, in the city from `city`"
CLUB_LABEL = (
    "Citilink Club price: applies when the order is placed signed in with the Club card "
    "(the card comes with a free site registration). Conditional — not the regular price"
)
BONUS_LABEL = (
    "Citilink Club bonus points for FUTURE purchases (1 point = 1 ₽ off a later order, capped by "
    "Club status). Not a discount on this item and not included in any price here. `base` is for "
    "the entry «Базовый» status; `extra_for_sbp_payment` only when paying via SBP; "
    "`site_shows_up_to` is the «До N бонусов» figure on the page"
)
NO_PRICE_NOTE = "no price: Citilink does not sell this item in this city right now (the site shows no price)"

_CLUB_SHOWN = {"PRICE_IN_RUB", "DIFF_IN_RUB", "DIFF_IN_PERCENT", "WANT_TO_KNOW"}


def price_block(price: dict | None, multiplicity: Any = 1) -> dict[str, Any]:
    """Every price of one product, each labelled; per unit, plus the lot when sold in lots.

    Rules checked against the rendered site (see README, "What the numbers mean"):
    - `current` "" -> no price (the site's own code turns it into "0");
    - `old` "" or "0" -> no strikethrough price;
    - `club` is shown only for clubPriceViewType PRICE_IN_RUB (verified) and the
      DIFF_*/WANT_TO_KNOW variants (from the site's render code, not seen live);
      SKIP / INVALID / UNKNOWN -> the site hides it, so do we, even when `club` is filled;
    - multiplicity > 1 -> the site's headline is the price of the lot
      (seen on the page: "3050 ₽ · 305 ₽×10 шт. в коробке"): reported in `lot`.
    """
    price = price or {}
    lot_size = multiplicity if isinstance(multiplicity, int) and multiplicity > 1 else 1
    current = rub(price.get("current"))
    old = rub(price.get("old"))
    if current is not None and old is not None and old <= current:
        old = None
    out: dict[str, Any] = {"price_rub": current}
    if current is None:
        out["price_note"] = NO_PRICE_NOTE
    else:
        out["price_label"] = PRICE_LABEL + ("; per 1 unit — sold only in lots, see `lot`" if lot_size > 1 else "")
    if old is not None:
        out["old_price_rub"] = old
        percent = (price.get("discount") or {}).get("percent")
        if isinstance(percent, int) and percent > 0:
            out["discount_percent"] = percent
    club = club_price(price, current)
    if club:
        out.update(club)
    bonus = bonuses(price, lot_size)
    if bonus:
        out["bonuses"] = bonus
    if lot_size > 1 and current is not None:
        lot: dict[str, Any] = {
            "units": lot_size,
            "price_rub": current * lot_size,
            "label": (
                f"sold only in lots of {lot_size} (the site shows «{current} ₽×{lot_size} шт.» under the price); "
                "this lot price is the big number on the page and the minimum you pay"
            ),
        }
        if old is not None:
            lot["old_price_rub"] = old * lot_size
        if club and "club_price_rub" in club:
            lot["club_price_rub"] = club["club_price_rub"] * lot_size
        out["lot"] = lot
    return out


def club_price(price: dict, current: int | None) -> dict[str, Any] | None:
    view = price.get("clubPriceViewType")
    club = rub(price.get("club"))
    if view not in _CLUB_SHOWN or club is None or current is None or club >= current:
        return None
    out: dict[str, Any] = {"club_price_rub": club}
    if view == "PRICE_IN_RUB":
        out["club_price_label"] = CLUB_LABEL + "; the site shows «Клубная цена N ₽»"
    elif view == "DIFF_IN_RUB":
        out["club_discount_rub"] = current - club
        out["club_price_label"] = (
            CLUB_LABEL + f"; the site shows it as «−{current - club} ₽ Клубная скидка» "
            "(display type taken from the site's code, not seen live)"
        )
    elif view == "DIFF_IN_PERCENT":
        percent = round((current - club) * 100 / current)
        out["club_discount_percent"] = percent
        out["club_price_label"] = (
            CLUB_LABEL + f"; the site shows it as «−{percent} % Клубная скидка» "
            "(display type taken from the site's code, not seen live)"
        )
    else:  # WANT_TO_KNOW
        out["club_price_label"] = (
            CLUB_LABEL + "; display type WANT_TO_KNOW, rendered like «Клубная цена» per the site's "
            "code, not seen live"
        )
    return out


def bonuses(price: dict, lot_size: int = 1) -> dict[str, Any] | None:
    base = rub(price.get("bonusPoints")) or 0
    sbp = price.get("sbpBonus")
    sbp = sbp if isinstance(sbp, int) and sbp > 0 else 0
    if not base and not sbp:
        return None
    out: dict[str, Any] = {
        "base": base * lot_size,
        "extra_for_sbp_payment": sbp * lot_size,
        "site_shows_up_to": (base + sbp) * lot_size,
        "label": BONUS_LABEL + ("; counted for the whole lot, as on the site" if lot_size > 1 else ""),
    }
    return out


def credit(block: dict | None) -> dict[str, Any] | None:
    """Loan / instalment offers: payment terms, not prices."""
    out: dict[str, Any] = {}
    for key in ("credit", "installment"):
        c = (block or {}).get(key) or {}
        monthly = rub(c.get("payment"))
        if monthly and c.get("period"):
            out[key] = {"monthly_rub": monthly, "months": c["period"], "program": clean(c.get("name"))}
    if not out:
        return None
    out["label"] = "bank loan / instalment terms offered on the page (approval needed); a way to pay, not a price"
    return out


# ---- labels and promotions --------------------------------------------------------

_PROMO = re.compile(r"^\s*Промокод\s+(\S+)\s+([−-]\s*[\d\s]+\s*(?:₽|%))", re.IGNORECASE)


def labels(items: Iterable[dict] | None) -> tuple[list[str], list[dict]]:
    """Site badges -> (plain titles, promo codes). Promo codes are conditional:
    they are typed in at checkout and are never subtracted from a price here."""
    titles: list[str] = []
    promos: list[dict] = []
    for label in items or []:
        title = clean(label.get("title"))
        if not title:
            continue
        if label.get("type") == "PROMOCODE":
            m = _PROMO.match(title)
            promo: dict[str, Any] = {"badge": title}
            if m:
                promo["code"] = m.group(1)
                promo["discount"] = re.sub(r"\s+", "", m.group(2)).replace("−", "-")
            desc = clean(label.get("description"))
            if desc:
                promo["terms"] = desc
            promos.append(promo)
        else:
            titles.append(title)
    return titles, promos


PROMO_NOTE = (
    "promo codes are conditional discounts entered at checkout (terms, limits and eligible "
    "items are set by Citilink); not included in any price here and not verified by this server"
)


def promotions(actions: dict | None) -> list[dict]:
    out = []
    for item in (actions or {}).get("items") or []:
        title = clean(item.get("shortDescription"))
        if not title:
            continue
        row = {"title": title, "type": clean(((item.get("type") or {}).get("name")))}
        terms = clean(item.get("disclaimer"))
        if terms:
            row["terms"] = terms
        out.append({k: v for k, v in row.items() if v})
    return out


# ---- ratings -------------------------------------------------------------------------


def stars(counters: Iterable[dict] | None) -> dict[str, int] | None:
    out = {}
    for c in counters or []:
        r, n = c.get("rating"), c.get("count")
        if isinstance(r, int) and isinstance(n, int):
            out[str(r)] = n
    if not out or not any(out.values()):
        return None
    return {k: out[k] for k in sorted(out, reverse=True)}


def opinions_payload(block: dict | None) -> dict:
    return ((block or {}).get("payload")) or {}


def own_rating(block: dict | None) -> dict[str, Any] | None:
    """The product's own rating (withGroup:false) from a search/batch alias."""
    payload = opinions_payload(block)
    if not payload:
        return None
    total = payload.get("totalOpinionsForProduct")
    return {"value": rating_value((payload.get("summary") or {}).get("rating")),
            "opinions": total if isinstance(total, int) else None}


GROUP_SCOPE_NOTE = (
    "shown on the site's search tiles and product header: for a product with variants "
    "(capacity, colour…) it covers ALL variants of the group; a product without variants "
    "is its own group"
)


def shown_rating(p: dict) -> dict[str, Any]:
    return {
        "value": rating_value(p.get("rating")),
        "opinions": (p.get("counters") or {}).get("opinions"),
        "scope": "group",
        "note": GROUP_SCOPE_NOTE,
    }


def rating_block(product: dict) -> dict[str, Any]:
    """Card ratings: this product vs its variant group vs what the header shows."""
    own = opinions_payload(product.get("own"))
    group_block = product.get("group") or {}
    group = opinions_payload(group_block)
    have_group = bool(own.get("haveGroup"))
    this = {
        "value": rating_value((own.get("summary") or {}).get("rating")),
        "opinions": own.get("totalOpinionsForProduct"),
        "stars": stars((own.get("summary") or {}).get("ratingCounters")),
        "source": "the «Этот вариант товара» reviews tab: reviews of this very product",
    }
    out: dict[str, Any] = {"this_product": _prune(this)}
    if have_group:
        out["group"] = _prune({
            "value": rating_value((group.get("summary") or {}).get("rating")),
            "opinions": (group_block.get("pageInfo") or {}).get("totalItems"),
            "stars": stars((group.get("summary") or {}).get("ratingCounters")),
            "source": "the «Все отзывы» tab: all variants of the group together",
        })
    shown = {
        "value": rating_value(product.get("rating")),
        "opinions": (product.get("counters") or {}).get("opinions"),
        "scope": "group" if have_group else "product",
    }
    if have_group:
        shown["note"] = "the header number covers the whole variant group; compare products by `this_product`"
    out["shown_on_card"] = _prune(shown)
    if this["opinions"] == 0:
        out["this_product"]["note"] = "no reviews of this product yet"
    return out


def ai_summary(summary: dict | None) -> dict[str, Any] | None:
    s = (summary or {}).get("productAISummary") or {}
    text = clean(s.get("text"))
    if not text:
        return None
    return _prune({
        "text": text,
        "positive": [clean(x) for x in s.get("positive") or [] if clean(x)] or None,
        "negative": [clean(x) for x in s.get("negative") or [] if clean(x)] or None,
        "label": ("machine-written digest of reviews published by Citilink on the page; it sits next "
                  "to the group rating, so it likely covers all variants; a summary, not a review"),
    })


# ---- availability ------------------------------------------------------------------------

STORE_FORMATS = {
    "FULL_FORMAT_SHOP": "Citilink store",
    "OWN_POINT_OF_EXPORT": "Citilink pickup point",
    "OUTSOURCE": "partner pickup point (not Citilink)",
}


def pickup_days(days: Iterable[dict] | None) -> list[dict]:
    out = []
    for d in days or []:
        date = calendar_date(d.get("deliveryTime"))
        n = d.get("storeCount")
        if date and isinstance(n, int) and n > 0:
            out.append({"date": date, "points": n})
    return out


def pickup_earliest(days: Iterable[dict] | None) -> str | None:
    rows = pickup_days(days)
    return min(r["date"] for r in rows) if rows else None


def own_stores(block: dict | None) -> list[dict]:
    """Citilink's own stores and pickup points (with their pickup date), one row per point."""
    rows = []
    for day in (block or {}).get("deliveries") or []:
        date = calendar_date(day.get("deliveryTime"))
        for item in ((day.get("stores") or {}).get("items")) or []:
            store = item.get("storeShort") or {}
            fmt = store.get("format")
            rows.append(_prune({
                "store": clean(store.get("name")),
                "address": clean(store.get("address")),
                "kind": STORE_FORMATS.get(fmt, fmt),
                "units_in_store_now": item.get("availableCount") if isinstance(item.get("availableCount"), int) else None,
                "pickup_date": date,
                "pickup_fee_rub": amount(item.get("deliveryPrice")),
            }))
    rows.sort(key=lambda r: (r.get("pickup_date") or "9999", r.get("store") or ""))
    return rows


def partner_points(block: dict | None) -> dict[str, Any] | None:
    """Partner (OUTSOURCE) pickup points as a summary, never mixed with Citilink's own."""
    total, paid, dates, fees = 0, 0, [], []
    for day in (block or {}).get("deliveries") or []:
        stores = day.get("stores") or {}
        items = stores.get("items") or []
        n = stores.get("total") if isinstance(stores.get("total"), int) else len(items)
        if not n:
            continue
        total += n
        date = calendar_date(day.get("deliveryTime"))
        if date:
            dates.append(date)
        for item in items:
            fee = amount(item.get("deliveryPrice"))
            if fee:
                paid += 1
                fees.append(fee)
    if not total:
        return None
    out: dict[str, Any] = {
        "count": total,
        "earliest_date": min(dates) if dates else None,
        "paid_count": paid,
        "label": ("partner pickup points (other companies' outlets) across Citilink's delivery area — "
                  "several regions, not only this city; not Citilink stores. Pickup there is "
                  "free unless counted in paid_count"),
    }
    if fees:
        out["paid_fee_range_rub"] = [min(fees), max(fees)]
    return out


COURIER_TYPES = {"STANDARD": "standard", "FAST": "fast", "TODAY": "today"}


def courier(block: dict | None) -> list[dict]:
    rows = []
    for v in (block or {}).get("variants") or []:
        rows.append(_prune({
            "type": COURIER_TYPES.get(v.get("type"), v.get("type")),
            "price_from_rub": amount(v.get("price")),
            "date": calendar_date(v.get("deliveryTime")),
        }))
    return rows


COURIER_LABEL = ("courier delivery in the city from `city`: the site says «от N ₽» — the final fee "
                 "depends on the address and is set at checkout")


# ---- properties -----------------------------------------------------------------------------


def specs(groups: Iterable[dict] | None) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for g in groups or []:
        rows = {}
        for prop in g.get("properties") or []:
            name, value = clean(prop.get("name")), clean(prop.get("value"))
            if name and value:
                measure = clean(prop.get("measure"))
                rows[name] = f"{value} {measure}" if measure else value
        if rows:
            out.setdefault(clean(g.get("name")) or "Прочее", {}).update(rows)
    return out


def prop(groups: Iterable[dict] | None, *names: str) -> str | None:
    wanted = [n.lower() for n in names]
    for rows in specs(groups).values():
        for key, value in rows.items():
            if key.lower() in wanted:
                return value
    return None


def markdown_condition(groups: Iterable[dict] | None) -> dict[str, Any]:
    """What the seller says about a markdown/used item (Причины уценки, Комплектация, износ…)."""
    out: dict[str, Any] = {}
    for rows in specs(groups).values():
        for key, value in rows.items():
            k = key.lower()
            if "причин" in k and "уцен" in k:
                out["reason"] = value
            elif "комплектац" in k:
                out["completeness"] = value
            elif "износ" in k or "следы" in k:
                out["wear"] = value
            elif "поврежд" in k:
                out["damage"] = value
            elif k == "гарантия":
                out["warranty"] = value
    return out


def variants(groups: Iterable[dict] | None, this_id: str) -> list[dict]:
    """Neighbouring variants (capacity, colour…); `is_this` from the product id —
    Citilink's `isStrictMatch` is true for several variants at once and means something else."""
    rows = []
    for g in groups or []:
        for v in g.get("variants") or []:
            p = v.get("product") or {}
            if not p.get("id"):
                continue
            rows.append(_prune({
                "group": clean(g.get("name")),
                "label": clean(v.get("label")),
                "id": p["id"],
                "available": p.get("isAvailable"),
                "is_this": p["id"] == this_id,
            }))
    return rows


# ---- listing items ------------------------------------------------------------------------------


def snippet(p: dict) -> dict[str, Any]:
    """One product of a search / batch / markdown listing."""
    pid = str(p.get("id"))
    titles, promos = labels(p.get("labels"))
    archived = not p.get("slug") and not p.get("category")
    item: dict[str, Any] = {
        "id": pid,
        "name": clean(p.get("shortName")) or clean(p.get("name")),
        "brand": clean((p.get("brand") or {}).get("name")),
        "mpn": clean(p.get("searchDescription")),
        "category": clean((p.get("category") or {}).get("name")),
        "url": product_url(p.get("slug"), pid),
        "available": bool(p.get("isAvailable")),
        **price_block(p.get("price"), p.get("multiplicity")),
        "rating": _prune({
            "this_product": own_rating(p.get("own")),
            "group_shown_on_site": {k: v for k, v in shown_rating(p).items() if k != "note"},
        }) or None,
        "units_in_city_stores": count((p.get("stock") or {}).get("countInStores")),
        "pickup_earliest_date": pickup_earliest(((p.get("delivery") or {}).get("self") or {}).get("availabilityByDays")),
        "prepayment_required": True if p.get("isPaymentMandatory") else None,
        "labels": titles or None,
        "promo_codes": promos or None,
    }
    if p.get("isDiscount") or pid.startswith("M"):
        item["is_markdown"] = True
        item["original_product_id"] = p.get("originalProductId")
    if archived:
        item["archived"] = True
        item["archived_note"] = "archived card: Citilink no longer sells this product (no page, no price)"
    # In a list the standard labels live once in the answer's `legend`; only the
    # unusual ones (unverified club display types) stay on the item.
    item.pop("price_label", None)
    if (p.get("price") or {}).get("clubPriceViewType") == "PRICE_IN_RUB":
        item.pop("club_price_label", None)
    if isinstance(item.get("bonuses"), dict):
        item["bonuses"] = {k: v for k, v in item["bonuses"].items() if k != "label"}
    return _prune(item)


LEGEND = {
    "price_rub": PRICE_LABEL + "; null (with price_note) = not sold there now; per unit when `lot` is present",
    "old_price_rub": "strikethrough price the site shows; absent when there is none",
    "club_price_rub": CLUB_LABEL + "; present only when the site shows it",
    "bonuses": BONUS_LABEL,
    "lot": "sold only in lots: lot.price_rub is the minimum you pay and the big number on the site",
    "promo_codes": PROMO_NOTE,
    "rating": ("this_product = the product's own reviews; group_shown_on_site = " + GROUP_SCOPE_NOTE
               + " — compare products by this_product"),
    "units_in_city_stores": ("units on the shelves of Citilink stores in this city's delivery area now "
                             "(\">50\" is the site's own wording); 0 does not rule out pickup soon — see "
                             "pickup_earliest_date"),
    "pickup_earliest_date": "earliest calendar day the item can be collected at any pickup point of the area",
    "is_markdown": "used or damaged unit (id M…); original_product_id is the new item",
}


# ---- reviews and questions -------------------------------------------------------------------------

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+7|8)[\s(\-]*\d{3}[\s)\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)")


def scrub(text: Any) -> str | None:
    """Review/question text without e-mail addresses and phone numbers people sometimes leave."""
    out = clean(text)
    if out is None:
        return None
    out = _EMAIL.sub("[e-mail removed]", out)
    return _PHONE.sub("[phone removed]", out)


SOURCES = {
    "CITILINK": "bought at Citilink («Куплено в Ситилинк»)",
    "YAMARKET": "imported from Yandex Market («Отзыв из открытых источников»), not a verified Citilink purchase",
    "SAMSUNG": "imported from Samsung, not a verified Citilink purchase",
}


def review(item: dict, this_id: str) -> dict[str, Any]:
    src = (item.get("source") or {}).get("sourceType")
    counters = ((item.get("voteInfo") or {}).get("info") or {}).get("counters") or {}
    product = item.get("product") or {}
    other = product.get("id") and str(product.get("id")) != this_id
    return _prune({
        "date": calendar_date(item.get("displayedDate")),
        "rating": item.get("rating"),
        "pros": scrub(item.get("pros")),
        "cons": scrub(item.get("cons")),
        "text": scrub(item.get("text")),
        "product_id": str(product["id"]) if product.get("id") else None,
        "other_variant": clean(product.get("shortName")) if other else None,
        "source": src,
        "source_label": SOURCES.get(src) or clean((item.get("source") or {}).get("description")),
        "likes": counters.get("likes") or None,
        "dislikes": counters.get("dislikes") or None,
        "is_best": True if item.get("isBest") else None,
    })


def _author_marks(item: dict) -> dict[str, Any]:
    author = item.get("author") or {}
    b2c = author.get("b2c") or {}
    vendor = author.get("vendor") or {}
    brand = clean(((vendor.get("brand") or {}).get("name"))) if vendor else None
    return {
        "by_citilink_expert": True if (b2c.get("expert") or {}).get("isExpert") else None,
        "by_brand": brand,
    }


def threads(items: Iterable[dict]) -> list[dict]:
    """Flat UGC_Message list (depth 0 = question, deeper = answers) -> question threads."""
    items = list(items or [])
    by_id = {m.get("id"): m for m in items}
    roots: dict[Any, dict] = {}
    order: list[Any] = []

    def root_of(m: dict) -> Any:
        seen = set()
        while m.get("depth", 0) > 0 and (m.get("parent") or {}).get("id") in by_id:
            if m.get("id") in seen:
                break
            seen.add(m.get("id"))
            m = by_id[(m.get("parent") or {}).get("id")]
        return m.get("id") if m.get("depth", 0) == 0 else None

    for m in items:
        if m.get("depth", 0) == 0:
            roots[m.get("id")] = {"question": _message(m), "answers": []}
            order.append(m.get("id"))
    orphans = []
    for m in items:
        if m.get("depth", 0) == 0:
            continue
        answer = _message(m)
        parent = (m.get("parent") or {}).get("id")
        if parent in by_id and by_id[parent].get("depth", 0) > 0:
            answer["reply_to_answer"] = True
        root = root_of(m)
        if root in roots:
            roots[root]["answers"].append(answer)
        else:
            orphans.append(answer)
    out = [roots[r] for r in order]
    for t in out:
        t["answers"].sort(key=lambda a: a.get("date") or "")
    if orphans:
        out.append({"question": None, "answers": orphans,
                    "note": "answers whose question is on another page"})
    return out


def _message(m: dict) -> dict[str, Any]:
    counters = ((m.get("voteInfo") or {}).get("info") or {}).get("counters") or {}
    return _prune({
        "date": calendar_date(m.get("creationDate")),
        "text": scrub(m.get("text")),
        "by_buyer": True if m.get("isBuyer") else None,
        **_author_marks(m),
        "is_best": True if m.get("isBest") else None,
        "likes": counters.get("likes") or None,
        "dislikes": counters.get("dislikes") or None,
    })


# ---- pruning ---------------------------------------------------------------------------------------

KEEP_NULL = {"price_rub"}   # "no price" is an answer, stated as null next to price_note


def _prune(d: dict) -> dict:
    """Drop None and empty containers; zeros and False stay (they are data)."""
    return {k: v for k, v in d.items() if (v is not None or k in KEEP_NULL) and v != [] and v != {}}


prune = _prune
