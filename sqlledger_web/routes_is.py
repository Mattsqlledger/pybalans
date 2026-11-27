"""Verkoop- en inkoopfacturen met artikelen (fase 3 stap 2, F1–F9): invoeren, wijzigen, creditnota,
betalingen en contante korting op de factuur, verwijderen.

Het scherm volgt is.pl/ir.pl: relatie, kopgegevens, artikelregels (artikel, omschrijving, aantal,
eenheid, prijs, korting, leverdatum, serienummer, project), btw per rekening (alleen berekend), totaal,
betalingen en afleveradres. Regelbedragen, btw en totalen rekent alleen de server (``sqlledger.invoice``);
het scherm vraagt ze op bij een wijziging (fetch met action=calc) en werkt zonder JavaScript met
"Bijwerken". Relatie, koersen, betalingen en referenties gaan zoals bij verkoop/inkoop (routes_arap).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from starlette.datastructures import FormData

from sqlledger import LedgerError
from sqlledger._db import currencies, cursor, default_currency, get_defaults
from sqlledger.amounts import _plain, format_amount, round_amount
from sqlledger.arap import ArapPayment
from sqlledger.invoice import SHIPTO_FIELDS, Invoice, InvoiceLedger, InvoiceLine

from . import doctypes
from .documents import DocumentRenderer
from .i18n import N_, gettext as _
from .libmsg import lib_message
from .reportdefs import Lib
from .routes_arap import (ITEMS_FETCH, OPTIONS_MAX, PayRow, _account_names, _amount, _date, _defcur, _iso, _lib,
                          _rate_text, _same_label, _vc_label, message as arap_message, require_module, resolve_vc)
from . import routes_attach as attach
from .routes_gl import RefRow, _accno, references, refs_from_post
from .web import Ctx, Redirect, ctx_company, render, require_role, upload_form

router = APIRouter()

KIND = {
    "customer": dict(prefix="verkoopfactuur", module="ar", vc_label=N_("Klant"), title=N_("Verkoopfactuur"),
                     note=N_("Creditnota verkoop (artikelen)"), note_type="credit_invoice",
                     title_add=N_("Verkoopfactuur toevoegen"), title_edit=N_("Verkoopfactuur wijzigen"),
                     note_add=N_("Creditnota verkoop (artikelen) toevoegen"),
                     note_edit=N_("Creditnota verkoop (artikelen) wijzigen"), arap_prefix="verkoop",
                     vc_not_found=N_("Geen klant gevonden voor “{text}”."),
                     vc_ambiguous=N_("Meer dan één klant gevonden voor “{text}”: kies er een uit de lijst.")),
    "vendor": dict(prefix="inkoopfactuur", module="ap", vc_label=N_("Leverancier"), title=N_("Inkoopfactuur"),
                   note=N_("Creditnota inkoop (artikelen)"), note_type="debit_invoice",
                   title_add=N_("Inkoopfactuur toevoegen"), title_edit=N_("Inkoopfactuur wijzigen"),
                   note_add=N_("Creditnota inkoop (artikelen) toevoegen"),
                   note_edit=N_("Creditnota inkoop (artikelen) wijzigen"), arap_prefix="inkoop",
                   vc_not_found=N_("Geen leverancier gevonden voor “{text}”."),
                   vc_ambiguous=N_("Meer dan één leverancier gevonden voor “{text}”: kies er een uit de lijst.")),
}

MESSAGES = {
    "Cannot post invoice for a closed period!": N_("De factuurdatum valt in een afgesloten periode."),
    "Not enough inventory in Row {n}": N_("Onvoldoende voorraad in regel {n} (het artikel heeft “voorraad controleren”)."),
}


def message(exc: Exception) -> str:
    text = str(exc)
    if text in MESSAGES:
        return _(MESSAGES[text])
    if text.startswith("Not enough inventory in Row "):
        return _(MESSAGES["Not enough inventory in Row {n}"]).format(n=text.rsplit(" ", 1)[1])
    return arap_message(exc)


# ------------------------------------------------------------------ formuliermodel

@dataclass
class ILine:
    item_text: str = ""             # "nummer omschrijving" zoals getypt/gekozen
    item_shown: str = ""            # tekst zoals de server het artikel het laatst toonde
    parts_id: Optional[int] = None
    id: str = ""                    # bestaande factuurregel (invoice.id)
    description: str = ""
    qty: str = ""
    unit: str = ""
    sellprice: str = ""
    discount: str = ""
    deliverydate: str = ""
    serialnumber: str = ""
    project_id: str = ""
    ordernumber: str = ""
    ponumber: str = ""
    itemnotes: str = ""
    cost: str = ""                  # verkoop: kostprijs per regel (bewaard, niet getoond; F8)
    vendor: str = ""
    vendor_id: str = ""
    lineitemdetail: str = ""        # vinkje "regeldetails" (Perl, voor documenten in fase 4): bewaard
    orderitems_id: str = ""         # omzetten uit een order: orderregel (voor de nabestelling, O4)
    # berekend
    linetotal: Decimal = Decimal(0)
    onhand: Optional[Decimal] = None
    stock: bool = False             # artikel met voorraad (of samengesteld)
    warning: str = ""


@dataclass
class TaxShow:
    accno: str
    description: str
    rate: Decimal
    amount: Decimal

    @property
    def pct(self) -> str:
        return format((self.rate * 100).normalize(), "f").replace(".", ",") + "%"


@dataclass
class IsForm:
    vc: str
    type: str = "invoice"
    id: Optional[int] = None
    vc_text: str = ""
    vc_shown: str = ""
    vc_id: Optional[int] = None
    invnumber: str = ""
    ordnumber: str = ""
    quonumber: str = ""
    ponumber: str = ""
    transdate: str = ""
    oldtransdate: str = ""
    duedate: str = ""
    oldduedate: str = ""
    terms: str = "0"
    oldterms: str = "0"
    currency: str = ""
    oldcurrency: str = ""
    exchangerate: str = ""
    department_id: str = ""
    employee_id: str = ""
    description: str = ""
    notes: str = ""
    intnotes: str = ""
    dcn: str = ""
    taxincluded: bool = False
    arap_accno: str = ""
    bank_accno: str = ""
    paymentmethod_id: str = ""
    language_code: str = ""
    onhold: bool = False
    cashdiscount: str = ""
    discountterms: str = ""
    shippingpoint: str = ""
    shipvia: str = ""
    waybill: str = ""
    order_id: str = ""
    taxextra: str = ""              # btw-rekeningen van de opgeslagen factuur (spatie-gescheiden)
    dup_ok: bool = False
    shipto: Dict[str, str] = field(default_factory=dict)
    lines: List[ILine] = field(default_factory=list)
    pays: List[PayRow] = field(default_factory=list)
    discount: PayRow = field(default_factory=PayRow)
    refs: List[RefRow] = field(default_factory=list)
    # berekend
    taxes: List[TaxShow] = field(default_factory=list)
    subtotal: Decimal = Decimal(0)
    invtotal: Decimal = Decimal(0)
    totalpaid: Decimal = Decimal(0)
    outstanding: Decimal = Decimal(0)
    cd_available: Decimal = Decimal(0)

    @property
    def note(self) -> bool:
        return self.type in ("credit_invoice", "debit_invoice")


def _line_filled(ln: ILine) -> bool:
    return bool(ln.item_text.strip() or ln.parts_id or ln.qty.strip() or ln.description.strip())


def _pad(f: IsForm) -> IsForm:
    if not f.lines or _line_filled(f.lines[-1]):
        f.lines.append(ILine())
    if not f.pays or f.pays[-1].amount.strip():
        f.pays.append(PayRow(accno=f.bank_accno))
    if not f.refs or f.refs[-1].description or f.refs[-1].is_file:
        f.refs.append(RefRow())
    return f


def form_from_post(vc: str, form: FormData) -> IsForm:
    g = lambda k: str(form.get(k, "")).strip()  # noqa: E731
    vid = g("vc_id")
    ttype = g("type")
    if ttype not in ("invoice", KIND[vc]["note_type"]):
        ttype = "invoice"
    f = IsForm(vc=vc, type=ttype, id=int(g("id")) if g("id").isdigit() else None,
               vc_text=g("vc_text"), vc_shown=g("vc_shown"), vc_id=int(vid) if vid.isdigit() else None,
               invnumber=g("invnumber")[:100], ordnumber=g("ordnumber")[:100], quonumber=g("quonumber")[:100],
               ponumber=g("ponumber")[:100], transdate=g("transdate"), oldtransdate=g("oldtransdate"),
               duedate=g("duedate"), oldduedate=g("oldduedate"), terms=g("terms") or "0",
               oldterms=g("oldterms") or "0", currency=g("currency"), oldcurrency=g("oldcurrency"),
               exchangerate=g("exchangerate"), department_id=g("department_id"), employee_id=g("employee_id"),
               description=str(form.get("description", "")), notes=str(form.get("notes", "")),
               intnotes=str(form.get("intnotes", "")), dcn=g("dcn"), taxincluded=g("taxincluded") == "1",
               arap_accno=g("arap_accno"), bank_accno=g("bank_accno"), paymentmethod_id=g("paymentmethod_id"),
               language_code=g("language_code"), onhold=g("onhold") == "1", cashdiscount=g("cashdiscount"),
               discountterms=g("discountterms"), shippingpoint=g("shippingpoint")[:100], shipvia=g("shipvia")[:100],
               waybill=g("waybill")[:100], order_id=g("order_id"), taxextra=g("taxextra"), dup_ok=g("dup_ok") == "1")
    f.shipto = {k: g(f"shipto{k}")[:64] for k in SHIPTO_FIELDS}
    if g("shiptorecurring") == "1":
        f.shipto["recurring"] = "1"
    n = int(g("rowcount")) if g("rowcount").isdigit() else 0
    for i in range(1, min(n, 500) + 1):
        pid = g(f"parts_id_{i}")
        ln = ILine(item_text=g(f"item_{i}"), item_shown=g(f"item_shown_{i}"),
                   parts_id=int(pid) if pid.isdigit() else None, id=g(f"line_id_{i}"),
                   description=g(f"description_{i}"), qty=g(f"qty_{i}"), unit=g(f"unit_{i}")[:5],
                   sellprice=g(f"sellprice_{i}"), discount=g(f"discount_{i}"), deliverydate=g(f"deliverydate_{i}"),
                   serialnumber=g(f"serialnumber_{i}"), project_id=g(f"project_{i}"),
                   ordernumber=g(f"ordernumber_{i}"), ponumber=g(f"ponumber_{i}"), itemnotes=g(f"itemnotes_{i}"),
                   cost=g(f"cost_{i}"), vendor=g(f"vendor_{i}"), vendor_id=g(f"vendor_id_{i}"),
                   lineitemdetail=g(f"lineitemdetail_{i}"), orderitems_id=g(f"orderitems_id_{i}"))
        if _line_filled(ln):
            f.lines.append(ln)
    m = int(g("paidcount")) if g("paidcount").isdigit() else 0
    for i in range(1, min(m, 200) + 1):
        f.pays.append(PayRow(datepaid=g(f"datepaid_{i}"), olddatepaid=g(f"olddatepaid_{i}"), source=g(f"source_{i}"),
                             memo=g(f"memo_{i}"), amount=g(f"paid_{i}"), exchangerate=g(f"exchangerate_{i}"),
                             accno=g(f"paidaccno_{i}"), cleared=g(f"paidcleared_{i}"),
                             paymentmethod_id=g(f"paymentmethod_{i}")))
    if f.pays and not f.pays[-1].amount.strip() and f.pays[-1].accno:
        f.bank_accno = f.pays[-1].accno      # rekening van de lege betaalregel wordt de standaard
    f.pays = [p for p in f.pays if p.amount.strip()]
    f.discount = PayRow(datepaid=g("discount_datepaid"), olddatepaid=g("discount_olddatepaid"),
                        source=g("discount_source"), memo=g("discount_memo"), amount=g("discount_paid"),
                        exchangerate=g("discount_exchangerate"), accno=g("discount_accno"),
                        cleared=g("discount_cleared"))
    f.refs = refs_from_post(form)
    return f


def _num_text(v, nf: str, places: int = 0) -> str:
    """Getal voor het scherm met minstens ``places`` decimalen en verder zoveel als nodig."""
    if v in (None, ""):
        return ""
    v = Decimal(v)
    s = _plain(v)
    dec = len(s.split(".", 1)[1]) if "." in s else 0
    return format_amount(v, nf, max(dec, places), "0").replace("&nbsp;", "")


def _item_label(p: Dict[str, Any]) -> str:
    return f"{p.get('partnumber') or ''} {p.get('description') or ''}".strip()


def form_from_invoice(lib: Lib, inv: Invoice, nf: str, precision: int, copy: bool = False) -> IsForm:
    def amt(v):
        return format_amount(v, nf, precision) if v else ""
    info = lib.arap.vc_info(inv.vc, inv.vc_id)
    foreign = bool(inv.currency) and inv.currency != _defcur(lib)
    rt = lambda v: _rate_text(v, nf) if foreign else ""  # noqa: E731
    f = IsForm(vc=inv.vc, type=inv.type, id=None if copy else inv.id, vc_id=inv.vc_id,
               invnumber="" if copy else (inv.invnumber or ""), ordnumber=inv.ordnumber or "",
               quonumber=inv.quonumber or "", ponumber=inv.ponumber or "", transdate=_iso(inv.transdate),
               duedate=_iso(inv.duedate), terms=str(inv.terms or 0), currency=inv.currency or "",
               exchangerate=rt(inv.exchangerate), department_id=str(inv.department_id or ""),
               employee_id=str(inv.employee_id or ""), description=inv.description or "", notes=inv.notes or "",
               intnotes=inv.intnotes or "", dcn=inv.dcn or "", taxincluded=inv.taxincluded,
               arap_accno=inv.arap_accno or "", bank_accno=inv.bank_accno or "",
               paymentmethod_id=str(inv.paymentmethod_id or ""), language_code=inv.language_code or "",
               onhold=inv.onhold, cashdiscount=format_amount(inv.cashdiscount, nf) if inv.cashdiscount else "",
               discountterms=str(inv.discountterms or ""), shippingpoint=inv.shippingpoint or "",
               shipvia=inv.shipvia or "", waybill=inv.waybill or "",
               taxextra=" ".join(sorted(inv.taxaccounts_extra)), shipto=dict(inv.shipto or {}))
    f.vc_text = f.vc_shown = _vc_label(info)
    f.oldtransdate, f.oldduedate, f.oldterms, f.oldcurrency = f.transdate, f.duedate, f.terms, f.currency
    with cursor(lib.conn) as cur:
        parts = InvoiceLedger(lib.conn, lib.cfg).parts_info(cur, [l.parts_id for l in inv.lines])
    for l in inv.lines:
        p = parts.get(l.parts_id) or {}
        label = _item_label(p)
        f.lines.append(ILine(
            item_text=label, item_shown=label, parts_id=l.parts_id, id="" if copy else str(l.id or ""),
            description=l.description or "", qty=_num_text(l.qty, nf), unit=l.unit or "",
            sellprice=_num_text(l.sellprice, nf, precision), discount=_num_text(l.discount, nf) if l.discount else "",
            deliverydate=str(l.deliverydate or ""), serialnumber=l.serialnumber or "",
            project_id=str(l.project_id or ""), ordernumber=l.ordernumber or "", ponumber=l.ponumber or "",
            itemnotes=l.itemnotes or "", cost="" if l.cost is None else _plain(l.cost), vendor=l.vendor or "",
            vendor_id="" if l.vendor_id is None else str(l.vendor_id), lineitemdetail="1" if l.lineitemdetail else "",
            orderitems_id=str(l.orderitems_id or "")))
    if not copy:
        for p in inv.payments:
            f.pays.append(PayRow(datepaid=_iso(p.datepaid), olddatepaid=_iso(p.datepaid), source=p.source or "",
                                 memo=p.memo or "", amount=amt(p.amount), exchangerate=rt(p.exchangerate),
                                 accno=p.accno, cleared=_iso(p.cleared),
                                 paymentmethod_id=str(p.paymentmethod_id or "")))
        if inv.discount:
            d = inv.discount
            f.discount = PayRow(datepaid=_iso(d.datepaid), olddatepaid=_iso(d.datepaid), source=d.source or "",
                                memo=d.memo or "", amount=amt(d.amount), exchangerate=rt(d.exchangerate),
                                accno=d.accno, cleared=_iso(d.cleared))
    for r in inv.references:
        f.refs.append(RefRow(description=r.description or "", code=r.code or "",
                             archive_id=str(r.archive_id or ""), folder=r.folder or "",
                             confidential=bool(r.confidential), filename=r.filename or ""))
    return f


# ------------------------------------------------------------------ rekenen

def _exact_label_items(il: InvoiceLedger, vc: str, text: str) -> List[Dict[str, Any]]:
    """Artikel met precies het label uit de zoeklijst ("nummer omschrijving"), ook als het nummer spaties bevat
    (bv. een artikelomschrijving met dubbele spaties); spaties tellen niet dubbel (PB17)."""
    words = text.split()
    for i in range(1, len(words)):
        hits = {p["id"]: p for p in il.find_items(vc, partnumber=" ".join(words[:i]), limit=ITEMS_FETCH)
                if _same_label(_item_label(p), text)}
        if hits:
            return list(hits.values())
    return []


def _no_discount(text: str, nf: str) -> bool:
    """Regelkorting leeg of 0 (dan mag de korting van de relatie erin, zoals SL ``||=``)."""
    try:
        return not text.strip() or not _amount(text, nf, "percentage")
    except ValueError:
        return False


def resolve_item(lib: Lib, f: IsForm, ln: ILine) -> Optional[str]:
    """Artikel uit het zoekveld van een regel bepalen (is.pl update + retrieve_item); bij een nieuw
    gekozen artikel omschrijving, eenheid, prijs en kostprijs voorinvullen."""
    text = ln.item_text.strip()
    if ln.parts_id and text == ln.item_shown:
        return None
    if not text:
        ln.parts_id = None
        return None
    il = InvoiceLedger(lib.conn, lib.cfg)
    first = text.split(" ", 1)[0]
    hits = [p for p in il.find_items(f.vc, partnumber=first) if (p["partnumber"] or "").lower() == first.lower()]
    if len(hits) > 1:
        hits = [p for p in hits if _item_label(p).lower() == text.lower()] or hits
    if len(hits) != 1:
        hits = _exact_label_items(il, f.vc, text) or hits
    if len(hits) != 1:
        hits = il.find_items(f.vc, text)
        exact = [p for p in hits if _item_label(p).lower() == text.lower() or (p["partnumber"] or "").lower() == text.lower()]
        if len(exact) == 1:
            hits = exact
    if not hits:
        return _("Geen artikel gevonden voor “{text}”.").format(text=text)
    if len(hits) > 1:
        return _("Meer dan één artikel gevonden voor “{text}”: kies er een uit de lijst.").format(text=text)
    p = hits[0]
    nf = lib.cfg.numberformat
    rate = None
    if f.currency and f.currency != _defcur(lib):
        try:
            rate = _amount(f.exchangerate, nf, "koers") or None
        except ValueError:
            rate = None
    d = il.item_defaults(f.vc, p["id"], rate)
    ln.parts_id = p["id"]
    ln.item_text = ln.item_shown = _item_label(p)
    ln.description = p["description"] or ""
    ln.unit = (p["unit"] or "")[:5]
    if not ln.qty.strip():
        ln.qty = "1"
    ln.sellprice = _num_text(d["price"], nf, lib.precision())
    if f.vc_id and _no_discount(ln.discount, nf):
        # VW7: korting % van de klant/leverancier op de nieuw gekozen regel (is.pl/ir.pl/oe.pl: discount ||=)
        vd = lib.arap.vc_defaults(f.vc, f.vc_id)["discount"]
        if vd:
            ln.discount = _num_text(Decimal(str(vd)) * 100, nf)
    if f.vc == "customer":
        ln.cost = _plain(d["cost"])
        ln.vendor_id = ln.vendor_id or "0"
    return None


def apply_vc_defaults(lib: Lib, f: IsForm, nf: str) -> None:
    """Nieuwe relatie gekozen: standaardwaarden laden (AA::get_name)."""
    d = lib.arap.vc_defaults(f.vc, f.vc_id, _date(f.transdate))
    f.terms = f.oldterms = str(d["terms"] or 0)
    td = _date(f.transdate)
    if td:
        f.duedate = f.oldduedate = (td + timedelta(days=int(d["terms"] or 0))).isoformat()
    f.taxincluded = bool(d["taxincluded"])
    if d["arap_accno"]:
        f.arap_accno = d["arap_accno"]
    f.bank_accno = d["payment_accno"] or f.bank_accno
    for p in f.pays:
        if not p.accno:
            p.accno = f.bank_accno
    f.cashdiscount = "" if f.note or not d["cashdiscount"] else format_amount(Decimal(str(d["cashdiscount"])) * 100, nf)
    f.discountterms = str(d["discountterms"] or "")
    f.paymentmethod_id = str(d["paymentmethod_id"] or "")
    f.language_code = d["language_code"] or ""
    if not f.id:
        f.currency = d["currency"] or ""
        f.employee_id = str(d["employee_id"] or "")


def to_invoice(lib: Lib, f: IsForm, nf: str, strict: bool = True) -> Invoice:
    """Formulier -> Invoice. strict=False: voor doorrekenen (ontbrekende relatie/datum mag)."""
    td = _date(f.transdate)
    if strict and not td:
        raise ValueError(_("Factuurdatum ontbreekt."))
    if strict and not f.vc_id:
        raise ValueError(_("Kies een klant.") if f.vc == "customer" else _("Kies een leverancier."))

    def rate(text):
        return _amount(text, nf, "koers") if text else None
    lines = []
    for i, ln in enumerate(f.lines, 1):
        if not ln.parts_id:
            if strict and (ln.item_text.strip() or ln.qty.strip()):
                raise ValueError(_("Regel {line}: kies een artikel.").format(line=i))
            continue
        if ln.deliverydate and not _date(ln.deliverydate):
            raise ValueError(_("Regel {line}: ongeldige leverdatum {value}").format(line=i, value=ln.deliverydate))
        lines.append(InvoiceLine(
            parts_id=ln.parts_id, qty=_amount(ln.qty, nf, "aantal"), sellprice=_amount(ln.sellprice, nf),
            discount=_amount(ln.discount, nf, "percentage"), description=ln.description, unit=ln.unit,
            deliverydate=_date(ln.deliverydate), project_id=int(ln.project_id) if ln.project_id.isdigit() else None,
            serialnumber=ln.serialnumber, ordernumber=ln.ordernumber, ponumber=ln.ponumber, itemnotes=ln.itemnotes,
            lineitemdetail=ln.lineitemdetail == "1",
            cost=Decimal(ln.cost) if ln.cost else (Decimal(0) if f.vc == "customer" and not ln.id else None),
            vendor=ln.vendor or None,
            vendor_id=int(ln.vendor_id) if ln.vendor_id.lstrip("-").isdigit() else None,
            id=int(ln.id) if ln.id.isdigit() else None,
            orderitems_id=int(ln.orderitems_id) if ln.orderitems_id.isdigit() else None))
    pays = []
    for i, p in enumerate(f.pays, 1):
        a = _amount(p.amount, nf)
        if not a:
            continue
        if not p.accno:
            raise ValueError(_("Betaling {line}: kies een rekening.").format(line=i))
        if p.datepaid and not _date(p.datepaid):
            raise ValueError(_("Ongeldige datum: {value}").format(value=p.datepaid))
        pays.append(ArapPayment(accno=_accno(p.accno), amount=a, datepaid=_date(p.datepaid),
                                source=p.source or None, memo=p.memo or None, exchangerate=rate(p.exchangerate),
                                cleared=_date(p.cleared),
                                paymentmethod_id=int(p.paymentmethod_id) if p.paymentmethod_id.isdigit() else None))
    discount = None
    d = f.discount
    if _amount(d.amount, nf):
        if strict and not d.accno:
            raise ValueError(_("Kies de rekening voor de korting."))
        discount = ArapPayment(accno=_accno(d.accno), amount=_amount(d.amount, nf), datepaid=_date(d.datepaid),
                               source=d.source or None, memo=d.memo or None, exchangerate=rate(d.exchangerate),
                               cleared=_date(d.cleared))
    defcur = _defcur(lib)
    currency = f.currency or defcur
    exchangerate = Decimal(1)
    if currency != defcur:
        exchangerate = rate(f.exchangerate) or Decimal(0)
    if strict and not f.arap_accno:
        raise ValueError(_("Kies de debiteurenrekening.") if f.vc == "customer" else _("Kies de crediteurenrekening."))
    refs = references(f.refs)
    try:
        terms = int(f.terms or 0)
    except ValueError:
        raise ValueError(_("Ongeldige termijn: {value}").format(value=f.terms))
    pct = _amount(f.cashdiscount, nf, "percentage") if f.cashdiscount else Decimal(0)
    return Invoice(
        vc=f.vc, vc_id=f.vc_id or 0, transdate=td or date.today(), arap_accno=_accno(f.arap_accno), lines=lines,
        type=f.type, id=f.id, invnumber=f.invnumber or None, ordnumber=f.ordnumber or None,
        quonumber=f.quonumber or None, ponumber=f.ponumber or None, duedate=_date(f.duedate), terms=terms,
        description=f.description or None, notes=f.notes or None, intnotes=f.intnotes or None, currency=currency,
        exchangerate=exchangerate, taxincluded=f.taxincluded,
        department_id=int(f.department_id) if f.department_id.isdigit() else None,
        employee_id=int(f.employee_id) if f.employee_id.isdigit() else None,
        language_code=f.language_code or None, shippingpoint=f.shippingpoint or None, shipvia=f.shipvia or None,
        waybill=f.waybill or None, payments=pays, discount=discount, cashdiscount=pct,
        discountterms=int(f.discountterms) if f.discountterms.isdigit() else 0, onhold=f.onhold,
        bank_accno=_accno(f.bank_accno) or None,
        paymentmethod_id=int(f.paymentmethod_id) if f.paymentmethod_id.isdigit() else None,
        dcn=f.dcn or None, references=refs, shipto=dict(f.shipto),
        order_id=int(f.order_id) if f.order_id.isdigit() else None,
        taxaccounts_extra=set(f.taxextra.split()))


def recalc(lib: Lib, f: IsForm, nf: str, precision: int, defcur: str) -> List[str]:
    """Wat is.pl/ir.pl update doet: relatie, artikelen, termijn/vervaldatum, koersen, regelbedragen,
    btw en totalen. Geeft fouten in de invoer terug (het formulier wordt toch getoond)."""
    errors: List[str] = []
    vc_before = f.vc_id
    err = resolve_vc(lib, f)       # gebruikt KIND van routes_arap voor de meldingen (zelfde teksten)
    if err:
        errors.append(err)
    td = _date(f.transdate)
    if f.transdate and not td:
        errors.append(_("Ongeldige datum: {value}").format(value=f.transdate))
    new_vc = bool(f.vc_id and (f.vc_id != vc_before or not f.vc_shown))
    if new_vc:
        apply_vc_defaults(lib, f, nf)
        f.vc_text = _vc_label(lib.arap.vc_info(f.vc, f.vc_id))
    f.vc_shown = f.vc_text if f.vc_id else ""

    if not new_vc and td:
        if f.terms != f.oldterms:
            try:
                f.duedate = (td + timedelta(days=int(f.terms or 0))).isoformat()
            except ValueError:
                errors.append(_("Ongeldige termijn: {value}").format(value=f.terms))
        elif f.duedate != f.oldduedate and _date(f.duedate):
            f.terms = str((_date(f.duedate) - td).days)
        elif f.transdate != f.oldtransdate:
            try:
                f.duedate = (td + timedelta(days=int(f.terms or 0))).isoformat()
            except ValueError:
                pass
    f.oldterms, f.oldduedate = f.terms, f.duedate

    foreign = bool(f.currency) and f.currency != defcur
    if not foreign:
        f.exchangerate = ""
        for p in f.pays + [f.discount]:
            p.exchangerate = ""
    else:
        changed = f.currency != f.oldcurrency
        if (changed or f.transdate != f.oldtransdate or not f.exchangerate) and td:
            rate = lib.gl.exchange_rate(f.currency, td)
            if rate or changed or f.transdate != f.oldtransdate:
                f.exchangerate = _rate_text(rate, nf) if rate else ""
        for p in f.pays + ([f.discount] if f.discount.amount else []):
            pd = _date(p.datepaid)
            if pd and (changed or p.datepaid != p.olddatepaid or not p.exchangerate):
                if pd == td and f.exchangerate:
                    p.exchangerate = f.exchangerate
                else:
                    rate = lib.gl.exchange_rate(f.currency, pd)
                    if rate:
                        p.exchangerate = _rate_text(rate, nf)
            p.olddatepaid = p.datepaid
    f.oldtransdate, f.oldcurrency = f.transdate, f.currency

    acc = lib.arap.accounts(f.vc)
    ARAP = KIND[f.vc]["module"].upper()
    if not f.arap_accno and acc[ARAP]:
        f.arap_accno = acc[ARAP][0]["accno"]
    if not f.bank_accno and acc[f"{ARAP}_paid"]:
        f.bank_accno = acc[f"{ARAP}_paid"][0]["accno"]

    for ln in f.lines:
        e = resolve_item(lib, f, ln)
        if e:
            errors.append(e)
        ln.item_shown = ln.item_text if ln.parts_id else ""

    def num(text, what="bedrag"):
        try:
            return _amount(text, nf, what)
        except ValueError as exc:
            errors.append(lib_message(exc))
            return Decimal(0)
    for ln in f.lines:
        num(ln.qty, "aantal"), num(ln.sellprice), num(ln.discount, "percentage")
    il = InvoiceLedger(lib.conn, lib.cfg)
    f.taxes = []
    f.subtotal = f.invtotal = Decimal(0)
    try:
        inv = to_invoice(lib, f, nf, strict=False)
    except ValueError as exc:
        errors.append(lib_message(exc))
        inv = None
    if inv is not None and f.vc_id:
        try:
            calc = il.calculate(inv, il.tax_rates(inv))
        except LedgerError as exc:
            errors.append(message(exc))
            calc = None
        if calc:
            names = _account_names(lib, f.vc)
            rates = il.tax_rates(inv)
            filled = [ln for ln in f.lines if ln.parts_id]
            bycalc = {c.index: c for c in calc.lines}
            sw = -1 if inv.credit else 1
            for i, ln in enumerate(filled):
                c = bycalc.get(i)
                ln.linetotal = c.fxlinetotal * sw if c else Decimal(0)
                if c:
                    p = c.part
                    ln.stock = bool(p["inventory_accno_id"] or p["assembly"])
                    ln.onhand = p["onhand"] if ln.stock else None
            f.taxes = [TaxShow(accno=a, description=names.get(a, ""), rate=rates.get(a, Decimal(0)),
                               amount=v * sw) for a, v in calc.fxtaxes.items() if v]
            f.subtotal = calc.fxsubtotal * sw
            f.invtotal = calc.fxtotal * sw
    # voorraadwaarschuwing (F4): verkoop boven de voorraad mag, maar wordt gemeld
    if f.vc == "customer" and f.type == "invoice":
        need: Dict[int, Decimal] = {}
        for ln in f.lines:
            if ln.parts_id and ln.stock:
                q = num(ln.qty, "aantal")
                need[ln.parts_id] = need.get(ln.parts_id, Decimal(0)) + q
        back = _own_qty(lib, f) if f.id else {}
        for ln in f.lines:
            if ln.parts_id and ln.stock and ln.onhand is not None:
                avail = ln.onhand + back.get(ln.parts_id, Decimal(0))
                if need.get(ln.parts_id, Decimal(0)) > avail:
                    ln.warning = _("voorraad {onhand}").format(onhand=_num_text(avail, nf))
    pct = num(f.cashdiscount, "percentage") if f.cashdiscount else Decimal(0)
    f.cd_available = round_amount(f.invtotal * pct / 100, precision)
    disc = num(f.discount.amount)
    f.totalpaid = sum((num(p.amount) for p in f.pays), Decimal(0)) + disc
    f.outstanding = round_amount(f.invtotal - f.totalpaid, precision)
    return list(dict.fromkeys(errors))


def _own_qty(lib: Lib, f: IsForm) -> Dict[int, Decimal]:
    """Wat deze (opgeslagen) factuur al van de voorraad heeft afgehaald, per artikel."""
    with cursor(lib.conn) as cur:
        cur.execute("""SELECT parts_id, SUM(qty::numeric) AS qty FROM invoice
                       WHERE trans_id = %s AND NOT COALESCE(assemblyitem, false) GROUP BY parts_id""", (f.id,))
        return {r["parts_id"]: Decimal(r["qty"]) for r in cur.fetchall()}


def for_new(lib: Lib, f: IsForm) -> None:
    """Opslaan als nieuw: geen id's, geen afletterdatums; met lock_sinumber een nieuw nummer."""
    f.id = None
    f.order_id = ""
    if f.vc == "customer":
        with cursor(lib.conn) as cur:
            if get_defaults(cur, "lock_sinumber").get("lock_sinumber") not in (None, "", "0"):
                f.invnumber = ""
    for ln in f.lines:
        ln.id = ""
        ln.orderitems_id = ""
    for p in f.pays + [f.discount]:
        p.cleared = ""
    f.refs = [r for r in f.refs if not r.archive_id]      # bijlagen blijven bij het origineel (RB2)


