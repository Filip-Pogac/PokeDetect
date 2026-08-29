# Migrating from pokemontcg.io to TCGdex (free, no API key)

pokemontcg.io no longer offers a usable free plan. TCGdex (`https://api.tcgdex.net/v2/en`)
is free, needs **no API key or signup**, and carries Cardmarket EUR pricing.

Verified against live responses on 2026-08-29 (fetched by the user; this sandbox's
egress proxy blocks the domain).

## Endpoint shapes

### Search — `GET /v2/en/cards?name=charizard`
Returns a **lean** list. No pricing, no set, no rarity:
```json
[{"id":"base1-4","localId":"4","name":"Charizard",
  "image":"https://assets.tcgdex.net/en/base/base1/4"}]
```
- Name matching is substring-ish and already fuzzy-friendly: "charizard" also
  returned "Blaine's Charizard", "Dark Charizard", "Charizard ex", "M Charizard EX".
- Returned ~130 rows unpaginated — needs a `pagination:page`/`itemsPerPage`
  param or client-side truncation.

### Card detail — `GET /v2/en/cards/base1-4`
Carries everything the app needs:
```json
{"id":"base1-4","name":"Charizard","localId":"4","rarity":"Rare",
 "image":"https://assets.tcgdex.net/en/base/base1/4",
 "set":{"id":"base1","name":"Base Set","cardCount":{"official":102,"total":102}},
 "pricing":{
   "cardmarket":{"unit":"EUR","trend":744.68,"avg":487.19,"low":75,
                 "avg1":1552.5,"avg7":594.65,"avg30":445.32,
                 "trend-holo":123.63,"avg7-holo":129.55,"...":"..."},
   "tcgplayer":{"unit":"USD",
                "holofoil":{"marketPrice":868.56,"midPrice":980.22,"lowPrice":500}}}}
```

## Field mapping (replaces `_simplify` / `_extract_cardmarket_price`)

| App field       | pokemontcg.io      | TCGdex                                   |
|-----------------|--------------------|------------------------------------------|
| `tcg_id`        | `id`               | `id`                                     |
| `name`          | `name`             | `name`                                   |
| `set_name`      | `set.name`         | `set.name`                               |
| `number`        | `number`           | **`localId`**                            |
| `rarity`        | `rarity`           | `rarity`                                 |
| `image_url`     | `images.small`     | **`image` + `/low.webp`** (see below)    |
| price (EUR)     | `cardmarket.prices.trendPrice` | `pricing.cardmarket.trend` |
| price fallbacks | `averageSellPrice`, `avg7`, `avg30` | `avg`, `avg7`, `avg30`  |
| price (USD alt) | `tcgplayer.prices.*.market`  | `pricing.tcgplayer.<variant>.marketPrice` / `midPrice` |

## Gotchas

1. **Image URLs have no file extension.** `image` is a base path; you MUST append
   a quality + format, e.g. `${image}/low.webp` or `${image}/high.png`. Using the
   bare URL renders a broken image. Some cards omit `image` entirely.
2. **Search returns no prices** — an N+1 detail fetch is required. Mitigate the
   same way `imagematch.py` already handles candidate images: fetch details for
   only the top-N text-ranked candidates via `asyncio.gather`, and memoize by
   `tcg_id` in a bounded `OrderedDict` LRU (mirror `_HASH_CACHE`).
3. **`-holo` price variants exist** alongside base keys; prefer the base
   (`trend`/`avg`) and treat `-holo` as a later refinement.
4. `variants_detailed[]` carries per-printing pricing (1st edition, shadowless…)
   with wildly different values — the top-level `pricing` is the sane default.
5. No API key means no `X-Api-Key` header; `POKEMONTCG_API_KEY` config becomes
   dead and should be removed from `config.py`, `.env.example` and the README.

## Files to change
- `backend/app/services/pokemontcg.py` — rewrite (or rename to `carddb.py`)
- `backend/app/config.py` — drop `pokemontcg_api_key`, repoint base URL
- `backend/.env.example`, `README.md` — drop the API-key setup step
