# Changelog

All notable changes to this project are documented here. Versions follow
[Semantic Versioning](https://semver.org): patch releases fix breakage caused
by changes on Citilink's side, minor releases add features.

## [0.1.0] — 2026-09-25

### Added
- `search_products`: 48-item pages, six sort orders by slug, price window,
  category, availability filter (`in_store_now` / `pickup` / `delivery`);
  `total_found`, server-computed `has_more`, price range of all matches,
  category facet. Each item carries the product's own rating next to the
  group rating the site shows.
- `get_product`: labelled regular / strikethrough / club price (only when the
  site shows it), bonuses, lot price, payment terms, product vs group rating
  with star split, Citilink's review digest, part number, model, warranty,
  country, variants, courier, pickup dates, Citilink stores and pickup points,
  partner-point summary, markdown condition, badges, promo codes, promotions,
  characteristics.
- `get_reviews` (product or group scope, star filter, worst-first),
  `get_questions` (threads, expert and brand answers marked, no author names),
  `find_markdown` (by category or product), `compare_products` (up to 20, with
  `not_found`), `resolve_city` (Citilink's directory, cached for a week).
- City set on every request and checked against Citilink's echo; sanity checks
  that refuse answers which ignored the sort, price window, availability or
  category, or came back truncated; Citilink's `_pcl` JS challenge solved in
  Python; clear errors for throttling, schema changes and a future demand for
  the `x-ad-mark` signature.
