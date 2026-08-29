import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import "./LoginPage.css";

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
      <div className="auth-hero">
        <div className="auth-hero-inner">
          <h1 className="auth-hero-title">
            Scan any Pokémon card.
            <br />
            <span className="hl">Know its condition & value.</span>
          </h1>
          <p className="auth-hero-sub">
            Point your camera at a card. PokeDetect identifies it, estimates its
            condition, and shows an approximate Cardmarket price — then saves it to
            your personal collection.
          </p>
          <ul className="auth-features">
            <li>
              <span className="dot" /> Deep-learning card recognition
            </li>
            <li>
              <span className="dot" /> Automatic condition & damage grading
            </li>
            <li>
              <span className="dot" /> Cardmarket price estimates
            </li>
            <li>
              <span className="dot" /> Your own saved collection
            </li>
          </ul>
        </div>
      </div>

      <div className="auth-form-wrap">
        <div className="auth-card card-surface">
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
