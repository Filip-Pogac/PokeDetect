import { useState, type FormEvent } from "react";
import { api, type ScanResult } from "../api/client";
import { CameraScanner } from "../components/CameraScanner";
import { ScanOnboarding, shouldShowOnboarding } from "../components/ScanOnboarding";
import { ScanResultModal } from "../components/ScanResultModal";
import "./ScanPage.css";

export function ScanPage() {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ScanResult | null>(null);
  const [captured, setCaptured] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [manualName, setManualName] = useState("");
  const [showHelp, setShowHelp] = useState(shouldShowOnboarding);

  const runScan = async (image: string) => {
    setBusy(true);
    setError(null);
    setCaptured(image);
    try {
      const res = await api.scan(image);
      setResult(res);
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
    try {
      const res = await api.scan(captured, undefined, undefined, corners);
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Re-scan failed. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const runManualSearch = async (e: FormEvent) => {
    e.preventDefault();
    if (!manualName.trim()) return;
    setBusy(true);
    setError(null);
    setCaptured(null);
    try {
      const matches = await api.searchCards(manualName.trim(), "Near Mint");
      setResult({
        matches,
        condition: {
          condition: "Near Mint",
          confidence: 0.3,
          is_potentially_damaged: false,
          notes: [
            "Condition not analyzed for manual searches — set it yourself below.",
          ],
        },
        recognized_text: [],
        card_detected: false,
        message: matches.length
          ? `Found ${matches.length} cards for “${manualName}”.`
          : `No cards found for “${manualName}”.`,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Search failed.");
    } finally {
      setBusy(false);
    }
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
            paused={result !== null || showHelp}
          />
          {error && <div className="alert alert-error" style={{ marginTop: 16 }}>{error}</div>}
        </div>

        <aside className="scan-side">
          <div className="card-surface scan-tips">
            <h3>Tips for a good scan</h3>
            <ul>
              <li>Use even, bright light — avoid glare on holo cards.</li>
              <li>Fill the frame with the whole card.</li>
              <li>Keep the card flat and in focus.</li>
              <li>A plain, dark background helps detection.</li>
            </ul>
          </div>

          <div className="card-surface scan-manual">
            <h3>Can’t read the card?</h3>
            <p className="muted">Search the Pokémon TCG database by name instead.</p>
            <form onSubmit={runManualSearch}>
              <div className="field">
                <input
                  value={manualName}
                  onChange={(e) => setManualName(e.target.value)}
                  placeholder="e.g. Charizard"
                />
              </div>
              <button className="btn btn-ghost btn-block" disabled={busy} type="submit">
                Search by name
              </button>
            </form>
          </div>
        </aside>
      </div>

      {result && (
        <ScanResultModal
          result={result}
          capturedImage={captured}
          onClose={() => setResult(null)}
          onSaved={() => { /* collection refreshes on its own page */ }}
          onRecrop={runRecrop}
          recropBusy={busy}
        />
      )}

      {showHelp && <ScanOnboarding onClose={() => setShowHelp(false)} />}
    </div>
  );
}
