"""Computer-vision pipeline for card detection, text recognition and condition.

Pipeline
--------
1. detect_card()      Locate the card rectangle in the photo and perspective-warp
                      it to a flat, upright image (classical CV with OpenCV).
2. recognize_text()   Read text off the card with a deep-learning OCR engine
                      (EasyOCR, a CRNN-based recognizer). OCRs the name,
                      collector-number and (only when the number is missing)
                      attack regions first - small, fast, less noisy crops -
                      and only falls back to whole-card OCR if that fails.
                      Returns *several* name candidates; carddb.resolve_name
                      decides which one is a real card name.
3. assess_condition() Estimate a Cardmarket-style condition grade from image
                      cues: focus/sharpness, corner and edge wear, and glare.
4. detect_type()      Read the energy type off the frame colour. Returns the
                      plausible types rather than one, because several are
                      genuine colour neighbours.
5. compute_phash()    Perceptual hash of the warped card, used by the scan
                      router to visually verify/rank candidate matches.

Every stage degrades gracefully: if OpenCV or the OCR engine is unavailable the
functions still return sensible, typed results so the API never hard-fails.
"""
from __future__ import annotations

import base64
import binascii
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from ..config import get_settings
from . import cardname, timing

settings = get_settings()

# --- Optional heavy dependencies, imported lazily/defensively ---
try:
    import cv2  # type: ignore
    _HAS_CV2 = True
except Exception:  # pragma: no cover - environment dependent
    _HAS_CV2 = False

try:
    from PIL import Image  # type: ignore
    import imagehash  # type: ignore
    _HAS_IMAGEHASH = True
except Exception:  # pragma: no cover - environment dependent
    _HAS_IMAGEHASH = False

# EasyOCR reader is expensive to construct, so build it once on first use.
_easyocr_reader = None
_ocr_unavailable = False

# Standard trading-card aspect ratio (63mm x 88mm). This is the *baseline* warp
# size; a high-resolution source is warped to a multiple of it, see _warp_scale.
CARD_W, CARD_H = 630, 880

# How much larger than the baseline a warped card may be. A collector number is
# roughly 2mm tall - about 20 pixels at the baseline size - so downsampling a
# 12MP photo to 630x880 before OCR destroys exactly the text that identifies the
# printing. 2x is where that text becomes comfortably legible; beyond it OCR
# cost climbs with the pixel count for no further accuracy.
MAX_WARP_SCALE = 2.0

# Fixed crop boxes (fractions of CARD_W/CARD_H) for the two text regions that
# matter for identification. The name band is deliberately generous: card
# layouts differ by era (modern cards put the name top-left beside the HP;
# e-Card/EX-era ones print a "STAGE 2 / Evolves from ..." line above it), so the
# band covers the whole top fifth and every line in it becomes a name candidate,
# scored later against the real card-name index rather than guessed at here.
_NAME_BAND = (0.02, 0.010, 0.98, 0.22)  # (x0, y0, x1, y1) as fractions

# The collector number ("9/165") is the single most discriminating thing on the
# card - it separates the ~55 printings of Dragonite from each other - but where
# it sits moves by era: bottom-left on modern cards, bottom-right on WotC and
# e-Card era ones. All of these are OCR'd and the results pooled; a box that
# holds no number simply contributes nothing.
_NUMBER_BOXES = (
    (0.02, 0.875, 0.55, 0.985),  # bottom-left (modern)
    (0.45, 0.875, 0.99, 0.985),  # bottom-right (WotC / e-Card era)
    (0.02, 0.930, 0.99, 1.000),  # full bottom strip (catch-all)
)

# The attack block. Its top edge moves by era - the Pokedex info strip pushes
# it down on WotC cards, a taller artwork window on modern ones, a VMAX further
# still - so the band is deliberately generous and the junk it picks up (rules
# text, the weakness/resistance row) is filtered out by _clean_attack_line
# rather than by tighter geometry that would miss whole eras.
_ATTACK_BAND = (0.05, 0.48, 0.95, 0.88)

# Where the card's frame colour can be read. The weakness/resistance/retreat
# row is the most reliable patch across every era: it spans nearly the full
# width, carries only small icons, and is painted in the frame colour on WotC,
# e-Card and modern layouts alike. The two side rails sit between the yellow
# outer border and the artwork window, and are sampled as corroboration - if
# one patch lands on artwork or glare, the median over all three survives it.
_TYPE_SAMPLE_BOXES = (
    (0.10, 0.855, 0.90, 0.895),  # weakness / resistance / retreat row
    (0.055, 0.30, 0.080, 0.70),  # left frame rail
    (0.920, 0.30, 0.945, 0.70),  # right frame rail
)

# Where the 1st Edition stamp sits on WotC-era cards: a small black wordmark
# with a laurel icon, to the left of the artwork window, above the attack
# block. Modern cards have nothing here - the crop simply OCRs blank/noise and
# the regex below finds nothing, which is the correct "not 1st edition" answer
# for every card printed after 2000.
_FIRST_EDITION_BAND = (0.03, 0.36, 0.30, 0.46)

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
class TypeReading:
    """The card's energy type as read off its frame colour.

    `types` is a *set* of plausible types, best first, not a single answer.
    That is deliberate: several types are genuinely close in colour - Fire and
    Fighting are both warm oranges, Metal and Colorless both near-grey - and a
    reading that pretends to separate them would be inventing precision it does
    not have. What the frame colour reliably says is that a blue-framed card is
    not a Fire card, which is enough to demote wrong candidates.
    """

    types: list[str]
    confidence: float


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


