import type { ArchivedDocument, ExtractionResult, Invoice, Profile, StatedTotals, ValidateResponse } from "./types";

export class ApiError extends Error {
  constructor(message: string, public details: string[] = []) {
    super(message);
  }
}

async function check(res: Response): Promise<Response> {
  if (res.ok) return res;
  let message = `Fehler ${res.status}`;
  let details: string[] = [];
  try {
    const body = await res.json();
    const d = body.detail;
    if (typeof d === "string") message = d;
    else if (d?.message) {
      message = d.message;
      details = (d.issues ?? []).map((i: { message: string }) => i.message);
    } else if (Array.isArray(d)) details = d.map((e: { msg: string; loc: unknown[] }) => `${e.loc.join(".")}: ${e.msg}`);
  } catch {
    /* non-JSON error body */
  }
  throw new ApiError(message, details);
}

function upload(file: File): FormData {
  const form = new FormData();
  form.append("file", file);
  return form;
}

export async function extractPdf(file: File): Promise<ExtractionResult> {
  const res = await check(await fetch("/api/extract", { method: "POST", body: upload(file) }));
  return res.json();
}

export async function importIdoc(file: File): Promise<Invoice> {
  const res = await check(await fetch("/api/erp/sap-ecc/idoc", { method: "POST", body: upload(file) }));
  return res.json();
}

export async function validateInvoice(
  invoice: Invoice,
  profile: Profile,
  stated: StatedTotals | null,
  signal?: AbortSignal,
): Promise<ValidateResponse> {
  const res = await check(
    await fetch("/api/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ invoice, profile, stated_totals: stated }),
      signal,
    }),
  );
  return res.json();
}

export interface ExportSource {
  kind: "pdf" | "idoc" | "manual";
  name: string;
}

export async function exportInvoice(invoice: Invoice, profile: Profile, pdf: File | null, source: ExportSource): Promise<Blob> {
  if (profile === "xrechnung") {
    const qs = new URLSearchParams({ source: source.kind, source_name: source.name });
    const res = await check(
      await fetch(`/api/export/xrechnung?${qs}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(invoice),
      }),
    );
    return res.blob();
  }
  const form = new FormData();
  form.append("invoice", JSON.stringify(invoice));
  form.append("source", source.kind);
  form.append("source_name", source.name);
  if (pdf) form.append("file", pdf);
  const res = await check(await fetch("/api/export/zugferd", { method: "POST", body: form }));
  return res.blob();
}

export async function listDocuments(): Promise<ArchivedDocument[]> {
  const res = await check(await fetch("/api/documents"));
  return res.json();
}

export function documentUrl(id: number): string {
  return `/api/documents/${id}/download`;
}
