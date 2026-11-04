"""SAP ECC source: billing documents exported as INVOIC02 IDocs (XML).

ECC writes one XML file per invoice into a folder (output via message
determination / XML-file port). This adapter reads those files, so the app
needs no SAP SDK, no RFC user and no network access to SAP.

The segment/qualifier mapping below follows the standard INVOIC02 basic type.
Systems are often customised — verify it against a real IDoc from your system
(transaction WE02/WE19) and adjust `MAPPING` if needed.

Segments used:
  E1EDK01  header: CURCY currency, EIGENUINR own VAT ID, KUNDEUINR customer VAT ID
  E1EDKA1  partners by PARVW: RS = invoicing party (seller), RE = bill-to (buyer)
  E1EDK02  references by QUALF: 009 = invoice no./date, 001 = customer order
  E1EDK03  dates by IDDAT: see MAPPING["dates"]
  E1EDK18  payment terms (TAGE days, ZTERM_TXT text)
  E1EDP01  items: POSEX, MENGE, MENEE (+ E1EDP19 text, E1EDP26 amounts, E1EDP04 tax)
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from lxml import etree

from ..company import Company
from ..model import Address, Contact, Invoice, LineItem, Party

MAPPING = {
    "seller_parvw": "RS",
    "buyer_parvw": "RE",
    "invoice_ref_qualf": "009",
    "order_ref_qualf": "001",
    "dates": {"issue": "012", "delivery": "011"},
    "item_net_qualf": "003",  # E1EDP26 QUALF 003 = item net value
    # SAP internal units -> UN/ECE Rec 20
    "units": {"ST": "H87", "PCE": "H87", "STK": "H87", "H": "HUR", "STD": "HUR",
              "KG": "KGM", "M": "MTR", "L": "LTR", "LE": "C62", "PAU": "LS"},
}


def _t(el: etree._Element | None, tag: str) -> str:
    if el is None:
        return ""
    child = el.find(tag)
    return (child.text or "").strip() if child is not None and child.text else ""


def _date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y%m%d").date() if value else None
    except ValueError:
        return None


def _dec(value: str, default: str = "0") -> Decimal:
    # SAP may send trailing minus ("12.50-") and no thousands separators.
    v = (value or default).strip()
    if v.endswith("-"):
        v = "-" + v[:-1]
    return Decimal(v)


def _party(seg: etree._Element | None) -> Party:
    if seg is None:
        return Party()
    street = " ".join(p for p in (_t(seg, "STRAS"), _t(seg, "HAUSN")) if p)
    return Party(
        name=" ".join(p for p in (_t(seg, "NAME1"), _t(seg, "NAME2")) if p),
        address=Address(
            line1=street,
            line2=_t(seg, "STRS2"),
            postcode=_t(seg, "PSTLZ"),
            city=_t(seg, "ORT01"),
            country_code=_t(seg, "LAND1") or "DE",
        ),
        contact=Contact(name=_t(seg, "BNAME"), phone=_t(seg, "TELF1")),
        erp_id=_t(seg, "PARTN"),
    )


def parse_invoic02(xml: bytes, company: Company) -> Invoice:
    root = etree.fromstring(xml)
    idoc = root.find("IDOC")
    if idoc is None:
        idoc = root if root.tag == "IDOC" else None
    if idoc is None or idoc.find("E1EDK01") is None:
        raise ValueError("Kein INVOIC02-IDoc (Segment E1EDK01 fehlt).")

    k01 = idoc.find("E1EDK01")
    partners = {_t(p, "PARVW"): p for p in idoc.findall("E1EDKA1")}
    refs = {_t(r, "QUALF"): r for r in idoc.findall("E1EDK02")}
    dates = {_t(d, "IDDAT"): _t(d, "DATUM") for d in idoc.findall("E1EDK03")}

    # Seller: own master data wins (complete incl. contact/e-address); the IDoc
    # only fills gaps. Buyer comes from the IDoc.
    seller = company.seller.model_copy(deep=True)
    idoc_seller = _party(partners.get(MAPPING["seller_parvw"]))
    if not seller.name:
        seller = idoc_seller
    seller.vat_id = seller.vat_id or _t(k01, "EIGENUINR")

    buyer = _party(partners.get(MAPPING["buyer_parvw"]))
    buyer.vat_id = _t(k01, "KUNDEUINR")

    inv_ref = refs.get(MAPPING["invoice_ref_qualf"])
    order_ref = refs.get(MAPPING["order_ref_qualf"])

    lines: list[LineItem] = []
    for p01 in idoc.findall("E1EDP01"):
        qty = _dec(_t(p01, "MENGE"), "1")
        amounts = {_t(a, "QUALF"): _t(a, "BETRG") for a in p01.findall("E1EDP26")}
        net = _dec(amounts.get(MAPPING["item_net_qualf"], "") or _t(p01, "NETWR"))
        tax = p01.find("E1EDP04")
        rate = _dec(_t(tax, "MSATZ"), "19")
        text_seg = p01.find("E1EDP19")
        lines.append(
            LineItem(
                id=_t(p01, "POSEX").lstrip("0") or str(len(lines) + 1),
                name=_t(text_seg, "KTEXT") or _t(text_seg, "IDTNR") or "Position",
                quantity=qty,
                unit_code=MAPPING["units"].get(_t(p01, "MENEE").upper(), "H87"),
                # SAP gives the net *value*; derive the unit price with enough
                # decimals that round2(quantity × price) reproduces SAP's figure.
                unit_price=(net / qty).quantize(Decimal("0.000001")).normalize() if qty else net,
                vat_category="S" if rate > 0 else "Z",
                vat_rate=rate,
            )
        )

    payment = company.payment.model_copy(deep=True)
    k18 = idoc.find("E1EDK18")
    if k18 is not None and _t(k18, "ZTERM_TXT"):
        payment.terms = _t(k18, "ZTERM_TXT")

    return Invoice(
        number=_t(inv_ref, "BELNR"),
        issue_date=_date(_t(inv_ref, "DATUM")) or _date(dates.get(MAPPING["dates"]["issue"], "")),
        delivery_date=_date(dates.get(MAPPING["dates"]["delivery"], "")),
        currency=_t(k01, "CURCY") or "EUR",
        type_code="381" if _t(k01, "BSART") == "CRME" else "380",
        order_reference=_t(order_ref, "BELNR"),
        seller=seller,
        buyer=buyer,
        lines=lines,
        payment=payment,
    )


class SapEccIdocFolderAdapter:
    """Reads INVOIC02 XML files that ECC drops into `inbox`."""

    name = "SAP ECC (INVOIC02-IDoc, Ordner)"

    def __init__(self, inbox: Path, company: Company) -> None:
        self.inbox = inbox
        self.company = company

    def list_new_invoices(self, since: datetime | None = None) -> list[str]:
        files = sorted(self.inbox.glob("*.xml"))
        if since is not None:
            ts = since.timestamp()
            files = [f for f in files if f.stat().st_mtime > ts]
        return [f.name for f in files]

    def get_invoice(self, invoice_id: str) -> Invoice:
        path = (self.inbox / invoice_id).resolve()
        if path.parent != self.inbox.resolve():
            raise ValueError("Ungültige IDoc-ID.")
        return parse_invoic02(path.read_bytes(), self.company)
