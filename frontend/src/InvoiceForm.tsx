import { BuyerLookup } from "./BuyerLookup";
import type { Invoice, Issue, LineItem, Party, VatCategory } from "./types";

export type Update = (path: string, value: unknown) => void;

/** Immutably set `obj[a][b][c] = value` for path "a.b.c" (numeric parts index arrays). */
export function setPath<T>(obj: T, path: string, value: unknown): T {
  const copy = structuredClone(obj);
  const parts = path.split(".");
  let cur: Record<string, unknown> = copy as Record<string, unknown>;
  for (const p of parts.slice(0, -1)) cur = cur[p] as Record<string, unknown>;
  cur[parts[parts.length - 1]] = value;
  return copy;
}

function hasError(issues: Issue[], path: string): Issue | undefined {
  return issues.find((i) => i.severity === "error" && i.field && (i.field === path || path.startsWith(i.field + ".")));
}

export interface FieldProps {
  label: string;
  path: string;
  value: string | null;
  update: Update;
  issues: Issue[];
  type?: "text" | "date" | "email";
  wide?: boolean;
  placeholder?: string;
}

export function Field({ label, path, value, update, issues, type = "text", wide, placeholder }: FieldProps) {
  const error = hasError(issues, path);
  return (
    <label className={`field${wide ? " wide" : ""}${error ? " invalid" : ""}`} title={error?.message}>
      <span>{label}</span>
      <input
        data-path={path}
        type={type}
        value={value ?? ""}
        placeholder={placeholder}
        onChange={(e) => update(path, type === "date" ? e.target.value || null : e.target.value)}
      />
    </label>
  );
}

export function PartyFields({ prefix, party, update, issues, seller }: {
  prefix: string;
  party: Party;
  update: Update;
  issues: Issue[];
  seller?: boolean;
}) {
  const f = (label: string, path: string, value: string, extra: Partial<FieldProps> = {}) => (
    <Field label={label} path={`${prefix}.${path}`} value={value} update={update} issues={issues} {...extra} />
  );
  return (
    <div className="grid">
      {f("Name / Firma", "name", party.name, { wide: true })}
      {f("Straße, Nr.", "address.line1", party.address.line1, { wide: true })}
      {f("Adresszusatz", "address.line2", party.address.line2, { wide: true })}
      {f("PLZ", "address.postcode", party.address.postcode)}
      {f("Ort", "address.city", party.address.city)}
      {f("Land (ISO)", "address.country_code", party.address.country_code)}
      {f("USt-IdNr.", "vat_id", party.vat_id)}
      {seller && f("Steuernummer", "tax_number", party.tax_number)}
      {f("E-Rechnungs-Adresse (E-Mail)", "electronic_address", party.electronic_address, { type: "email", wide: true })}
      {seller && f("Kontakt Name", "contact.name", party.contact.name)}
      {seller && f("Kontakt Telefon", "contact.phone", party.contact.phone)}
      {seller && f("Kontakt E-Mail", "contact.email", party.contact.email, { type: "email" })}
      {!seller && f("Kundennummer (ERP)", "erp_id", party.erp_id)}
    </div>
  );
}

const UNITS: [string, string][] = [
  ["H87", "Stück"],
  ["C62", "Einheit"],
  ["HUR", "Stunde"],
  ["DAY", "Tag"],
  ["KGM", "kg"],
  ["MTR", "Meter"],
  ["LTR", "Liter"],
  ["LS", "Pauschale"],
];

const CATEGORIES: [VatCategory, string][] = [
  ["S", "Normal"],
  ["Z", "Nullsatz"],
  ["E", "Steuerfrei"],
  ["AE", "Reverse Charge"],
  ["K", "Innergem. Lieferung"],
  ["G", "Export"],
];