def _warp_scale(rect: np.ndarray) -> float:
    """How many multiples of the baseline card size this quad can support.

    Derived from the quad's own size in source pixels, so a card photographed at
    high resolution is rectified at high resolution instead of being thrown away
    by a fixed-size warp. Never below 1.0: upscaling a small card would only
    invent detail, and the baseline is what the fractional crop boxes and the
    condition heuristics are tuned against.
    """
    top = np.linalg.norm(rect[1] - rect[0])
    bottom = np.linalg.norm(rect[2] - rect[3])
    left = np.linalg.norm(rect[3] - rect[0])
    right = np.linalg.norm(rect[2] - rect[1])
    width = max(float(top), float(bottom))
    height = max(float(left), float(right))
    if width <= 0 or height <= 0:
        return 1.0
    # The limiting dimension decides - a quad that is generous in one axis but
    # thin in the other has no more real detail than its thin side allows.
    scale = min(width / CARD_W, height / CARD_H)
    return float(np.clip(scale, 1.0, MAX_WARP_SCALE))


def _warp_to_card(image: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Perspective-correct a four-point quad to a flat, upright card image.

    The output is CARD_W x CARD_H scaled up by _warp_scale, so all the crop
    boxes stay valid (they are fractions) while OCR keeps whatever resolution
    the original photo actually had.

    Shared by automatic detection and the user-corrected corners path.
    """
    rect = _order_corners(quad)
    scale = _warp_scale(rect)
    out_w, out_h = int(round(CARD_W * scale)), int(round(CARD_H * scale))
    dst = np.array(
        [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]],
        dtype="float32",
    )
    matrix = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(image, matrix, (out_w, out_h))


# Aspect ratio of a real card (63mm x 88mm).
CARD_RATIO = CARD_W / CARD_H
# Tolerance for validating a rectangle found *inside* a photo. Generous, because
# perspective foreshortens a card photographed even slightly off-axis.
_RATIO_TOLERANCE = 0.30
# Tolerance for deciding a whole image is itself a card. Deliberately tight: a
# phone photo is 3:4 (0.75) or 9:16, and at the loose tolerance those would be
# mistaken for full-bleed card scans and skip detection entirely.
_EXACT_RATIO_TOLERANCE = 0.04


def _is_card_shaped(width: float, height: float, tolerance: float | None = None) -> bool:
    """Whether a w x h rectangle has roughly a trading card's proportions."""
    if width <= 0 or height <= 0:
        return False
    tolerance = _RATIO_TOLERANCE if tolerance is None else tolerance
    # Accept either orientation; a sideways card is still a card.
    ratio = min(width, height) / max(width, height)
    return abs(ratio - CARD_RATIO) <= tolerance * CARD_RATIO


def _whole_frame(image: np.ndarray) -> CardDetection:
    """Treat the entire image as the card and warp it to the standard size."""
    h, w = image.shape[:2]
    quad = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype="float32")
    return CardDetection(image=_warp_to_card(image, quad), detected=True)


def _quad_from_contour(cnt: np.ndarray) -> np.ndarray | None:
    """Reduce a contour to four corners, or None if it isn't a quadrilateral.

    A single epsilon is not enough on real photographs: card corners are rounded
    and edges are noisy, so a 2% approximation routinely yields five or six
    points and the card is missed entirely. Progressively coarser
    approximations are tried, and if none lands on exactly four points the
    contour's minimum-area rectangle is used instead - a rotated bounding box is
    a perfectly good fit for something known to be rectangular.
    """
    peri = cv2.arcLength(cnt, True)
    for epsilon in (0.02, 0.03, 0.04, 0.05, 0.07):
        approx = cv2.approxPolyDP(cnt, epsilon * peri, True)
        if len(approx) == 4:
            return approx.reshape(4, 2).astype("float32")

    rect = cv2.minAreaRect(cnt)
    (_, _), (rw, rh), _ = rect
    if not _is_card_shaped(rw, rh):
        return None
    # The box must actually be filled by the contour, or this is a sprawling
    # edge blob whose bounding box happens to be card-shaped.
    if rw * rh <= 0 or cv2.contourArea(cnt) / (rw * rh) < 0.75:
        return None
    return cv2.boxPoints(rect).astype("float32")


def detect_card(image: np.ndarray) -> CardDetection:
    """Find the largest card-like quadrilateral and warp it flat.

    An image that already has a card's exact proportions is used whole. Anything
    else is searched for a card-shaped rectangle, falling back from an exact
    4-point contour to the rotated bounding box of a card-shaped contour, since
    rounded corners and noisy edges routinely make a real card approximate to
    five or six points rather than four.
    """
    if not _HAS_CV2 or image is None:
        return CardDetection(image=image, detected=False)

    h, w = image.shape[:2]

    # An image already at a card's exact proportions *is* the card - a scan or a
    # tight crop. Checked before contour search, not after: on a borderless card
    # image the strongest edge is the inner border of the frame, so searching
    # first would crop the outer border away and take the collector number with
    # it, which is the whole reason the reading was failing on clean uploads.
    if _is_card_shaped(w, h, _EXACT_RATIO_TOLERANCE):
        return _whole_frame(image)

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
        if area < frame_area * 0.10:  # ignore small blobs
            continue
        quad = _quad_from_contour(cnt)
        if quad is None or area <= best_area:
            continue
        # Reject quads that aren't card-shaped, measured on the rotated box so
        # perspective skew doesn't disqualify a genuine card.
        (_, _), (rw, rh), _ = cv2.minAreaRect(quad)
        if not _is_card_shaped(rw, rh):
            continue
        best_quad = quad / scale
        best_area = area

    if best_quad is not None:
        return CardDetection(image=_warp_to_card(image, best_quad), detected=True)

    return CardDetection(image=image, detected=False)


