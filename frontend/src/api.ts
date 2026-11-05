import type {
  AdminSettings,
  AuditEntry,
  CurrentUser,
  CustomerInfo,
  DocumentFilter,
  DocumentPage,
  ExtractionResult,
  IdocStatus,
  ImportResult,
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
}

export interface ExportResult {
  blob: Blob;
  documentId: number;
}

export async function exportInvoice(invoice: Invoice, profile: Profile, pdf: File | null, source: ExportSource): Promise<ExportResult> {
  if (profile === "xrechnung") {
    const qs = new URLSearchParams({ source: source.kind, source_name: source.name });
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

export async function getIdocStatus(): Promise<IdocStatus> {
  return (await check(await apiFetch("/api/admin/idoc/status"))).json();
}

export async function retryIdoc(name: string): Promise<void> {
  await check(await apiFetch("/api/admin/idoc/retry", json("POST", { name })));
}
