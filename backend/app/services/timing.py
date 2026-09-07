"""Per-request stage timing for the scan pipeline.

The scan path is a long sequence of CPU work (decode, warp, OCR, grading) and
network work (search, detail enrichment, reference-image fetches), and until
now nothing measured the split between them. Optimising it without that number
is guesswork: OCR and the TCGdex fan-out are both plausible dominant costs, and
they call for opposite fixes.

The timer is carried in a ContextVar rather than threaded through every
signature, so `carddb`, `imagematch` and `vision` can record without changing
their public shapes. Outside a scan - unit tests, the collection routes - the
ContextVar holds nothing and `timer()` hands back a sink that discards
everything, so instrumentation is never a None check at the call site.

Note for threads: a ContextVar set on the event loop is *not* visible inside
`asyncio.to_thread` unless the context is copied. Capture `timer()` into a
local before offloading and pass the object itself.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


class ScanTimer:
    """Accumulates stage durations (ms) and event counters for one request."""

    def __init__(self) -> None:
        self.stages: dict[str, float] = {}
        self.counters: dict[str, int] = {}
        self._started = time.perf_counter()

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Time a block, accumulating if the same name is timed more than once."""
        start = time.perf_counter()
        try:
            yield
        finally:
            self.add_ms(name, (time.perf_counter() - start) * 1000.0)

    def add_ms(self, name: str, ms: float) -> None:
        self.stages[name] = self.stages.get(name, 0.0) + ms

    def count(self, name: str, n: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + n

    @property
    def total_ms(self) -> float:
        return (time.perf_counter() - self._started) * 1000.0

    def as_dict(self) -> dict[str, float | int | dict]:
        return {
            "total_ms": round(self.total_ms, 1),
            "stages_ms": {k: round(v, 1) for k, v in self.stages.items()},
            "counters": dict(self.counters),
        }

    def summary(self) -> str:
        """One-line, log-friendly rendering, slowest stage first."""
        stages = sorted(self.stages.items(), key=lambda kv: kv[1], reverse=True)
        parts = [f"{k}={v:.0f}ms" for k, v in stages]
        parts += [f"{k}={v}" for k, v in sorted(self.counters.items())]
        return f"total={self.total_ms:.0f}ms " + " ".join(parts)


class _NullTimer(ScanTimer):
    """Discards everything. Used when no scan is in flight."""

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        yield

    def add_ms(self, name: str, ms: float) -> None:
        pass

    def count(self, name: str, n: int = 1) -> None:
        pass


_NULL = _NullTimer()

current_timer: ContextVar[ScanTimer | None] = ContextVar("current_timer", default=None)


def timer() -> ScanTimer:
    """The timer for the request in flight, or a sink outside one."""
    return current_timer.get() or _NULL
