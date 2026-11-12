// Mirrors backend/app/model.py. Decimals travel as strings, dates as "YYYY-MM-DD".

export type Profile = "zugferd" | "xrechnung";
export type VatCategory = "S" | "Z" | "E" | "AE" | "K" | "G";

export interface Address {
  line1: string;
  line2: string;
  postcode: string;
  city: string;
  country_code: string;
}

export interface Contact {
  name: string;
  phone: string;
  email: string;
}

export interface Party {
  name: string;
  address: Address;
  vat_id: string;
  tax_number: string;
  electronic_address: string;
  contact: Contact;
  erp_id: string;
}

export interface LineItem {
  id: string;
  name: string;
  seller_item_id: string;
  quantity: string;
  unit_code: string;
  unit_price: string;
  price_base_quantity: string; // price refers to this many units, e.g. "100" for "€ / 100 kg"
  vat_category: VatCategory;
  vat_rate: string;
}

export interface PaymentInfo {
  means_code: string;
  iban: string;
  bic: string;
  account_holder: string;
  terms: string;
  due_date: string | null;
}

export interface Invoice {
  number: string;
  issue_date: string | null;
  type_code: string;
  currency: string;
  buyer_reference: string;
  order_reference: string;
  delivery_date: string | null;
  note: string;
  seller: Party;
  buyer: Party;
  lines: LineItem[];
  payment: PaymentInfo;
}

export interface StatedTotals {
  net: string | null;
  tax: string | null;
  gross: string | null;
}

export interface ExtractionResult {
  invoice: Invoice;
  stated_totals: StatedTotals;
  template_id: string;
  template_name: string;
  warnings: string[];
  notes: string[];
  raw_text: string;
  ocr: boolean;
  company_id: string;
}

export interface Issue {
  code: string;
  field: string;
  message: string;
  severity: "error" | "warning";
}

export interface TaxBreakdown {
  category: VatCategory;
  rate: string;
  basis: string;
  amount: string;
}

export interface Totals {
  line_total: string;
  tax_basis_total: string;
  tax_total: string;
  grand_total: string;
  due_payable: string;
  breakdown: TaxBreakdown[];
}

export interface ValidateResponse {
  totals: Totals;
  issues: Issue[];
  /** mandatory form fields for this invoice and profile ("lines.0.name", "buyer.vat_id", ...) */
  required: string[];
}

export interface ArchivedDocument {
  id: number;
  created_at: string;
  created_by: string;
  source: string;
  source_name: string;
  profile: Profile;
  invoice_number: string;
  buyer_name: string;
  grand_total: string;
  currency: string;
  filename: string;
}

export type Role = "admin" | "user";