def do_post(lib: Lib, f: IsForm, nf: str) -> int:
    """Boek het (doorgerekende) formulier. Bij wijzigen blijven herhaalschema en afdruk-/mailstatus van
    de bestaande factuur staan."""
    il = InvoiceLedger(lib.conn, lib.cfg)
    old = il.get_invoice(f.id, f.vc) if f.id else None
    inv = to_invoice(lib, f, nf)
    defcur = _defcur(lib)
    for p in inv.payments + ([inv.discount] if inv.discount else []):
        if inv.currency != defcur and not p.exchangerate and p.datepaid and p.datepaid != inv.transdate:
            p.exchangerate = lib.gl.exchange_rate(inv.currency, p.datepaid)
    if old:
        inv.recurring = old.recurring
        inv.printed, inv.emailed, inv.queued = old.printed, old.emailed, old.queued
    return il.post_invoice(inv)


# ------------------------------------------------------------------ pagina

def _choices(lib: Lib, f: IsForm) -> Dict[str, Any]:
    with cursor(lib.conn) as cur:
        curr = [c for c in currencies(cur) if c]
        defaults = get_defaults(cur, "lock_sinumber", "precision")
    td = _date(f.transdate)
    ARAP = KIND[f.vc]["module"].upper()
    acc = lib.arap.accounts(f.vc)
    names = _account_names(lib, f.vc)

    def with_current(key, current):
        rows = list(acc[key])
        known = {r["accno"] for r in rows}
        for a in current:
            if a and a not in known:
                rows.append({"accno": a, "description": names.get(a, "")})
                known.add(a)
        return rows
    projects = lib.gl.projects(td)
    used = {ln.project_id for ln in f.lines if ln.project_id}
    if used - {str(p["id"]) for p in projects}:
        projects += [p for p in lib.gl.projects() if str(p["id"]) in used and p not in projects]
    sales = lib.arap.employees(td)
    return dict(
        arap_accounts=with_current(ARAP, [_accno(f.arap_accno)]),
        paid_accounts=with_current(f"{ARAP}_paid", [_accno(p.accno) for p in f.pays] + [_accno(f.bank_accno)]),
        discount_accounts=with_current(f"{ARAP}_discount", [_accno(f.discount.accno)]),
        departments=lib.gl.departments(f.vc), projects=projects,
        employees=lib.arap.employees(td, int(f.employee_id) if f.employee_id.isdigit() else None) if sales else [],
        paymentmethods=lib.arap.payment_methods(), currencies=curr, defaultcurrency=curr[0] if curr else "",
        lock_sinumber=f.vc == "customer" and defaults.get("lock_sinumber") not in (None, "", "0"),
        precision=int(defaults.get("precision") or 2))


