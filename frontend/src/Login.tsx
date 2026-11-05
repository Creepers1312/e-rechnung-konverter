import { useState } from "react";
import { ApiError, changePassword, login } from "./api";
import type { CurrentUser } from "./types";

export function Login({ onLogin }: { onLogin: (user: CurrentUser) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      onLogin(await login(username, password));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-page">
      <form className="card auth-card" onSubmit={submit}>
        <h1>E-Rechnung Konverter</h1>
        <p className="hint">Bitte anmelden.</p>
        <label className="field">
          <span>Benutzername</span>
          <input autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
        </label>
        <label className="field">
          <span>Passwort</span>
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <div className="alert error" role="alert">{error}</div>}
        <button className="primary" disabled={busy}>Anmelden</button>
      </form>
    </div>
  );
}

/** Own password change. `forced` = initial password, the app is locked until it is changed. */
export function ChangePassword({ forced, onDone, onCancel }: { forced?: boolean; onDone: () => void; onCancel?: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (next !== repeat) {
      setError("Die neuen Passwörter stimmen nicht überein.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await changePassword(current, next);
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-page">
      <form className="card auth-card" onSubmit={submit}>
        <h1>Passwort ändern</h1>
        {forced && <p className="hint">Bitte vergeben Sie vor der ersten Nutzung ein eigenes Passwort.</p>}
        <label className="field">
          <span>Aktuelles Passwort</span>
          <input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required />
        </label>
        <label className="field">
          <span>Neues Passwort (mind. 10 Zeichen)</span>
          <input type="password" autoComplete="new-password" minLength={10} value={next} onChange={(e) => setNext(e.target.value)} required />
        </label>
        <label className="field">
          <span>Neues Passwort wiederholen</span>
          <input type="password" autoComplete="new-password" minLength={10} value={repeat} onChange={(e) => setRepeat(e.target.value)} required />
        </label>
        {error && <div className="alert error" role="alert">{error}</div>}
        <button className="primary" disabled={busy}>Passwort speichern</button>
        {onCancel && (
          <button type="button" className="secondary" onClick={onCancel}>Abbrechen</button>
        )}
      </form>
    </div>
  );
}
