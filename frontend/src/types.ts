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
  quantity: string;
  unit_code: string;
  unit_price: string;
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
  raw_text: string;
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
}
