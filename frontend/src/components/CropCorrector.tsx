import {
  useCallback,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type SyntheticEvent,
} from "react";
import "./CropCorrector.css";

export interface Corner {
  x: number; // 0-1 fraction of image width
  y: number; // 0-1 fraction of image height
}

interface Props {
  imageUrl: string;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: (corners: Corner[]) => void;
}

/** Starts on the framing guide the user was already aiming at
 *  (`.scanner-frame` is inset 12% / 22%). Order: TL, TR, BR, BL. */
const INITIAL: Corner[] = [
  { x: 0.22, y: 0.12 },
  { x: 0.78, y: 0.12 },
  { x: 0.78, y: 0.88 },
  { x: 0.22, y: 0.88 },
];

const LABELS = ["top-left", "top-right", "bottom-right", "bottom-left"];

/** Tallest the editor may get, in vh. Mirrored into the width cap above. */
const MAX_STAGE_VH = 60;

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

export function CropCorrector({ imageUrl, busy = false, onCancel, onConfirm }: Props) {
  const [corners, setCorners] = useState<Corner[]>(INITIAL);
  const [dragging, setDragging] = useState<number | null>(null);
  const [ratio, setRatio] = useState<number | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);

  // The corners are sent to the backend as fractions of the *image*, and the
  // backend multiplies them straight back up by the image's pixel size. So the
  // stage the handles are positioned in has to be exactly the box the image
  // content occupies — if the image were letterboxed inside a differently
  // shaped stage, every fraction would be off, the card would be warped from
  // the wrong region, and the OCR would read some other card entirely.
  // Matching the stage to the image's own aspect ratio removes the letterbox.
  const handleImageLoad = useCallback((e: SyntheticEvent<HTMLImageElement>) => {
    const { naturalWidth, naturalHeight } = e.currentTarget;
    if (naturalWidth > 0 && naturalHeight > 0) {
      setRatio(naturalWidth / naturalHeight);
    }
  }, []);

  // Height is capped by expressing the cap as a width, so the box keeps its
  // aspect ratio instead of being squashed by a max-height.
  const stageStyle = ratio
    ? { width: `min(100%, ${(ratio * MAX_STAGE_VH).toFixed(2)}vh)`, aspectRatio: `${ratio}` }
    : undefined;

  const moveCorner = useCallback((index: number, clientX: number, clientY: number) => {
    const stage = stageRef.current;
    if (!stage) return;
    const rect = stage.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const x = clamp01((clientX - rect.left) / rect.width);
    const y = clamp01((clientY - rect.top) / rect.height);
    setCorners((prev) => prev.map((c, i) => (i === index ? { x, y } : c)));
  }, []);

  // Pointer events cover mouse, touch and pen in one path. setPointerCapture
  // keeps move events coming to this handle even once the finger slides off it,
  // so no window-level listeners are needed.
  const handlePointerDown = useCallback(
    (index: number) => (e: ReactPointerEvent<HTMLButtonElement>) => {
      e.preventDefault();
      e.currentTarget.setPointerCapture(e.pointerId);
      setDragging(index);
      moveCorner(index, e.clientX, e.clientY);
    },
    [moveCorner],
  );

  const handlePointerMove = useCallback(
    (index: number) => (e: ReactPointerEvent<HTMLButtonElement>) => {
      if (dragging !== index) return;
      e.preventDefault();
      moveCorner(index, e.clientX, e.clientY);
    },
    [dragging, moveCorner],
  );

  const handlePointerUp = useCallback(
    (e: ReactPointerEvent<HTMLButtonElement>) => {
      if (e.currentTarget.hasPointerCapture(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId);
      }
      setDragging(null);
    },
    [],
  );

  // Keyboard nudging, so the editor isn't pointer-only.
  const handleKeyDown = useCallback(
    (index: number) => (e: React.KeyboardEvent<HTMLButtonElement>) => {
      const step = e.shiftKey ? 0.05 : 0.01;
      const deltas: Record<string, [number, number]> = {
        ArrowLeft: [-step, 0],
        ArrowRight: [step, 0],
        ArrowUp: [0, -step],
        ArrowDown: [0, step],
      };
      const delta = deltas[e.key];
      if (!delta) return;
      e.preventDefault();
      setCorners((prev) =>
        prev.map((c, i) =>
          i === index ? { x: clamp01(c.x + delta[0]), y: clamp01(c.y + delta[1]) } : c,
        ),
      );
    },
    [],
  );

  const polygon = corners.map((c) => `${c.x * 100},${c.y * 100}`).join(" ");

  return (
    <div className="crop">
      <div className="crop-head">
        <h3>Mark the card edges</h3>
        <p className="muted">
          Drag each handle to a corner of the card, then re-scan. This tells us
          exactly where the card is so we can read it properly.
        </p>
      </div>

      <div className="crop-stage" ref={stageRef} style={stageStyle}>
        <img
          src={imageUrl}
          alt="Scanned card"
          draggable={false}
          onLoad={handleImageLoad}
        />

        {/* The overlay's viewBox is 0-100 with preserveAspectRatio="none", so
            SVG coordinates are literally the normalized percentages. */}
        <svg
          className="crop-overlay"
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          <defs>
            <mask id="crop-mask">
              <rect x="0" y="0" width="100" height="100" fill="white" />
              <polygon points={polygon} fill="black" />
            </mask>
          </defs>
          <rect
            x="0"
            y="0"
            width="100"
            height="100"
            fill="rgba(22,24,29,0.55)"
            mask="url(#crop-mask)"
          />
          <polygon
            points={polygon}
            fill="none"
            stroke="var(--yellow)"
            strokeWidth="0.6"
            vectorEffect="non-scaling-stroke"
          />
        </svg>

        {/* Handles are HTML, not SVG, so their hit area stays a finger-friendly
            size in CSS pixels no matter how large the image renders. */}
        {corners.map((c, i) => (
          <button
            key={i}
            type="button"
            className={`crop-handle ${dragging === i ? "crop-handle-active" : ""}`}
            style={{ left: `${c.x * 100}%`, top: `${c.y * 100}%` }}
            onPointerDown={handlePointerDown(i)}
            onPointerMove={handlePointerMove(i)}
            onPointerUp={handlePointerUp}
            onPointerCancel={handlePointerUp}
            onKeyDown={handleKeyDown(i)}
            aria-label={`Move ${LABELS[i]} corner`}
            data-corner={i}
          >
            <span />
          </button>
        ))}
      </div>

      <div className="crop-actions">
        <button className="btn btn-ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button
          className="btn btn-primary"
          onClick={() => onConfirm(corners)}
          disabled={busy}
        >
          {busy ? <span className="spinner" /> : "Re-scan with these edges"}
        </button>
      </div>
    </div>
  );
}
