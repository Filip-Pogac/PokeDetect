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


@lru_cache
def get_settings() -> Settings:
    return Settings()
