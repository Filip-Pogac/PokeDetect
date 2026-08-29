"""Application configuration loaded from environment variables."""
from __future__ import annotations

import os
from functools import lru_cache


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

        # Database
        self.database_url: str = os.getenv("DATABASE_URL", "sqlite:///./pokedetect.db")

        # Pokemon TCG API (https://pokemontcg.io). A key raises rate limits but is optional.
        self.pokemontcg_api_key: str = os.getenv("POKEMONTCG_API_KEY", "")
        self.pokemontcg_base_url: str = os.getenv(
            "POKEMONTCG_BASE_URL", "https://api.pokemontcg.io/v2"
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
        # How many raw candidates to pull from the Pokemon TCG API text search.
        self.match_candidate_limit: int = int(os.getenv("MATCH_CANDIDATE_LIMIT", "40"))
        # Of those, how many (the top text-scored ones) get visually re-ranked
        # by fetching + perceptual-hashing their reference image.
        self.match_visual_rerank_limit: int = int(
            os.getenv("MATCH_VISUAL_RERANK_LIMIT", "18")
        )
        # How many ranked matches to return to the frontend.
        self.match_final_limit: int = int(os.getenv("MATCH_FINAL_LIMIT", "8"))
        # Kill switch for the visual re-ranking stage (network + CPU cost per scan).
        self.enable_visual_rerank: bool = (
            os.getenv("ENABLE_VISUAL_RERANK", "true").lower() == "true"
        )
        # Per-candidate-image fetch timeout for visual re-ranking.
        self.image_fetch_timeout_seconds: float = float(
            os.getenv("IMAGE_FETCH_TIMEOUT_SECONDS", "3.0")
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
