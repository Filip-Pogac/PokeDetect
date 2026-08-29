import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type CollectionCard } from "../api/client";
import { CONDITIONS, ConditionBadge } from "../components/ConditionBadge";
import "./CollectionPage.css";

const MULTIPLIERS: Record<string, number> = {
  Mint: 1.15,
  "Near Mint": 1.0,
  Excellent: 0.85,
  Good: 0.65,
  "Light Played": 0.5,
  Played: 0.38,
  Poor: 0.25,
};

function symbol(currency: string) {
  return currency === "USD" ? "$" : "€";
}

function estimatedValue(card: CollectionCard): number | null {
  if (card.market_price == null) return null;
  return card.market_price * (MULTIPLIERS[card.condition] ?? 1);
}

export function CollectionPage() {
  const [cards, setCards] = useState<CollectionCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);

  const load = async () => {
    try {
      setCards(await api.listCollection());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load your collection.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const totals = useMemo(() => {
    let eur = 0;
    let usd = 0;
    for (const c of cards) {
      const v = estimatedValue(c);
      if (v == null) continue;
      if (c.currency === "USD") usd += v;
      else eur += v;
    }
    return { eur, usd };
  }, [cards]);

  const changeCondition = async (card: CollectionCard, condition: string) => {
    const prev = cards;
    setCards((cs) =>
      cs.map((c) => (c.id === card.id ? { ...c, condition } : c)),
    );
    setEditingId(null);
    try {
      await api.updateCard(card.id, { condition });
    } catch {
      setCards(prev); // revert on failure
      setError("Could not update condition.");
    }
  };

  const remove = async (card: CollectionCard) => {
    if (!confirm(`Remove ${card.name} from your collection?`)) return;
    const prev = cards;
    setCards((cs) => cs.filter((c) => c.id !== card.id));
    try {
      await api.deleteCard(card.id);
    } catch {
      setCards(prev);
      setError("Could not remove the card.");
    }
  };

  if (loading) {
    return (
      <div className="container collection-loading">
        <span className="spinner spinner-ink" />
        <span>Loading your collection…</span>
      </div>
    );
  }

  return (
    <div className="collection-page container">
      <div className="collection-head">
        <div>
          <h1>My Collection</h1>
          <p className="muted">
            {cards.length} {cards.length === 1 ? "card" : "cards"} saved
          </p>
        </div>
        <div className="collection-total">
          <span className="muted">Estimated total</span>
          <strong>
            {totals.eur > 0 && `€${totals.eur.toFixed(2)}`}
            {totals.eur > 0 && totals.usd > 0 && " + "}
            {totals.usd > 0 && `$${totals.usd.toFixed(2)}`}
            {totals.eur === 0 && totals.usd === 0 && "—"}
          </strong>
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {cards.length === 0 ? (
        <div className="collection-empty card-surface">
          <h3>No cards yet</h3>
          <p className="muted">
            Scan your first Pokémon card to start building your collection.
          </p>
          <Link to="/" className="btn btn-primary">
            Scan a card
          </Link>
        </div>
      ) : (
        <div className="collection-grid">
          {cards.map((card) => {
            const est = estimatedValue(card);
            return (
              <div key={card.id} className="collection-card card-surface">
                <div className="cc-image">
                  {card.image_url ? (
                    <img src={card.image_url} alt={card.name} />
                  ) : (
                    <div className="cc-image-empty">No image</div>
                  )}
                </div>
                <div className="cc-body">
                  <h4 className="cc-name">{card.name}</h4>
                  <p className="cc-meta muted">
                    {card.set_name}
                    {card.number ? ` · #${card.number}` : ""}
                  </p>

                  <div className="cc-condition">
                    {editingId === card.id ? (
                      <select
                        autoFocus
                        defaultValue={card.condition}
                        onChange={(e) => changeCondition(card, e.target.value)}
                        onBlur={() => setEditingId(null)}
                      >
                        {CONDITIONS.map((c) => (
                          <option key={c} value={c}>
                            {c}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <button
                        className="cc-condition-btn"
                        onClick={() => setEditingId(card.id)}
                        title="Change condition"
                      >
                        <ConditionBadge condition={card.condition} />
                        <span className="cc-edit-hint">edit</span>
                      </button>
                    )}
                  </div>

                  <div className="cc-price">
                    <span className="cc-price-val">
                      {est != null
                        ? `${symbol(card.currency)}${est.toFixed(2)}`
                        : "No price"}
                    </span>
                    {card.market_price != null && (
                      <span className="cc-price-base muted">
                        trend {symbol(card.currency)}
                        {card.market_price.toFixed(2)}
                      </span>
                    )}
                  </div>

                  {card.damage_notes && (
                    <p className="cc-damage" title={card.damage_notes}>
                      {card.damage_notes}
                    </p>
                  )}

                  <button
                    className="btn btn-danger btn-sm cc-remove"
                    onClick={() => remove(card)}
                  >
                    Remove
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <p className="collection-disclaimer muted">
        Prices are approximate market figures from Cardmarket data and are not exact
        quotes — actual value varies with condition, edition and demand.
      </p>
    </div>
  );
}
