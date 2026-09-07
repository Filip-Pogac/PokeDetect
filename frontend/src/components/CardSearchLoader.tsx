import { useEffect, useState } from "react";
import "./CardSearchLoader.css";

/** Deliberately not the scanning animation: a name lookup isn't a scan, and
 *  reusing the scanline made it look like the camera was still working. */
const LINES = [
  "Flipping through the binder…",
  "Checking set symbols…",
  "Sorting by collector number…",
  "Asking Professor Oak…",
  "Dusting off the rare holos…",
];

export function CardSearchLoader({ query }: { query: string }) {
  const [line, setLine] = useState(0);

  useEffect(() => {
    const timer = setInterval(
      () => setLine((n) => (n + 1) % LINES.length),
      1800,
    );
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="card-search-loader" role="status" aria-live="polite">
      <div className="csl-deck" aria-hidden="true">
        <span className="csl-card csl-card-1" />
        <span className="csl-card csl-card-2" />
        <span className="csl-card csl-card-3" />
      </div>
      <p className="csl-line">{LINES[line]}</p>
      {query && <p className="csl-query muted">Looking for “{query}”</p>}
    </div>
  );
}
