import { useState, type FormEvent } from "react";
import { api, type CardMatch, type ScanResult } from "../api/client";
import { CameraScanner } from "../components/CameraScanner";
import { CardSearchLoader } from "../components/CardSearchLoader";
import { ScanOnboarding, shouldShowOnboarding } from "../components/ScanOnboarding";
import { ScanResultModal } from "../components/ScanResultModal";
import "./ScanPage.css";

/** Split "Lucario 67" into a name and a collector number.
 *
 * People type the number off the card along with the name, and the search
 * ranks an exact collector-number hit to the top - so pulling the trailing
 * number out turns a scroll through 40 Lucarios into the right one first.
 * Anything that isn't a trailing number is left in the name untouched.
 */
function parseQuery(raw: string): { name: string; number: string | null } {
  const NUMBER = /^#?([0-9]{1,3}[a-z]?(?:\/[0-9]{1,3})?)$/i;
  // A bare number is a valid search on its own.
  const bare = raw.match(NUMBER);
  if (bare) return { name: "", number: bare[1] };
  // Otherwise the number has to be a separate trailing word, so names that
  // simply end in a digit ("Porygon2") stay intact.
  const parts = raw.match(/^(.*\S)\s+(\S+)$/);
  const tail = parts?.[2].match(NUMBER);
  if (!parts || !tail) return { name: raw, number: null };
  return { name: parts[1].trim(), number: tail[1] };
}

const TIPS: string[] = [
  "Use even, bright light — avoid glare on holo cards.",
  "Fill the frame with the whole card.",
  "Keep the card flat and in focus.",
  "A plain, dark background helps detection.",
];

export function ScanPage() {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ScanResult | null>(null);
  const [captured, setCaptured] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [manualName, setManualName] = useState("");
  // Kept apart from `busy`: a name lookup is not a scan, so it must not drive
  // the camera's scanning animation.
  const [searching, setSearching] = useState(false);
  const [savedNote, setSavedNote] = useState<string | null>(null);
  const [showHelp, setShowHelp] = useState(shouldShowOnboarding);
  // Bumped on every new result. Used as the modal's key so a re-scan replaces
  // the open modal outright instead of leaving it holding the previous scan's
  // selection and edge-marking state — otherwise the only way to see the new
  // result is to close the window and start over.
  const [scanId, setScanId] = useState(0);

  // Name searches carry no photo, so nothing was graded — the modal hides the
  // estimate and confidence bar and leaves the note explaining why.
  const [fromNameSearch, setFromNameSearch] = useState(false);

  const showResult = (res: ScanResult, nameSearch = false) => {
    setResult(res);
    setFromNameSearch(nameSearch);
    setScanId((n) => n + 1);
  };

  const runScan = async (image: string) => {
    setBusy(true);
    setError(null);
    setCaptured(image);
    try {
      showResult(await api.scan(image));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Scan failed. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  /** Re-run the scan on the same still, using corners the user placed. */
  const runRecrop = async (corners: Array<[number, number]>) => {
    if (!captured) return;
    setBusy(true);
    setError(null);
    // Dismiss the window the correction was started from: it is still showing
    // the mis-read result, and leaving it up over a spinner-less modal reads as
    // if nothing happened. The new result opens a fresh modal of its own, and a
    // failed re-scan surfaces its error on the page rather than behind a modal.
    setResult(null);
    try {
      showResult(await api.scan(captured, undefined, undefined, corners));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Re-scan failed. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const runManualSearch = async (e: FormEvent) => {
    e.preventDefault();
    const raw = manualName.trim();
    if (!raw) return;
    const { name, number } = parseQuery(raw);
    setSearching(true);
    setError(null);
    setCaptured(null);
    try {
      const matches = await api.searchCards(name, "Near Mint", number);
      showResult(
        {
          matches,
          suggestions: [],
          suggested_names: [],
          condition: {
            condition: "Near Mint",
            confidence: 0.3,
            is_potentially_damaged: false,
            notes: [
              "Condition not analyzed for manual searches — set it yourself below.",
            ],
          },
          recognized_text: [],
          recognized_number: number,
          card_detected: false,
          message: matches.length
            ? `Found ${matches.length} cards for “${name || raw}”.`
            : `No cards found for “${raw}”.`,
        },
        true,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Search failed.");
    } finally {
      setSearching(false);
    }
  };

  /** Throw this attempt away and go back to a clean scanner.
   *
   * Clears the still as well as the result: leaving it behind would keep the
   * frozen frame over the camera view, so the next attempt would look like it
   * had already captured something. */
  const handleRescan = () => {
    setResult(null);
    setCaptured(null);
    setError(null);
  };

  const handleSaved = (card: CardMatch) => {
    setSavedNote(`${card.name} — ${card.set_name} added to your collection.`);
    window.setTimeout(() => setSavedNote(null), 4000);
  };

  return (
    <div className="scan-page container">
      <div className="scan-head">
        <h1>
          Scan a card
          <button
            className="scan-help-btn"
            onClick={() => setShowHelp(true)}
            aria-label="How scanning works"
            title="How scanning works"
          >
            ?
          </button>
        </h1>
        <p className="muted">
          Frame a single Pokémon card inside the guides and hold steady — we’ll
          capture it automatically, identify it, grade its condition, and estimate
          its value.
        </p>
      </div>

      <div className="scan-layout">
        <div className="scan-main card-surface">
          {/* paused matters: busy clears the moment the request resolves, but
              the result modal opens at that same instant — without this,
              auto-capture would start firing again behind the open modal. */}
          <CameraScanner
            onCapture={runScan}
            busy={busy}
            paused={result !== null || showHelp || searching}
            /* Only while the scan runs: `captured` outlives it so a re-crop can
               reuse the same still, but the stage goes back to the live feed as
               soon as there is a result to look at. */
            stillImage={busy ? captured : null}
          />
          {error && <div className="alert alert-error" style={{ marginTop: 16 }}>{error}</div>}
        </div>

        <aside className="scan-side">
          <div className="card-surface scan-tips">
            <h3>Tips for a good scan</h3>
            <ul className="scan-tip-list">
              {TIPS.map((tip) => (
                <li key={tip}>{tip}</li>
              ))}
            </ul>
          </div>

          <div className="card-surface scan-manual">
            <h3>Can’t read the card?</h3>
            <p className="muted">
              Search the Pokémon TCG database by name instead. Add the collector
              number — “Lucario 67” — to put that printing first.
            </p>
            <form onSubmit={runManualSearch}>
              <div className="field">
                <input
                  value={manualName}
                  onChange={(e) => setManualName(e.target.value)}
                  placeholder="e.g. Charizard, or Lucario 67"
                  disabled={searching}
                />
              </div>
              <button
                className="btn btn-ghost btn-block"
                disabled={busy || searching}
                type="submit"
              >
                {searching ? "Searching…" : "Search by name"}
              </button>
            </form>
            {searching && <CardSearchLoader query={manualName.trim()} />}
          </div>
        </aside>
      </div>

      {result && (
        <ScanResultModal
          key={scanId}
          result={result}
          capturedImage={captured}
          onClose={() => setResult(null)}
          onRescan={handleRescan}
          onSaved={handleSaved}
          onRecrop={runRecrop}
          recropBusy={busy}
          conditionAnalyzed={!fromNameSearch}
        />
      )}

      {savedNote && <div className="scan-toast">✓ {savedNote}</div>}

      {showHelp && <ScanOnboarding onClose={() => setShowHelp(false)} />}
    </div>
  );
}
