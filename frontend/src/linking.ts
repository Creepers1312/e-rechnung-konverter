import type { PdfPageWords, PdfWord } from "./types";

/** Click-to-link: geometry helpers. Coordinates are PDF points, origin top-left (as pdfplumber). */

export interface Selection {
  page: number;
  words: PdfWord[];
  text: string;
  box: { x0: number; top: number; x1: number; bottom: number };
  anchor: { text: string; x0: number; top: number } | null;
}

const LINE_GAP = 2.5; // same line: tops differ by less (same value as the backend)

function sameLine(a: PdfWord, b: PdfWord): boolean {
  return Math.abs(a.top - b.top) < LINE_GAP;
}

function close(a: PdfWord, b: PdfWord): boolean {
  const gap = b.x0 - a.x1;
  return gap >= 0 && gap < Math.max(4, 0.6 * (a.bottom - a.top));
}

function lineOf(words: PdfWord[], w: PdfWord): PdfWord[] {
  return words.filter((x) => sameLine(x, w)).sort((a, b) => a.x0 - b.x0);
}

/** Words of the phrase around `w` (close neighbours on the same line), without a leading "Label:". */
export function phraseAt(page: PdfPageWords, w: PdfWord): PdfWord[] {
  const line = lineOf(page.words, w);
  let i = line.indexOf(w);
  let start = i;
  while (start > 0 && close(line[start - 1], line[start]) && !line[start - 1].text.endsWith(":")) start--;
  let end = i;
  while (end + 1 < line.length && close(line[end], line[end + 1]) && !line[end].text.endsWith(":")) end++;
  // clicked on the label itself ("Rechnungsnr.:"): take the value right of it instead
  if (w.text.endsWith(":") && i + 1 < line.length) {
    i += 1;
    start = i;
    end = i;
    while (end + 1 < line.length && close(line[end], line[end + 1])) end++;
  }
  return line.slice(start, end + 1);
}

export function wordAt(page: PdfPageWords, x: number, y: number): PdfWord | null {
  const pad = 1.5;
  return (
    page.words.find((w) => x >= w.x0 - pad && x <= w.x1 + pad && y >= w.top - pad && y <= w.bottom + pad) ?? null
  );
}

export function wordsIn(page: PdfPageWords, r: { x0: number; top: number; x1: number; bottom: number }): PdfWord[] {
  return page.words.filter((w) => {
    const cx = (w.x0 + w.x1) / 2;
    const cy = (w.top + w.bottom) / 2;
    return cx >= r.x0 && cx <= r.x1 && cy >= r.top && cy <= r.bottom;
  });
}

function textOf(words: PdfWord[]): string {
  const rows: PdfWord[][] = [];
  for (const w of [...words].sort((a, b) => a.top - b.top || a.x0 - b.x0)) {
    const row = rows.find((r) => sameLine(r[0], w));
    if (row) row.push(w);
    else rows.push([w]);
  }
  return rows.map((r) => r.sort((a, b) => a.x0 - b.x0).map((w) => w.text).join(" ")).join(" ");
}

/** The label belonging to the value: the phrase left of it on the same line, else the one right above. */
function anchorFor(page: PdfPageWords, words: PdfWord[]): Selection["anchor"] {
  const first = [...words].sort((a, b) => a.top - b.top || a.x0 - b.x0)[0];
  const left = lineOf(page.words, first).filter((w) => w.x1 <= first.x0 + 0.5 && first.x0 - w.x1 < 220);
  if (left.length) {
    const run = [left[left.length - 1]];
    for (let i = left.length - 2; i >= 0 && close(left[i], run[0]); i--) run.unshift(left[i]);
    const text = run.map((w) => w.text).join(" ");
    if (/[A-Za-zÄÖÜäöüß]/.test(text)) return { text, x0: run[0].x0, top: Math.min(...run.map((w) => w.top)) };
  }
  const x0 = Math.min(...words.map((w) => w.x0));
  const x1 = Math.max(...words.map((w) => w.x1));
  const above = page.words
    .filter((w) => w.bottom <= first.top + 0.5 && first.top - w.bottom < 30 && w.x1 > x0 - 2 && w.x0 < x1 + 2)
    .sort((a, b) => b.bottom - a.bottom || a.x0 - b.x0);
  if (above.length) {
    const line = lineOf(page.words, above[0]);
    let run = [above[0]];
    const i = line.indexOf(above[0]);
    for (let j = i - 1; j >= 0 && close(line[j], run[0]); j--) run = [line[j], ...run];
    for (let j = i + 1; j < line.length && close(run[run.length - 1], line[j]); j++) run = [...run, line[j]];
    return { text: run.map((w) => w.text).join(" "), x0: run[0].x0, top: Math.min(...run.map((w) => w.top)) };
  }
  return null;
}

export function makeSelection(page: PdfPageWords, words: PdfWord[]): Selection | null {
  if (!words.length) return null;
  return {
    page: page.page,
    words,
    text: textOf(words),
    box: {
      x0: Math.min(...words.map((w) => w.x0)),
      top: Math.min(...words.map((w) => w.top)),
      x1: Math.max(...words.map((w) => w.x1)),
      bottom: Math.max(...words.map((w) => w.bottom)),
    },
    anchor: anchorFor(page, words),
  };
}

/** Text from the PDF -> value for the form field (German dates/numbers converted). */
export function valueFor(path: string, inputType: string, text: string): string | null {
  const t = text.trim();
  if (inputType === "date") {
    const m = t.match(/(\d{1,2})\.(\d{1,2})\.(\d{2,4})/);
    if (m) {
      const y = m[3].length === 2 ? `20${m[3]}` : m[3];
      return `${y}-${m[2].padStart(2, "0")}-${m[1].padStart(2, "0")}`;
    }
    return /^\d{4}-\d{2}-\d{2}/.test(t) ? t.slice(0, 10) : null;
  }
  if (/(quantity|unit_price|vat_rate)$/.test(path)) {
    const m = t.match(/-?\d{1,3}(?:\.\d{3})*(?:,\d+)?|-?\d+(?:,\d+)?/);
    return m ? m[0].replace(/\./g, "").replace(",", ".") : null;
  }
  if (/vat_id$/.test(path)) return t.replace(/\s+/g, "").toUpperCase();
  return t;
}
