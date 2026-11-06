import { useEffect, useRef, useState } from "react";
import { ApiError, deleteFieldLink, getSettings, listFieldLinks, saveSettings } from "./api";
import { CountrySelect } from "./CountrySelect";
import { Field, setPath } from "./InvoiceForm";
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

const VAT_ID = /^[A-Z]{2}[0-9A-Z]{8,12}$/;

function ibanValid(iban: string): boolean {
  const s = iban.replace(/\s+/g, "").toUpperCase();
  if (!/^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$/.test(s)) return false;
  const digits = (s.slice(4) + s.slice(0, 4)).replace(/[A-Z]/g, (ch) => String(ch.charCodeAt(0) - 55));
  let rest = 0;
  for (const d of digits) rest = (rest * 10 + Number(d)) % 97;
  return rest === 1;
}

/** Plausibility hints for a company's master data (not blocking). */
function hints(c: Company): string[] {
  const out: string[] = [];
  const vat = c.seller.vat_id.replace(/\s+/g, "").toUpperCase();
  const tax = c.seller.tax_number.replace(/\s+/g, "").toUpperCase();
  if (!c.seller.name.trim()) out.push("Name fehlt.");
  if (!vat && VAT_ID.test(tax))
    out.push(`„${c.seller.tax_number}“ steht unter Steuernummer, sieht aber wie eine USt-IdNr. aus – bitte ins Feld USt-IdNr. verschieben.`);
  else if (!vat && !tax) out.push("USt-IdNr. oder Steuernummer fehlt (für E-Rechnungen Pflicht).");
  else if (vat && !VAT_ID.test(vat)) out.push(`USt-IdNr. „${c.seller.vat_id}“ hat kein gültiges Format (z. B. DE123456789).`);
  if (!c.seller.address.postcode || !c.seller.address.city) out.push("PLZ oder Ort fehlt.");
  if (c.payment.iban && !ibanValid(c.payment.iban)) out.push("IBAN-Prüfsumme stimmt nicht.");
  if (!c.payment.iban) out.push("IBAN fehlt (für Überweisung nötig).");
  return out;
}

