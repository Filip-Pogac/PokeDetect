/**
 * Pure frame-analysis helpers for camera auto-capture.
 *
 * Deliberately free of React and DOM APIs so they can be unit-tested against
 * synthetic buffers. Everything operates on an 8-bit luma plane
 * (one byte per pixel, row-major).
 */

// --- Tunable thresholds -----------------------------------------------------
// Starting values; these want tuning against real devices and lighting.

/** Max mean luma difference between consecutive frames to count as "steady".
 *  Sensor noise and auto-exposure drift keep a perfectly still handheld frame
 *  around 1-2 on a 0-255 scale, so this sits just above that. */
export const MOTION_MAX = 2.5;

/** Min contrast-normalized focus score for a frame to count as "in focus".
 *  On synthetic card crops, sharp images score ~2.0 and blurred ones <=0.31,
 *  in both bright and dim light, so this sits in the gap. */
export const SHARPNESS_MIN = 0.55;

/** Min fraction of edge pixels inside the framing guide for something
 *  card-like to be considered present. A blank surface lands near zero; a
 *  printed card fills the guide with art and borders. */
export const EDGE_DENSITY_MIN = 0.04;

/** Consecutive passing samples required before firing (~600ms at 8fps). */
export const HOLD_SAMPLES = 5;

/** Gradient magnitude above which a pixel counts as an edge. */
const EDGE_MAGNITUDE = 36;

export interface Rect {
  /** All values are fractions of width/height, 0-1. */
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/** The on-screen framing guide (`.scanner-frame` is `inset: 12% 22%`). */
export const GUIDE_RECT: Rect = { x0: 0.22, y0: 0.12, x1: 0.78, y1: 0.88 };

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

export interface FrameMetrics {
  motion: number;
  sharpness: number;
  edges: number;
}

/** Whether a frame passes every gate and so counts toward the hold streak. */
export function isFrameReady(m: FrameMetrics): boolean {
  return (
    m.motion <= MOTION_MAX &&
    m.sharpness >= SHARPNESS_MIN &&
    m.edges >= EDGE_DENSITY_MIN
  );
}
