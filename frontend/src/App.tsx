import { useCallback, useEffect, useState } from "react";
import { Admin } from "./Admin";
import { UNAUTHORIZED_EVENT, getDashboard, getMe, logout } from "./api";
import { Archive } from "./Archive";
import { Dashboard } from "./Dashboard";
import { Help } from "./Help";
import { ChangePassword, Login } from "./Login";
import { Review } from "./Review";
import { Toaster } from "./toast";
import { type Page, navigate, usePage } from "./router";
import type { CurrentUser } from "./types";
import { Workspace } from "./Workspace";

const NAV: { page: Page; label: string; icon: string; admin?: boolean }[] = [
  { page: "start", label: "Start", icon: "⌂" },
  { page: "pruefen", label: "Zu prüfen", icon: "✎" },
  { page: "umwandeln", label: "Umwandeln", icon: "⇄" },
  { page: "archiv", label: "Archiv", icon: "▤" },
  { page: "verwaltung", label: "Verwaltung", icon: "⚙", admin: true },
  { page: "hilfe", label: "Hilfe", icon: "?" },
];

/** Shell: login gate, forced password change, sidebar navigation by role. */
export function App() {
  const [user, setUser] = useState<CurrentUser | null | undefined>(undefined); // undefined = loading
  const page = usePage();
  const [refreshKey, setRefreshKey] = useState(0);
  const [reviewOpen, setReviewOpen] = useState(0);
  const changed = useCallback(() => setRefreshKey((k) => k + 1), []);

  useEffect(() => {
    getMe().then(setUser).catch(() => setUser(null));
    const onUnauthorized = () => setUser(null);
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);

  // badge "Zu prüfen"
  useEffect(() => {
    if (!user || user.must_change_password) return;
    const load = () =>
      getDashboard()
        .then((d) => setReviewOpen(d.review_open))
        .catch(() => undefined);
    void load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [user, refreshKey]);

  if (user === undefined) return null;
  if (user === null)
    return (
      <Login
        onLogin={(u) => {
          navigate("start");
          setUser(u);
        }}
      />
    );
  const refresh = () => getMe().then(setUser);
  if (user.must_change_password) return <ChangePassword forced onDone={refresh} />;
  const isAdmin = user.role === "admin";
  const current = page === "verwaltung" && !isAdmin ? "start" : page;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <svg className="logo" viewBox="0 0 200 200" aria-hidden="true">
            <rect width="200" height="200" rx="44" fill="#066baf" />
            <g transform="translate(55 42) scale(0.4)" fill="#fff" fillRule="evenodd">
              <path d="M0 0H125C172 0 202 36 202 86C202 132 170 166 120 170L230 290H170L0 112Z M50 46V126H124C141 126 152 110 152 86C152 62 141 46 124 46Z" />
              <path d="M0 150L133 290H0Z" />
            </g>
          </svg>
          <span>
            E-Rechnung
            <small>Konverter</small>
          </span>
        </div>
        <nav aria-label="Bereiche">
          {NAV.filter((n) => !n.admin || isAdmin).map((n) => (
            <a
              key={n.page}
              href={`#/${n.page}`}
              className={current === n.page ? "active" : ""}
              aria-current={current === n.page ? "page" : undefined}
            >
              <span className="icon" aria-hidden>
                {n.icon}
              </span>
              <span className="label">{n.label}</span>
              {n.page === "pruefen" && reviewOpen > 0 && <span className="badge-count">{reviewOpen}</span>}
            </a>
          ))}
        </nav>
        <div className="user">
          <div className="who">
            <strong>{user.username}</strong>
            <small>{isAdmin ? "Administrator" : "Benutzer"}</small>
          </div>
          <a href="#/passwort" className={current === "passwort" ? "active" : ""}>
            Passwort ändern
          </a>
          <button
            className="link"
            onClick={async () => {
              await logout();
              setUser(null);
            }}
          >
            Abmelden
          </button>
        </div>
      </aside>

      <main className="content">
        {current === "start" && <Dashboard me={user} refreshKey={refreshKey} />}
        {current === "pruefen" && <Review refreshKey={refreshKey} onChanged={changed} />}
        {/* stays mounted, so switching pages never loses an invoice in progress */}
        <div hidden={current !== "umwandeln"} className="page page-full">
          <Workspace onChanged={changed} />
        </div>
        {current === "archiv" && (
          <div className="page">
            <Archive refreshKey={refreshKey} />
          </div>
        )}
        {current === "verwaltung" && isAdmin && (
          <div className="page">
            <Admin me={user} />
          </div>
        )}
        {current === "hilfe" && (
          <div className="page">
            <Help me={user} />
          </div>
        )}
        {current === "passwort" && (
          <ChangePassword onDone={() => navigate("start")} onCancel={() => navigate("start")} />
        )}
      </main>
      <Toaster />
    </div>
  );
}
