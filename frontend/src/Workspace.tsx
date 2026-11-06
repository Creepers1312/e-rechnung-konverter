import { useEffect, useRef, useState } from "react";
import {
  exportInvoice,
  getReview,
  getReviewPdf,
  listCompanies,
  listFieldLinks,
  listReview,
  saveFieldLink,
  validateInvoice,
} from "./api";
import { type BatchItem, BatchList, type Loaded, loadFile, processItem } from "./Batch";
import { type Country, countryCodeOf, loadCountries } from "./CountrySelect";
import { InvoiceForm, setPath } from "./InvoiceForm";
import { type Selection, valueFor } from "./linking";
import { PdfPreview } from "./PdfPreview";
import { navigate } from "./router";
import type { Company, FieldLink, Invoice, Issue, Profile, ValidateResponse } from "./types";
import { showToast } from "./toast";
import { describe, download, eur } from "./util";
import { onWorkspaceRequest } from "./workspace";

const ACCEPT = ".pdf,.xml,application/pdf,application/xml,text/xml";

// header fields that can be linked permanently (same list as backend field_links.LINKABLE)
const LINKABLE: Record<string, string> = {
  number: "Rechnungsnummer",
  issue_date: "Rechnungsdatum",
  delivery_date: "Liefer-/Leistungsdatum",
  buyer_reference: "Leitweg-ID / Käuferreferenz",
  order_reference: "Bestellnummer",
  note: "Bemerkung",
  "buyer.name": "Käufer: Name",
  "buyer.address.line1": "Käufer: Straße",
  "buyer.address.line2": "Käufer: Adresszusatz",
  "buyer.address.postcode": "Käufer: PLZ",
  "buyer.address.city": "Käufer: Ort",
  "buyer.vat_id": "Käufer: USt-IdNr.",
  "buyer.electronic_address": "Käufer: E-Rechnungs-Adresse",
  "buyer.erp_id": "Käufer: Kundennummer",
  "payment.terms": "Zahlungsbedingungen",
  "payment.due_date": "Fälligkeit",
};

function stored(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}
function store(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: the setting just isn't remembered */
  }
}

/** Scroll to and focus the form field an issue refers to (e.g. "buyer.address" -> first buyer address input). */
export function focusField(field: string) {
  if (!field) return;
  const el =
    document.querySelector<HTMLElement>(`[data-path="${field}"]`) ??
    document.querySelector<HTMLElement>(`[data-path^="${field}."]`);
  if (!el) return;
  el.closest("details")?.setAttribute("open", "");
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  setTimeout(() => el.focus({ preventScroll: true }), 250);
}

