import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type MutableRefObject,
  type RefObject,
} from "react";
import {
  contrastScore,
  coverCrop,
  downsample,
  edgeDensity,
  EDGE_DENSITY_MIN,
  framing,
  HOLD_SAMPLES,
  isFrameReady,
  MOTION_POOL,
  motionScore,
  relativeMotion,
  relaxation,
  sharpnessScore,
  toLuma,
} from "../lib/frameAnalysis";

export type AutoCapturePhase = "idle" | "searching" | "holding" | "captured";

/** Why a frame was rejected, so the UI can say what to change rather than
 *  leaving the user to guess at a guide that never fills. */
export type AutoCaptureHint = null | "framing" | "steady" | "focus";

/** Analysis cadence. Well below the video frame rate - sampling every frame
 *  would burn CPU for no benefit. */
const SAMPLE_INTERVAL_MS = 125; // ~8fps
/** Downscale used for motion and edge density (cheap, whole-frame). Coarse on
 *  purpose: at a finer scale a one-pixel hand tremor moves whole features
 *  between samples and reads as motion. */
const SMALL_W = 128;
const SMALL_H = 96;
/** Native-resolution centre crop used for focus (see sharpnessScore). Wide
 *  enough to still land on the card when it is held off-centre - at 200px a
 *  slightly offset card put the crop on the background and the frame read as
 *  out of focus. */
const CROP = 320;
/** How much of the hold streak a single failing sample costs. A hand-held
 *  phone drops the occasional frame to a wobble or a refocus; zeroing the
 *  streak on one of those is what made the guide feel like it had to be hit
 *  exactly, since the ring kept restarting. Decaying instead keeps a mostly
 *  good hold moving forward while a genuine miss still unwinds it in a few
 *  samples. */
const HOLD_DECAY = 2;

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
  /** What is currently blocking a capture, if anything. */
  hint: AutoCaptureHint;
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
  const [hint, setHint] = useState<AutoCaptureHint>(null);

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
  // When a card first appeared in frame. Drives the patience ramp: the longer
  // the user has been trying, the more give the steadiness and focus gates get.
  const presenceSinceRef = useRef(0);
  const cooldownUntilRef = useRef(0);
  const smallCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const cropCanvasRef = useRef<HTMLCanvasElement | null>(null);
  // Mirrors of the rendered values, so we only setState on an actual change.
  const phaseRef = useRef<AutoCapturePhase>("idle");
  const progressRef = useRef(0);
  const hintRef = useRef<AutoCaptureHint>(null);

  const publishHint = useCallback((next: AutoCaptureHint) => {
    if (hintRef.current !== next) {
      hintRef.current = next;
      setHint(next);
    }
  }, []);

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
      presenceSinceRef.current = 0;
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
        presenceSinceRef.current = 0;
        publishHint(null);
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

      // Analyse the same centre crop the stage displays, not the whole sensor
      // frame: the guide fractions are in stage coordinates, so measuring a
      // wider field would judge the card against scenery the user cannot see.
      const box = video.getBoundingClientRect();
      const stageAspect =
        box.width > 0 && box.height > 0
          ? box.width / box.height
          : video.videoWidth / video.videoHeight;
      const view = coverCrop(video.videoWidth, video.videoHeight, stageAspect);

      // Downscaled view: motion + presence.
      smallCtx.drawImage(
        video,
        view.sx,
        view.sy,
        view.sw,
        view.sh,
        0,
        0,
        SMALL_W,
        SMALL_H,
      );
      const luma = toLuma(smallCtx.getImageData(0, 0, SMALL_W, SMALL_H).data);
      // Motion is measured on a pooled copy; edges need the finer plane.
      const pooled = downsample(luma, SMALL_W, SMALL_H, MOTION_POOL).luma;
      const motion = relativeMotion(
        motionScore(prevLumaRef.current, pooled),
        contrastScore(pooled),
      );
      const edges = edgeDensity(luma, SMALL_W, SMALL_H);
      // Framing: does the card actually span the guide, or is it small/off?
      const frame = framing(luma, SMALL_W, SMALL_H);
      prevLumaRef.current = pooled;

      // The patience clock runs on presence alone, so it keeps counting while
      // the user fights to hold the frame and only restarts once the card
      // actually leaves the guide.
      const now = performance.now();
      if (edges < EDGE_DENSITY_MIN) presenceSinceRef.current = 0;
      else if (presenceSinceRef.current === 0) presenceSinceRef.current = now;
      const relax = relaxation(
        presenceSinceRef.current === 0 ? 0 : now - presenceSinceRef.current,
      );

      // Centre of the view at native resolution: focus.
      const cw = Math.min(CROP, Math.round(view.sw));
      const ch = Math.min(CROP, Math.round(view.sh));
      const sx = view.sx + (view.sw - cw) / 2;
      const sy = view.sy + (view.sh - ch) / 2;
      cropCtx.drawImage(video, sx, sy, cw, ch, 0, 0, cw, ch);
      const cropLuma = toLuma(cropCtx.getImageData(0, 0, cw, ch).data);
      const sharpness = sharpnessScore(cropLuma, cw, ch);

      const metrics = {
        motion,
        sharpness,
        edges,
        coverage: frame.coverage,
        drift: frame.drift,
      };
      const ready = isFrameReady(metrics, relax);
      if (!ready) {
        // Report the *first* unmet gate, in the order the user can act on it:
        // there is no point asking for a steadier hand while the card is still
        // too far away to read.
        publishHint(
          edges < EDGE_DENSITY_MIN
            ? null
            : !isFrameReady({ ...metrics, motion: 0, sharpness: Infinity }, relax)
              ? "framing"
              : !isFrameReady({ ...metrics, motion: 0 }, relax)
                ? "focus"
                : "steady",
        );
        // Bleed the streak off rather than dropping it, so one bad sample in an
        // otherwise steady hold does not send the user back to the start.
        holdRef.current = Math.max(0, holdRef.current - HOLD_DECAY);
        if (holdRef.current === 0) resetHold("searching");
        else publish("holding", holdRef.current / HOLD_SAMPLES);
        return;
      }

      holdRef.current += 1;
      if (holdRef.current >= HOLD_SAMPLES) {
        holdRef.current = 0;
        presenceSinceRef.current = 0;
        cooldownUntilRef.current = now + COOLDOWN_MS;
        publish("captured", 1);
        onFireRef.current();
        return;
      }
      publishHint(null);
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

  return { phase, progress, hint };
}
