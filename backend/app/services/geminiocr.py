"""Card reading via Gemini's vision API, an alternative to local OCR.

Why this exists alongside EasyOCR: EasyOCR reads *characters*, so everything
that turns characters into a card identity - which line is the name, which of
the bottom corners holds the collector number, whether "VMAX" belongs to the
name above it - is reconstructed afterwards by the heuristics in `vision`. A
vision model reads the *card*, so it can be asked for those fields directly,
and it is markedly better on the two things that break local OCR here: small
stylised print (the collector number) and holo/glare interference.

It is a network call with a per-request cost, so it is opt-in via
OCR_ENGINE=gemini and always degrades to the local pipeline - see
`vision.recognize_text`. This module deliberately returns *raw* fields rather
than a `vision.TextReading`: validation (a collector number's halves, HP's
multiple-of-10 rule) lives in `vision` next to the local path's copy of it, so
both engines are held to the same standard and the import stays one-directional.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx

from ..config import get_settings
from . import timing

settings = get_settings()

# What the model is asked for. Two things in here are doing real work. The
# "exactly as printed" instruction on the name is what keeps era-specific
# suffixes ("ex", "VMAX") and regional prefixes ("Galarian") attached, which is
# the distinction between two different cards at two different prices - it is
# the same problem the local path solves by stitching modifier lines back on.
# And the instruction to leave a field empty rather than guess matters more
# than usual: a plausible-looking invented collector number is worse than none,
# because the ranker downstream treats a number as near-conclusive evidence.
_PROMPT = """You are reading a single Pokemon trading card from a photo that has already been flattened to the card's borders.

Report only what is legibly printed on this card:

- name: the Pokemon or card name exactly as printed, including any suffix that is part of the name (ex, EX, V, VMAX, VSTAR, GX, LV.X, BREAK) and any regional prefix (Galarian, Alolan, Hisuian, Paldean). Do not include the HP value or the stage line ("Stage 2", "Evolves from ...").
- name_candidates: alternative readings of the name if any part is unclear, best first. Leave empty if the name is unambiguous.
- number: the collector number printed in a bottom corner, in its printed form, e.g. "9/165". Promo and set-letter forms should be given as printed.
- hp: the printed HP as a whole number, or 0 if the card has none (Trainer and Energy cards).
- attacks: the names of the card's attacks and abilities, in printed order, without their energy costs or damage numbers.

Report a field as empty ("" or 0 or []) when it is not legible or not present on the card. Do not guess, and do not infer a value from your knowledge of which cards exist - an empty field is more useful than an invented one."""

# Gemini's structured-output schema. Constraining the response this way removes
# the whole class of failure where the model answers correctly in prose that
# then has to be parsed out of a code fence.
_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "name": {"type": "STRING"},
        "name_candidates": {"type": "ARRAY", "items": {"type": "STRING"}},
        "number": {"type": "STRING"},
        "hp": {"type": "INTEGER"},
        "attacks": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["name", "number", "hp", "attacks"],
}


@dataclass
class GeminiReading:
    """Raw fields as the model reported them, before any validation."""

    name_candidates: list[str] = field(default_factory=list)
    number: str | None = None
    hp: int | None = None
    attack_names: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        """Whether the model read nothing that could identify the card.

        HP and attack names are excluded on purpose: neither identifies a card
        on its own, so a response carrying only those is a failed read, and the
        caller should spend a local OCR pass rather than proceed on it.
        """
        return not self.name_candidates and not self.number


def is_configured() -> bool:
    """Whether the Gemini engine is selected and has a key to call with."""
    return settings.ocr_engine == "gemini" and bool(settings.gemini_api_key)


def _parse(payload: dict) -> GeminiReading | None:
    """Pull the reading out of a generateContent response body."""
    try:
        parts = payload["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        # A response with no candidates is a safety block or an empty
        # generation. Both mean "no reading", which the caller handles.
        return None

    text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))
    if not text.strip():
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    def _strings(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [s.strip() for s in (str(v) for v in value) if s.strip()]

    # The primary name leads the candidate list. Ordering matters downstream:
    # carddb.resolve_name can only ever pick a name it was offered, and takes
    # the earliest one that clears its similarity bar.
    names: list[str] = []
    seen: set[str] = set()
    for value in [str(data.get("name", ""))] + _strings(data.get("name_candidates")):
        value = value.strip()
        if value and value.lower() not in seen:
            seen.add(value.lower())
            names.append(value)

    number = str(data.get("number", "")).strip() or None

    raw_hp = data.get("hp")
    hp = int(raw_hp) if isinstance(raw_hp, (int, float)) and raw_hp else None

    return GeminiReading(
        name_candidates=names,
        number=number,
        hp=hp,
        attack_names=_strings(data.get("attacks")),
    )


def read_card(jpeg: bytes) -> GeminiReading | None:
    """Read a flattened card image, or None if the model could not be reached.

    None means "ask the local pipeline instead" and covers every failure mode
    equally - no key, timeout, rate limit, malformed body - because the caller's
    response to all of them is the same. Called from a worker thread (see
    scan._primary_cv), so a synchronous request is the right shape here.
    """
    if not is_configured() or not jpeg:
        return None

    import base64

    url = (
        f"{settings.gemini_base_url.rstrip('/')}/models/"
        f"{settings.gemini_model}:generateContent"
    )
    body = {
        "contents": [
            {
                "parts": [
                    {"text": _PROMPT},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": base64.b64encode(jpeg).decode("ascii"),
                        }
                    },
                ]
            }
        ],
        # Temperature 0: this is a transcription task with one right answer,
        # and sampling variety only invents plausible-looking card names.
        "generationConfig": {
            "temperature": 0.0,
            "responseMimeType": "application/json",
            "responseSchema": _RESPONSE_SCHEMA,
            # Gemini 3 models deliberate before answering by default, which on
            # a transcription task is latency spent on nothing: at the default
            # level a Base Set Charizard took ~18s and came back with an empty
            # attack list, and at "low" it took ~5s and read both attacks.
            "thinkingConfig": {"thinkingLevel": "low"},
        },
    }

    watch = timing.timer()
    try:
        with watch.stage("gemini_ocr_ms"):
            response = httpx.post(
                url,
                json=body,
                headers={"x-goog-api-key": settings.gemini_api_key},
                timeout=settings.gemini_timeout_seconds,
            )
        watch.count("gemini_calls")
        if response.status_code != 200:
            # 429 is the expected one on the free tier's daily quota, and it is
            # not an error worth failing a scan over - the local pipeline is
            # still there.
            watch.count("gemini_errors")
            return None
        return _parse(response.json())
    except (httpx.HTTPError, ValueError):
        watch.count("gemini_errors")
        return None
