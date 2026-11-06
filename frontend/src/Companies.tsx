import { useEffect, useState } from "react";
import { ApiError, deleteFieldLink, getSettings, listFieldLinks, saveSettings } from "./api";
import { Field, PartyFields, setPath } from "./InvoiceForm";
import type { AdminSettings, Company, FieldLink } from "./types";

const MAX_COMPANIES = 10;

function message(err: unknown): string {
  if (err instanceof ApiError) return [err.message, ...err.details].join(" – ");
  return String(err);
}

function emptyCompany(): Company {
  return {
    id: "",
    keywords: [],
    seller: {
      name: "",
      address: { line1: "", line2: "", postcode: "", city: "", country_code: "DE" },
      vat_id: "",
      tax_number: "",
      electronic_address: "",
      contact: { name: "", phone: "", email: "" },
      erp_id: "",
    },
    payment: { means_code: "58", iban: "", bic: "", account_holder: "", terms: "", due_date: null },
  };
}

/** Linked fields of one company (click-to-link in the PDF preview), with delete. */
function Links({ companyId }: { companyId: string }) {
  const [links, setLinks] = useState<FieldLink[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!companyId) return;
    listFieldLinks(companyId)
      .then(setLinks)
      .catch((e) => setError(message(e)));
  }, [companyId]);

  if (!companyId) return <p className="hint">Nach dem Speichern können Felder verknüpft werden.</p>;
  if (error) return <div className="alert error">{error}</div>;
  if (!links) return null;
  if (!links.length)
    return (
      <p className="hint">
        Noch keine Feldverknüpfungen. Beim Prüfen einer Rechnung: „Felder zuordnen“ in der PDF-Vorschau, Wert im PDF
        anklicken, dann das Feld im Formular.
      </p>
    );
  return (
    <table className="data compact">
      <thead>
        <tr>
          <th>Feld</th>
          <th>Beispielwert</th>
          <th>Seite</th>
          <th>Bezug (Beschriftung)</th>
          <th>Zuletzt</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {links.map((l) => (
          <tr key={l.id}>
            <td>{l.label}</td>
            <td>{l.sample || "–"}</td>
            <td>{l.page}</td>
            <td>{l.anchor_text || <span className="muted">feste Position</span>}</td>
            <td className="muted small">{l.updated_by}</td>
            <td>
              <button
                className="link danger"
                onClick={async () => {
                  if (!window.confirm(`Verknüpfung „${l.label}“ löschen? Das Feld wird dann wieder über die Regeln erkannt.`)) return;
                  try {
                    await deleteFieldLink(l.id);
                    setLinks((x) => x && x.filter((y) => y.id !== l.id));
                  } catch (e) {
                    setError(message(e));
                  }
                }}
              >
                Löschen
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Admin: own companies (master data of each invoice issuer) and their linked fields. */
export function Companies() {
  const [settings, setSettings] = useState<AdminSettings | null>(null);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [savedAt, setSavedAt] = useState<string>("");

  useEffect(() => {
    getSettings()
      .then(setSettings)
      .catch((e) => setError(message(e)));
  }, []);

  if (!settings) return error ? <div className="alert error">{error}</div> : <p className="hint">Lädt …</p>;
  const update = (path: string, value: unknown) => setSettings((s) => s && setPath(s, path, value));

  async function save() {
    setError("");
    setInfo("");
    try {
      setSettings(await saveSettings(settings!));
      setSavedAt(new Date().toISOString());
      setInfo("Gespeichert. Neue Rechnungen werden der passenden Firma automatisch zugeordnet.");
    } catch (e) {
      setError(message(e));
    }
  }

  const companies = settings.companies;
  return (
    <div className="form">
      <p className="hint">
        Jede eigene Firma (Rechnungssteller) hat eigene Stammdaten und eigene Feldverknüpfungen. Eingehende Rechnungen
        werden über <strong>USt-IdNr., Steuernummer, IBAN, Erkennungsmerkmale</strong> und Namen der richtigen Firma
        zugeordnet (SAP-IDocs über die eigene USt-IdNr.). Die erste Firma ist der Standard.
      </p>
      {companies.map((c, i) => {
        const p = `companies.${i}`;
        return (
          <section key={c.id || `neu-${i}`} className="company-card">
            <div className="section-head">
              <h2>
                {c.seller.name || "Neue Firma"} {i === 0 && <span className="badge">Standard</span>}
              </h2>
              <div className="toolbar">
                {i > 0 && (
                  <button
                    className="link"
                    onClick={() => {
                      const next = [...companies];
                      [next[i - 1], next[i]] = [next[i], next[i - 1]];
                      update("companies", next);
                    }}
                  >
                    {i === 1 ? "Als Standard" : "Nach oben"}
                  </button>
                )}
                {companies.length > 1 && (
                  <button
                    className="link danger"
                    onClick={() => {
                      if (window.confirm(`„${c.seller.name || "Neue Firma"}“ entfernen? Ihre Feldverknüpfungen werden beim Speichern gelöscht.`))
                        update(
                          "companies",
                          companies.filter((_, j) => j !== i),
                        );
                    }}
                  >
                    Entfernen
                  </button>
                )}
              </div>
            </div>
            <PartyFields prefix={`${p}.seller`} party={c.seller} update={update} issues={[]} seller />
            <h3 className="subhead">Bankverbindung & Zahlungsbedingungen</h3>
            <div className="grid">
              <Field label="IBAN" path={`${p}.payment.iban`} value={c.payment.iban} update={update} issues={[]} />
              <Field label="BIC" path={`${p}.payment.bic`} value={c.payment.bic} update={update} issues={[]} />
              <Field label="Kontoinhaber" path={`${p}.payment.account_holder`} value={c.payment.account_holder} update={update} issues={[]} />
              <Field label="Standard-Zahlungsbedingungen" path={`${p}.payment.terms`} value={c.payment.terms} update={update} issues={[]} wide />
              <label className="field wide">
                <span>Erkennungsmerkmale (optional, durch Komma getrennt – z. B. Text aus dem Briefkopf)</span>
                <input
                  value={c.keywords.join(", ")}
                  onChange={(e) =>
                    update(
                      `${p}.keywords`,
                      e.target.value.split(",").map((k) => k.trimStart()),
                    )
                  }
                  onBlur={(e) =>
                    update(
                      `${p}.keywords`,
                      e.target.value
                        .split(",")
                        .map((k) => k.trim())
                        .filter(Boolean),
                    )
                  }
                />
              </label>
            </div>
            <h3 className="subhead">Feldverknüpfungen</h3>
            <Links key={`${c.id}-${savedAt}`} companyId={c.id} />
          </section>
        );
      })}
      {companies.length < MAX_COMPANIES && (
        <div>
          <button className="secondary" onClick={() => update("companies", [...companies, emptyCompany()])}>
            + Weitere Firma
          </button>
        </div>
      )}
      {error && (
        <div className="alert error" role="alert">
          {error}
        </div>
      )}
      {info && <div className="alert info">{info}</div>}
      <div>
        <button className="primary inline" onClick={save}>
          Firmen speichern
        </button>
      </div>
    </div>
  );
}
