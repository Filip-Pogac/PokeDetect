/**
 * Pure frame-analysis helpers for camera auto-capture.
 *
 * Deliberately free of React and DOM APIs so they can be unit-tested against
 * synthetic buffers. Everything operates on an 8-bit luma plane
 * (one byte per pixel, row-major).
 */

// --- Tunable thresholds -----------------------------------------------------
// Starting values; these want tuning against real devices and lighting.

/** Max frame-to-frame change of the pooled frame (see downsample), as a
 *  fraction of the scene's own contrast.
 *
 *  Tightened from 0.18 now that framing is gated separately: with the card
 *  guaranteed to fill the guide, the capture is worth holding to a steadier
 *  standard, and residual movement is the remaining source of smear.
 *
 *  Absolute luma difference does not work as a steadiness test: it scales with
 *  how busy the picture is, so the same small hand tremor reads as ~1 over a
 *  plain desk and well into double digits over a holo card's artwork. A single
 *  absolute threshold therefore demands near-perfect stillness of exactly the
 *  subject this scanner exists for. Dividing by the frame's own contrast (the
 *  same trick sharpnessScore uses for lighting) makes the number scale-free:
 *  it answers "how much of this picture changed", not "how detailed is it". */
export const MOTION_RATIO_MAX = 0.12;

/** Contrast floor for that ratio. Below it the scene has nothing to measure
 *  against and the division would amplify sensor noise into false motion. */
const MIN_CONTRAST = 8;

/** Min contrast-normalized focus score for a frame to count as "in focus".
 *  On synthetic card crops, sharp images score ~2.0 and blurred ones <=0.31,
 *  in both bright and dim light, so this sits in the gap - kept nearer the blurred
 *  end of it, since this is the one gate that must not be loosened away: it is
 *  what keeps the captured frame legible.
 *
 *  Raised above the original 0.55: the collector number is the smallest print
 *  on the card and the first thing softness costs, and a misread number picks a
 *  confidently wrong printing rather than failing visibly. */
export const SHARPNESS_MIN = 0.6;

/** Min fraction of edge pixels inside the framing guide for something
 *  card-like to be considered present. A blank surface lands near zero; a
 *  printed card fills the guide with art and borders. */
export const EDGE_DENSITY_MIN = 0.03;

/** Consecutive passing samples required before firing (~500ms at 8fps). */
export const HOLD_SAMPLES = 4;

/** How long a card can sit in frame before the gates are fully relaxed.
 *
 *  Someone who has been aiming at a card for six seconds is not going to hold
 *  it any better on the seventh - at that point a slightly soft capture beats
 *  a scanner that never fires. The presence gate never relaxes, so this ramp
 *  can only ever loosen how still and how sharp, never fire at nothing. */
export const PATIENCE_MS = 6000;

/** Gradient magnitude above which a pixel counts as an edge. */
const EDGE_MAGNITUDE = 36;

