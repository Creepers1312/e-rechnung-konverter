import { useEffect, useState } from "react";

/** Pages of the app; the URL hash keeps the browser's back/forward buttons and bookmarks working. */
export type Page = "start" | "pruefen" | "umwandeln" | "archiv" | "verwaltung" | "passwort";
const PAGES: Page[] = ["start", "pruefen", "umwandeln", "archiv", "verwaltung", "passwort"];

function current(): Page {
  const p = window.location.hash.replace(/^#\/?/, "") as Page;
  return PAGES.includes(p) ? p : "start";
}

export function navigate(page: Page) {
  if (current() !== page) window.location.hash = `/${page}`;
}

export function usePage(): Page {
  const [page, setPage] = useState<Page>(current);
  useEffect(() => {
    const onChange = () => setPage(current());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return page;
}
