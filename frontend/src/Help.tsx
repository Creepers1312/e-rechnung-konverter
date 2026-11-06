import { marked } from "marked";
import { useEffect, useMemo, useRef, useState } from "react";
import { getDoc } from "./api";
import type { CurrentUser, DocEndpoint } from "./types";
import { describe } from "./util";

type Tab = "anwender" | "system";

function slug(text: string): string {
  return text
    .toLowerCase()
    .replace(/ä/g, "ae")
    .replace(/ö/g, "oe")
    .replace(/ü/g, "ue")
    .replace(/ß/g, "ss")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

/** Markdown from our own files (app/handbuch), images served by the API behind login. */
function render(markdown: string): string {
  const html = marked.parse(markdown, { async: false }) as string;
  return html.replace(/src="img\//g, 'src="/api/docs/img/');
}

function Endpoints({ items }: { items: DocEndpoint[] }) {
  const [q, setQ] = useState("");
  const shown = items.filter((e) => `${e.method} ${e.path} ${e.summary} ${e.access}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <section className="doc-endpoints">
      <div className="section-head">
        <h2 id="rest-endpunkte">REST-Endpunkte (automatisch aus dem laufenden System)</h2>
        <input type="search" placeholder="Filtern …" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="table-card">
        <table className="data">
          <thead>
            <tr>
              <th>Methode</th>
              <th>Pfad</th>
              <th>Zugriff</th>
              <th>Beschreibung</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((e) => (
              <tr key={`${e.method} ${e.path}`}>
                <td>
                  <code className={`method ${e.method.toLowerCase()}`}>{e.method}</code>
                </td>
                <td>
                  <code>{e.path}</code>
                </td>
                <td>{e.access}</td>
                <td>{e.summary}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** Help: user manual for everyone, system documentation for admins. */
export function Help({ me }: { me: CurrentUser }) {
  const isAdmin = me.role === "admin";
  const [tab, setTab] = useState<Tab>("anwender");
  const [markdown, setMarkdown] = useState<Record<Tab, string>>({ anwender: "", system: "" });
  const [endpoints, setEndpoints] = useState<DocEndpoint[]>([]);
  const [error, setError] = useState("");
  const [toc, setToc] = useState<{ id: string; text: string; level: number }[]>([]);
  const body = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setError("");
    if (tab === "anwender" && !markdown.anwender)
      getDoc("anwender")
        .then((d) => setMarkdown((m) => ({ ...m, anwender: d.markdown })))
        .catch((e) => setError(describe(e).join(" ")));
    if (tab === "system" && !markdown.system)
      getDoc("system")
        .then((d) => {
          setMarkdown((m) => ({ ...m, system: d.markdown }));
          setEndpoints(d.endpoints);
        })
        .catch((e) => setError(describe(e).join(" ")));
  }, [tab, markdown]);

  const html = useMemo(() => (markdown[tab] ? render(markdown[tab]) : ""), [markdown, tab]);

  // heading ids + table of contents from the rendered document
  useEffect(() => {
    if (!body.current) return;
    const items: { id: string; text: string; level: number }[] = [];
    body.current.querySelectorAll("h2, h3").forEach((h) => {
      const text = h.textContent ?? "";
      h.id = slug(text);
      items.push({ id: h.id, text, level: h.tagName === "H2" ? 2 : 3 });
    });
    if (tab === "system" && endpoints.length) items.push({ id: "rest-endpunkte", text: "REST-Endpunkte", level: 2 });
    setToc(items);
  }, [html, tab, endpoints]);

  const jump = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });

  return (
    <div className="help">
      <header className="page-head help-head">
        <div>
          <h1>Hilfe</h1>
          <p className="muted">
            {tab === "anwender"
              ? "Anwenderhandbuch: so wandeln, prüfen und finden Sie E-Rechnungen."
              : "Systemdokumentation: Aufbau, Schnittstellen, Betrieb – nur für Administratoren."}
          </p>
        </div>
        <button className="secondary inline" onClick={() => window.print()}>
          Drucken / als PDF
        </button>
      </header>
      {isAdmin && (
        <div className="tabs" role="tablist">
          {(
            [
              ["anwender", "Anwenderhandbuch"],
              ["system", "Systemdokumentation"],
            ] as const
          ).map(([t, label]) => (
            <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
              {label}
            </button>
          ))}
        </div>
      )}
      {error && <div className="alert error">{error}</div>}
      <div className="help-layout">
        <nav className="toc" aria-label="Inhalt">
          <strong>Inhalt</strong>
          {toc.map((t) => (
            <a
              key={t.id}
              href={`#${t.id}`}
              className={`l${t.level}`}
              onClick={(e) => {
                e.preventDefault(); // the hash is used by the page router
                jump(t.id);
              }}
            >
              {t.text}
            </a>
          ))}
        </nav>
        <article className="doc">
          <div ref={body} dangerouslySetInnerHTML={{ __html: html }} />
          {tab === "system" && endpoints.length > 0 && <Endpoints items={endpoints} />}
        </article>
      </div>
    </div>
  );
}