export interface CurrentUser {
  id: number;
  username: string;
  role: Role;
  active: boolean;
  must_change_password: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface Company {
  id: string;
  seller: Party;
  payment: PaymentInfo;
  keywords: string[];
}

export interface IdocSettings {
  enabled: boolean;
  profile: Profile;
  interval_seconds: number;
}

export interface PdfInboxSettings {
  enabled: boolean;
  profile: Profile;
  auto_export: boolean;
  force_ocr: boolean;
}

export interface CustomerImportSettings {
  enabled: boolean;
  master_wins: boolean;
  mapping: Record<string, string>;
}

export interface AdminSettings {
  companies: Company[];
  idoc: IdocSettings;
  pdf_inbox: PdfInboxSettings;
  customer_import: CustomerImportSettings;
}

export interface AuditEntry {
  id: number;
  at: string;
  username: string;
  action: string;
  detail: Record<string, unknown>;
}

export interface ImportResult {
  invoice: Invoice;
  notes: string[];
  company_id: string;
}

export interface DocumentPage {
  items: ArchivedDocument[];
  total: number;
}

export interface DocumentFilter {
  q?: string;
  date_from?: string;
  date_to?: string;
  profile?: Profile | "";
  source?: "pdf" | "idoc" | "manual" | "";
  offset?: number;
  limit?: number;
}

export interface CustomerInfo {
  id: number;
  name: string;
  party: Party;
  buyer_reference: string;
  updated_at: string;
}

export interface ErrorFile {
  name: string;
  size: number;
  modified_at: string;
  reason: string;
}

export type InboxKind = "idoc" | "pdf" | "stammdaten";

export interface InboxInfo {
  kind: InboxKind;
  label: string;
  folder_available: boolean;
  counts: Record<"inbox" | "outbox" | "processed" | "error", number>;
  errors: ErrorFile[];
}

export interface WorkerReport {
  last_round_at?: string;
  enabled?: boolean;
  pdf_enabled?: boolean;
  profile?: Profile;
  interval_seconds?: number;
  converted_total?: number;
  failed_total?: number;
  review_total?: number;
  last_converted_at?: string | null;
  last_failed_at?: string | null;
}

export interface InboxStatus {
  worker_alive: boolean;
  worker: WorkerReport;
  inboxes: InboxInfo[];
}

export interface ReviewInfo {
  id: number;
  created_at: string;
  status: "offen" | "erledigt" | "verworfen";
  source: "pdf" | "idoc";
  source_name: string;
  profile: Profile;
  invoice_number: string;
  buyer_name: string;
  issues: Issue[];
  resolved_by: string;
  document_id: number | null;
}

export interface ReviewDetail extends ReviewInfo {
  invoice: Invoice;
  stated_totals: StatedTotals | null;
  warnings: string[];
  notes: string[];
  template_name: string;
  has_pdf: boolean;
  company_id: string;
}

export interface Dashboard {
  review_open: number;
  documents_today: number;
  documents_total: number;
  worker: WorkerReport;
}

export interface CustomerRow {
  id: number;
  erp_id: string;
  name: string;
  city: string;
  country: string;
  vat_id: string;
  electronic_address: string;
  buyer_reference: string;
  source: "sap" | "rechnung";
  synced_at: string | null;
  updated_at: string;
}

export interface CustomerPage {
  items: CustomerRow[];
  total: number;
  missing_einvoice: number;
}

export interface ImportReport {
  file: string;
  at: string;
  rows: number;
  created: number;
  updated: number;
  skipped: number;
  missing_einvoice: number;
  mapping: Record<string, string>;
  unmapped_columns: string[];
  warnings: string[];
}

export interface CustomerImportInfo {
  last: ImportReport | null;
  fields: { key: string; label: string }[];
}

export interface DashboardStats {
  days: number;
  currency: string;
  daily: { date: string; automatic: number; manual: number }[];
  monthly: { month: string; count: number; volume: string }[];
  this_month: { count: number; volume: string };
  last_month: { count: number; volume: string };
  last_month_to_date: { count: number; volume: string };
  automation_rate: number | null;
  by_profile: Record<string, number>;
  by_source: Record<string, number>;
  top_buyers: { name: string; count: number; volume: string }[];
  review: { open: number; resolved: number; discarded: number; avg_hours_to_resolve: number | null };
}

export interface DocEndpoint {
  method: string;
  path: string;
  access: string;
  summary: string;
}

export interface PdfWord {
  text: string;
  x0: number;
  top: number;
  x1: number;
  bottom: number;
}

export interface PdfPageWords {
  page: number;
  width: number;
  height: number;
  words: PdfWord[];
}

export interface PdfWords {
  pages: PdfPageWords[];
  ocr: boolean;
  warning: string;
}

export interface FieldLinkIn {
  company_id: string;
  field: string;
  page: number;
  x0: number;
  top: number;
  x1: number;
  bottom: number;
  anchor_text: string;
  anchor_x0: number | null;
  anchor_top: number | null;
  sample: string;
}

export interface FieldLink extends FieldLinkIn {
  id: number;
  label: string;
  updated_by: string;
}
