import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { EnergyChip, Pokeball, PokeballWatermark, type EnergyType } from "../components/PokeArt";
import { useAuth } from "../context/AuthContext";
import "./LoginPage.css";

const FEATURES: Array<{ type: EnergyType; label: string }> = [
  { type: "lightning", label: "Deep-learning card recognition" },
  { type: "water", label: "Automatic condition & damage grading" },
  { type: "fire", label: "Cardmarket price estimates" },
  { type: "grass", label: "Your own saved collection" },
];

export function LoginPage() {
  const { login, register } = useAuth();
  const navigate = useNavigate();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "login") {
        await login(email, password);
      } else {
        await register(email, name, password);
      }
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      {/* Split in two so the phone layout can wrap the form: the pitch reads
          above the card, the supporting detail below it. On desktop the two
          halves flow together as one column of hero copy. */}
      <div className="auth-hero">
        {/* Anchored to the hero column itself — .auth-hero-inner is positioned,
            so a watermark nested inside it would offset from that 460px block
            instead of the column's bottom-right corner. */}
        <PokeballWatermark className="auth-hero-ball" size={460} />
        <div className="auth-hero-inner">
          <div className="auth-hero-head">
            <span className="auth-eyebrow">
              <Pokeball size={16} />
              Pokémon TCG scanner
            </span>
            <h1 className="auth-hero-title">
              Scan any Pokémon card.
              <br />
              <span className="hl">Know its condition & value.</span>
            </h1>
          </div>
          <div className="auth-hero-body">
            {/* The phone counterpart: the hero column is dissolved there, so the
                band carries its own mark. Hidden on desktop. */}
            <PokeballWatermark className="auth-hero-ball-sm" size={290} />
            <p className="auth-hero-sub">
              Point your camera at a card. PokeDetect identifies it, estimates its
              condition, and shows an approximate Cardmarket price — then saves it
              to your personal collection.
            </p>
            <ul className="auth-features">
              {FEATURES.map((f) => (
                <li key={f.label}>
                  <EnergyChip type={f.type} /> {f.label}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>

      <div className="auth-form-wrap">
        <div className="auth-card card-surface">
          <div className="auth-card-mark" aria-hidden="true">
            <Pokeball size={34} split />
          </div>
          <div className="auth-tabs">
            <button
              className={mode === "login" ? "auth-tab active" : "auth-tab"}
              onClick={() => setMode("login")}
            >
              Sign in
            </button>
            <button
              className={mode === "register" ? "auth-tab active" : "auth-tab"}
              onClick={() => setMode("register")}
            >
              Create account
            </button>
          </div>

          <form onSubmit={submit}>
            {mode === "register" && (
              <div className="field">
                <label htmlFor="name">Display name</label>
                <input
                  id="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Ash Ketchum"
                  required
                />
              </div>
            )}
            <div className="field">
              <label htmlFor="email">Email</label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                required
              />
            </div>
            <div className="field">
              <label htmlFor="password">Password</label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={mode === "register" ? "At least 6 characters" : "••••••••"}
                minLength={6}
                required
              />
            </div>

            {error && <div className="alert alert-error">{error}</div>}

            <button className="btn btn-primary btn-block" disabled={busy} type="submit">
              {busy ? (
                <span className="spinner" />
              ) : mode === "login" ? (
                "Sign in"
              ) : (
                "Create account"
              )}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
