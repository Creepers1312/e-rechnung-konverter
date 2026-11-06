"""Rule-based field extraction from PDF text.

`_generic.yaml` always applies; the first layout template whose `match.any`
string occurs in the text is merged on top of it (dict keys override,
lists replace). Adding a layout = adding a YAML file, no code change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from ..company import Company
from ..model import Address, Invoice, LineItem, Party, parse_decimal, round2
from ..validation import StatedTotals
from .pdf_text import PdfText

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

AMOUNT = r"-?\d{1,3}(?:\.\d{3})*,\d{2}|-?\d+,\d{2}"
DATE = r"\d{1,2}\.\d{1,2}\.\d{2,4}"
NUMBER = r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?"  # quantities: 1.000 / 23,820 / 23.820 (OCR)
SEP = r"[ \t]*:?[ \t]*"  # between label and value, never across a line break
POSTCODE_CITY = re.compile(r"^(?:(?P<cc>[A-Z]{1,2})-)?(?P<postcode>\d{4,5})\s+(?P<city>.+)$")


class ExtractionResult(BaseModel):
    invoice: Invoice
    stated_totals: StatedTotals = Field(default_factory=StatedTotals)
    template_id: str
    template_name: str
    company_id: str = ""  # own company the invoice was recognised for (see recognize.py)
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)  # informational, e.g. "filled from address book"
    ocr: bool = False  # text came from OCR (scanned PDF)
    raw_text: str = ""


@dataclass
class Template:
    id: str
    name: str
    config: dict[str, Any]
    match_any: list[str] = field(default_factory=list)


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_templates(directory: Path = TEMPLATE_DIR) -> tuple[Template, list[Template]]:
    generic: Template | None = None
    layouts: list[Template] = []
    for path in sorted(directory.glob("*.yaml")):
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        tpl = Template(
            id=cfg.get("id", path.stem),
            name=cfg.get("name", path.stem),
            config=cfg,
            match_any=list((cfg.get("match") or {}).get("any") or []),
        )
        if path.stem == "_generic":
            generic = tpl
        else:
            layouts.append(tpl)
    if generic is None:
        raise RuntimeError(f"{directory}/_generic.yaml is missing")
    return generic, layouts


def select_template(text: str, generic: Template, layouts: list[Template]) -> Template:
    lowered = text.lower()
    for tpl in layouts:
        if any(s.lower() in lowered for s in tpl.match_any):
            merged = _merge(generic.config, {k: v for k, v in tpl.config.items() if k != "match"})
            return Template(id=tpl.id, name=tpl.name, config=merged, match_any=tpl.match_any)
    return generic


# wrapped in (?:...) so a placeholder's own alternatives ("|") cannot split the surrounding pattern
PLACEHOLDERS = {
    key: f"(?:{value})" for key, value in {"{AMOUNT}": AMOUNT, "{DATE}": DATE, "{NUMBER}": NUMBER, "{SEP}": SEP}.items()
}


def _compile(pattern: str, flags: int = 0) -> re.Pattern[str]:
    for key, value in PLACEHOLDERS.items():
        pattern = pattern.replace(key, value)
    return re.compile(pattern, flags | re.IGNORECASE)


def _value(m: re.Match[str]) -> str | None:
    """First non-empty capture group (patterns may use alternatives)."""
    return next((g.strip() for g in m.groups() if g), None)


def _find(cfg: dict[str, Any], name: str, text: str) -> str | None:
    pattern = (cfg.get("fields") or {}).get(name)
    if not pattern:
        return None
    m = _compile(pattern, re.MULTILINE).search(text)
    return _value(m) if m else None


def _find_all(cfg: dict[str, Any], name: str, text: str) -> list[str]:
    pattern = (cfg.get("fields") or {}).get(name)
    if not pattern:
        return []
    return [v for m in _compile(pattern, re.MULTILINE).finditer(text) if (v := _value(m))]


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        d, m, y = (int(p) for p in value.split("."))
        if y < 100:
            y += 2000
        return date(y, m, d)
    except ValueError:
        return None


def _dec(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(parse_decimal(value)))
    except InvalidOperation:
        return None


def _unit_code(cfg: dict[str, Any], unit: str | None) -> str:
    units = {str(k).lower(): str(v) for k, v in (cfg.get("units") or {}).items()}
    if not unit:
        return "H87"
    return units.get(unit.lower().rstrip("."), "H87")


def _quantity_candidates(value: str | None) -> list[Decimal]:
    """Possible readings of a printed quantity, most likely first.

    "1.000,5" and "23,820" are unambiguous. "23.820" is not: German thousands
    separator (23820) or a comma misread by OCR (23.82)? Both are returned and the
    caller picks the one that matches the printed amounts.
    """
    if value is None:
        return []
    v = value.strip()
    if "," not in v and re.fullmatch(r"\d{1,3}\.\d{3}", v):
        return [d for d in (_dec(v.replace(".", ",")), _dec(v.replace(".", ""))) if d is not None]
    d = _dec(v)
    return [d] if d is not None else []


def _pick_quantity(candidates: list[Decimal], price: Decimal, target: Decimal | None) -> Decimal:
    if target is not None:
        for c in candidates:
            if abs(round2(c * price) - target) <= Decimal("0.01"):
                return c
    return candidates[0]


def _line_region(lcfg: dict[str, Any], text: str, *, include_start: bool) -> str:
    rows = text.splitlines()
    start = _compile(lcfg["start"]) if lcfg.get("start") else None
    end = _compile(lcfg["end"]) if lcfg.get("end") else None
    if start:
        first = next((i for i, r in enumerate(rows) if start.search(r)), None)
        if first is not None:
            rows = rows[first if include_start else first + 1 :]
    if end:
        cut = next((i for i, r in enumerate(rows) if i > 0 and end.search(r)), None)
        if cut is not None:
            rows = rows[:cut]
    return "\n".join(rows)


def extract_lines(
    cfg: dict[str, Any], text: str, default_rate: Decimal, net_total: Decimal | None = None
) -> tuple[list[LineItem], list[str]]:
    lcfg = cfg.get("lines") or {}
    if lcfg.get("block_pattern"):
        # one item spread over several lines: search the whole region
        region = _line_region(lcfg, text, include_start=True)
        matches = list(_compile(lcfg["block_pattern"], re.MULTILINE).finditer(region))
    elif lcfg.get("pattern"):
        pattern = _compile(lcfg["pattern"])
        region = _line_region(lcfg, text, include_start=False)
        matches = [m for row in region.splitlines() if (m := pattern.match(row.strip()))]
    else:
        return [], ["Vorlage enthält kein Positionsmuster."]

    lines: list[LineItem] = []
    warnings: list[str] = []
    for m in matches:
        g = m.groupdict()
        candidates, price, total = (
            _quantity_candidates(g.get("quantity")),
            _dec(g.get("unit_price")),
            _dec(g.get("total")),
        )
        if not candidates or price is None or not (g.get("name") or "").strip():
            continue
        # without a line total, a single-item invoice can be checked against the printed net total
        qty = _pick_quantity(
            candidates, price, total if total is not None else (net_total if len(matches) == 1 else None)
        )
        rate = _dec(g.get("vat_rate")) or default_rate
        item = LineItem(
            id=g.get("id") or str(len(lines) + 1),
            name=" ".join(g["name"].split()),
            seller_item_id=(g.get("article") or "").strip(),
            quantity=qty,
            unit_code=_unit_code(cfg, g.get("unit")),
            unit_price=price,
            vat_category=(cfg.get("defaults") or {}).get("vat_category", "S"),
            vat_rate=rate,
        )
        if total is not None and abs(round2(qty * price) - total) > Decimal("0.01"):
            warnings.append(
                f"Position '{item.name}': Menge × Preis = {round2(qty * price)} ≠ {total} im PDF "
                "(Rabatt oder Lesefehler?)."
            )
        lines.append(item)
    if not lines:
        warnings.append("Keine Positionen erkannt – bitte manuell erfassen oder eine Layout-Vorlage anlegen.")
    return lines, warnings


# "D-79618", "A-1010", "CH-4310": vehicle registration style prefixes -> ISO 3166 codes
POSTCODE_PREFIX = {
    "D": "DE",
    "A": "AT",
    "CH": "CH",
    "F": "FR",
    "I": "IT",
    "B": "BE",
    "NL": "NL",
    "L": "LU",
    "PL": "PL",
    "CZ": "CZ",
    "DK": "DK",
    "S": "SE",
    "E": "ES",
    "P": "PT",
    "H": "HU",
    "SK": "SK",
}
COUNTRY_NAMES = {
    "deutschland": "DE",
    "germany": "DE",
    "österreich": "AT",
    "oesterreich": "AT",
    "austria": "AT",
    "schweiz": "CH",
    "switzerland": "CH",
    "frankreich": "FR",
    "france": "FR",
    "italien": "IT",
    "italy": "IT",
    "niederlande": "NL",
    "netherlands": "NL",
    "belgien": "BE",
    "belgium": "BE",
    "luxemburg": "LU",
    "polen": "PL",
    "poland": "PL",
    "tschechien": "CZ",
    "czech republic": "CZ",
    "dänemark": "DK",
    "schweden": "SE",
    "sweden": "SE",
    "spanien": "ES",
    "spain": "ES",
    "ungarn": "HU",
    "slowakei": "SK",
}
# return address above the window: "Firma GmbH, Straße 2, D-79618 Ort" (or with "·" / "|")
RETURN_LINE = re.compile(r"[,·|].*\b(?:[A-Z]{1,2}-)?\d{4,5}\s+\S")


def extract_buyer(cfg: dict[str, Any], pdf: PdfText, seller_name: str) -> Party:
    region = cfg.get("buyer_region")
    block = pdf.region_text(tuple(region)) if region else ""  # type: ignore[arg-type]
    rows = [r.strip() for r in block.splitlines() if r.strip()]
    seller_key = seller_name.lower()[:12]
    rows = [r for r in rows if not (seller_key and seller_key in r.lower()) and not RETURN_LINE.search(r)]

    party = Party()
    pc_index = next((i for i, r in enumerate(rows) if POSTCODE_CITY.match(r)), None)
    if pc_index is None:
        return party
    m = POSTCODE_CITY.match(rows[pc_index])
    assert m
    country = POSTCODE_PREFIX.get(m["cc"] or "D", m["cc"] or "DE")
    # a country line right below "PLZ Ort" wins over the prefix
    if pc_index + 1 < len(rows) and rows[pc_index + 1].strip().lower() in COUNTRY_NAMES:
        country = COUNTRY_NAMES[rows[pc_index + 1].strip().lower()]
    # Typical block: name, [extra lines like "z. Hd. ..."], street, postcode + city
    names = rows[: max(pc_index - 1, 0)]
    party.name = names[0] if names else ""
    party.address = Address(
        line1=rows[pc_index - 1] if pc_index >= 1 else "",
        line2=" ".join(names[1:]),
        postcode=m["postcode"],
        city=m["city"].strip(),
        country_code=country,
    )
    return party


def extract(pdf: PdfText, company: Company, templates_dir: Path = TEMPLATE_DIR) -> ExtractionResult:
    generic, layouts = load_templates(templates_dir)
    tpl = select_template(pdf.text, generic, layouts)
    cfg = tpl.config
    text = pdf.text
    warnings: list[str] = []

    defaults = cfg.get("defaults") or {}
    default_rate = _dec(_find(cfg, "vat_rate", text)) or Decimal(str(defaults.get("vat_rate", 19)))

    # Totals: the last match is the final figure (earlier ones may be subtotals).
    # Tax is only compared with a single tax line; mixed rates print several.
    nets, taxes, grosses = (_find_all(cfg, f, text) for f in ("total_net", "total_tax", "total_gross"))
    stated = StatedTotals(
        net=_dec(nets[-1]) if nets else None,
        tax=_dec(taxes[0]) if len(taxes) == 1 else None,
        gross=_dec(grosses[-1]) if grosses else None,
    )

    lines, line_warnings = extract_lines(cfg, text, default_rate, stated.net)
    warnings += line_warnings

    buyer = extract_buyer(cfg, pdf, company.seller.name)
    buyer.vat_id = (_find(cfg, "buyer_vat_id", text) or "").replace(" ", "")
    buyer.erp_id = _find(cfg, "buyer_erp_id", text) or ""
    buyer.electronic_address = _find(cfg, "buyer_email", text) or ""
    if not buyer.name:
        warnings.append("Käuferanschrift nicht erkannt – bitte ergänzen.")

    payment = company.payment.model_copy(deep=True)
    payment.terms = _find(cfg, "terms", text) or payment.terms
    payment.due_date = parse_date(_find(cfg, "due_date", text))

    invoice = Invoice(
        number=_find(cfg, "number", text) or "",
        issue_date=parse_date(_find(cfg, "issue_date", text)),
        delivery_date=parse_date(_find(cfg, "delivery_date", text)),
        order_reference=_find(cfg, "order_reference", text) or "",
        buyer_reference=_find(cfg, "buyer_reference", text) or "",
        seller=company.seller.model_copy(deep=True),
        buyer=buyer,
        lines=lines,
        payment=payment,
    )
    notes: list[str] = []
    if invoice.payment.due_date is None and invoice.issue_date:
        days = [int(d) for d in _find_all(cfg, "due_days", text) if d.isdigit()]
        if days:
            invoice.payment.due_date = invoice.issue_date + timedelta(days=max(days))
            notes.append(f"Fälligkeit aus Zahlungsziel berechnet: {max(days)} Tage nach Rechnungsdatum.")

    if not invoice.number:
        warnings.append("Rechnungsnummer nicht erkannt.")
    if not invoice.issue_date:
        warnings.append("Rechnungsdatum nicht erkannt.")
    if not text.strip():
        warnings.append("Das PDF enthält keinen lesbaren Text (Scan?) und die Texterkennung (OCR) war nicht verfügbar.")
    elif pdf.ocr:
        notes.append("Gescanntes PDF – Text per OCR erkannt. Werte bitte besonders sorgfältig prüfen.")
    seller_words = [w for w in re.findall(r"\w{4,}", company.seller.name) if w.lower() not in ("gmbh", "mbh")]
    if text.strip() and seller_words and not any(w.lower() in text.lower() for w in seller_words[:2]):
        warnings.append(
            f"Absender im PDF passt nicht zu den eigenen Firmendaten („{company.seller.name}“). "
            "Bitte unter Verwaltung → Einstellungen prüfen."
        )

    return ExtractionResult(
        invoice=invoice,
        stated_totals=stated,
        template_id=tpl.id,
        template_name=tpl.name,
        warnings=warnings,
        notes=notes,
        raw_text=text,
        ocr=pdf.ocr,
    )
