"""PokeDetect API — FastAPI application entrypoint."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .database import engine, init_db
from .routers import auth, collection, scan
from .services import carddb, imagematch, vision

settings = get_settings()
logger = logging.getLogger("pokedetect")


# Readiness of the two startup jobs below, reported by /api/health. Plain
# strings rather than booleans because "why is it not ready" is the thing you
# actually need when the only window into the container is that endpoint.
_db_status = "pending"
_ocr_status = "pending"


async def _startup_jobs() -> None:
    """Prepare the database and the OCR model, off the startup critical path.

    Deliberately not awaited by the lifespan. A serving instance must answer
    its first request within a few seconds, and both of these can take far
    longer: building the EasyOCR reader runs a throwaway inference that costs
    several seconds on one vCPU, and a database connect can sit until its
    timeout. Awaiting them made the container miss its readiness window, so
    *every* route - /api/health included - failed at the platform level with
    no application log to show for it.

    Both are best effort. A failure here degrades one feature and is visible
    in /api/health, rather than taking the whole service down.
    """
    global _db_status, _ocr_status
    if settings.database_url_missing:
        _db_status = "misconfigured: DATABASE_URL is not set"
        logger.error(
            "DATABASE_URL is not set on a deployed instance; falling back to a "
            "throwaway SQLite file. Set it in the Vercel project's environment "
            "variables - any account created before then will be lost."
        )
    try:
        await asyncio.to_thread(init_db)
        _db_status = "ready"
        logger.info("database ready")
    except Exception as exc:
        _db_status = f"error: {type(exc).__name__}"
        logger.exception("database init failed; DB-backed routes will fail")

    # A Gemini engine without a key is not an error - every scan quietly takes
    # the local path and still works. That is exactly why it is worth saying
    # out loud: the deployment looks healthy while running at the accuracy the
    # key was meant to buy back, and nothing else would ever mention it.
    if settings.ocr_engine == "gemini" and not settings.gemini_api_key:
        logger.error(
            "OCR_ENGINE=gemini but GEMINI_API_KEY is not set; every scan will "
            "fall back to local OCR. Set it in the Vercel project's environment "
            "variables."
        )

    if not settings.ocr_warmup:
        _ocr_status = "skipped"
        return
    try:
        ready = await asyncio.to_thread(vision.warmup_ocr)
        _ocr_status = "ready" if ready else "unavailable"
        logger.info("ocr warm-up %s", _ocr_status)
    except Exception:  # pragma: no cover - environment dependent
        _ocr_status = "error"
        logger.warning("ocr warm-up failed; first scan will load the model")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_startup_jobs())
    yield
    task.cancel()
    # Release the pooled connections held by the card API and image clients.
    await carddb.close_client()
    await imagematch.close_image_client()


app = FastAPI(
    title="PokeDetect API",
    description="Recognize Pokémon cards, estimate condition, and compare Cardmarket prices.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(collection.router)
app.include_router(scan.router)


@app.get("/api/health", tags=["health"])
def health() -> dict[str, str]:
    """Liveness plus the state of the two background startup jobs.

    The database is probed live rather than reported from cache: on a platform
    where the container is the only place the failure is visible, this is the
    one endpoint that can say whether the instance can reach Neon at all.
    """
    db = _db_status
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_live = "ok"
    except Exception as exc:
        db_live = f"error: {type(exc).__name__}"
    return {
        "status": "ok",
        "database": db,
        "database_live": db_live,
        "ocr": _ocr_status,
        "ocr_engine": _ocr_engine_status(),
    }


def _ocr_engine_status() -> str:
    """Which engine scans actually use, and why, if it isn't the configured one.

    Reported because the Gemini path fails *open*: a missing key, a typo in the
    model id or an exhausted quota all end in a working scan served by local
    OCR, so there is no failure anywhere for an operator to notice. This is the
    one place that can say the vision model is configured but not in play.
    """
    if settings.ocr_engine != "gemini":
        return settings.ocr_engine
    if not settings.gemini_api_key:
        return "misconfigured: OCR_ENGINE=gemini without GEMINI_API_KEY; using easyocr"
    return f"gemini:{settings.gemini_model} (fallback: easyocr)"