def _title(f: IsForm) -> str:
    k = KIND[f.vc]
    if f.note:
        return k["note_edit"] if f.id else k["note_add"]
    return k["title_edit"] if f.id else k["title_add"]


def _page(ctx: Ctx, lib: Lib, f: IsForm, *, status: int = 200, error: Optional[str] = None,
          confirm: Optional[str] = None, inv: Optional[Invoice] = None, readonly_reason: Optional[str] = None,
          duplicates=None, **kw):
    ch = _choices(lib, f)
    info = None
    if f.vc_id:
        info = lib.arap.vc_info(f.vc, f.vc_id, exclude_id=f.id)
        rate = Decimal(1)
        if f.currency and f.currency != ch["defaultcurrency"]:
            try:
                rate = _amount(f.exchangerate, ctx.user["numberformat"]) or Decimal(1)
            except ValueError:
                pass
        info["creditremaining"] -= (f.invtotal - f.totalpaid) * rate * (-1 if f.note else 1)
    if not readonly_reason:
        _pad(f)
    recurring = inv.recurring if inv else None
    # herhaalschema (R1): opgeslagen factuur, geen batch of kassabon
    repeatable = bool(f.id) and not (inv and (inv.batch or inv.till))
    nf = ctx.user["numberformat"]
    order_info = None
    if f.order_id.isdigit() and not f.id:
        with cursor(lib.conn) as cur:
            cur.execute("SELECT ordnumber, quonumber, quotation, customer_id FROM oe WHERE id = %s", (int(f.order_id),))
            r = cur.fetchone()
        if r:
            kind = (N_("offerte") if r["customer_id"] else N_("offerteaanvraag")) if r["quotation"] else \
                (N_("verkooporder") if r["customer_id"] else N_("inkooporder"))
            order_info = {"kind": kind, "number": r["quonumber"] if r["quotation"] else r["ordnumber"],
                          "quotation": bool(r["quotation"])}
    return render(ctx, "is/form.html", status, f=f, ch=ch, k=KIND[f.vc], info=info, error=error, confirm=confirm,
                  order_info=order_info, repeatable=repeatable,
                  attachments=attach.saved_attachments(ctx, lib.conn, f.id),
                  attach_formname=f"{'ar' if f.vc == 'customer' else 'ap'}_{f.type}",
                  docs=doctypes.screen_docs(lib.conn, _doc_formname(f), f.id, ctx.request.query_params.get("pdf")),
                  variants=doctypes.variant_choices(lib.conn, doc_renderer(ctx), f.language_code),
                  title=_title(f), readonly_reason=readonly_reason, recurring=recurring, duplicates=duplicates,
                  nf=nf, can_edit=ctx.has_role("bookkeeper"), shipto_fields=SHIPTO_FIELDS,
                  amount=lambda v, dash="": format_amount(v, nf, ch["precision"], dash).replace("&nbsp;", "").strip(),
                  qtytext=lambda v: _num_text(v, nf), isodate=_date, **kw)


