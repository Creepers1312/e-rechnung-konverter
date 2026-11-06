import { useEffect, useRef, useState } from "react";
import { ApiError, getCustomerImportInfo, getSettings, importCustomerMaster, listCustomerMaster, saveSettings } from "./api";
import type { AdminSettings, CustomerImportInfo, CustomerPage } from "./types";
import { fmtDateTime } from "./util";

function message(err: unknown): string {
  if (err instanceof ApiError) return [err.message, ...err.details].join(" – ");
  return String(err);
}

/** Admin: SAP customer master import + address book. */
export function CustomerMaster() {
  const [info, setInfo] = useState<CustomerImportInfo | null>(null);
  const [settings, setSettings] = useState<AdminSettings | null>(null);
  const [page, setPage] = useState<CustomerPage | null>(null);
  const [q, setQ] = useState("");
  const [onlyMissing, setOnlyMissing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const reload = () => {
    getCustomerImportInfo().then(setInfo).catch((e) => setError(message(e)));
    getSettings().then(setSettings).catch((e) => setError(message(e)));
  };
  useEffect(reload, []);
  useEffect(() => {
    const t = setTimeout(() => {
      listCustomerMaster({ q, missing_einvoice: onlyMissing })
        .then(setPage)
        .catch((e) => setError(message(e)));
    }, 250);
    return () => clearTimeout(t);
  }, [q, onlyMissing, info]);

  async function upload(file: File) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const r = await importCustomerMaster(file);
      setNotice(`„${r.file}“ importiert: ${r.created} neu, ${r.updated} aktualisiert, ${r.skipped} übersprungen.`);
      reload();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  async function saveImportSettings(next: AdminSettings) {
    setError("");
    try {
      setSettings(await saveSettings(next));
      setNotice("Einstellungen gespeichert – gelten ab dem nächsten Import.");
    } catch (e) {
      setError(message(e));
    }
  }

  const last = info?.last;
  const cfg = settings?.customer_import;
  return (
    <div className="form">
      <section>
        <div className="section-head">
          <h2>SAP-Kundenstamm</h2>
          <input
            ref={fileInput}
            type="file"
            accept=".csv,.txt,text/csv,text/plain"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void upload(f);
              e.target.value = "";
            }}
          />
          <button className="primary inline" disabled={busy} onClick={() => fileInput.current?.click()}>
            {busy ? "Importiere …" : "CSV jetzt importieren"}
          </button>
        </div>
        <p className="hint">
          SAP legt den Export (SAP-Query über KNA1 + Adressdaten, als täglicher Job) im Ordner{" "}
          <code>data/stammdaten/inbox</code> ab; die App liest ihn automatisch ein. Fehlen auf einer Rechnung Angaben zum
          Käufer, werden sie über die <strong>Kundennummer</strong> aus diesem Stamm ergänzt.
        </p>
        {last ? (
          <div className="stats">
            <div className="stat">
              <span>Letzter Import</span>
              <strong className="small-strong">{fmtDateTime(last.at)}</strong>
              <small className="muted">{last.file}</small>
            </div>
            <div className="stat">
              <span>Neu / aktualisiert</span>
              <strong>
                {last.created} / {last.updated}
              </strong>
            </div>
            <div className={`stat${last.skipped ? " bad" : ""}`}>
              <span>Übersprungen</span>
              <strong>{last.skipped}</strong>
            </div>
            <div className={`stat${page?.missing_einvoice ? " bad" : ""}`}>
              <span>SAP-Kunden ohne E-Rechnungs-Adresse</span>
              <strong>{page?.missing_einvoice ?? "–"}</strong>
            </div>
          </div>
        ) : (
          <p className="muted">Noch kein Import.</p>
        )}
        {last && last.warnings.length > 0 && (
          <details>
            <summary>{last.warnings.length} Hinweise zum letzten Import</summary>
            <ul className="plain">
              {last.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </details>
        )}
        {error && <div className="alert error">{error}</div>}
        {notice && <div className="alert info">{notice}</div>}
      </section>

      {cfg && info && (
        <section>
          <h2>Import-Einstellungen</h2>
          <div className="grid">
            <label className="field checkbox">
              <input
                type="checkbox"
                checked={cfg.enabled}
                onChange={(e) => void saveImportSettings({ ...settings!, customer_import: { ...cfg, enabled: e.target.checked } })}
              />
              <span>Automatischer Import aus dem Ordner aktiv</span>
            </label>
            <label className="field checkbox">
              <input
                type="checkbox"
                checked={cfg.master_wins}
                onChange={(e) =>
                  void saveImportSettings({ ...settings!, customer_import: { ...cfg, master_wins: e.target.checked } })
                }
              />
              <span>SAP-Stammdaten haben Vorrang vor Angaben aus dem PDF (empfohlen bei Scans)</span>
            </label>
          </div>
          <h3 className="subhead">Spaltenzuordnung</h3>
          <p className="hint">
            Leer lassen = automatisch erkennen (z. B. KUNNR, NAME1, STRAS, PSTLZ, ORT01, LAND1, STCEG, SMTP_ADDR). Nur
            eintragen, wenn eine Spalte anders heißt.
          </p>
          <div className="lines">
            <table>
              <thead>
                <tr>
                  <th>Feld</th>
                  <th>Spalte in der CSV</th>
                  <th>Zuletzt erkannt</th>
                </tr>
              </thead>
              <tbody>
                {info.fields.map((f) => (
                  <tr key={f.key}>
                    <td>{f.label}</td>
                    <td>
                      <input
                        value={cfg.mapping[f.key] ?? ""}
                        placeholder="automatisch"
                        onChange={(e) =>
                          setSettings({
                            ...settings!,
                            customer_import: { ...cfg, mapping: { ...cfg.mapping, [f.key]: e.target.value } },
                          })
                        }
                      />
                    </td>
                    <td className="muted">{last?.mapping[f.key] ?? "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {last && last.unmapped_columns.length > 0 && (
            <p className="hint">Nicht verwendete Spalten im letzten Export: {last.unmapped_columns.join(", ")}</p>
          )}
          <button className="primary inline" onClick={() => void saveImportSettings(settings!)}>
            Zuordnung speichern
          </button>
        </section>
      )}

      <section>
        <div className="section-head">
          <h2>Kunden {page ? `(${page.total})` : ""}</h2>
          <div className="filters inline-filters">
            <input type="search" placeholder="Suche: Name, Kundennummer, Ort" value={q} onChange={(e) => setQ(e.target.value)} />
            <label className="field checkbox">
              <input type="checkbox" checked={onlyMissing} onChange={(e) => setOnlyMissing(e.target.checked)} />
              <span>nur ohne E-Rechnungs-Adresse</span>
            </label>
          </div>
        </div>
        <div className="table-card">
          <table className="data">
            <thead>
              <tr>
                <th>Kd.-Nr.</th>
                <th>Name</th>
                <th>Ort</th>
                <th>USt-IdNr.</th>
                <th>E-Rechnungs-Adresse</th>
                <th>Leitweg-ID</th>
                <th>Herkunft</th>
              </tr>
            </thead>
            <tbody>
              {page?.items.map((c) => (
                <tr key={c.id}>
                  <td>{c.erp_id || "–"}</td>
                  <td>{c.name}</td>
                  <td>
                    {c.city} {c.country && c.country !== "DE" ? `(${c.country})` : ""}
                  </td>
                  <td>{c.vat_id || "–"}</td>
                  <td className={c.electronic_address ? "" : "missing"}>{c.electronic_address || "fehlt"}</td>
                  <td>{c.buyer_reference || "–"}</td>
                  <td>
                    {c.source === "sap" ? (
                      <span className="badge">SAP {c.synced_at ? fmtDateTime(c.synced_at) : ""}</span>
                    ) : (
                      <span className="muted small">aus Rechnung</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {page && page.items.length === 0 && <p className="muted pad">Keine Kunden gefunden.</p>}
        </div>
      </section>
    </div>
  );
}
