import { useEffect, useState } from "react";
import { discardReview, listReview } from "./api";
import { navigate } from "./router";
import type { ReviewInfo } from "./types";
import { describe, fmtDateTime } from "./util";
import { requestWorkspace } from "./workspace";

const SOURCE: Record<string, string> = { pdf: "PDF-Eingang", idoc: "SAP-IDoc" };

/** Invoices the automation could not finish ("Zu prüfen"). */
export function Review({ refreshKey, onChanged }: { refreshKey: number; onChanged: () => void }) {
  const [items, setItems] = useState<ReviewInfo[] | null>(null);
  const [error, setError] = useState("");

  const load = () =>
    listReview()
      .then((r) => {
        setItems(r);
        setError("");
      })
      .catch((e) => setError(describe(e).join(" – ")));

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 20_000);
    return () => clearInterval(t);
  }, [refreshKey]);

  const openItem = (id: number) => {
    requestWorkspace({ kind: "review", id });
    navigate("umwandeln");
  };

  return (
    <div className="page">
      <header className="page-head">
        <h1>Zu prüfen</h1>
        <p className="muted">
          Rechnungen aus dem SAP- und PDF-Eingang, die nicht vollautomatisch erzeugt werden konnten – meist fehlt eine
          Angabe zum Käufer. Öffnen, ergänzen, erzeugen: Danach geht es direkt mit der nächsten weiter.
        </p>
      </header>
      {error && <div className="alert error">{error}</div>}
      {items && items.length === 0 && (
        <div className="empty">
          <strong>✓ Alles erledigt</strong>
          <span>Keine Rechnungen warten auf eine Prüfung.</span>
        </div>
      )}
      {items && items.length > 0 && (
        <>
          <div className="toolbar">
            <button className="primary inline" onClick={() => openItem(items[items.length - 1].id)}>
              Prüfung starten ({items.length})
            </button>
          </div>
          <div className="table-card">
            <table className="data">
              <thead>
                <tr>
                  <th>Eingang</th>
                  <th>Quelle</th>
                  <th>Rechnung</th>
                  <th>Käufer</th>
                  <th>Was fehlt</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((r) => (
                  <tr key={r.id} className="clickable" onClick={() => openItem(r.id)}>
                    <td>{fmtDateTime(r.created_at)}</td>
                    <td title={r.source_name}>
                      {SOURCE[r.source] ?? r.source}
                      <div className="muted small">{r.source_name}</div>
                    </td>
                    <td>{r.invoice_number || <span className="muted">unbekannt</span>}</td>
                    <td>{r.buyer_name || <span className="muted">fehlt</span>}</td>
                    <td className="issues-cell">
                      {r.issues.length === 0
                        ? "Freigabe (Vollautomatik aus)"
                        : r.issues
                            .slice(0, 3)
                            .map((i) => i.message)
                            .join(" · ") + (r.issues.length > 3 ? ` · +${r.issues.length - 3}` : "")}
                    </td>
                    <td className="actions" onClick={(e) => e.stopPropagation()}>
                      <button className="primary inline small" onClick={() => openItem(r.id)}>
                        Bearbeiten
                      </button>
                      <button
                        className="secondary"
                        onClick={async () => {
                          if (!window.confirm(`„${r.invoice_number || r.source_name}“ verwerfen? Es wird keine E-Rechnung erzeugt.`)) return;
                          try {
                            await discardReview(r.id);
                            onChanged();
                            await load();
                          } catch (e) {
                            setError(describe(e).join(" – "));
                          }
                        }}
                      >
                        Verwerfen
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
