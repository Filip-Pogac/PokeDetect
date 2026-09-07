import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";
import { coverCrop } from "../lib/frameAnalysis";
import { Pokeball } from "./PokeArt";
import { useAutoCapture } from "./useAutoCapture";
import "./CameraScanner.css";

interface Props {
  onCapture: (imageDataUrl: string) => void;
  busy: boolean;
  /** Suppress auto-capture (scan in flight, or the result modal is open). */
  paused?: boolean;
  /** The still being scanned, shown in the stage for as long as the scan runs.
   *  An uploaded photo has no camera feed behind it, so without this the
   *  scanline sweeps an empty black frame. */
  stillImage?: string | null;
}

const AUTO_KEY = "pokedetect_auto_capture";

function loadAutoPreference(): boolean {
  try {
    return localStorage.getItem(AUTO_KEY) !== "off";
  } catch {
    return true;
  }
}

const STATUS_TEXT: Record<string, string> = {
  searching: "Point at a card…",
  holding: "Hold steady…",
  captured: "Captured",
};

// What is blocking the shot takes precedence over the generic phase text: a
// guide that never fills has to say *why*, or the stricter framing check just
// reads as the scanner being broken.
const HINT_TEXT: Record<string, string> = {
  framing: "Move closer — fill the guides",
  focus: "Hold still to focus…",
  steady: "Hold steady…",
};

export function CameraScanner({
  onCapture,
  busy,
  paused = false,
  stillImage = null,
}: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [active, setActive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoCapture, setAutoCapture] = useState(loadAutoPreference);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const stopCamera = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setActive(false);
  }, []);

  const startCamera = useCallback(async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment", width: { ideal: 1280 } },
        audio: false,
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      setActive(true);
    } catch {
      setError(
        "Camera access was blocked or is unavailable. You can upload a photo instead.",
      );
    }
  }, []);

  useEffect(() => () => stopCamera(), [stopCamera]);

  const capture = useCallback(() => {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return;

    // The preview is object-fit: cover, so the stage shows a centre crop of the
    // feed. Grabbing the full frame instead would hand the scanner a wider shot
    // than the one the user framed — the card comes out smaller than it looked
    // inside the guides. Reproduce the same crop here so what is captured is
    // exactly what was on screen.
    const vw = video.videoWidth;
    const vh = video.videoHeight;
    const box = video.getBoundingClientRect();
    const stageAspect = box.width > 0 && box.height > 0 ? box.width / box.height : vw / vh;
    const { sx, sy, sw, sh } = coverCrop(vw, vh, stageAspect);

    const canvas = document.createElement("canvas");
    canvas.width = Math.round(sw);
    canvas.height = Math.round(sh);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(video, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
    onCapture(canvas.toDataURL("image/jpeg", 0.9));
  }, [onCapture]);

  const { phase, progress, hint } = useAutoCapture({
    videoRef,
    active,
    enabled: autoCapture,
    paused: paused || busy,
    onFire: capture,
  });

  const toggleAuto = useCallback(() => {
    setAutoCapture((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(AUTO_KEY, next ? "on" : "off");
      } catch {
        /* storage unavailable - preference just won't persist */
      }
      return next;
    });
  }, []);

  const handleFile = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => onCapture(String(reader.result));
      reader.readAsDataURL(file);
      e.target.value = "";
    },
    [onCapture],
  );

  const showStatus = active && autoCapture && !busy && !paused && phase !== "idle";

  return (
    <div className="scanner">
      <div className="scanner-stage">
        <video
          ref={videoRef}
          className="scanner-video"
          playsInline
          muted
          data-active={active}
        />
        {/* Sits over the video too: freezing the frame being scanned reads
            better than a live feed that has already moved on. */}
        {stillImage && (
          <>
            <img className="scanner-still-bg" src={stillImage} alt="" aria-hidden="true" />
            <img className="scanner-still" src={stillImage} alt="Card being scanned" />
          </>
        )}

        {!active && !stillImage && (
          <div className="scanner-placeholder">
            <div className="scanner-icon" aria-hidden="true">
              <Pokeball size={44} />
            </div>
            <p>Point your camera at a Pokémon card</p>
          </div>
        )}

        {/* Card framing guide — brightens as the steady-hold fills. It is an
            aiming aid, so it comes off once there is a still to look at. */}
        {!stillImage && (
          <div className="scanner-frame" data-active={active} data-phase={phase}>
            <span className="corner tl" />
            <span className="corner tr" />
            <span className="corner bl" />
            <span className="corner br" />
          </div>
        )}

        {showStatus && (
          <div className="scanner-status" data-phase={phase}>
            <span className="scanner-status-ring" aria-hidden="true">
              <svg viewBox="0 0 36 36">
                <circle className="ring-track" cx="18" cy="18" r="16" />
                <circle
                  className="ring-fill"
                  cx="18"
                  cy="18"
                  r="16"
                  style={{ strokeDashoffset: 100.5 * (1 - progress) }}
                />
              </svg>
            </span>
            <span>{(hint && HINT_TEXT[hint]) || STATUS_TEXT[phase] || ""}</span>
          </div>
        )}

        {busy && (
          <div className="scanner-scanline-wrap">
            <div className="scanner-scanline" />
          </div>
        )}
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      <div className="scanner-controls">
        {!active ? (
          <button className="btn btn-primary" onClick={startCamera} disabled={busy}>
            Start camera
          </button>
        ) : (
          <>
            <button className="btn btn-accent" onClick={capture} disabled={busy}>
              {busy ? <span className="spinner spinner-ink" /> : "Scan card"}
            </button>
            <button className="btn btn-ghost" onClick={stopCamera} disabled={busy}>
              Stop
            </button>
          </>
        )}

        <button
          className="btn btn-ghost"
          onClick={() => fileInputRef.current?.click()}
          disabled={busy}
        >
          Upload photo
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          hidden
          onChange={handleFile}
        />

        <label className="auto-toggle" title="Capture automatically when the card is steady and in focus">
          <input type="checkbox" checked={autoCapture} onChange={toggleAuto} />
          <span>Auto-capture</span>
        </label>
      </div>
    </div>
  );
}
