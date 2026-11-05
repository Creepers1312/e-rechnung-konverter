import { useEffect, useState } from "react";
import { searchCustomers } from "./api";
import type { CustomerInfo, Invoice } from "./types";

/** Search the buyer address book and copy a stored customer into the invoice. */
export function BuyerLookup({
  invoice,
  update,
}: {
  invoice: Invoice;
  update: (path: string, value: unknown) => void;
}) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<CustomerInfo[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      searchCustomers(q, ctrl.signal)
        .then(setHits)
        .catch(() => setHits([]));
    }, 200);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [q, open]);

  function choose(c: CustomerInfo) {
    update("buyer", c.party);
    if (c.buyer_reference && !invoice.buyer_reference) update("buyer_reference", c.buyer_reference);
    setOpen(false);
    setQ("");
  }

  return (
    <div className="lookup">
      <input
        type="search"
        placeholder="Aus Adressbuch übernehmen … (Name oder Kundennummer)"
        aria-label="Käufer im Adressbuch suchen"
        value={q}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onChange={(e) => setQ(e.target.value)}
      />
      {open && hits.length > 0 && (
        <ul className="lookup-list" role="listbox">
          {hits.map((c) => (
            <li key={c.id}>
              <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => choose(c)}>
                <strong>{c.name}</strong>
                <span>
                  {[c.party.erp_id && `Kd.-Nr. ${c.party.erp_id}`, c.party.address.city, c.party.electronic_address]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {open && hits.length === 0 && q && <div className="lookup-list empty">Kein Eintrag gefunden.</div>}
    </div>
  );
}