def warp_with_corners(
    image: np.ndarray, corners_norm: Sequence[Sequence[float]]
) -> CardDetection:
    """Warp using corners the user placed by hand, bypassing auto-detection.

    `corners_norm` is four (x, y) points as fractions of image width/height —
    normalized so the frontend, which renders the still at an arbitrary CSS
    size, never needs to know the image's pixel dimensions.

    Returns detected=True: corners the user confirmed are at least as
    trustworthy as an automatic fit, and marking them as a detection re-enables
    the region-crop OCR and perceptual hash that a failed detection turns off.
    Falls back to automatic detection if anything is wrong with the input.
    """
    if not _HAS_CV2 or image is None:
        return CardDetection(image=image, detected=False)
    try:
        h, w = image.shape[:2]
        quad = np.array(
            [[float(x) * w, float(y) * h] for x, y in corners_norm], dtype="float32"
        )
        if quad.shape != (4, 2) or not np.isfinite(quad).all():
            return detect_card(image)
        return CardDetection(image=_warp_to_card(image, quad), detected=True)
    except Exception:
        return detect_card(image)


# --------------------------------------------------------------------------- #
# 2. OCR (deep-learning text recognition)
# --------------------------------------------------------------------------- #
# EasyOCR's Reader is not safe to call from several threads at once, and torch
# already parallelises a single inference across cores - so concurrent scans
# are serialised here rather than oversubscribing the CPU. This costs nothing
# on a single scan: the point of moving OCR off the event loop is that the
# *loop* stays free to drive other requests' network work, not that two scans
# recognise text simultaneously.
_OCR_LOCK = threading.Lock()


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


def warmup_ocr() -> bool:
    """Build the OCR reader and run one throwaway inference.

    Called at startup so the model load - and the lazy allocations of the first
    inference, which are a large part of the cost - happen before any user is
    waiting. Without this the first scan after every restart pays several
    seconds that have nothing to do with that scan, and with --reload in
    development that is every code edit.

    Returns whether an OCR engine is actually available.
    """
    if settings.ocr_engine != "easyocr" or not _HAS_CV2:
        return False
    reader = _get_easyocr()
    if reader is None:
        return False
    try:
        reader.readtext(np.zeros((64, 256, 3), np.uint8), detail=0, paragraph=False)
    except Exception:  # pragma: no cover - warm-up is best effort
        pass
    return True


def _ocr_lines(image: np.ndarray) -> list[str]:
    engine = settings.ocr_engine
    if engine == "none" or not _HAS_CV2 or image is None or image.size == 0:
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
        watch = timing.timer()
        watch.count("ocr_passes")
        watch.count("ocr_pixels", int(image.shape[0] * image.shape[1]))
        with watch.stage("ocr_ms"):
            with _OCR_LOCK:
                results = reader.readtext(image, detail=0, paragraph=False)
        return [str(r).strip() for r in results if str(r).strip()]
    except Exception:
        return []


def _baseline_size(image: np.ndarray) -> np.ndarray:
    """The image at the standard CARD_W x CARD_H warp size."""
    if image.shape[0] == CARD_H and image.shape[1] == CARD_W:
        return image
    return cv2.resize(image, (CARD_W, CARD_H), interpolation=cv2.INTER_AREA)


