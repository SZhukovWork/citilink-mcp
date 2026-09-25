# citilink-mcp

**English** · [Русский](README.ru.md)

An [MCP](https://modelcontextprotocol.io) server that gives LLM agents live,
honestly-labelled data from [Citilink](https://www.citilink.ru), a large Russian
electronics and appliance retailer: search with real pages, sorting, price
windows and availability filters; full product cards for a chosen city; the
product's own rating next to its variant group's; Citilink's stores and pickup
points; reviews; questions and answers; markdown (used / damaged) units.

Plain HTTP to the site's own GraphQL endpoint — no browser, no account.

## Why this server

Citilink's data is easy to fetch and easy to misread. This server is built
around not passing on numbers that are not what a buyer in your city sees:

| Citilink pitfall | What this server does |
|---|---|
| The city lives in a "sticky" cookie; a stale or bogus value silently gives Moscow prices | Sets the city on every request and checks the city Citilink says it answered for; an answer for another city is retried once, then refused |
| Price, club price, strikethrough price, assortment, stock and delivery differ by city | Every answer carries `city` (Citilink's own echo and where the choice came from) and `fetched_at` |
| The API holds a club price even when the site hides it (`clubPriceViewType: SKIP`) | The club price is returned only when the site shows it, and always labelled as conditional (signed-in Club members) |
| An empty price string (`""`) — the site's own code turns it into "0" | `price_rub: null` with a reason; never 0 |
| Bonus points ("До 537 бонусов") look like a discount | Returned separately as points for future purchases; never subtracted |
| Items sold only in boxes: the site's big number is `price × 10` | `price_rub` per unit plus `lot` with the lot price, strikethrough and club price exactly as the page shows them |
| Search tiles and the card header show the rating of the whole variant group (1, 2 and 4 TB together) | `rating.this_product` (the product's own reviews) next to `group` / `group_shown_on_site`, with `scope` |
| "Pickup from 1560 stores" — 1548 of them are partner outlets across several regions, some with a paid fee | Citilink's own stores and pickup points listed with dates; partner points only as a labelled summary with the paid count and fee range |
| `perPage` above 48 is silently cut to 48 and `totalPages` then lies | Pages are always 48; `has_more` is computed by the server |
| A numeric sort id is silently ignored (relevance stays) | Sorts are sent by slug; the server checks the sort, price window, availability and category Citilink says it applied and refuses an answer that ignored them |
| An unknown id silently disappears from a batch; an over-long one wipes out the whole batch | Ids are validated first; unknown ones are listed in `not_found` |
| In group mode every review is flagged `fromGroup`, even this product's own | Reviews say which other variant they are about (`other_variant`), from the review's product id |
| A star filter recomputes the review summary (a 4.9 product shows 3.8) | Headline ratings in `get_reviews` are always the unfiltered ones |
| Reviews imported from Yandex Market sit among Citilink's own | Each review has `source`; imported ones are labelled as not verified purchases |
| Dates come as midnight UTC | Returned as calendar dates (`YYYY-MM-DD`), no time-zone shift |
| Citilink's own anti-bot challenge (HTTP 429 + JS page) now shows up on the API too | Solved in Python once per call (no 512 MB allocation), the cookie is kept for its hour; a repeated challenge is an error, not a loop |
| GraphQL answers can carry data and errors at once | Data is kept, failed fields are listed in `partial_errors` |

## Tools

| Tool | What it returns |
|---|---|
| `search_products(query, page, sort, price_min, price_max, category_id, availability, limit, city)` | 48 items per page; sort `relevance` / `price_asc` / `price_desc` / `discount` / `rating` / `opinions`; availability `any` / `in_store_now` / `pickup` / `delivery`; `total_found`, `has_more`, `price_range_rub` (the site's price-filter span), `categories` (id, slug, count). Per item: price, strikethrough, club price when shown, bonuses, lot, own rating and group rating, units in the area's stores, earliest pickup date, badges, promo codes, markdown flag, URL |
| `get_product(product, include_specs, include_stores, city)` | Everything above plus payment terms (prepayment, loan, instalment), star split for the product and its group, Citilink's review digest, part number (`mpn`), model, warranty, country, variants with ids, courier fee and date, pickup dates, Citilink stores and pickup points, partner-point summary, markdown condition, promotions, all characteristics |
| `get_reviews(product, page, limit, sort, scope, stars)` | Date, stars, pros, cons, text, variant, source, votes; `scope=product` (default) or `group`; `sort=worst` puts complaints first; star filter |
| `get_questions(product, page, limit, sort)` | Questions with their answers as threads; answers by Citilink experts and brand representatives are marked; no author names |
| `find_markdown(category_slug \| product, page, sort, city)` | Markdown / used units of a category or of one product: price, reason, completeness, wear, damage, warranty, the new product's id, pickup date |
| `compare_products(products, city)` | Up to 20 products side by side in one request, with `not_found` and `invalid` |
| `resolve_city(name)` | City ids from Citilink's directory (8,465 cities), e.g. `Березовский` → two matches in two regions |

`product` accepts an id (`1896626`), a markdown id (`M00295786`) or a product URL.
`city` accepts an id (`ekat_cl`, `ekat_cl:5x0029236`) or a name (`Екатеринбург`, `Кировск, Мурманская`).

## Requirements

Measured on Linux (CachyOS, Python 3.12, a home connection in Russia):

| | |
|---|---|
| Python | ≥ 3.10, with [uv](https://docs.astral.sh/uv/) (or pip). Dependencies: `mcp`, `requests` |
| Browser | **Not needed.** No Playwright, no Chromium |
| Docker | Not needed |
| Display | Not needed |
| Memory | ≈ 73 MB resident when idle, ≈ 84 MB peak after a 48-item search and a full card |
| Cold start | 0.5 s from launch to the tool list with `uvx` once the package is built; 1.4 s on the very first run (build). Installing from GitHub adds uv's `git fetch` on every start |
| Disk | ≈ 40 MB for the uv environment; ≈ 1.2 MB cache (`~/.cache/citilink-mcp`: city directory + session cookies) |
| Request time | 0.1–1.1 s per request to Citilink (a 48-item search 0.7–1.1 s, once 2.9 s; a full card 0.2–0.7 s; reviews and questions 0.1–0.3 s), plus the 2.5 s spacing between requests |
| Network | A Russian IP. From a home IP everything works; a third-party report says datacenter IPs get HTTP 429 for the whole domain (not verified here). Use `CITILINK_PROXY` if needed |

## Install

Claude Code:

```bash
claude mcp add citilink -- uvx --from git+https://github.com/SZhukovWork/citilink-mcp citilink-mcp
```

Any MCP client (`claude_desktop_config.json`, `.mcp.json`, …):

```json
{
  "mcpServers": {
    "citilink": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/SZhukovWork/citilink-mcp", "citilink-mcp"]
    }
  }
}
```

From a checkout: `uv venv && uv pip install -e . && .venv/bin/citilink-mcp`
(or `uvx --from /path/to/citilink-mcp citilink-mcp`).

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `CITILINK_CITY` | `ekat_cl` (Екатеринбург) | Default city: an id or a name (`Екатеринбург`, `Москва`, `Березовский, Свердловская`). A name costs one request for Citilink's city directory, which is then cached for a week; an ambiguous name is an error listing the candidates. A `city` argument wins over it |
| `CITILINK_MIN_INTERVAL` | `2.5` | Seconds between requests to Citilink (plus up to 0.5 s jitter). Calls from parallel agents queue up |
| `CITILINK_PROXY` | — | Proxy URL, e.g. `http://user:pass@host:3128` |
| `CITILINK_CACHE_DIR` | `~/.cache/citilink-mcp` (honours `XDG_CACHE_HOME`) | City directory (refreshed weekly) and session cookies (file mode 0600, directory 0700) |

## What the numbers mean

- **`price_rub`** — the regular price anyone sees on the site without signing
  in, in the city from `city`. `null` with `price_note`: Citilink does not sell
  the item there now.
- **`old_price_rub`**, **`discount_percent`** — the strikethrough price and the
  discount the site shows; absent when there is none.
- **`club_price_rub`** — the Citilink Club price, applied at checkout to orders
  placed signed in with a Club card (the card comes with a free registration).
  Conditional, so never a replacement for `price_rub`. Returned only when the
  site shows it (display type `PRICE_IN_RUB`, checked against the rendered
  page); the `DIFF_IN_RUB` / `DIFF_IN_PERCENT` / `WANT_TO_KNOW` display types
  follow the site's render code and are marked "not seen live".
- **`bonuses`** — Club points for future purchases: `base` (entry status),
  `extra_for_sbp_payment` (paying via SBP), `site_shows_up_to` (the «До N
  бонусов» figure). Not a discount on this item.
- **`lot`** — the item is sold only in lots. The site's big number is the lot
  price (checked on the page: «3050 ₽ · 305 ₽×10 шт. в коробке · Клубная цена
  2430 ₽ · До 50 бонусов»); `price_rub` stays per unit.
- **`promo_codes`** — badges such as «Промокод ИГРАЙ −2000₽»: typed in at
  checkout, not verified and not subtracted. `payment.credit` /
  `installment` — loan terms, not prices. `prepayment_required` — the site
  demands payment in advance («Предоплата»).
- **Ratings** — `rating.this_product` is the product's own reviews (the site's
  «Этот вариант товара» tab); `group` / `group_shown_on_site` / `shown_on_card`
  cover all variants of the group (the site's default «Все отзывы» and the
  number on tiles). A product without variants is its own group. `null` rating
  = no reviews yet. `site_review_digest` is Citilink's machine-written summary.
- **Availability** — `units_in_city_stores`: units on the shelves of Citilink
  stores in the city's delivery area right now (`">50"` is the site's own
  wording). 0 does not rule out pickup today: items also come from the
  warehouse. `pickup` / `pickup_earliest_date`: dates the item can be
  collected. `own_stores`: Citilink's own stores and pickup points with dates
  and `units_in_store_now`. `partner_points`: other companies' outlets across
  Citilink's delivery area (several regions), `paid_count` of them charge a fee.
  `courier`: "from" price and date for the city; the final fee depends on the
  address.
- **Markdown** — ids starting with `M`, names ending with «(Б/У)»: used or
  damaged units. `condition` / `markdown` quote Citilink: reason, completeness,
  wear, damage, warranty (often 1 month). `original_product_id` is the new item.

## Limitations

- **Unofficial.** It uses the storefront's internal GraphQL API, which Citilink
  can change at any time. Queries are minimal and every field was checked live;
  a changed schema produces a clear `SchemaChanged` error naming the field.
- **Anonymous prices only.** Personal prices, closed promotions and your own
  bonus rate need a signed-in account; the server never signs in. Whether the
  club price is exactly what checkout charges was not verified.
- **No price history, no sellers, no barcodes.** Citilink has no public price
  history, sells its own stock (no marketplace sellers) and does not publish
  EAN codes in its characteristics; `mpn` (part number) and `model` are there.
- **Promo codes** are shown as badges only; their terms are Citilink's.
- **Unverified display types.** `DIFF_*` / `WANT_TO_KNOW` club-price display
  types were not seen live; their labels follow the site's code.
- **Search facets are Citilink's.** `availability=in_store_now` is the site's
  «Забрать через 5 минут» filter and can include items with 0 units on
  shelves; `total_found` can exceed the category facet count (it seems to count
  unavailable items too).
- **Anti-bot.** Citilink's JS challenge is solved in Python; if its layout
  changes the server says so instead of guessing. Datacenter and VPN IPs may be
  refused (HTTP 403/429).
- **Throttling.** Requests are serialised and spaced; on a rate-limit error the
  server pauses once and then reports it — wait minutes, do not loop.

### If Citilink starts requiring `x-ad-mark`

The site's own GraphQL client (a Rust/WASM module) adds an `x-ad-mark` header
to every request. Citilink does not check it today. If it starts to, the
server answers «Ситилинк начал требовать подпись запроса (x-ad-mark); нужен
запасной путь из README» with the HTTP status. Planned fallbacks:

1. Run the site's `graphql_bg.wasm` in Node with small shims — its imports are
   only `fetch`, `getRandomValues`, `location.origin`, `Headers` and
   `JSON.stringify`, so no browser is needed.
2. As in [ozon-mcp](https://github.com/SZhukovWork/ozon-mcp): a long-lived headless page that calls the site's own client
   (≈ 1.3–1.8 GB RAM for Chromium, ≈ 1 s per navigation).

## Roadmap

- Optional account mode (your personal prices, your club price and bonus rate)
  and the cart — later.
- Checkout, payment and address changes are deliberately out of scope.

## Development

```bash
uv venv && uv pip install -e '.[dev]'
.venv/bin/pytest            # offline tests on recorded answers
.venv/bin/pytest -m live    # end-to-end over MCP stdio against live citilink.ru (11–12 requests, ~35 s)
```

Parsers are pure functions in `parse.py`; the request policy is `analyse()` in
`client.py`; GraphQL documents are in `queries.py`. Fixtures in
`tests/fixtures/` are real answers recorded once: a test checks that the JSON
ones hold no author names, cookies or page HTML, and the recorded anti-bot
challenge page has its cookie chunks replaced with dummies.

## Disclaimer

Not affiliated with Citilink. An unofficial client of the site's internal API
for personal price research: keep request rates human (the default spacing is
2.5 s) and respect Citilink's [terms of use](https://www.citilink.ru/doc/agreement/).

License: MIT.
