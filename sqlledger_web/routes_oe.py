"""Orders en offertes (fase 3 stap 3, O1–O6, ON1–ON4, SA11): verkooporder, inkooporder, offerte en
offerteaanvraag in één scherm, omzetten, heropenen, verwijderen, lijsten.

Het scherm volgt oe.pl: relatie, kopgegevens (nummer, datum, leverdatum/geldig tot, termijn, valuta,
afdeling, medewerker, inkoopordernummer), artikelregels zoals de factuur (artikel, omschrijving, aantal,
eenheid, prijs, korting, leverdatum, serienummer, project), btw per rekening en totaal (alleen de server
rekent, ``sqlledger.order``), notities en afleveradres. Geen kolom "Verzonden" (ON2) en geen aanbetalingen
(O5). Omzetten: offerte -> order, order -> offerte, order/offerte -> factuur (factuurscherm met
``?order=``); de bron sluit pas bij opslaan/boeken van het doel (O2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from starlette.datastructures import FormData

from sqlledger import LedgerError
from sqlledger import support
from sqlledger._db import currencies, cursor, get_defaults
from sqlledger.amounts import _plain, format_amount
from sqlledger.order import CONVERSIONS, SHIPTO_FIELDS, TYPES, Order, OrderLedger, OrderLine

from . import doctypes
from .documents import DocumentRenderer
from .i18n import N_, gettext as _
from .libmsg import lib_message
from .reportdefs import Lib
from .routes_arap import (ITEMS_FETCH, OPTIONS_MAX, _account_names, _amount, _date, _defcur, _iso, _lib, _rate_text,
                          _vc_label, resolve_vc)
from .routes_arap import message as arap_message
from . import routes_attach as attach
from .routes_gl import RefRow, references, refs_from_post
from .routes_is import ILine, TaxShow, _item_label, _num_text, resolve_item
from .tables import Col, Row, Table, cell_text, to_csv, to_xlsx
from .reportpdf import pdf_response
from .web import Ctx, Redirect, ctx_company, render, require_role, upload_form

router = APIRouter()

KIND = {
    "sales_order": dict(prefix="verkooporder", list="verkooporders", module="oe", title=N_("Verkooporder"),
                        title_add=N_("Verkooporder toevoegen"), title_edit=N_("Verkooporder wijzigen"),
                        plural=N_("Verkooporders"), number=N_("Ordernummer"), date=N_("Orderdatum"),
                        reqdate=N_("Leverdatum"), new=N_("Nieuwe verkooporder")),
    "purchase_order": dict(prefix="inkooporder", list="inkooporders", module="oe", title=N_("Inkooporder"),
                           title_add=N_("Inkooporder toevoegen"), title_edit=N_("Inkooporder wijzigen"),
                           plural=N_("Inkooporders"), number=N_("Ordernummer"), date=N_("Orderdatum"),
                           reqdate=N_("Leverdatum"), new=N_("Nieuwe inkooporder")),
    "sales_quotation": dict(prefix="offerte", list="offertes", module="quotations", title=N_("Offerte"),
                            title_add=N_("Offerte toevoegen"), title_edit=N_("Offerte wijzigen"),
                            plural=N_("Offertes"), number=N_("Offertenummer"), date=N_("Offertedatum"),
                            reqdate=N_("Geldig tot"), new=N_("Nieuwe offerte")),
    "request_quotation": dict(prefix="offerteaanvraag", list="offerteaanvragen", module="quotations",
                              title=N_("Offerteaanvraag"), title_add=N_("Offerteaanvraag toevoegen"),
                              title_edit=N_("Offerteaanvraag wijzigen"), plural=N_("Offerteaanvragen"),
                              number=N_("Aanvraagnummer"), date=N_("Aanvraagdatum"), reqdate=N_("Leverdatum"),
                              new=N_("Nieuwe offerteaanvraag")),
}
VC_LABEL = {"customer": N_("Klant"), "vendor": N_("Leverancier")}
INVOICE_PREFIX = {"customer": "verkoopfactuur", "vendor": "inkoopfactuur"}

MESSAGES = {
    "Order is invoiced": N_("Deze order is gefactureerd en kan niet meer gewijzigd worden."),
    "Cannot delete an invoiced order": N_("Een gefactureerde order kan niet verwijderd worden; verwijder eerst de factuur."),
    "Order is closed": N_("Deze order is gesloten; heropen hem eerst."),
    "Quotation has been converted": N_("Deze offerte is al omgezet naar een order of factuur en kan niet heropend worden; verwijder eerst het vervolg."),
    "Order is in a closed period": N_("De datum valt in een afgesloten periode."),
    "Order Date missing!": N_("Orderdatum ontbreekt."),
    "Quotation Date missing!": N_("Offertedatum ontbreekt."),
    "De offerte is al gesloten of omgezet": N_("De offerte is al gesloten of omgezet."),
    "Aanbetalingen op orders worden niet ondersteund": N_("Aanbetalingen op orders worden (nog) niet ondersteund."),
}


def message(exc: Exception) -> str:
    text = str(exc)
    if text in MESSAGES:
        return _(MESSAGES[text])
    return arap_message(exc)


def require_oe(ctx: Ctx, otype: str) -> None:
    if KIND[otype]["module"] not in (ctx.company["modules"] or []):
        raise HTTPException(403, _("Deze module staat uit voor deze administratie."))


# ------------------------------------------------------------------ formuliermodel

@dataclass
class OLine(ILine):
    ship: str = ""                  # geleverd/gefactureerd aantal (bewaard, niet in het scherm; ON2)


@dataclass
class OForm:
    type: str
    id: Optional[int] = None
    vc_text: str = ""
    vc_shown: str = ""
    vc_id: Optional[int] = None
    ordnumber: str = ""
    quonumber: str = ""
    ponumber: str = ""
    transdate: str = ""
    oldtransdate: str = ""
    reqdate: str = ""
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
    taxincluded: bool = False
    language_code: str = ""
    shippingpoint: str = ""
    shipvia: str = ""
    waybill: str = ""
    warehouse_id: str = ""
    backorder: bool = False
    source_id: str = ""
    shipto: Dict[str, str] = field(default_factory=dict)
    lines: List[OLine] = field(default_factory=list)
    refs: List[RefRow] = field(default_factory=list)
    # berekend
    taxes: List[TaxShow] = field(default_factory=list)
    subtotal: Decimal = Decimal(0)
    total: Decimal = Decimal(0)

    @property
    def vc(self) -> str:
        return TYPES[self.type][0]

    @property
    def quotation(self) -> bool:
        return TYPES[self.type][1]

    @property
    def number(self) -> str:
        return self.quonumber if self.quotation else self.ordnumber


def _line_filled(ln: ILine) -> bool:
    return bool(ln.item_text.strip() or ln.parts_id or ln.qty.strip() or ln.description.strip())


def _pad(f: OForm) -> OForm:
    if not f.lines or _line_filled(f.lines[-1]):
        f.lines.append(OLine())
    if not f.refs or f.refs[-1].description or f.refs[-1].is_file:
        f.refs.append(RefRow())
    return f


def form_from_post(otype: str, form: FormData) -> OForm:
    g = lambda k: str(form.get(k, "")).strip()  # noqa: E731
    vid = g("vc_id")
    f = OForm(type=otype, id=int(g("id")) if g("id").isdigit() else None, vc_text=g("vc_text"),
              vc_shown=g("vc_shown"), vc_id=int(vid) if vid.isdigit() else None, ordnumber=g("ordnumber")[:100],
              quonumber=g("quonumber")[:100], ponumber=g("ponumber")[:100], transdate=g("transdate"),
              oldtransdate=g("oldtransdate"), reqdate=g("reqdate"), terms=g("terms") or "0",
              oldterms=g("oldterms") or "0", currency=g("currency"), oldcurrency=g("oldcurrency"),
              exchangerate=g("exchangerate"), department_id=g("department_id"), employee_id=g("employee_id"),
              description=str(form.get("description", "")), notes=str(form.get("notes", "")),
              intnotes=str(form.get("intnotes", "")), taxincluded=g("taxincluded") == "1",
              language_code=g("language_code"), shippingpoint=g("shippingpoint")[:100], shipvia=g("shipvia")[:100],
              waybill=g("waybill")[:100], warehouse_id=g("warehouse_id"), backorder=g("backorder") == "1",
              source_id=g("source_id"))
    f.shipto = {k: g(f"shipto{k}")[:64] for k in SHIPTO_FIELDS}
    if g("shiptorecurring") == "1":
        f.shipto["recurring"] = "1"
    n = int(g("rowcount")) if g("rowcount").isdigit() else 0
    for i in range(1, min(n, 500) + 1):
        pid = g(f"parts_id_{i}")
        ln = OLine(item_text=g(f"item_{i}"), item_shown=g(f"item_shown_{i}"),
                   parts_id=int(pid) if pid.isdigit() else None, id=g(f"line_id_{i}"),
                   description=g(f"description_{i}"), qty=g(f"qty_{i}"), unit=g(f"unit_{i}")[:5],
                   sellprice=g(f"sellprice_{i}"), discount=g(f"discount_{i}"), deliverydate=g(f"deliverydate_{i}"),
                   serialnumber=g(f"serialnumber_{i}"), project_id=g(f"project_{i}"),
                   ordernumber=g(f"ordernumber_{i}"), ponumber=g(f"ponumber_{i}"), itemnotes=g(f"itemnotes_{i}"),
                   cost=g(f"cost_{i}"), vendor=g(f"vendor_{i}"), vendor_id=g(f"vendor_id_{i}"),
                   lineitemdetail=g(f"lineitemdetail_{i}"), ship=g(f"ship_{i}"))
        if _line_filled(ln):
            f.lines.append(ln)
    f.refs = refs_from_post(form)
    return f


def form_from_order(lib: Lib, o: Order, nf: str, precision: int) -> OForm:
    foreign = bool(o.currency) and o.currency != _defcur(lib)
    f = OForm(type=o.type, id=o.id, vc_id=o.vc_id, ordnumber=o.ordnumber or "", quonumber=o.quonumber or "",
              ponumber=o.ponumber or "", transdate=_iso(o.transdate),
              reqdate=str(o.reqdate) if o.reqdate else "", terms=str(o.terms or 0), currency=o.currency or "",
              exchangerate=_rate_text(o.exchangerate, nf) if foreign else "",
              department_id=str(o.department_id or ""), employee_id=str(o.employee_id or ""),
              description=o.description or "", notes=o.notes or "", intnotes=o.intnotes or "",
              taxincluded=o.taxincluded, language_code=o.language_code or "", shippingpoint=o.shippingpoint or "",
              shipvia=o.shipvia or "", waybill=o.waybill or "",
              warehouse_id="" if o.warehouse_id is None else str(o.warehouse_id), backorder=o.backorder,
              source_id=str(o.source_id or ""), shipto=dict(o.shipto or {}))
    f.vc_text = f.vc_shown = _vc_label(lib.arap.vc_info(o.vc, o.vc_id))
    f.oldtransdate, f.oldterms, f.oldcurrency = f.transdate, f.terms, f.currency
    from sqlledger.invoice import InvoiceLedger
    with cursor(lib.conn) as cur:
        parts = InvoiceLedger(lib.conn, lib.cfg).parts_info(cur, [l.parts_id for l in o.lines])
    for l in o.lines:
        label = _item_label(parts.get(l.parts_id) or {})
        f.lines.append(OLine(
            item_text=label, item_shown=label, parts_id=l.parts_id, id=str(l.id or ""),
            description=l.description or "", qty=_num_text(l.qty, nf), unit=l.unit or "",
            sellprice=_num_text(l.sellprice, nf, precision), discount=_num_text(l.discount, nf) if l.discount else "",
            deliverydate=str(l.reqdate or ""), serialnumber=l.serialnumber or "",
            project_id=str(l.project_id or ""), ordernumber=l.ordernumber or "", ponumber=l.ponumber or "",
            itemnotes=l.itemnotes or "", cost="" if l.cost is None else _plain(l.cost), vendor=l.vendor or "",
            vendor_id="" if l.vendor_id is None else str(l.vendor_id), lineitemdetail="1" if l.lineitemdetail else "",
            ship="" if l.ship is None else _plain(l.ship)))
    for r in o.references:
        f.refs.append(RefRow(description=r.description or "", code=r.code or "", archive_id=str(r.archive_id or ""),
                             folder=r.folder or "", confidential=bool(r.confidential), filename=r.filename or ""))
    return f


def apply_vc_defaults(lib: Lib, f: OForm) -> None:
    """Nieuwe relatie gekozen (AA::get_name): termijn, btw inbegrepen, valuta, taal, medewerker."""
    d = lib.arap.vc_defaults(f.vc, f.vc_id, _date(f.transdate))
    # een offerteaanvraag heeft geen termijn (oe.pl form_header)
    f.terms = f.oldterms = "0" if f.type == "request_quotation" else str(d["terms"] or 0)
    f.taxincluded = bool(d["taxincluded"])
    f.language_code = d["language_code"] or ""
    if not f.id:
        f.currency = d["currency"] or ""
        f.employee_id = str(d["employee_id"] or "")


def to_order(lib: Lib, f: OForm, nf: str, strict: bool = True) -> Order:
    td = _date(f.transdate)
    if strict and not td:
        raise ValueError(_("Offertedatum ontbreekt.") if f.quotation else _("Orderdatum ontbreekt."))
    if strict and not f.vc_id:
        raise ValueError(_("Kies een klant.") if f.vc == "customer" else _("Kies een leverancier."))
    if f.reqdate and not _date(f.reqdate):
        raise ValueError(_("Ongeldige datum: {value}").format(value=f.reqdate))
    lines = []
    for i, ln in enumerate(f.lines, 1):
        if not ln.parts_id:
            if strict and (ln.item_text.strip() or ln.qty.strip()):
                raise ValueError(_("Regel {line}: kies een artikel.").format(line=i))
            continue
        if ln.deliverydate and not _date(ln.deliverydate):
            raise ValueError(_("Regel {line}: ongeldige leverdatum {value}").format(line=i, value=ln.deliverydate))
        lines.append(OrderLine(
            parts_id=ln.parts_id, qty=_amount(ln.qty, nf, "aantal"), sellprice=_amount(ln.sellprice, nf),
            discount=_amount(ln.discount, nf, "percentage"), description=ln.description, unit=ln.unit,
            reqdate=_date(ln.deliverydate), project_id=int(ln.project_id) if ln.project_id.isdigit() else None,
            serialnumber=ln.serialnumber, ordernumber=ln.ordernumber, ponumber=ln.ponumber,
            itemnotes=ln.itemnotes, lineitemdetail=ln.lineitemdetail == "1",
            cost=Decimal(ln.cost) if ln.cost else (None if ln.id else Decimal(0)), vendor=ln.vendor,
            vendor_id=int(ln.vendor_id) if ln.vendor_id.lstrip("-").isdigit() else (None if ln.id else 0),
            ship=Decimal(ln.ship) if ln.ship else None, id=int(ln.id) if ln.id.isdigit() else None))
    defcur = _defcur(lib)
    currency = f.currency or defcur
    rate = Decimal(1)
    if currency != defcur:
        rate = (_amount(f.exchangerate, nf, "koers") if f.exchangerate else Decimal(0))
    try:
        terms = int(f.terms or 0)
    except ValueError:
        raise ValueError(_("Ongeldige termijn: {value}").format(value=f.terms))
    refs = references(f.refs)
    return Order(
        type=f.type, vc_id=f.vc_id or 0, transdate=td or date.today(), lines=lines, id=f.id,
        ordnumber=f.ordnumber, quonumber=f.quonumber, ponumber=f.ponumber,     # leeg = '' zoals OE.pm
        reqdate=_date(f.reqdate), terms=terms, description=f.description, notes=f.notes,
        intnotes=f.intnotes, currency=currency, exchangerate=rate, taxincluded=f.taxincluded,
        department_id=int(f.department_id) if f.department_id.isdigit() else None,
        employee_id=int(f.employee_id) if f.employee_id.isdigit() else None,
        language_code=f.language_code, shippingpoint=f.shippingpoint, shipvia=f.shipvia,
        waybill=f.waybill, warehouse_id=int(f.warehouse_id) if f.warehouse_id.isdigit() else None,
        backorder=f.backorder, shipto=dict(f.shipto), references=refs,
        source_id=int(f.source_id) if f.source_id.isdigit() else None)


def recalc(lib: Lib, f: OForm, nf: str, precision: int, defcur: str) -> List[str]:
    """Wat oe.pl update doet: relatie, artikelen, leverdatum bij een andere termijn, koers, regelbedragen,
    btw en totalen. Geeft fouten in de invoer terug (het formulier wordt toch getoond)."""
    errors: List[str] = []
    vc_before = f.vc_id
    err = resolve_vc(lib, f)
    if err:
        errors.append(err)
    td = _date(f.transdate)
    if f.transdate and not td:
        errors.append(_("Ongeldige datum: {value}").format(value=f.transdate))
    new_vc = bool(f.vc_id and (f.vc_id != vc_before or not f.vc_shown))
    if new_vc:
        apply_vc_defaults(lib, f)
        f.vc_text = _vc_label(lib.arap.vc_info(f.vc, f.vc_id))
    f.vc_shown = f.vc_text if f.vc_id else ""
    if f.type == "request_quotation":
        f.terms = "0"
    f.oldterms = f.terms
    foreign = bool(f.currency) and f.currency != defcur
    if not foreign:
        f.exchangerate = ""
    else:
        changed = f.currency != f.oldcurrency
        if (changed or f.transdate != f.oldtransdate or not f.exchangerate) and td:
            rate = lib.gl.exchange_rate(f.currency, td)
            if rate or changed or f.transdate != f.oldtransdate:
                f.exchangerate = _rate_text(rate, nf) if rate else ""
    f.oldtransdate, f.oldcurrency = f.transdate, f.currency
    for ln in f.lines:
        before = ln.parts_id if ln.item_text.strip() == ln.item_shown else None
        e = resolve_item(lib, f, ln)
        if e:
            errors.append(e)
        if ln.parts_id and ln.parts_id != before:
            # nieuw gekozen artikel (io.pl): kostprijs = laatste inkoopprijs, ook bij inkoop; leverdatum van de
            # regel = leverdatum van de order (niet bij offertes)
            if f.vc == "vendor":
                rate = None
                if foreign:
                    try:
                        rate = _amount(f.exchangerate, nf, "koers") or None
                    except ValueError:
                        rate = None
                ln.cost = _plain(OrderLedger(lib.conn, lib.cfg).item_defaults("customer", ln.parts_id, rate)["cost"])
                ln.vendor_id = ln.vendor_id or "0"
            if not f.quotation and f.reqdate and not ln.deliverydate:
                ln.deliverydate = f.reqdate
        ln.item_shown = ln.item_text if ln.parts_id else ""
    for ln in f.lines:
        for text, what in ((ln.qty, "aantal"), (ln.sellprice, "bedrag"), (ln.discount, "percentage")):
            try:
                _amount(text, nf, what)
            except ValueError as exc:
                errors.append(lib_message(exc))
    f.taxes = []
    f.subtotal = f.total = Decimal(0)
    try:
        o = to_order(lib, f, nf, strict=False)
    except ValueError as exc:
        errors.append(lib_message(exc))
        o = None
    if o is not None and f.vc_id:
        ol = OrderLedger(lib.conn, lib.cfg)
        try:
            rates = ol.tax_rates(o)
            calc = ol.calculate(o, rates)
        except LedgerError as exc:
            errors.append(message(exc))
            calc = None
        if calc:
            names = _account_names(lib, f.vc)
            bycalc = {c.index: c for c in calc.lines}
            filled = [ln for ln in f.lines if ln.parts_id]
            for i, ln in enumerate(filled):
                c = bycalc.get(i)
                ln.linetotal = c.linetotal if c else Decimal(0)
                if c:
                    p = c.part
                    ln.stock = bool(p["inventory_accno_id"] or p["assembly"])
                    ln.onhand = p["onhand"] if ln.stock else None
            f.taxes = [TaxShow(accno=a, description=names.get(a, ""), rate=rates.get(a, Decimal(0)), amount=v)
                       for a, v in calc.taxes.items() if v]
            f.subtotal = calc.netamount if not f.taxincluded else calc.subtotal
            f.total = calc.amount
    return list(dict.fromkeys(errors))


# ------------------------------------------------------------------ pagina

def _choices(lib: Lib, f: OForm) -> Dict[str, Any]:
    with cursor(lib.conn) as cur:
        curr = [c for c in currencies(cur) if c]
        defaults = get_defaults(cur, "precision")
    td = _date(f.transdate)
    projects = lib.gl.projects(td)
    used = {ln.project_id for ln in f.lines if ln.project_id}
    if used - {str(p["id"]) for p in projects}:
        projects += [p for p in lib.gl.projects() if str(p["id"]) in used and p not in projects]
    sales = lib.arap.employees(td)
    return dict(departments=lib.gl.departments(f.vc), projects=projects,
                employees=lib.arap.employees(td, int(f.employee_id) if f.employee_id.isdigit() else None) if sales else [],
                currencies=curr, defaultcurrency=curr[0] if curr else "", precision=int(defaults.get("precision") or 2))


def _title(f: OForm) -> str:
    k = KIND[f.type]
    return k["title_edit"] if f.id else k["title_add"]


def _conversions(ctx: Ctx, f: OForm, o: Optional[Order]) -> List[Dict[str, str]]:
    """Knoppen om om te zetten (oe.pl form_footer): alleen bij een opgeslagen, open order/offerte."""
    if not f.id or not o or o.closed or o.aa_id or not ctx.has_role("bookkeeper"):
        return []
    mods = ctx.company["modules"] or []
    out = []
    for (src, dst), _closes in CONVERSIONS.items():
        if src == f.type and KIND[dst]["module"] in mods:
            out.append({"action": f"to_{dst}", "label": _(KIND[dst]["title"])})
    inv_mod = "ar" if f.vc == "customer" else "ap"
    if inv_mod in mods:
        out.append({"action": "to_invoice", "label": _("Verkoopfactuur") if f.vc == "customer" else _("Inkoopfactuur")})
    return out


def _page(ctx: Ctx, lib: Lib, f: OForm, *, status: int = 200, error: Optional[str] = None,
          confirm: Optional[str] = None, o: Optional[Order] = None, readonly_reason: Optional[str] = None,
          **kw):
    ch = _choices(lib, f)
    info = lib.arap.vc_info(f.vc, f.vc_id) if f.vc_id else None
    if not readonly_reason:
        _pad(f)
    nf = ctx.user["numberformat"]
    links = OrderLedger(lib.conn, lib.cfg).links(f.id) if f.id else {"from": [], "to": []}
    source = None
    if f.source_id.isdigit():
        with cursor(lib.conn) as cur:
            cur.execute("SELECT quonumber, ordnumber FROM oe WHERE id = %s", (int(f.source_id),))
            source = cur.fetchone()
    recurring = None
    if f.id:
        if o:
            recurring = o.recurring
        else:
            with cursor(lib.conn) as cur:
                recurring = support.get_recurring(cur, int(f.id))
    # herhaalschema (R1, RH6): alleen orders, opgeslagen, zonder aanbetaling
    repeatable = bool(f.id) and f.type in ("sales_order", "purchase_order") and not (o and o.prepaid)
    docs = doctypes.screen_docs(lib.conn, f.type, f.id, ctx.request.query_params.get("pdf"))
    kw.setdefault("attachments", attach.saved_attachments(ctx, lib.conn, f.id))
    return render(ctx, "oe/form.html", status, recurring=recurring, repeatable=repeatable, f=f, ch=ch, k=KIND[f.type], kinds=KIND, info=info, error=error, docs=docs,
                  variants=doctypes.variant_choices(lib.conn, doc_renderer(ctx), f.language_code),
                  confirm=confirm, o=o, title=_title(f), readonly_reason=readonly_reason,
                  vc_label=VC_LABEL[f.vc], nf=nf, can_edit=ctx.has_role("bookkeeper"), shipto_fields=SHIPTO_FIELDS,
                  conversions=_conversions(ctx, f, o), links=links, source=source,
                  invoice_prefix=INVOICE_PREFIX[f.vc],
                  amount=lambda v, dash="": format_amount(v, nf, ch["precision"], dash).replace("&nbsp;", "").strip(),
                  isodate=_date, **kw)


def blank_form(lib: Lib, otype: str, transdate: str) -> OForm:
    f = OForm(type=otype, transdate=transdate, oldtransdate=transdate)
    last = OrderLedger(lib.conn, lib.cfg).last_used(otype)
    if last:
        f.vc_id = last["vc_id"]
        f.department_id = str(last["department_id"] or "")
    return f


def _type_of(lib: Lib, oe_id: int) -> Optional[str]:
    with cursor(lib.conn) as cur:
        cur.execute("SELECT customer_id, quotation FROM oe WHERE id = %s", (oe_id,))
        r = cur.fetchone()
    if not r:
        return None
    from sqlledger.order import order_type
    return order_type("customer" if r["customer_id"] else "vendor", bool(r["quotation"]))


def new_form(ctx: Ctx, otype: str):
    q = ctx.request.query_params
    require_oe(ctx, otype)
    require_role(ctx, "bookkeeper")
    conn, lib = _lib(ctx)
    nf = ctx.user["numberformat"]
    with conn:
        src = q.get("van", "")
        if src.isdigit():
            st = _type_of(lib, int(src))
            if not st or (st, otype) not in CONVERSIONS:
                raise HTTPException(404, _("Order of offerte niet gevonden."))
            try:
                o = OrderLedger(conn, lib.cfg).convert(int(src), otype)
            except LedgerError as exc:
                return edit_form(ctx, st, int(src), error=message(exc))
            f = form_from_order(lib, o, nf, lib.precision())
            f.id = None
            for ln in f.lines:
                ln.id = ""
                ln.ship = ""
        else:
            f = blank_form(lib, otype, date.today().isoformat())
            if q.get("relatie", "").isdigit():
                f.vc_id = int(q["relatie"])
        errors = recalc(lib, f, nf, lib.precision(), _defcur(lib))
        return _page(ctx, lib, f, error=" ".join(errors) or None)


def readonly_reason(ctx: Ctx, o: Order) -> Optional[str]:
    if not ctx.has_role("bookkeeper"):
        return _("Uw rol in deze administratie is alleen-lezen.")
    if o.aa_id:
        return _("Deze order is gefactureerd (factuur {number}) en kan niet meer gewijzigd worden.").format(
            number=o.invnumber or o.aa_id)
    if o.locked:
        return _("Deze order valt in een afgesloten periode.")
    if o.closed:
        return (_("Deze offerte is gesloten (omgezet of afgehandeld).") if o.quotation
                else _("Deze order is gesloten."))
    if o.prepaid:
        return _("Deze order heeft een aanbetaling; aanbetalingen op orders worden (nog) niet ondersteund.")
    if o.readonly:
        return _("Deze order is in gebruik door {user}.").format(user=o.haslock)
    return None


def edit_form(ctx: Ctx, otype: str, oe_id: int, error: Optional[str] = None):
    require_oe(ctx, otype)
    conn, lib = _lib(ctx)
    with conn:
        t = _type_of(lib, oe_id)
        if not t:
            raise HTTPException(404, _("Order of offerte niet gevonden."))
        if t != otype:
            raise Redirect(f"/{KIND[t]['prefix']}/{oe_id}")
        ol = OrderLedger(conn, lib.cfg)
        o = ol.get_order(oe_id, lock=ctx.has_role("bookkeeper"))
        nf = ctx.user["numberformat"]
        f = form_from_order(lib, o, nf, lib.precision())
        recalc(lib, f, nf, lib.precision(), _defcur(lib))
        reason = readonly_reason(ctx, o)
        return _page(ctx, lib, f, o=o, readonly_reason=reason, error=error)


def post_form(ctx: Ctx, otype: str, form: FormData):
    require_oe(ctx, otype)
    require_role(ctx, "bookkeeper")
    f = form_from_post(otype, form)
    action = str(form.get("action", "update"))
    nf = ctx.user["numberformat"]
    prefix = KIND[otype]["prefix"]
    conn, lib = _lib(ctx)
    with conn:
        precision = lib.precision()
        ol = OrderLedger(conn, lib.cfg)
        if action == "cancel":
            with cursor(conn) as cur:
                from sqlledger import support
                support.remove_locks(cur, lib.cfg, "oe")
            raise Redirect(f"/{prefix}/nieuw")
        if action == "reopen" and f.id:
            try:
                ol.reopen_order(f.id)
            except LedgerError as exc:
                return edit_form(ctx, otype, f.id, error=message(exc))
            ctx.log("order_reopened", id=f.id, type=otype, number=f.number)
            raise Redirect(f"/{prefix}/{f.id}?ok=order_reopened")
        upload_error = None
        if action != "calc":
            # bijlagen bij een nieuwe order/offerte (stap 6a, zoals 4e): gaan als token mee tot Opslaan
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
            from sqlledger.numbering import next_number
            with conn.transaction(), cursor(conn) as cur:
                n = next_number(cur, TYPES[otype][3], dateformat=lib.cfg.dateformat, transdate=_date(f.transdate),
                                currency=f.currency or None, fields={"description": f.description or None})
            if f.quotation:
                f.quonumber = n
            else:
                f.ordnumber = n
            return _page(ctx, lib, f)
        if action in ("delete", "delete_confirmed"):
            if not f.id:
                return _page(ctx, lib, f, status=400, error=_("Er is nog niets opgeslagen om te verwijderen."))
            if action == "delete":
                return _page(ctx, lib, f, confirm="delete")
            try:
                ol.delete_order(f.id)
            except LedgerError as exc:
                return _page(ctx, lib, f, status=400, error=message(exc))
            ctx.log("order_deleted", id=f.id, type=otype, number=f.number)
            raise Redirect(f"/{prefix}/nieuw?ok=order_deleted")
        with_pdf = action == "save_pdf" and not f.id      # "Opslaan en PDF" (D7/DS2), alleen nieuw
        if with_pdf:
            action = "save"
        convert = action.startswith("to_")
        if action not in ("save", "save_as_new") and not convert:
            raise HTTPException(400, _("Onbekende actie."))
        if errors:
            return _page(ctx, lib, f, status=400, error=" ".join(errors))
        if not convert and not any(ln.parts_id for ln in f.lines):
            return _page(ctx, lib, f, status=400, error=_("Voeg minstens één artikel toe."))
        if action == "save_as_new":
            for_new(lib, f)
        new = not f.id
        missing = attach.resolve_pending(ctx, f.refs)
        if missing:
            return _page(ctx, lib, f, status=400, error=missing)
        try:
            if not convert or not f.id or _editable(ctx, ol, f.id):
                oe_id = do_save(lib, f, nf)
            else:
                oe_id = f.id
            o = ol.get_order(oe_id)
        except ValueError as exc:
            return _page(ctx, lib, f, status=400, error=lib_message(exc))
        except LedgerError as exc:
            return _page(ctx, lib, f, status=400, error=lib_message(Exception(message(exc))))
        attach.discard_pending(ctx, f.refs)
        ctx.log("order_saved", id=oe_id, type=otype, number=o.number, new=new)
        if convert:
            dst = action[3:]
            if dst == "invoice":
                raise Redirect(f"/{INVOICE_PREFIX[f.vc]}/nieuw?order={oe_id}")
            if (otype, dst) in CONVERSIONS:
                raise Redirect(f"/{KIND[dst]['prefix']}/nieuw?van={oe_id}")
            raise HTTPException(400, _("Onbekende actie."))
        pdf = f"&pdf={otype}" if with_pdf else ""
        raise Redirect(f"/{prefix}/{oe_id}?ok=order_saved{pdf}")


def for_new(lib: Lib, f: OForm) -> None:
    """Opslaan als nieuw (oe.pl save_as_new): geen id's, geen verzonden aantallen, geen omzetkoppeling;
    met lock_sonumber e.d. een nieuw nummer."""
    f.id = None
    f.source_id = ""
    f.backorder = False
    for ln in f.lines:
        ln.id = ""
        ln.ship = ""
    f.refs = [r for r in f.refs if not r.archive_id]      # bijlagen blijven bij het origineel (RB2)
    fld = f"lock_{TYPES[f.type][3]}"
    with cursor(lib.conn) as cur:
        lock = get_defaults(cur, fld).get(fld)
    if lock not in (None, "", "0"):
        if f.quotation:
            f.quonumber = ""
        else:
            f.ordnumber = ""


def do_save(lib: Lib, f: OForm, nf: str, _force: bool = False) -> int:
    """Sla het (doorgerekende) formulier op. Bij wijzigen blijven herhaalschema, afdruk-/mailstatus en open/
    gesloten van de bestaande order staan. ``_force``: alleen de echte-datatoets (gesloten orders opslaan)."""
    ol = OrderLedger(lib.conn, lib.cfg)
    o = to_order(lib, f, nf)
    if f.id:
        old = ol.get_order(f.id)
        o.recurring, o.printed, o.emailed, o.queued = old.recurring, old.printed, old.emailed, old.queued
        o.closed = old.closed
    return ol.save_order(o, _force=_force)


def _editable(ctx: Ctx, ol: OrderLedger, oe_id: int) -> bool:
    o = ol.get_order(oe_id)
    return readonly_reason(ctx, o) is None


def item_options(ctx: Ctx, otype: str):
    q = ctx.request.query_params
    require_oe(ctx, otype)
    text = (q.get("text") or "").strip()
    conn, lib = _lib(ctx)
    rows = []
    if text:
        with conn:
            rows = OrderLedger(conn, lib.cfg).find_items(TYPES[otype][0], text, limit=ITEMS_FETCH)
    return render(ctx, "is/item_options.html", rows=rows[:OPTIONS_MAX], more=max(0, len(rows) - OPTIONS_MAX),
                  label=_item_label)


# ------------------------------------------------------------------ lijsten

COLUMNS = [("number", N_("Nummer"), "text"), ("transdate", N_("Datum"), "date"), ("reqdate", N_("Leverdatum"), "date"),
           ("name", N_("Relatie"), "text"), ("vcnumber", N_("Relatienummer"), "text"),
           ("ponumber", N_("Inkoopordernummer"), "text"), ("description", N_("Omschrijving"), "text"),
           ("netamount", N_("Bedrag"), "amount"), ("tax", N_("Btw"), "amount"), ("amount", N_("Totaal"), "amount"),
           ("curr", N_("Valuta"), "text"), ("employee", N_("Medewerker"), "text"), ("status", N_("Status"), "text"),
           ("invnumber", N_("Factuur"), "text"), ("shipvia", N_("Verzonden via"), "text"),
           ("notes", N_("Notities"), "text")]
DEFAULT_COLUMNS = ["number", "transdate", "reqdate", "name", "netamount", "tax", "amount", "curr", "status"]
SORTS = [("transdate", N_("Datum")), ("number", N_("Nummer")), ("name", N_("Relatie")), ("reqdate", N_("Leverdatum"))]
PAGE_SIZE = 200


def _list_values(q) -> Dict[str, Any]:
    v: Dict[str, Any] = {k: (q.get(k) or "").strip()[:200] for k in ("name", "vcnumber", "number", "ponumber",
                                                                      "description", "notes", "memo")}
    run = q.get("run") == "1"
    v["open"] = q.get("open") == "1" if run else True
    v["closed"] = q.get("closed") == "1" if run else False
    for k in ("datefrom", "dateto"):
        try:
            v[k] = date.fromisoformat(q.get(k)).isoformat() if q.get(k) else ""
        except ValueError:
            v[k] = ""
    v["department_id"] = q.get("department_id") if (q.get("department_id") or "").isdigit() else ""
    v["sort"] = q.get("sort") if q.get("sort") in dict(SORTS) else "transdate"
    v["desc"] = q.get("direction") == "DESC"
    cols = [c for c in q.getlist("cols") if c in {x[0] for x in COLUMNS}]
    v["cols"] = cols if run and cols else list(DEFAULT_COLUMNS)
    return v


def list_page(ctx: Ctx, otype: str):
    require_oe(ctx, otype)
    q = ctx.request.query_params
    v = _list_values(q)
    k = KIND[otype]
    conn, lib = _lib(ctx)
    with conn:
        rows = OrderLedger(conn, lib.cfg).list_orders(
            otype, open=v["open"], closed=v["closed"], name=v["name"], vcnumber=v["vcnumber"], number=v["number"],
            ponumber=v["ponumber"], description=v["description"], notes=v["notes"], memo=v["memo"],
            datefrom=date.fromisoformat(v["datefrom"]) if v["datefrom"] else None,
            dateto=date.fromisoformat(v["dateto"]) if v["dateto"] else None,
            department_id=int(v["department_id"]) if v["department_id"] else None, sort=v["sort"], desc=v["desc"])
        precision = lib.precision()
        departments = lib.gl.departments(TYPES[otype][0])
    columns = []
    for key, label, kind in COLUMNS:
        if key in v["cols"]:
            if key == "reqdate" and TYPES[otype][1]:
                label = k["reqdate"]
            columns.append(Col(key, _(label), kind, precision if kind == "amount" else 2))
    out = []
    totals: Dict[str, Decimal] = {"netamount": Decimal(0), "tax": Decimal(0), "amount": Decimal(0)}
    inv_prefix = INVOICE_PREFIX[TYPES[otype][0]]
    for r in rows:
        cells = dict(r)
        if r["aa_id"]:
            cells["status"] = _("gefactureerd")
        elif r["closed"]:
            cells["status"] = _("gesloten")
        else:
            cells["status"] = _("nabestelling") if r["backorder"] else _("open")
        links = {"number": f"/{k['prefix']}/{r['id']}"}
        if r["aa_id"]:
            links["invnumber"] = f"/{inv_prefix}/{r['aa_id']}"
        for key in totals:
            totals[key] += r[key]
        out.append(Row("row", cells, links))
    if out:
        out.append(Row("total", {"number": _("Totaal"), **totals}))
    table = Table(columns, out)
    nf, df = ctx.user["numberformat"], ctx.user["dateformat"]
    export = q.get("export")
    fname = k["list"]
    if export == "pdf":
        return pdf_response(ctx, [table], _(k["plural"]), [], fname)
    if export in ("csv", "xlsx"):
        ctx.log("report_exported", report=fname, format=export)
        if export == "csv":
            return Response(to_csv([table], nf, df), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{fname}.csv"'})
        return Response(to_xlsx([table], _(k["plural"]), [ctx.company["name"]], nf, df),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{fname}.xlsx"'})
    p = q.get("page", "1")
    page = int(p) if p.isdigit() else 1
    paged, pages = table.page(page, PAGE_SIZE)
    page = min(max(page, 1), pages)
    export_query = urlencode([(a, b) for a, b in q.multi_items() if a not in ("export", "page", "ok")]
                             or [("run", "1")])
    return render(ctx, "oe/list.html", v=v, k=k, otype=otype, table=paged, count=len(rows), page=page, pages=pages,
                  columns=COLUMNS, sorts=SORTS, departments=departments, can_edit=ctx.has_role("bookkeeper"),
                  export_query=export_query, cell=lambda c, r: cell_text(c, r, nf, df), vc_label=VC_LABEL[TYPES[otype][0]])


def _routes(otype: str) -> None:
    k = KIND[otype]
    prefix = k["prefix"]

    @router.get(f"/{prefix}/nieuw", name=f"{prefix}_new")
    def _new(ctx: Ctx = Depends(ctx_company)):
        return new_form(ctx, otype)

    @router.get(f"/{prefix}/artikelen", name=f"{prefix}_items")
    def _items(ctx: Ctx = Depends(ctx_company)):
        return item_options(ctx, otype)

    @router.get(f"/{prefix}/{{oe_id}}", name=f"{prefix}_edit")
    def _edit(oe_id: int, ctx: Ctx = Depends(ctx_company)):
        return edit_form(ctx, otype, oe_id)

    @router.post(f"/{prefix}", name=f"{prefix}_post")
    def _post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(upload_form)):
        return post_form(ctx, otype, form)

    @router.get(f"/{k['list']}", name=f"{k['list']}_list")
    def _list(ctx: Ctx = Depends(ctx_company)):
        return list_page(ctx, otype)


for _t in KIND:
    _routes(_t)


def doc_renderer(ctx: Ctx) -> DocumentRenderer:
    return DocumentRenderer(ctx.settings.data_dir, ctx.company["id"], ctx.company.get("doc_language") or "nl")