def _doc_formname(f: IsForm) -> str:
    """Documentsoort voor de knop PDF (io.pl formname)."""
    if f.vc == "customer":
        return "credit_invoice" if f.note else "invoice"
    return "debit_invoice" if f.note else "vendor_invoice"


def _module_of(lib: Lib, trans_id: int) -> Optional[Dict[str, Any]]:
    with cursor(lib.conn) as cur:
        cur.execute("""SELECT 'customer' AS vc, invoice, till FROM ar WHERE id = %(id)s
                       UNION ALL SELECT 'vendor', invoice, NULL FROM ap WHERE id = %(id)s""", {"id": trans_id})
        return cur.fetchone()


def blank_form(lib: Lib, vc: str, ttype: str, transdate: str) -> IsForm:
    """Nieuwe factuur: begint met de relatie en afdeling van de laatste boeking (Form::lastname_used)."""
    f = IsForm(vc=vc, type=ttype, transdate=transdate, oldtransdate=transdate)
    last = lib.arap.last_used(vc)
    if last:
        f.vc_id = last["vc_id"]
        f.department_id = str(last["department_id"] or "")
    return f


def new_form(ctx: Ctx, vc: str):
    q = ctx.request.query_params
    require_module(ctx, vc)
    require_role(ctx, "bookkeeper")
    conn, lib = _lib(ctx)
    nf = ctx.user["numberformat"]
    with conn:
        copy = q.get("kopie", "")
        order = q.get("order", "")
        if order.isdigit():
            # factuur uit een order/offerte (O2, ON2, ON4): de order sluit pas bij het boeken
            from sqlledger.order import OrderLedger
            with cursor(conn) as cur:
                cur.execute("SELECT customer_id FROM oe WHERE id = %s", (int(order),))
                r = cur.fetchone()
            if not r or ("customer" if r["customer_id"] else "vendor") != vc:
                raise HTTPException(404, _("Order of offerte niet gevonden."))
            try:
                inv = OrderLedger(conn, lib.cfg).to_invoice(int(order))
            except LedgerError as exc:
                raise HTTPException(400, lib_message(exc))
            f = form_from_invoice(lib, inv, nf, lib.precision(), copy=True)
            f.order_id = order
            f.oldterms = "x"
            f.oldtransdate = f.transdate
        elif copy.isdigit():
            m = _module_of(lib, int(copy))
            if not m or m["vc"] != vc or not m["invoice"]:
                raise HTTPException(404, _("Factuur niet gevonden."))
            inv = InvoiceLedger(conn, lib.cfg).get_invoice(int(copy), vc)
            f = form_from_invoice(lib, inv, nf, lib.precision(), copy=True)
            f.refs = [r for r in f.refs if not r.archive_id]      # bijlagen blijven bij het origineel (RB2)
            if _choices(lib, f)["lock_sinumber"]:
                f.invnumber = ""
            f.transdate = f.oldtransdate = date.today().isoformat()
            f.oldterms = "x"
        else:
            ttype = KIND[vc]["note_type"] if q.get("creditnota") == "1" else "invoice"
            f = blank_form(lib, vc, ttype, date.today().isoformat())
            if q.get("relatie", "").isdigit():
                f.vc_id = int(q["relatie"])
        errors = recalc(lib, f, nf, lib.precision(), _defcur(lib))
        return _page(ctx, lib, f, error=" ".join(errors) or None)


