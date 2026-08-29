import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";
import "./CameraScanner.css";

interface Props {
  onCapture: (imageDataUrl: string) => void;
  busy: boolean;
}

export function CameraScanner({ onCapture, busy }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [active, setActive] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    onCapture(canvas.toDataURL("image/jpeg", 0.9));
  }, [onCapture]);

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
        {!active && (
          <div className="scanner-placeholder">
            <div className="scanner-icon" aria-hidden="true">
              <svg width="40" height="40" viewBox="0 0 24 24" fill="none">
                <path
                  d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                />
                <rect
                  x="9"
                  y="9"
                  width="6"
                  height="6"
                  rx="1"
                  stroke="currentColor"
                  strokeWidth="2"
                />
              </svg>
            </div>
            <p>Point your camera at a Pokémon card</p>
          </div>
        )}

        {/* Card framing guide */}
        <div className="scanner-frame" data-active={active}>
          <span className="corner tl" />
          <span className="corner tr" />
          <span className="corner bl" />
          <span className="corner br" />
        </div>

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
            <button
              className="btn btn-accent"
              onClick={capture}
              disabled={busy}
            >
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
      </div>
    </div>
  );
}
