# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Always make a sound notification when answer for given prompt is finished.
Never put claude as author or coauthor on github commits.

## What this is

PokeDetect: scan a Pokémon card with a phone camera, identify it via a
computer-vision + OCR pipeline, estimate condition, and show an approximate
Cardmarket price. Users can save scans to a personal collection. See
[README.md](README.md) for the full feature list, setup instructions, and a
step-by-step description of the recognition pipeline — read it before working
on `services/vision.py`, `services/imagematch.py`, or `services/carddb.py`.

## Commands

### Backend (`backend/`)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # set SECRET_KEY
uvicorn app.main:app --reload --port 8000
```

There is no backend test suite. Interactive API docs are served at `/docs`.

### Frontend (`frontend/`)

```bash
npm install
npm run dev          # Vite dev server on :5173, proxies /api to :8000
npm run build         # tsc --noEmit then vite build — this is the type check
npm run test:frames   # re-checks auto-capture gate thresholds against synthetic frames
```

`npm run build` is the only type-checking step (no separate `tsc` or lint
script) — run it after TypeScript changes. `test:frames` is the only test
suite in the repo; it bundles `src/lib/frameAnalysis.ts` with esbuild and runs
`scripts/test-frame-analysis.mjs` against it.

### Database

Local dev uses SQLite (`backend/pokedetect.db`) automatically, no setup
needed. Deployed instances (Vercel, ephemeral filesystem) require Neon
Postgres via `DATABASE_URL` — see the README's Database section for the
`neon link` workflow and the `.env.local` / `backend/.env` load order in
`app/config.py`. **A memory in this project's auto-memory notes that
`TestClient`/API scripts hit the real dev DB — isolate `DATABASE_URL` before
running them.**

## Architecture

Two independently deployed services under one Vercel project (`vercel.json`
routes `/api/*` to the backend, everything else to the frontend):

- **`backend/`** — FastAPI + SQLAlchemy, deployed as a container
  (`Dockerfile.vercel`, not a Python serverless function — the OCR/PyTorch
  stack exceeds the 500 MB function bundle cap).
- **`frontend/`** — React + TypeScript + Vite, plain CSS (no component
  library).

### Backend request flow

`app/main.py` wires routers (`auth`, `collection`, `scan`) and runs DB
init + OCR model warm-up as a **detached background task** in `lifespan`,
not awaited — a deployed container must answer its readiness check within
seconds, and both jobs can take longer. `/api/health` reports live status of
both (`_db_status`, `_ocr_status` globals), which is the only visibility into
a failed container on the platform. Auth is a bearer-JWT dependency
(`deps.get_current_user`) decoding tokens issued in `security.py`.

### Recognition pipeline (`services/`)

This is the part that spans the most files and is easy to misjudge from any
one of them — read README.md's "How recognition works" section first. In
short: `vision.py` detects/warps the card and OCRs name + number regions →
`cardname.py`/`carddb.py` resolves the OCR'd name against a cached index of
real card names (typo correction) → `carddb.py` queries TCGdex for candidates
→ `imagematch.py` ranks candidates by blended fuzzy-text + perceptual-hash
visual similarity → `vision.py` separately grades condition from image
heuristics. Almost every stage is behind a `config.py` kill-switch
(`ENABLE_VISUAL_RERANK`, `ENABLE_ATTACK_OCR`, `ENABLE_TYPE_DETECTION`,
`ENABLE_FIRST_EDITION_OCR`, `ENABLE_FINISH_DETECTION`) and a tunable
threshold — check there before assuming a signal is unconditionally applied.
`timing.py` provides the per-stage instrumentation surfaced via
`DEBUG_TIMINGS`.

Two in-process caches matter for behavior during development: the card-name
index (`NAME_INDEX_TTL_SECONDS`, 1 day) and per-card detail/pricing
(`CARD_DETAIL_TTL_SECONDS`, 6 hours) — a TCGdex-side change may not show up
locally until these expire or the process restarts.

### Frontend capture flow

`src/lib/frameAnalysis.ts` samples the live camera feed client-side (~8x/sec)
and fires the shutter automatically once motion/focus/edge-density gates hold
for ~600ms — no round-trip to the backend for this. Gate thresholds are named
constants at the top of the file; tune them there and re-validate with
`npm run test:frames`. `CropCorrector.tsx` is the manual fallback (drag
corners) when auto-detection misses, feeding `warp_with_corners` on the
backend instead of `detect_card`.

### Config

Nearly all backend runtime behavior (matching weights, cache TTLs,
concurrency limits, feature kill-switches) is environment-driven through
`app/config.py`'s `Settings` class — check there before hardcoding a
threshold or limit elsewhere.
