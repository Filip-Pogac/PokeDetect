"""Computer-vision pipeline for card detection, text recognition and condition.

Pipeline
--------
1. detect_card()      Locate the card rectangle in the photo and perspective-warp
                      it to a flat, upright image (classical CV with OpenCV).
2. recognize_text()   Read text off the card with a deep-learning OCR engine
                      (EasyOCR, a CRNN-based recognizer) and pull out a likely
                      card name and collector number.
3. assess_condition() Estimate a Cardmarket-style condition grade from image
                      cues: focus/sharpness, corner and edge wear, and glare.

Every stage degrades gracefully: if OpenCV or the OCR engine is unavailable the
functions still return sensible, typed results so the API never hard-fails.
"""
from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field

import numpy as np

from ..config import get_settings

settings = get_settings()

# --- Optional heavy dependencies, imported lazily/defensively ---
try:
    import cv2  # type: ignore
    _HAS_CV2 = True
except Exception:  # pragma: no cover - environment dependent
    _HAS_CV2 = False

# EasyOCR reader is expensive to construct, so build it once on first use.
_easyocr_reader = None
_ocr_unavailable = False

# Standard trading-card aspect ratio (63mm x 88mm).
CARD_W, CARD_H = 630, 880

CONDITION_ORDER = [
    "Mint",
    "Near Mint",
    "Excellent",
    "Good",
    "Light Played",
    "Played",
    "Poor",
]


@dataclass
class CardDetection:
    image: np.ndarray  # warped, upright card (BGR) or the original frame
    detected: bool  # whether a card boundary was actually found


@dataclass
class ConditionResult:
    condition: str
    confidence: float
    is_potentially_damaged: bool
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Image decoding
# --------------------------------------------------------------------------- #
def decode_image(data_url_or_b64: str) -> np.ndarray | None:
    """Decode a data URL or raw base64 string into a BGR image array."""
    if not data_url_or_b64:
        return None
    payload = data_url_or_b64
    if payload.startswith("data:"):
        _, _, payload = payload.partition(",")
    try:
        raw = base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError):
        return None
    if not _HAS_CV2:
        return None
    buf = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return img


# --------------------------------------------------------------------------- #
# 1. Card detection + perspective correction
# --------------------------------------------------------------------------- #
def _order_corners(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def detect_card(image: np.ndarray) -> CardDetection:
    """Find the largest card-like quadrilateral and warp it flat."""
    if not _HAS_CV2 or image is None:
        return CardDetection(image=image, detected=False)

    h, w = image.shape[:2]
    scale = 1000.0 / max(h, w) if max(h, w) > 1000 else 1.0
    small = cv2.resize(image, (int(w * scale), int(h * scale)))

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 40, 120)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(
        edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    frame_area = small.shape[0] * small.shape[1]
    best_quad = None
    best_area = 0.0

    for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:8]:
        area = cv2.contourArea(cnt)
        if area < frame_area * 0.15:  # ignore small blobs
            continue
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
        if len(approx) == 4 and area > best_area:
            best_quad = approx.reshape(4, 2).astype("float32") / scale
            best_area = area

    if best_quad is None:
        return CardDetection(image=image, detected=False)

    rect = _order_corners(best_quad)
    dst = np.array(
        [[0, 0], [CARD_W - 1, 0], [CARD_W - 1, CARD_H - 1], [0, CARD_H - 1]],
        dtype="float32",
    )
    matrix = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(image, matrix, (CARD_W, CARD_H))
    return CardDetection(image=warped, detected=True)


# --------------------------------------------------------------------------- #
# 2. OCR (deep-learning text recognition)
# --------------------------------------------------------------------------- #
def _get_easyocr():
    global _easyocr_reader, _ocr_unavailable
    if _easyocr_reader is not None or _ocr_unavailable:
        return _easyocr_reader
    try:
        import easyocr  # type: ignore

        _easyocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    except Exception:  # pragma: no cover - environment/network dependent
        _ocr_unavailable = True
        _easyocr_reader = None
    return _easyocr_reader


def _ocr_lines(image: np.ndarray) -> list[str]:
    engine = settings.ocr_engine
    if engine == "none" or not _HAS_CV2 or image is None:
        return []

    if engine == "tesseract":
        try:
            import pytesseract  # type: ignore

            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            text = pytesseract.image_to_string(rgb)
            return [ln.strip() for ln in text.splitlines() if ln.strip()]
        except Exception:
            return []

    # Default: EasyOCR (deep learning).
    reader = _get_easyocr()
    if reader is None:
        return []
    try:
        results = reader.readtext(image, detail=0, paragraph=False)
        return [str(r).strip() for r in results if str(r).strip()]
    except Exception:
        return []


_NUMBER_RE = re.compile(r"\b(\d{1,3})\s*/\s*(\d{1,3})\b")
_NAME_STOPWORDS = {
    "hp", "basic", "stage", "trainer", "energy", "pokemon", "pokémon",
    "evolves", "from", "put", "damage", "weakness", "resistance", "retreat",
    "illus", "the", "ex", "gx", "vmax", "vstar", "v",
}


