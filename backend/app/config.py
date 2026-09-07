"""Application configuration loaded from environment variables.

Values come from the process environment, with `backend/.env` loaded into it
first if that file exists. Real environment variables always win over the file,
so a container or CI job can override any of these without editing anything.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Both files are resolved relative to this file, not the working directory, so
# they are found no matter where the server is launched from.
#
# backend/.env      - hand-written local settings.
# <repo>/.env.local - written by `neon link` / `neon config pull`, holding the
#                     Neon connection strings. Machine-managed, gitignored, and
#                     overwritten by the CLI, so nothing hand-edited lives here.
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
NEON_ENV_FILE = Path(__file__).resolve().parents[2] / ".env.local"

# override=False throughout: anything already exported in the real environment
# takes precedence over both files. Done at import time so it lands before the
# first os.getenv call below.
#
# .env.local is loaded *first*, so on a linked checkout the Neon DATABASE_URL
# wins over whatever backend/.env says. Without that ordering, a leftover
# `DATABASE_URL=sqlite:///...` line in backend/.env would silently keep the app
# on the local file while appearing to be configured for Neon.
load_dotenv(NEON_ENV_FILE, override=False)
load_dotenv(ENV_FILE, override=False)


class Settings:
    """Runtime settings. Values come from environment variables with sane defaults."""

    def __init__(self) -> None:
        # Auth
        self.secret_key: str = os.getenv(
            "SECRET_KEY", "dev-only-insecure-secret-change-me-in-production-32b+"
        )
        self.algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
        self.access_token_expire_minutes: int = int(
            os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10080")  # 7 days
        )

        # Database. SQLite by default for local development; set DATABASE_URL
        # to a Postgres connection string (e.g. Neon) for any real deployment.
        # Vercel's filesystem is ephemeral, so a SQLite file there is wiped
        # whenever the instance is recycled - users and collections with it.
        # The SQLite default is a local-development convenience only. On a
        # deployed instance it is a trap: the app starts, accepts a signup, and
        # writes it to a container filesystem that is thrown away.
        #
        # Raising here was the obvious guard and is the wrong one - it kills
        # the container before it can serve anything, and a container that
        # cannot boot reports nothing useful. Flag it instead and let
        # /api/health say so, which is the one channel that still works.
        # VERCEL is set by the platform on every build and runtime instance.
        _database_url = os.getenv("DATABASE_URL", "")
        self.database_url_missing: bool = not _database_url and bool(
            os.getenv("VERCEL")
        )
        self.database_url: str = _database_url or "sqlite:///./pokedetect.db"
        # Connection pool, only used for non-SQLite engines. Small on purpose:
        # a serverless instance serves few requests at once, and every instance
        # holds its own pool against Neon's connection limit.
        self.db_pool_size: int = int(os.getenv("DB_POOL_SIZE", "2"))
        self.db_max_overflow: int = int(os.getenv("DB_MAX_OVERFLOW", "3"))
        # Fail fast instead of hanging a request when the database is
        # unreachable or still waking from auto-suspend.
        self.db_connect_timeout_seconds: int = int(
            os.getenv("DB_CONNECT_TIMEOUT_SECONDS", "10")
        )

        # Card database: TCGdex (https://tcgdex.dev). Free, no API key needed,
        # and carries Cardmarket pricing. Replaces pokemontcg.io, whose free
        # tier is gone (its API now answers 502).
        self.tcgdex_base_url: str = os.getenv(
            "TCGDEX_BASE_URL", "https://api.tcgdex.net/v2/en"
        )
        self.card_api_timeout_seconds: float = float(
            os.getenv("CARD_API_TIMEOUT_SECONDS", "15.0")
        )

        # CORS: comma-separated list of allowed origins for the frontend.
        self.cors_origins: list[str] = [
            origin.strip()
            for origin in os.getenv(
                "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
            ).split(",")
            if origin.strip()
        ]

        # OCR engine: "easyocr" (deep learning), "tesseract", or "none".
        self.ocr_engine: str = os.getenv("OCR_ENGINE", "easyocr").lower()

        # Card matching pipeline tunables.
        # How many raw candidates to pull from the card API text search. Sized
        # to cover *every* printing of a popular Pokemon (Dragonite alone has
        # ~55) rather than a first page, since the frontend now lists them all.
        self.match_candidate_limit: int = int(os.getenv("MATCH_CANDIDATE_LIMIT", "250"))
        # Of those, how many (the top text-scored ones) get visually re-ranked
        # by fetching + perceptual-hashing their reference image.
        self.match_visual_rerank_limit: int = int(
            os.getenv("MATCH_VISUAL_RERANK_LIMIT", "24")
        )
        # How many ranked matches to return to the frontend. High on purpose:
        # the point of the list is "here is every printing of this Pokemon,
        # pick yours", so truncating it to a handful hides the right card.
        self.match_final_limit: int = int(os.getenv("MATCH_FINAL_LIMIT", "60"))
        # Weight of the perceptual-hash signal when blending it with the text
        # score. Kept below the text weight: phash on a phone photo is noisy
        # (glare, holo, warp) and at higher weights it confidently promotes a
        # completely different Pokemon over an exact name match.
        self.match_visual_weight: float = float(os.getenv("MATCH_VISUAL_WEIGHT", "0.35"))
        # Text score below which the visual signal is ignored entirely - a
        # candidate whose *name* does not match the reading should never win on
        # image similarity alone.
        self.match_visual_min_text: float = float(
            os.getenv("MATCH_VISUAL_MIN_TEXT", "0.6")
        )
        # Kill switch for the visual re-ranking stage (network + CPU cost per scan).
        self.enable_visual_rerank: bool = (
            os.getenv("ENABLE_VISUAL_RERANK", "true").lower() == "true"
        )
        # Kill switch for the attack-name OCR pass. Costs one extra OCR crop
        # per scan and buys the signal that tells printings of a Pokemon apart
        # when the collector number is unreadable - a Charizard whose attack is
        # "Fire Spin" is the Base Set one, not the twenty other Charizards.
        self.enable_attack_ocr: bool = (
            os.getenv("ENABLE_ATTACK_OCR", "true").lower() == "true"
        )
        # Similarity at which a read attack line is accepted as being one of a
        # candidate's attacks. High: attack names are short, so a loose bar
        # matches unrelated ones ("Slash" vs "Slam").
        self.attack_match_threshold: float = float(
            os.getenv("ATTACK_MATCH_THRESHOLD", "0.86")
        )
        # Kill switch for frame-colour type detection (pure CPU, no network).
        self.enable_type_detection: bool = (
            os.getenv("ENABLE_TYPE_DETECTION", "true").lower() == "true"
        )
        # How confident the frame-colour reading must be before it is allowed
        # to influence ranking at all. Colour is the weakest signal here - holo
        # foiling, glare and full-art printings all corrupt it - so an unsure
        # reading is discarded rather than applied weakly.
        self.type_min_confidence: float = float(
            os.getenv("TYPE_MIN_CONFIDENCE", "0.5")
        )
        # Kill switch for the 1st-Edition-stamp OCR crop. One extra small pass,
        # cheap, and reads as "not found" on every non-WotC-era card by
        # construction - see vision.detect_first_edition.
        self.enable_first_edition_ocr: bool = (
            os.getenv("ENABLE_FIRST_EDITION_OCR", "true").lower() == "true"
        )
        # Kill switch for foil-finish texture detection (pure CPU, no network).
        # Decides between two prices TCGdex already carries for the same card
        # (e.g. normal vs reverse holo), never between candidates.
        self.enable_finish_detection: bool = (
            os.getenv("ENABLE_FINISH_DETECTION", "true").lower() == "true"
        )
        # Confidence floor for the foil-texture reading, independent of the
        # colour one above - they measure different things (texture vs hue)
        # and are tuned separately.
        self.finish_min_confidence: float = float(
            os.getenv("FINISH_MIN_CONFIDENCE", "0.5")
        )
        # How many per-card detail fetches (set/rarity/pricing) run at once when
        # enriching the final match list. Caps the fan-out now that the list can
        # be every printing of a Pokemon rather than a handful.
        self.enrich_concurrency: int = int(os.getenv("ENRICH_CONCURRENCY", "8"))
        # Per-candidate-image fetch timeout for visual re-ranking.
        self.image_fetch_timeout_seconds: float = float(
            os.getenv("IMAGE_FETCH_TIMEOUT_SECONDS", "3.0")
        )

        # Card-name index: one cached listing of every name in the database,
        # used to snap noisy OCR readings onto real card names.
        self.name_index_ttl_seconds: float = float(
            os.getenv("NAME_INDEX_TTL_SECONDS", "86400")  # 1 day
        )
        self.name_index_timeout_seconds: float = float(
            os.getenv("NAME_INDEX_TIMEOUT_SECONDS", "30.0")  # ~2MB response
        )
        # Similarity (0-1) at which an OCR reading is trusted to *be* that card
        # name and gets replaced by the database spelling.
        self.name_resolve_threshold: float = float(
            os.getenv("NAME_RESOLVE_THRESHOLD", "0.72")
        )
        # Lower bar for offering a name as a "did you mean" suggestion.
        self.name_suggest_threshold: float = float(
            os.getenv("NAME_SUGGEST_THRESHOLD", "0.55")
        )
        # How many suggested cards to return when nothing matched outright.
        self.suggestion_limit: int = int(os.getenv("SUGGESTION_LIMIT", "60"))

        # Per-card detail cache. Card metadata and Cardmarket trend prices move
        # slowly, and the same printings come back for every scan of the same
        # Pokemon, so caching detail fetches removes most of the enrichment
        # fan-out without changing which cards get enriched.
        self.card_detail_ttl_seconds: float = float(
            os.getenv("CARD_DETAIL_TTL_SECONDS", "21600")  # 6 hours
        )
        self.card_detail_cache_max: int = int(os.getenv("CARD_DETAIL_CACHE_MAX", "5000"))
        # Search-result cache. Much shorter: a search is a view over the whole
        # database, and new printings should show up the same day.
        self.search_cache_ttl_seconds: float = float(
            os.getenv("SEARCH_CACHE_TTL_SECONDS", "600")  # 10 minutes
        )
        self.search_cache_max: int = int(os.getenv("SEARCH_CACHE_MAX", "256"))

        # Build the OCR reader at startup instead of inside the first request.
        # Off by default only for tests, which should not pay for a model load.
        self.ocr_warmup: bool = os.getenv("OCR_WARMUP", "true").lower() == "true"

        # Attach the per-stage timing breakdown to the scan response. Always
        # logged; this only controls whether it also travels to the client.
        self.debug_timings: bool = os.getenv("DEBUG_TIMINGS", "false").lower() == "true"

        # Set index (set id -> printed card count), cached like the name index.
        # It is what turns the "/165" half of a collector number into a set
        # filter: "9/165" is the 9th card of a 165-card set.
        self.set_index_ttl_seconds: float = float(
            os.getenv("SET_INDEX_TTL_SECONDS", "86400")  # 1 day
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
