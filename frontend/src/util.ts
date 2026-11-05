import { ApiError } from "./api";

export function eur(value: string | number | null | undefined, currency = "EUR"): string {
  if (value == null || value === "") return "–";
  return Number(value).toLocaleString("de-DE", { style: "currency", currency });
}

export function describe(err: unknown): string[] {
  if (err instanceof ApiError) return [err.message, ...err.details];
  return [String(err)];
}

export function download(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function fmtDateTime(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) : "–";
}

export function isIdoc(file: File): boolean {
  return file.name.toLowerCase().endsWith(".xml");
}
