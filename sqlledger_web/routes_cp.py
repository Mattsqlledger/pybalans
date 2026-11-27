"""Ontvangsten en betalingen op openstaande posten (stap 6, cp.pl).

Drie schermen per soort (klant: ontvangst, leverancier: betaling):

* één relatie (cp.pl ``payment``): posten aanvinken, bedrag en korting per post,
  totaalbedrag; wat niet aan posten is toegewezen wordt een vooruitbetaling;
* meerdere relaties (cp.pl ``payments``): één regel per relatie, het bedrag wordt
  over de posten verdeeld (creditnota's eerst, dan de oudste);
* corrigeren (cp.pl ``payment_register``): betalingen zoeken en storneren of
  opnieuw uitgeven.

Er worden geen locks gezet bij het tonen (P8): het scherm stuurt per post het
openstaande bedrag mee dat het toonde, en de bibliotheek weigert te boeken als
dat intussen veranderd is. Bedragen rekent alleen de server (P5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException
from starlette.datastructures import FormData

from sqlledger import LedgerError
from sqlledger._db import currencies, cursor
from sqlledger.amounts import format_amount, round_amount
from sqlledger.payments import ChangedError, OpenInvoice, Payment, PaymentLine, Payments
from sqlledger.paymentspec import spec_key

from .i18n import N_, gettext as _
from .libmsg import lib_message
from .reportdefs import Lib
from .routes_arap import (OPTIONS_MAX, _amount, _date, _defcur, _lib, _rate_text, _same_label, _vc_label,
                          exact_label_vc)
from .routes_gl import message as gl_message
from .web import Ctx, Redirect, csrf_form, ctx_company, render, require_role

router = APIRouter()

ZERO = Decimal(0)

KIND = {
    "customer": dict(prefix="ontvangst", many="ontvangsten", title=N_("Ontvangst"), titles=N_("Ontvangsten"),
                     vc_label=N_("Klant"), vc_labels=N_("Klanten"), arap_label=N_("Debiteurenrekening"), arap="ar",
                     book="verkoop", prepay=N_("Vooruitontvangst"),
                     # samengestelde teksten van de schermen (één zin per soort)
                     prepay_lc=N_("vooruitontvangst"), vc_labels_lc=N_("klanten"),
                     back_label=N_("Terug naar ontvangsten"), many_label=N_("Ontvangsten (klanten)"),
                     one_label=N_("Ontvangst (één relatie)"), register_title=N_("Ontvangsten corrigeren"),
                     posted=N_("Ontvangst {source} geboekt: {name}, {amount}"),
                     rest_label=N_("Wordt vooruitontvangst: {amount}"), rest_short=N_("{amount} vooruitontvangst"),
                     prepay_booked=N_("en wordt geboekt als vooruitontvangst."),
                     choose=N_("Kies een klant."),
                     vc_not_found=N_("Geen klant gevonden voor “{text}”."),
                     vc_not_found_open=N_("Geen klant met openstaande posten gevonden voor “{text}”. "
                                          "Vink “Alle relaties” aan voor een vooruitbetaling."),
                     vc_ambiguous=N_("Meer dan één klant gevonden voor “{text}”: kies er een uit de lijst.")),
    "vendor": dict(prefix="betaling", many="betalingen", title=N_("Betaling"), titles=N_("Betalingen"),
                   vc_label=N_("Leverancier"), vc_labels=N_("Leveranciers"), arap_label=N_("Crediteurenrekening"),
                   arap="ap", book="inkoop", prepay=N_("Vooruitbetaling"),
                   prepay_lc=N_("vooruitbetaling"), vc_labels_lc=N_("leveranciers"),
                   back_label=N_("Terug naar betalingen"), many_label=N_("Betalingen (leveranciers)"),
                   one_label=N_("Betaling (één relatie)"), register_title=N_("Betalingen corrigeren"),
                   posted=N_("Betaling {source} geboekt: {name}, {amount}"),
                   rest_label=N_("Wordt vooruitbetaling: {amount}"), rest_short=N_("{amount} vooruitbetaling"),
                   prepay_booked=N_("en wordt geboekt als vooruitbetaling."),
                   choose=N_("Kies een leverancier."),
                   vc_not_found=N_("Geen leverancier gevonden voor “{text}”."),
                   vc_not_found_open=N_("Geen leverancier met openstaande posten gevonden voor “{text}”. "
                                        "Vink “Alle relaties” aan voor een vooruitbetaling."),
                   vc_ambiguous=N_("Meer dan één leverancier gevonden voor “{text}”: kies er een uit de lijst.")),
}

MESSAGES = {
    "Date missing!": N_("Vul de betaaldatum in."),
    "Cannot post payment for a closed period!": N_("De betaaldatum valt in een afgesloten periode."),
    "Cannot void payment for a closed period!": N_("De stornodatum valt in een afgesloten periode."),
    "Cannot move a payment into or out of a closed period!":
        N_("Een betaling kan niet naar of uit een afgesloten periode verplaatst worden."),
    "Exchange rate missing!": N_("Vul de koers in."),
    "Nothing to post!": N_("Er is niets te boeken: vink een post aan of vul een bedrag in."),
    "Amount is less than the total of the allocated payments!":
        N_("Het totaalbedrag is lager dan de som van de bedragen bij de posten."),
    "A negative payment cannot leave a prepayment": N_("Bij een negatief bedrag kan niets als vooruitbetaling overblijven."),
    "Cash discount account missing!": N_("Kies de rekening voor de contante korting."),
    "Amount is less than the credit notes of this customer/vendor":
        N_("Het bedrag is lager dan de creditnota's van deze relatie; boek dit in het scherm per relatie."),
    "Transactions in batches are not supported": N_("Boekingen in een batch vallen buiten deze versie."),
    "No account for exchange rate gain/loss (fxgainloss_accno_id)":
        N_("Er is geen rekening voor koersverschillen ingesteld (Beheer > Instellingen)."),
    "New reference (source) missing!": N_("Vul het nieuwe kenmerk in."),
    "Payment reference (source) missing!": N_("Een betaling zonder kenmerk kan niet gecorrigeerd worden."),
}


def message(exc: Exception) -> str:
    text = str(exc)
    if text in MESSAGES:
        return _(MESSAGES[text])
    return gl_message(exc)


def require_cash(ctx: Ctx) -> None:
    if "cash" not in (ctx.company["modules"] or []):
        raise HTTPException(403, _("De module Kas en bank staat uit voor deze administratie."))
    require_role(ctx, "bookkeeper")


def _int(text: str) -> Optional[int]:
    return int(text) if text and text.isdigit() else None


def _incr(source: str) -> str:
    """Volgend kenmerk: eerste getal + 1 (cp.pl ``s/(\\d+)/$1 + 1/e``)."""
    return re.sub(r"\d+", lambda m: str(int(m.group()) + 1), source or "", count=1)


# ------------------------------------------------------------------ gedeelde filters

@dataclass
class Filters:
    duedatefrom: str = ""
    duedateto: str = ""
    department_id: str = ""
    business_id: str = ""
    paymentmethod_id: str = ""
    arap_accno: str = ""
    currency: str = ""
    payment_accno: str = ""
    oldpayment_accno: str = ""
    discount_accno: str = ""
    datepaid: str = ""
    olddatepaid: str = ""
    oldcurrency: str = ""
    exchangerate: str = ""
    key: str = ""

    def selection_key(self, *extra) -> str:
        return "|".join(str(x) for x in (*extra, self.duedatefrom, self.duedateto, self.department_id,
                                          self.business_id, self.paymentmethod_id, self.arap_accno,
                                          self.currency))

    def query(self, vc_id: Optional[int] = None) -> Dict[str, Any]:
        return dict(vc_id=vc_id, currency=self.currency or None, datepaid=_date(self.datepaid),
                    duedatefrom=_date(self.duedatefrom), duedateto=_date(self.duedateto),
                    department_id=_int(self.department_id), paymentmethod_id=_int(self.paymentmethod_id),
                    arap_accno=self.arap_accno or None, business_id=_int(self.business_id))


def filters_from(g) -> Dict[str, str]:
    return {k: g(k) for k in Filters.__dataclass_fields__}


def _choices(lib: Lib, vc: str) -> Dict[str, Any]:
    ARAP = KIND[vc]["arap"].upper()
    acc = lib.arap.accounts(vc)
    with cursor(lib.conn) as cur:
        curr = [c for c in currencies(cur) if c]
        cur.execute("SELECT id, description FROM business ORDER BY rn NULLS LAST, description")
        business = cur.fetchall() if vc == "vendor" else []
    return dict(arap_accounts=acc[ARAP], paid_accounts=acc[f"{ARAP}_paid"],
                discount_accounts=acc[f"{ARAP}_discount"], departments=lib.gl.departments(vc),
                businesses=business, paymentmethods=lib.arap.payment_methods(),
                currencies=curr, defaultcurrency=curr[0] if curr else "", precision=lib.precision())


def recalc_common(lib: Lib, vc: str, f: Filters, ch: Dict[str, Any], nf: str, errors: List[str]) -> None:
    """Rekening, kenmerkvoorstel en koers (cp.pl update_payment/update_payments)."""
    if not f.payment_accno and ch["paid_accounts"]:
        f.payment_accno = ch["paid_accounts"][0]["accno"]
    if not f.discount_accno and ch["discount_accounts"]:
        f.discount_accno = ch["discount_accounts"][0]["accno"]
    if f.datepaid and not _date(f.datepaid):
        errors.append(_("Ongeldige datum: {value}").format(value=f.datepaid))
    defcur = ch["defaultcurrency"]
    if not f.currency:
        f.currency = defcur
    if f.currency == defcur:
        f.exchangerate = ""
    elif f.currency != f.oldcurrency or f.datepaid != f.olddatepaid or not f.exchangerate:
        rate = lib.gl.exchange_rate(f.currency, _date(f.datepaid)) if _date(f.datepaid) else None
        if rate or f.currency != f.oldcurrency or f.datepaid != f.olddatepaid:
            f.exchangerate = _rate_text(rate, nf) if rate else ""
    f.oldcurrency = f.currency


# ------------------------------------------------------------------ één relatie

@dataclass
class RowIn:
    checked: bool = False
    paid: str = ""
    discount: str = ""
    due: str = ""          # openstaand zoals getoond (P8)
    sug: str = ""          # laatst voorgesteld bedrag
    dsug: str = ""         # laatst voorgestelde korting
    invnumber: str = ""


@dataclass
class Row:
    inv: OpenInvoice
    checked: bool
    paid: str
    discount: str
    sug: str
    dsug: str
    total: Decimal = ZERO


@dataclass
class PayForm(Filters):
    vc: str = "customer"
    vc_text: str = ""
    vc_shown: str = ""
    vc_id: Optional[int] = None
    all_vc: bool = False
    source: str = ""
    memo: str = ""
    amount: str = ""
    oldsum: str = ""
    allbox: bool = False
    oldallbox: bool = False
    prepay_ok: bool = False
    back: str = ""                      # terug naar het scherm voor meerdere relaties
    inputs: Dict[int, RowIn] = field(default_factory=dict)
    # berekend
    rows: List[Row] = field(default_factory=list)
    info: Optional[Dict[str, Any]] = None
    sum_amount: Decimal = ZERO
    sum_due: Decimal = ZERO
    sum_paid: Decimal = ZERO
    sum_discount: Decimal = ZERO
    total: Decimal = ZERO
    rest: Decimal = ZERO
    notices: List[str] = field(default_factory=list)

    @property
    def transdate(self) -> str:          # voor het zoeken van de relatie (actief op de betaaldatum)
        return self.datepaid


def payform_from_post(vc: str, form: FormData) -> PayForm:
    g = lambda k: str(form.get(k, "")).strip()  # noqa: E731
    f = PayForm(vc=vc, **filters_from(g), vc_text=g("vc_text"), vc_shown=g("vc_shown"), vc_id=_int(g("vc_id")),
                all_vc=g("all_vc") == "1", source=g("source")[:100], memo=g("memo")[:200], amount=g("amount"),
                oldsum=g("oldsum"), allbox=g("allbox") == "1", oldallbox=g("oldallbox") == "1",
                prepay_ok=g("prepay_ok") == "1", back=g("back") == "1" and "1" or "")
    for k in form.keys():
        m = re.fullmatch(r"due_(\d+)", k)
        if m:
            i = int(m.group(1))
            f.inputs[i] = RowIn(checked=g(f"checked_{i}") == "1", paid=g(f"paid_{i}"), discount=g(f"discount_{i}"),
                                due=g(f"due_{i}"), sug=g(f"sug_{i}"), dsug=g(f"dsug_{i}"),
                                invnumber=g(f"invnumber_{i}"))
    return f


def resolve_vc(lib: Lib, f: PayForm) -> Optional[str]:
    """Relatie uit het zoekveld: zonder "Alle relaties" alleen relaties met open posten (P3)."""
    text = f.vc_text.strip()
    if f.vc_id and text == f.vc_shown:
        return None
    f.vc_id = None
    if not text:
        return None
    k = KIND[f.vc]
    if f.all_vc:
        td = _date(f.datepaid)
        first = text.split(" ", 1)[0]
        hits = [r for r in lib.arap.find_names(f.vc, number=f'"{first}"', transdate=td)
                if (r["number"] or "") == first]
        if len(hits) != 1:
            hits = exact_label_vc(lib, f.vc, text, td) or hits
        if len(hits) != 1:
            hits = lib.arap.find_names(f.vc, name=text, transdate=td)
            exact = [r for r in hits if (r["name"] or "").lower() == text.lower()
                     or _vc_label(r).lower() == text.lower()]
            hits = exact if len(exact) == 1 else hits
        hits = [dict(id=r["id"], number=r["number"], name=r["name"]) for r in hits]
    else:
        rows = _open_vc(lib, f.vc)
        first = text.split(" ", 1)[0]
        hits = [r for r in rows if (r["number"] or "") == first]
        if len(hits) != 1:
            hits = [r for r in rows if _same_label(_vc_label(r), text)] or hits
        if len(hits) != 1:
            low = text.lower()
            hits = [r for r in rows if low in (r["name"] or "").lower() or low == _vc_label(r).lower()]
            exact = [r for r in hits if (r["name"] or "").lower() == low or _vc_label(r).lower() == low]
            hits = exact if len(exact) == 1 else hits
    if not hits:
        if f.all_vc:
            return _(k["vc_not_found"]).format(text=text)
        return _(k["vc_not_found_open"]).format(text=text)
    if len(hits) > 1:
        return _(k["vc_ambiguous"]).format(text=text)
    f.vc_id = hits[0]["id"]
    f.vc_text = _vc_label(hits[0])
    return None


def _open_vc(lib: Lib, vc: str) -> List[Dict[str, Any]]:
    out = []
    for r in lib.payments.open_vc(vc):
        out.append(dict(id=r["id"], number=r.get(f"{vc}number"), name=r["name"], curr=r["curr"]))
    return out


def recalc_payment(lib: Lib, f: PayForm, ch: Dict[str, Any], nf: str) -> List[str]:
    """Wat cp.pl update_payment doet: relatie, open posten, voorstellen en totalen."""
    errors: List[str] = []
    precision = ch["precision"]
    vc_before = f.vc_id
    err = resolve_vc(lib, f)
    if err:
        errors.append(err)
    new_vc = bool(f.vc_id) and f.vc_id != vc_before or (bool(f.vc_id) and not f.vc_shown)
    if new_vc:
        info = lib.arap.vc_info(f.vc, f.vc_id)
        f.vc_text = _vc_label(info)
        cur = lib.arap.vc_defaults(f.vc, f.vc_id, _date(f.datepaid))["currency"]
        if cur and cur in ch["currencies"]:
            f.currency = cur
    f.vc_shown = f.vc_text if f.vc_id else ""

    recalc_common(lib, f.vc, f, ch, nf, errors)
    if f.payment_accno != f.oldpayment_accno or not f.source:
        f.source = lib.payments.next_source(f.vc, f.payment_accno) or "" if f.payment_accno else ""
    f.oldpayment_accno = f.payment_accno

    key = f.selection_key(f.vc_id)
    if key != f.key:
        f.inputs = {}
        f.amount = ""
        f.allbox = f.oldallbox = False
    f.key = key
    if f.allbox != f.oldallbox:
        for r in f.inputs.values():
            r.checked = f.allbox
    f.oldallbox = f.allbox

    def num(text, what="bedrag"):
        try:
            return _amount(text, nf, what)
        except ValueError as exc:
            errors.append(lib_message(exc))
            return ZERO
    fmt = lambda v: format_amount(v, nf, precision) if v else ""  # noqa: E731

    invoices: List[OpenInvoice] = []
    f.info = None
    if f.vc_id:
        f.info = lib.arap.vc_info(f.vc, f.vc_id)
        invoices = lib.payments.open_invoices(f.vc, **f.query(f.vc_id))
    seen = set()
    f.rows = []
    for inv in invoices:
        seen.add(inv.id)
        r = f.inputs.get(inv.id)
        if r is None:
            r = RowIn(checked=f.allbox)
        due_txt = fmt(inv.due)
        if r.due and r.due != due_txt and r.checked:
            f.notices.append(_("Post {item} is intussen gewijzigd: het bedrag is opnieuw voorgesteld.")
                             .format(item=inv.invnumber))
            r.paid = r.discount = ""
        if r.checked:
            dsug = fmt(inv.discount)
            if not r.discount.strip() or r.discount == r.dsug:
                r.discount = dsug if inv.calcdiscount else r.discount
            disc = num(r.discount)
            sug = fmt(inv.paid_suggested if disc == inv.discount else round_amount(inv.due - disc, precision))
            if not r.paid.strip() or r.paid == r.sug:
                r.paid = sug
            row = Row(inv=inv, checked=True, paid=r.paid, discount=r.discount, sug=sug, dsug=dsug)
            row.total = num(r.paid) + disc
        else:
            row = Row(inv=inv, checked=False, paid="", discount="", sug="", dsug="")
        f.rows.append(row)
    for i, r in f.inputs.items():
        if i not in seen and r.checked:
            f.notices.append(_("Post {item} staat niet meer open (betaald, gewijzigd of in gebruik).")
                             .format(item=r.invnumber or i))

    f.sum_amount = sum((round_amount(i.amount / i.exchangerate, precision) for i in invoices), ZERO)
    f.sum_due = sum((i.due for i in invoices), ZERO)
    f.sum_paid = sum((num(r.paid) for r in f.rows if r.checked), ZERO)
    f.sum_discount = sum((num(r.discount) for r in f.rows if r.checked), ZERO)
    if not f.amount.strip() or (f.oldsum and f.amount == f.oldsum):
        f.total = f.sum_paid
        f.amount = fmt(f.total)
    else:
        f.total = num(f.amount)
    f.oldsum = fmt(f.sum_paid)
    f.rest = round_amount(f.total - f.sum_paid, precision)
    if f.rest < 0 and f.sum_paid > 0:
        errors.append(_(MESSAGES["Amount is less than the total of the allocated payments!"]))
    return list(dict.fromkeys(errors))


def to_payment(f: PayForm, nf: str) -> Payment:
    if not f.vc_id:
        raise ValueError(_(KIND[f.vc]["choose"]))
    if not _date(f.datepaid):
        raise ValueError(_(MESSAGES["Date missing!"]))
    if not f.payment_accno:
        raise ValueError(_("Kies de betaalrekening."))
    lines = []
    for r in f.rows:
        if r.checked:
            paid, disc = _amount(r.paid, nf), _amount(r.discount, nf)
            if paid or disc:
                lines.append(PaymentLine(r.inv.id, paid, disc, expected_due=r.inv.due))
    rate = _amount(f.exchangerate, nf, "koers") if f.exchangerate else None
    return Payment(vc=f.vc, vc_id=f.vc_id, datepaid=_date(f.datepaid), payment_accno=f.payment_accno,
                   lines=lines, amount=f.total, currency=f.currency or None, exchangerate=rate,
                   source=f.source, memo=f.memo or None,
                   discount_accno=f.discount_accno if any(l.discount for l in lines) else None,
                   arap_accno=f.arap_accno or None, department_id=_int(f.department_id),
                   paymentmethod_id=_int(f.paymentmethod_id))


def _posted_summary(lib: Lib, vc: str, q) -> Optional[Dict[str, Any]]:
    """Melding na boeken (P11): de betaling opgezocht in de administratie (geen vrije tekst uit de URL)."""
    src, accno, d, vid = q.get("geboekt"), q.get("rekening"), _date(q.get("datum", "")), _int(q.get("gb", ""))
    if not (src and accno and d and vid):
        return None
    rows = lib.payments.payment_register(vc, payment_accno=accno, vc_id=vid, source_from=src, source_to=src,
                                         date_from=d, date_to=d, option="exclude")
    rows = [r for r in rows if r["source"] == src]
    if not rows:
        return None
    ids = sorted({t for r in rows for t in r["trans_ids"]})
    table = KIND[vc]["arap"]
    with cursor(lib.conn) as cur:
        cur.execute(f"SELECT id, invnumber, amount FROM {table} WHERE id = ANY(%s) ORDER BY transdate, id", (ids,))
        invs = cur.fetchall()
    return dict(source=src, name=rows[0]["name"], amount=sum((r["amount"] for r in rows), ZERO), invoices=invs,
                key=spec_key(accno, src, d, vid))


def payment_page(ctx: Ctx, lib: Lib, f: PayForm, ch, *, status=200, error=None, confirm=None, posted=None, **kw):
    if posted:
        kw["notice"] = None                 # de uitgebreide melding vervangt "Betaling geboekt."
    return render(ctx, "cp/payment.html", status, f=f, ch=ch, k=KIND[f.vc], error=error, confirm=confirm,
                  posted=posted, nf=ctx.user["numberformat"],
                  amount=lambda v, dash="": format_amount(v, ctx.user["numberformat"], ch["precision"], dash)
                  .replace("&nbsp;", "").strip(), **kw)


def new_payment(ctx: Ctx, vc: str):
    require_cash(ctx)
    q = ctx.request.query_params
    conn, lib = _lib(ctx)
    nf = ctx.user["numberformat"]
    with conn:
        ch = _choices(lib, vc)
        f = PayForm(vc=vc, datepaid=q.get("datum") if _date(q.get("datum", "")) else date.today().isoformat())
        f.olddatepaid = f.datepaid
        if q.get("rekening") in {a["accno"] for a in ch["paid_accounts"]}:
            f.payment_accno = f.oldpayment_accno = q["rekening"]
        for k, name in (("van", "duedatefrom"), ("tot", "duedateto")):
            if _date(q.get(k, "")):
                setattr(f, name, q[k])
        for k, name in (("afdeling", "department_id"), ("branche", "business_id"), ("methode", "paymentmethod_id")):
            if _int(q.get(k, "")):
                setattr(f, name, q[k])
        if q.get("valuta") in ch["currencies"]:
            f.currency = f.oldcurrency = q["valuta"]
        if q.get("debiteurenrekening") in {a["accno"] for a in ch["arap_accounts"]}:
            f.arap_accno = q["debiteurenrekening"]
        if _int(q.get("relatie", "")):
            f.vc_id = int(q["relatie"])
            f.all_vc = True
            f.back = "1" if q.get("terug") == "1" else ""
        errors = recalc_payment(lib, f, ch, nf)
        return payment_page(ctx, lib, f, ch, error=" ".join(errors) or None, posted=_posted_summary(lib, vc, q))


def post_payment(ctx: Ctx, vc: str, form: FormData):
    require_cash(ctx)
    f = payform_from_post(vc, form)
    action = str(form.get("action", "update"))
    nf = ctx.user["numberformat"]
    conn, lib = _lib(ctx)
    with conn:
        ch = _choices(lib, vc)
        errors = recalc_payment(lib, f, ch, nf)
        if action in ("calc", "update"):
            if action == "calc":           # alleen het blok met de posten ververst (cp.js)
                return payment_page(ctx, lib, f, ch, calc_error=" ".join(errors) or None)
            return payment_page(ctx, lib, f, ch, error=" ".join(errors) or None)
        if action not in ("post", "post_confirmed"):
            raise HTTPException(400, _("Onbekende actie."))
        if errors or f.notices:
            return payment_page(ctx, lib, f, ch, status=400, error=" ".join(errors) or None)
        if f.rest > 0 and action != "post_confirmed":
            return payment_page(ctx, lib, f, ch, confirm="prepay")
        try:
            p = to_payment(f, nf)
            with conn.transaction():
                res = lib.payments.post_payment(p)
        except ValueError as exc:
            return payment_page(ctx, lib, f, ch, status=400, error=lib_message(exc))
        except ChangedError as exc:
            f.notices = []
            recalc_payment(lib, f, ch, nf)
            return payment_page(ctx, lib, f, ch, status=409, error=message(exc))
        except LedgerError as exc:
            return payment_page(ctx, lib, f, ch, status=400, error=message(exc))
        ctx.log("payment_posted", module=KIND[vc]["arap"], vc_id=f.vc_id, source=res.source,
                amount=str(f.total), trans_ids=res.trans_ids, prepayment=res.overpayment_id)
        params = dict(ok="payment_posted", rekening=f.payment_accno, datum=f.datepaid, geboekt=res.source or "",
                      gb=f.vc_id)
        if f.back:
            raise Redirect(f"/{KIND[vc]['many']}?" + urlencode(dict(params, **_filter_params(f))))
        raise Redirect(f"/{KIND[vc]['prefix']}/nieuw?" + urlencode(params))


def _filter_params(f: Filters) -> Dict[str, str]:
    out = {"van": f.duedatefrom, "tot": f.duedateto, "afdeling": f.department_id, "branche": f.business_id,
           "methode": f.paymentmethod_id, "valuta": f.currency, "debiteurenrekening": f.arap_accno}
    return {k: v for k, v in out.items() if v}


def vc_options(ctx: Ctx, vc: str):
    """Keuzelijst bij het zoekveld: relaties met open posten, of alle actieve relaties."""
    require_cash(ctx)
    q = ctx.request.query_params
    text = (q.get("vc_text") or "").strip().lower()
    conn, lib = _lib(ctx)
    rows = []
    if text:
        with conn:
            if q.get("all_vc") == "1":
                td = _date(q.get("datepaid", ""))
                seen = set()
                for r in (lib.arap.find_names(vc, number=text, transdate=td)
                          + lib.arap.find_names(vc, name=text, transdate=td)):
                    if r["id"] not in seen:
                        seen.add(r["id"])
                        rows.append(r)
            else:
                rows = [r for r in _open_vc(lib, vc)
                        if text in (r["name"] or "").lower() or text in (r["number"] or "").lower()]
    return render(ctx, "arap/vc_options.html", rows=rows[:OPTIONS_MAX], more=max(0, len(rows) - OPTIONS_MAX),
                  label=_vc_label)


# ------------------------------------------------------------------ meerdere relaties

@dataclass
class VcIn:
    checked: bool = False
    paid: str = ""
    memo: str = ""
    source: str = ""
    sug: str = ""
    due: str = ""


@dataclass
class VcRow:
    vc_id: int
    name: str
    number: str
    invoices: List[OpenInvoice]
    amount: Decimal
    due: Decimal
    discount: Decimal
    suggested: Decimal
    checked: bool = False
    paid: str = ""
    memo: str = ""
    source: str = ""
    sug: str = ""
    rest: Decimal = ZERO


@dataclass
class PaysForm(Filters):
    vc: str = "customer"
    allbox: bool = False
    oldallbox: bool = False
    prepay_ok: bool = False
    inputs: Dict[int, VcIn] = field(default_factory=dict)
    rows: List[VcRow] = field(default_factory=list)
    sum_amount: Decimal = ZERO
    sum_due: Decimal = ZERO
    sum_paid: Decimal = ZERO
    sum_rest: Decimal = ZERO
    notices: List[str] = field(default_factory=list)


def paysform_from_post(vc: str, form: FormData) -> PaysForm:
    g = lambda k: str(form.get(k, "")).strip()  # noqa: E731
    f = PaysForm(vc=vc, **filters_from(g), allbox=g("allbox") == "1", oldallbox=g("oldallbox") == "1",
                 prepay_ok=g("prepay_ok") == "1")
    for k in form.keys():
        m = re.fullmatch(r"vcdue_(\d+)", k)
        if m:
            i = int(m.group(1))
            f.inputs[i] = VcIn(checked=g(f"vcchecked_{i}") == "1", paid=g(f"vcpaid_{i}"), memo=g(f"vcmemo_{i}")[:200],
                               source=g(f"vcsource_{i}")[:100], sug=g(f"vcsug_{i}"), due=g(f"vcdue_{i}"))
    return f


def recalc_payments(lib: Lib, f: PaysForm, ch: Dict[str, Any], nf: str) -> List[str]:
    """cp.pl update_payments: open posten per relatie, voorstellen en kenmerken."""
    errors: List[str] = []
    precision = ch["precision"]
    accno_before = f.oldpayment_accno
    recalc_common(lib, f.vc, f, ch, nf, errors)
    f.oldpayment_accno = f.payment_accno
    key = f.selection_key()
    if key != f.key:
        f.inputs = {}
        f.allbox = f.oldallbox = False
    f.key = key
    if f.allbox != f.oldallbox:
        for r in f.inputs.values():
            r.checked = f.allbox
    f.oldallbox = f.allbox
    fmt = lambda v: format_amount(v, nf, precision) if v else ""  # noqa: E731

    def num(text):
        try:
            return _amount(text, nf)
        except ValueError as exc:
            errors.append(lib_message(exc))
            return ZERO

    groups: Dict[int, VcRow] = {}
    for inv in lib.payments.open_invoices(f.vc, **f.query()):
        r = groups.get(inv.vc_id)
        if r is None:
            r = groups[inv.vc_id] = VcRow(vc_id=inv.vc_id, name=inv.name or "", number=inv.vcnumber or "",
                                          invoices=[], amount=ZERO, due=ZERO, discount=ZERO, suggested=ZERO)
        r.invoices.append(inv)
        r.amount += round_amount(inv.amount / inv.exchangerate, precision)
        r.due += inv.due
        r.discount += inv.discount
        r.suggested += inv.paid_suggested
    f.rows = list(groups.values())
    source = None
    for r in f.rows:
        i = f.inputs.get(r.vc_id) or VcIn(checked=f.allbox)
        if i.due and i.due != fmt(r.due) and i.checked:
            f.notices.append(_("De posten van {name} zijn intussen gewijzigd: het bedrag is opnieuw voorgesteld.")
                             .format(name=r.name))
            i.paid = ""
        r.checked = i.checked
        r.memo = i.memo
        if r.checked:
            r.sug = fmt(r.suggested)
            r.paid = r.sug if not i.paid or i.paid == i.sug else i.paid
            if source is None:
                own = i.source if i.source and f.payment_accno == accno_before else ""
                source = own or (lib.payments.next_source(f.vc, f.payment_accno) or "")
            else:
                source = _incr(source)
            r.source = source
            r.rest = round_amount(num(r.paid) - r.suggested, precision) if num(r.paid) > r.suggested else ZERO
    for i_id, i in f.inputs.items():
        if i.checked and i_id not in groups:
            f.notices.append(_("Een aangevinkte relatie heeft geen openstaande posten meer."))
    f.sum_amount = sum((r.amount for r in f.rows), ZERO)
    f.sum_due = sum((r.due for r in f.rows), ZERO)
    f.sum_paid = sum((num(r.paid) for r in f.rows if r.checked), ZERO)
    f.sum_rest = sum((r.rest for r in f.rows), ZERO)
    return list(dict.fromkeys(errors))


class BookError(Exception):
    """Boeken van een relatie in het scherm voor meerdere relaties mislukt (niets geboekt)."""

    def __init__(self, text: str, cause: Exception):
        super().__init__(text)
        self.cause = cause


def book_many(lib: Lib, f: PaysForm, nf: str):
    """Boek de aangevinkte relaties (cp.pl post_payments), alles of niets. Geeft
    (regel, PaymentResult) per relatie terug."""
    rate = _amount(f.exchangerate, nf, "koers") if f.exchangerate else None
    done = []
    current = None
    try:
        with lib.conn.transaction():
            for r in f.rows:
                if not (r.checked and _amount(r.paid, nf)):
                    continue
                current = r
                paid = _amount(r.paid, nf)
                lines = Payments.allocate(r.invoices, paid)
                res = lib.payments.post_payment(Payment(
                    vc=f.vc, vc_id=r.vc_id, datepaid=_date(f.datepaid), payment_accno=f.payment_accno,
                    lines=lines, amount=paid, currency=f.currency or None, exchangerate=rate,
                    source=r.source, memo=r.memo or None,
                    discount_accno=f.discount_accno if any(l.discount for l in lines) else None,
                    arap_accno=f.arap_accno or None, department_id=_int(f.department_id),
                    paymentmethod_id=_int(f.paymentmethod_id)))
                done.append((r, res))
    except (LedgerError, ValueError) as exc:
        text = message(exc) if isinstance(exc, LedgerError) else str(exc)
        raise BookError((f"{current.name}: " if current else "") + text, exc)
    return done


def payments_page(ctx: Ctx, lib: Lib, f: PaysForm, ch, *, status=200, error=None, confirm=None, posted=None,
                  **kw):
    if posted:
        kw["notice"] = None
    return render(ctx, "cp/payments.html", status, f=f, ch=ch, k=KIND[f.vc], error=error, confirm=confirm,
                  posted=posted, nf=ctx.user["numberformat"], detail_params=urlencode(dict(
                      _filter_params(f), rekening=f.payment_accno, datum=f.datepaid, terug="1")),
                  amount=lambda v, dash="": format_amount(v, ctx.user["numberformat"], ch["precision"], dash)
                  .replace("&nbsp;", "").strip(), **kw)


def list_payments(ctx: Ctx, vc: str):
    require_cash(ctx)
    q = ctx.request.query_params
    conn, lib = _lib(ctx)
    nf = ctx.user["numberformat"]
    with conn:
        ch = _choices(lib, vc)
        f = PaysForm(vc=vc, datepaid=q.get("datum") if _date(q.get("datum", "")) else date.today().isoformat())
        f.olddatepaid = f.datepaid
        if q.get("rekening") in {a["accno"] for a in ch["paid_accounts"]}:
            f.payment_accno = f.oldpayment_accno = q["rekening"]
        for k, name in (("van", "duedatefrom"), ("tot", "duedateto")):
            if _date(q.get(k, "")):
                setattr(f, name, q[k])
        for k, name in (("afdeling", "department_id"), ("branche", "business_id"), ("methode", "paymentmethod_id")):
            if _int(q.get(k, "")):
                setattr(f, name, q[k])
        if q.get("valuta") in ch["currencies"]:
            f.currency = f.oldcurrency = q["valuta"]
        if q.get("debiteurenrekening") in {a["accno"] for a in ch["arap_accounts"]}:
            f.arap_accno = q["debiteurenrekening"]
        errors = recalc_payments(lib, f, ch, nf)
        from .routes_payspec import posted_specs
        specs = posted_specs(lib, vc, q.getlist("spec")) if q.get("ok") == "payments_posted" else []
        return payments_page(ctx, lib, f, ch, error=" ".join(errors) or None, posted=_posted_summary(lib, vc, q),
                             specs=specs)


def post_payments(ctx: Ctx, vc: str, form: FormData):
    require_cash(ctx)
    f = paysform_from_post(vc, form)
    action = str(form.get("action", "update"))
    nf = ctx.user["numberformat"]
    conn, lib = _lib(ctx)
    with conn:
        ch = _choices(lib, vc)
        errors = recalc_payments(lib, f, ch, nf)
        if action in ("calc", "update"):
            if action == "calc":
                return payments_page(ctx, lib, f, ch, calc_error=" ".join(errors) or None)
            return payments_page(ctx, lib, f, ch, error=" ".join(errors) or None)
        if action not in ("post", "post_confirmed"):
            raise HTTPException(400, _("Onbekende actie."))
        if errors or f.notices:
            return payments_page(ctx, lib, f, ch, status=400, error=" ".join(errors) or None)
        todo = [r for r in f.rows if r.checked and _amount(r.paid, nf)]
        if not todo:
            return payments_page(ctx, lib, f, ch, status=400, error=_(MESSAGES["Nothing to post!"]))
        if not _date(f.datepaid):
            return payments_page(ctx, lib, f, ch, status=400, error=_(MESSAGES["Date missing!"]))
        if f.sum_rest > 0 and action != "post_confirmed":
            return payments_page(ctx, lib, f, ch, confirm="prepay")
        try:
            done = book_many(lib, f, nf)
        except BookError as exc:
            if isinstance(exc.cause, ChangedError):
                f.notices = []
                recalc_payments(lib, f, ch, nf)
            return payments_page(ctx, lib, f, ch, status=400, error=_("{error} Er is niets geboekt.").format(error=exc))
        for r, res in done:
            ctx.log("payment_posted", module=KIND[vc]["arap"], vc_id=r.vc_id, source=res.source, amount=r.paid,
                    trans_ids=res.trans_ids, prepayment=res.overpayment_id)
        params = dict(_filter_params(f), ok="payments_posted", rekening=f.payment_accno, datum=f.datepaid,
                      aantal=str(len(done)))
        specs = [spec_key(f.payment_accno, res.source, _date(f.datepaid), r.vc_id) for r, res in done]
        raise Redirect(f"/{KIND[vc]['many']}?" + urlencode(params) + "&" + urlencode([("spec", k) for k in specs]))


# ------------------------------------------------------------------ corrigeren

OPTIONS = [("all", N_("Alle")), ("exclude", N_("Zonder gestorneerde")), ("void", N_("Alleen gestorneerde")),
           ("printed", N_("Afgedrukt")), ("notprinted", N_("Niet afgedrukt"))]


def _register_filters(q) -> Dict[str, str]:
    keys = ("rekening", "relatie", "kenmerk_van", "kenmerk_tot", "van", "tot", "weergave")
    out = {k: (q.get(k) or "").strip() for k in keys}
    out["weergave"] = out["weergave"] if out["weergave"] in dict(OPTIONS) else "exclude"
    return out


def _register_rows(lib: Lib, vc: str, flt: Dict[str, str]) -> List[Dict[str, Any]]:
    name = flt["relatie"]
    rows = lib.payments.payment_register(
        vc, payment_accno=flt["rekening"] or None, name=name or None,
        source_from=flt["kenmerk_van"] or None, source_to=flt["kenmerk_tot"] or None,
        date_from=_date(flt["van"]), date_to=_date(flt["tot"]), option=flt["weergave"])
    if name and not rows:     # ook op nummer zoeken
        rows = lib.payments.payment_register(
            vc, payment_accno=flt["rekening"] or None, number=name,
            source_from=flt["kenmerk_van"] or None, source_to=flt["kenmerk_tot"] or None,
            date_from=_date(flt["van"]), date_to=_date(flt["tot"]), option=flt["weergave"])
    for i, r in enumerate(rows, 1):
        r["key"] = "|".join([r["accno"], r["source"] or "", r["datepaid"].isoformat(), str(r["vc_id"])])
        r["can"] = bool(r["source"]) and not r["void"] and r["amount"] != 0
    return rows


def register_page(ctx: Ctx, vc: str, *, status=200, error=None, confirm=None, selected=(), form=None):
    require_cash(ctx)
    q = ctx.request.query_params if form is None else form
    flt = _register_filters(q)
    conn, lib = _lib(ctx)
    with conn:
        ch = _choices(lib, vc)
        searched = form is not None or "weergave" in ctx.request.query_params
        rows = _register_rows(lib, vc, flt) if searched else []
        sel = [r for r in rows if r["key"] in set(selected)]
        new_source = ""
        if confirm == "reissue" and sel:
            new_source = (str(form.get("new_source", "")).strip() if form is not None else "") or \
                (lib.payments.next_source(vc, sel[0]["accno"]) or "")
        return render(ctx, "cp/register.html", status, k=KIND[vc], ch=ch, flt=flt, rows=rows, searched=searched,
                      options=OPTIONS, error=error, confirm=confirm, sel=sel, selected=set(selected),
                      new_source=new_source, voiddate=(form.get("voiddate") if form is not None else "")
                      or date.today().isoformat(), new_date=form.get("new_date", "") if form is not None else "",
                      query=urlencode({k: v for k, v in flt.items() if v}),
                      amount=lambda v, dash="": format_amount(v, ctx.user["numberformat"], ch["precision"], dash)
                      .replace("&nbsp;", "").strip())


def post_register(ctx: Ctx, vc: str, form: FormData):
    require_cash(ctx)
    action = str(form.get("action", ""))
    selected = [str(s) for s in form.getlist("sel")]
    if action == "search":
        flt = _register_filters(form)
        raise Redirect(f"/{KIND[vc]['many']}/corrigeren?" + urlencode(flt))
    if action not in ("void", "void_confirmed", "reissue", "reissue_confirmed"):
        raise HTTPException(400, _("Onbekende actie."))
    if not selected:
        return register_page(ctx, vc, status=400, error=_("Selecteer eerst een of meer betalingen."), form=form)
    if action in ("void", "reissue"):
        return register_page(ctx, vc, confirm=action, selected=selected, form=form)
    conn, lib = _lib(ctx)
    with conn:
        rows = {r["key"]: r for r in _register_rows(lib, vc, _register_filters(form))}
        sel = [rows[k] for k in selected if k in rows and rows[k]["can"]]
        if len(sel) != len(selected):
            conn.rollback()
            return register_page(ctx, vc, status=409, form=form,
                                 error=_("De lijst is intussen gewijzigd; zoek opnieuw en controleer de selectie."))
        try:
            with conn.transaction():
                if action == "void_confirmed":
                    voiddate = _date(str(form.get("voiddate", "")))
                    if not voiddate:
                        raise ValueError(_("Vul de stornodatum in."))
                    for r in sel:
                        lib.payments.void_payment(vc, r["accno"], r["source"], trans_ids=r["trans_ids"],
                                                  datepaid=r["datepaid"], voiddate=voiddate)
                else:
                    src = str(form.get("new_source", "")).strip()
                    if not src:
                        raise ValueError(_(MESSAGES["New reference (source) missing!"]))
                    new_date = _date(str(form.get("new_date", "")))
                    if str(form.get("new_date", "")).strip() and not new_date:
                        raise ValueError(_("Ongeldige datum: {value}").format(value=form.get("new_date")))
                    for r in sel:
                        lib.payments.reissue_payment(vc, r["accno"], r["source"], src, new_date=new_date,
                                                     trans_ids=r["trans_ids"], datepaid=r["datepaid"])
                        src = _incr(src)
        except ValueError as exc:
            return register_page(ctx, vc, status=400, error=lib_message(exc), confirm=action.split("_")[0],
                                 selected=selected, form=form)
        except LedgerError as exc:
            return register_page(ctx, vc, status=400, error=message(exc), confirm=action.split("_")[0],
                                 selected=selected, form=form)
        what = "payment_voided" if action == "void_confirmed" else "payment_reissued"
        for r in sel:
            ctx.log(what, module=KIND[vc]["arap"], accno=r["accno"], source=r["source"],
                    datepaid=r["datepaid"].isoformat(), vc_id=r["vc_id"], trans_ids=r["trans_ids"])
    flt = _register_filters(form)
    raise Redirect(f"/{KIND[vc]['many']}/corrigeren?" + urlencode(dict(flt, ok=what)))


# ------------------------------------------------------------------ routes

def _routes(vc: str) -> None:
    one, many = KIND[vc]["prefix"], KIND[vc]["many"]

    @router.get(f"/{one}/nieuw", name=f"{one}_new")
    def _new(ctx: Ctx = Depends(ctx_company)):
        return new_payment(ctx, vc)

    @router.get(f"/{one}/relaties", name=f"{one}_vc")
    def _vc(ctx: Ctx = Depends(ctx_company)):
        return vc_options(ctx, vc)

    @router.post(f"/{one}", name=f"{one}_post")
    def _post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
        return post_payment(ctx, vc, form)

    @router.get(f"/{many}", name=f"{many}_list")
    def _list(ctx: Ctx = Depends(ctx_company)):
        return list_payments(ctx, vc)

    @router.post(f"/{many}", name=f"{many}_post")
    def _posts(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
        return post_payments(ctx, vc, form)

    @router.get(f"/{many}/corrigeren", name=f"{many}_register")
    def _register(ctx: Ctx = Depends(ctx_company)):
        return register_page(ctx, vc)

    @router.post(f"/{many}/corrigeren", name=f"{many}_register_post")
    def _register_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
        return post_register(ctx, vc, form)


_routes("customer")
_routes("vendor")