def _crop_region(image: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray:
    """Crop a (x0, y0, x1, y1) fractional box out of an image, bounds-checked."""
    h, w = image.shape[:2]
    x0, y0, x1, y1 = box
    px0, py0 = max(0, int(x0 * w)), max(0, int(y0 * h))
    px1, py1 = min(w, int(x1 * w)), min(h, int(y1 * h))
    if px1 <= px0 or py1 <= py0:
        return image[0:0, 0:0]
    return image[py0:py1, px0:px1]


# Target height for a preprocessed text crop. EasyOCR's recognizer wants text
# tens of pixels tall; below that it guesses, above it just costs time.
_OCR_TARGET_HEIGHT = 320


def _preprocess_for_ocr(crop: np.ndarray, scale: float | None = None) -> np.ndarray:
    """Upscale + contrast-boost a text region for more reliable OCR.

    The upscale factor is chosen to bring the crop to a consistent working
    height rather than being fixed, because the warped card is no longer a fixed
    size - a blanket 3.5x on a 3x warp would produce a needlessly huge image and
    slow every scan down.
    """
    if crop.size == 0:
        return crop
    if scale is None:
        height = max(1, crop.shape[0])
        scale = float(np.clip(_OCR_TARGET_HEIGHT / height, 1.0, 4.0))
    upscaled = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(upscaled, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    return clahe.apply(gray)


# Collector numbers are printed small, so OCR routinely swaps letters for the
# digits they look like. Inside a "<number> / <total>" shape those letters can
# only have been digits, so they are mapped back before parsing.
_DIGIT_LOOKALIKES = str.maketrans(
    {
        "O": "0", "o": "0", "Q": "0", "D": "0",
        "I": "1", "l": "1", "i": "1",
        "Z": "2", "z": "2",
        "S": "5", "s": "5",
        "G": "6", "b": "6",
        "T": "7",
        "B": "8",
        "g": "9", "q": "9",
    }
)

# The slash is frequently read as a backslash or a pipe. It is folded back to a
# slash *before* the look-alike table runs, because "|" is ambiguous - it could
# equally be a "1" - and treating it as the separator is what keeps "9|165"
# parseable instead of collapsing it into "91165".
_SEPARATORS = str.maketrans({"\\": "/", "|": "/", "\u2044": "/", "\u2215": "/"})

# Some sets print a set letter after the total ("9/165 E"), so the total is not
# anchored at a word boundary - only at "not another digit".
_NUMBER_RE = re.compile(r"(\d{1,3})\s*/\s*(\d{1,3})(?!\d)")

# Words that never appear alone as a card name. A line made only of these is
# template chrome ("STAGE 2", "Basic Pokemon") rather than an identity.
_NAME_STOPWORDS = {
    "hp", "basic", "stage", "trainer", "energy", "pokemon", "pokémon",
    "evolves", "from", "put", "damage", "weakness", "resistance", "retreat",
    "illus", "the", "ex", "gx", "vmax", "vstar", "v", "ability", "item",
    "supporter", "stadium", "on", "card", "your", "this", "and", "to", "of",
}

# Template words that sit on the name's own row and can be trimmed off it.
_NAME_TRIM_WORDS = {"hp", "stage", "basic", "pokemon", "pokémon", "trainer"}

# HP prints on the name's own row on every layout - "320 HP" on modern cards,
# "HP 80" on WotC-era ones - so it comes free with the name-band OCR and needs
# no extra pass. It is worth having: it is what separates a V from its VMAX and
# a 2016 Charizard EX from a 2023 Charizard ex when both share a name.
_HP_RE = re.compile(r"(?:HP\s*[:.]?\s*(\d{1,3})|(\d{1,3})\s*HP)", re.IGNORECASE)

# Lines that are structurally *about* another card rather than naming this one.
# The evolution line is the dangerous one: "Evolves from Dragonair / Put
# Dragonite on the Stage 1 card" sits directly above the name on e-Card-era
# layouts, and a misread of it is what produced readings like "conar".
_NOT_A_NAME_RE = re.compile(
    r"^\s*(evolves\s+from|put\s|stage\s*\d|basic\b|does\s|attach\b|illus)",
    re.IGNORECASE,
)


@dataclass
class TextReading:
    """What OCR could make of a card."""

    lines: list[str]  # every line read, for debugging/display
    name_candidates: list[str]  # plausible names, most likely first
    number: str | None  # collector number, e.g. "9/165"
    # The "/165" half on its own. A set's printed card count identifies the set
    # far more sharply than the card index does, so it is kept separately: even
    # when the left-hand number is misread, the total still narrows ~55 possible
    # printings of a Pokemon down to the few sets of that size.
    set_total: int | None = None
    # Every "<n>/<total>" shape read, best first. Kept because the boxes are
    # OCR'd independently and the second-best reading is often the right one.
    number_candidates: list[str] = field(default_factory=list)
    # Printed HP, e.g. 320. Only meaningful once a candidate has been enriched
    # (search rows carry no HP), so it is a late tie-breaker rather than a
    # search term - see imagematch.apply_detail_agreement.
    hp: int | None = None
    # Attack names read off the card, e.g. ["Max Whiteout"]. Like HP, they are
    # compared against detail-only data, so they are a late signal too.
    attack_names: list[str] = field(default_factory=list)

    @property
    def name(self) -> str | None:
        return self.name_candidates[0] if self.name_candidates else None


def _normalize_digits(line: str) -> str:
    """Normalize a line into the form the collector-number regex expects.

    Separators are folded to "/" first, then digit-look-alike letters are
    mapped back to digits - the order matters, see _SEPARATORS.
    """
    return line.translate(_SEPARATORS).translate(_DIGIT_LOOKALIKES)


def _extract_numbers(lines: list[str]) -> list[str]:
    """Every plausible "<n>/<total>" reading in the lines, in order, deduped.

    Readings whose left half exceeds the total ("165/9") are dropped as
    transpositions rather than trusted - a card's index is never larger than the
    set it belongs to.
    """
    found: list[str] = []
    seen: set[str] = set()
    for line in lines:
        for m in _NUMBER_RE.finditer(_normalize_digits(line)):
            index, total = int(m.group(1)), int(m.group(2))
            if total == 0 or index == 0 or index > total:
                continue
            value = f"{index}/{total}"
            if value not in seen:
                seen.add(value)
                found.append(value)
    return found


def _extract_hp(lines: list[str]) -> int | None:
    """The card's printed HP, or None.

    Two things make this safe enough to act on despite being small print. The
    literal "HP" has to be there - a bare number anywhere on the card is attack
    damage, not HP - and every real HP value is a multiple of 10 between 10 and
    400, so a reading that isn't one is a misread rather than an unusual card.
    Both the raw and digit-corrected forms of each line are tried, since the
    same look-alike confusions that hit collector numbers hit HP too.
    """
    for line in lines:
        for text in (line, _normalize_digits(line)):
            match = _HP_RE.search(text)
            if match is None:
                continue
            value = int(match.group(1) or match.group(2))
            if 10 <= value <= 400 and value % 10 == 0:
                return value
    return None


def _clean_name_line(line: str) -> str | None:
    """Reduce one OCR line to a bare name, or None if it can't be one.

    Strips the HP value and energy/symbol noise that share the name's row on
    modern layouts ("Lucario 110 ⬤" -> "Lucario").
    """
    if _NOT_A_NAME_RE.search(line):
        return None
    letters = re.sub(r"[^A-Za-z' \-]", " ", line)
    letters = re.sub(r"\s+", " ", letters).strip(" -'")
    if len(letters) < 3:
        return None
    words = [w for w in letters.split() if w]
    if not words or all(w.lower() in _NAME_STOPWORDS for w in words):
        return None
    # "Lucario 110 HP" cleans to "Lucario HP" - drop the template words the name
    # shares its row with, from both ends. Only these: suffixes like "ex" or
    # "VMAX" are part of the real card name and must survive.
    while words and words[0].lower() in _NAME_TRIM_WORDS:
        words.pop(0)
    while words and words[-1].lower() in _NAME_TRIM_WORDS:
        words.pop()
    if not words:
        return None
    letters = " ".join(words)
    if len(letters) < 3:
        return None
    # Card names are short. Longer lines are rules or flavor text.
    if len(words) > 4:
        return None
    # Reject lines that were mostly digits/symbols before cleaning.
    if len(letters) / max(len(line), 1) < 0.4:
        return None
    return letters


def _letters_only(line: str) -> str:
    """The line's words, punctuation dropped but casing kept.

    Casing is preserved because these strings end up in the name candidates the
    user can see; cardname does its own case-insensitive comparison.
    """
    return " ".join(re.sub(r"[^A-Za-z ]", " ", line).split())


def _ends_with(name: str, tail: str) -> bool:
    """Whether `name` already ends in the words of `tail`, ignoring case."""
    name_words = cardname.words(name)
    tail_words = cardname.words(tail)
    return bool(tail_words) and name_words[-len(tail_words):] == tail_words


def _starts_with(name: str, head: str) -> bool:
    name_words = cardname.words(name)
    head_words = cardname.words(head)
    return bool(head_words) and name_words[: len(head_words)] == head_words


# Lines inside the attack block that are structurally not attack names: the
# stat row beneath it, the ability/power headers above it, and the opening
# words of rules text. Rules text is the bulk of what the band picks up, and
# most of it is caught by the word-count limit instead - this catches the short
# lines that would otherwise slip through looking like names.
_NOT_AN_ATTACK_RE = re.compile(
    r"(weakness|resistance|retreat|ability|poke\s*-?\s*(power|body)|"
    r"this attack|your opponent|flip a coin|damage counter|"
    r"during your next turn|search your deck|put \d)",
    re.IGNORECASE,
)


def _clean_attack_line(line: str) -> str | None:
    """Reduce one line of the attack block to an attack name, or None.

    An attack's row is "<energy icons> <name> <damage>": the icons OCR as
    stray single characters on the left and the damage is digits (often with a
    "+", "x" or "×") on the right, so both ends are trimmed and what survives
    in the middle is the name. Rules text, which shares the band, is rejected
    on length - attack names run one to four words, sentences do not.
    """
    if _NOT_AN_ATTACK_RE.search(line):
        return None
    # Damage is set hard right on the attack's own row.
    text = re.sub(r"[\s\d+x×*]+$", "", line)
    letters = re.sub(r"[^A-Za-z' \-]", " ", text)
    # Single characters are energy-cost icons misread as letters, never part of
    # an attack name.
    words = [w for w in letters.split() if len(w) > 1]
    # The rest of the cost survives as short runs of one repeated letter -
    # "WWWW" for four Water energy, "GG" for two Grass - sitting hard left of
    # the name. No attack name begins with a word that is one letter repeated.
    while words and len(set(words[0].lower())) == 1:
        words.pop(0)
    if not 1 <= len(words) <= 4:
        return None
    name = " ".join(words)
    if len(name) < 4 or all(w.lower() in _NAME_STOPWORDS for w in words):
        return None
    # Reject lines that were mostly digits or symbols before cleaning.
    if len(name) / max(len(line), 1) < 0.4:
        return None
    return name


def _extract_attack_names(lines: list[str], limit: int = 4) -> list[str]:
    """Plausible attack names in the attack block, in printed order.

    Kept as a list rather than resolved to one: a card has one or two attacks
    plus possibly an ability, all of which are equally good evidence, and the
    ranker only needs *any* of them to match a candidate's attack list.
    """
    names: list[str] = []
    seen: set[str] = set()
    for line in lines:
        cleaned = _clean_attack_line(line)
        if cleaned is None:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(cleaned)
        if len(names) >= limit:
            break
    return names


def _extract_name_candidates(lines: list[str], limit: int = 6) -> list[str]:
    """Every plausible name in the read text, most specific first.

    Two things happen here. Each line is reduced to a bare name as before, and
    lines holding nothing but a modifier - "Galarian" above the name, "VMAX"
    below it, both printed in fonts that make OCR break them out on their own -
    are stitched back onto the neighbouring name line.

    The stitched reading is emitted *ahead* of the bare one because it is the
    more specific identity, and that ordering is the whole point: the caller
    resolves these against the real card-name index (carddb.resolve_name),
    which can only ever pick a name it was offered. Without the stitch, a
    Galarian Darmanitan VMAX has no way to be identified as anything but a
    Darmanitan - a different card, in a different set, at a different price.

    Both forms are kept, never just the stitched one: a modifier line can be
    stray text, and the ranker downstream is better placed to decide.
    """
    # (kind, value) per line, in reading order. Modifier lines are kept as
    # neighbours to stitch with; everything else is dropped as before.
    parsed: list[tuple[str, str]] = []
    for line in lines:
        # Modifiers are classified *before* the name cleaner gets a look: a bare
        # "GALARIAN" is a perfectly well-formed name as far as the cleaner is
        # concerned - one long word, no stopwords - and would be banked as a
        # name of its own, leaving nothing to stitch onto the line below it.
        kind = cardname.modifier_kind(line)
        if kind is not None:
            parsed.append((kind, _letters_only(line)))
            continue
        cleaned = _clean_name_line(line)
        if cleaned is not None:
            parsed.append(("name", cleaned))

    candidates: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        key = value.lower()
        if len(value) >= 3 and key not in seen:
            seen.add(key)
            candidates.append(value)

    for i, (kind, value) in enumerate(parsed):
        if kind != "name":
            continue
        previous = parsed[i - 1] if i > 0 else None
        following = parsed[i + 1] if i + 1 < len(parsed) else None
        # A modifier the name line already carries was read twice, once in each
        # font; stitching it on again would produce "Darmanitan VMAX VMAX".
        prefix = (
            previous[1]
            if previous and previous[0] == "prefix" and not _starts_with(value, previous[1])
            else ""
        )
        suffix = (
            following[1]
            if following and following[0] == "suffix" and not _ends_with(value, following[1])
            else ""
        )
        stitched = " ".join(part for part in (prefix, value, suffix) if part)
        if stitched != value:
            add(stitched)
        add(value)
        if len(candidates) >= limit:
            break
    return candidates[:limit]


def recognize_text(image: np.ndarray) -> TextReading:
    """Read the name band and collector number off a (warped) card.

    OCRs the two regions that matter first - smaller, less noisy crops than the
    whole card, so both faster and more accurate in the common case - and falls
    back to whole-card OCR if a region yields nothing usable.
    """
    if image is None or not _HAS_CV2:
        return TextReading(lines=[], name_candidates=[], number=None)

    name_lines = _ocr_lines(_preprocess_for_ocr(_crop_region(image, _NAME_BAND)))

    # The boxes are tried in order and the loop stops at the first one that
    # actually yields a number, so the common case (a modern card, number
    # bottom-left) still costs a single extra OCR pass.
    number_lines: list[str] = []
    numbers: list[str] = []
    for box in _NUMBER_BOXES:
        lines = _ocr_lines(_preprocess_for_ocr(_crop_region(image, box)))
        number_lines.extend(lines)
        numbers = _extract_numbers(number_lines)
        if numbers:
            break

    candidates = _extract_name_candidates(name_lines)
    # HP shares the name's row, so it is already in `name_lines` - reading it
    # costs nothing beyond the parse.
    hp = _extract_hp(name_lines)

    # The attack block earns its own OCR pass only when the collector number
    # could not be read. A number plus a set size is already an exact
    # identification and no attack name can improve on it; without one, the
    # attacks are the best remaining way to tell the Base Set Charizard from
    # the twenty other Charizards, all of which match the name equally well.
    attack_lines: list[str] = []
    attacks: list[str] = []
    if settings.enable_attack_ocr and not numbers:
        attack_lines = _ocr_lines(
            _preprocess_for_ocr(_crop_region(image, _ATTACK_BAND))
        )
        attacks = _extract_attack_names(attack_lines)

    if candidates and numbers:
        return _reading(name_lines + number_lines, candidates, numbers, hp, attacks)

    # Region OCR didn't get everything - fall back to whole-card OCR and fill
    # in whichever piece is missing. Run at the baseline size: this is a broad
    # sweep for text the fixed boxes missed, and OCR cost scales with pixel
    # count, so sweeping a 2x warp would double the price of every hard scan.
    fallback_lines = _ocr_lines(_baseline_size(image))
    if not candidates:
        candidates = _extract_name_candidates(fallback_lines)
    if not numbers:
        numbers = _extract_numbers(fallback_lines)
    if hp is None:
        hp = _extract_hp(fallback_lines)
    if not attacks:
        attacks = _extract_attack_names(fallback_lines)

    return _reading(
        name_lines + number_lines + attack_lines + fallback_lines,
        candidates,
        numbers,
        hp,
        attacks,
    )


def _reading(
    lines: list[str],
    candidates: list[str],
    numbers: list[str],
    hp: int | None = None,
    attacks: list[str] | None = None,
) -> TextReading:
    """Assemble a TextReading, deriving the set total from the best number."""
    number = numbers[0] if numbers else None
    set_total = int(number.split("/")[1]) if number else None
    return TextReading(
        lines=lines,
        name_candidates=candidates,
        number=number,
        set_total=set_total,
        number_candidates=numbers,
        hp=hp,
        attack_names=attacks or [],
    )


def compute_phash(image: np.ndarray | None) -> str | None:
    """Perceptual hash of the (ideally warped, upright) card image.

    Used to visually re-rank text-search candidates. Returns None if the
    imagehash/Pillow stack isn't available or no image was given, in which
    case callers should skip visual matching entirely.
    """
    if not _HAS_IMAGEHASH or image is None or image.size == 0:
        return None
    try:
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB) if _HAS_CV2 else image[..., ::-1]
        pil_image = Image.fromarray(rgb)
        return str(imagehash.phash(pil_image))
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# 2b. Energy type from the frame colour
# --------------------------------------------------------------------------- #
# Approximate frame colours (sRGB) of each energy type. These are the band
# around the artwork window, not the energy symbol itself, which is too small
# and too often foiled to sample.
_TYPE_REFERENCE_RGB: dict[str, tuple[int, int, int]] = {
    "Grass": (124, 168, 78),
    "Fire": (214, 96, 54),
    "Water": (86, 146, 196),
    "Lightning": (236, 200, 72),
    "Psychic": (156, 110, 172),
    "Fighting": (186, 124, 72),
    "Darkness": (74, 86, 98),
    "Metal": (162, 168, 174),
    "Dragon": (178, 152, 80),
    "Fairy": (228, 146, 186),
    "Colorless": (222, 214, 196),
}

# CIE Lab distance within which a type counts as plausible alongside the best
# one. Calibrated against the reference table itself: its closest pair
# (Fighting and Dragon) is 19.6 apart, so a margin below that keeps a colour
# sitting squarely on one reference from dragging in even its nearest
# neighbour, while a colour that has drifted a third of the way toward another
# - which is what glare and holo foiling do - keeps both in play.
_TYPE_MARGIN = 14.0

# Lab distance at which the frame colour resembles no type at all, and the
# spread (mean absolute deviation of the sampled pixels) at which the sample is
# too inconsistent to be one flat colour - a full-art card, heavy glare, or a
# crop that landed on artwork. Both drive confidence to zero.
_TYPE_MAX_DISTANCE = 45.0
_TYPE_MAX_SPREAD = 40.0


def _srgb_to_lab(rgb: Sequence[float]) -> np.ndarray:
    """Convert one sRGB triple (0-255) to CIE Lab under a D65 white point.

    Done in numpy rather than through cv2 so the reference table can be built
    at import time whether or not OpenCV is installed, and in true Lab units
    (L 0-100) rather than cv2's rescaled bytes, so distances can be reasoned
    about against published perceptual thresholds.
    """
    c = np.asarray(rgb, dtype=np.float64) / 255.0
    linear = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    matrix = np.array(
        [
            [0.4124, 0.3576, 0.1805],
            [0.2126, 0.7152, 0.0722],
            [0.0193, 0.1192, 0.9505],
        ]
    )
    xyz = matrix @ linear / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (841 / 108) * xyz + 4 / 29)
    return np.array(
        [116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])]
    )


