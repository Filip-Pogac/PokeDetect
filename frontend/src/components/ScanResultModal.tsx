import { useMemo, useState } from "react";
import {
  api,
  type CardMatch,
  type ScanResult,
} from "../api/client";
import { CONDITIONS, ConditionBadge } from "./ConditionBadge";
import { CropCorrector } from "./CropCorrector";
import "./ScanResultModal.css";

interface Props {
  result: ScanResult;
  capturedImage: string | null;
  onClose: () => void;
  onSaved: () => void;
  /** Re-run the scan with hand-placed card corners. */
  onRecrop?: (corners: Array<[number, number]>) => void;
  recropBusy?: boolean;
}

function formatPrice(value: number | null, currency: string): string {
  if (value == null) return "—";
  const symbol = currency === "USD" ? "$" : "€";
  return `${symbol}${value.toFixed(2)}`;
}

export function ScanResultModal({
  result,
  capturedImage,
  onClose,
  onSaved,
  onRecrop,
  recropBusy = false,
}: Props) {
  const [selectedId, setSelectedId] = useState<string>(
    result.matches[0]?.tcg_id ?? "",
  );
  const [condition, setCondition] = useState<string>(result.condition.condition);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cropping, setCropping] = useState(false);

  // Only worth offering when detection actually failed and we still hold the
  // original still to work from.
  const canRecrop = !result.card_detected && !!capturedImage && !!onRecrop;

  const selected: CardMatch | undefined = useMemo(
    () => result.matches.find((m) => m.tcg_id === selectedId),
    [result.matches, selectedId],
  );

  // Recompute the condition-adjusted estimate on the client so it updates
  // instantly when the user overrides the auto-detected grade.
  const multipliers: Record<string, number> = {
    Mint: 1.15,
    "Near Mint": 1.0,
    Excellent: 0.85,
    Good: 0.65,
    "Light Played": 0.5,
    Played: 0.38,
    Poor: 0.25,
  };
  const adjusted = useMemo(() => {
    if (!selected?.price.market_price) return null;
    return selected.price.market_price * (multipliers[condition] ?? 1);
  }, [selected, condition]);

  const handleSave = async () => {
    if (!selected) return;
    setSaving(true);
    setError(null);
    try {
      await api.addCard({
        name: selected.name,
        set_name: selected.set_name,
        number: selected.number,
        rarity: selected.rarity,
        image_url: selected.image_url,
        tcg_id: selected.tcg_id,
        market_price: selected.price.market_price,
        currency: selected.price.currency,
        condition,
        condition_confidence: result.condition.confidence,
        damage_notes: result.condition.notes.join(" "),
      });
      setSaved(true);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the card.");
    } finally {
      setSaving(false);
    }
  };

  const cond = result.condition;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal card-surface"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <button className="modal-close" onClick={onClose} aria-label="Close">
          ✕
        </button>

        {cropping && capturedImage ? (
          <CropCorrector
            imageUrl={capturedImage}
            busy={recropBusy}
            onCancel={() => setCropping(false)}
            onConfirm={(corners) => {
              setCropping(false);
              onRecrop?.(corners.map((c) => [c.x, c.y] as [number, number]));
            }}
          />
        ) : (
        <div className="modal-grid">
          {/* Left: card image + condition */}
          <div className="modal-left">
            <div className="result-image">
              {selected?.image_url ? (
                <img src={selected.image_url} alt={selected.name} />
              ) : capturedImage ? (
                <img src={capturedImage} alt="Scanned card" />
              ) : (
                <div className="result-image-empty">No image</div>
              )}
            </div>

            <div className="condition-block">
              <div className="condition-head">
                <span className="condition-label">Estimated condition</span>
                <ConditionBadge condition={cond.condition} />
              </div>
              <div className="confidence-bar">
                <div
                  className="confidence-fill"
                  style={{ width: `${Math.round(cond.confidence * 100)}%` }}
                />
              </div>
              <span className="confidence-text">
                {Math.round(cond.confidence * 100)}% confidence
              </span>

              {cond.is_potentially_damaged && (
                <div className="damage-flag">
                  ⚠ This card may be damaged or worn.
                </div>
              )}
              <ul className="condition-notes">
                {cond.notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            </div>
          </div>

          {/* Right: identity, matches, price, save */}
          <div className="modal-right">
            <p className="modal-message">{result.message}</p>

            {canRecrop && (
              <div className="recrop-banner">
                <span>
                  We couldn’t find the card’s edges, so the reading may be off.
                </span>
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => setCropping(true)}
                >
                  Mark edges manually
                </button>
              </div>
            )}

            {result.matches.length > 0 ? (
              <>
                <label className="section-label">
                  Which card is this? ({result.matches.length} matches)
                </label>
                <div className="match-list">
                  {result.matches.map((m, i) => (
                    <button
                      key={m.tcg_id}
                      className={`match-row ${
                        m.tcg_id === selectedId ? "match-row-active" : ""
                      }`}
                      onClick={() => setSelectedId(m.tcg_id)}
                    >
                      {m.image_url && (
                        <img src={m.image_url} alt="" className="match-thumb" />
                      )}
                      <span className="match-info">
                        <strong>
                          {m.name}
                          {i === 0 && m.confidence > 0.7 && (
                            <span className="best-match-badge">Best match</span>
                          )}
                        </strong>
                        <span className="muted">
                          {m.set_name}
                          {m.number ? ` · #${m.number}` : ""}
                          {m.rarity ? ` · ${m.rarity}` : ""}
                        </span>
                        <span className="match-confidence muted">
                          {Math.round(m.confidence * 100)}% match
                        </span>
                      </span>
                      <span className="match-price">
                        {formatPrice(m.price.market_price, m.price.currency)}
                      </span>
                    </button>
                  ))}
                </div>
              </>
            ) : (
              <div className="alert alert-info">
                No card match found. Try re-scanning with better lighting, or use
                “Search by name”.
              </div>
            )}

            {selected && (
              <div className="price-panel">
                <div className="price-row">
                  <span className="muted">Cardmarket trend</span>
                  <span className="price-big">
                    {formatPrice(selected.price.market_price, selected.price.currency)}
                  </span>
                </div>

                <div className="field">
                  <label htmlFor="cond-select">
                    Condition (adjusts the estimate)
                  </label>
                  <select
                    id="cond-select"
                    value={condition}
                    onChange={(e) => setCondition(e.target.value)}
                  >
                    {CONDITIONS.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="price-row estimate">
                  <span>Estimated value ({condition})</span>
                  <span className="price-big price-accent">
                    {formatPrice(adjusted, selected.price.currency)}
                  </span>
                </div>

                <p className="disclaimer">{selected.price.disclaimer}</p>
              </div>
            )}

            {error && <div className="alert alert-error">{error}</div>}

            <div className="modal-actions">
              {saved ? (
                <div className="saved-note">✓ Saved to your collection</div>
              ) : (
                <button
                  className="btn btn-primary btn-block"
                  onClick={handleSave}
                  disabled={!selected || saving}
                >
                  {saving ? <span className="spinner" /> : "Save to my collection"}
                </button>
              )}
            </div>
          </div>
        </div>
        )}
      </div>
    </div>
  );
}
