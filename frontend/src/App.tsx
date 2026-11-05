import { useEffect, useState } from "react";
import { Admin } from "./Admin";
import { UNAUTHORIZED_EVENT, getMe, logout } from "./api";
import { Converter } from "./Converter";
import { ChangePassword, Login } from "./Login";
import type { CurrentUser } from "./types";

type View = "convert" | "admin" | "password";

/** Shell: login gate, forced password change, navigation by role. */
export function App() {
  const [user, setUser] = useState<CurrentUser | null | undefined>(undefined); // undefined = loading
  const [view, setView] = useState<View>("convert");

  useEffect(() => {
    getMe().then(setUser).catch(() => setUser(null));
    const onUnauthorized = () => setUser(null);
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);

  if (user === undefined) return null;
  if (user === null)
    return (
      <Login
        onLogin={(u) => {
          setView("convert");
          setUser(u);
        }}
      />
    );
  const refresh = () => getMe().then(setUser);
  if (user.must_change_password) return <ChangePassword forced onDone={refresh} />;

  return (
    <div className="app">
      <header className="topbar">
        <h1>E-Rechnung Konverter</h1>
        <nav className="tabs" aria-label="Bereiche">
          <button className={view === "convert" ? "active" : ""} onClick={() => setView("convert")}>Umwandeln</button>
          {user.role === "admin" && (
            <button className={view === "admin" ? "active" : ""} onClick={() => setView("admin")}>Verwaltung</button>
          )}
        </nav>
        <div className="usermenu">
          <span>
            {user.username}
            <small>{user.role === "admin" ? "Administrator" : "Benutzer"}</small>
          </span>
          <button className="secondary" onClick={() => setView("password")}>Passwort ändern</button>
          <button
            className="secondary"
            onClick={async () => {
              await logout();
              setUser(null);
            }}
          >
            Abmelden
          </button>
        </div>
      </header>

      {/* keep the converter mounted so switching tabs does not lose an invoice in progress */}
      <div hidden={view !== "convert"}>
        <Converter />
      </div>
      {view === "admin" && user.role === "admin" && <Admin me={user} />}
      {view === "password" && <ChangePassword onDone={() => setView("convert")} onCancel={() => setView("convert")} />}
    </div>
  );
}
