import type {
  AdminSettings,
  AuditEntry,
  CurrentUser,
  CustomerImportInfo,
  CustomerInfo,
  CustomerPage,
  DocumentFilter,
  DocumentPage,
  ExtractionResult,
  Dashboard,
  ImportReport,
  ImportResult,
  InboxKind,
  InboxStatus,
  ReviewDetail,
  ReviewInfo,
  Invoice,
  Profile,
  Role,
  StatedTotals,
  ValidateResponse,
} from "./types";

export class ApiError extends Error {
  constructor(message: string, public details: string[] = []) {
    super(message);
  }
}

/** Fired when the server says the session is gone; App.tsx shows the login again. */
export const UNAUTHORIZED_EVENT = "erechnung:unauthorized";

/** fetch() with the CSRF header the backend requires on every write. */
function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("X-Requested-With", "erechnung");
  return fetch(input, { ...init, headers, credentials: "same-origin" });
}

async function check(res: Response, { notifyUnauthorized = true } = {}): Promise<Response> {
  if (res.ok) return res;
  if (res.status === 401 && notifyUnauthorized) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
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
  const res = await check(await apiFetch("/api/extract", { method: "POST", body: upload(file) }));
  return res.json();
}

export async function importIdoc(file: File): Promise<ImportResult> {
  const res = await check(await apiFetch("/api/erp/sap-ecc/idoc", { method: "POST", body: upload(file) }));
  return res.json();
}

export async function validateInvoice(
  invoice: Invoice,
  profile: Profile,
  stated: StatedTotals | null,
  signal?: AbortSignal,
): Promise<ValidateResponse> {
  const res = await check(
    await apiFetch("/api/validate", {
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
  reviewId?: number;  // marks this review item as done
}

export interface ExportResult {
  blob: Blob;
  documentId: number;
}

export async function exportInvoice(invoice: Invoice, profile: Profile, pdf: File | null, source: ExportSource): Promise<ExportResult> {
  if (profile === "xrechnung") {
    const qs = new URLSearchParams({ source: source.kind, source_name: source.name });
    if (source.reviewId) qs.set("review_id", String(source.reviewId));
    const res = await check(
      await apiFetch(`/api/export/xrechnung?${qs}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(invoice),
      }),
    );
    return { blob: await res.blob(), documentId: Number(res.headers.get("X-Document-Id")) };
  }
  const form = new FormData();
  form.append("invoice", JSON.stringify(invoice));
  form.append("source", source.kind);
  form.append("source_name", source.name);
  if (source.reviewId) form.append("review_id", String(source.reviewId));
  if (pdf) form.append("file", pdf);
  const res = await check(await apiFetch("/api/export/zugferd", { method: "POST", body: form }));
  return { blob: await res.blob(), documentId: Number(res.headers.get("X-Document-Id")) };
}

export async function listDocuments(filter: DocumentFilter = {}): Promise<DocumentPage> {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(filter)) if (v !== undefined && v !== "") params.set(k, String(v));
  const res = await check(await apiFetch(`/api/documents?${params}`));
  return res.json();
}

/** Selected archive entries as one ZIP file. */
export async function downloadZip(ids: number[]): Promise<Blob> {
  const res = await check(
    await apiFetch("/api/documents/zip", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    }),
  );
  return res.blob();
}

export async function searchCustomers(q: string, signal?: AbortSignal): Promise<CustomerInfo[]> {
  const res = await check(await apiFetch(`/api/customers?${new URLSearchParams({ q })}`, { signal }));
  return res.json();
}

export function documentUrl(id: number): string {
  return `/api/documents/${id}/download`;
}

// --- auth ---------------------------------------------------------------------

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

/** null when not logged in. */
export async function getMe(): Promise<CurrentUser | null> {
  const res = await apiFetch("/api/auth/me");
  if (res.status === 401) return null;
  return (await check(res)).json();
}

export async function login(username: string, password: string): Promise<CurrentUser> {
  const res = await check(await apiFetch("/api/auth/login", json("POST", { username, password })), {
    notifyUnauthorized: false,
  });
  return res.json();
}

export async function logout(): Promise<void> {
  await apiFetch("/api/auth/logout", { method: "POST" });
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  await check(
    await apiFetch("/api/auth/password", json("POST", { current_password: currentPassword, new_password: newPassword })),
  );
}

// --- admin --------------------------------------------------------------------

export async function listUsers(): Promise<CurrentUser[]> {
  return (await check(await apiFetch("/api/admin/users"))).json();
}

export async function createUser(username: string, role: Role, password: string): Promise<CurrentUser> {
  return (await check(await apiFetch("/api/admin/users", json("POST", { username, role, password })))).json();
}

export async function updateUser(
  id: number,
  change: { role?: Role; active?: boolean; password?: string },
): Promise<CurrentUser> {
  return (await check(await apiFetch(`/api/admin/users/${id}`, json("PATCH", change)))).json();
}

export async function getSettings(): Promise<AdminSettings> {
  return (await check(await apiFetch("/api/admin/settings"))).json();
}

export async function saveSettings(settings: AdminSettings): Promise<AdminSettings> {
  return (await check(await apiFetch("/api/admin/settings", json("PUT", settings)))).json();
}

export async function getAudit(action?: string): Promise<AuditEntry[]> {
  const qs = action ? `?${new URLSearchParams({ action })}` : "";
  return (await check(await apiFetch(`/api/admin/audit${qs}`))).json();
}

export async function getInboxStatus(): Promise<InboxStatus> {
  return (await check(await apiFetch("/api/admin/inbox/status"))).json();
}

export async function retryInboxFile(kind: InboxKind, name: string): Promise<void> {
  await check(await apiFetch("/api/admin/inbox/retry", json("POST", { kind, name })));
}

// --- review queue + dashboard ------------------------------------------------------

export async function getDashboard(): Promise<Dashboard> {
  return (await check(await apiFetch("/api/dashboard"))).json();
}

export async function listReview(): Promise<ReviewInfo[]> {
  return (await check(await apiFetch("/api/review"))).json();
}

export async function getReview(id: number): Promise<ReviewDetail> {
  return (await check(await apiFetch(`/api/review/${id}`))).json();
}

export async function getReviewPdf(id: number, name: string): Promise<File> {
  const blob = await (await check(await apiFetch(`/api/review/${id}/pdf`))).blob();
  return new File([blob], name || "rechnung.pdf", { type: "application/pdf" });
}

export async function discardReview(id: number): Promise<void> {
  await check(await apiFetch(`/api/review/${id}/discard`, { method: "POST" }));
}

// --- SAP customer master ------------------------------------------------------------

export async function listCustomerMaster(params: { q?: string; missing_einvoice?: boolean; source?: string }): Promise<CustomerPage> {
  const qs = new URLSearchParams({ limit: "500" });
  if (params.q) qs.set("q", params.q);
  if (params.missing_einvoice) qs.set("missing_einvoice", "true");
  if (params.source) qs.set("source", params.source);
  return (await check(await apiFetch(`/api/admin/customers?${qs}`))).json();
}

export async function getCustomerImportInfo(): Promise<CustomerImportInfo> {
  return (await check(await apiFetch("/api/admin/customers/import"))).json();
}

export async function importCustomerMaster(file: File): Promise<ImportReport> {
  const form = new FormData();
  form.append("file", file);
  return (await check(await apiFetch("/api/admin/customers/import", { method: "POST", body: form }))).json();
}
