# PokeDetect

Scan a Pokémon card with your camera, identify it with a deep-learning vision
pipeline, estimate its condition, and see an approximate Cardmarket price — then
save it to your own collection.

> **On prices:** the value shown is an approximate market figure derived from
> Cardmarket data (via the [Pokémon TCG API](https://pokemontcg.io)). It is **not
> an exact quote.** Actual value depends on condition, edition, printing and
> demand, but the estimate should be roughly in the right range.

---

## Features

- **Camera scanning** — capture a card in the browser (or upload a photo).
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

## Tech stack

| Layer      | Technology                                              |
| ---------- | ------------------------------------------------------- |
| Frontend   | React + TypeScript, Vite, React Router (plain CSS)      |
| Backend    | Python, FastAPI, SQLAlchemy, SQLite                     |
| Auth       | JWT (PyJWT) + bcrypt password hashing                   |
| Vision     | OpenCV (detection/warp), EasyOCR (text), imagehash + rapidfuzz (matching), NumPy heuristics |
| Card data  | [Pokémon TCG API](https://pokemontcg.io) (incl. Cardmarket prices) |

---

## Getting started

### 1. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # optional — edit SECRET_KEY, add a POKEMONTCG_API_KEY
uvicorn app.main:app --reload --port 8000
```

The API is now on `http://localhost:8000` (interactive docs at `/docs`).

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
      pokemontcg.py    Pokémon TCG API client + price extraction
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

1. **Detect** — find the largest card-shaped quadrilateral in the frame and warp
   it flat (`detect_card`).
2. **Read** — OCR the card's name-banner and collector-number regions
   specifically (small, clean crops — falls back to whole-card OCR if a region
   read fails) to extract a likely card name and number (`recognize_text`).
3. **Search** — query the Pokémon TCG API with a broadened, typo-tolerant
   query (a short name fragment, no hard number filter) to pull a pool of
   candidates, each with its Cardmarket price (`services/pokemontcg.py`).
4. **Rank** — score every candidate by fuzzy name similarity (tolerant of OCR
   misreads) plus, for the top few, perceptual-hash visual similarity between
   the scanned photo and the candidate's reference image. The blended score is
   shown to you as a per-match confidence, best match first
   (`services/imagematch.py`).
5. **Grade** — assess condition from image cues and map it to a Cardmarket grade
   (`assess_condition`).

If OCR can't read the card clearly, use **Search by name** to look it up manually
(still fuzzy-matched) and set the condition yourself.

## Notes & limitations

- Condition grading is a heuristic estimate from a single photo, not a
  professional grade — treat it as guidance.
- Recognition accuracy depends on lighting, framing and focus. A plain dark
  background and even lighting help a lot.
- Prices track Cardmarket trend data and are approximate; see the disclaimer
  above.
