"""Rule-based field extraction from PDF text.

`_generic.yaml` always applies; the first layout template whose `match.any`
string occurs in the text is merged on top of it (dict keys override,
lists replace). Adding a layout = adding a YAML file, no code change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
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
POSTCODE_CITY = re.compile(r"^(?:(?P<cc>[A-Z]{1,2})-)?(?P<postcode>\d{4,5})\s+(?P<city>.+)$")


class ExtractionResult(BaseModel):
    invoice: Invoice
    stated_totals: StatedTotals = Field(default_factory=StatedTotals)
    template_id: str
    template_name: str
    warnings: list[str] = Field(default_factory=list)
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


def _compile(pattern: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(pattern.replace("{AMOUNT}", AMOUNT).replace("{DATE}", DATE), flags | re.IGNORECASE)


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


def extract_lines(cfg: dict[str, Any], text: str, default_rate: Decimal) -> tuple[list[LineItem], list[str]]:
    lcfg = cfg.get("lines") or {}
    pattern = _compile(lcfg["pattern"]) if lcfg.get("pattern") else None
    if pattern is None:
        return [], ["Vorlage enthält kein Positionsmuster."]
    start = _compile(lcfg["start"]) if lcfg.get("start") else None
    end = _compile(lcfg["end"]) if lcfg.get("end") else None

    rows = text.splitlines()
    if start and any(start.search(r) for r in rows):
        rows = rows[next(i for i, r in enumerate(rows) if start.search(r)) + 1 :]
    if end:
        cut = next((i for i, r in enumerate(rows) if end.search(r)), None)
        if cut is not None:
            rows = rows[:cut]

    lines: list[LineItem] = []
    warnings: list[str] = []
    for row in rows:
        m = pattern.match(row.strip())
        if not m:
            continue
        g = m.groupdict()
        qty, price, total = _dec(g.get("quantity")), _dec(g.get("unit_price")), _dec(g.get("total"))
        if qty is None or price is None:
            continue
        rate = _dec(g.get("vat_rate")) or default_rate
        item = LineItem(
            id=g.get("id") or str(len(lines) + 1),
            name=g["name"].strip(),
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


def extract_buyer(cfg: dict[str, Any], pdf: PdfText, seller_name: str) -> Party:
    region = cfg.get("buyer_region")
    block = pdf.region_text(tuple(region)) if region else ""  # type: ignore[arg-type]
    rows = [r.strip() for r in block.splitlines() if r.strip()]
    seller_key = seller_name.lower()[:12]
    rows = [r for r in rows if not (seller_key and seller_key in r.lower())]

    party = Party()
    pc_index = next((i for i, r in enumerate(rows) if POSTCODE_CITY.match(r)), None)
    if pc_index is None:
        return party
    m = POSTCODE_CITY.match(rows[pc_index])
    assert m
    # Typical block: name, [extra lines like "z. Hd. ..."], street, postcode + city
    names = rows[: max(pc_index - 1, 0)]
    party.name = names[0] if names else ""
    party.address = Address(
        line1=rows[pc_index - 1] if pc_index >= 1 else "",
        line2=" ".join(names[1:]),
        postcode=m["postcode"],
        city=m["city"].strip(),
        country_code="DE" if not m["cc"] or m["cc"] == "D" else m["cc"],
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

    lines, line_warnings = extract_lines(cfg, text, default_rate)
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
    if not invoice.number:
        warnings.append("Rechnungsnummer nicht erkannt.")
    if not invoice.issue_date:
        warnings.append("Rechnungsdatum nicht erkannt.")
    if not text.strip():
        warnings.append("Das PDF enthält keinen Text (Scan?). Texterkennung (OCR) ist noch nicht eingebaut.")

    # Totals: the last match is the final figure (earlier ones may be subtotals).
    # Tax is only compared with a single tax line; mixed rates print several.
    nets, taxes, grosses = (_find_all(cfg, f, text) for f in ("total_net", "total_tax", "total_gross"))
    stated = StatedTotals(
        net=_dec(nets[-1]) if nets else None,
        tax=_dec(taxes[0]) if len(taxes) == 1 else None,
        gross=_dec(grosses[-1]) if grosses else None,
    )
    return ExtractionResult(
        invoice=invoice,
        stated_totals=stated,
        template_id=tpl.id,
        template_name=tpl.name,
        warnings=warnings,
        raw_text=text,
    )