export interface Rect {
  /** All values are fractions of width/height, 0-1. */
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/**
 * The drawn framing guide, in stage coordinates: 84% of the stage height at the
 * card's own 63:88 ratio. What the user sees is exactly what is measured.
 */
export const GUIDE_RECT: Rect = { x0: 0.27, y0: 0.08, x1: 0.73, y1: 0.92 };

/**
 * How much of the guide the card has to span before it counts as framed.
 *
 * Measured as the extent of the picture's detail relative to the guide, on
 * both axes - not as an edge count. An earlier attempt compared density inside
 * the guide against density outside it, which turned out to measure the wrong
 * thing: on a plain table the background contributes almost no edges, so a
 * card a third of the intended size still scored as "concentrated in the
 * guide". Extent answers the question that was actually being asked.
 *
 * Why it is worth gating at all: the warp is clamped at 1x below
 * (vision._warp_scale), so a card that occupies less of the frame is not
 * rectified smaller - it is *upscaled* to 630x880, inventing no detail. The
 * collector number, about 2mm of print, is what pays for that first, and a
 * misread number picks a confidently wrong printing rather than failing
 * visibly.
 */
export const FRAMING_COVERAGE_MIN = 0.78;

/** How far the content's centre may sit from the guide's, as a fraction of the
 *  frame. Generous: a card that spans the guide is already well placed, and
 *  this only rejects one pushed toward an edge, where the warp starts clipping
 *  it. */
export const FRAMING_DRIFT_MAX = 0.1;

/** Fraction of the busiest row/column an edge profile must reach to count as
 *  containing the card. Relative to the frame's own maximum, so it does not
 *  care whether the artwork is a full-art holo or a plain Trainer card. */
const PROFILE_THRESHOLD = 0.25;

/**
 * Convert RGBA pixel data (as returned by `ctx.getImageData`) to an 8-bit luma
 * plane using the Rec. 601 weights.
 */
export function toLuma(rgba: Uint8ClampedArray | Uint8Array): Uint8Array {
  const out = new Uint8Array(rgba.length >> 2);
  for (let i = 0, p = 0; p < out.length; i += 4, p++) {
    out[p] = (rgba[i] * 299 + rgba[i + 1] * 587 + rgba[i + 2] * 114) / 1000;
  }
  return out;
}

/**
 * Mean absolute difference between two luma planes, 0-255.
 * Low means the camera is being held still.
 */
export function motionScore(prev: Uint8Array | null, next: Uint8Array): number {
  if (!prev || prev.length !== next.length || next.length === 0) {
    return Number.POSITIVE_INFINITY; // no baseline yet - never "steady"
  }
  let sum = 0;
  for (let i = 0; i < next.length; i++) {
    sum += Math.abs(next[i] - prev[i]);
  }
  return sum / next.length;
}

/**
 * Contrast-normalized focus score: variance of a 3x3 Laplacian response
 * divided by the crop's own intensity variance.
 *
 * The normalization matters. Raw Laplacian variance (what the backend uses for
 * condition grading, where exposure is not a variable) scales with scene
 * contrast as well as focus, so a sharp frame in dim light and a blurred frame
 * in bright light land in the same range and no fixed threshold separates them.
 * Dividing by intensity variance measures what fraction of the scene's energy
 * is high-frequency, which holds steady across lighting: on synthetic card
 * crops, sharp scores ~2.0 whether bright or dim, blurred <=0.31 either way.
 *
 * Must be given a NATIVE-resolution crop: downscaling destroys the very
 * high-frequency detail that distinguishes sharp from blurry, so running this
 * on a thumbnail reports nonsense.
 */
export function sharpnessScore(luma: Uint8Array, width: number, height: number): number {
  if (width < 3 || height < 3) return 0;
  const n = (width - 2) * (height - 2);
  let lapSum = 0;
  let lapSumSq = 0;
  let intSum = 0;
  let intSumSq = 0;
  for (let y = 1; y < height - 1; y++) {
    for (let x = 1; x < width - 1; x++) {
      const i = y * width + x;
      // Kernel: 0 1 0 / 1 -4 1 / 0 1 0
      const v =
        luma[i - width] + luma[i + width] + luma[i - 1] + luma[i + 1] - 4 * luma[i];
      lapSum += v;
      lapSumSq += v * v;
      const p = luma[i];
      intSum += p;
      intSumSq += p * p;
    }
  }
  const lapMean = lapSum / n;
  const lapVar = lapSumSq / n - lapMean * lapMean;
  const intMean = intSum / n;
  const intVar = intSumSq / n - intMean * intMean;
  // A flat field has no contrast to normalize against and no focus to measure.
  if (intVar < 1) return 0;
  return lapVar / intVar;
}

/**
 * Fraction of pixels within `rect` whose gradient magnitude exceeds
 * EDGE_MAGNITUDE. Used as a presence gate: an empty desk held steady is also
 * "steady and sharp", so without this auto-capture happily fires at nothing.
 */
export function edgeDensity(
  luma: Uint8Array,
  width: number,
  height: number,
  rect: Rect = GUIDE_RECT,
): number {
  const x0 = Math.max(1, Math.floor(rect.x0 * width));
  const y0 = Math.max(1, Math.floor(rect.y0 * height));
  const x1 = Math.min(width - 1, Math.ceil(rect.x1 * width));
  const y1 = Math.min(height - 1, Math.ceil(rect.y1 * height));
  if (x1 <= x0 || y1 <= y0) return 0;

  let edges = 0;
  let total = 0;
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      const i = y * width + x;
      const gx = luma[i + 1] - luma[i - 1];
      const gy = luma[i + width] - luma[i - width];
      if (Math.abs(gx) + Math.abs(gy) >= EDGE_MAGNITUDE) edges++;
      total++;
    }
  }
  return total === 0 ? 0 : edges / total;
}

