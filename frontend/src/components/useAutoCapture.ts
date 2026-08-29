import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type MutableRefObject,
  type RefObject,
} from "react";
import {
  edgeDensity,
  HOLD_SAMPLES,
  isFrameReady,
  motionScore,
  sharpnessScore,
  toLuma,
} from "../lib/frameAnalysis";

export type AutoCapturePhase = "idle" | "searching" | "holding" | "captured";

/** Analysis cadence. Well below the video frame rate - sampling every frame
 *  would burn CPU for no benefit. */
const SAMPLE_INTERVAL_MS = 125; // ~8fps
/** Downscale used for motion and edge density (cheap, whole-frame). */
const SMALL_W = 160;
const SMALL_H = 120;
/** Native-resolution centre crop used for focus (see sharpnessScore). */
const CROP = 200;
/** Suppression window after a capture, so an error path that leaves no modal
 *  open cannot machine-gun the backend. */
const COOLDOWN_MS = 1500;

interface Options {
  videoRef: RefObject<HTMLVideoElement | null>;
  /** Camera is streaming. The analysis loop only runs while true. */
  active: boolean;
  /** User has auto-capture switched on. */
  enabled: boolean;
  /** Externally suppressed (request in flight, or result modal open). */
  paused: boolean;
  onFire: () => void;
}

interface AutoCaptureState {
  phase: AutoCapturePhase;
  /** 0-1 progress through the steady-hold, for the progress ring. */
  progress: number;
}

/**
 * Watches the live video and fires `onFire` once the frame has been held
 * steady, in focus, and with something card-like in the framing guide for
 * HOLD_SAMPLES consecutive samples.
 */
export function useAutoCapture({
  videoRef,
  active,
  enabled,
  paused,
  onFire,
}: Options): AutoCaptureState {
  const [phase, setPhase] = useState<AutoCapturePhase>("idle");
  const [progress, setProgress] = useState(0);

  // Everything the loop reads lives in refs. Props are mirrored rather than
  // closed over, so the rAF callback never sees a stale value and prop changes
  // don't tear down and restart the loop.
  const enabledRef = useRef(enabled);
  const pausedRef = useRef(paused);
  const onFireRef = useRef(onFire);
  useEffect(() => {
    enabledRef.current = enabled;
  }, [enabled]);
  useEffect(() => {
    pausedRef.current = paused;
  }, [paused]);
  useEffect(() => {
    onFireRef.current = onFire;
  }, [onFire]);

  // Per-sample scratch - deliberately refs, not state. Pushing metrics through
  // setState at 8fps would re-render the scanner continuously.
  const rafRef = useRef<number | null>(null);
  const lastSampleRef = useRef(0);
  const prevLumaRef = useRef<Uint8Array | null>(null);
  const holdRef = useRef(0);
  const cooldownUntilRef = useRef(0);
  const smallCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const cropCanvasRef = useRef<HTMLCanvasElement | null>(null);
  // Mirrors of the rendered values, so we only setState on an actual change.
  const phaseRef = useRef<AutoCapturePhase>("idle");
  const progressRef = useRef(0);

  const publish = useCallback((next: AutoCapturePhase, nextProgress: number) => {
    if (phaseRef.current !== next) {
      phaseRef.current = next;
      setPhase(next);
    }
    if (progressRef.current !== nextProgress) {
      progressRef.current = nextProgress;
      setProgress(nextProgress);
    }
  }, []);

  const resetHold = useCallback(
    (next: AutoCapturePhase) => {
      holdRef.current = 0;
      publish(next, 0);
    },
    [publish],
  );

  useEffect(() => {
    if (!active) {
      resetHold("idle");
      prevLumaRef.current = null;
      return;
    }

    const getCtx = (
      ref: MutableRefObject<HTMLCanvasElement | null>,
      w: number,
      h: number,
    ): CanvasRenderingContext2D | null => {
      if (!ref.current) ref.current = document.createElement("canvas");
      const canvas = ref.current;
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
      }
      // willReadFrequently avoids a GPU readback stall on every getImageData.
      return canvas.getContext("2d", { willReadFrequently: true });
    };

    const sample = () => {
      const video = videoRef.current;
      if (!video || video.readyState < 2 || !video.videoWidth) return;

      if (!enabledRef.current || pausedRef.current) {
        // Drop the motion baseline so resuming doesn't compare against a stale
        // frame and register a false "steady".
        prevLumaRef.current = null;
        resetHold("idle");
        return;
      }

      if (performance.now() < cooldownUntilRef.current) {
        publish("captured", 1);
        return;
      }

      const smallCtx = getCtx(smallCanvasRef, SMALL_W, SMALL_H);
      const cropCtx = getCtx(cropCanvasRef, CROP, CROP);
      if (!smallCtx || !cropCtx) return;

      // Whole frame, downscaled: motion + presence.
      smallCtx.drawImage(video, 0, 0, SMALL_W, SMALL_H);
      const luma = toLuma(smallCtx.getImageData(0, 0, SMALL_W, SMALL_H).data);
      const motion = motionScore(prevLumaRef.current, luma);
      const edges = edgeDensity(luma, SMALL_W, SMALL_H);
      prevLumaRef.current = luma;

      // Centre crop at native resolution: focus.
      const cw = Math.min(CROP, video.videoWidth);
      const ch = Math.min(CROP, video.videoHeight);
      const sx = (video.videoWidth - cw) / 2;
      const sy = (video.videoHeight - ch) / 2;
      cropCtx.drawImage(video, sx, sy, cw, ch, 0, 0, cw, ch);
      const cropLuma = toLuma(cropCtx.getImageData(0, 0, cw, ch).data);
      const sharpness = sharpnessScore(cropLuma, cw, ch);

      if (!isFrameReady({ motion, sharpness, edges })) {
        resetHold("searching");
        return;
      }

      holdRef.current += 1;
      if (holdRef.current >= HOLD_SAMPLES) {
        holdRef.current = 0;
        cooldownUntilRef.current = performance.now() + COOLDOWN_MS;
        publish("captured", 1);
        onFireRef.current();
        return;
      }
      publish("holding", holdRef.current / HOLD_SAMPLES);
    };

    const tick = (timestamp: number) => {
      rafRef.current = requestAnimationFrame(tick);
      if (timestamp - lastSampleRef.current < SAMPLE_INTERVAL_MS) return;
      lastSampleRef.current = timestamp;
      sample();
    };

    // Cancel any in-flight loop before starting a new one. StrictMode invokes
    // this effect twice in development; without this the loops would stack.
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(tick);
    publish("searching", 0);

    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      prevLumaRef.current = null;
    };
  }, [active, videoRef, publish, resetHold]);

  return { phase, progress };
}
