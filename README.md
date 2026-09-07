# PokeDetect

Scan a Pokémon card with your camera, identify it with a deep-learning vision
pipeline, estimate its condition, and see an approximate Cardmarket price — then
save it to your own collection.

> **On prices:** the value shown is an approximate market figure derived from
> Cardmarket data (via [TCGdex](https://tcgdex.dev)). It is **not
> an exact quote.** Actual value depends on condition, edition, printing and
> demand, but the estimate should be roughly in the right range.

---

## Features

- **Camera scanning with auto-capture** — hold the phone over a card and the
  shot is taken by itself once the frame is steady, in focus, and actually has
  a card in it (analyzed in-browser, no round-trips). A progress ring shows the
  hold building so it never fires unexpectedly; auto-capture can be switched
  off, and the manual shutter and photo upload always work.
- **Manual edge correction** — if automatic card detection misses, drag the four
  corners onto the card and re-scan. Confirmed corners restore the flattening
  step that OCR and visual matching depend on.
- **Deep-learning recognition** — the card is located and perspective-corrected
  with OpenCV, its name/number regions are read with EasyOCR (a CRNN-based
  recognizer), and candidates from the Pokémon TCG card database are ranked by
  a blend of fuzzy text matching (typo-tolerant, so OCR misreads still surface
  the right card) and perceptual-hash visual similarity against the scanned
  photo — each match shown with a confidence score.
- **Condition / damage detection** — image heuristics (focus/sharpness, corner
  and edge wear, glare) estimate a Cardmarket-style grade
  (Mint → Near Mint → Excellent → Good → Light Played → Played → Poor) and flag
  cards that look potentially damaged.
- **Cardmarket price estimate** — the market trend price, adjusted by the
  detected condition, with a clear "approximate" disclaimer.
- **Accounts & collections** — register / sign in (JWT), then save scanned cards
  to a personal collection with name, set, number, price and condition. Edit the
  condition or remove cards anytime, and see your collection's estimated total.
- **Collection management** — saving a card you already own bumps its copy count
  instead of creating a duplicate entry (the same card in a different condition
  stays separate, since it's worth a different amount). Search by name/set/number,
  filter by set, sort by value, name, condition or date added, and export the
  current view to CSV. Totals follow the active filter, so filtering to one set
  values just that set.

## Tech stack

| Layer      | Technology                                              |
| ---------- | ------------------------------------------------------- |
| Frontend   | React + TypeScript, Vite, React Router (plain CSS)      |
| Backend    | Python, FastAPI, SQLAlchemy, SQLite                     |
| Auth       | JWT (PyJWT) + bcrypt password hashing                   |
| Vision     | OpenCV (detection/warp), EasyOCR (text), imagehash + rapidfuzz (matching), NumPy heuristics |
| Card data  | [TCGdex](https://tcgdex.dev) — free, no API key (incl. Cardmarket prices) |

---

## Getting started

### 1. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then set SECRET_KEY to a random string
uvicorn app.main:app --reload --port 8000
```

The API is now on `http://localhost:8000` (interactive docs at `/docs`).

`backend/.env` is loaded automatically at startup (see `app/config.py`), and is
found relative to the source tree, so it applies wherever you launch from. Real
environment variables take precedence over the file, so a container or CI job
can override any setting without editing it. Every key is optional except
`SECRET_KEY`, which signs login tokens — leave it at the default and anyone
reading this repo can forge a session. `backend/.env.example` lists the rest.

> **First scan note:** on the first real scan, EasyOCR downloads its model
> weights (~100 MB) once. If OpenCV or EasyOCR isn't installed, the app degrades
> gracefully — you can still search cards by name and set the condition manually.
> To use the lighter Tesseract engine instead, set `OCR_ENGINE=tesseract` (and
> install the Tesseract binary), or `OCR_ENGINE=none` to disable OCR.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The Vite dev server proxies `/api` to the backend
on port 8000, so no extra configuration is needed.

---

## Project structure

```
backend/
  app/
    main.py            FastAPI app + CORS + startup
    config.py          Env-driven settings
    database.py        SQLAlchemy engine / session
    models.py          User, CollectionCard
    schemas.py         Pydantic request/response models
    security.py        bcrypt + JWT helpers
    deps.py            Auth dependency (current user)
    routers/
      auth.py          register / login / me
      collection.py    CRUD for saved cards
      scan.py          scan image -> matches + condition; manual search
    services/
      carddb.py        TCGdex client: search, price extraction, name index
      vision.py        card detection, region-aware OCR, condition heuristics, perceptual hash
      imagematch.py    fuzzy text + visual-hash candidate ranking
frontend/
  src/
    api/client.ts      Typed REST client
    context/           Auth context
    components/         Navbar, CameraScanner, ScanResultModal, ConditionBadge
    pages/             Login, Scan, Collection
    styles/            Design system (white / dark-gray / blue / yellow)
```

## How recognition works

0. **Capture** — the browser samples the live video ~8x/second and fires the
   shutter once motion, focus and edge-density gates all pass for ~600ms
   (`src/lib/frameAnalysis.ts`). The focus measure is normalized by scene
   contrast so it behaves the same in bright and dim light.
1. **Detect** — find the largest card-shaped quadrilateral in the frame and warp
   it flat (`detect_card`), or use corners the user placed by hand
   (`warp_with_corners`).
2. **Read** — OCR the card's name band and collector-number region
   specifically (small, clean crops — falls back to whole-card OCR if a region
   read fails). The name band spans the whole top of the card, because layouts
   differ by era, and *every* line in it comes back as a name candidate
   (`recognize_text`).
3. **Resolve the name** — score those candidates against a cached index of every
   real card name in the database and keep the best (`carddb.resolve_name`).
   This both fixes OCR typos ("Lucarlo" → "Lucario") and discards lines that
   aren't the name at all — the "Evolves from Dragonair" line printed above the
   name on older cards, say.
4. **Search** — query TCGdex with a broadened, typo-tolerant query (a short name
   fragment, no hard number filter) to pull a pool of candidates
   (`services/carddb.py`). Set, rarity and Cardmarket price are then fetched
   per-card for just the handful that get shown.
5. **Rank** — score every candidate by fuzzy name similarity (tolerant of OCR
   misreads) plus, for the top few, perceptual-hash visual similarity between
   the scanned photo and the candidate's reference image. A matching collector
   number breaks ties between printings. The blended score is shown to you as a
   per-match confidence, best match first (`services/imagematch.py`).
6. **Grade** — assess condition from image cues and map it to a Cardmarket grade
   (`assess_condition`).

If nothing matches confidently, the scan doesn't dead-end: it comes back with
every card carrying the name that was read (and the closest real names to it),
so you can just pick yours from the list. Failing that, **Search by name** looks
a card up manually (still fuzzy-matched) and you set the condition yourself.

### Tuning auto-capture

The gate thresholds live as named constants at the top of
`frontend/src/lib/frameAnalysis.ts`. They were validated against synthetic
frames but want tuning on real devices — if capture fires too eagerly or never
fires, adjust `MOTION_MAX`, `SHARPNESS_MIN`, `EDGE_DENSITY_MIN` or
`HOLD_SAMPLES` there. `npm run test:frames` re-checks the metrics against
synthetic sharp/blurred/dim/blank frames.

## Notes & limitations

- Condition grading is a heuristic estimate from a single photo, not a
  professional grade — treat it as guidance.
- Recognition accuracy depends on lighting, framing and focus. A plain dark
  background and even lighting help a lot.
- Prices track Cardmarket trend data and are approximate; see the disclaimer
  above.