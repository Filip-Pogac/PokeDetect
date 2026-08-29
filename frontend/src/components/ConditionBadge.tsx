// Cardmarket-style condition grades and their badge styling.

export const CONDITIONS = [
  "Mint",
  "Near Mint",
  "Excellent",
  "Good",
  "Light Played",
  "Played",
  "Poor",
] as const;

export type Condition = (typeof CONDITIONS)[number];

const CLASS_MAP: Record<string, string> = {
  Mint: "badge-mint",
  "Near Mint": "badge-nm",
  Excellent: "badge-exc",
  Good: "badge-good",
  "Light Played": "badge-played",
  Played: "badge-played",
  Poor: "badge-poor",
};

// Short Cardmarket abbreviations, shown alongside the full grade.
export const CONDITION_ABBR: Record<string, string> = {
  Mint: "MT",
  "Near Mint": "NM",
  Excellent: "EX",
  Good: "GD",
  "Light Played": "LP",
  Played: "PL",
  Poor: "PO",
};

export function ConditionBadge({ condition }: { condition: string }) {
  const cls = CLASS_MAP[condition] ?? "badge-exc";
  const abbr = CONDITION_ABBR[condition];
  return (
    <span className={`badge ${cls}`}>
      {abbr && <strong>{abbr}</strong>}
      {condition}
    </span>
  );
}
