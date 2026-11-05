import { useEffect, useState } from "react";
import {
  ApiError,
  createUser,
  getAudit,
  getIdocStatus,
  getSettings,
  listUsers,
  retryIdoc,
  saveSettings,
  updateUser,
} from "./api";
import { Field, PartyFields, setPath } from "./InvoiceForm";
import type { AdminSettings, AuditEntry, CurrentUser, IdocStatus, Role } from "./types";

function message(err: unknown): string {
  if (err instanceof ApiError) return [err.message, ...err.details].join(" – ");
  return String(err);
}

function fmt(date: string | null): string {
  return date ? new Date(date).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) : "–";
}

type Tab = "users" | "idoc" | "settings" | "audit";

export function Admin({ me }: { me: CurrentUser }) {
  const [tab, setTab] = useState<Tab>("users");
  return (
    <div className="admin">
      <nav className="tabs" role="tablist">
        {([
          ["users", "Benutzer"],
          ["idoc", "SAP-Eingang"],
          ["settings", "Einstellungen & Konnektoren"],
          ["audit", "Protokoll"],
        ] as [Tab, string][]).map(
          ([id, label]) => (
            <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>
              {label}
            </button>
          ),
        )}
      </nav>
      {tab === "users" && <Users me={me} />}
      {tab === "idoc" && <IdocPanel />}
      {tab === "settings" && <SettingsPanel />}
      {tab === "audit" && <Audit />}
    </div>
  );
}

// --- users ----------------------------------------------------------------------

