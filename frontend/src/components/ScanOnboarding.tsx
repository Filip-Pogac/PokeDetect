import { PokeballWatermark } from "./PokeArt";
import "./ScanOnboarding.css";

const KEY = "pokedetect_onboarded";

/** Whether the first-run explainer still needs showing. */
export function shouldShowOnboarding(): boolean {
  try {
    return localStorage.getItem(KEY) !== "done";
  } catch {
    return false; // storage blocked - don't nag on every visit
  }
}

function markSeen() {
  try {
    localStorage.setItem(KEY, "done");
  } catch {
    /* storage unavailable - it'll just show again next time */
  }
}

const STEPS = [
  {
    title: "Start the camera",
    body: "Allow camera access, then hold your phone over a single card on a flat surface.",
  },
  {
    title: "Line it up and hold still",
    body: "Fill the guide with the card. Once it's steady and in focus the shot is taken automatically — no button needed.",
  },
  {
    title: "Confirm and save",
    body: "Pick the right match, check the condition we estimated, and save it to your collection.",
  },
];

export function ScanOnboarding({ onClose }: { onClose: () => void }) {
  const dismiss = () => {
    markSeen();
    onClose();
  };

  return (
    <div className="onboard-backdrop" onClick={dismiss}>
      <div
        className="onboard card-surface"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="onboard-title"
      >
        <PokeballWatermark className="onboard-ball" size={200} />
        <h2 id="onboard-title">How scanning works</h2>
        <ol className="onboard-steps">
          {STEPS.map((s, i) => (
            <li key={s.title}>
              <span className="onboard-num">{i + 1}</span>
              <span>
                <strong>{s.title}</strong>
                <span className="muted">{s.body}</span>
              </span>
            </li>
          ))}
        </ol>
        <button className="btn btn-primary btn-block" onClick={dismiss}>
          Got it
        </button>
      </div>
    </div>
  );
}
