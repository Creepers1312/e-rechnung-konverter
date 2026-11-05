import { useEffect, useRef, useState } from "react";
import { getDashboard, listDocuments } from "./api";
import { navigate } from "./router";
import type { ArchivedDocument, CurrentUser, Dashboard as DashboardData } from "./types";
import { eur, fmtDateTime } from "./util";
import { requestWorkspace } from "./workspace";

function greeting(): string {
  const h = new Date().getHours();
  return h < 11 ? "Guten Morgen" : h < 18 ? "Guten Tag" : "Guten Abend";
}

export function Dashboard({ me, refreshKey }: { me: CurrentUser; refreshKey: number }) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [recent, setRecent] = useState<ArchivedDocument[]>([]);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const load = () => {
      getDashboard().then(setData).catch(() => undefined);
      listDocuments({ limit: 6 })
        .then((p) => setRecent(p.items))
        .catch(() => undefined);
    };
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [refreshKey]);

  const upload = (files: FileList | null) => {
    if (!files?.length) return;
    requestWorkspace({ kind: "files", files: Array.from(files) });
    navigate("umwandeln");
  };

  const w = data?.worker ?? {};
  const workerAge = w.last_round_at ? (Date.now() - new Date(w.last_round_at).getTime()) / 1000 : Infinity;
  const workerOk = workerAge < 3 * (w.interval_seconds ?? 10) + 30;

  return (
    <div className="page dashboard">
      <header className="page-head">
        <h1>
          {greeting()}, {me.username}
        </h1>
        <p className="muted">Hier sehen Sie, was ansteht – neue Rechnungen einfach unten ablegen.</p>
      </header>

      <div className="kpis">
        <button
          className={`kpi${data && data.review_open > 0 ? " attention" : ""}`}
          onClick={() => navigate("pruefen")}
        >
          <span>Zu prüfen</span>
          <strong>{data?.review_open ?? "–"}</strong>
          <small>{data?.review_open ? "Rechnungen warten auf Sie →" : "nichts offen"}</small>
        </button>
        <button className="kpi" onClick={() => navigate("archiv")}>
          <span>Heute erzeugt</span>
          <strong>{data?.documents_today ?? "–"}</strong>
          <small>{data ? `${data.documents_total} insgesamt im Archiv` : ""}</small>
        </button>
        <div className={`kpi static ${!w.last_round_at ? "" : workerOk ? "good" : "bad"}`}>
          <span>Automatik (SAP- und PDF-Eingang)</span>
          <strong>{!w.last_round_at ? "–" : workerOk ? "läuft" : "gestört"}</strong>
          <small>
            {w.last_round_at
              ? `${w.converted_total ?? 0} automatisch erzeugt · ${w.review_total ?? 0} zur Prüfung`
              : "noch keine Meldung"}
          </small>
        </div>
      </div>

      <input ref={input} type="file" multiple hidden accept=".pdf,.xml" onChange={(e) => upload(e.target.files)} />
      <button
        type="button"
        className={`dropzone${dragging ? " active" : ""}`}
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          upload(e.dataTransfer.files);
        }}
      >
        <span className="dropzone-icon" aria-hidden>
          ⇪
        </span>
        <strong>Rechnungen hierher ziehen oder klicken</strong>
        <span>PDF (auch Scans) oder SAP-IDoc · eine Datei zum Prüfen, mehrere als Stapel</span>
      </button>

      <section className="card">
        <div className="section-head">
          <h2>Zuletzt erzeugt</h2>
          <button className="link" onClick={() => navigate("archiv")}>
            Ganzes Archiv →
          </button>
        </div>
        {recent.length === 0 ? (
          <p className="muted">Noch keine E-Rechnungen.</p>
        ) : (
          <ul className="recent">
            {recent.map((d) => (
              <li key={d.id}>
                <strong>{d.invoice_number}</strong>
                <span>{d.buyer_name}</span>
                <span className="num">{eur(d.grand_total, d.currency)}</span>
                <span className="muted">
                  {fmtDateTime(d.created_at)} · {d.created_by || "–"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
