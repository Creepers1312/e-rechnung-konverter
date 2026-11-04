import { useEffect, useState } from "react";
import { documentUrl, listDocuments } from "./api";
import type { ArchivedDocument } from "./types";

const SOURCE_LABEL: Record<string, string> = { pdf: "PDF", idoc: "SAP-IDoc", manual: "Manuell" };

/** Every generated e-invoice (UI exports and the SAP inbox worker), newest first. */
export function Archive({ refreshKey }: { refreshKey: number }) {
  const [docs, setDocs] = useState<ArchivedDocument[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    listDocuments()
      .then((d) => {
        setDocs(d);
        setError("");
      })
      .catch((e) => setError(String(e.message ?? e)));
  }, [refreshKey]);

  return (
    <section className="archive">
      <h2>Archiv</h2>
      {error && <p className="hint">Archiv nicht verfügbar: {error}</p>}
      {docs && docs.length === 0 && <p className="hint">Noch keine E-Rechnungen erzeugt.</p>}
      {docs && docs.length > 0 && (
        <div className="lines">
          <table>
            <thead>
              <tr>
                <th>Erstellt</th>
                <th>Rechnung</th>
                <th>Käufer</th>
                <th className="right">Brutto</th>
                <th>Format</th>
                <th>Quelle</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.id}>
                  <td>{new Date(d.created_at).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" })}</td>
                  <td>{d.invoice_number}</td>
                  <td>{d.buyer_name}</td>
                  <td className="right">{Number(d.grand_total).toLocaleString("de-DE", { style: "currency", currency: d.currency })}</td>
                  <td>{d.profile === "xrechnung" ? "XRechnung" : "ZUGFeRD"}</td>
                  <td title={d.source_name}>{SOURCE_LABEL[d.source] ?? d.source}</td>
                  <td>
                    <a href={documentUrl(d.id)} download={d.filename}>Herunterladen</a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