def edit_form(ctx: Ctx, vc: str, trans_id: int):
    require_module(ctx, vc)
    conn, lib = _lib(ctx)
    with conn:
        m = _module_of(lib, trans_id)
        if not m or m["vc"] != vc:
            raise HTTPException(404, _("Factuur niet gevonden."))
        if not m["invoice"]:
            raise Redirect(f"/{KIND[vc]['arap_prefix']}/{trans_id}")
        editor = ctx.has_role("bookkeeper")
        il = InvoiceLedger(conn, lib.cfg)
        inv = il.get_invoice(trans_id, vc, lock=editor)
        nf = ctx.user["numberformat"]
        f = form_from_invoice(lib, inv, nf, lib.precision())
        recalc(lib, f, nf, lib.precision(), _defcur(lib))
        reason = None
        if not editor:
            reason = _("Uw rol in deze administratie is alleen-lezen.")
        elif inv.batch:
            reason = _("Deze factuur hoort bij een batch; batches vallen buiten deze versie.")
        elif m.get("till"):
            reason = _("Dit is een kassabon; de kassa (POS) volgt later.")
        elif inv.locked:
            with cursor(conn) as cur:
                revtrans = get_defaults(cur, "revtrans").get("revtrans") not in (None, "", "0")
            reason = (_("Boekingen kunnen niet gewijzigd worden (instelling: alleen tegenboeken).") if revtrans
                      else _("Deze factuur valt in een afgesloten periode."))
        elif inv.readonly:
            reason = _("Deze factuur is in gebruik door {user}.").format(user=inv.haslock)
        warning = None
        if not reason:
            # btw-instellingen van artikel of relatie na het boeken gewijzigd: melden vóór het opslaan
            with cursor(conn) as cur:
                cur.execute(f"SELECT amount::numeric AS amount, netamount::numeric AS netamount, exchangerate "
                            f"FROM {KIND[vc]['module']} WHERE id = %s", (trans_id,))
                h = cur.fetchone()
            calc = il.calculate(inv, il.tax_rates(inv))
            sw = -1 if inv.credit else 1
            stored = round_amount(Decimal(h["amount"]) * sw, lib.precision())
            cd = inv.discount is not None and inv.discount.amount
            if not cd and round_amount(calc.invamount * sw, lib.precision()) != stored:
                warning = _("Let op: met de huidige btw-instellingen van de artikelen en de relatie komt deze factuur "
                            "op {new} (geboekt: {old}). Bij opslaan wordt het nieuwe bedrag geboekt.").format(
                    new=format_amount(calc.invamount * sw, nf, lib.precision()),
                    old=format_amount(stored, nf, lib.precision()))
        return _page(ctx, lib, f, inv=inv, readonly_reason=reason, warning=warning)