/**
 * Average-pool a luma plane down by `factor` in each axis.
 *
 * Used to make the motion test translation-tolerant. Comparing two frames
 * pixel-for-pixel measures how far the picture *moved*, and on printed artwork
 * a one-pixel hand tremor already rewrites most pixel values - which is why an
 * unpooled difference demands a tripod. What actually ruins a scan is camera
 * movement large enough to smear the card, and that survives pooling while a
 * tremor averages away.
 */
export function downsample(
  luma: Uint8Array,
  width: number,
  height: number,
  factor: number,
): { luma: Uint8Array; width: number; height: number } {
  const w = Math.max(1, Math.floor(width / factor));
  const h = Math.max(1, Math.floor(height / factor));
  const out = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let sum = 0;
      let n = 0;
      for (let dy = 0; dy < factor; dy++) {
        const sy = y * factor + dy;
        if (sy >= height) break;
        for (let dx = 0; dx < factor; dx++) {
          const sx = x * factor + dx;
          if (sx >= width) break;
          sum += luma[sy * width + sx];
          n++;
        }
      }
      out[y * w + x] = n === 0 ? 0 : sum / n;
    }
  }
  return { luma: out, width: w, height: h };
}

/** Pooling factor applied before the motion comparison. */
export const MOTION_POOL = 4;

/** RMS deviation from the mean - how much contrast the frame carries. */
export function contrastScore(luma: Uint8Array): number {
  if (luma.length === 0) return 0;
  let sum = 0;
  let sumSq = 0;
  for (let i = 0; i < luma.length; i++) {
    sum += luma[i];
    sumSq += luma[i] * luma[i];
  }
  const mean = sum / luma.length;
  return Math.sqrt(Math.max(0, sumSq / luma.length - mean * mean));
}

/** Frame-to-frame change relative to the frame's own contrast. */
export function relativeMotion(motion: number, contrast: number): number {
  if (!Number.isFinite(motion)) return motion;
  return motion / Math.max(contrast, MIN_CONTRAST);
}

/** How far the gates have been relaxed, 0 (strict) to 1 (fully patient). */
export function relaxation(elapsedMs: number): number {
  if (!(elapsedMs > 0)) return 0;
  return Math.min(1, elapsedMs / PATIENCE_MS);
}

export interface Framing {
  /** Span of the content relative to the guide, worst of the two axes. 1.0
   *  means the card spans the guide exactly; below 1 it sits inside it. */
  coverage: number;
  /** How far the content's centre is from the guide's, in frame fractions. */
  drift: number;
}

/**
 * Where the picture's detail actually lies, as a normalized box.
 *
 * Row and column edge profiles are thresholded against their own maximum, so
 * the measure is independent of how busy the card is - what matters is where
 * the detail stops, not how much of it there is. Returns null for a frame with
 * no detail at all.
 */
export function contentBounds(
  luma: Uint8Array,
  width: number,
  height: number,
): Rect | null {
  const rows = new Float32Array(height);
  const cols = new Float32Array(width);
  for (let y = 1; y < height - 1; y++) {
    for (let x = 1; x < width - 1; x++) {
      const i = y * width + x;
      const gx = luma[i + 1] - luma[i - 1];
      const gy = luma[i + width] - luma[i - width];
      if (Math.abs(gx) + Math.abs(gy) >= EDGE_MAGNITUDE) {
        rows[y] += 1;
        cols[x] += 1;
      }
    }
  }

  const span = (profile: Float32Array): [number, number] | null => {
    let max = 0;
    for (const v of profile) if (v > max) max = v;
    if (max <= 0) return null;
    const cut = max * PROFILE_THRESHOLD;
    let first = -1;
    let last = -1;
    for (let i = 0; i < profile.length; i++) {
      if (profile[i] >= cut) {
        if (first < 0) first = i;
        last = i;
      }
    }
    return first < 0 ? null : [first, last];
  };

  const vertical = span(rows);
  const horizontal = span(cols);
  if (!vertical || !horizontal) return null;
  return {
    x0: horizontal[0] / width,
    y0: vertical[0] / height,
    x1: (horizontal[1] + 1) / width,
    y1: (vertical[1] + 1) / height,
  };
}