_TYPE_LAB: dict[str, np.ndarray] = {
    name: _srgb_to_lab(rgb) for name, rgb in _TYPE_REFERENCE_RGB.items()
}


def detect_type(image: np.ndarray | None) -> TypeReading:
    """Read the card's energy type from the colour of its frame.

    The weakest of the identification signals by some distance, and treated as
    such. Holo foiling, glare, coloured lighting and full-art printings all
    corrupt the frame colour, and several types are near-neighbours in colour
    to begin with - so this returns every type within a perceptual margin of
    the sampled colour, plus a confidence that collapses when the sample is
    either unlike any type or too varied to be a flat frame at all.

    Callers should treat an empty or low-confidence reading as "no information"
    and rank without it, which is what the scan route does.
    """
    if not _HAS_CV2 or image is None or image.size == 0:
        return TypeReading(types=[], confidence=0.0)

    card = _baseline_size(image)
    patches = [_crop_region(card, box) for box in _TYPE_SAMPLE_BOXES]
    usable = [p.reshape(-1, 3) for p in patches if p.size]
    if not usable:
        return TypeReading(types=[], confidence=0.0)

    pixels = np.concatenate(usable, axis=0).astype(np.float64)
    if len(pixels) < 200:  # too little frame to read a colour off
        return TypeReading(types=[], confidence=0.0)

    # Median, not mean: the patches still contain text, icons and the odd
    # specular highlight, and a median ignores them where a mean would be
    # dragged toward whatever is brightest.
    median_bgr = np.median(pixels, axis=0)
    spread = float(np.mean(np.abs(pixels - median_bgr)))
    lab = _srgb_to_lab(median_bgr[::-1])  # BGR -> RGB

    distances = {
        name: float(np.linalg.norm(lab - reference))
        for name, reference in _TYPE_LAB.items()
    }
    ranked = sorted(distances.items(), key=lambda kv: kv[1])
    best_distance = ranked[0][1]
    plausible = [name for name, d in ranked if d <= best_distance + _TYPE_MARGIN]

    confidence = max(0.0, 1.0 - best_distance / _TYPE_MAX_DISTANCE) * max(
        0.0, 1.0 - spread / _TYPE_MAX_SPREAD
    )
    return TypeReading(types=plausible, confidence=round(confidence, 3))


