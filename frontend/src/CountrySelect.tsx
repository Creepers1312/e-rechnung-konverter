import { useEffect, useMemo, useRef, useState } from "react";
import { listCountries } from "./api";
import type { Issue } from "./types";

export interface Country {
  code: string;
  name: string;
}

const FAVOURITES = ["DE", "AT", "CH", "FR", "IT", "NL", "BE", "LU", "PL", "CZ", "ES", "GB", "US"];

let cache: Promise<Country[]> | null = null;
export function loadCountries(): Promise<Country[]> {
  cache ??= listCountries().catch((e) => {
    cache = null;
    throw e;
  });
  return cache;
}

function norm(text: string): string {
  return text
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/ß/g, "ss")
    .trim();
}

const EN = typeof Intl.DisplayNames === "function" ? new Intl.DisplayNames(["en"], { type: "region" }) : null;

/** Code for a printed country name or code ("COSTA RICA" -> "CR"), using the loaded list. */
export function countryCodeOf(text: string, list: Country[]): string | null {
  const t = norm(text);
  if (!t) return null;
  const hit = list.find((c) => norm(c.code) === t || norm(c.name) === t || (EN && norm(EN.of(c.code) ?? "") === t));
  return hit?.code ?? null;
}

/** Country picker: type a code or name, pick from the list ("CR – Costa Rica"). */
export function CountrySelect({
  label = "Land",
  path,
  value,
  update,
  issues,
  required,
}: {
  label?: string;
  path: string;
  value: string;
  update: (path: string, value: unknown) => void;
  issues: Issue[];
  required?: boolean;
}) {
  const [list, setList] = useState<Country[]>([]);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    loadCountries()
      .then(setList)
      .catch(() => undefined);
  }, []);

  const current = list.find((c) => c.code === value);
  const shown = useMemo(() => {
    const q = norm(query);
    if (!q) {
      const fav = FAVOURITES.map((code) => list.find((c) => c.code === code)).filter(Boolean) as Country[];
      return [...fav, ...list.filter((c) => !FAVOURITES.includes(c.code))];
    }
    const en = (c: Country) => norm(EN?.of(c.code) ?? "");
    return list
      .filter((c) => norm(c.code) === q || norm(c.name).includes(q) || en(c).includes(q))
      .sort((a, b) => Number(norm(b.code) === q) - Number(norm(a.code) === q) || a.name.localeCompare(b.name, "de"));
  }, [query, list]);

  useEffect(() => {
    listRef.current?.children[active]?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const error = issues.find((i) => i.severity === "error" && i.field && (i.field === path || path.startsWith(`${i.field}.`)));
  const choose = (c: Country) => {
    update(path, c.code);
    setOpen(false);
    setQuery("");
  };

  return (
    <label className={`field country-field${error ? " invalid" : ""}`} title={error?.message}>
      <span>
        {label}
        {required && <abbr className="req" title="Pflichtangabe">*</abbr>}
      </span>
      <input
        data-path={path}
        aria-required={required || undefined}
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
        autoComplete="off"
        value={open ? query : current ? `${current.code} – ${current.name}` : value}
        placeholder={open ? `${value || ""} – Kürzel oder Name eingeben` : "Land wählen"}
        onFocus={() => {
          setOpen(true);
          setQuery("");
          setActive(0);
        }}
        onBlur={() => setOpen(false)}
        onChange={(e) => {
          setQuery(e.target.value);
          setActive(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((a) => Math.min(a + 1, shown.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, 0));
          } else if (e.key === "Enter" && open && shown[active]) {
            e.preventDefault();
            choose(shown[active]);
            (e.target as HTMLInputElement).blur();
          } else if (e.key === "Escape") {
            setOpen(false);
            (e.target as HTMLInputElement).blur();
          }
        }}
      />
      {open && (
        <ul className="country-list" role="listbox" ref={listRef}>
          {shown.length === 0 && <li className="muted">Kein Land gefunden</li>}
          {shown.map((c, i) => (
            <li
              key={c.code}
              role="option"
              aria-selected={c.code === value}
              className={`${i === active ? "active" : ""}${c.code === value ? " chosen" : ""}${
                !query && i === FAVOURITES.length - 1 ? " sep" : ""
              }`}
              onMouseDown={(e) => {
                e.preventDefault(); // keep focus until chosen
                choose(c);
              }}
              onMouseEnter={() => setActive(i)}
            >
              <code>{c.code}</code> {c.name}
            </li>
          ))}
        </ul>
      )}
    </label>
  );
}