def post_form(ctx: Ctx, vc: str, form: FormData):
    require_module(ctx, vc)
    require_role(ctx, "bookkeeper")
    f = form_from_post(vc, form)
    action = str(form.get("action", "update"))
    nf = ctx.user["numberformat"]
    prefix = KIND[vc]["prefix"]
    conn, lib = _lib(ctx)
    with conn:
        precision = lib.precision()
        if action == "cancel":
            lib.arap.unlock(vc)
            raise Redirect(f"/{prefix}/nieuw" + ("?creditnota=1" if f.note else ""))
        upload_error = None
        if action != "calc":
            # bijlagen bij een nieuwe factuur (stap 6a, zoals 4e): gaan als token mee tot Opslaan
            attach.check_pending(ctx, f.refs, form)
            if attach.drop_pending(ctx, form, f.refs):
                action = "update"
            upload_error = attach.take_upload(ctx, form, f.refs) if not f.id else None
            attach.mark_duplicates(ctx, lib.conn, f.refs)
        errors = recalc(lib, f, nf, precision, _defcur(lib))
        if action == "calc":
            return _page(ctx, lib, f, calc_error=" ".join(errors) or None)
        if upload_error:
            return _page(ctx, lib, f, status=400, error=upload_error)
        if action == "update":
            return _page(ctx, lib, f, error=" ".join(errors) or None)
        if action == "new_number":
            f.invnumber = lib.arap.next_invnumber(vc, _date(f.transdate), f.currency or None, f.description or None)
            return _page(ctx, lib, f)
        il = InvoiceLedger(conn, lib.cfg)
        if action in ("delete", "delete_confirmed"):
            if not f.id:
                return _page(ctx, lib, f, status=400, error=_("Er is nog niets opgeslagen om te verwijderen."))
            if action == "delete":
                return _page(ctx, lib, f, confirm="delete")
            try:
                kept = il.delete_invoice(f.id, vc)
            except LedgerError as exc:
                return _page(ctx, lib, f, status=400, error=message(exc))
            ctx.log("invoice_deleted", id=f.id, module=KIND[vc]["module"], invnumber=f.invnumber)
            raise Redirect(f"/{prefix}/nieuw?ok=" + ("invoice_backorder_kept" if kept else "invoice_deleted"))
        with_pdf = action == "post_pdf" and not f.id      # "Opslaan en PDF" (D7/DS2), alleen nieuw
        if with_pdf:
            action = "post"
        if action not in ("post", "post_confirmed", "post_as_new"):
            raise HTTPException(400, _("Onbekende actie."))
        if errors:
            return _page(ctx, lib, f, status=400, error=" ".join(errors))
        if not any(ln.parts_id for ln in f.lines):
            return _page(ctx, lib, f, status=400, error=_("Voeg minstens één artikel toe."))
        if action == "post_as_new":
            for_new(lib, f)
        if vc == "vendor" and f.vc_id and f.invnumber and not f.dup_ok:
            dups = lib.arap.same_invnumber(vc, f.vc_id, f.invnumber, f.id)
            if dups:
                return _page(ctx, lib, f, confirm="duplicate", duplicates=dups, next_action=action)
        if f.id and action == "post":
            return _page(ctx, lib, f, confirm="post")
        new = not f.id
        missing = attach.resolve_pending(ctx, f.refs)
        if missing:
            return _page(ctx, lib, f, status=400, error=missing)
        try:
            trans_id = do_post(lib, f, nf)
        except ValueError as exc:
            return _page(ctx, lib, f, status=400, error=lib_message(exc))
        except LedgerError as exc:
            return _page(ctx, lib, f, status=400, error=lib_message(Exception(message(exc))))
        attach.discard_pending(ctx, f.refs)
        with cursor(conn) as cur:
            cur.execute(f"SELECT invnumber FROM {KIND[vc]['module']} WHERE id = %s", (trans_id,))
            invnumber = cur.fetchone()["invnumber"]
        ctx.log("invoice_posted", id=trans_id, module=KIND[vc]["module"], invnumber=invnumber, new=new, type=f.type)
        pdf = f"&pdf={_doc_formname(f)}" if with_pdf else ""
        raise Redirect(f"/{prefix}/{trans_id}?ok=invoice_posted{pdf}")


