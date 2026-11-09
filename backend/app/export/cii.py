"""Generate UN/CEFACT Cross Industry Invoice (CII D16B) XML from an `Invoice`.

The same syntax is used for both outputs; only the guideline ID differs:
- ZUGFeRD / Factur-X, profile EN 16931 ("Comfort")
- XRechnung 3.0 (CII syntax)

Element order follows the CII XSD — do not reorder casually.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from lxml import etree

from ..model import Address, Invoice, Party, compute_totals

Profile = Literal["zugferd", "xrechnung"]

GUIDELINE_IDS: dict[Profile, str] = {
    "zugferd": "urn:cen.eu:en16931:2017",
    "xrechnung": "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0",
}

BUSINESS_PROCESS_ID = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"

NS = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "qdt": "urn:un:unece:uncefact:data:standard:QualifiedDataType:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
}


def _q(tag: str) -> str:
    prefix, local = tag.split(":")
    return f"{{{NS[prefix]}}}{local}"


def _sub(parent: etree._Element, tag: str, text: str | None = None, **attrs: str) -> etree._Element:
    el = etree.SubElement(parent, _q(tag), {k: v for k, v in attrs.items()})
    if text is not None:
        el.text = text
    return el


def _date(parent: etree._Element, tag: str, value: date) -> None:
    wrapper = _sub(parent, tag)
    _sub(wrapper, "udt:DateTimeString", value.strftime("%Y%m%d"), format="102")


def _amount(value: Decimal) -> str:
    return f"{value:.2f}"


def _number(value: Decimal) -> str:
    """Quantities / rates / unit prices: no exponent, no trailing zeros noise."""
    text = format(value.normalize(), "f")
    return text if "." not in text else text.rstrip("0").rstrip(".") or "0"


def _party(parent: etree._Element, tag: str, party: Party, *, with_contact: bool) -> None:
    el = _sub(parent, tag)
    _sub(el, "ram:Name", party.name)
    c = party.contact
    if with_contact and (c.name or c.phone or c.email):
        contact = _sub(el, "ram:DefinedTradeContact")
        if c.name:
            _sub(contact, "ram:PersonName", c.name)
        if c.phone:
            _sub(_sub(contact, "ram:TelephoneUniversalCommunication"), "ram:CompleteNumber", c.phone)
        if c.email:
            _sub(_sub(contact, "ram:EmailURIUniversalCommunication"), "ram:URIID", c.email)
    _address(el, party.address)
    if party.electronic_address:
        _sub(_sub(el, "ram:URIUniversalCommunication"), "ram:URIID", party.electronic_address, schemeID="EM")
    if party.vat_id:
        _sub(_sub(el, "ram:SpecifiedTaxRegistration"), "ram:ID", party.vat_id, schemeID="VA")
    if party.tax_number:
        _sub(_sub(el, "ram:SpecifiedTaxRegistration"), "ram:ID", party.tax_number, schemeID="FC")


def _address(parent: etree._Element, a: Address) -> None:
    addr = _sub(parent, "ram:PostalTradeAddress")
    if a.postcode:
        _sub(addr, "ram:PostcodeCode", a.postcode)
    if a.line1:
        _sub(addr, "ram:LineOne", a.line1)
    if a.line2:
        _sub(addr, "ram:LineTwo", a.line2)
    if a.city:
        _sub(addr, "ram:CityName", a.city)
    _sub(addr, "ram:CountryID", a.country_code or "DE")


# BT-120 exemption reasons are required for non-standard categories.
EXEMPTION_REASONS = {
    "E": "Steuerbefreit",
    "AE": "Steuerschuldnerschaft des Leistungsempfängers (Reverse Charge)",
    "K": "Steuerfreie innergemeinschaftliche Lieferung",
    "G": "Steuerfreie Ausfuhrlieferung",
}


def build_cii(invoice: Invoice, profile: Profile = "zugferd") -> bytes:
    totals = compute_totals(invoice)
    cur = invoice.currency

    root = etree.Element(_q("rsm:CrossIndustryInvoice"), nsmap=NS)

    ctx = _sub(root, "rsm:ExchangedDocumentContext")
    if profile == "xrechnung":  # BT-23, mandatory in XRechnung (PEPPOL-EN16931-R001)
        _sub(_sub(ctx, "ram:BusinessProcessSpecifiedDocumentContextParameter"), "ram:ID", BUSINESS_PROCESS_ID)
    _sub(_sub(ctx, "ram:GuidelineSpecifiedDocumentContextParameter"), "ram:ID", GUIDELINE_IDS[profile])

    doc = _sub(root, "rsm:ExchangedDocument")
    _sub(doc, "ram:ID", invoice.number)
    _sub(doc, "ram:TypeCode", invoice.type_code)
    _date(doc, "ram:IssueDateTime", invoice.issue_date or date.today())
    if invoice.note:
        _sub(_sub(doc, "ram:IncludedNote"), "ram:Content", invoice.note)

    tx = _sub(root, "rsm:SupplyChainTradeTransaction")

    for line in invoice.lines:
        item = _sub(tx, "ram:IncludedSupplyChainTradeLineItem")
        _sub(_sub(item, "ram:AssociatedDocumentLineDocument"), "ram:LineID", line.id)
        product = _sub(item, "ram:SpecifiedTradeProduct")
        if line.seller_item_id:
            _sub(product, "ram:SellerAssignedID", line.seller_item_id)
        _sub(product, "ram:Name", line.name)
        agreement = _sub(item, "ram:SpecifiedLineTradeAgreement")
        net_price = _sub(agreement, "ram:NetPriceProductTradePrice")
        _sub(net_price, "ram:ChargeAmount", _number(line.unit_price))
        if line.price_base_quantity != 1:  # BT-149/150, e.g. price per 100 kg
            _sub(net_price, "ram:BasisQuantity", _number(line.price_base_quantity), unitCode=line.unit_code)
        delivery = _sub(item, "ram:SpecifiedLineTradeDelivery")
        _sub(delivery, "ram:BilledQuantity", _number(line.quantity), unitCode=line.unit_code)
        settlement = _sub(item, "ram:SpecifiedLineTradeSettlement")
        tax = _sub(settlement, "ram:ApplicableTradeTax")
        _sub(tax, "ram:TypeCode", "VAT")
        _sub(tax, "ram:CategoryCode", line.vat_category)
        rate = line.vat_rate if line.vat_category == "S" else Decimal("0")
        _sub(tax, "ram:RateApplicablePercent", _number(rate))
        summ = _sub(settlement, "ram:SpecifiedTradeSettlementLineMonetarySummation")
        _sub(summ, "ram:LineTotalAmount", _amount(line.net_amount))

    agreement = _sub(tx, "ram:ApplicableHeaderTradeAgreement")
    if invoice.buyer_reference:
        _sub(agreement, "ram:BuyerReference", invoice.buyer_reference)
    _party(agreement, "ram:SellerTradeParty", invoice.seller, with_contact=True)
    _party(agreement, "ram:BuyerTradeParty", invoice.buyer, with_contact=False)
    if invoice.order_reference:
        _sub(_sub(agreement, "ram:BuyerOrderReferencedDocument"), "ram:IssuerAssignedID", invoice.order_reference)

    delivery = _sub(tx, "ram:ApplicableHeaderTradeDelivery")
    if any(line.vat_category == "K" for line in invoice.lines):
        # Deliver-to country (BT-80) is mandatory for intra-community supplies (BR-IC-12), XRechnung
        # then also wants post code and city: the model has no delivery address, goods go to the buyer.
        _address(_sub(delivery, "ram:ShipToTradeParty"), invoice.buyer.address)
    if invoice.delivery_date:
        _date(_sub(delivery, "ram:ActualDeliverySupplyChainEvent"), "ram:OccurrenceDateTime", invoice.delivery_date)

    settlement = _sub(tx, "ram:ApplicableHeaderTradeSettlement")
    _sub(settlement, "ram:PaymentReference", invoice.number)
    _sub(settlement, "ram:InvoiceCurrencyCode", cur)
    pay = invoice.payment
    if pay.iban or pay.means_code:
        means = _sub(settlement, "ram:SpecifiedTradeSettlementPaymentMeans")
        _sub(means, "ram:TypeCode", pay.means_code or "58")
        if pay.iban:
            account = _sub(means, "ram:PayeePartyCreditorFinancialAccount")
            _sub(account, "ram:IBANID", pay.iban.replace(" ", ""))
            if pay.account_holder:
                _sub(account, "ram:AccountName", pay.account_holder)
        if pay.bic:
            _sub(_sub(means, "ram:PayeeSpecifiedCreditorFinancialInstitution"), "ram:BICID", pay.bic)

    for b in totals.breakdown:
        tax = _sub(settlement, "ram:ApplicableTradeTax")
        _sub(tax, "ram:CalculatedAmount", _amount(b.amount))
        _sub(tax, "ram:TypeCode", "VAT")
        if b.category in EXEMPTION_REASONS:
            _sub(tax, "ram:ExemptionReason", EXEMPTION_REASONS[b.category])
        _sub(tax, "ram:BasisAmount", _amount(b.basis))
        _sub(tax, "ram:CategoryCode", b.category)
        _sub(tax, "ram:RateApplicablePercent", _number(b.rate))

    if pay.terms or pay.due_date:
        terms = _sub(settlement, "ram:SpecifiedTradePaymentTerms")
        if pay.terms:
            _sub(terms, "ram:Description", pay.terms)
        if pay.due_date:
            _date(terms, "ram:DueDateDateTime", pay.due_date)

    summ = _sub(settlement, "ram:SpecifiedTradeSettlementHeaderMonetarySummation")
    _sub(summ, "ram:LineTotalAmount", _amount(totals.line_total))
    _sub(summ, "ram:TaxBasisTotalAmount", _amount(totals.tax_basis_total))
    _sub(summ, "ram:TaxTotalAmount", _amount(totals.tax_total), currencyID=cur)
    _sub(summ, "ram:GrandTotalAmount", _amount(totals.grand_total))
    _sub(summ, "ram:DuePayableAmount", _amount(totals.due_payable))

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
