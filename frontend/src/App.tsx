import { useEffect, useState } from "react";
import { ApiError, exportInvoice, extractPdf, importIdoc, validateInvoice } from "./api";
import { Archive } from "./Archive";
import { InvoiceForm, setPath } from "./InvoiceForm";
import type { Invoice, Profile, StatedTotals, ValidateResponse } from "./types";

type Source = { kind: "pdf"; file: File } | { kind: "idoc"; file: File };

function eur(value: string | null | undefined): string {
  if (value == null) return "–";
  return Number(value).toLocaleString("de-DE", { style: "currency", currency: "EUR" });
}

function describe(err: unknown): string[] {
  if (err instanceof ApiError) return [err.message, ...err.details];
  return [String(err)];
}

function download(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function App() {
  const [source, setSource] = useState<Source | null>(null);
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [stated, setStated] = useState<StatedTotals | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [templateName, setTemplateName] = useState("");
  const [profile, setProfile] = useState<Profile>("zugferd");
  const [check, setCheck] = useState<ValidateResponse | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [archiveKey, setArchiveKey] = useState(0);

  // Re-validate (debounced) whenever the invoice or profile changes.
  useEffect(() => {
    if (!invoice) return;
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      validateInvoice(invoice, profile, stated, ctrl.signal)
        .then(setCheck)
        .catch((e) => {
          if (e.name !== "AbortError") setErrors(describe(e));
        });
    }, 300);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [invoice, profile, stated]);

  async function load(file: File) {
    setErrors([]);
    setBusy(true);
    setCheck(null);
    try {
      if (file.name.toLowerCase().endsWith(".xml")) {
        setInvoice(await importIdoc(file));
        setStated(null);
        setWarnings([]);
        setTemplateName("SAP ECC IDoc (INVOIC02)");
        setSource({ kind: "idoc", file });
      } else {
        const r = await extractPdf(file);
        setInvoice(r.invoice);
        setStated(r.stated_totals);
        setWarnings(r.warnings);
        setTemplateName(r.template_name);
        setSource({ kind: "pdf", file });
      }
    } catch (e) {
      setErrors(describe(e));
    } finally {
      setBusy(false);
    }
  }

  async function doExport() {
    if (!invoice) return;
    setErrors([]);
    setBusy(true);
    try {
      const blob = await exportInvoice(
        invoice,
        profile,
        source?.kind === "pdf" ? source.file : null,
        { kind: source?.kind ?? "manual", name: source?.file.name ?? "" },
      );
      const base = invoice.number.replace(/[^\w-]/g, "_") || "rechnung";
      download(blob, profile === "xrechnung" ? `${base}_xrechnung.xml` : `${base}_zugferd.pdf`);
      setArchiveKey((k) => k + 1);
    } catch (e) {
      setErrors(describe(e));
    } finally {
      setBusy(false);
    }
  }

  const issues = check?.issues ?? [];
  const errorCount = issues.filter((i) => i.severity === "error").length;

  return (
    <div className="app">
      <header>
        <h1>E-Rechnung Konverter</h1>
        <p>PDF-Rechnung oder SAP-IDoc hochladen, Daten prüfen, als ZUGFeRD oder XRechnung exportieren.</p>
      </header>

      <div
        className="drop"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          const file = e.dataTransfer.files[0];
          if (file) void load(file);
        }}
      >
        <label className="button">
          Datei wählen
          <input
            type="file"
            accept=".pdf,.xml,application/pdf,application/xml,text/xml"
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void load(file);
              e.target.value = "";
            }}
          />
        </label>
        <span>oder hierher ziehen · PDF-Rechnung oder INVOIC02-IDoc (.xml)</span>
        {source && <strong className="filename">{source.file.name}</strong>}
      </div>

      {errors.length > 0 && (
        <div className="alert error" role="alert">
          {errors.map((e, i) => (
            <div key={i}>{e}</div>
          ))}
        </div>
      )}

      {invoice && (
        <main>
          <div>
            {(warnings.length > 0 || templateName) && (
              <div className="alert info">
                <div>Erkannt mit: <strong>{templateName}</strong></div>
                {warnings.map((w, i) => (
                  <div key={i}>⚠ {w}</div>
                ))}
              </div>
            )}
            <InvoiceForm invoice={invoice} update={(path, value) => setInvoice((inv) => inv && setPath(inv, path, value))} issues={issues} />
          </div>

          <aside>
            <div className="card">
              <h2>Format</h2>
              <div className="segmented" role="radiogroup">
                {(["zugferd", "xrechnung"] as Profile[]).map((p) => (
                  <button key={p} role="radio" aria-checked={profile === p} className={profile === p ? "active" : ""} onClick={() => setProfile(p)}>
                    {p === "zugferd" ? "ZUGFeRD (PDF)" : "XRechnung (XML)"}
                  </button>
                ))}
              </div>
              <p className="hint">
                {profile === "zugferd"
                  ? source?.kind === "pdf"
                    ? "Das hochgeladene PDF bekommt die XML-Daten eingebettet."
                    : "Es wird ein einfaches Rechnungs-PDF erzeugt und die XML-Daten eingebettet."
                  : "Reines XML, z. B. für Behörden (Leitweg-ID nötig)."}
              </p>
            </div>

            {check && (
              <div className="card">
                <h2>Summen</h2>
                <dl className="totals">
                  <dt>Netto</dt>
                  <dd>{eur(check.totals.line_total)}</dd>
                  {check.totals.breakdown.map((b) => (
                    <div key={`${b.category}${b.rate}`} className="row">
                      <dt>USt. {Number(b.rate).toLocaleString("de-DE")} % ({b.category})</dt>
                      <dd>{eur(b.amount)}</dd>
                    </div>
                  ))}
                  <dt className="strong">Brutto</dt>
                  <dd className="strong">{eur(check.totals.grand_total)}</dd>
                  {stated?.gross && (
                    <>
                      <dt className="muted">laut PDF</dt>
                      <dd className="muted">{eur(stated.gross)}</dd>
                    </>
                  )}
                </dl>
              </div>
            )}

            <div className="card">
              <h2>Prüfung</h2>
              {!check ? (
                <p className="hint">Wird geprüft …</p>
              ) : issues.length === 0 ? (
                <p className="ok">✓ Keine Fehler gefunden</p>
              ) : (
                <ul className="issues">
                  {issues.map((i, k) => (
                    <li key={k} className={i.severity}>
                      <code>{i.code}</code> {i.message}
                    </li>
                  ))}
                </ul>
              )}
              <button className="primary" disabled={busy || !check || errorCount > 0} onClick={doExport}>
                {errorCount > 0 ? `${errorCount} Fehler beheben` : profile === "zugferd" ? "ZUGFeRD-PDF herunterladen" : "XRechnung herunterladen"}
              </button>
            </div>
          </aside>
        </main>
      )}

      <Archive refreshKey={archiveKey} />
    </div>
  );
}