def item_options(ctx: Ctx, vc: str):
    """Keuzelijst bij het artikelveld van een regel: artikelen op nummer, barcode of omschrijving."""
    q = ctx.request.query_params
    require_module(ctx, vc)
    text = (q.get("text") or "").strip()
    conn, lib = _lib(ctx)
    rows = []
    if text:
        with conn:
            rows = InvoiceLedger(conn, lib.cfg).find_items(vc, text, limit=ITEMS_FETCH)
    return render(ctx, "is/item_options.html", rows=rows[:OPTIONS_MAX], more=max(0, len(rows) - OPTIONS_MAX),
                  label=_item_label)


def _routes(vc: str) -> None:
    prefix = KIND[vc]["prefix"]

    @router.get(f"/{prefix}/nieuw", name=f"{prefix}_new")
    def _new(ctx: Ctx = Depends(ctx_company)):
        return new_form(ctx, vc)

    @router.get(f"/{prefix}/artikelen", name=f"{prefix}_items")
    def _items(ctx: Ctx = Depends(ctx_company)):
        return item_options(ctx, vc)

    @router.get(f"/{prefix}/{{trans_id}}", name=f"{prefix}_edit")
    def _edit(trans_id: int, ctx: Ctx = Depends(ctx_company)):
        return edit_form(ctx, vc, trans_id)

    @router.post(f"/{prefix}", name=f"{prefix}_post")
    def _post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(upload_form)):
        return post_form(ctx, vc, form)


_routes("customer")
_routes("vendor")


def doc_renderer(ctx: Ctx) -> DocumentRenderer:
    return DocumentRenderer(ctx.settings.data_dir, ctx.company["id"], ctx.company.get("doc_language") or "nl")
