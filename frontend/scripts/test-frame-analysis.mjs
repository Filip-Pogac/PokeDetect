import {
  toLuma, motionScore, sharpnessScore, edgeDensity, isFrameReady,
  MOTION_MAX, SHARPNESS_MIN, EDGE_DENSITY_MIN, GUIDE_RECT,
} from "../.frame-analysis.build.mjs";

let failures = 0;
const check = (name, cond, detail = "") => {
  console.log(`${cond ? "  ok  " : "FAIL  "} ${name}${detail ? "  " + detail : ""}`);
  if (!cond) failures++;
};

const W = 160, H = 120;

// --- builders ---------------------------------------------------------------
const flat = (v) => { const a = new Uint8Array(W * H); a.fill(v); return a; };

const checker = (cell) => {
  const a = new Uint8Array(W * H);
  for (let y = 0; y < H; y++)
    for (let x = 0; x < W; x++)
      a[y * W + x] = (Math.floor(x / cell) + Math.floor(y / cell)) % 2 ? 235 : 20;
  return a;
};

const boxBlur = (src, w, h, r = 2) => {
  const out = new Uint8Array(src.length);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    let s = 0, n = 0;
    for (let dy = -r; dy <= r; dy++) for (let dx = -r; dx <= r; dx++) {
      const yy = y + dy, xx = x + dx;
      if (yy < 0 || yy >= h || xx < 0 || xx >= w) continue;
      s += src[yy * w + xx]; n++;
    }
    out[y * w + x] = s / n;
  }
  return out;
};

const shifted = (src, w, h, dx) => {
  const out = new Uint8Array(src.length);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++)
    out[y * w + x] = src[y * w + Math.min(w - 1, Math.max(0, x - dx))];
  return out;
};

// A "card": bright rect with printed detail, inside the guide region, on dark bg.
const cardLike = () => {
  const a = flat(18);
  const x0 = Math.floor(GUIDE_RECT.x0 * W), x1 = Math.floor(GUIDE_RECT.x1 * W);
  const y0 = Math.floor(GUIDE_RECT.y0 * H), y1 = Math.floor(GUIDE_RECT.y1 * H);
  for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) a[y * W + x] = 238;
  // printed art + text lines
  for (let y = y0 + 8; y < y0 + 40; y++) for (let x = x0 + 6; x < x1 - 6; x++)
    a[y * W + x] = (x + y) % 3 ? 70 : 200;
  for (let ty = y0 + 50; ty < y1 - 8; ty += 6)
    for (let y = ty; y < ty + 2; y++) for (let x = x0 + 8; x < x1 - 10; x++)
      a[y * W + x] = 40;
  return a;
};

console.log("\n--- toLuma ---");
const rgba = new Uint8ClampedArray([255,255,255,255, 0,0,0,255, 255,0,0,255]);
const luma = toLuma(rgba);
check("white -> ~255", luma[0] === 255, `got ${luma[0]}`);
check("black -> 0", luma[1] === 0, `got ${luma[1]}`);
check("red -> ~76", Math.abs(luma[2] - 76) <= 1, `got ${luma[2]}`);

console.log("\n--- motionScore ---");
const base = checker(8);
check("no baseline -> Infinity", motionScore(null, base) === Infinity);
check("identical frames -> 0", motionScore(base, base) === 0);
const m1 = motionScore(base, shifted(base, W, H, 1));
const m8 = motionScore(base, shifted(base, W, H, 8));
check("1px shift exceeds MOTION_MAX", m1 > MOTION_MAX, `motion=${m1.toFixed(1)}`);
check("8px shift is larger still", m8 > m1, `${m8.toFixed(1)} > ${m1.toFixed(1)}`);
const jitter = base.map((v) => Math.min(255, Math.max(0, v + (Math.random() * 3 - 1.5))));
check("sensor-noise jitter stays under MOTION_MAX",
  motionScore(base, Uint8Array.from(jitter)) <= MOTION_MAX,
  `motion=${motionScore(base, Uint8Array.from(jitter)).toFixed(2)}`);

