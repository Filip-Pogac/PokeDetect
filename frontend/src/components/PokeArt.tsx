/** Shared Pokémon-flavoured decoration.
 *
 * Everything here is original geometry drawn with `currentColor` so a motif
 * picks up whatever the surrounding surface sets — that is what keeps the
 * theme sleek instead of sticker-like. No raster art, no external assets:
 * the visuals stay crisp at any size and cost nothing to load.
 */
import "./PokeArt.css";

interface GlyphProps {
  size?: number;
  className?: string;
}

/** The app's core mark. `split` draws the two-tone ball, otherwise it is a
 *  single-colour line glyph that sits quietly inside text and buttons. */
export function Pokeball({
  size = 24,
  className,
  split = false,
}: GlyphProps & { split?: boolean }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden="true"
    >
      {split && (
        <>
          <path d="M16 2a14 14 0 0 1 14 14H2A14 14 0 0 1 16 2Z" fill="currentColor" />
          <circle cx="16" cy="16" r="14" fill="none" stroke="currentColor" strokeWidth="2.5" />
        </>
      )}
      {!split && <circle cx="16" cy="16" r="14" stroke="currentColor" strokeWidth="2.5" />}
      <path d="M2 16h8M22 16h8" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
      <circle cx="16" cy="16" r="5.5" fill="var(--pokeart-core, #fff)" stroke="currentColor" strokeWidth="2.5" />
    </svg>
  );
}

/** The six energy types we use as accents. Deliberately abstract — a flame,
 *  a droplet, a leaf, a bolt, a swirl, a fist — so they read at 14px. */
export type EnergyType =
  | "fire"
  | "water"
  | "grass"
  | "lightning"
  | "psychic"
  | "fighting";

const ENERGY_PATHS: Record<EnergyType, string> = {
  fire: "M12 3c3.5 3.6 6 6.4 6 9.8A6 6 0 0 1 6 13c0-1.6.6-3 1.8-4.4.2 1.5.9 2.4 2 2.7C9.6 8.6 10.4 5.8 12 3Z",
  water: "M12 3c3.4 4.3 6 7.5 6 10.3A6 6 0 0 1 6 13.3C6 10.5 8.6 7.3 12 3Z",
  grass: "M19 4c0 7.5-3.6 12-8 12a5 5 0 0 1-5-4.2C10.6 11.4 14.8 8.6 19 4ZM5 20c1.6-3.4 4.4-6 8-7.6",
  lightning: "M14.5 2 5.5 13.2h5L9 22l9.5-11.6h-5.4L14.5 2Z",
  psychic: "M12 3a9 9 0 1 1-6.4 15.3M12 7.5a4.5 4.5 0 1 1-3.2 7.7",
  fighting: "M6 9.5 12 4l6 5.5-2 2.5V19H8v-7l-2-2.5Z",
};

/** Types that read better as an outline than a fill. */
const STROKED: EnergyType[] = ["grass", "psychic"];

export function EnergyGlyph({
  type,
  size = 16,
  className,
}: GlyphProps & { type: EnergyType }) {
  const stroked = STROKED.includes(type);
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <path
        d={ENERGY_PATHS[type]}
        fill={stroked ? "none" : "currentColor"}
        stroke={stroked ? "currentColor" : "none"}
        strokeWidth="1.9"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** A colour-tinted energy chip. Used where a plain bullet would otherwise be. */
export function EnergyChip({
  type,
  size = 15,
}: {
  type: EnergyType;
  size?: number;
}) {
  return (
    <span className="energy-chip" data-type={type}>
      <EnergyGlyph type={type} size={size} />
    </span>
  );
}

const ALL_TYPES: EnergyType[] = [
  "fire",
  "water",
  "grass",
  "lightning",
  "psychic",
  "fighting",
];

/** A muted strip of type glyphs — a page ornament, not a control. */
export function EnergyRow({ types = ALL_TYPES }: { types?: EnergyType[] }) {
  return (
    <div className="energy-row" aria-hidden="true">
      {types.map((t) => (
        <span key={t} className="energy-row-item" data-type={t}>
          <EnergyGlyph type={t} size={17} />
        </span>
      ))}
    </div>
  );
}

/** Oversized, very low-contrast pokéball for hero and empty-state corners.
 *  Purely decorative, and inert to pointers so it never eats a click. */
export function PokeballWatermark({
  className = "",
  size = 320,
}: {
  className?: string;
  size?: number;
}) {
  return (
    <span className={`poke-watermark ${className}`.trim()} aria-hidden="true">
      <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
        <circle cx="16" cy="16" r="14.5" stroke="currentColor" strokeWidth="1" />
        <circle cx="16" cy="16" r="11" stroke="currentColor" strokeWidth="0.6" />
        <path d="M1.5 16h9M21.5 16h9" stroke="currentColor" strokeWidth="1" />
        <circle cx="16" cy="16" r="5" stroke="currentColor" strokeWidth="1" />
        <circle cx="16" cy="16" r="2.2" stroke="currentColor" strokeWidth="0.6" />
      </svg>
    </span>
  );
}

/** Three fanned card backs. The empty-collection counterpart to the search
 *  loader's riffle — same vocabulary, no motion. */
export function CardFan({ size = 96 }: { size?: number }) {
  return (
    <span className="card-fan" style={{ width: size }} aria-hidden="true">
      <span className="card-fan-item fan-left" />
      <span className="card-fan-item fan-right" />
      <span className="card-fan-item fan-mid">
        <Pokeball size={size * 0.34} split />
      </span>
    </span>
  );
}
