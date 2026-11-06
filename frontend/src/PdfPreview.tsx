import type { PDFDocumentProxy } from "pdfjs-dist";
import { useEffect, useRef, useState } from "react";
import { getPdfWords } from "./api";
import { type Selection, makeSelection, phraseAt, wordAt, wordsIn } from "./linking";
import type { FieldLink, PdfPageWords, PdfWords } from "./types";
import { describe } from "./util";

interface LinkProps {
  linkMode: boolean;
  onToggleLink: () => void;
  selection: Selection | null;
  onSelect: (s: Selection | null) => void;
  links: FieldLink[];
  companyName: string;
}

/** One rendered page plus the click/drag layer used for linking fields. */
function Page({
  doc,
  n,
  words,
  link,
}: {
  doc: PDFDocumentProxy;
  n: number;
  words: PdfPageWords | undefined;
  link: LinkProps;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const layer = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const [hover, setHover] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const page = await doc.getPage(n);
      const el = canvas.current;
      if (!el || cancelled) return;
      const cssWidth = el.parentElement?.clientWidth || 500;
      const base = page.getViewport({ scale: 1 });
      const ratio = window.devicePixelRatio || 1;
      const viewport = page.getViewport({ scale: (cssWidth / base.width) * ratio });
      el.width = viewport.width;
      el.height = viewport.height;
      await page.render({ canvasContext: el.getContext("2d")!, viewport }).promise;
    })().catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [doc, n]);

  const active = link.linkMode && words;
  // pointer -> PDF points
  const toPdf = (e: React.PointerEvent) => {
    const r = layer.current!.getBoundingClientRect();
    return { x: ((e.clientX - r.left) / r.width) * words!.width, y: ((e.clientY - r.top) / r.height) * words!.height };
  };
  const pct = (b: { x0: number; top: number; x1: number; bottom: number }) => ({
    left: `${(b.x0 / words!.width) * 100}%`,
    top: `${(b.top / words!.height) * 100}%`,
    width: `${((b.x1 - b.x0) / words!.width) * 100}%`,
    height: `${((b.bottom - b.top) / words!.height) * 100}%`,
  });

  return (
    <div className="pdf-page">
      <canvas ref={canvas} aria-label={`Seite ${n}`} />
      {active && (
        <div
          ref={layer}
          className="pdf-link-layer"
          onPointerDown={(e) => {
            const p = toPdf(e);
            e.currentTarget.setPointerCapture(e.pointerId);
            setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y });
          }}
          onPointerMove={(e) => {
            const p = toPdf(e);
            if (drag) setDrag({ ...drag, x1: p.x, y1: p.y });
            else {
              const w = wordAt(words, p.x, p.y);
              setHover(w ? `${w.x0}:${w.top}` : null);
            }
          }}
          onPointerUp={(e) => {
            if (!drag) return;
            const p = toPdf(e);
            const rect = {
              x0: Math.min(drag.x0, p.x),
              top: Math.min(drag.y0, p.y),
              x1: Math.max(drag.x0, p.x),
              bottom: Math.max(drag.y0, p.y),
            };
            setDrag(null);
            const dragged = rect.x1 - rect.x0 > 4 || rect.bottom - rect.top > 4;
            if (dragged) {
              link.onSelect(makeSelection(words, wordsIn(words, rect)));
            } else {
              const w = wordAt(words, p.x, p.y);
              link.onSelect(w ? makeSelection(words, phraseAt(words, w)) : null);
            }
          }}
        >
          {words.words.map((w, i) => (
            <span
              key={i}
              className={`pdf-word${hover === `${w.x0}:${w.top}` ? " hover" : ""}`}
              style={pct({ x0: w.x0 - 1, top: w.top - 1, x1: w.x1 + 1, bottom: w.bottom + 1 })}
            />
          ))}
          {link.links
            .filter((l) => l.page === n)
            .map((l) => (
              <span key={l.id} className="pdf-linked" style={pct(l)} title={`${l.label} (gespeichert)`}>
                <small>{l.label}</small>
              </span>
            ))}
          {link.selection?.page === n && <span className="pdf-selected" style={pct(link.selection.box)} />}
          {drag && (
            <span
              className="pdf-drag"
              style={pct({
                x0: Math.min(drag.x0, drag.x1),
                top: Math.min(drag.y0, drag.y1),
                x1: Math.max(drag.x0, drag.x1),
                bottom: Math.max(drag.y0, drag.y1),
              })}
            />
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Renders all pages of a PDF with pdf.js (same look in every browser, incl. mobile).
 * pdf.js is loaded lazily, so it only costs bandwidth when a preview is shown.
 * In link mode the words (from the server, OCR for scans) can be clicked or dragged over
 * to pick a value; the editor then links it to the form field clicked next.
 */
export function PdfPreview({ file, ...link }: { file: File } & LinkProps) {
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState("");
  const [words, setWords] = useState<PdfWords | null>(null);
  const [loadingWords, setLoadingWords] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let loaded: PDFDocumentProxy | null = null;
    setDoc(null);
    setWords(null);
    setError("");
    (async () => {
      const pdfjs = await import("pdfjs-dist");
      const worker = await import("pdfjs-dist/build/pdf.worker.min.mjs?url");
      pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
      loaded = await pdfjs.getDocument({ data: new Uint8Array(await file.arrayBuffer()) }).promise;
      if (!cancelled) setDoc(loaded);
    })().catch((e) => !cancelled && setError(`Vorschau nicht möglich: ${e?.message ?? e}`));
    return () => {
      cancelled = true;
      void loaded?.destroy();
    };
  }, [file]);

  // words are only needed (and fetched, OCR for scans) once linking is switched on
  useEffect(() => {
    if (!link.linkMode || words || loadingWords) return;
    setLoadingWords(true);
    getPdfWords(file)
      .then(setWords)
      .catch((e) => setError(describe(e).join(" ")))
      .finally(() => setLoadingWords(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [link.linkMode, file]);

  const pages = doc ? Array.from({ length: doc.numPages }, (_, i) => i + 1) : [];
  return (
    <div className={`pdf-preview card${link.linkMode ? " linking" : ""}`}>
      <div className="pdf-head">
        <h2>
          Original-PDF <small>{pages.length > 1 ? `${pages.length} Seiten` : ""}</small>
        </h2>
        <button
          className={link.linkMode ? "primary inline small-btn" : "secondary small-btn"}
          onClick={link.onToggleLink}
          aria-pressed={link.linkMode}
          title="Wert im PDF anklicken, dann das Feld im Formular – wird für diese Firma gemerkt"
        >
          {link.linkMode ? "Zuordnen beenden" : "Felder zuordnen"}
        </button>
      </div>
      {link.linkMode && (
        <div className="link-help" role="status">
          {loadingWords ? (
            "Text wird gelesen … (bei Scans per Texterkennung, kann etwas dauern)"
          ) : link.selection ? (
            <>
              <span>
                Ausgewählt: <strong>„{link.selection.text}“</strong> – jetzt rechts das passende Feld anklicken.
              </span>
              <button className="link" onClick={() => link.onSelect(null)}>
                Abbrechen
              </button>
            </>
          ) : (
            <span>
              Wert im PDF <strong>anklicken</strong> (oder einen Rahmen <strong>ziehen</strong>), dann das Feld im Formular.
              Die Zuordnung wird für <strong>{link.companyName}</strong> gespeichert und bei den nächsten Rechnungen
              automatisch verwendet.
            </span>
          )}
          {words?.warning && <span className="muted"> {words.warning}</span>}
        </div>
      )}
      {error && <p className="hint">{error}</p>}
      <div className="pdf-pages">
        {doc && pages.map((n) => <Page key={n} doc={doc} n={n} words={words?.pages.find((p) => p.page === n)} link={link} />)}
      </div>
    </div>
  );
}
