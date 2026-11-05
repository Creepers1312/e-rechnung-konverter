import { useEffect, useState } from "react";
import { documentUrl, downloadZip, listDocuments } from "./api";
import type { DocumentFilter, DocumentPage } from "./types";
import { describe, download, eur, fmtDateTime } from "./util";

const SOURCE_LABEL: Record<string, string> = { pdf: "PDF", idoc: "SAP-IDoc", manual: "Manuell" };
const PAGE_SIZE = 25;

/** Every generated e-invoice (UI exports and the SAP inbox worker), newest first, searchable. */
export function Archive({ refreshKey }: { refreshKey: number }) {
  const [filter, setFilter] = useState<DocumentFilter>({ q: "", date_from: "", date_to: "", profile: "", source: "" });
  const [query, setQuery] = useState(""); // search box, applied debounced
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<DocumentPage | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [error, setError] = useState("");

  useEffect(() => {
    const t = setTimeout(() => {
      setFilter((f) => ({ ...f, q: query }));
      setOffset(0);
    }, 300);
    return () => clearTimeout(t);
  }, [query]);

  useEffect(() => {
    listDocuments({ ...filter, offset, limit: PAGE_SIZE })
      .then((p) => {
        setPage(p);
        setError("");
      })
      .catch((e) => setError(describe(e).join(" – ")));
  }, [filter, offset, refreshKey]);

  const set = (patch: DocumentFilter) => {
    setFilter((f) => ({ ...f, ...patch }));
    setOffset(0);
  };
  const toggle = (id: number) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const items = page?.items ?? [];
  const allOnPage = items.length > 0 && items.every((d) => selected.has(d.id));
  const filtered = Boolean(filter.q || filter.date_from || filter.date_to || filter.profile || filter.source);

  return (
    <section className="archive">
      <div className="archive-head">
        <h2>Archiv</h2>
        <span className="hint">{page ? `${page.total} E-Rechnungen${filtered ? " gefunden" : ""}` : ""}</span>
      </div>

      <div className="filters">
        <label className="field grow">
          <span>Suche (Rechnungsnummer oder Käufer)</span>
          <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="z. B. RE-2026 oder Kunde AG" />
        </label>
        <label className="field">
          <span>Von</span>
          <input type="date" value={filter.date_from} onChange={(e) => set({ date_from: e.target.value })} />
        </label>
        <label className="field">
          <span>Bis</span>
          <input type="date" value={filter.date_to} onChange={(e) => set({ date_to: e.target.value })} />
        </label>
        <label className="field">
          <span>Format</span>
          <select value={filter.profile} onChange={(e) => set({ profile: e.target.value as DocumentFilter["profile"] })}>
            <option value="">Alle</option>
            <option value="zugferd">ZUGFeRD</option>
            <option value="xrechnung">XRechnung</option>
          </select>
        </label>
        <label className="field">
          <span>Quelle</span>
          <select value={filter.source} onChange={(e) => set({ source: e.target.value as DocumentFilter["source"] })}>
            <option value="">Alle</option>
            <option value="pdf">PDF</option>
            <option value="idoc">SAP-IDoc</option>
            <option value="manual">Manuell</option>
          </select>
        </label>
      </div>

      {error && <p className="hint">Archiv nicht verfügbar: {error}</p>}
      {page && items.length === 0 && (
        <p className="hint">{filtered ? "Keine Treffer für diese Filter." : "Noch keine E-Rechnungen erzeugt."}</p>
      )}

      {items.length > 0 && (
        <div className="lines">
          <table>
            <thead>
              <tr>
                <th>
                  <input
                    type="checkbox"
                    aria-label="Alle auf dieser Seite auswählen"
                    checked={allOnPage}
                    onChange={() =>
                      setSelected((s) => {
                        const next = new Set(s);
                        for (const d of items) {
                          if (allOnPage) next.delete(d.id);
                          else next.add(d.id);
                        }
                        return next;
                      })
                    }
                  />
                </th>
                <th>Erstellt</th>
                <th>Rechnung</th>
                <th>Käufer</th>
                <th className="right">Brutto</th>
                <th>Format</th>
                <th>Quelle</th>
                <th>Von</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((d) => (
                <tr key={d.id}>
                  <td>
                    <input type="checkbox" aria-label={`${d.invoice_number} auswählen`} checked={selected.has(d.id)} onChange={() => toggle(d.id)} />
                  </td>
                  <td>{fmtDateTime(d.created_at)}</td>
                  <td>{d.invoice_number}</td>
                  <td>{d.buyer_name}</td>
                  <td className="right">{eur(d.grand_total, d.currency)}</td>
                  <td>{d.profile === "xrechnung" ? "XRechnung" : "ZUGFeRD"}</td>
                  <td title={d.source_name}>{SOURCE_LABEL[d.source] ?? d.source}</td>
                  <td>{d.created_by || "–"}</td>
                  <td>
                    <a href={documentUrl(d.id)} download={d.filename}>
                      Herunterladen
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {page && page.total > 0 && (
        <div className="pager">
          <button className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
            ← Neuere
          </button>
          <span className="hint">
            {offset + 1}–{Math.min(offset + PAGE_SIZE, page.total)} von {page.total}
          </span>
          <button
            className="secondary"
            disabled={offset + PAGE_SIZE >= page.total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Ältere →
          </button>
          <span className="spacer" />
          {selected.size > 0 && (
            <>
              <button className="secondary" onClick={() => setSelected(new Set())}>
                Auswahl aufheben
              </button>
              <button
                className="primary inline"
                onClick={async () => {
                  try {
                    download(await downloadZip([...selected]), "e-rechnungen.zip");
                  } catch (e) {
                    setError(describe(e).join(" – "));
                  }
                }}
              >
                {selected.size} als ZIP herunterladen
              </button>
            </>
          )}
        </div>
      )}
    </section>
  );
}
