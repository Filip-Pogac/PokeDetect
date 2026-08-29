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

// Worst-to-best, for sorting by condition.
const CONDITION_RANK: Record<string, number> = Object.fromEntries(
  [...CONDITIONS].reverse().map((c, i) => [c, i]),
);

type SortKey = "recent" | "value" | "name" | "condition";

const SORT_LABELS: Record<SortKey, string> = {
  recent: "Recently added",
  value: "Highest value",
  name: "Name (A–Z)",
  condition: "Best condition",
};

function symbol(currency: string) {
  return currency === "USD" ? "$" : "€";
}

/** Condition-adjusted value of a single copy. */
function unitValue(card: CollectionCard): number | null {
  if (card.market_price == null) return null;
  return card.market_price * (MULTIPLIERS[card.condition] ?? 1);
}

/** Condition-adjusted value of all copies owned. */
function totalValue(card: CollectionCard): number | null {
  const unit = unitValue(card);
  return unit == null ? null : unit * card.quantity;
}

function toCsv(cards: CollectionCard[]): string {
  const escape = (value: unknown) => `"${String(value ?? "").replace(/"/g, '""')}"`;
  const header = [
    "Name",
    "Set",
    "Number",
    "Rarity",
    "Condition",
    "Quantity",
    "Currency",
    "Market price",
    "Estimated value (all copies)",
    "Added",
  ];
  const rows = cards.map((c) => [
    c.name,
    c.set_name,
    c.number,
    c.rarity,
    c.condition,
    c.quantity,
    c.currency,
    c.market_price ?? "",
    totalValue(c)?.toFixed(2) ?? "",
    c.created_at,
  ]);
  return [header, ...rows].map((row) => row.map(escape).join(",")).join("\n");
}