/** Linked fields of one company, with delete. */
function Links({ companyId }: { companyId: string }) {
  const [links, setLinks] = useState<FieldLink[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!companyId) return;
    listFieldLinks(companyId)
      .then(setLinks)
      .catch((e) => setError(message(e)));
  }, [companyId]);

  if (!companyId) return <p className="hint">Nach dem ersten Speichern können Felder verknüpft werden.</p>;
  if (error) return <div className="alert error">{error}</div>;
  if (!links) return null;
  if (!links.length)
    return (
      <p className="hint">
        Noch keine. Beim Prüfen einer Rechnung: „Felder zuordnen“ in der PDF-Vorschau, Wert im PDF anklicken, dann das
        Feld im Formular.
      </p>
    );
  return (
    <ul className="link-list">
      {links.map((l) => (
        <li key={l.id}>
          <div>
            <strong>{l.label}</strong>
            <span className="muted small">
              Seite {l.page} · {l.anchor_text ? `neben „${l.anchor_text}“` : "feste Position"} · {l.updated_by}
            </span>
            <span className="sample" title={l.sample}>
              {l.sample || "–"}
            </span>
          </div>
          <button
            type="button"
            className="link danger"
            onClick={async () => {
              if (!window.confirm(`Zuordnung „${l.label}“ löschen? Das Feld wird dann wieder über die Regeln erkannt.`)) return;
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
        </li>
      ))}
    </ul>
  );
}

/** Modal editor for one company (native <dialog>: focus trap, Esc closes). */
function CompanyDialog({
  company,
  isNew,
  onSave,
  onClose,
}: {
  company: Company;
  isNew: boolean;
  onSave: (c: Company) => Promise<void>;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [draft, setDraft] = useState<Company>(() => structuredClone(company));
  const [keywords, setKeywords] = useState(company.keywords.join(", "));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    ref.current?.showModal();
  }, []);

  const update = (path: string, value: unknown) => setDraft((d) => setPath(d, path, value));
  const f = (label: string, path: string, value: string, extra: { wide?: boolean; type?: "email"; placeholder?: string } = {}) => (
    <Field label={label} path={path} value={value} update={update} issues={[]} {...extra} />
  );
  const s = draft.seller;
  const warn = hints(draft);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await onSave({
        ...draft,
        keywords: keywords
          .split(",")
          .map((k) => k.trim())
          .filter(Boolean),
      });
      ref.current?.close();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <dialog ref={ref} className="modal" onClose={onClose} aria-labelledby="company-dialog-title">
      <form onSubmit={save}>
        <header className="modal-head">
          <h2 id="company-dialog-title">{isNew ? "Neue Firma" : s.name || "Firma bearbeiten"}</h2>
          <button type="button" className="icon" aria-label="Schließen" onClick={() => ref.current?.close()}>
            ×
          </button>
        </header>
        <div className="modal-body">
          <fieldset>
            <legend>Firma & Anschrift</legend>
            <div className="grid">
              {f("Name / Firma", "seller.name", s.name, { wide: true })}
              {f("Straße, Nr.", "seller.address.line1", s.address.line1, { wide: true })}
              {f("Adresszusatz", "seller.address.line2", s.address.line2, { wide: true })}
              {f("PLZ", "seller.address.postcode", s.address.postcode)}
              {f("Ort", "seller.address.city", s.address.city)}
              <CountrySelect path="seller.address.country_code" value={s.address.country_code} update={update} issues={[]} />
            </div>
          </fieldset>
          <fieldset>
            <legend>Steuer & E-Rechnung</legend>
            <div className="grid">
              {f("USt-IdNr.", "seller.vat_id", s.vat_id, { placeholder: "DE123456789" })}
              {f("Steuernummer (falls keine USt-IdNr.)", "seller.tax_number", s.tax_number)}
              {f("E-Rechnungs-Adresse (E-Mail)", "seller.electronic_address", s.electronic_address, { type: "email", wide: true })}
            </div>
          </fieldset>
          <fieldset>
            <legend>Ansprechpartner</legend>
            <div className="grid">
              {f("Name", "seller.contact.name", s.contact.name)}
              {f("Telefon", "seller.contact.phone", s.contact.phone)}
              {f("E-Mail", "seller.contact.email", s.contact.email, { type: "email" })}
            </div>
          </fieldset>
          <fieldset>
            <legend>Bankverbindung & Zahlung</legend>
            <div className="grid">
              {f("IBAN", "payment.iban", draft.payment.iban, { wide: true })}
              {f("BIC", "payment.bic", draft.payment.bic)}
              {f("Kontoinhaber", "payment.account_holder", draft.payment.account_holder)}
              {f("Standard-Zahlungsbedingungen", "payment.terms", draft.payment.terms, { wide: true })}
            </div>
          </fieldset>
          <fieldset>
            <legend>Erkennung</legend>
            <label className="field wide">
              <span>Erkennungsmerkmale (optional, durch Komma getrennt – z. B. Text aus dem Briefkopf)</span>
              <input value={keywords} onChange={(e) => setKeywords(e.target.value)} />
            </label>
            <p className="hint">USt-IdNr., Steuernummer, IBAN und Name werden automatisch zum Erkennen genutzt.</p>
          </fieldset>
          {!isNew && (
            <fieldset>
              <legend>Feldzuordnungen</legend>
              <Links companyId={draft.id} />
            </fieldset>
          )}
        </div>
        <footer className="modal-foot">
          {warn.length > 0 && (
            <ul className="hint-list" aria-live="polite">
              {warn.map((w) => (
                <li key={w}>⚠ {w}</li>
              ))}
            </ul>
          )}
          {error && <div className="alert error">{error}</div>}
          <div className="modal-actions">
            <button type="button" className="secondary" onClick={() => ref.current?.close()}>
              Abbrechen
            </button>
            <button className="primary inline" disabled={busy || !s.name.trim()}>
              {busy ? "Speichere …" : "Speichern"}
            </button>
          </div>
        </footer>
      </form>
    </dialog>
  );
}