/** How well the detected content fills and centres on the drawn guide. */
export function framing(luma: Uint8Array, width: number, height: number): Framing {
  const bounds = contentBounds(luma, width, height);
  if (!bounds) return { coverage: 0, drift: 1 };

  const guideW = GUIDE_RECT.x1 - GUIDE_RECT.x0;
  const guideH = GUIDE_RECT.y1 - GUIDE_RECT.y0;
  // The worst axis decides: a card that spans the guide vertically but sits
  // half out of it horizontally is not framed.
  const coverage = Math.min(
    (bounds.x1 - bounds.x0) / guideW,
    (bounds.y1 - bounds.y0) / guideH,
  );
  const drift =
    Math.abs((bounds.x0 + bounds.x1) / 2 - (GUIDE_RECT.x0 + GUIDE_RECT.x1) / 2) +
    Math.abs((bounds.y0 + bounds.y1) / 2 - (GUIDE_RECT.y0 + GUIDE_RECT.y1) / 2);
  return { coverage, drift };
}

export interface FrameMetrics {
  /** Already divided through by contrast - see MOTION_RATIO_MAX. */
  motion: number;
  sharpness: number;
  edges: number;
  /** From framing(). Omitted by callers that do not check framing. */
  coverage?: number;
  drift?: number;
}

/**
 * Whether a frame passes every gate and so counts toward the hold streak.
 *
 * `relax` (0-1) widens the steadiness and focus gates for a card that has been
 * sitting in frame for a while. Presence is not relaxed: an empty desk is
 * never a card, however long it is pointed at.
 */
export function isFrameReady(m: FrameMetrics, relax = 0): boolean {
  const r = Math.min(1, Math.max(0, relax));
  // Framing does not relax. Patience exists so a shaky hand still gets a scan;
  // it must not hand the reader a card too small to read, because that failure
  // is silent - a plausible wrong printing rather than a visible retry.
  if (m.coverage !== undefined && m.coverage < FRAMING_COVERAGE_MIN) return false;
  if (m.drift !== undefined && m.drift > FRAMING_DRIFT_MAX) return false;
  return (
    m.motion <= MOTION_RATIO_MAX * (1 + 1.6 * r) &&
    // Focus barely gives: it is the difference between a card the backend can
    // read and one it cannot, so patience buys a wobblier shot, never a
    // blurred one. Fully relaxed this still sits above the range blurred
    // crops score in.
    m.sharpness >= SHARPNESS_MIN * (1 - 0.1 * r) &&
    m.edges >= EDGE_DENSITY_MIN
  );
}


/** A source rectangle in the video frame, in pixels. */
export interface CropRect {
  sx: number;
  sy: number;
  sw: number;
  sh: number;
}

/**
 * The centre crop of a `vw x vh` frame that `object-fit: cover` would show in a
 * box of the given aspect ratio.
 *
 * Both the capture and the frame analysis run through this, so "what the user
 * sees in the stage", "what gets sent to the scanner" and "what the auto-capture
 * gates measure" are the same pixels. Without it the analysis works on the full
 * sensor frame - typically 16:9 against a 4:3 stage - and the guide fractions
 * above would point at a region the user was never shown.
 */
export function coverCrop(vw: number, vh: number, aspect: number): CropRect {
  if (!(vw > 0) || !(vh > 0) || !(aspect > 0)) return { sx: 0, sy: 0, sw: vw, sh: vh };
  let sw = vw;
  let sh = vh;
  if (vw / vh > aspect) sw = vh * aspect; // frame wider than the box: trim sides
  else sh = vw / aspect; // frame taller: trim top and bottom
  return { sx: (vw - sw) / 2, sy: (vh - sh) / 2, sw, sh };
}