def _guess_name_and_number(lines: list[str]) -> tuple[str | None, str | None]:
    """Heuristically pull a card name and collector number out of OCR lines."""
    number: str | None = None
    for line in lines:
        m = _NUMBER_RE.search(line)
        if m:
            number = f"{m.group(1)}/{m.group(2)}"
            break

    # Name candidate: an early, mostly-alphabetic line that isn't a stopword and
    # isn't dominated by digits. Card names sit at the top of the card.
    name: str | None = None
    for line in lines[:6]:
        letters = re.sub(r"[^A-Za-z ]", "", line).strip()
        if len(letters) < 3:
            continue
        words = [w for w in letters.split() if w]
        if not words:
            continue
        if all(w.lower() in _NAME_STOPWORDS for w in words):
            continue
        # Reject lines that are mostly numbers/symbols.
        if len(letters) / max(len(line), 1) < 0.5:
            continue
        name = letters.strip()
        break

    return name, number


def recognize_text(image: np.ndarray) -> tuple[list[str], str | None, str | None]:
    """Return (all_lines, guessed_name, guessed_number)."""
    lines = _ocr_lines(image)
    name, number = _guess_name_and_number(lines)
    return lines, name, number


# --------------------------------------------------------------------------- #
# 3. Condition / damage assessment
# --------------------------------------------------------------------------- #
def _score_to_condition(score: float) -> str:
    """Map a 0-100 quality score to a Cardmarket condition grade."""
    thresholds = [
        (92, "Mint"),
        (82, "Near Mint"),
        (70, "Excellent"),
        (55, "Good"),
        (40, "Light Played"),
        (25, "Played"),
    ]
    for cutoff, grade in thresholds:
        if score >= cutoff:
            return grade
    return "Poor"


def assess_condition(image: np.ndarray, detected: bool) -> ConditionResult:
    """Estimate condition from focus, corner/edge wear and glare cues."""
    if not _HAS_CV2 or image is None:
        return ConditionResult(
            condition="Near Mint",
            confidence=0.3,
            is_potentially_damaged=False,
            notes=["Automatic condition analysis unavailable; defaulting to Near Mint."],
        )

    notes: list[str] = []
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]

    # (a) Sharpness: variance of the Laplacian. Blurry photos or soft, worn
    # surfaces reduce high-frequency detail.
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    # Normalise: ~<40 very soft, >400 crisp.
    sharp_score = float(np.clip((sharpness - 40.0) / (400.0 - 40.0), 0.0, 1.0))
    if sharp_score < 0.35:
        notes.append("Soft focus or surface wear reduces edge detail.")

    # (b) Glare: fraction of blown-out highlight pixels (scratches/holo glare).
    glare_ratio = float(np.mean(gray > 245))
    glare_score = float(np.clip(1.0 - (glare_ratio - 0.02) / 0.18, 0.0, 1.0))
    if glare_ratio > 0.08:
        notes.append("Strong glare/reflection detected; grade may be less reliable.")

    # (c) Corner wear: bright/whitened pixels in the four corner patches often
    # indicate frayed or whitened corners.
    patch = max(12, min(h, w) // 12)
    corners = [
        gray[0:patch, 0:patch],
        gray[0:patch, w - patch:w],
        gray[h - patch:h, 0:patch],
        gray[h - patch:h, w - patch:w],
    ]
    corner_white = float(np.mean([np.mean(c > 220) for c in corners]))
    corner_score = float(np.clip(1.0 - corner_white / 0.6, 0.0, 1.0))
    if corner_white > 0.25:
        notes.append("Possible corner whitening or wear.")

    # (d) Edge/border straightness: how much the outer border deviates.
    edges = cv2.Canny(gray, 50, 150)
    border = np.concatenate(
        [
            edges[0:3, :].ravel(),
            edges[h - 3:h, :].ravel(),
            edges[:, 0:3].ravel(),
            edges[:, w - 3:w].ravel(),
        ]
    )
    edge_noise = float(np.mean(border > 0))
    edge_score = float(np.clip(1.0 - (edge_noise - 0.15) / 0.5, 0.0, 1.0))
    if edge_noise > 0.4:
        notes.append("Edges look uneven; possible edge wear or creasing.")

    # Weighted overall quality score (0-100).
    quality = (
        0.40 * sharp_score
        + 0.20 * glare_score
        + 0.25 * corner_score
        + 0.15 * edge_score
    ) * 100.0
    condition = _score_to_condition(quality)

    # Confidence is lower when we couldn't isolate the card or glare is high.
    confidence = 0.75 if detected else 0.45
    confidence *= float(np.clip(1.0 - glare_ratio, 0.5, 1.0))
    confidence = round(float(np.clip(confidence, 0.2, 0.95)), 2)

    is_damaged = condition in {"Good", "Light Played", "Played", "Poor"}
    if is_damaged and not notes:
        notes.append("Overall wear suggests this card is not in top condition.")
    if not notes:
        notes.append("No obvious damage detected.")

    return ConditionResult(
        condition=condition,
        confidence=confidence,
        is_potentially_damaged=is_damaged,
        notes=notes,
    )