console.log("\n--- sharpnessScore (contrast-normalized) ---");
// A realistic card face rather than a pathological checkerboard: light stock,
// an art block, and rows of small text.
const S = 200;
const cardFace = () => {
  const a = new Uint8Array(S * S); a.fill(232);
  for (let y = 10; y < 90; y++) for (let x = 12; x < 188; x++)
    a[y * S + x] = (x * 7 + y * 3) % 23 < 11 ? 96 : 150;
  for (let ty = 104; ty < 190; ty += 9)
    for (let y = ty; y < ty + 3; y++) for (let x = 16; x < 184; x++) a[y * S + x] = 52;
  return a;
};
const face = cardFace();
const faceDim = Uint8Array.from(face, (v) => 128 + (v - 128) * 0.35); // dim lighting
const sSharp = sharpnessScore(face, S, S);
const sSharpDim = sharpnessScore(faceDim, S, S);
const sBlur = sharpnessScore(boxBlur(face, S, S, 2), S, S);
const sBlurDim = sharpnessScore(boxBlur(faceDim, S, S, 2), S, S);
const sFlat = sharpnessScore(flat(128), W, H);

check("sharp card passes SHARPNESS_MIN", sSharp >= SHARPNESS_MIN, `s=${sSharp.toFixed(2)}`);
check("blurred card fails SHARPNESS_MIN", sBlur < SHARPNESS_MIN, `s=${sBlur.toFixed(2)}`);
check("sharp DIM card still passes (lighting-invariant)",
  sSharpDim >= SHARPNESS_MIN, `s=${sSharpDim.toFixed(2)}`);
check("blurred DIM card still fails", sBlurDim < SHARPNESS_MIN, `s=${sBlurDim.toFixed(2)}`);
check("bright vs dim sharp scores agree within 10%",
  Math.abs(sSharp - sSharpDim) / sSharp < 0.1,
  `${sSharp.toFixed(2)} vs ${sSharpDim.toFixed(2)}`);
check("sharp/blur separation is >3x", sSharp > sBlur * 3,
  `${sSharp.toFixed(2)} vs ${sBlur.toFixed(2)}`);
check("flat field -> 0", sFlat === 0, `s=${sFlat}`);
check("degenerate size -> 0", sharpnessScore(new Uint8Array(4), 2, 2) === 0);

console.log("\n--- edgeDensity ---");
const eFlat = edgeDensity(flat(128), W, H);
const eCard = edgeDensity(cardLike(), W, H);
check("blank surface below EDGE_DENSITY_MIN", eFlat < EDGE_DENSITY_MIN, `e=${eFlat.toFixed(4)}`);
check("card-like passes EDGE_DENSITY_MIN", eCard >= EDGE_DENSITY_MIN, `e=${eCard.toFixed(3)}`);
check("degenerate rect -> 0", edgeDensity(cardLike(), W, H, {x0:0.5,y0:0.5,x1:0.5,y1:0.5}) === 0);

console.log("\n--- isFrameReady (the real gate combination) ---");
check("steady + sharp + card -> READY",
  isFrameReady({ motion: 1.0, sharpness: sSharp, edges: eCard }) === true);
check("steady + sharp + EMPTY DESK -> blocked (presence gate)",
  isFrameReady({ motion: 1.0, sharpness: sSharp, edges: eFlat }) === false);
check("moving -> blocked", isFrameReady({ motion: 9, sharpness: sSharp, edges: eCard }) === false);
check("blurry -> blocked", isFrameReady({ motion: 1.0, sharpness: sBlur, edges: eCard }) === false);

console.log(failures ? `\n${failures} FAILURE(S)\n` : "\nALL METRIC TESTS PASSED\n");
process.exit(failures ? 1 : 0);
