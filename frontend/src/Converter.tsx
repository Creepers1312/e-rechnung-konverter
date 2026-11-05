import { useEffect, useRef, useState } from "react";
import { exportInvoice, validateInvoice } from "./api";
import { Archive } from "./Archive";
import { type BatchItem, BatchList, type Loaded, loadFile, processItem } from "./Batch";
import { InvoiceForm, setPath } from "./InvoiceForm";
import { PdfPreview } from "./PdfPreview";
import type { Invoice, Profile, ValidateResponse } from "./types";
import { describe, download, eur } from "./util";

const ACCEPT = ".pdf,.xml,application/pdf,application/xml,text/xml";

function initialPreview(): boolean {
  try {
    return localStorage.getItem("erechnung.preview") !== "off";
  } catch {
    return true;
  }
}

export function Converter() {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [profile, setProfile] = useState<Profile>("zugferd");
  const [check, setCheck] = useState<ValidateResponse | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [archiveKey, setArchiveKey] = useState(0);
  const [preview, setPreview] = useState(initialPreview);
  const [batch, setBatch] = useState<BatchItem[] | null>(null);
  const [batchRunning, setBatchRunning] = useState(false);
  const [openBatchId, setOpenBatchId] = useState<number | null>(null);
  const editorRef = useRef<HTMLDivElement>(null);

  // Re-validate (debounced) whenever the invoice or profile changes.
  useEffect(() => {
    if (!invoice) return;
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      validateInvoice(invoice, profile, loaded?.stated ?? null, ctrl.signal)
        .then(setCheck)
        .catch((e) => {
          if (e.name !== "AbortError") setErrors(describe(e));
        });
    }, 300);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [invoice, profile, loaded]);

  function open(l: Loaded, batchId: number | null = null) {
    setLoaded(l);
    setInvoice(l.invoice);
    setCheck(null);
    setOpenBatchId(batchId);
    setTimeout(() => editorRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  }

  async function loadSingle(file: File) {
    setErrors([]);
    setBusy(true);
    try {
      open(await loadFile(file));
    } catch (e) {
      setErrors(describe(e));
    } finally {
      setBusy(false);
    }
  }

  async function runBatch(files: File[]) {
    setErrors([]);
    const items: BatchItem[] = files.map((file, i) => ({ id: Date.now() + i, file, status: "wartet" }));
    setBatch(items);
    setBatchRunning(true);
    for (const item of items) {
      setBatch((b) => b && b.map((x) => (x.id === item.id ? { ...x, status: "läuft" } : x)));
      const result = await processItem(item, profile);
      setBatch((b) => b && b.map((x) => (x.id === item.id ? result : x)));
    }
    setBatchRunning(false);
    setArchiveKey((k) => k + 1);
  }

  function handleFiles(list: FileList | null) {
    const files = Array.from(list ?? []);
    if (files.length === 1) void loadSingle(files[0]);
    else if (files.length > 1) void runBatch(files);
  }

  async function doExport() {
    if (!invoice || !loaded) return;
    setErrors([]);
    setBusy(true);
    try {
      const res = await exportInvoice(invoice, profile, loaded.kind === "pdf" ? loaded.file : null, {
        kind: loaded.kind,
        name: loaded.file.name,
      });
      const base = invoice.number.replace(/[^\w-]/g, "_") || "rechnung";
      download(res.blob, profile === "xrechnung" ? `${base}_xrechnung.xml` : `${base}_zugferd.pdf`);
      setArchiveKey((k) => k + 1);
      if (openBatchId !== null) {
        const gross = check?.totals.grand_total;
        setBatch(
          (b) =>
            b &&
            b.map((x) =>
              x.id === openBatchId
                ? { ...x, status: "fertig", documentId: res.documentId, message: undefined, grossTotal: gross }
                : x,
            ),
        );
      }
    } catch (e) {
      setErrors(describe(e));
    } finally {
      setBusy(false);
    }
  }

  function togglePreview() {
    setPreview((p) => {
      try {
        localStorage.setItem("erechnung.preview", p ? "off" : "on");
      } catch {
        /* storage unavailable: setting just isn't remembered */
      }
      return !p;
    });
  }

  const issues = check?.issues ?? [];
  const errorCount = issues.filter((i) => i.severity === "error").length;
  const showPreview = preview && loaded?.kind === "pdf";

  return (
    <div>
      <p className="intro">
        PDF-Rechnungen oder SAP-IDocs hochladen, Daten prüfen, als ZUGFeRD oder XRechnung exportieren. Mehrere Dateien
        auf einmal werden als Stapel verarbeitet.
      </p>

      <div
        className="drop"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          if (!batchRunning) handleFiles(e.dataTransfer.files);
        }}
      >
        <label className={`button${batchRunning ? " disabled" : ""}`}>
          Dateien wählen
          <input
            type="file"
            accept={ACCEPT}
            multiple
            hidden
            disabled={batchRunning}
            onChange={(e) => {
              handleFiles(e.target.files);
              e.target.value = "";
            }}
          />
        </label>
        <span>oder hierher ziehen · PDF-Rechnungen oder INVOIC02-IDocs (.xml), auch mehrere</span>
        {loaded && <strong className="filename">{loaded.file.name}</strong>}
        <span className="drop-format">
          Format:
          <select value={profile} onChange={(e) => setProfile(e.target.value as Profile)} disabled={batchRunning}>
            <option value="zugferd">ZUGFeRD (PDF)</option>
            <option value="xrechnung">XRechnung (XML)</option>
          </select>
        </span>
      </div>

      {errors.length > 0 && (
        <div className="alert error" role="alert">
          {errors.map((e, i) => (
            <div key={i}>{e}</div>
          ))}
        </div>
      )}

      {batch && (
        <BatchList
          items={batch}
          profile={profile}
          running={batchRunning}
          onOpen={(item) => item.loaded && open(item.loaded, item.id)}
          onClose={() => {
            setBatch(null);
            setOpenBatchId(null);
          }}
        />
      )}

      {invoice && loaded && (
        <main ref={editorRef} className={showPreview ? "with-preview" : ""}>
          {showPreview && <PdfPreview file={loaded.file} />}
          <div>
            <div className="alert info">
              <div className="info-row">
                <span>
                  Erkannt mit: <strong>{loaded.templateName}</strong>
                </span>
                {loaded.kind === "pdf" && (
                  <button className="secondary small" onClick={togglePreview}>
                    {preview ? "PDF-Vorschau ausblenden" : "PDF-Vorschau anzeigen"}
                  </button>
                )}
              </div>
              {loaded.notes.map((n, i) => (
                <div key={`n${i}`}>ℹ {n}</div>
              ))}
              {loaded.warnings.map((w, i) => (
                <div key={`w${i}`}>⚠ {w}</div>
              ))}
            </div>
            <InvoiceForm
              invoice={invoice}
              update={(path, value) => setInvoice((inv) => inv && setPath(inv, path, value))}
              issues={issues}
            />
          </div>

          <aside>
            <div className="card">
              <h2>Format</h2>
              <div className="segmented" role="radiogroup">
                {(["zugferd", "xrechnung"] as Profile[]).map((p) => (
                  <button
                    key={p}
                    role="radio"
                    aria-checked={profile === p}
                    className={profile === p ? "active" : ""}
                    onClick={() => setProfile(p)}
                  >
                    {p === "zugferd" ? "ZUGFeRD (PDF)" : "XRechnung (XML)"}
                  </button>
                ))}
              </div>
              <p className="hint">
                {profile === "zugferd"
                  ? loaded.kind === "pdf"
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
                      <dt>
                        USt. {Number(b.rate).toLocaleString("de-DE")} % ({b.category})
                      </dt>
                      <dd>{eur(b.amount)}</dd>
                    </div>
                  ))}
                  <dt className="strong">Brutto</dt>
                  <dd className="strong">{eur(check.totals.grand_total)}</dd>
                  {loaded.stated?.gross && (
                    <>
                      <dt className="muted">laut PDF</dt>
                      <dd className="muted">{eur(loaded.stated.gross)}</dd>
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
                {errorCount > 0
                  ? `${errorCount} Fehler beheben`
                  : profile === "zugferd"
                    ? "ZUGFeRD-PDF herunterladen"
                    : "XRechnung herunterladen"}
              </button>
            </div>
          </aside>
        </main>
      )}

      <Archive refreshKey={archiveKey} />
    </div>
  );
}
