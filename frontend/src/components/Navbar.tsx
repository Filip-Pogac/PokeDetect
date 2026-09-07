import { NavLink } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { Pokeball } from "./PokeArt";
import "./Navbar.css";

function Logo() {
  return (
    <div className="brand">
      <span className="brand-mark" aria-hidden="true">
        <Pokeball size={26} split />
      </span>
      <span className="brand-name">
        Poke<span className="brand-accent">Detect</span>
      </span>
    </div>
  );
}

export function Navbar() {
  const { user, logout } = useAuth();

  return (
    <header className="navbar">
      <div className="container navbar-inner">
        <NavLink to="/" className="brand-link">
          <Logo />
        </NavLink>

        {user && (
          <nav className="nav-links">
            <NavLink to="/" end className="nav-link">
              Scan
            </NavLink>
            <NavLink to="/collection" className="nav-link">
              My Collection
            </NavLink>
          </nav>
        )}

        <div className="nav-right">
          {user ? (
            <>
              <span className="nav-user">{user.display_name}</span>
              <button className="btn btn-ghost btn-sm" onClick={logout}>
                Sign out
              </button>
            </>
          ) : null}
        </div>
      </div>
    </header>
  );
}
