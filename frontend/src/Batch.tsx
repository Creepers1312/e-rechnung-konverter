import { downloadZip, exportInvoice, extractPdf, importIdoc, validateInvoice } from "./api";
import type { Invoice, Profile, StatedTotals } from "./types";
import { describe, download, eur, isIdoc } from "./util";

/** An invoice loaded from a file, ready for the review form. */
export interface Loaded {
  file: File;
  kind: "pdf" | "idoc";
  invoice: Invoice;
  stated: StatedTotals | null;
  warnings: string[];
  notes: string[];
  templateName: string;
  companyId: string; // own company the invoice belongs to (field links are stored per company)
  reviewId?: number; // opened from "Zu prüfen": exporting marks it done
  reviewProfile?: Profile;
}

export type BatchStatus = "wartet" | "läuft" | "fertig" | "prüfen" | "fehler";

export interface BatchItem {
  id: number;
  file: File;
  status: BatchStatus;
  loaded?: Loaded;
  documentId?: number;
  grossTotal?: string;
  message?: string;
}

export async function loadFile(file: File, companyId = ""): Promise<Loaded> {
  if (isIdoc(file)) {
    const r = await importIdoc(file);
    return {
      file,
      kind: "idoc",
      invoice: r.invoice,
      stated: null,
      warnings: [],
      notes: r.notes,
      templateName: "SAP ECC IDoc (INVOIC02)",
      companyId: r.company_id,
    };
  }
  const r = await extractPdf(file, companyId);
  return {
    file,
    kind: "pdf",
    invoice: r.invoice,
    stated: r.stated_totals,
    warnings: r.warnings,
    notes: r.notes,
    templateName: r.template_name,
    companyId: r.company_id,
  };
}

/** Extract → validate → export if there are no errors. Never throws. */
export async function processItem(item: BatchItem, profile: Profile): Promise<BatchItem> {
  try {
    const loaded = await loadFile(item.file);
    const check = await validateInvoice(loaded.invoice, profile, loaded.stated);
    const errors = check.issues.filter((i) => i.severity === "error");
    if (errors.length > 0) {
      return {
        ...item,
        loaded,
        status: "prüfen",
        grossTotal: check.totals.grand_total,
        message: errors.map((e) => e.message).join(" · "),
      };
    }
    const res = await exportInvoice(loaded.invoice, profile, loaded.kind === "pdf" ? loaded.file : null, {
      kind: loaded.kind,
      name: loaded.file.name,
    });
    return { ...item, loaded, status: "fertig", documentId: res.documentId, grossTotal: check.totals.grand_total };
  } catch (e) {
    return { ...item, status: "fehler", message: describe(e).join(" – ") };
  }
}

const LABEL: Record<BatchStatus, string> = {
  wartet: "wartet",
  läuft: "wird verarbeitet …",
  fertig: "✓ erzeugt",
  prüfen: "bitte prüfen",
  fehler: "Fehler",
};

export function BatchList({
  items,
  profile,
  running,
  onOpen,
  onClose,
}: {
  items: BatchItem[];
  profile: Profile;
  running: boolean;
  onOpen: (item: BatchItem) => void;
  onClose: () => void;
}) {
  const done = items.filter((i) => i.status === "fertig");
  const review = items.filter((i) => i.status === "prüfen").length;
  const failed = items.filter((i) => i.status === "fehler").length;

  return (
    <section className="batch">
      <div className="batch-head">
        <h2>
          Stapel: {items.length} Dateien → {profile === "xrechnung" ? "XRechnung" : "ZUGFeRD"}
        </h2>
        <span className="hint">
          {done.length} erzeugt · {review} zu prüfen · {failed} Fehler
          {running && " · läuft …"}
        </span>
        <div className="batch-actions">
          <button
            className="secondary"
            disabled={done.length === 0 || running}
            onClick={async () => download(await downloadZip(done.map((d) => d.documentId!)), "e-rechnungen.zip")}
          >
            {done.length} erzeugte als ZIP
          </button>
          <button className="secondary" disabled={running} onClick={onClose}>
            Stapel schließen
          </button>
        </div>
      </div>
      <div className="lines">
        <table>
          <thead>
            <tr>
              <th>Datei</th>
              <th>Rechnung</th>
              <th className="right">Brutto</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id} className={`status-${item.status}`}>
                <td>{item.file.name}</td>
                <td>{item.loaded?.invoice.number || "–"}</td>
                <td className="right">{eur(item.grossTotal)}</td>
                <td>
                  <strong>{LABEL[item.status]}</strong>
                  {item.message && <div className="detail">{item.message}</div>}
                </td>
                <td>
                  {item.loaded && item.status !== "läuft" && (
                    <button className="secondary" onClick={() => onOpen(item)}>
                      {item.status === "prüfen" ? "Öffnen & korrigieren" : "Öffnen"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