function Users({ me }: { me: CurrentUser }) {
  const [users, setUsers] = useState<CurrentUser[]>([]);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("user");
  const [password, setPassword] = useState("");

  const reload = () => listUsers().then(setUsers).catch((e) => setError(message(e)));
  useEffect(() => {
    void reload();
  }, []);

  async function run(action: () => Promise<unknown>, success: string) {
    setError("");
    setInfo("");
    try {
      await action();
      setInfo(success);
      await reload();
    } catch (e) {
      setError(message(e));
    }
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    await run(async () => {
      await createUser(name.trim(), role, password);
      setName("");
      setPassword("");
    }, `Benutzer „${name.trim()}“ angelegt. Das Startpasswort muss bei der ersten Anmeldung geändert werden.`);
  }

  function resetPassword(u: CurrentUser) {
    const pw = window.prompt(`Neues Startpasswort für „${u.username}“ (mind. 10 Zeichen):`);
    if (pw) void run(() => updateUser(u.id, { password: pw }), `Passwort von „${u.username}“ zurückgesetzt.`);
  }

  return (
    <div className="form">
      <section>
        <h2>Benutzer</h2>
        <div className="lines">
          <table>
            <thead>
              <tr>
                <th>Benutzer</th>
                <th>Rolle</th>
                <th>Status</th>
                <th>Letzte Anmeldung</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className={u.active ? "" : "inactive"}>
                  <td>
                    {u.username}
                    {u.id === me.id && <span className="badge">Sie</span>}
                  </td>
                  <td>
                    <select
                      value={u.role}
                      onChange={(e) => void run(() => updateUser(u.id, { role: e.target.value as Role }), `Rolle von „${u.username}“ geändert.`)}
                    >
                      <option value="user">Benutzer</option>
                      <option value="admin">Administrator</option>
                    </select>
                  </td>
                  <td>{!u.active ? "gesperrt" : u.must_change_password ? "Startpasswort" : "aktiv"}</td>
                  <td>{fmt(u.last_login_at)}</td>
                  <td className="actions">
                    <button className="secondary" onClick={() => resetPassword(u)}>Passwort zurücksetzen</button>
                    <button
                      className="secondary"
                      onClick={() =>
                        void run(() => updateUser(u.id, { active: !u.active }), `„${u.username}“ ${u.active ? "gesperrt" : "entsperrt"}.`)
                      }
                    >
                      {u.active ? "Sperren" : "Entsperren"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {error && <div className="alert error" role="alert">{error}</div>}
        {info && <div className="alert info">{info}</div>}
      </section>

      <section>
        <h2>Neuer Benutzer</h2>
        <form className="grid" onSubmit={add}>
          <label className="field">
            <span>Benutzername</span>
            <input value={name} onChange={(e) => setName(e.target.value)} required minLength={2} />
          </label>
          <label className="field">
            <span>Rolle</span>
            <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
              <option value="user">Benutzer (nur umwandeln)</option>
              <option value="admin">Administrator</option>
            </select>
          </label>
          <label className="field">
            <span>Startpasswort (mind. 10 Zeichen)</span>
            <input type="text" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={10} />
          </label>
          <div className="field end">
            <button className="primary">Anlegen</button>
          </div>
        </form>
      </section>
    </div>
  );
}

// --- settings / connectors --------------------------------------------------------

function SettingsPanel() {
  const [settings, setSettings] = useState<AdminSettings | null>(null);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");

  useEffect(() => {
    getSettings().then(setSettings).catch((e) => setError(message(e)));
  }, []);

  if (!settings) return error ? <div className="alert error">{error}</div> : <p className="hint">Lädt …</p>;
  const update = (path: string, value: unknown) => setSettings((s) => s && setPath(s, path, value));
  const f = (label: string, path: string, value: string, wide = false) => (
    <Field label={label} path={path} value={value} update={update} issues={[]} wide={wide} />
  );

  async function save() {
    setError("");
    setInfo("");
    try {
      setSettings(await saveSettings(settings!));
      setInfo("Gespeichert. Der SAP-Konnektor übernimmt die Änderung in seiner nächsten Runde.");
    } catch (e) {
      setError(message(e));
    }
  }

  const idoc = settings.idoc;
  return (
    <div className="form">
      <section>
        <h2>Konnektor: SAP ECC (IDoc-Eingangsordner)</h2>
        <p className="hint">
          SAP legt INVOIC02-Dateien im Eingangsordner ab; der Worker wandelt sie automatisch um und archiviert sie.
          Der Ordner selbst wird beim Start festgelegt (<code>IDOC_HOST_DIR</code>).
        </p>
        <div className="grid">
          <label className="field checkbox">
            <input type="checkbox" checked={idoc.enabled} onChange={(e) => update("idoc.enabled", e.target.checked)} />
            <span>Automatische Verarbeitung aktiv</span>
          </label>
          <label className="field">
            <span>Ausgabeformat</span>
            <select value={idoc.profile} onChange={(e) => update("idoc.profile", e.target.value)}>
              <option value="zugferd">ZUGFeRD (PDF)</option>
              <option value="xrechnung">XRechnung (XML)</option>
            </select>
          </label>
          <label className="field">
            <span>Prüfintervall (Sekunden, 5–3600)</span>
            <input
              type="number"
              min={5}
              max={3600}
              value={idoc.interval_seconds}
              onChange={(e) => update("idoc.interval_seconds", Number(e.target.value))}
            />
          </label>
        </div>
      </section>

      <section>
        <h2>Eigene Firmendaten (Rechnungssteller)</h2>
        <PartyFields prefix="company.seller" party={settings.company.seller} update={update} issues={[]} seller />
      </section>

      <section>
        <h2>Bankverbindung & Zahlungsbedingungen</h2>
        <div className="grid">
          {f("IBAN", "company.payment.iban", settings.company.payment.iban)}
          {f("BIC", "company.payment.bic", settings.company.payment.bic)}
          {f("Kontoinhaber", "company.payment.account_holder", settings.company.payment.account_holder)}
          {f("Standard-Zahlungsbedingungen", "company.payment.terms", settings.company.payment.terms, true)}
        </div>
      </section>

      {error && <div className="alert error" role="alert">{error}</div>}
      {info && <div className="alert info">{info}</div>}
      <div>
        <button className="primary inline" onClick={save}>Einstellungen speichern</button>
      </div>
    </div>
  );
}

// --- audit log -----------------------------------------------------------------------

const ACTIONS: Record<string, string> = {
  login: "Anmeldung",
  login_failed: "Fehlgeschlagene Anmeldung",
  export: "E-Rechnung erzeugt",
  download: "Archiv-Download",
  user_created: "Benutzer angelegt",
  user_updated: "Benutzer geändert",
  password_changed: "Passwort geändert",
  password_reset: "Passwort zurückgesetzt",
  settings_changed: "Einstellungen geändert",
  idoc_retry: "IDoc erneut eingereiht",
};

function Audit() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    getAudit(filter || undefined).then(setEntries).catch((e) => setError(message(e)));
  }, [filter]);

  return (
    <section>
      <h2>Protokoll</h2>
      <label className="field narrow">
        <span>Filter</span>
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">Alle Ereignisse</option>
          {Object.entries(ACTIONS).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
      </label>
      {error && <div className="alert error">{error}</div>}
      <div className="lines">
        <table>
          <thead>
            <tr>
              <th>Zeit</th>
              <th>Benutzer</th>
              <th>Ereignis</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id} className={e.action === "login_failed" ? "warn" : ""}>
                <td>{fmt(e.at)}</td>
                <td>{e.username}</td>
                <td>{ACTIONS[e.action] ?? e.action}</td>
                <td className="detail">
                  {Object.entries(e.detail)
                    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
                    .join(" · ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// --- SAP IDoc inbox status ----------------------------------------------------------

function IdocPanel() {
  const [status, setStatus] = useState<IdocStatus | null>(null);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");

  const reload = () =>
    getIdocStatus()
      .then((s) => {
        setStatus(s);
        setError("");
      })
      .catch((e) => setError(message(e)));

  useEffect(() => {
    void reload();
    const t = setInterval(() => void reload(), 10_000);
    return () => clearInterval(t);
  }, []);

  if (!status) return error ? <div className="alert error">{error}</div> : <p className="hint">Lädt …</p>;
  const w = status.worker;
  const state = !w.last_round_at
    ? { cls: "bad", text: "Worker hat sich noch nie gemeldet" }
    : !status.worker_alive
      ? { cls: "bad", text: `Worker antwortet nicht (zuletzt ${fmt(w.last_round_at)})` }
      : w.enabled === false
        ? { cls: "warn", text: "Worker läuft, automatische Verarbeitung ist ausgeschaltet" }
        : { cls: "good", text: `Worker läuft · prüft alle ${w.interval_seconds} s · Format ${w.profile === "xrechnung" ? "XRechnung" : "ZUGFeRD"}` };

  return (
    <div className="form">
      <section>
        <h2>SAP-Eingang (IDoc-Ordner)</h2>
        <p className={`status-line ${state.cls}`}>● {state.text}</p>
        {!status.folder_available && (
          <div className="alert error">
            Der IDoc-Ordner ist im Backend nicht eingebunden – Zähler und Fehlerliste sind nicht verfügbar
            (siehe <code>IDOC_HOST_DIR</code> in <code>compose.yaml</code>).
          </div>
        )}
        <div className="stats">
          <div className="stat">
            <span>Eingang (wartet)</span>
            <strong>{status.counts.inbox}</strong>
          </div>
          <div className="stat">
            <span>Erzeugt (Ausgang)</span>
            <strong>{status.counts.outbox}</strong>
          </div>
          <div className="stat">
            <span>Verarbeitet</span>
            <strong>{status.counts.processed}</strong>
          </div>
          <div className={`stat${status.counts.error ? " bad" : ""}`}>
            <span>Fehler</span>
            <strong>{status.counts.error}</strong>
          </div>
        </div>
        <p className="hint">
          Seit Start des Workers: {w.converted_total ?? 0} umgewandelt, {w.failed_total ?? 0} fehlgeschlagen · letzte
          Umwandlung {fmt(w.last_converted_at ?? null)} · letzter Fehler {fmt(w.last_failed_at ?? null)} · letzte
          Runde {fmt(w.last_round_at ?? null)}
        </p>
      </section>

      <section>
        <h2>Fehlgeschlagene IDocs</h2>
        {status.errors.length === 0 ? (
          <p className="ok">✓ Keine fehlgeschlagenen IDocs.</p>
        ) : (
          <>
            <p className="hint">
              Ursache beheben (z. B. Firmendaten oder Adressbuch ergänzen), dann „Erneut verarbeiten“ – die Datei
              wandert zurück in den Eingang.
            </p>
            <div className="lines">
              <table>
                <thead>
                  <tr>
                    <th>Datei</th>
                    <th>Zeit</th>
                    <th>Grund</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {status.errors.map((f) => (
                    <tr key={f.name}>
                      <td>{f.name}</td>
                      <td>{fmt(f.modified_at)}</td>
                      <td className="detail">{f.reason || "–"}</td>
                      <td>
                        <button
                          className="secondary"
                          onClick={async () => {
                            try {
                              await retryIdoc(f.name);
                              setInfo(`„${f.name}“ wurde erneut in den Eingang gelegt.`);
                              await reload();
                            } catch (e) {
                              setError(message(e));
                            }
                          }}
                        >
                          Erneut verarbeiten
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
        {error && <div className="alert error">{error}</div>}
        {info && <div className="alert info">{info}</div>}
      </section>
    </div>
  );
}