# --------------------------------------------------------------------------- #
# 2c. Print variant: foil finish and 1st Edition
# --------------------------------------------------------------------------- #
# Local texture ("roughness") thresholds that separate a flat-printed frame
# from a foiled one. Foil - holo pattern behind the artwork on classic rares,
# a full sheet of it on reverse-holos and modern secret rares - scatters light
# unevenly at a scale finer than print texture or JPEG noise, so it reads as
# high local variance even after the median blur that would flatten a matte
# card's own compression artifacts. There is deliberately a gap between the
# two thresholds: a sample landing in it is genuinely ambiguous (a dim photo
# flattens foil sparkle; strong directional glare roughens a matte card) and
# reporting nothing is more honest than a coin flip.
_FOIL_ROUGHNESS_HIGH = 14.0
_FOIL_ROUGHNESS_LOW = 6.0
# Roughness at which confidence saturates in either direction, i.e. how far
# past its threshold a sample has to be before the reading is reported as sure.
_FOIL_ROUGHNESS_SPAN = 10.0


@dataclass
class FinishReading:
    """Whether the card's surface is foiled, read from frame texture.

    `is_foil` is None - not False - when the sample was too ambiguous to call
    either way; callers must treat that the same as "no information", not as
    "not foil". This is the shakiest signal in the pipeline: it decides between
    two prices TCGdex already lists for the same card, not between candidates,
    so a wrong call costs an inaccurate price estimate, not a wrong card.
    """

    is_foil: bool | None
    confidence: float