export function CollectionPage() {
  const [cards, setCards] = useState<CollectionCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);

  const [query, setQuery] = useState("");
  const [setFilter, setSetFilter] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("recent");

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

  const setNames = useMemo(() => {
    const names = new Set<string>();
    for (const c of cards) if (c.set_name) names.add(c.set_name);
    return [...names].sort();
  }, [cards]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = cards.filter((c) => {
      if (setFilter && c.set_name !== setFilter) return false;
      if (!q) return true;
      return (
        c.name.toLowerCase().includes(q) ||
        c.set_name.toLowerCase().includes(q) ||
        c.number.toLowerCase().includes(q)
      );
    });

    const sorted = [...filtered];
    sorted.sort((a, b) => {
      switch (sortKey) {
        case "value":
          return (totalValue(b) ?? -1) - (totalValue(a) ?? -1);
        case "name":
          return a.name.localeCompare(b.name);
        case "condition":
          return (CONDITION_RANK[b.condition] ?? 0) - (CONDITION_RANK[a.condition] ?? 0);
        default:
          return b.created_at.localeCompare(a.created_at);
      }
    });
    return sorted;
  }, [cards, query, setFilter, sortKey]);

  // Totals reflect what's currently shown, so filtering doubles as a way to
  // value a single set.
  const totals = useMemo(() => {
    let eur = 0;
    let usd = 0;
    let copies = 0;
    for (const c of visible) {
      copies += c.quantity;
      const v = totalValue(c);
      if (v == null) continue;
      if (c.currency === "USD") usd += v;
      else eur += v;
    }
    return { eur, usd, copies };
  }, [visible]);

  const patchCard = async (
    card: CollectionCard,
    patch: Partial<Pick<CollectionCard, "condition" | "quantity">>,
    failureMessage: string,
  ) => {
    const prev = cards;
    setCards((cs) => cs.map((c) => (c.id === card.id ? { ...c, ...patch } : c)));
    try {
      await api.updateCard(card.id, patch);
    } catch {
      setCards(prev); // revert on failure
      setError(failureMessage);
    }
  };

  const changeCondition = (card: CollectionCard, condition: string) => {
    setEditingId(null);
    return patchCard(card, { condition }, "Could not update condition.");
  };

  const changeQuantity = (card: CollectionCard, delta: number) => {
    const quantity = card.quantity + delta;
    if (quantity < 1) return; // removing the last copy is an explicit Remove
    return patchCard(card, { quantity }, "Could not update quantity.");
  };

  const remove = async (card: CollectionCard) => {
    const label =
      card.quantity > 1 ? `all ${card.quantity} copies of ${card.name}` : card.name;
    if (!confirm(`Remove ${label} from your collection?`)) return;
    const prev = cards;
    setCards((cs) => cs.filter((c) => c.id !== card.id));
    try {
      await api.deleteCard(card.id);
    } catch {
      setCards(prev);
      setError("Could not remove the card.");
    }
  };

  const exportCsv = () => {
    const blob = new Blob([toCsv(visible)], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `pokedetect-collection-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  if (loading) {
    return (
      <div className="container collection-loading">
        <span className="spinner spinner-ink" />
        <span>Loading your collection…</span>
      </div>
    );
  }

  const isFiltered = query.trim() !== "" || setFilter !== "";

  return (
    <div className="collection-page container">
      <div className="collection-head">
        <div>
          <h1>My Collection</h1>
          <p className="muted">
            {totals.copies} {totals.copies === 1 ? "card" : "cards"}
            {visible.length !== totals.copies &&
              ` · ${visible.length} ${visible.length === 1 ? "entry" : "entries"}`}
            {isFiltered && ` (filtered from ${cards.length})`}
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

      {cards.length > 0 && (
        <div className="collection-toolbar card-surface">
          <input
            className="cc-search"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by name, set or number…"
            aria-label="Search collection"
          />
          <select
            value={setFilter}
            onChange={(e) => setSetFilter(e.target.value)}
            aria-label="Filter by set"
          >
            <option value="">All sets</option>
            {setNames.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as SortKey)}
            aria-label="Sort collection"
          >
            {(Object.keys(SORT_LABELS) as SortKey[]).map((k) => (
              <option key={k} value={k}>
                {SORT_LABELS[k]}
              </option>
            ))}
          </select>
          <button
            className="btn btn-ghost btn-sm"
            onClick={exportCsv}
            disabled={visible.length === 0}
          >
            Export CSV
          </button>
        </div>
      )}

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
      ) : visible.length === 0 ? (
        <div className="collection-empty card-surface">
          <h3>No matches</h3>
          <p className="muted">
            No cards match your search or filter. Try a different term.
          </p>
          <button
            className="btn btn-ghost"
            onClick={() => {
              setQuery("");
              setSetFilter("");
            }}
          >
            Clear filters
          </button>
        </div>
      ) : (
        <div className="collection-grid">
          {visible.map((card) => {
            const unit = unitValue(card);
            const total = totalValue(card);
            return (
              <div key={card.id} className="collection-card card-surface">
                <div className="cc-image">
                  {card.image_url ? (
                    <img src={card.image_url} alt={card.name} />
                  ) : (
                    <div className="cc-image-empty">No image</div>
                  )}
                  {card.quantity > 1 && (
                    <span className="cc-qty-badge">×{card.quantity}</span>
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

                  <div className="cc-qty">
                    <span className="muted">Copies</span>
                    <div className="cc-qty-controls">
                      <button
                        onClick={() => changeQuantity(card, -1)}
                        disabled={card.quantity <= 1}
                        aria-label={`Decrease quantity of ${card.name}`}
                      >
                        −
                      </button>
                      <span className="cc-qty-value">{card.quantity}</span>
                      <button
                        onClick={() => changeQuantity(card, 1)}
                        aria-label={`Increase quantity of ${card.name}`}
                      >
                        +
                      </button>
                    </div>
                  </div>

                  <div className="cc-price">
                    <span className="cc-price-val">
                      {total != null
                        ? `${symbol(card.currency)}${total.toFixed(2)}`
                        : "No price"}
                    </span>
                    {unit != null && card.quantity > 1 && (
                      <span className="cc-price-base muted">
                        {symbol(card.currency)}
                        {unit.toFixed(2)} each
                      </span>
                    )}
                    {card.market_price != null && card.quantity === 1 && (
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
