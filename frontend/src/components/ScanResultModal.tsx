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
  /** Discard this result and go back to the scanner for another attempt.
   *  Distinct from onClose: closing keeps the still around, this throws it
   *  away so the camera starts clean. */
  onRescan?: () => void;
  /** Fired once the card is in the collection; the modal closes right after. */
  onSaved: (card: CardMatch) => void;
  /** Re-run the scan with hand-placed card corners. */
  onRecrop?: (corners: Array<[number, number]>) => void;
  recropBusy?: boolean;
  /**
   * False for name searches, where there is no photo to grade: the estimate
   * and its confidence bar would just be a guess dressed up as a reading.
   */
  conditionAnalyzed?: boolean;
}

function formatPrice(value: number | null, currency: string): string {
  if (value == null) return "—";
  const symbol = currency === "USD" ? "$" : "€";
  return `${symbol}${value.toFixed(2)}`;
}

interface MatchListProps {
  cards: CardMatch[];
  selectedId: string;
  onSelect: (id: string) => void;
  /** Confidence is only meaningful for ranked matches, not name suggestions. */
  showConfidence?: boolean;
}

function MatchList({
  cards,
  selectedId,
  onSelect,
  showConfidence = false,
}: MatchListProps) {
  return (
    <div className="match-list">
      {cards.map((m, i) => (
        <button
          key={m.tcg_id}
          className={`match-row ${
            m.tcg_id === selectedId ? "match-row-active" : ""
          }`}
          onClick={() => onSelect(m.tcg_id)}
        >
          {m.image_url && <img src={m.image_url} alt="" className="match-thumb" />}
          <span className="match-info">
            <strong>
              {m.name}
              {showConfidence && i === 0 && m.confidence > 0.7 && (
                <span className="best-match-badge">Best match</span>
              )}
            </strong>
            <span className="muted">
              {m.set_name}
              {m.number ? ` · #${m.number}` : ""}
              {m.rarity ? ` · ${m.rarity}` : ""}
              {m.variant ? ` · ${m.variant}` : ""}
            </span>
            {showConfidence && (
              <span className="match-confidence muted">
                {Math.round(m.confidence * 100)}% match
              </span>
            )}
          </span>
          <span className="match-price">
            {formatPrice(m.price.market_price, m.price.currency)}
          </span>
        </button>
      ))}
    </div>
  );
}

export function ScanResultModal({
  result,
  capturedImage,
  onClose,
  onRescan,
  onSaved,
  onRecrop,
  recropBusy = false,
  conditionAnalyzed = true,
}: Props) {
  const [selectedId, setSelectedId] = useState<string>(
    result.matches[0]?.tcg_id ?? "",
  );
  const [condition, setCondition] = useState<string>(result.condition.condition);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cropping, setCropping] = useState(false);

  // Offered whenever the original still is still around, not only when
  // detection failed outright: automatic detection can just as easily lock onto
  // the wrong rectangle and return a confident reading of the wrong card, and
  // a user who sees the wrong card needs a way to correct it.
  const canRecrop = !!capturedImage && !!onRecrop;

  // A pick can come from either list, so resolve against both.
  const selectable = useMemo(
    () => [...result.matches, ...(result.suggestions ?? [])],
    [result.matches, result.suggestions],
  );

  const selected: CardMatch | undefined = useMemo(
    () => selectable.find((m) => m.tcg_id === selectedId),
    [selectable, selectedId],
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
        variant: selected.variant,
        market_price: selected.price.market_price,
        currency: selected.price.currency,
        condition,
        condition_confidence: result.condition.confidence,
        damage_notes: result.condition.notes.join(" "),
      });
      // The card is in the collection, so the picker has done its job — get
      // it out of the way instead of leaving the user to dismiss it.
      onSaved(selected);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the card.");
      setSaving(false);
    }
  };

  const cond = result.condition;
  const suggestions = result.suggestions ?? [];
  const suggestedNames = result.suggested_names ?? [];

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
              {conditionAnalyzed && (
                <>
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
                </>
              )}

              {conditionAnalyzed && cond.is_potentially_damaged && (
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
                  {result.card_detected
                    ? "Wrong card? Marking the edges yourself usually fixes it."
                    : "We couldn’t find the card’s edges, so the reading may be off."}
                </span>
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => setCropping(true)}
                >
                  Mark edges manually
                </button>
              </div>
            )}

            {result.recognized_number && (
              <p className="read-number muted">
                Card number read: <strong>{result.recognized_number}</strong>
              </p>
            )}

            {result.matches.length > 0 && (
              <>
                <label className="section-label">
                  Which card is this? ({result.matches.length}{" "}
                  {result.matches.length === 1 ? "printing" : "printings"})
                </label>
                <MatchList
                  cards={result.matches}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                  showConfidence
                />
              </>
            )}

            {result.matches.length === 0 && suggestions.length > 0 && (
              <>
                <div className="alert alert-info">
                  Couldn’t pin down the exact printing, so here is every card
                  named {suggestedNames.map((n) => `“${n}”`).join(", ")}.
                </div>
                <label className="section-label">
                  Cards with this name ({suggestions.length})
                </label>
                <MatchList
                  cards={suggestions}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                />
              </>
            )}

            {result.matches.length === 0 && suggestions.length === 0 && (
              <div className="alert alert-info">
                No card match found. Try “Scan again” with better lighting, or
                use “Search by name”.
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
              <button
                className="btn btn-primary"
                onClick={handleSave}
                disabled={!selected || saving}
              >
                {saving ? <span className="spinner" /> : "Save to my collection"}
              </button>
              {onRescan && (
                <button
                  className="btn btn-ghost"
                  onClick={onRescan}
                  disabled={saving}
                >
                  Scan again
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
