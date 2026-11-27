"""Klanten en leveranciers (fase 2, beslispunten K1–K9): zoeken/lijst, toevoegen,
wijzigen, opslaan als nieuw, nieuw nummer, verwijderen (alleen ongebruikt, ook
meervoudig vanuit de lijst) en inactief maken.

Het scherm volgt ct.pl: algemeen, adres, contactpersoon, bank, voorwaarden en
rekeningen met btw, verzendadres, notities en referenties. Opslaan schrijft via
``sqlledger.contacts`` alleen wat gewijzigd is. Knoppen naar een nieuwe
verkoop/inkoop en ontvangst/betaling openen de bestaande schermen met de relatie
voorgevuld (de einddatum blijft staan; Perl wiste die bij elke boekingsknop).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from starlette.datastructures import FormData

from sqlledger import ArapLedger, Contacts, ContactSearch, LedgerError, Relation
from sqlledger.apupload import PurchaseUpload
from sqlledger.amounts import format_amount, parse_amount
from sqlledger.contacts import SHIPTO_FIELDS
from sqlledger.icp import IcpReturns
from sqlledger.vatforms import FORMS
from sqlledger.vatreturn import VatReturns

from .companies import connect_company
from .i18n import N_, gettext as _
from .libmsg import lib_message
from . import routes_attach as attach
from .routes_gl import RefRow, references, refs_from_post
from .routes_icp import CUSTOMER_KIND_LABELS, sync_icp_flag
from .routes_vat import CHOICE_LABELS as VAT_CHOICE_LABELS
from .tables import Col, Row, Table, cell_text, to_csv, to_xlsx
from .reportpdf import pdf_response
from .web import Ctx, Redirect, csrf_form, ctx_company, render, require_role

router = APIRouter()

PAGE_SIZE = 500

KIND = {
    "customer": dict(prefix="klanten", module="ar", label=N_("Klant"), plural=N_("Klanten"), one="klant",
                     arap_label=N_("Debiteurenrekening"), payment_label=N_("Ontvangstrekening"),
                     employee_label=N_("Verkoper"), booking_prefix="verkoop", cash_prefix="ontvangst",
                     booking_label=N_("Nieuwe verkoopboeking"), note_label=N_("Nieuwe creditnota"),
                     cash_label=N_("Ontvangst"), report="verkoop",
                     title_add=N_("Klant toevoegen"), title_edit=N_("Klant wijzigen"),
                     search_label=N_("Klanten zoeken"), number_label=N_("Klantnummer"),
                     not_found=N_("Klant niet gevonden."), delete_question=N_("Klant {name} verwijderen?"),
                     delete_title=N_("Klanten verwijderen")),
    "vendor": dict(prefix="leveranciers", module="ap", label=N_("Leverancier"), plural=N_("Leveranciers"),
                   one="leverancier", arap_label=N_("Crediteurenrekening"), payment_label=N_("Betaalrekening"),
                   employee_label=N_("Medewerker"), booking_prefix="inkoop", cash_prefix="betaling",
                   booking_label=N_("Nieuwe inkoopboeking"), note_label=N_("Nieuwe creditnota"),
                   cash_label=N_("Betaling"), report="inkoop",
                   title_add=N_("Leverancier toevoegen"), title_edit=N_("Leverancier wijzigen"),
                   search_label=N_("Leveranciers zoeken"), number_label=N_("Leveranciernummer"),
                   not_found=N_("Leverancier niet gevonden."), delete_question=N_("Leverancier {name} verwijderen?"),
                   delete_title=N_("Leveranciers verwijderen")),
}



#: velden van het formulier en hun soort (zelfde namen als in sqlledger.contacts.Relation)
TEXT = ("number", "name", "address1", "address2", "city", "state", "zipcode", "country", "salutation",
        "firstname", "lastname", "contacttitle", "occupation", "phone", "fax", "mobile", "email", "cc", "bcc",
        "taxnumber", "sic_code", "gifi_accno", "curr", "language_code", "arap_accno", "payment_accno",
        "discount_accno", "prepayment_accno", "bankname", "iban", "bic", "membernumber", "clearingnumber",
        "bankaddress1", "bankaddress2", "bankcity", "bankstate", "bankzipcode", "bankcountry") + SHIPTO_FIELDS
INTS = ("terms", "discountterms")
PCTS = ("discount", "cashdiscount")
AMOUNTS = ("creditlimit", "threshold")
IDS = ("business_id", "employee_id", "paymentmethod_id")
DATES = ("startdate", "enddate")
BOOLS = ("taxincluded", "remittancevoucher")


class CtForm:
    """Formulierwaarden als tekst (zoals de browser ze stuurt)."""

    def __init__(self, vc: str, **values: Any):
        self.__dict__.update(vc=vc, id=None, typeofcontact="company", gender="", notes="", taxaccounts=[],
                             refs=[], number_preview="", taxincluded=False, remittancevoucher=False)
        for k in TEXT + INTS + PCTS + AMOUNTS + IDS + DATES:
            self.__dict__.setdefault(k, "")
        self.__dict__.update(values)

    def __getattr__(self, name: str) -> Any:        # onbekend veld in het sjabloon: leeg
        if name.startswith("__"):
            raise AttributeError(name)
        return ""


def _num_text(v, nf: str, places=None) -> str:
    return format_amount(v, nf, places).replace("&nbsp;", "").strip() if v else ""


def form_from_relation(r: Relation, nf: str, precision: int) -> CtForm:
    f = CtForm(r.vc, id=r.id, typeofcontact=r.typeofcontact or "company", gender=r.gender or "",
               notes=r.notes or "", taxaccounts=list(r.taxaccounts), taxincluded=bool(r.taxincluded),
               remittancevoucher=bool(r.remittancevoucher))
    for k in TEXT:
        setattr(f, k, (getattr(r, k) or "").strip() if k == "curr" else (getattr(r, k) or ""))
    for k in INTS:
        setattr(f, k, str(getattr(r, k) or 0) if getattr(r, k) else "")
    for k in PCTS:
        setattr(f, k, _num_text(getattr(r, k), nf))
    for k in AMOUNTS:
        setattr(f, k, _num_text(getattr(r, k), nf, precision))
    for k in IDS:
        setattr(f, k, str(getattr(r, k) or ""))
    for k in DATES:
        setattr(f, k, getattr(r, k).isoformat() if getattr(r, k) else "")
    f.refs = [RefRow(description=x.description or "", code=x.code or "", archive_id=str(x.archive_id or ""),
                     folder=x.folder or "", confidential=bool(x.confidential)) for x in r.references]
    return f


def form_from_post(vc: str, form: FormData) -> CtForm:
    def g(k: str, strip: bool = True) -> str:
        v = str(form.get(k, ""))
        return v.strip() if strip else v
    f = CtForm(vc, id=int(g("id")) if g("id").isdigit() else None, typeofcontact=g("typeofcontact") or "company",
               gender=g("gender"), notes=g("notes", strip=False),
               taxaccounts=[str(x) for x in form.getlist("tax")], taxincluded=g("taxincluded") == "1",
               remittancevoucher=g("remittancevoucher") == "1", number_preview=g("number_preview"))
    for k in TEXT + INTS + PCTS + AMOUNTS + IDS + DATES:
        setattr(f, k, g(k)[:500])
    f.expense_accno = g("expense_accno")
    f.vat_box = g("vat_box") if any(g("vat_box") in fm.customer_choices for fm in FORMS.values()) else ""
    f.icp_kind = g("icp_kind") if g("icp_kind") in ("services", "abc") else ""
    f.refs = [x for x in refs_from_post(form) if not x.token]
    return f


def to_relation(f: CtForm, base: Relation, nf: str) -> Relation:
    """Formulier over de opgeslagen relatie heen leggen (kolommen die het scherm niet
    kent, zoals de prijsgroep, blijven zo). Fouten in de invoer: ValueError."""
    r = Relation(**{k: getattr(base, k) for k in base.__dataclass_fields__})
    r.typeofcontact = f.typeofcontact if f.typeofcontact in ("company", "person") else "company"
    r.gender = f.gender if f.gender in ("M", "F") else ""
    r.notes = f.notes
    for k in TEXT:
        setattr(r, k, getattr(f, k))
    for k in INTS:
        v = getattr(f, k)
        if v and not re.fullmatch(r"-?\d+", v):
            raise ValueError(_("Ongeldig aantal dagen: {value}").format(value=v))
        setattr(r, k, int(v or 0))
    for k in PCTS + AMOUNTS:
        v = getattr(f, k)
        if v and not re.fullmatch(r"[0-9.,' -]+", v):
            raise ValueError(_("Ongeldig getal: {value}").format(value=v))
        try:
            setattr(r, k, parse_amount(v, nf) if v else Decimal(0))
        except (InvalidOperation, ValueError):
            raise ValueError(_("Ongeldig getal: {value}").format(value=v))
    for k in IDS:
        v = getattr(f, k)
        setattr(r, k, int(v) if v.isdigit() and int(v) else None)
    for k in DATES:
        v = getattr(f, k)
        try:
            setattr(r, k, date.fromisoformat(v) if v else None)
        except ValueError:
            raise ValueError(_("Ongeldige datum: {value}").format(value=v))
    r.taxincluded, r.remittancevoucher = f.taxincluded, f.remittancevoucher
    r.taxaccounts = list(f.taxaccounts)
    r.references = references(f.refs)
    return r


# ------------------------------------------------------------------ hulp

def require_module(ctx: Ctx, vc: str) -> None:
    if KIND[vc]["module"] not in (ctx.company["modules"] or []):
        raise HTTPException(403, _("Deze module staat uit voor deze administratie."))


def _lib(ctx: Ctx):
    try:
        conn = connect_company(ctx.settings, ctx.company)
    except psycopg.Error:
        raise HTTPException(503, _("De administratie is niet bereikbaar."))
    return conn, Contacts(conn, ctx.user_config())


def _title(f: CtForm) -> str:
    k = KIND[f.vc]
    return k["title_edit"] if f.id else k["title_add"]


def _page(ctx: Ctx, lib: Contacts, f: CtForm, *, status: int = 200, error: Optional[str] = None,
          confirm: Optional[str] = None, rel: Optional[Relation] = None, **kw):
    """Bewerkscherm. rel: de opgeslagen relatie (voor in gebruik / actief)."""
    if f.id and rel is None:
        try:
            rel = lib.get(f.vc, f.id)
        except LedgerError:
            rel = None
    ch = lib.choices(f.vc, include=rel)
    # rekeningen/btw die op het formulier staan maar niet (meer) in de lijst: toch tonen
    for key, fld in (("arap", "arap_accno"), ("payment", "payment_accno"), ("discount", "discount_accno"),
                     ("arap", "prepayment_accno")):
        v = getattr(f, fld)
        if v and v not in {a["accno"] for a in ch[key]}:
            ch[key] = ch[key] + [{"accno": v, "description": ""}]
    have = {a["accno"] for a in ch["tax"]}
    ch["tax"] = ch["tax"] + [{"accno": a, "description": ""} for a in f.taxaccounts if a not in have]
    if f.employee_id and f.employee_id not in {str(e["id"]) for e in ch["employees"]}:
        ch["employees"] = ch["employees"] + [{"id": int(f.employee_id), "name": f.employee_id}]
    if not f.refs or f.refs[-1].description or f.refs[-1].is_file:
        f.refs = list(f.refs) + [RefRow()]
    modules = ctx.company["modules"] or []
    k = KIND[f.vc]
    links = []
    if rel and rel.id:
        q = urlencode({"run": "1", "vc_id": rel.id, "period": "", "fromdate": "", "todate": ""})
        links.append((f"/rapporten/overzicht-{k['report']}?{q}", N_("Boekingen")))
        links.append((f"/rapporten/openstaand-{k['report']}?{q}", N_("Openstaand")))
    actions = []
    if rel and rel.id and ctx.has_role("bookkeeper"):
        actions.append((f"/{k['booking_prefix']}/nieuw?relatie={rel.id}", k["booking_label"]))
        actions.append((f"/{k['booking_prefix']}/nieuw?creditnota=1&relatie={rel.id}", k["note_label"]))
        if "cash" in modules:
            actions.append((f"/{k['cash_prefix']}/nieuw?relatie={rel.id}", k["cash_label"]))
    readonly = not ctx.has_role("bookkeeper")
    if f.vc == "vendor":            # vaste kostenrekening voor Inkoopfactuur uploaden (stap 4e, IU3)
        ch["expense"] = _expense_accounts(lib, f.expense_accno)
    if f.vc == "customer":          # rubriek voor omzet zonder btw (btw-aangifte, BA4)
        vr = VatReturns(lib.conn)
        form = vr.form
        ch["vat_boxes"] = form.customer_choices if (f.vat_box or vr.configured()) else ()
        ch["vat_default"] = form.default_untaxed
        vat_labels = VAT_CHOICE_LABELS[form.country]
        # ICP-soort (ICP5/ICP6): naast de btw-rubriek, zodat 3B en de soort in één keer in te stellen zijn
        ch["icp_kinds"] = CUSTOMER_KIND_LABELS if (f.icp_kind or (ch["vat_boxes"] and form.country == "NL")) else ()
    else:
        vat_labels = VAT_CHOICE_LABELS["NL"]
    attachments = attach.saved_attachments(ctx, lib.conn, f.id)
    return render(ctx, "ct/form.html", status, f=f, ch=ch, k=k, rel=rel, error=error, confirm=confirm,
                  attachments=attachments, can_edit=ctx.has_role("bookkeeper"),
                  title=_title(f), readonly=readonly, dis=" disabled" if readonly else "", links=links,
                  actions=actions, vat_box_labels=vat_labels,
                  nf=ctx.user["numberformat"], today=date.today(), **kw)


def _expense_accounts(lib: Contacts, current: str) -> List[Dict[str, Any]]:
    rows = ArapLedger(lib.conn, lib.config).accounts("vendor")["AP_amount"]
    if current and current not in {a["accno"] for a in rows}:
        rows = rows + [{"accno": current, "description": ""}]
    return rows


def _load_expense(lib: Contacts, f: CtForm) -> None:
    if f.vc == "vendor" and f.id:
        f.expense_accno = PurchaseUpload(lib.conn, lib.config).default_expense(f.id) or ""
    if f.vc == "customer" and f.id:
        f.vat_box = VatReturns(lib.conn).customer_box(f.id)
        f.icp_kind = IcpReturns(lib.conn).customer_kind(f.id)


# ------------------------------------------------------------------ bewerkscherm

def new_form(ctx: Ctx, vc: str):
    require_module(ctx, vc)
    require_role(ctx, "bookkeeper")
    conn, lib = _lib(ctx)
    with conn:
        f = form_from_relation(lib.new(vc), ctx.user["numberformat"], lib.choices(vc)["precision"])
        if ctx.request.query_params.get("soort") == "persoon":
            f.typeofcontact = "person"
        return _page(ctx, lib, f)


def edit_form(ctx: Ctx, vc: str, vc_id: int):
    require_module(ctx, vc)
    conn, lib = _lib(ctx)
    with conn:
        try:
            rel = lib.get(vc, vc_id)
        except LedgerError:
            raise HTTPException(404, _(KIND[vc]["not_found"]))
        f = form_from_relation(rel, ctx.user["numberformat"], lib.choices(vc)["precision"])
        _load_expense(lib, f)
        return _page(ctx, lib, f, rel=rel)


def base_relation(lib: Contacts, f: CtForm) -> Relation:
    return lib.get(f.vc, f.id) if f.id else lib.new(f.vc)


def do_save(lib: Contacts, f: CtForm, nf: str, as_new: bool = False) -> int:
    """Opslaan zoals het scherm: formulier over de opgeslagen relatie; als nieuw: zonder id,
    en met een nieuw nummer als het nummer vergrendeld of ongewijzigd is (geen dubbel nummer)."""
    base = base_relation(lib, f)
    r = to_relation(f, base, nf)
    reserved = f.number_preview or None
    if as_new:
        if lib.choices(f.vc)["lock_number"] or (f.id and r.number.strip() == (base.number or "").strip()):
            r.number = ""
            reserved = None
        r.id = None
    return lib.save(r, reserved_number=reserved)


def post_form(ctx: Ctx, vc: str, form: FormData):
    require_module(ctx, vc)
    require_role(ctx, "bookkeeper")
    f = form_from_post(vc, form)
    action = str(form.get("action", "update"))
    nf = ctx.user["numberformat"]
    k = KIND[vc]
    conn, lib = _lib(ctx)
    with conn:
        if f.id:
            try:
                lib.get(vc, f.id)
            except LedgerError:
                raise HTTPException(404, _(k["not_found"]))
        if action == "update":
            return _page(ctx, lib, f)
        if action == "new_number":
            try:
                r = to_relation(f, base_relation(lib, f), nf)
            except ValueError as exc:
                return _page(ctx, lib, f, status=400, error=lib_message(exc))
            f.number = f.number_preview = lib.next_number(vc, r, peek=True)
            return _page(ctx, lib, f, number_note=True)
        if action in ("delete", "delete_confirmed", "inactive"):
            if not f.id:
                return _page(ctx, lib, f, status=400, error=_("Er is nog niets opgeslagen."))
            if action == "delete":
                return _page(ctx, lib, f, confirm="delete")
            try:
                if action == "inactive":
                    lib.set_inactive(vc, f.id)
                    ctx.log("ct_inactive", id=f.id, vc=vc)
                    raise Redirect(f"/{k['prefix']}/{f.id}?ok=ct_inactive")
                name = lib.get(vc, f.id).name
                lib.delete(vc, f.id)
            except LedgerError as exc:
                return _page(ctx, lib, f, status=400, error=lib_message(exc))
            ctx.log("ct_deleted", id=f.id, vc=vc, name=name)
            raise Redirect(f"/{k['prefix']}?ok=ct_deleted")
        if action not in ("save", "save_as_new"):
            raise HTTPException(400, _("Onbekende actie."))
        if action == "save_as_new" and not f.id:
            action = "save"
        new = action == "save_as_new" or not f.id
        if action == "save_as_new":
            f.refs = [x for x in f.refs if not x.archive_id]      # bijlagen blijven bij het origineel (RB2)
        if vc == "vendor" and f.expense_accno and \
                f.expense_accno not in {a["accno"] for a in ArapLedger(conn, lib.config).accounts("vendor")["AP_amount"]}:
            return _page(ctx, lib, f, status=400,
                         error=lib_message("Kies als vaste kostenrekening een rekening voor inkoopregels."))
        try:
            # EU-klant (3B) of EU-leverancier (4B): zonder geldig btw-nummer geen ICP-opgaaf / verlegging
            IcpReturns(conn).require_vat_number(vc, f.taxnumber, f.vat_box, f.taxaccounts)
            vc_id = do_save(lib, f, nf, as_new=action == "save_as_new")
            pu = PurchaseUpload(conn, lib.config)
            if vc == "vendor" and f.expense_accno != (pu.default_expense(vc_id) or ""):
                pu.set_default_expense(vc_id, f.expense_accno)
            if vc == "customer":
                VatReturns(conn).save_customer_box(vc_id, f.vat_box, ctx.employee_login)
                IcpReturns(conn).save_customer_kind(vc_id, f.icp_kind, ctx.employee_login)
                sync_icp_flag(ctx, conn)
        except ValueError as exc:
            return _page(ctx, lib, f, status=400, error=lib_message(exc))
        except LedgerError as exc:
            return _page(ctx, lib, f, status=400, error=lib_message(exc))
        ctx.log("ct_saved", id=vc_id, vc=vc, new=new)
        raise Redirect(f"/{k['prefix']}/{vc_id}?ok=ct_saved")


# ------------------------------------------------------------------ lijst

#: kolommen van de lijst (ct.pl include_in_report): sleutel, label, soort
COLUMNS = [
    ("number", N_("Nummer"), "text"), ("name", N_("Naam"), "text"), ("typeofcontact", N_("Soort"), "text"),
    ("address", N_("Adres"), "text"), ("city", N_("Plaats"), "text"), ("state", N_("Provincie"), "text"),
    ("zipcode", N_("Postcode"), "text"), ("country", N_("Land"), "text"), ("salutation", N_("Aanhef"), "text"),
    ("contact", N_("Contactpersoon"), "text"), ("contacttitle", N_("Titel"), "text"), ("gender", N_("Geslacht"), "text"),
    ("occupation", N_("Functie"), "text"), ("phone", N_("Telefoon"), "text"), ("fax", N_("Fax"), "text"),
    ("mobile", N_("Mobiel"), "text"), ("email", N_("E-mail"), "text"), ("cc", N_("Cc"), "text"), ("bcc", N_("Bcc"), "text"),
    ("notes", N_("Notities"), "text"), ("discount", N_("Korting %"), "pct"), ("termstext", N_("Termijn"), "text"),
    ("threshold", N_("Drempel"), "amount"), ("creditlimit", N_("Kredietlimiet"), "amount"),
    ("outstanding", N_("Openstaand"), "amount"), ("availablecredit", N_("Beschikbaar krediet"), "amount"),
    ("arap_accno", N_("Deb./cred.rekening"), "text"), ("payment_accno", N_("Betaalrekening"), "text"),
    ("discount_accno", N_("Kortingsrekening"), "text"), ("prepayment_accno", N_("Vooruitbetaling"), "text"),
    ("taxaccounts", N_("Btw-rekeningen"), "text"), ("paymentmethod", N_("Betaalmethode"), "text"),
    ("taxnumber", N_("Btw-nummer"), "text"), ("gifi_accno", N_("GIFI"), "text"), ("sic_code", N_("SIC"), "text"),
    ("employee", N_("Medewerker"), "text"), ("business", N_("Branche"), "text"), ("pricegroup", N_("Prijsgroep"), "text"),
    ("language", N_("Taal"), "text"), ("curr", N_("Valuta"), "text"), ("bankname", N_("Bank"), "text"),
    ("bankaddress", N_("Adres bank"), "text"), ("bankcity", N_("Plaats bank"), "text"),
    ("bankstate", N_("Provincie bank"), "text"), ("bankzipcode", N_("Postcode bank"), "text"),
    ("bankcountry", N_("Land bank"), "text"), ("iban", N_("IBAN"), "text"), ("bic", N_("BIC"), "text"),
    ("membernumber", N_("Lidnummer"), "text"), ("clearingnumber", N_("Clearingnummer"), "text"),
    ("remittancevoucher", N_("Betaalbewijs"), "bool"), ("startdate", N_("Startdatum"), "date"),
    ("enddate", N_("Einddatum"), "date"),
]
DEFAULT_COLUMNS = ["number", "name", "contact", "city", "phone", "email"]
SORTS = [("name", N_("Naam")), ("number", N_("Nummer")), ("city", N_("Plaats")), ("zipcode", N_("Postcode")),
         ("country", N_("Land")), ("contact", N_("Contactpersoon")), ("email", N_("E-mail")), ("phone", N_("Telefoon")),
         ("business", N_("Branche")), ("employee", N_("Medewerker")), ("startdate", N_("Startdatum")), ("enddate", N_("Einddatum"))]
STATUSES = [("all", N_("Alle")), ("active", N_("Actief")), ("inactive", N_("Inactief")), ("orphaned", N_("Ongebruikt"))]
SEARCH_TEXT = ("name", "number", "contact", "email", "phone", "notes", "address", "city", "state", "zipcode")


def _columns(vc: str):
    return [c for c in COLUMNS if not (c[0] == "gifi_accno" and vc != "vendor")
            and not (c[0] == "pricegroup" and vc != "customer")]


def _search_values(q) -> Dict[str, Any]:
    v: Dict[str, Any] = {k: (q.get(k) or "").strip()[:200] for k in SEARCH_TEXT}
    v["country"] = (q.get("country") or "").strip()[:100]
    for k in ("employee_id", "business_id"):
        v[k] = (q.get(k) or "") if (q.get(k) or "").isdigit() else ""
    for k in ("startdatefrom", "startdateto"):
        try:
            v[k] = date.fromisoformat(q.get(k)).isoformat() if q.get(k) else ""
        except ValueError:
            v[k] = ""
    v["status"] = q.get("status") if q.get("status") in dict(STATUSES) else "all"
    v["sort"] = q.get("sort") if q.get("sort") in dict(SORTS) else "name"
    v["direction"] = "DESC" if q.get("direction") == "DESC" else "ASC"
    cols = [c for c in q.getlist("cols") if c]
    v["cols"] = cols if q.get("run") == "1" and cols else list(DEFAULT_COLUMNS)
    return v


def _search(v: Dict[str, Any]) -> ContactSearch:
    return ContactSearch(**{k: v[k] or None for k in SEARCH_TEXT}, country=v["country"] or None,
                         employee_id=int(v["employee_id"]) if v["employee_id"] else None,
                         business_id=int(v["business_id"]) if v["business_id"] else None,
                         startdatefrom=date.fromisoformat(v["startdatefrom"]) if v["startdatefrom"] else None,
                         startdateto=date.fromisoformat(v["startdateto"]) if v["startdateto"] else None,
                         status=v["status"], sort=v["sort"], direction=v["direction"])


def _table(vc: str, rows: List[Dict[str, Any]], cols: List[str], precision: int) -> Table:
    k = KIND[vc]
    columns = []
    for key, label, kind in _columns(vc):
        if key not in cols:
            continue
        if kind == "amount":
            columns.append(Col(key, _(label), "amount", precision))
        elif kind == "pct":
            columns.append(Col(key, _(label), "amount", 2))
        else:
            columns.append(Col(key, _(label), kind if kind == "date" else "text"))
    out = []
    for r in rows:
        cells = dict(r)
        cells["contact"] = " ".join(x for x in (r.get("firstname"), r.get("lastname")) if x) or r.get("contact")
        cells["typeofcontact"] = _("persoon") if r.get("typeofcontact") == "person" else _("bedrijf")
        cells["address"] = " ".join(x for x in (r.get("address1"), r.get("address2")) if x)
        cells["bankaddress"] = " ".join(x for x in (r.get("bankaddress1"), r.get("bankaddress2")) if x)
        cells["remittancevoucher"] = bool(r.get("remittancevoucher"))
        for key in ("discount", "threshold", "creditlimit"):
            if not cells.get(key):
                cells[key] = None
        out.append(Row("row", cells, {"name": f"/{k['prefix']}/{r['id']}", "number": f"/{k['prefix']}/{r['id']}"}))
    return Table(columns, out)


def list_page(ctx: Ctx, vc: str, notice_names: Optional[List[str]] = None):
    require_module(ctx, vc)
    q = ctx.request.query_params
    v = _search_values(q)
    conn, lib = _lib(ctx)
    with conn:
        rows = lib.search(vc, _search(v))
        ch = lib.choices(vc)
        countries = lib.countries(vc)
    table = _table(vc, rows, v["cols"], ch["precision"])
    nf, df = ctx.user["numberformat"], ctx.user["dateformat"]
    export = q.get("export")
    if export == "pdf":
        return pdf_response(ctx, [table], _(KIND[vc]["plural"]), [], KIND[vc]["prefix"])
    if export in ("csv", "xlsx"):
        ctx.log("report_exported", report=KIND[vc]["prefix"], format=export)
        if export == "csv":
            return Response(to_csv([table], nf, df), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{KIND[vc]["prefix"]}.csv"'})
        return Response(to_xlsx([table], _(KIND[vc]["plural"]), [ctx.company["name"]], nf, df),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{KIND[vc]["prefix"]}.xlsx"'})
    p = q.get("page", "1")
    page = int(p) if p.isdigit() else 1
    paged, pages = table.page(page, PAGE_SIZE)
    page = min(max(page, 1), pages)
    in_use = {r["id"]: r["in_use"] for r in rows}
    export_query = urlencode([(a, b) for a, b in q.multi_items() if a not in ("export", "page", "ok")]
                             or [("run", "1")])
    return render(ctx, "ct/list.html", k=KIND[vc], vc=vc, v=v, table=paged, count=len(rows), page=page,
                  pages=pages, columns=_columns(vc), sorts=SORTS, statuses=STATUSES, ch=ch, countries=countries,
                  in_use=in_use, can_edit=ctx.has_role("bookkeeper"), export_query=export_query,
                  cell=lambda c, r: cell_text(c, r, nf, df))


def delete_many(ctx: Ctx, vc: str, form: FormData):
    """Meerdere ongebruikte relaties verwijderen (ct.pl delete_customers/yes__delete):
    eerst bevestigen met de namen, dan alles of niets."""
    require_module(ctx, vc)
    require_role(ctx, "bookkeeper")
    k = KIND[vc]
    ids = sorted({int(x) for x in form.getlist("ids") if str(x).isdigit()})
    back = str(form.get("back", ""))
    back = back if back.startswith(f"/{k['prefix']}") and "//" not in back else f"/{k['prefix']}"
    conn, lib = _lib(ctx)
    with conn:
        if not ids:
            return render(ctx, "ct/delete_many.html", 400, k=k, names=[], ids=[], back=back,
                          error=_("Niets geselecteerd."))
        if form.get("confirmed") != "1":
            names = []
            for i in ids:
                try:
                    names.append(lib.get(vc, i).name)
                except LedgerError:
                    pass
            return render(ctx, "ct/delete_many.html", k=k, names=names, ids=ids, back=back)
        try:
            names = lib.delete_many(vc, ids)
        except LedgerError as exc:
            return render(ctx, "ct/delete_many.html", 400, k=k, names=[], ids=ids, back=back, error=lib_message(exc))
    ctx.log("ct_deleted", vc=vc, ids=ids, names=names)
    sep = "&" if "?" in back else "?"
    raise Redirect(f"{back}{sep}ok=ct_deleted_many")


def _routes(vc: str) -> None:
    prefix = KIND[vc]["prefix"]

    @router.get(f"/{prefix}", name=f"{prefix}_list")
    def _list(ctx: Ctx = Depends(ctx_company)):
        return list_page(ctx, vc)

    @router.get(f"/{prefix}/nieuw", name=f"{prefix}_new")
    def _new(ctx: Ctx = Depends(ctx_company)):
        return new_form(ctx, vc)

    @router.get(f"/{prefix}/{{vc_id}}", name=f"{prefix}_edit")
    def _edit(vc_id: int, ctx: Ctx = Depends(ctx_company)):
        return edit_form(ctx, vc, vc_id)

    @router.post(f"/{prefix}", name=f"{prefix}_post")
    def _post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
        return post_form(ctx, vc, form)

    @router.post(f"/{prefix}/verwijderen", name=f"{prefix}_delete_many")
    def _delete_many(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
        return delete_many(ctx, vc, form)


_routes("customer")
_routes("vendor")