def _local_roughness(patch: np.ndarray) -> float | None:
    """Mean local variation of a BGR patch, or None if it's too small to judge.

    Downsamples to a coarse grid first and measures neighbour-to-neighbour
    difference on *that*, rather than raw pixel variance - a matte card's own
    print texture and sensor noise are pixel-scale and would otherwise be
    indistinguishable from foil sparkle, which is a genuinely coarser pattern.
    """
    if patch.size == 0:
        return None
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY).astype(np.float64)
    h, w = gray.shape
    grid = (max(1, h // 6), max(1, w // 6))
    if grid[0] < 3 or grid[1] < 3:
        return None
    small = cv2.resize(gray, (grid[1], grid[0]), interpolation=cv2.INTER_AREA)
    dx = np.abs(np.diff(small, axis=1))
    dy = np.abs(np.diff(small, axis=0))
    return float((dx.sum() + dy.sum()) / (dx.size + dy.size))


def detect_finish(image: np.ndarray | None) -> FinishReading:
    """Read whether the card is foiled from texture in the frame patches.

    Reuses the same frame-only patches as detect_type() - the border between
    the yellow outer edge and the artwork window - deliberately excluding the
    artwork itself, since photographic detail there would read as "rough"
    regardless of finish and swamp the signal this is looking for.
    """
    if not _HAS_CV2 or image is None or image.size == 0:
        return FinishReading(is_foil=None, confidence=0.0)

    card = _baseline_size(image)
    scores = [
        r
        for r in (
            _local_roughness(_crop_region(card, box)) for box in _TYPE_SAMPLE_BOXES
        )
        if r is not None
    ]
    if not scores:
        return FinishReading(is_foil=None, confidence=0.0)

    roughness = float(np.median(scores))
    if roughness >= _FOIL_ROUGHNESS_HIGH:
        confidence = min(1.0, (roughness - _FOIL_ROUGHNESS_HIGH) / _FOIL_ROUGHNESS_SPAN)
        return FinishReading(is_foil=True, confidence=round(confidence, 3))
    if roughness <= _FOIL_ROUGHNESS_LOW:
        confidence = min(1.0, (_FOIL_ROUGHNESS_LOW - roughness) / _FOIL_ROUGHNESS_SPAN)
        return FinishReading(is_foil=False, confidence=round(confidence, 3))
    return FinishReading(is_foil=None, confidence=0.0)


# 1st Edition stamps read cleanly as the word "EDITION" - a real dictionary
# word in a fairly plain typeface - even when the stylised leading "1st" next
# to it is misread as noise, so the word alone is the anchor and a leading
# digit-like token is only corroboration, never required on its own.
_FIRST_EDITION_RE = re.compile(r"1\s*st\s*edition|\bedition\b", re.IGNORECASE)


def detect_first_edition(image: np.ndarray | None) -> bool | None:
    """Whether the WotC-era "1st Edition" stamp is present.

    Returns None, not False, when OCR is unavailable - "not read" is not the
    same claim as "confirmed absent", and only the caller knows whether that
    distinction matters (it does: a None must never suppress the unlimited-
    tier price the way a confirmed False safely could).

    Modern cards simply have nothing printed in this crop, so this reads as
    "not found" for the overwhelming majority of scans by construction, not by
    any special-casing here.
    """
    if not settings.enable_first_edition_ocr or image is None:
        return None
    lines = _ocr_lines(_preprocess_for_ocr(_crop_region(_baseline_size(image), _FIRST_EDITION_BAND)))
    if not lines:
        return None
    text = " ".join(lines)
    return bool(_FIRST_EDITION_RE.search(text))


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
    # Graded at the baseline size regardless of how large the warp was: the
    # sharpness and edge-noise thresholds below are absolute, so letting the
    # input resolution vary would quietly change everyone's condition grades.
    image = _baseline_size(image)
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