export function Workspace({ onChanged }: { onChanged: () => void }) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [profile, setProfile] = useState<Profile>(() => (stored("erechnung.profile", "zugferd") as Profile));
  const [check, setCheck] = useState<ValidateResponse | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState(() => stored("erechnung.preview", "on") !== "off");
  const [batch, setBatch] = useState<BatchItem[] | null>(null);
  const [batchRunning, setBatchRunning] = useState(false);
  const [openBatchId, setOpenBatchId] = useState<number | null>(null);
  const [dragging, setDragging] = useState(false);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [linkMode, setLinkMode] = useState(false);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [links, setLinks] = useState<FieldLink[]>([]);
  const [countries, setCountries] = useState<Country[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    loadCountries()
      .then(setCountries)
      .catch(() => undefined);
    listCompanies()
      .then(setCompanies)
      .catch(() => undefined);
  }, []);

  // saved links of the current company (shown as frames in the preview)
  const companyId = loaded?.companyId ?? "";
  useEffect(() => {
    setLinks([]);
    if (!companyId || !linkMode) return;
    listFieldLinks(companyId)
      .then(setLinks)
      .catch(() => undefined);
  }, [companyId, linkMode]);
  const company = companies.find((c) => c.id === companyId);
  const companyName = company?.seller.name || "diese Firma";

  /** Link mode: a value is selected in the PDF and a form field was clicked. */
  function assign(e: React.MouseEvent) {
    if (!linkMode || !selection || !invoice) return;
    const target = e.target as HTMLElement;
    const sel = "input[data-path], textarea[data-path]";
    const input =
      target.closest<HTMLInputElement | HTMLTextAreaElement>(sel) ??
      target.closest("label")?.querySelector<HTMLInputElement | HTMLTextAreaElement>(sel);
    if (!input) return;
    e.preventDefault();
    const path = input.dataset.path!;
    const value = path.endsWith("country_code")
      ? countryCodeOf(selection.text, countries)
      : valueFor(path, input.type, selection.text);
    if (value === null) {
      showToast(`„${selection.text}“ passt nicht in dieses Feld.`);
      return;
    }
    setInvoice((inv) => inv && setPath(inv, path, input.type === "date" ? value || null : value));
    const picked = selection;
    setSelection(null);
    input.blur(); // e.g. the country picker would stay open
    if (!(path in LINKABLE) || !companyId) {
      showToast("Wert übernommen. (Positionen werden nicht dauerhaft verknüpft.)");
      return;
    }
    saveFieldLink({
      company_id: companyId,
      field: path,
      page: picked.page,
      ...picked.box,
      anchor_text: picked.anchor?.text ?? "",
      anchor_x0: picked.anchor?.x0 ?? null,
      anchor_top: picked.anchor?.top ?? null,
      sample: picked.text.slice(0, 255),
    })
      .then((saved) => {
        setLinks((ls) => [...ls.filter((l) => l.field !== saved.field), saved]);
        showToast(`„${LINKABLE[path]}“ verknüpft – gilt ab jetzt für alle Rechnungen von ${companyName}.`);
      })
      .catch((err) => setErrors(describe(err)));
  }

  /** Read the PDF again with text recognition on every page (scanner text layer was poor). */
  async function forceOcr() {
    if (!loaded || loaded.kind !== "pdf") return;
    if (!window.confirm("PDF komplett neu per Texterkennung lesen? Ungespeicherte Änderungen im Formular gehen verloren.")) return;
    setBusy(true);
    setErrors([]);
    try {
      const fresh = await loadFile(loaded.file, loaded.companyId, true);
      open({ ...fresh, reviewId: loaded.reviewId, reviewProfile: loaded.reviewProfile }, openBatchId);
      showToast("Neu per Texterkennung gelesen.");
    } catch (e) {
      setErrors(describe(e));
    } finally {
      setBusy(false);
    }
  }

  /** Another own company chosen in the editor: re-read a fresh PDF with its links, else swap the seller. */
  async function switchCompany(id: string) {
    if (!loaded || !invoice) return;
    const c = companies.find((x) => x.id === id);
    if (!c) return;
    if (loaded.kind === "pdf" && !loaded.reviewId && loaded.file.size > 0) {
      setBusy(true);
      try {
        open(await loadFile(loaded.file, id), openBatchId);
      } catch (e) {
        setErrors(describe(e));
      } finally {
        setBusy(false);
      }
      return;
    }
    setInvoice({
      ...invoice,
      seller: structuredClone(c.seller),
      payment: { ...structuredClone(c.payment), due_date: invoice.payment.due_date, terms: invoice.payment.terms || c.payment.terms },
    });
    setLoaded({ ...loaded, companyId: id });
  }

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
    setErrors([]);
    setOpenBatchId(batchId);
    setSelection(null);
    if (l.reviewProfile) setProfile(l.reviewProfile);
  }

  function close() {
    setLoaded(null);
    setInvoice(null);
    setCheck(null);
    setOpenBatchId(null);
  }

  async function openReview(id: number) {
    setBusy(true);
    setErrors([]);
    try {
      const r = await getReview(id);
      const file = r.has_pdf
        ? await getReviewPdf(id, r.source_name)
        : new File([], r.source_name || "idoc.xml", { type: "application/xml" });
      open({
        file,
        kind: r.has_pdf ? "pdf" : "idoc",
        invoice: r.invoice,
        stated: r.stated_totals,
        warnings: r.warnings,
        notes: r.notes,
        templateName: r.template_name,
        companyId: r.company_id,
        reviewId: r.id,
        reviewProfile: r.profile,
      });
    } catch (e) {
      setErrors(describe(e));
    } finally {
      setBusy(false);
    }
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
    close();
    const items: BatchItem[] = files.map((file, i) => ({ id: Date.now() + i, file, status: "wartet" }));
    setBatch(items);
    setBatchRunning(true);
    for (const item of items) {
      setBatch((b) => b && b.map((x) => (x.id === item.id ? { ...x, status: "läuft" } : x)));
      const result = await processItem(item, profile);
      setBatch((b) => b && b.map((x) => (x.id === item.id ? result : x)));
    }
    setBatchRunning(false);
    onChanged();
  }

  function handleFiles(list: FileList | File[] | null) {
    const files = Array.from(list ?? []);
    if (files.length === 1) void loadSingle(files[0]);
    else if (files.length > 1) void runBatch(files);
  }

  // requests from the start page / review queue
  useEffect(
    () =>
      onWorkspaceRequest((req) => {
        if (req.kind === "files") handleFiles(req.files);
        else void openReview(req.id);
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  async function doExport() {
    if (!invoice || !loaded) return;
    setErrors([]);
    setBusy(true);
    try {
      const res = await exportInvoice(invoice, profile, loaded.kind === "pdf" ? loaded.file : null, {
        kind: loaded.kind,
        name: loaded.file.name,
        reviewId: loaded.reviewId,
      });
      const base = invoice.number.replace(/[^\w-]/g, "_") || "rechnung";
      download(res.blob, profile === "xrechnung" ? `${base}_xrechnung.xml` : `${base}_zugferd.pdf`);
      onChanged();
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
      if (loaded.reviewId) {
        // review mode: continue with the next open item
        const next = (await listReview()).find((r) => r.id !== loaded.reviewId);
        if (next) {
          showToast(`${invoice.number} erzeugt – nächste Rechnung aus „Zu prüfen“ geöffnet.`);
          await openReview(next.id);
        } else {
          showToast(`${invoice.number} erzeugt – „Zu prüfen“ ist leer.`);
          close();
          navigate("pruefen");
        }
      } else {
        showToast(`${invoice.number} erzeugt und archiviert.`);
      }
    } catch (e) {
      setErrors(describe(e));
    } finally {
      setBusy(false);
    }
  }

  const issues = check?.issues ?? [];
  const errorIssues = issues.filter((i) => i.severity === "error");
  const warningIssues = issues.filter((i) => i.severity === "warning");
  const showPreview = preview && loaded?.kind === "pdf" && loaded.file.size > 0;
  const stated = loaded?.stated?.gross;
  const grossMismatch = stated != null && check && Math.abs(Number(stated) - Number(check.totals.grand_total)) > 0.01;

  const dropHandlers = {
    onDragOver: (e: React.DragEvent) => {
      e.preventDefault();
      if (!batchRunning) setDragging(true);
    },
    onDragLeave: () => setDragging(false),
    onDrop: (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      if (!batchRunning) handleFiles(e.dataTransfer.files);
    },
  };

  return (
    <div className={`workspace${dragging ? " dragging" : ""}`} {...dropHandlers}>
      <input
        ref={fileInput}
        type="file"
        accept={ACCEPT}
        multiple
        hidden
        onChange={(e) => {
          handleFiles(e.target.files);
          e.target.value = "";
        }}
      />

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

      {!loaded && !batch && (
        <button type="button" className="dropzone" onClick={() => fileInput.current?.click()} disabled={busy}>
          <span className="dropzone-icon" aria-hidden>
            ⇪
          </span>
          <strong>{busy ? "Wird gelesen …" : "Rechnungen hierher ziehen oder klicken"}</strong>
          <span>
            PDF (auch gescannt – Texterkennung läuft automatisch) oder SAP-IDoc (.xml) · mehrere Dateien werden als
            Stapel verarbeitet
          </span>
        </button>
      )}

      {invoice && loaded && (
        <div className="editor">
          <div className="editor-bar">
            <div className="bar-file">
              <strong title={loaded.file.name}>{invoice.number || loaded.file.name || "Neue Rechnung"}</strong>
              <span className="muted">
                {loaded.reviewId ? "Aus „Zu prüfen“ · " : ""}
                {loaded.templateName}
              </span>
            </div>

            {companies.length > 1 && (
              <label className="company-select" title="Rechnungssteller – Feldverknüpfungen gelten je Firma">
                <span>Firma</span>
                <select value={companyId} onChange={(e) => void switchCompany(e.target.value)} disabled={busy}>
                  {companies.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.seller.name || c.id}
                    </option>
                  ))}
                </select>
              </label>
            )}

            <div className={`chip ${!check ? "" : errorIssues.length ? "bad" : "good"}`}>
              {!check ? "Prüfe …" : errorIssues.length ? `${errorIssues.length} Fehler` : "✓ Bereit"}
            </div>

            <div className="segmented small" role="radiogroup" aria-label="Format">
              {(["zugferd", "xrechnung"] as Profile[]).map((p) => (
                <button
                  key={p}
                  role="radio"
                  aria-checked={profile === p}
                  className={profile === p ? "active" : ""}
                  onClick={() => {
                    setProfile(p);
                    store("erechnung.profile", p);
                  }}
                >
                  {p === "zugferd" ? "ZUGFeRD" : "XRechnung"}
                </button>
              ))}
            </div>

            {check && (
              <div className="bar-totals" title={check.totals.breakdown.map((b) => `USt. ${b.rate} %: ${eur(b.amount)}`).join("\n")}>
                <span>
                  Netto <strong>{eur(check.totals.line_total)}</strong>
                </span>
                <span>
                  USt <strong>{eur(check.totals.tax_total)}</strong>
                </span>
                <span className={grossMismatch ? "bad" : ""}>
                  Brutto <strong>{eur(check.totals.grand_total)}</strong>
                  {stated != null && (
                    <small> {grossMismatch ? `≠ PDF ${eur(stated)}` : "= PDF"}</small>
                  )}
                </span>
              </div>
            )}

            <div className="bar-actions">
              {loaded.kind === "pdf" && loaded.file.size > 0 && (
                <button
                  className="secondary"
                  onClick={() =>
                    setPreview((p) => {
                      store("erechnung.preview", p ? "off" : "on");
                      return !p;
                    })
                  }
                >
                  {preview ? "PDF ausblenden" : "PDF anzeigen"}
                </button>
              )}
              {loaded.kind === "pdf" && loaded.file.size > 0 && (
                <button
                  className="secondary"
                  onClick={() => void forceOcr()}
                  disabled={busy}
                  title="Wenn Text falsch oder gar nicht gelesen wurde: jede Seite neu per Texterkennung (OCR) lesen"
                >
                  {busy ? "Lese …" : "Texterkennung erzwingen"}
                </button>
              )}
              <button className="secondary" onClick={() => fileInput.current?.click()} disabled={batchRunning}>
                Andere Datei
              </button>
              <button
                className="primary inline"
                disabled={busy || !check || errorIssues.length > 0}
                onClick={doExport}
                title={errorIssues.length ? "Erst die Fehler beheben" : ""}
              >
                {profile === "zugferd" ? "ZUGFeRD erzeugen" : "XRechnung erzeugen"}
              </button>
            </div>
          </div>

          <div className={`editor-body${showPreview ? " with-preview" : ""}`}>
            {showPreview && (
              <PdfPreview
                file={loaded.file}
                linkMode={linkMode}
                onToggleLink={() => {
                  setLinkMode((m) => !m);
                  setSelection(null);
                }}
                selection={selection}
                onSelect={setSelection}
                links={links}
                companyName={companyName}
                forceOcr={loaded.forcedOcr}
              />
            )}
            <div className={`editor-form${linkMode && selection ? " link-armed" : ""}`} onClickCapture={assign}>
              {(errorIssues.length > 0 || warningIssues.length > 0) && (
                <IssueList errors={errorIssues} warnings={warningIssues} />
              )}
              {(loaded.notes.length > 0 || loaded.warnings.length > 0) && (
                <div className="alert info">
                  {loaded.notes.map((n, i) => (
                    <div key={`n${i}`}>ℹ {n}</div>
                  ))}
                  {loaded.warnings.map((w, i) => (
                    <div key={`w${i}`}>⚠ {w}</div>
                  ))}
                </div>
              )}
              <InvoiceForm
                invoice={invoice}
                update={(path, value) => setInvoice((inv) => inv && setPath(inv, path, value))}
                issues={issues}
              />
            </div>
          </div>
        </div>
      )}
      {dragging && <div className="drop-overlay">Loslassen zum Hochladen</div>}
    </div>
  );
}

function IssueList({ errors, warnings }: { errors: Issue[]; warnings: Issue[] }) {
  return (
    <div className="issue-panel">
      <strong>
        {errors.length > 0 ? `Vor dem Erzeugen beheben (${errors.length})` : "Hinweise"}
      </strong>
      <ul>
        {[...errors, ...warnings].map((i, k) => (
          <li key={k} className={i.severity}>
            <button type="button" onClick={() => focusField(i.field)} disabled={!i.field}>
              <code>{i.code}</code> {i.message}
              {i.field && <span className="goto">→ zum Feld</span>}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