function LinesTable({ lines, update, issues }: { lines: LineItem[]; update: Update; issues: Issue[] }) {
  const add = () =>
    update("lines", [
      ...lines,
      { id: String(lines.length + 1), name: "", seller_item_id: "", quantity: "1", unit_code: "H87", unit_price: "0", vat_category: "S", vat_rate: "19" },
    ]);
  const remove = (i: number) => update("lines", lines.filter((_, j) => j !== i));
  const cell = (i: number, key: keyof LineItem, extra: { className?: string } = {}) => {
    const path = `lines.${i}.${key}`;
    return (
      <input
        data-path={path}
        className={`${extra.className ?? ""}${hasError(issues, path) ? " invalid" : ""}`}
        value={lines[i][key]}
        onChange={(e) => update(path, e.target.value)}
      />
    );
  };
  return (
    <div className="lines">
      <table>
        <thead>
          <tr>
            <th>Pos</th>
            <th>Artikel-Nr.</th>
            <th>Bezeichnung</th>
            <th>Menge</th>
            <th>Einheit</th>
            <th>Einzelpreis netto</th>
            <th>Steuer</th>
            <th>%</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {lines.map((l, i) => (
            <tr key={i}>
              <td>{cell(i, "id", { className: "xs" })}</td>
              <td>{cell(i, "seller_item_id", { className: "sm" })}</td>
              <td>{cell(i, "name")}</td>
              <td>{cell(i, "quantity", { className: "num" })}</td>
              <td>
                <select value={l.unit_code} onChange={(e) => update(`lines.${i}.unit_code`, e.target.value)}>
                  {UNITS.map(([code, label]) => (
                    <option key={code} value={code}>{label}</option>
                  ))}
                  {!UNITS.some(([c]) => c === l.unit_code) && <option value={l.unit_code}>{l.unit_code}</option>}
                </select>
              </td>
              <td>{cell(i, "unit_price", { className: "num" })}</td>
              <td>
                <select value={l.vat_category} onChange={(e) => update(`lines.${i}.vat_category`, e.target.value)}>
                  {CATEGORIES.map(([code, label]) => (
                    <option key={code} value={code}>{label}</option>
                  ))}
                </select>
              </td>
              <td>{cell(i, "vat_rate", { className: "xs num" })}</td>
              <td>
                <button className="icon" onClick={() => remove(i)} aria-label={`Position ${i + 1} löschen`}>×</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button className="secondary" onClick={add}>+ Position</button>
    </div>
  );
}

export function InvoiceForm({ invoice, update, issues }: { invoice: Invoice; update: Update; issues: Issue[] }) {
  const f = (label: string, path: string, value: string | null, extra: Partial<FieldProps> = {}) => (
    <Field label={label} path={path} value={value} update={update} issues={issues} {...extra} />
  );
  return (
    <div className="form">
      <section>
        <h2>Rechnung</h2>
        <div className="grid">
          {f("Rechnungsnummer", "number", invoice.number)}
          {f("Rechnungsdatum", "issue_date", invoice.issue_date, { type: "date" })}
          {f("Liefer-/Leistungsdatum", "delivery_date", invoice.delivery_date, { type: "date" })}
          <label className="field">
            <span>Art</span>
            <select value={invoice.type_code} onChange={(e) => update("type_code", e.target.value)}>
              <option value="380">Rechnung</option>
              <option value="381">Gutschrift</option>
            </select>
          </label>
          {f("Leitweg-ID / Käuferreferenz", "buyer_reference", invoice.buyer_reference)}
          {f("Bestellnummer", "order_reference", invoice.order_reference)}
          {f("Bemerkung", "note", invoice.note, { wide: true })}
        </div>
      </section>
      <section>
        <div className="section-head">
          <h2>Käufer</h2>
          <BuyerLookup invoice={invoice} update={update} />
        </div>
        <PartyFields prefix="buyer" party={invoice.buyer} update={update} issues={issues} />
      </section>
      <section>
        <h2>Positionen</h2>
        <LinesTable lines={invoice.lines} update={update} issues={issues} />
      </section>
      <section>
        <h2>Zahlung</h2>
        <div className="grid">
          {f("IBAN", "payment.iban", invoice.payment.iban)}
          {f("BIC", "payment.bic", invoice.payment.bic)}
          {f("Kontoinhaber", "payment.account_holder", invoice.payment.account_holder)}
          {f("Fällig am", "payment.due_date", invoice.payment.due_date, { type: "date" })}
          {f("Zahlungsbedingungen", "payment.terms", invoice.payment.terms, { wide: true })}
        </div>
      </section>
      <details open={issues.some((i) => i.severity === "error" && i.field.startsWith("seller")) || undefined}>
        <summary>Verkäufer (eigene Firma – aus Stammdaten)</summary>
        <PartyFields prefix="seller" party={invoice.seller} update={update} issues={issues} seller />
      </details>
    </div>
  );
}