function CompanyCard({
  c,
  index,
  count,
  onEdit,
  onDefault,
  onRemove,
}: {
  c: Company;
  index: number;
  count: number;
  onEdit: () => void;
  onDefault: () => void;
  onRemove: (() => void) | null;
}) {
  const [links, setLinks] = useState<number | null>(null);
  useEffect(() => {
    if (c.id) listFieldLinks(c.id).then((l) => setLinks(l.length)).catch(() => undefined);
  }, [c.id]);
  const s = c.seller;
  const warn = hints(c);
  return (
    <article className="company-tile">
      <header>
        <h3>{s.name || "Ohne Namen"}</h3>
        {index === 0 && <span className="badge">Standard</span>}
      </header>
      <dl>
        <dt>Anschrift</dt>
        <dd>
          {s.address.line1 || "–"}
          <br />
          {[s.address.postcode, s.address.city].filter(Boolean).join(" ") || "–"} {s.address.country_code && `(${s.address.country_code})`}
        </dd>
        <dt>USt-IdNr.</dt>
        <dd>{s.vat_id || <span className="missing">fehlt</span>}</dd>
        <dt>IBAN</dt>
        <dd className="mono">{c.payment.iban || <span className="missing">fehlt</span>}</dd>
        <dt>E-Rechnung</dt>
        <dd>{s.electronic_address || "–"}</dd>
        <dt>Zuordnungen</dt>
        <dd>{c.id ? (links === null ? "…" : `${links} Feld${links === 1 ? "" : "er"}`) : "–"}</dd>
      </dl>
      {warn.length > 0 && <p className="tile-warn">⚠ {warn.length === 1 ? warn[0] : `${warn.length} Hinweise – bitte prüfen`}</p>}
      <footer>
        <button className="secondary small" onClick={onEdit}>
          Bearbeiten
        </button>
        {index > 0 && count > 1 && (
          <button className="link" onClick={onDefault}>
            Als Standard
          </button>
        )}
        {onRemove && (
          <button className="link danger" onClick={onRemove}>
            Entfernen
          </button>
        )}
      </footer>
    </article>
  );
}

/** Admin: own companies (invoice issuers) as cards; editing in a dialog. Every change is saved right away. */
export function Companies() {
  const [settings, setSettings] = useState<AdminSettings | null>(null);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [editing, setEditing] = useState<{ index: number; company: Company } | null>(null);
  const [version, setVersion] = useState(0); // refreshes the link counts on the cards

  useEffect(() => {
    getSettings()
      .then(setSettings)
      .catch((e) => setError(message(e)));
  }, []);

  if (!settings) return error ? <div className="alert error">{error}</div> : <p className="hint">Lädt …</p>;
  const companies = settings.companies;

  async function persist(next: Company[], done: string) {
    setError("");
    const saved = await saveSettings({ ...settings!, companies: next });
    setSettings(saved);
    setInfo(done);
  }

  const run = (next: Company[], done: string) => persist(next, done).catch((e) => setError(message(e)));

  return (
    <div className="companies">
      <p className="hint">
        Jede eigene Firma (Rechnungssteller) hat eigene Stammdaten und Feldzuordnungen. Rechnungen werden über{" "}
        <strong>USt-IdNr., Steuernummer, IBAN, Erkennungsmerkmale</strong> und Namen automatisch der richtigen Firma
        zugeordnet (SAP-IDocs über die eigene USt-IdNr.). Die erste Firma ist der Standard.
      </p>
      {error && <div className="alert error">{error}</div>}
      {info && <div className="alert info">{info}</div>}
      <div className="company-grid">
        {companies.map((c, i) => (
          <CompanyCard
            key={`${c.id || `neu-${i}`}-${version}`}
            c={c}
            index={i}
            count={companies.length}
            onEdit={() => setEditing({ index: i, company: c })}
            onDefault={() => {
              const next = [c, ...companies.filter((_, j) => j !== i)];
              void run(next, `„${c.seller.name}“ ist jetzt die Standard-Firma.`);
            }}
            onRemove={
              companies.length > 1
                ? () => {
                    if (
                      window.confirm(
                        `„${c.seller.name || "Firma"}“ entfernen? Ihre Feldzuordnungen werden ebenfalls gelöscht.`,
                      )
                    )
                      void run(
                        companies.filter((_, j) => j !== i),
                        `„${c.seller.name}“ entfernt.`,
                      );
                  }
                : null
            }
          />
        ))}
        {companies.length < MAX_COMPANIES && (
          <button className="company-tile add" onClick={() => setEditing({ index: companies.length, company: emptyCompany() })}>
            <span aria-hidden>＋</span>
            Firma hinzufügen
          </button>
        )}
      </div>
      {editing && (
        <CompanyDialog
          company={editing.company}
          isNew={editing.index >= companies.length}
          onClose={() => {
            setEditing(null);
            setVersion((v) => v + 1);
          }}
          onSave={async (c) => {
            const next = [...companies];
            next[editing.index] = c;
            await persist(next, `„${c.seller.name}“ gespeichert.`);
          }}
        />
      )}
    </div>
  );
}
