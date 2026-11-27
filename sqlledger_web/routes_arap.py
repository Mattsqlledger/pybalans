"""Verkoop- en inkoopboekingen zonder artikelen (stap 5): invoeren, wijzigen,
creditnota, betalingen en contante korting op de boeking, verwijderen.

Het scherm volgt aa.pl: relatie, kopgegevens, regels (bedrag, rekening,
omschrijving, project), btw per rekening met "berekenen", totaal met de
debiteuren-/crediteurenrekening, betalingen. Btw en totalen rekent alleen de
server (bibliotheek); het scherm vraagt ze via HTMX op bij een wijziging.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Set

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from starlette.datastructures import FormData

from sqlledger import LedgerError
from sqlledger._db import currencies, cursor, default_currency, get_defaults
from sqlledger.amounts import format_amount, parse_amount, round_amount
from sqlledger.arap import ArapLine, ArapPayment, ArapTransaction

from .companies import connect_company
from . import doctypes
from .documents import DocumentRenderer
from .i18n import N_, gettext as _
from .libmsg import lib_message
from .reportdefs import Lib
from . import routes_attach as attach
from .routes_gl import RefRow, _accno, references, refs_from_post
from .routes_gl import message as gl_message
from .web import Ctx, Redirect, ctx_company, render, require_role, upload_form

router = APIRouter()

#: url-voorvoegsel, module en teksten per soort relatie
KIND = {
    "customer": dict(prefix="verkoop", module="ar", vc_label=N_("Klant"), title=N_("Verkoopboeking"),
                     note=N_("Creditnota verkoop"), note_type="credit_note",
                     title_add=N_("Verkoopboeking toevoegen"), title_edit=N_("Verkoopboeking wijzigen"),
                     note_add=N_("Creditnota verkoop toevoegen"), note_edit=N_("Creditnota verkoop wijzigen"),
                     vc_not_found=N_("Geen klant gevonden voor “{text}”."),
                     vc_ambiguous=N_("Meer dan één klant gevonden voor “{text}”: kies er een uit de lijst.")),
    "vendor": dict(prefix="inkoop", module="ap", vc_label=N_("Leverancier"), title=N_("Inkoopboeking"),
                   note=N_("Creditnota inkoop"), note_type="debit_note",
                   title_add=N_("Inkoopboeking toevoegen"), title_edit=N_("Inkoopboeking wijzigen"),
                   note_add=N_("Creditnota inkoop toevoegen"), note_edit=N_("Creditnota inkoop wijzigen"),
                   vc_not_found=N_("Geen leverancier gevonden voor “{text}”."),
                   vc_ambiguous=N_("Meer dan één leverancier gevonden voor “{text}”: kies er een uit de lijst.")),
}

MESSAGES = {
    "Invoice Date missing!": N_("Factuurdatum ontbreekt."),
    "Customer missing!": N_("Kies een klant."),
    "Vendor missing!": N_("Kies een leverancier."),
    "Payment date missing!": N_("Vul de datum van de betaling in."),
    "Cannot post payment for a closed period!": N_("De betaaldatum valt in een afgesloten periode."),
    "Cash Discount date missing!": N_("Vul de datum van de korting in."),
    "Cannot post cash discount for a closed period!": N_("De kortingsdatum valt in een afgesloten periode."),
    "Date for cash discount past due!": N_("De kortingstermijn is verstreken op die datum."),
    "Cash discount exceeds available discount!": N_("De korting is hoger dan de beschikbare korting."),
    "Exchange rate for payment missing!": N_("Vul de koers van de betaling in."),
    "No account for exchange rate gain/loss (fxgainloss_accno_id)":
        N_("Er is geen rekening voor koersverschillen ingesteld (Beheer > Instellingen)."),
    "Transactions in batches are not supported": N_("Boekingen in een batch vallen buiten deze versie."),
}


def message(exc: Exception) -> str:
    text = str(exc)
    if text in MESSAGES:
        return _(MESSAGES[text])
    return _(gl_message(exc))


def require_module(ctx: Ctx, vc: str) -> None:
    if KIND[vc]["module"] not in (ctx.company["modules"] or []):
        raise HTTPException(403, _("Deze module staat uit voor deze administratie."))


# ------------------------------------------------------------------ formuliermodel

@dataclass
class ALine:
    amount: str = ""
    accno: str = ""
    description: str = ""
    project_id: str = ""
    cleared: str = ""


@dataclass
class TaxRow:
    accno: str
    amount: str = ""
    calc: bool = True
    rate: Optional[Decimal] = None       # None: rekening hoort niet (meer) bij de relatie
    description: str = ""

    @property
    def pct(self) -> str:
        if self.rate is None:
            return ""
        return format((self.rate * 100).normalize(), "f").replace(".", ",") + "%"


@dataclass
class PayRow:
    datepaid: str = ""
    olddatepaid: str = ""
    source: str = ""
    memo: str = ""
    amount: str = ""
    exchangerate: str = ""
    accno: str = ""
    cleared: str = ""
    paymentmethod_id: str = ""


@dataclass
class ArapForm:
    vc: str
    type: str = "transaction"
    id: Optional[int] = None
    vc_text: str = ""
    vc_shown: str = ""          # tekst zoals de server de relatie het laatst toonde
    vc_id: Optional[int] = None
    invnumber: str = ""
    ordnumber: str = ""
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
    dcn: str = ""
    taxincluded: bool = False
    checktax: bool = False
    arap_accno: str = ""
    bank_accno: str = ""
    paymentmethod_id: str = ""
    language_code: str = ""
    onhold: bool = False
    cashdiscount: str = ""
    discountterms: str = ""
    dup_ok: bool = False
    lines: List[ALine] = field(default_factory=list)
    taxes: List[TaxRow] = field(default_factory=list)
    pays: List[PayRow] = field(default_factory=list)
    discount: PayRow = field(default_factory=PayRow)
    refs: List[RefRow] = field(default_factory=list)
    # berekend (niet ingelezen)
    subtotal: Decimal = Decimal(0)
    invtotal: Decimal = Decimal(0)
    totalpaid: Decimal = Decimal(0)
    outstanding: Decimal = Decimal(0)
    cd_available: Decimal = Decimal(0)

    @property
    def note(self) -> bool:
        return self.type.endswith("_note")


def _filled(ln: ALine) -> bool:
    return bool(ln.amount.strip() or ln.description.strip() or ln.project_id)


def _pay_filled(p: PayRow) -> bool:
    return bool(p.amount.strip())


def _pad(f: ArapForm) -> ArapForm:
    """Altijd een lege regel onderaan bij de regels en de betalingen."""
    while not f.lines or _filled(f.lines[-1]) or len(f.lines) < 2:
        f.lines.append(ALine(accno=f.lines[-1].accno if f.lines else ""))
        if len(f.lines) >= 200:
            break
    if not f.pays or _pay_filled(f.pays[-1]):
        f.pays.append(PayRow(accno=f.bank_accno))
    if not f.refs or f.refs[-1].description or f.refs[-1].is_file:
        f.refs.append(RefRow())
    return f


def form_from_post(vc: str, form: FormData) -> ArapForm:
    g = lambda k: str(form.get(k, "")).strip()  # noqa: E731
    vid = g("vc_id")
    ttype = g("type")
    if ttype not in ("transaction", KIND[vc]["note_type"]):
        ttype = "transaction"
    f = ArapForm(vc=vc, type=ttype, id=int(g("id")) if g("id").isdigit() else None,
                 vc_text=g("vc_text"), vc_shown=g("vc_shown"), vc_id=int(vid) if vid.isdigit() else None,
                 invnumber=g("invnumber")[:100], ordnumber=g("ordnumber")[:100], ponumber=g("ponumber")[:100],
                 transdate=g("transdate"), oldtransdate=g("oldtransdate"), duedate=g("duedate"),
                 oldduedate=g("oldduedate"), terms=g("terms") or "0", oldterms=g("oldterms") or "0",
                 currency=g("currency"), oldcurrency=g("oldcurrency"), exchangerate=g("exchangerate"),
                 department_id=g("department_id"), employee_id=g("employee_id"),
                 description=str(form.get("description", "")), notes=str(form.get("notes", "")),
                 dcn=g("dcn"), taxincluded=g("taxincluded") == "1", checktax=g("checktax") == "1",
                 arap_accno=g("arap_accno"), bank_accno=g("bank_accno"), paymentmethod_id=g("paymentmethod_id"),
                 language_code=g("language_code"), onhold=g("onhold") == "1", cashdiscount=g("cashdiscount"),
                 discountterms=g("discountterms"), dup_ok=g("dup_ok") == "1")
    n = int(g("rowcount")) if g("rowcount").isdigit() else 0
    for i in range(1, min(n, 500) + 1):
        f.lines.append(ALine(amount=g(f"amount_{i}"), accno=g(f"accno_{i}"), description=g(f"description_{i}"),
                             project_id=g(f"project_{i}"), cleared=g(f"cleared_{i}")))
    f.lines = [ln for ln in f.lines if _filled(ln)]
    for a in form.getlist("taxaccount"):
        a = str(a)
        f.taxes.append(TaxRow(accno=a, amount=g(f"tax_{a}"), calc=g(f"calctax_{a}") == "1"))
    m = int(g("paidcount")) if g("paidcount").isdigit() else 0
    for i in range(1, min(m, 200) + 1):
        f.pays.append(PayRow(datepaid=g(f"datepaid_{i}"), olddatepaid=g(f"olddatepaid_{i}"), source=g(f"source_{i}"),
                             memo=g(f"memo_{i}"), amount=g(f"paid_{i}"), exchangerate=g(f"exchangerate_{i}"),
                             accno=g(f"paidaccno_{i}"), cleared=g(f"paidcleared_{i}"),
                             paymentmethod_id=g(f"paymentmethod_{i}")))
    if f.pays and not _pay_filled(f.pays[-1]) and f.pays[-1].accno:
        f.bank_accno = f.pays[-1].accno      # aa.pl: rekening van de lege betaalregel wordt de standaard
    f.pays = [p for p in f.pays if _pay_filled(p)]
    f.discount = PayRow(datepaid=g("discount_datepaid"), olddatepaid=g("discount_olddatepaid"),
                        source=g("discount_source"), memo=g("discount_memo"), amount=g("discount_paid"),
                        exchangerate=g("discount_exchangerate"), accno=g("discount_accno"),
                        cleared=g("discount_cleared"))
    f.refs = refs_from_post(form)
    return f


def _vc_label(row) -> str:
    return f"{row['number'] or ''} {row['name'] or ''}".strip()


def _iso(d) -> str:
    return d.isoformat() if d else ""


def _rate_text(v, nf: str) -> str:
    """Koers voor het scherm: zonder overbodige nullen, in de notatie van de gebruiker."""
    if v in (None, "") or Decimal(v) == 0:
        return ""
    s = format(Decimal(v).normalize(), "f")
    return s.replace(".", ",") if nf in ("1.000,00", "1000,00") else s


def form_from_trans(lib: Lib, t: ArapTransaction, nf: str, precision: int, copy: bool = False) -> ArapForm:
    def amt(v):
        return format_amount(v, nf, precision) if v else ""
    info = lib.arap.vc_info(t.vc, t.vc_id)
    names = _account_names(lib, t.vc)
    foreign = bool(t.currency) and t.currency != _defcur(lib)
    rt = lambda v: _rate_text(v, nf) if foreign else ""  # noqa: E731
    label = lambda a: f"{a} {names.get(a, '')}".strip()  # noqa: E731
    f = ArapForm(vc=t.vc, type=t.type, id=None if copy else t.id, vc_id=t.vc_id,
                 invnumber="" if copy else (t.invnumber or ""), ordnumber=t.ordnumber or "",
                 ponumber=t.ponumber or "", transdate=_iso(t.transdate), duedate=_iso(t.duedate),
                 terms=str(t.terms or 0), currency=t.currency or "", exchangerate=rt(t.exchangerate),
                 department_id=str(t.department_id or ""), employee_id=str(t.employee_id or ""),
                 description=t.description or "", notes=t.notes or "", dcn=t.dcn or "",
                 taxincluded=t.taxincluded, checktax=t.taxincluded, arap_accno=t.arap_accno or "",
                 bank_accno=t.bank_accno or "", paymentmethod_id=str(t.paymentmethod_id or ""),
                 language_code=t.language_code or "", onhold=t.onhold,
                 cashdiscount=format_amount(t.cashdiscount, nf) if t.cashdiscount else "",
                 discountterms=str(t.discountterms or ""))
    f.vc_text = f.vc_shown = _vc_label(info)
    # bij openen blijft de vervaldatum staan (Perl laadde oldterms niet en rekende hem opnieuw uit)
    f.oldtransdate, f.oldduedate, f.oldterms, f.oldcurrency = f.transdate, f.duedate, f.terms, f.currency
    for ln in t.lines:
        f.lines.append(ALine(amount=amt(ln.amount), accno=label(ln.accno), description=ln.description or "",
                             project_id=str(ln.project_id or ""),
                             cleared="" if copy or not ln.cleared else ln.cleared.isoformat()))
    for a in sorted(set(t.taxrates) | set(t.taxes)):
        f.taxes.append(TaxRow(accno=a, amount=amt(t.taxes.get(a)), calc=a in t.calctax))
    if not copy:
        for p in t.payments:
            f.pays.append(PayRow(datepaid=_iso(p.datepaid), olddatepaid=_iso(p.datepaid), source=p.source or "",
                                 memo=p.memo or "", amount=amt(p.amount), exchangerate=rt(p.exchangerate),
                                 accno=p.accno, cleared=_iso(p.cleared),
                                 paymentmethod_id=str(p.paymentmethod_id or "")))
        if t.discount:
            d = t.discount
            f.discount = PayRow(datepaid=_iso(d.datepaid), olddatepaid=_iso(d.datepaid), source=d.source or "",
                                memo=d.memo or "", amount=amt(d.amount), exchangerate=rt(d.exchangerate),
                                accno=d.accno, cleared=_iso(d.cleared))
    for r in t.references:
        if copy and r.archive_id:
            continue                # bijlage blijft bij de oorspronkelijke boeking
        f.refs.append(RefRow(description=r.description or "", code=r.code or "",
                             archive_id=str(r.archive_id or ""), folder=r.folder or "",
                             confidential=bool(r.confidential), filename=r.filename or ""))
    return f


# ------------------------------------------------------------------ rekenen

#: foutmelding bij een ongeldig getal, per soort getal (``what`` van _amount)
INVALID = {
    "bedrag": N_("Ongeldig bedrag: {value}"),
    "percentage": N_("Ongeldig percentage: {value}"),
    "koers": N_("Ongeldige koers: {value}"),
    "afschriftsaldo": N_("Ongeldig afschriftsaldo: {value}"),
}


def _invalid(what: str, text: str) -> str:
    if what in INVALID:
        return _(INVALID[what]).format(value=text)
    return _("Ongeldig {what}: {value}").format(what=_(what), value=text)


def _amount(text: str, nf: str, what: str = "bedrag") -> Decimal:
    text = (text or "").strip()
    if not text:
        return Decimal(0)
    if not re.fullmatch(r"[0-9.,' -]+", text):
        raise ValueError(_invalid(what, text))
    try:
        return parse_amount(text, nf)
    except (InvalidOperation, ValueError):
        raise ValueError(_invalid(what, text))


def _date(text: str) -> Optional[date]:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def _account_names(lib: Lib, vc: str) -> Dict[str, str]:
    with cursor(lib.conn) as cur:
        cur.execute("""SELECT c.accno, COALESCE(l.description, c.description) AS description FROM chart c
                       LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %s)""",
                    (lib.cfg.countrycode,))
        return {r["accno"]: r["description"] for r in cur.fetchall()}


def _same_label(a: str, b: str) -> bool:
    return " ".join((a or "").lower().split()) == " ".join((b or "").lower().split())


def exact_label_vc(lib: Lib, vc: str, text: str, transdate: Optional[date] = None) -> List[Dict]:
    """Relatie met precies het label uit de zoeklijst ("nummer naam"), ook als het nummer spaties bevat
    (Cookie: "Per Incasso"); spaties tellen niet dubbel (PB17)."""
    words = text.split()
    for i in range(1, len(words)):
        hits = {r["id"]: r for r in lib.arap.find_names(vc, number=" ".join(words[:i]), transdate=transdate)
                if _same_label(_vc_label(r), text)}
        if hits:
            return list(hits.values())
    return []


def resolve_vc(lib: Lib, f: ArapForm) -> Optional[str]:
    """Relatie uit het zoekveld bepalen (arap.pl check_name). Geeft een foutmelding
    terug als de tekst niet (eenduidig) bij een relatie hoort."""
    text = f.vc_text.strip()
    if f.vc_id and text == f.vc_shown:
        return None
    if not text:
        f.vc_id = None
        return None
    td = _date(f.transdate)
    first = text.split(" ", 1)[0]
    hits = [r for r in lib.arap.find_names(f.vc, number=f'"{first}"', transdate=td)
            if (r["number"] or "") == first]
    if len(hits) != 1:
        hits = exact_label_vc(lib, f.vc, text, td) or hits
    if len(hits) != 1:
        hits = lib.arap.find_names(f.vc, name=text, transdate=td)
        exact = [r for r in hits if (r["name"] or "").lower() == text.lower() or _vc_label(r).lower() == text.lower()]
        if len(exact) == 1:
            hits = exact
    if not hits:
        return _(KIND[f.vc]["vc_not_found"]).format(text=text)
    if len(hits) > 1:
        return _(KIND[f.vc]["vc_ambiguous"]).format(text=text)
    f.vc_id = hits[0]["id"]
    f.vc_text = _vc_label(hits[0])
    return None


def apply_vc_defaults(lib: Lib, f: ArapForm, nf: str) -> None:
    """Nieuwe relatie gekozen: standaardwaarden laden (AA::get_name)."""
    d = lib.arap.vc_defaults(f.vc, f.vc_id, _date(f.transdate))
    names = _account_names(lib, f.vc)
    f.terms = f.oldterms = str(d["terms"] or 0)
    td = _date(f.transdate)
    if td:
        f.duedate = f.oldduedate = (td + timedelta(days=int(d["terms"] or 0))).isoformat()
    f.taxincluded = bool(d["taxincluded"])
    f.checktax = False
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
    if d["payment_accno"] is None and not f.bank_accno:
        f.bank_accno = ""
    if not f.id:
        f.currency = d["currency"] or ""
        f.employee_id = str(d["employee_id"] or "")
        if not any(ln.amount.strip() for ln in f.lines):
            last = lib.arap.last_lines(f.vc, f.vc_id)
            if last:
                f.lines = [ALine(accno=f"{ln.accno} {names.get(ln.accno, '')}".strip(),
                                 project_id=str(ln.project_id or "")) for ln in last]
    f.taxes = [TaxRow(accno=a, calc=True) for a in sorted(d["taxrates"])]


def recalc(lib: Lib, f: ArapForm, nf: str, precision: int, defcur: str) -> List[str]:
    """Wat aa.pl update doet: relatie, termijn/vervaldatum, koersen, btw en totalen.
    Geeft fouten in de invoer terug (het formulier wordt toch getoond)."""
    errors: List[str] = []
    vc_before = f.vc_id
    err = resolve_vc(lib, f)
    if err:
        errors.append(err)
    td = _date(f.transdate)
    if f.transdate and not td:
        errors.append(_("Ongeldige datum: {value}").format(value=f.transdate))
    if f.vc_id and f.vc_id != vc_before or (f.vc_id and not f.vc_shown):
        apply_vc_defaults(lib, f, nf)
        f.vc_text = _vc_label(lib.arap.vc_info(f.vc, f.vc_id))
        new_vc = True
    else:
        new_vc = False
    f.vc_shown = f.vc_text if f.vc_id else ""

    # termijn <-> vervaldatum
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

    # koersen (Form::check_exchangerate): bij een andere datum of valuta opnieuw uit de koerstabel
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

    # zonder rekening van de relatie: de eerste uit de keuzelijst (zoals de browser in aa.pl kiest)
    acc = lib.arap.accounts(f.vc)
    ARAP = KIND[f.vc]["module"].upper()
    if not f.arap_accno and acc[ARAP]:
        f.arap_accno = acc[ARAP][0]["accno"]
    if not f.bank_accno and acc[f"{ARAP}_paid"]:
        f.bank_accno = acc[f"{ARAP}_paid"][0]["accno"]

    # btw (aa.pl update) via de bibliotheek
    rates: Dict[str, Decimal] = {}
    if f.vc_id:
        rates = lib.arap.vc_defaults(f.vc, f.vc_id, td)["taxrates"]
    have = {t.accno for t in f.taxes}
    f.taxes += [TaxRow(accno=a, calc=True) for a in sorted(rates) if a not in have]
    names = _account_names(lib, f.vc)
    for t in f.taxes:
        t.rate = rates.get(t.accno)
        t.description = names.get(t.accno, "")

    def num(text, what="bedrag"):
        try:
            return _amount(text, nf, what)
        except ValueError as exc:
            errors.append(lib_message(exc))
            return Decimal(0)
    amounts = [num(ln.amount) for ln in f.lines]
    taxes = {t.accno: num(t.amount) for t in f.taxes}
    disc = num(f.discount.amount)
    t = ArapTransaction(vc=f.vc, vc_id=f.vc_id or 0, transdate=td or date.today(), arap_accno="",
                        lines=[ArapLine(accno="", amount=a) for a in amounts], taxes=taxes,
                        taxincluded=f.taxincluded,
                        discount=ArapPayment(accno="", amount=disc) if disc else None)
    only = {x.accno for x in f.taxes if x.calc and x.rate is not None}
    taxes = lib.arap.calculate_taxes(t, rates={a: r for a, r in rates.items()}, only=only,
                                     checktax=f.checktax) if rates else taxes
    if f.taxincluded:
        f.checktax = True
    for x in f.taxes:
        v = taxes.get(x.accno, Decimal(0))
        x.amount = format_amount(v, nf, precision) if v else ""
    totaltax = sum(taxes.values(), Decimal(0))
    f.subtotal = sum(amounts, Decimal(0))
    f.invtotal = f.subtotal if f.taxincluded else f.subtotal + totaltax
    pct = num(f.cashdiscount, "percentage") if f.cashdiscount else Decimal(0)
    f.cd_available = round_amount(f.invtotal * pct / 100, precision)
    f.totalpaid = sum((num(p.amount) for p in f.pays), Decimal(0)) + disc
    f.outstanding = round_amount(f.invtotal - f.totalpaid, precision)
    return list(dict.fromkeys(errors))


def to_transaction(lib: Lib, f: ArapForm, nf: str) -> ArapTransaction:
    td = _date(f.transdate)
    if not td:
        raise ValueError(_(MESSAGES["Invoice Date missing!"]))
    if not f.vc_id:
        raise ValueError(_(MESSAGES["Customer missing!" if f.vc == "customer" else "Vendor missing!"]))
    lines = []
    for i, ln in enumerate(f.lines, 1):
        a = _amount(ln.amount, nf)
        if not a:
            continue                       # aa.pl: regels zonder bedrag vallen weg
        accno = _accno(ln.accno)
        if not accno:
            raise ValueError(_("Regel {line}: kies een rekening.").format(line=i))
        lines.append(ArapLine(accno=accno, amount=a, description=ln.description or None,
                              project_id=int(ln.project_id) if ln.project_id.isdigit() else None,
                              cleared=_date(ln.cleared)))
    taxes = {x.accno: _amount(x.amount, nf) for x in f.taxes}

    def rate(text):
        return _amount(text, nf, "koers") if text else None
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
        if not d.accno:
            raise ValueError(_("Kies de rekening voor de korting."))
        discount = ArapPayment(accno=_accno(d.accno), amount=_amount(d.amount, nf), datepaid=_date(d.datepaid),
                               source=d.source or None, memo=d.memo or None, exchangerate=rate(d.exchangerate),
                               cleared=_date(d.cleared))
    with cursor(lib.conn) as cur:
        defcur = default_currency(cur)
    currency = f.currency or defcur
    exchangerate = Decimal(1)
    if currency != defcur:
        exchangerate = rate(f.exchangerate) or Decimal(0)
    if not f.arap_accno:
        raise ValueError(_("Kies de debiteurenrekening.") if f.vc == "customer" else _("Kies de crediteurenrekening."))
    refs = references(f.refs)
    try:
        terms = int(f.terms or 0)
    except ValueError:
        raise ValueError(_("Ongeldige termijn: {value}").format(value=f.terms))
    pct = _amount(f.cashdiscount, nf, "percentage") if f.cashdiscount else Decimal(0)
    return ArapTransaction(
        vc=f.vc, vc_id=f.vc_id, transdate=td, arap_accno=_accno(f.arap_accno), lines=lines, taxes=taxes,
        id=f.id, type=f.type, invnumber=f.invnumber or None, duedate=_date(f.duedate), terms=terms,
        ordnumber=f.ordnumber or None, ponumber=f.ponumber or None, description=f.description or None,
        notes=f.notes or None, currency=currency, exchangerate=exchangerate, taxincluded=f.taxincluded,
        department_id=int(f.department_id) if f.department_id.isdigit() else None,
        employee_id=int(f.employee_id) if f.employee_id.isdigit() else None,
        language_code=f.language_code or None, payments=pays, discount=discount, cashdiscount=pct,
        discountterms=int(f.discountterms) if f.discountterms.isdigit() else 0, onhold=f.onhold,
        bank_accno=_accno(f.bank_accno) or None,
        paymentmethod_id=int(f.paymentmethod_id) if f.paymentmethod_id.isdigit() else None,
        dcn=f.dcn or None, references=refs, oldinvtotal=f.invtotal)


def for_new(lib: Lib, f: ArapForm) -> None:
    """Opslaan als nieuw: geen id, geen afletterdatums; met lock_sinumber een nieuw nummer."""
    f.id = None
    if f.vc == "customer":
        with cursor(lib.conn) as cur:
            if get_defaults(cur, "lock_sinumber").get("lock_sinumber") not in (None, "", "0"):
                f.invnumber = ""
    for ln in f.lines:
        ln.cleared = ""
    for p in f.pays + [f.discount]:
        p.cleared = ""
    f.refs = [r for r in f.refs if not r.archive_id]      # bijlagen blijven bij het origineel


def do_post(lib: Lib, f: ArapForm, nf: str) -> int:
    """Boek het (doorgerekende) formulier. Bij wijzigen blijven herhaalschema en
    afdruk-/mailstatus van de bestaande boeking staan."""
    old = lib.arap.get_transaction(f.id, f.vc) if f.id else None
    t = to_transaction(lib, f, nf)
    defcur = _defcur(lib)
    for p in t.payments + ([t.discount] if t.discount else []):
        if t.currency != defcur and not p.exchangerate and p.datepaid and p.datepaid != t.transdate:
            p.exchangerate = lib.gl.exchange_rate(t.currency, p.datepaid)
    if old:
        t.recurring = old.recurring
        t.printed, t.emailed, t.queued = old.printed, old.emailed, old.queued
    return lib.arap.post_transaction(t)


# ------------------------------------------------------------------ pagina

def _choices(lib: Lib, f: ArapForm) -> Dict[str, Any]:
    with cursor(lib.conn) as cur:
        curr = [c for c in currencies(cur) if c]
        defaults = get_defaults(cur, "lock_sinumber", "precision")
        cur.execute("SELECT count(*) AS n FROM language")
        has_languages = cur.fetchone()["n"] > 0
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
        line_accounts=acc[f"{ARAP}_amount"],
        arap_accounts=with_current(ARAP, [_accno(f.arap_accno)]),
        paid_accounts=with_current(f"{ARAP}_paid", [_accno(p.accno) for p in f.pays] + [_accno(f.bank_accno)]),
        discount_accounts=with_current(f"{ARAP}_discount", [_accno(f.discount.accno)]),
        departments=lib.gl.departments(f.vc), projects=projects,
        employees=lib.arap.employees(td, int(f.employee_id) if f.employee_id.isdigit() else None) if sales else [],
        paymentmethods=lib.arap.payment_methods(), has_languages=has_languages,
        currencies=curr, defaultcurrency=curr[0] if curr else "",
        lock_sinumber=f.vc == "customer" and defaults.get("lock_sinumber") not in (None, "", "0"),
        precision=int(defaults.get("precision") or 2))


def _title(f: ArapForm) -> str:
    k = KIND[f.vc]
    if f.note:
        return k["note_edit"] if f.id else k["note_add"]
    return k["title_edit"] if f.id else k["title_add"]


def _page(ctx: Ctx, lib: Lib, f: ArapForm, *, status: int = 200, error: Optional[str] = None,
          confirm: Optional[str] = None, trans: Optional[ArapTransaction] = None,
          readonly_reason: Optional[str] = None, duplicates=None, **kw):
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
        ml = -1 if f.note else 1
        info["creditremaining"] -= (f.invtotal - f.totalpaid) * rate * ml
    if not readonly_reason:
        _pad(f)
    recurring = trans.recurring if trans else None
    screen = ("credit_note" if f.note else "ar_transaction") if f.vc == "customer" else \
        ("debit_note" if f.note else "ap_transaction")
    docs = doctypes.screen_docs(lib.conn, screen, f.id, ctx.request.query_params.get("pdf"))
    return render(ctx, "arap/form.html", status, f=f, ch=ch, k=KIND[f.vc], info=info, error=error, docs=docs,
                  attachments=attach.saved_attachments(ctx, lib.conn, f.id),
                  variants=doctypes.variant_choices(lib.conn, doc_renderer(ctx), f.language_code),
                  confirm=confirm, title=_title(f), readonly_reason=readonly_reason, recurring=recurring,
                  duplicates=duplicates, nf=ctx.user["numberformat"], can_edit=ctx.has_role("bookkeeper"),
                  amount=lambda v, dash="": format_amount(v, ctx.user["numberformat"], ch["precision"], dash)
                  .replace("&nbsp;", "").strip(), **kw)


def _lib(ctx: Ctx):
    try:
        conn = connect_company(ctx.settings, ctx.company)
    except psycopg.Error:
        raise HTTPException(503, _("De administratie is niet bereikbaar."))
    return conn, Lib(conn, ctx.user_config())


def _defcur(lib: Lib) -> str:
    with cursor(lib.conn) as cur:
        return default_currency(cur)


def _module_of(lib: Lib, trans_id: int) -> Optional[Dict[str, Any]]:
    with cursor(lib.conn) as cur:
        cur.execute("""SELECT 'customer' AS vc, invoice FROM ar WHERE id = %(id)s
                       UNION ALL SELECT 'vendor', invoice FROM ap WHERE id = %(id)s""", {"id": trans_id})
        return cur.fetchone()


def blank_form(lib: Lib, vc: str, ttype: str, transdate: str) -> ArapForm:
    """Nieuwe boeking: begint met de relatie en afdeling van de laatste boeking (Form::lastname_used)."""
    f = ArapForm(vc=vc, type=ttype, transdate=transdate, oldtransdate=transdate)
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
        if copy.isdigit():
            m = _module_of(lib, int(copy))
            if not m or m["vc"] != vc or m["invoice"]:
                raise HTTPException(404, _("Boeking niet gevonden."))
            t = lib.arap.get_transaction(int(copy), vc)
            f = form_from_trans(lib, t, nf, lib.precision(), copy=True)
            if _choices(lib, f)["lock_sinumber"]:
                f.invnumber = ""
            f.transdate = f.oldtransdate = date.today().isoformat()
            f.oldterms = "x"             # vervaldatum opnieuw uit de termijn
        else:
            ttype = KIND[vc]["note_type"] if q.get("creditnota") == "1" else "transaction"
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
            raise HTTPException(404, _("Boeking niet gevonden."))
        if m["invoice"]:                                   # factuur met artikelen (fase 3, F9)
            raise Redirect(f"/{'verkoopfactuur' if vc == 'customer' else 'inkoopfactuur'}/{trans_id}")
        editor = ctx.has_role("bookkeeper")
        t = lib.arap.get_transaction(trans_id, vc, lock=editor)
        nf = ctx.user["numberformat"]
        f = form_from_trans(lib, t, nf, lib.precision())
        recalc(lib, f, nf, lib.precision(), _defcur(lib))
        reason = None
        if not editor:
            reason = _("Uw rol in deze administratie is alleen-lezen.")
        elif t.batch:
            reason = _("Deze boeking hoort bij een batch; batches vallen buiten deze versie.")
        elif t.locked:
            with cursor(conn) as cur:
                revtrans = get_defaults(cur, "revtrans").get("revtrans") not in (None, "", "0")
            reason = (_("Boekingen kunnen niet gewijzigd worden (instelling: alleen tegenboeken).") if revtrans
                      else _("Deze boeking valt in een afgesloten periode."))
        elif t.readonly:
            reason = _("Deze boeking is in gebruik door {user}.").format(user=t.haslock)
        return _page(ctx, lib, f, trans=t, readonly_reason=reason)


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
            # bijlagen bij een nieuwe boeking (stap 4e): gaan als token mee tot Opslaan
            attach.check_pending(ctx, f.refs, form)
            if attach.drop_pending(ctx, form, f.refs):
                action = "update"
            upload_error = attach.take_upload(ctx, form, f.refs) if not f.id else None
            attach.mark_duplicates(ctx, lib.conn, f.refs)
        errors = recalc(lib, f, nf, precision, _defcur(lib))
        if action == "calc":           # alleen btw/totalen ververst (arap.js); fout in het formulier zelf
            return _page(ctx, lib, f, calc_error=" ".join(errors) or None)
        if upload_error:
            return _page(ctx, lib, f, status=400, error=upload_error)
        if action == "update":
            return _page(ctx, lib, f, error=" ".join(errors) or None)
        if action == "new_number":
            f.invnumber = lib.arap.next_invnumber(vc, _date(f.transdate), f.currency or None, f.description or None)
            return _page(ctx, lib, f)
        if action in ("delete", "delete_confirmed"):
            if not f.id:
                return _page(ctx, lib, f, status=400, error=_("Er is nog niets opgeslagen om te verwijderen."))
            if action == "delete":
                return _page(ctx, lib, f, confirm="delete")
            try:
                lib.arap.delete_transaction(f.id, vc)
            except LedgerError as exc:
                return _page(ctx, lib, f, status=400, error=message(exc))
            ctx.log("arap_deleted", id=f.id, module=KIND[vc]["module"], invnumber=f.invnumber)
            raise Redirect(f"/{prefix}/nieuw?ok=arap_deleted")
        with_pdf = action == "post_pdf" and not f.id      # "Opslaan en PDF" (D7/DS2), alleen nieuw
        if with_pdf:
            action = "post"
        if action not in ("post", "post_confirmed", "post_as_new"):
            raise HTTPException(400, _("Onbekende actie."))
        if errors:
            return _page(ctx, lib, f, status=400, error=" ".join(errors))
        if action == "post_as_new":
            for_new(lib, f)
        # dubbel factuurnummer bij dezelfde leverancier: waarschuwen (V8)
        if vc == "vendor" and f.vc_id and f.invnumber and not f.dup_ok:
            dups = lib.arap.same_invnumber(vc, f.vc_id, f.invnumber, f.id)
            if dups:
                return _page(ctx, lib, f, confirm="duplicate", duplicates=dups, next_action=action)
        if f.id and action == "post":
            return _page(ctx, lib, f, confirm="post")        # bevestigen (V11)
        new = not f.id
        missing = attach.resolve_pending(ctx, f.refs)
        if missing:
            return _page(ctx, lib, f, status=400, error=missing)
        try:
            trans_id = do_post(lib, f, nf)
        except ValueError as exc:
            return _page(ctx, lib, f, status=400, error=lib_message(exc))
        except LedgerError as exc:
            return _page(ctx, lib, f, status=400, error=message(exc))
        attach.discard_pending(ctx, f.refs)
        with cursor(conn) as cur:
            cur.execute(f"SELECT invnumber FROM {KIND[vc]['module']} WHERE id = %s", (trans_id,))
            invnumber = cur.fetchone()["invnumber"]
        ctx.log("arap_posted", id=trans_id, module=KIND[vc]["module"], invnumber=invnumber, new=new,
                type=f.type)
        pdf = ""
        if with_pdf:
            pdf = "&pdf=" + (("credit_note" if f.note else "ar_transaction") if vc == "customer"
                             else ("debit_note" if f.note else "ap_transaction"))
        raise Redirect(f"/{prefix}/{trans_id}?ok=arap_posted{pdf}")


#: zoeklijst (PB17, static/combo.js): zoveel tonen, daarna "nog n – typ verder"
OPTIONS_MAX = 50
#: zoveel artikelen ophalen om n te kunnen tellen
ITEMS_FETCH = 500


def vc_options(ctx: Ctx, vc: str):
    """Keuzelijst bij het zoekveld relatie (HTMX): relaties op naam of nummer."""
    q = ctx.request.query_params
    require_module(ctx, vc)
    text = (q.get("vc_text") or "").strip()
    conn, lib = _lib(ctx)
    rows = []
    if text:
        with conn:
            td = _date(q.get("transdate", ""))
            seen = set()
            for r in lib.arap.find_names(vc, number=text, transdate=td) + lib.arap.find_names(vc, name=text,
                                                                                               transdate=td):
                if r["id"] not in seen:
                    seen.add(r["id"])
                    rows.append(r)
    return render(ctx, "arap/vc_options.html", rows=rows[:OPTIONS_MAX], more=max(0, len(rows) - OPTIONS_MAX),
                  label=_vc_label)


def _routes(vc: str) -> None:
    prefix = KIND[vc]["prefix"]

    @router.get(f"/{prefix}/nieuw", name=f"{prefix}_new")
    def _new(ctx: Ctx = Depends(ctx_company)):
        return new_form(ctx, vc)

    @router.get(f"/{prefix}/relaties", name=f"{prefix}_vc")
    def _vc(ctx: Ctx = Depends(ctx_company)):
        return vc_options(ctx, vc)

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
