import { useEffect, useRef, useState } from "react";

/**
 * Renders all pages of a PDF with pdf.js (same look in every browser, incl. mobile).
 * pdf.js is loaded lazily, so it only costs bandwidth when a preview is shown.
 */
export function PdfPreview({ file }: { file: File }) {
  const container = useRef<HTMLDivElement>(null);
  const [error, setError] = useState("");
  const [pages, setPages] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const el = container.current;
    if (!el) return;
    el.replaceChildren();
    setError("");

    (async () => {
      const pdfjs = await import("pdfjs-dist");
      const worker = await import("pdfjs-dist/build/pdf.worker.min.mjs?url");
      pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
      const doc = await pdfjs.getDocument({ data: new Uint8Array(await file.arrayBuffer()) }).promise;
      if (cancelled) return;
      setPages(doc.numPages);
      const cssWidth = el.clientWidth || 500;
      for (let n = 1; n <= doc.numPages && !cancelled; n++) {
        const page = await doc.getPage(n);
        const base = page.getViewport({ scale: 1 });
        const ratio = window.devicePixelRatio || 1;
        const viewport = page.getViewport({ scale: (cssWidth / base.width) * ratio });
        const canvas = document.createElement("canvas");
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        canvas.style.width = "100%";
        canvas.setAttribute("aria-label", `Seite ${n} von ${doc.numPages}`);
        el.appendChild(canvas);
        await page.render({ canvasContext: canvas.getContext("2d")!, viewport }).promise;
      }
      void doc.destroy();
    })().catch((e) => !cancelled && setError(`Vorschau nicht möglich: ${e?.message ?? e}`));

    return () => {
      cancelled = true;
    };
  }, [file]);

  return (
    <div className="pdf-preview card">
      <h2>
        Original-PDF <small>{pages > 1 ? `${pages} Seiten` : ""}</small>
      </h2>
      {error && <p className="hint">{error}</p>}
      <div ref={container} className="pdf-pages" />
    </div>
  );
}
