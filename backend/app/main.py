"""PokeDetect API — FastAPI application entrypoint."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .database import init_db
from .routers import auth, collection, scan
from .services import carddb, imagematch, vision

settings = get_settings()
logger = logging.getLogger("pokedetect")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Pay the OCR model load here rather than inside whichever scan happens to
    # arrive first. Offloaded to a thread so startup stays responsive, and best
    # effort throughout: a machine without the model still serves everything
    # else, and the reader falls back to its lazy path on first use.
    if settings.ocr_warmup:
        try:
            ready = await asyncio.to_thread(vision.warmup_ocr)
            logger.info("ocr warm-up %s", "ready" if ready else "unavailable")
        except Exception:  # pragma: no cover - environment dependent
            logger.warning("ocr warm-up failed; first scan will load the model")
    yield
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
    return {"status": "ok"}
