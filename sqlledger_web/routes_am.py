"""Beheer (fase 2, A1–A8): instellingen, rekeningschema, btw, valuta en koersen,
afsluiten, audittrail, locks en de eenvoudige lijsten (afdelingen, projecten,
talen, branches, betaalmethoden).

De schermen volgen am.pl/pe.pl; rekenen en controleren gebeurt in de bibliotheek
(``sqlledger.admin``, ``sqlledger.masterdata``). De functies ``*_fields`` geven de
velden zoals het formulier ze toont en ``*_from_form`` leest ze terug; de
Perl-vergelijking (compare/compare_am.py) gebruikt dezelfde functies.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from starlette.datastructures import FormData

from sqlledger import LedgerError, NotFoundError, _db
from sqlledger.admin import CATEGORIES, IC_DEFAULTS, LINK_ORDER, SETTINGS, Account, Admin, with_new_rate
from sqlledger.dashboard import Dashboard
from sqlledger import newcompany, production
from sqlledger.icp import IcpReturns
from sqlledger.amounts import format_amount, parse_amount
from sqlledger.dates import format_date
from sqlledger.masterdata import MasterData, Project

from . import production as menu_production
from .accounts import ACTION_LABELS
from .companies import connect_company
from .i18n import N_, gettext as _
from .libmsg import lib_message
from .tables import safe_cell
from .web import NOTICES, Ctx, Redirect, csrf_form, ctx_company, render, require_role

router = APIRouter(prefix="/beheer")

NOTICES.update({
    "am_saved": N_("Opgeslagen."),
    "am_deleted": N_("Verwijderd."),
    "am_locks": N_("Locks vrijgegeven."),
})
ACTION_LABELS.update({
    "am_settings_saved": N_("instellingen opgeslagen"),
    "am_account_saved": N_("rekening opgeslagen"),
    "am_account_deleted": N_("rekening verwijderd"),
    "am_gifi_saved": N_("GIFI-code opgeslagen"),
    "am_gifi_deleted": N_("GIFI-code verwijderd"),
    "am_taxes_saved": N_("btw-tarieven opgeslagen"),
    "am_closing_saved": N_("boekperiode/afsluiten gewijzigd"),
    "am_currency_saved": N_("valuta gewijzigd"),
    "am_currency_deleted": N_("valuta verwijderd"),
    "am_default_currency": N_("standaardvaluta gewijzigd"),
    "am_rates_saved": N_("koersen opgeslagen"),
    "am_rates_deleted": N_("koersen verwijderd"),
    "am_list_saved": N_("stamgegevens opgeslagen"),
    "am_list_deleted": N_("stamgegevens verwijderd"),
    "am_locks_released": N_("locks vrijgegeven"),
    "am_company_locked": N_("administratie op slot gezet"),
    "am_company_unlocked": N_("administratie van slot gehaald"),
})

#: koppelingen in groepen zoals am.pl account_header
LINK_GROUPS = [
    (N_("Hoofdrekening voor"), [("AR", N_("Debiteuren")), ("AP", N_("Crediteuren")), ("IC", N_("Voorraad"))]),
    (N_("Verkoop"), [("AR_amount", N_("Omzet")), ("AR_paid", N_("Betaling")), ("AR_discount", N_("Korting")), ("AR_tax", N_("Btw"))]),
    (N_("Inkoop"), [("AP_amount", N_("Kosten")), ("AP_paid", N_("Betaling")), ("AP_discount", N_("Korting")), ("AP_tax", N_("Btw"))]),
    (N_("Artikelen met voorraad"), [("IC_sale", N_("Omzet")), ("IC_cogs", N_("Kostprijs")), ("IC_taxpart", N_("Btw"))]),
    (N_("Diensten/artikelen zonder voorraad"), [("IC_income", N_("Omzet")), ("IC_expense", N_("Kosten")),
                                            ("IC_taxservice", N_("Btw"))]),
]
#: koppeling -> (groep, label); vertaald samengevoegd bij het tonen
LINK_LABELS = {k: (g, label) for g, items in LINK_GROUPS for k, label in items}


# ------------------------------------------------------------------ algemeen

def _open(ctx: Ctx) -> psycopg.Connection:
    try:
        return connect_company(ctx.settings, ctx.company)
    except psycopg.Error:
        raise HTTPException(503, _("De administratie is niet bereikbaar."))


def _admin(ctx: Ctx) -> None:
    require_role(ctx, "admin")


def _module(ctx: Ctx, module: str) -> None:
    if module not in (ctx.company["modules"] or []):
        raise HTTPException(403, _("Deze module staat uit voor deze administratie."))


def _g(form: Mapping, key: str) -> str:
    return str(form.get(key, "") or "")


def _date(text: str) -> Optional[date]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(_("Ongeldige datum: {value}").format(value=text))


def _number(text: str, nf: str) -> Optional[Decimal]:
    text = (text or "").strip()
    if not text:
        return None
    if not all(ch in "0123456789.,' -" for ch in text):
        raise ValueError(_("Ongeldig getal: {value}").format(value=text))
    try:
        return parse_amount(text, nf)
    except (InvalidOperation, ValueError):
        raise ValueError(_("Ongeldig getal: {value}").format(value=text))


def _fmt(value: Any, nf: str) -> str:
    """Getal zonder vaste decimalen (koersen, tarieven) in de notatie van de gebruiker."""
    if value is None:
        return ""
    return format_amount(Decimal(value), nf) or "0"


def _err(exc: Exception) -> str:
    return lib_message(exc)


# ================================================================== instellingen (A3)

def settings_fields(values: Mapping[str, str]) -> Dict[str, str]:
    """Velden van het instellingenscherm, met ``shown_<sleutel>`` = wat getoond werd."""
    out: Dict[str, str] = {}
    for k in SETTINGS:
        out[k] = values.get(k) or ""
        out[f"shown_{k}"] = out[k]
    for k in ("cdt", "namesbynumber") + tuple(k for k in SETTINGS if k.startswith("lock_")):
        out[k] = "1" if out[k] not in ("", "0") else ""
        out[f"shown_{k}"] = out[k]
    return out


def settings_from_form(form: Mapping) -> Tuple[Dict[str, str], Dict[str, str]]:
    # sleutel die niet op het scherm stond (ook geen shown_): ongemoeid laten, bv. referenceurl (VW3)
    values = {k: _g(form, k) for k in SETTINGS if k in form or f"shown_{k}" in form}
    shown = {k: _g(form, f"shown_{k}") for k in SETTINGS if f"shown_{k}" in form}
    return values, shown


@router.get("/instellingen")
def settings_form(ctx: Ctx = Depends(ctx_company)):
    _admin(ctx)
    with _open(ctx) as conn:
        adm = Admin(conn, ctx.user_config())
        return _settings_page(ctx, adm, settings_fields(adm.settings()))


def _settings_page(ctx, adm: Admin, f: Dict[str, str], status: int = 200, **kw):
    current = adm.settings()
    accounts = adm.setting_accounts()
    names = {a["id"]: f"{a['accno']} {a['description'] or ''}".strip()
             for lst in accounts.values() for a in lst}
    ic = {k: names.get(int(current[k]), current[k]) if current[k].isdigit() else current[k] for k in IC_DEFAULTS}
    fx = f.get("fxgainloss_accno_id") or ""
    if fx and fx not in {str(a["id"]) for a in accounts["fxgainloss_accno_id"]}:
        # huidige rekening staat niet in de keuzelijst (bv. geen opbrengst/kosten): wel tonen, niet stil wissen
        accounts["fxgainloss_accno_id"].append({"id": fx, "accno": names.get(int(fx), fx) if fx.isdigit() else fx,
                                                "description": _("(huidige rekening)")})
    kw.setdefault("doc_language", ctx.company.get("doc_language") or "nl")
    kw.setdefault("vat_period", Dashboard(adm.conn, ctx.user_config()).vat_period())
    kw.setdefault("icp_period", IcpReturns(adm.conn).period_kind())
    if "last_yearend" not in kw:                             # JA5: waarschuwing bij wijzigen
        ly = adm.last_yearend()
        kw["last_yearend"] = format_date(ly, ctx.user["dateformat"]) if ly else None
    if "ic" in (ctx.company.get("modules") or []):
        kw.setdefault("show_production", production.setting(adm.conn))
        kw.setdefault("has_assemblies", production.has_assemblies(adm.conn))
    from .routes_system import MONTHS
    return render(ctx, "am/settings.html", status, f=f, accounts=accounts, ic=ic, doc_languages=DOC_LANGUAGES,
                  months=MONTHS,
                  vat_periods=VAT_PERIOD_LABELS, icp_periods=ICP_PERIOD_LABELS, **kw)


#: btw-aangiftetijdvak (dashboard, DB5)
VAT_PERIOD_LABELS = (("quarter", N_("kwartaal")), ("month", N_("maand")), ("year", N_("jaar")),
                     ("none", N_("geen herinnering")))


#: tijdvak ICP-opgaaf (ICP2)
ICP_PERIOD_LABELS = (("quarter", N_("kwartaal")), ("month", N_("maand")))


#: documenttaal per administratie (DS10/DT2)
DOC_LANGUAGES = (("nl", N_("Nederlands")), ("en", N_("Engels")), ("de", N_("Duits")), ("fr", N_("Frans")))


@router.post("/instellingen")
def settings_save(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    _admin(ctx)
    values, shown = settings_from_form(form)
    doc_language = _g(form, "doc_language") or (ctx.company.get("doc_language") or "nl")
    with _open(ctx) as conn:
        adm = Admin(conn, ctx.user_config())
        if doc_language not in dict(DOC_LANGUAGES):
            f = dict(values, **{f"shown_{k}": v for k, v in shown.items()})
            return _settings_page(ctx, adm, f, 400, error=_("Onbekende documenttaal."))
        vat_period = _g(form, "vat_period")
        icp_period = _g(form, "icp_period")
        try:
            changed = adm.save_settings(values, shown)
            if vat_period and Dashboard(conn, ctx.user_config()).save_vat_period(vat_period, ctx.employee_login):
                changed = list(changed) + ["vat_return_period"]
            if icp_period and IcpReturns(conn).save_period(icp_period, ctx.employee_login):
                changed = list(changed) + ["icp_period"]
            shown = _g(form, "shown_show_production")
            if shown and (_g(form, "show_production") == "1") != (shown == "1") \
                    and production.save_setting(conn, _g(form, "show_production") == "1", ctx.employee_login):
                changed = list(changed) + ["show_production"]
                menu_production.forget(ctx.company)
        except LedgerError as exc:
            f = dict(values, **{f"shown_{k}": v for k, v in shown.items()})
            return _settings_page(ctx, adm, f, 400, error=_err(exc), doc_language=doc_language,
                                  vat_period=vat_period, icp_period=icp_period or IcpReturns(conn).period_kind())
        from .routes_vat import sync_flag
        sync_flag(ctx, conn)                    # menu-item Btw-aangifte volgt het tijdvak (BA2)
        if doc_language != (ctx.company.get("doc_language") or "nl"):
            ctx.conn.execute("UPDATE company SET doc_language = %s WHERE id = %s",
                             (doc_language, ctx.company["id"]))
            changed = list(changed) + ["doc_language"]
        if doc_language != newcompany.setting(conn, newcompany.DOC_LANGUAGE_SETTING):
            newcompany.save_setting(conn, newcompany.DOC_LANGUAGE_SETTING, doc_language, ctx.employee_login)
    ctx.log("am_settings_saved", keys=changed)
    raise Redirect("/beheer/instellingen?ok=am_saved")


# ================================================================== rekeningschema (A1)

def account_fields(view) -> Dict[str, str]:
    """Velden van het rekeningscherm voor een AccountView (of None = nieuw)."""
    a = view.account if view else Account(accno="", charttype="A")
    f = {"id": str(a.id or ""), "accno": a.accno, "description": a.description, "category": a.category,
         "charttype": a.charttype or "A", "gifi_accno": a.gifi_accno,
         "contra": "1" if a.contra else "", "closed": "1" if a.closed else "",
         "bankname": a.bankname, "iban": a.iban, "bic": a.bic}
    for k in LINK_ORDER:
        f[f"link_{k}"] = "1" if k in a.link else ""
    return f


def account_from_form(form: Mapping) -> Account:
    rid = _g(form, "id")
    return Account(id=int(rid) if rid.isdigit() else None, accno=_g(form, "accno"),
                   description=_g(form, "description"), category=_g(form, "category"),
                   charttype=_g(form, "charttype") or "A", gifi_accno=_g(form, "gifi_accno"),
                   contra=bool(_g(form, "contra")), closed=bool(_g(form, "closed")),
                   link={k for k in LINK_ORDER if _g(form, f"link_{k}")},
                   bankname=_g(form, "bankname"), iban=_g(form, "iban"), bic=_g(form, "bic"))


def _rgs_codes(conn) -> Dict[int, str]:
    """RGS-code per rekening (nieuwste versie), voor de kolom RGS (RS13)."""
    with _db.cursor(conn) as cur:
        cur.execute("SELECT to_regclass('x_account_rgs') AS t")
        if not cur.fetchone()["t"]:
            return {}
        cur.execute("SELECT DISTINCT ON (chart_id) chart_id, code FROM x_account_rgs "
                    "ORDER BY chart_id, string_to_array(version, '.')::int[] DESC")
        return {r["chart_id"]: r["code"] for r in cur.fetchall()}


@router.get("/rekeningschema")
def accounts_list(ctx: Ctx = Depends(ctx_company)):
    _admin(ctx)
    with _open(ctx) as conn:
        rows = Admin(conn, ctx.user_config()).accounts()
        rgs_codes = _rgs_codes(conn)
    for r in rows:
        r["rgs"] = rgs_codes.get(r["id"], "")
        r["links"] = [f"{_(LINK_LABELS[k][0])}: {_(LINK_LABELS[k][1])}" if k in LINK_LABELS else k
                      for k in (r["link"] or "").split(":") if k]
    return render(ctx, "am/accounts.html", rows=rows, categories=CATEGORIES, show_rgs=bool(rgs_codes))


def _account_page(ctx, f: Dict[str, str], view=None, status: int = 200, **kw):
    title = N_("Rekening wijzigen") if f.get("id") else N_("Rekening toevoegen")
    with _open(ctx) as conn:
        gifi = Admin(conn, ctx.user_config()).gifi_codes()
    # GF4: zoeklijst uit de GIFI-indeling; een code die er niet in staat, mag (dode verwijzing uit SL), met signaal
    code = Admin.clean_gifi(f.get("gifi_accno", ""))[0]
    gifi_unknown = code if code and code not in {g["accno"] for g in gifi} else ""
    return render(ctx, "am/account.html", status, f=f, view=view, title=title, groups=LINK_GROUPS,
                  categories=CATEGORIES, gifi=gifi, gifi_unknown=gifi_unknown, **kw)


@router.get("/rekeningschema/nieuw")
def account_new(ctx: Ctx = Depends(ctx_company)):
    _admin(ctx)
    return _account_page(ctx, account_fields(None))


@router.get("/rekeningschema/{chart_id}")
def account_edit(chart_id: int, ctx: Ctx = Depends(ctx_company)):
    _admin(ctx)
    with _open(ctx) as conn:
        try:
            view = Admin(conn, ctx.user_config()).get_account(chart_id)
        except NotFoundError:
            raise HTTPException(404, _("Rekening niet gevonden."))
    return _account_page(ctx, account_fields(view), view)


@router.post("/rekeningschema")
def account_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    _admin(ctx)
    action = _g(form, "action") or "save"
    acc = account_from_form(form)
    f = {k: _g(form, k) for k in account_fields(None)}
    with _open(ctx) as conn:
        adm = Admin(conn, ctx.user_config())
        view = None
        if acc.id:
            try:
                view = adm.get_account(acc.id)
            except NotFoundError:
                raise HTTPException(404, _("Rekening niet gevonden."))
        try:
            if action in ("delete", "delete_confirmed"):
                if not acc.id:
                    raise HTTPException(400, _("Er is nog niets opgeslagen."))
                if action == "delete":
                    return _account_page(ctx, f, view, confirm="delete")
                adm.delete_account(acc.id)
                ctx.log("am_account_deleted", accno=view.account.accno)
                raise Redirect("/beheer/rekeningschema?ok=am_deleted")
            if action == "save_as_new":
                acc.id = None
            elif action != "save":
                raise HTTPException(400, _("Onbekende actie."))
            new_id = adm.save_account(acc)
        except LedgerError as exc:
            return _account_page(ctx, f, view, 400, error=_err(exc))
    ctx.log("am_account_saved", accno=acc.accno, id=new_id)
    raise Redirect(f"/beheer/rekeningschema/{new_id}?ok=am_saved")


# ================================================================== GIFI-indeling (bouwstap 3, GF1–GF6)

def _gifi_page(ctx, adm: Admin, f: Optional[Dict[str, str]] = None, current=None, status: int = 200, **kw):
    return render(ctx, "am/gifi.html", status, rows=adm.gifi_codes(), orphans=adm.gifi_orphans(), f=f,
                  current=current, **kw)


@router.get("/gifi")
def gifi_list(ctx: Ctx = Depends(ctx_company)):
    _admin(ctx)
    with _open(ctx) as conn:
        return _gifi_page(ctx, Admin(conn, ctx.user_config()))


@router.get("/gifi/nieuw")
def gifi_new(ctx: Ctx = Depends(ctx_company)):
    """Nieuwe code; ``?code=`` vult de code alvast in (vanuit de lijst met onbekende codes)."""
    _admin(ctx)
    code = Admin.clean_gifi(ctx.request.query_params.get("code", ""))[0]
    with _open(ctx) as conn:
        return _gifi_page(ctx, Admin(conn, ctx.user_config()), {"key": "", "accno": code, "description": ""})


@router.get("/gifi/{code:path}")
def gifi_edit(code: str, ctx: Ctx = Depends(ctx_company)):
    _admin(ctx)
    with _open(ctx) as conn:
        adm = Admin(conn, ctx.user_config())
        try:
            row = adm.get_gifi(code)
        except NotFoundError:
            raise HTTPException(404, _("GIFI-code niet gevonden."))
        f = {"key": row["accno"], "accno": row["accno"], "description": row["description"] or ""}
        return _gifi_page(ctx, adm, f, row)


@router.post("/gifi")
def gifi_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    _admin(ctx)
    action = _g(form, "action") or "save"
    f = {k: _g(form, k) for k in ("key", "accno", "description")}
    key = f["key"]
    with _open(ctx) as conn:
        adm = Admin(conn, ctx.user_config())
        current = None
        if key:
            try:
                current = adm.get_gifi(key)
            except NotFoundError:
                raise HTTPException(404, _("GIFI-code niet gevonden."))
        try:
            if action in ("delete", "delete_confirmed"):
                if not key:
                    raise HTTPException(400, _("Er is nog niets opgeslagen."))
                if action == "delete":
                    return _gifi_page(ctx, adm, f, current, confirm="delete")
                adm.delete_gifi(key)
                ctx.log("am_gifi_deleted", code=key)
                raise Redirect("/beheer/gifi?ok=am_deleted")
            if action != "save":
                raise HTTPException(400, _("Onbekende actie."))
            code = adm.save_gifi(f["accno"], f["description"], key or None)
        except LedgerError as exc:
            return _gifi_page(ctx, adm, f, current, 400, error=_err(exc))
    ctx.log("am_gifi_saved", code=code, **({"old": key} if key and key != code else {}))
    raise Redirect("/beheer/gifi?ok=am_saved")


# ================================================================== btw (A2)

def tax_fields(rows: List[Dict[str, Any]], nf: str) -> Dict[str, str]:
    f: Dict[str, str] = {"rowcount": str(len(rows))}
    for i, r in enumerate(rows, 1):
        f[f"chart_id_{i}"] = str(r["chart_id"])
        f[f"rate_{i}"] = _fmt(r["rate"], nf) if r.get("rate") is not None else ""
        f[f"taxnumber_{i}"] = r.get("taxnumber") or ""
        f[f"validto_{i}"] = r["validto"].isoformat() if r.get("validto") else ""
        if "closed" in r:
            f[f"closed_{r['chart_id']}"] = "1" if r["closed"] else ""
    return f


def taxes_from_form(form: Mapping, nf: str) -> Tuple[List[Dict[str, Any]], Dict[int, bool]]:
    n = int(_g(form, "rowcount")) if _g(form, "rowcount").isdigit() else 0
    rows, charts = [], []
    for i in range(1, min(n, 1000) + 1):
        cid = _g(form, f"chart_id_{i}")
        if not cid.isdigit():
            continue
        rows.append({"chart_id": int(cid), "rate": _number(_g(form, f"rate_{i}"), nf),
                     "taxnumber": _g(form, f"taxnumber_{i}"), "validto": _date(_g(form, f"validto_{i}"))})
        charts.append(int(cid))
    closed = {cid: bool(_g(form, f"closed_{cid}")) for cid in dict.fromkeys(charts)}
    # "Nieuw tarief per datum" (BT1): het lopende tarief krijgt de einddatum, het nieuwe komt erbij
    for cid in dict.fromkeys(charts):
        rate, start = _g(form, f"newrate_{cid}"), _g(form, f"newstart_{cid}")
        if not rate and not start:
            continue
        if not rate or not _date(start):
            raise ValueError(_("Vul bij een nieuw tarief zowel het percentage als de ingangsdatum in."))
        rows = with_new_rate(rows, cid, _number(rate, nf), _date(start))
    return rows, closed


def _tax_display(adm: Admin, f: Dict[str, str]) -> List[Dict[str, Any]]:
    """Regels voor het scherm uit de formuliervelden, met rekeninggegevens."""
    info = {}
    for r in adm.taxes():
        info.setdefault(r["chart_id"], r)
    with adm.conn.cursor() as cur:
        cur.execute("SELECT id, accno, description, closed, link FROM chart")
        for cid, accno, desc, closed, link in cur.fetchall():
            info.setdefault(cid, {"chart_id": cid, "accno": accno, "description": desc, "closed": closed,
                                  "has_tax_link": True})
    out, prev = [], None
    n = int(f.get("rowcount") or 0)
    for i in range(1, n + 1):
        cid = int(f[f"chart_id_{i}"])
        r = info.get(cid, {})
        out.append({"i": i, "chart_id": cid, "first": cid != prev, "accno": r.get("accno"),
                    "description": r.get("description"), "has_tax_link": r.get("has_tax_link", True)})
        prev = cid
    return out


@router.get("/btw")
def taxes_form(ctx: Ctx = Depends(ctx_company)):
    _admin(ctx)
    with _open(ctx) as conn:
        adm = Admin(conn, ctx.user_config())
        return _taxes_page(ctx, adm, tax_fields(adm.taxes(), ctx.user["numberformat"]))


def _taxes_page(ctx, adm, f, status=200, **kw):
    return render(ctx, "am/taxes.html", status, f=f, lines=_tax_display(adm, f), **kw)


@router.post("/btw")
def taxes_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    _admin(ctx)
    nf = ctx.user["numberformat"]
    action = _g(form, "action") or "save"
    f = {k: _g(form, k) for k in form.keys() if k not in ("csrf", "action")}
    with _open(ctx) as conn:
        adm = Admin(conn, ctx.user_config())
        if action != "save":
            raise HTTPException(400, _("Onbekende actie."))
        try:
            rows, closed = taxes_from_form(form, nf)
            adm.save_taxes(rows, closed)
        except (LedgerError, ValueError) as exc:
            return _taxes_page(ctx, adm, f, 400, error=_err(exc))
    ctx.log("am_taxes_saved")
    raise Redirect("/beheer/btw?ok=am_saved")


# ================================================================== afsluiten (A4) + slot (A7)

@router.get("/afsluiten")
def closing_form(ctx: Ctx = Depends(ctx_company)):
    _admin(ctx)
    with _open(ctx) as conn:
        c = Admin(conn, ctx.user_config()).closing()
    return _closing_page(ctx, c)


def _closing_page(ctx, c, status=200, **kw):
    lock = ctx.conn.execute("SELECT locked, lock_message FROM company WHERE id = %s",
                            (ctx.company["id"],)).fetchone()
    shown = c["closedto_date"].isoformat() if c.get("closedto_date") else ""
    return render(ctx, "am/closing.html", status, c=c, shown=shown, lock=lock,
                  fmt=lambda d: format_date(d, ctx.user["dateformat"]), **kw)


@router.post("/afsluiten")
def closing_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    _admin(ctx)
    action = _g(form, "action") or "save"
    if action in ("lock", "unlock"):
        locked = action == "lock"
        ctx.conn.execute("UPDATE company SET locked = %s, lock_message = %s WHERE id = %s",
                         (locked, _g(form, "lock_message").strip()[:500] if locked else "", ctx.company["id"]))
        ctx.log("am_company_locked" if locked else "am_company_unlocked")
        raise Redirect("/beheer/afsluiten?ok=am_saved")
    if action != "save":
        raise HTTPException(400, _("Onbekende actie."))
    with _open(ctx) as conn:
        adm = Admin(conn, ctx.user_config())
        c = adm.closing()
        text, shown = _g(form, "closedto").strip(), _g(form, "closedto_shown").strip()
        try:
            if text == shown and "closedto_shown" in form:
                closedto: Any = c["closedto"]   # niet gewijzigd: opgeslagen waarde blijft (ook 20173112)
            else:
                closedto = _date(text)
            adm.save_closing(closedto, bool(_g(form, "revtrans")), bool(_g(form, "audittrail")))
        except (LedgerError, ValueError) as exc:
            return _closing_page(ctx, c, 400, error=_err(exc))
    ctx.log("am_closing_saved", closedto=str(closedto or ""), revtrans=bool(_g(form, "revtrans")),
            audittrail=bool(_g(form, "audittrail")))
    raise Redirect("/beheer/afsluiten?ok=am_saved")


# ================================================================== valuta (A5)

@router.get("/valuta")
def currencies_page(ctx: Ctx = Depends(ctx_company)):
    _admin(ctx)
    with _open(ctx) as conn:
        md = MasterData(conn, ctx.user_config())
        return _currencies(ctx, md)


def _currencies(ctx, md: MasterData, status=200, **kw):
    return render(ctx, "am/currencies.html", status, rows=md.currencies(), **kw)


@router.post("/valuta")
def currencies_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    _admin(ctx)
    action, curr = _g(form, "action"), _g(form, "curr").strip().upper()
    with _open(ctx) as conn:
        md = MasterData(conn, ctx.user_config())
        try:
            if action == "add":
                md.add_currency(curr)       # decimalen alleen-lezen (VW4): SL-standaard 2
            elif action in ("up", "down"):
                md.move_currency(curr, action)
            elif action == "default":
                objections, moving = md.default_change_check(curr)
                return _currencies(ctx, md, confirm="default", curr=curr, objections=objections, moving=moving)
            elif action == "default_confirmed":
                md.set_default_currency(curr)
                ctx.log("am_default_currency", curr=curr)
                raise Redirect("/beheer/valuta?ok=am_saved")
            elif action == "delete":
                return _currencies(ctx, md, confirm="delete", curr=curr)
            elif action == "delete_confirmed":
                md.delete_currency(curr)
                ctx.log("am_currency_deleted", curr=curr)
                raise Redirect("/beheer/valuta?ok=am_deleted")
            else:
                raise HTTPException(400, _("Onbekende actie."))
        except LedgerError as exc:
            return _currencies(ctx, md, 400, error=_err(exc), new_curr=curr if action == "add" else "")
    ctx.log("am_currency_saved", curr=curr, change=action)
    raise Redirect("/beheer/valuta?ok=am_saved")


# ================================================================== koersen (A5)

def rate_fields(transdate: Optional[date], rates: Mapping[str, Any], nf: str) -> Dict[str, str]:
    f = {"transdate": transdate.isoformat() if transdate else "",
         "olddate": transdate.isoformat() if transdate else "", "currencies": ":".join(rates)}
    for c, r in rates.items():
        f[f"rate_{c}"] = _fmt(r, nf) if r else ""
    return f


def rates_from_form(form: Mapping, nf: str) -> Tuple[Optional[date], Dict[str, Optional[Decimal]], Optional[date]]:
    currs = [c for c in _g(form, "currencies").split(":") if c]
    return (_date(_g(form, "transdate")), {c: _number(_g(form, f"rate_{c}"), nf) for c in currs},
            _date(_g(form, "olddate")))


@router.get("/koersen")
def rates_list(ctx: Ctx = Depends(ctx_company)):
    _admin(ctx)
    q = ctx.request.query_params
    flt = {"valuta": q.get("valuta", ""), "van": q.get("van", ""), "tot": q.get("tot", "")}
    with _open(ctx) as conn:
        md = MasterData(conn, ctx.user_config())
        currencies = [c["curr"] for c in md.currencies()]
        try:
            rows = md.exchangerates(flt["valuta"] or None, _date(flt["van"]), _date(flt["tot"]))
            error = None
        except ValueError as exc:
            rows, error = [], str(exc)
    foreign = currencies[1:]
    shown = [flt["valuta"]] if flt["valuta"] in foreign else foreign
    by_date: Dict[date, Dict[str, Any]] = {}
    for r in rows:
        by_date.setdefault(r["transdate"], {})[r["curr"]] = r["exchangerate"]
    table = [(d, [_fmt(v.get(c), ctx.user["numberformat"]) if v.get(c) is not None else "" for c in shown])
             for d, v in sorted(by_date.items(), reverse=True)]
    return render(ctx, "am/rates.html", flt=flt, foreign=foreign, shown=shown, table=table, error=error,
                  default=currencies[0] if currencies else "",
                  fmt=lambda d: format_date(d, ctx.user["dateformat"]))


def _rate_page(ctx, md: MasterData, f, status=200, **kw):
    foreign = [c["curr"] for c in md.currencies()][1:]
    return render(ctx, "am/rate_edit.html", status, f=f, foreign=[c for c in f["currencies"].split(":") if c]
                  or foreign, **kw)


@router.get("/koersen/datum")
def rate_edit(ctx: Ctx = Depends(ctx_company)):
    _admin(ctx)
    try:
        d = _date(ctx.request.query_params.get("datum", ""))
    except ValueError:
        raise HTTPException(400, _("Ongeldige datum."))
    with _open(ctx) as conn:
        md = MasterData(conn, ctx.user_config())
        rates = md.rates_on(d) if d else {c["curr"]: None for c in md.currencies()[1:]}
        if not rates:
            raise HTTPException(400, _("Er zijn geen vreemde valuta; voeg eerst een valuta toe."))
        return _rate_page(ctx, md, rate_fields(d, rates, ctx.user["numberformat"]))


@router.post("/koersen/datum")
def rate_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    _admin(ctx)
    nf = ctx.user["numberformat"]
    action = _g(form, "action") or "save"
    f = {k: _g(form, k) for k in form.keys() if k not in ("csrf", "action")}
    with _open(ctx) as conn:
        md = MasterData(conn, ctx.user_config())
        try:
            transdate, rates, olddate = rates_from_form(form, nf)
            if action == "delete":
                return _rate_page(ctx, md, f, confirm="delete")
            if action == "delete_confirmed":
                d = olddate or transdate
                if not d:
                    raise ValueError(_("Datum ontbreekt."))
                md.save_exchangerates(d, {c: None for c in rates})
                ctx.log("am_rates_deleted", date=d.isoformat())
                raise Redirect("/beheer/koersen?ok=am_deleted")
            if action != "save":
                raise HTTPException(400, _("Onbekende actie."))
            if not transdate:
                raise ValueError(_("Datum ontbreekt."))
            md.save_exchangerates(transdate, rates, olddate)
        except (LedgerError, ValueError) as exc:
            return _rate_page(ctx, md, f, 400, error=_err(exc))
    ctx.log("am_rates_saved", date=transdate.isoformat())
    raise Redirect(f"/beheer/koersen/datum?datum={transdate.isoformat()}&ok=am_saved")


# ================================================================== audittrail en locks (A7)

AUDIT_PAGE = 500


@router.get("/audittrail")
def audit_page(ctx: Ctx = Depends(ctx_company)):
    _admin(ctx)
    q = ctx.request.query_params
    flt = {k: q.get(k, "") for k in ("medewerker", "van", "tot", "actie", "kenmerk", "tabel")}
    page = int(q.get("page", "1")) if q.get("page", "1").isdigit() else 1
    error = None
    with _open(ctx) as conn:
        adm = Admin(conn, ctx.user_config())
        links = adm.audit_actions()
        try:
            kw = dict(employee_id=int(flt["medewerker"]) if flt["medewerker"].isdigit() else None,
                      datefrom=_date(flt["van"]), dateto=_date(flt["tot"]), action=flt["actie"] or None,
                      reference=flt["kenmerk"] or None, tablename=flt["tabel"] or None)
            if q.get("csv"):
                rows = adm.audit_log(**kw, limit=1_000_000)
            else:
                rows = adm.audit_log(**kw, limit=AUDIT_PAGE + 1, offset=(page - 1) * AUDIT_PAGE)
        except ValueError as exc:
            rows, error = [], str(exc)
    if q.get("csv"):
        out = io.StringIO()
        w = csv.writer(out, delimiter=";")
        w.writerow([_("Tijd"), _("Medewerker"), _("Tabel"), _("Kenmerk"), _("Formulier"), _("Actie"), _("Boeking")])
        for r in rows:
            w.writerow([r["transdate"].strftime("%Y-%m-%d %H:%M:%S") if r["transdate"] else "",
                        safe_cell(r["name"] or r["login"] or ""), safe_cell(r["tablename"] or ""),
                        safe_cell(r["reference"] or ""), safe_cell(r["formname"] or ""),
                        safe_cell(r["action"] or ""), r["trans_id"] or ""])
        ctx.log("report_exported", report="audittrail")
        return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="audittrail.csv"'})
    more = len(rows) > AUDIT_PAGE
    return render(ctx, "am/audit.html", rows=rows[:AUDIT_PAGE], flt=flt, links=links, page=page, more=more,
                  error=error)


@router.get("/locks")
def locks_page(ctx: Ctx = Depends(ctx_company)):
    _admin(ctx)
    with _open(ctx) as conn:
        rows = Admin(conn, ctx.user_config()).locks()
    return render(ctx, "am/locks.html", rows=rows, confirm=ctx.request.query_params.get("bevestig") == "1")


@router.post("/locks")
def locks_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    _admin(ctx)
    action = _g(form, "action")
    with _open(ctx) as conn:
        adm = Admin(conn, ctx.user_config())
        if action == "release" and _g(form, "id").isdigit():
            n = adm.release_lock(int(_g(form, "id")), _g(form, "login") or None)
        elif action == "release_all":
            raise Redirect("/beheer/locks?bevestig=1")
        elif action == "release_all_confirmed":
            n = adm.release_all_locks()
        else:
            raise HTTPException(400, _("Onbekende actie."))
    ctx.log("am_locks_released", count=n, id=_g(form, "id") or "alle")
    raise Redirect("/beheer/locks?ok=am_locks")


# ================================================================== eenvoudige lijsten (A6)

@dataclass
class Fld:
    name: str
    label: str
    kind: str = "text"          # text | number | date | radio | select
    options: List[Tuple[str, str]] = field(default_factory=list)
    required: bool = False


@dataclass
class Kind:
    slug: str
    title: str
    singular: str
    role: str
    module: Optional[str]
    key: str                    # 'id' of 'code'
    fields: List[Fld]
    columns: List[Tuple[str, str]]          # (veld, kop) in de lijst
    movable: bool = False
    rows: Callable = None       # (md) -> list
    save: Callable = None       # (md, values, key) -> key
    delete: Callable = None
    move: Callable = None
    add_text: str = ""          # knop/kop "… toevoegen"
    edit_text: str = ""         # kop "… wijzigen"
    delete_text: str = ""       # vraag "… verwijderen?"
    not_found: str = ""         # melding "… niet gevonden."
    extra: List[str] = field(default_factory=list)   # velden met een eigen stukje sjabloon (projecten: soort, v1.1)


def _kinds() -> Dict[str, Kind]:
    return {
        "afdelingen": Kind(
            "afdelingen", N_("Afdelingen"), N_("Afdeling"), "bookkeeper", "projects", "id",
            [Fld("description", N_("Omschrijving"), required=True),
             Fld("role", N_("Soort"), "radio", [("P", N_("Winstplaats")), ("C", N_("Kostenplaats"))])],
            [("description", N_("Omschrijving")), ("role_label", N_("Soort"))], movable=True,
            rows=lambda md: md.departments(),
            save=lambda md, v, k: md.save_department(v["description"], v["role"] or "P", int(k) if k else None),
            delete=lambda md, k: md.delete_department(int(k)),
            move=lambda md, k, d: md.move_department(int(k), d),
            add_text=N_("Afdeling toevoegen"), edit_text=N_("Afdeling wijzigen"),
            delete_text=N_("Afdeling verwijderen?"), not_found=N_("Afdeling niet gevonden.")),
        "projecten": Kind(
            "projecten", N_("Projecten"), N_("Project"), "bookkeeper", "projects", "id",
            [Fld("projectnumber", N_("Nummer")), Fld("description", N_("Omschrijving")),
             Fld("startdate", N_("Startdatum"), "date"), Fld("enddate", N_("Einddatum"), "date"),
             Fld("customer_id", N_("Klant"), "select")],
            [("projectnumber", N_("Nummer")), ("description", N_("Omschrijving")), ("startdate", N_("Start")),
             ("enddate", N_("Einde")), ("customer", N_("Klant")), ("kind_label", N_("Soort")),
             ("department", N_("Kostenplaats"))],
            rows=lambda md: md.projects(),
            save=lambda md, v, k: md.save_project(Project(
                id=int(k) if k else None, projectnumber=v["projectnumber"], description=v["description"],
                startdate=_date(v["startdate"]), enddate=_date(v["enddate"]),
                customer_id=int(v["customer_id"]) if v["customer_id"].isdigit() else None,
                # v1.1 interne uren (IN1/IN2): bij een nieuw project is de soort verplicht ("" = fout "Kies het
                # soort"); een bestaand, niet ingedeeld project mag ongewijzigd opgeslagen worden (None = laten staan)
                kind=v.get("kind") or ("" if not k else None), department_id=int(v["department_id"])
                if str(v.get("department_id", "")).isdigit() else None,
                new_department=v.get("new_department") or "")),
            delete=lambda md, k: md.delete_project(int(k)),
            add_text=N_("Project toevoegen"), edit_text=N_("Project wijzigen"),
            delete_text=N_("Project verwijderen?"), not_found=N_("Project niet gevonden."),
            extra=["kind", "department_id", "new_department"]),
        "talen": Kind(
            "talen", N_("Talen"), N_("Taal"), "admin", None, "code",
            [Fld("code", N_("Code"), required=True), Fld("description", N_("Omschrijving"), required=True)],
            [("code", N_("Code")), ("description", N_("Omschrijving"))],
            rows=lambda md: md.languages(),
            save=lambda md, v, k: md.save_language(v["code"], v["description"], k or None),
            delete=lambda md, k: md.delete_language(k),
            add_text=N_("Taal toevoegen"), edit_text=N_("Taal wijzigen"),
            delete_text=N_("Taal verwijderen?"), not_found=N_("Taal niet gevonden.")),
        "branches": Kind(
            "branches", N_("Branches"), N_("Branche"), "admin", None, "id",
            [Fld("description", N_("Omschrijving"), required=True)],      # korting weg (VW5), blijft in de database
            [("description", N_("Omschrijving"))], movable=True,
            rows=lambda md: md.business(),
            save=lambda md, v, k: md.save_business(v["description"], None, int(k) if k else None),
            delete=lambda md, k: md.delete_business(int(k)),
            move=lambda md, k, d: md.move_business(int(k), d),
            add_text=N_("Branche toevoegen"), edit_text=N_("Branche wijzigen"),
            delete_text=N_("Branche verwijderen?"), not_found=N_("Branche niet gevonden.")),
        "betaalmethoden": Kind(
            "betaalmethoden", N_("Betaalmethoden"), N_("Betaalmethode"), "admin", None, "id",
            [Fld("description", N_("Omschrijving"), required=True),       # kosten weg (VW6): ook in SL zonder effect
             Fld("roundchange", N_("Afronding"), "radio",
                 [("", N_("geen")), ("0.05", "0,05"), ("0.1", "0,10"), ("0.2", "0,20"), ("0.25", "0,25"),
                  ("0.5", "0,50"), ("1", "1,00")])],      # keuzes van am.pl paymentmethod_header
            [("description", N_("Omschrijving")), ("roundchange_text", N_("Afronding"))],
            movable=True,
            rows=lambda md: md.paymentmethods(),
            save=lambda md, v, k: md.save_paymentmethod(v["description"], None, v["roundchange"] or None,
                                                         int(k) if k else None),
            delete=lambda md, k: md.delete_paymentmethod(int(k)),
            move=lambda md, k, d: md.move_paymentmethod(int(k), d),
            add_text=N_("Betaalmethode toevoegen"), edit_text=N_("Betaalmethode wijzigen"),
            delete_text=N_("Betaalmethode verwijderen?"), not_found=N_("Betaalmethode niet gevonden.")),
    }


ROLE_LABELS = {"P": N_("Winstplaats"), "C": N_("Kostenplaats")}
#: soort project (v1.1 interne uren, IN1); "none" = bestaand project zonder klant en zonder keuze
KIND_LABELS = {"customer": N_("Klantproject"), "internal": N_("Intern project"), "none": N_("niet ingedeeld")}


def _kind(ctx: Ctx, slug: str) -> Kind:
    k = _kinds().get(slug)
    if not k:
        raise HTTPException(404, _("Pagina niet gevonden."))
    require_role(ctx, k.role)
    if k.module:
        _module(ctx, k.module)
    return k


def _decorate(rows, nf):
    for r in rows:
        if "role" in r:
            r["role_label"] = _(ROLE_LABELS.get(r["role"], r["role"]))
        if "kind" in r:
            r["kind_label"] = _(KIND_LABELS.get(r["kind"], r["kind"]))
        if "roundchange" in r:
            rc = r["roundchange"]
            r["roundchange_text"] = _fmt(rc, nf) if rc else ""
    return rows


def simple_fields(kind: Kind, row: Optional[Mapping], nf: str) -> Dict[str, str]:
    """Velden van het bewerkscherm (ook voor de Perl-vergelijking)."""
    f = {"key": "" if row is None else str(row[kind.key])}
    for fl in kind.fields:
        v = None if row is None else row.get(fl.name)
        if v is None:
            f[fl.name] = "P" if fl.name == "role" else ""
        elif isinstance(v, date):
            f[fl.name] = v.isoformat()
        elif isinstance(v, Decimal):
            if fl.name == "roundchange":
                f[fl.name] = str(v.normalize()) if v else ""
            else:
                f[fl.name] = _fmt(v, nf) if v else ""
        else:
            f[fl.name] = str(v)
    for name in kind.extra:
        v = None if row is None else row.get(name)
        f[name] = "" if v in (None, "none") else str(v)
    return f


def simple_values(kind: Kind, form: Mapping, nf: str) -> Dict[str, Any]:
    v: Dict[str, Any] = {}
    for fl in kind.fields:
        text = _g(form, fl.name)
        v[fl.name] = _number(text, nf) if fl.kind == "number" else text
    for name in kind.extra:
        v[name] = _g(form, name)
    return v


def _simple_page(ctx, kind: Kind, md: MasterData, f: Optional[Dict[str, str]] = None, status=200, **kw):
    nf = ctx.user["numberformat"]
    rows = _decorate(kind.rows(md), nf)
    if kind.slug == "projecten":
        status_f = ctx.request.query_params.get("status", "")
        if status_f in ("active", "inactive", "orphaned"):
            rows = _decorate(md.projects(status_f), nf)
        kw.setdefault("status_filter", status_f)
    customers = md.customers() if kind.slug == "projecten" and f is not None else []
    if kind.slug == "projecten" and f is not None:
        kw.setdefault("cost_centres", md.cost_centres())
    if customers is not None and f and f.get("customer_id") and \
            f["customer_id"] not in {str(c["id"]) for c in customers}:
        # klant bestaat niet (meer): koppeling zichtbaar houden zodat ongewijzigd opslaan hem niet wist
        customers.append({"id": int(f["customer_id"]), "name": _("(onbekende klant {id})").format(id=f["customer_id"]),
                          "customernumber": None})
    current = None
    if f is not None and f.get("key"):
        current = next((r for r in rows if str(r[kind.key]) == f["key"]), None)
        if current is None and kind.slug == "projecten":
            try:
                current = md.get_project(int(f["key"]))
            except (NotFoundError, ValueError):
                current = None
    return render(ctx, "am/simple.html", status, kind=kind, rows=rows, f=f, current=current,
                  customers=customers, fmt=lambda v: format_date(v, ctx.user["dateformat"]) if isinstance(v, date)
                  else ("" if v is None else v), **kw)


def simple_list(slug: str, ctx: Ctx = Depends(ctx_company)):
    kind = _kind(ctx, slug)
    with _open(ctx) as conn:
        return _simple_page(ctx, kind, MasterData(conn, ctx.user_config()))


def simple_new(slug: str, ctx: Ctx = Depends(ctx_company)):
    kind = _kind(ctx, slug)
    with _open(ctx) as conn:
        return _simple_page(ctx, kind, MasterData(conn, ctx.user_config()),
                            simple_fields(kind, None, ctx.user["numberformat"]))


def simple_edit(slug: str, key: str, ctx: Ctx = Depends(ctx_company)):
    kind = _kind(ctx, slug)
    with _open(ctx) as conn:
        md = MasterData(conn, ctx.user_config())
        rows = kind.rows(md)
        row = next((r for r in rows if str(r[kind.key]) == key), None)
        if row is None:
            raise HTTPException(404, _(kind.not_found))
        return _simple_page(ctx, kind, md, simple_fields(kind, row, ctx.user["numberformat"]))


def simple_post(slug: str, ctx: Ctx, form: FormData):
    kind = _kind(ctx, slug)
    nf = ctx.user["numberformat"]
    action = _g(form, "action") or "save"
    key = _g(form, "key")
    f = {"key": key, **{fl.name: _g(form, fl.name) for fl in kind.fields + [Fld(n, n) for n in kind.extra]}}
    with _open(ctx) as conn:
        md = MasterData(conn, ctx.user_config())
        try:
            if action in ("up", "down") and kind.movable:
                kind.move(md, key, action)
                raise Redirect(f"/beheer/{slug}")
            if action == "delete":
                return _simple_page(ctx, kind, md, f, confirm="delete")
            if action == "delete_confirmed":
                kind.delete(md, key)
                ctx.log("am_list_deleted", list=slug, key=key)
                raise Redirect(f"/beheer/{slug}?ok=am_deleted")
            if action != "save":
                raise HTTPException(400, _("Onbekende actie."))
            new_key = kind.save(md, simple_values(kind, form, nf), key)
        except (LedgerError, ValueError) as exc:
            return _simple_page(ctx, kind, md, f, 400, error=_err(exc))
    ctx.log("am_list_saved", list=slug, key=str(new_key))
    raise Redirect(f"/beheer/{slug}?ok=am_saved")


def _register(slug: str) -> None:
    """Vaste routes per lijst (geen /beheer/{slug}: dat zou routes van andere onderdelen overschaduwen)."""
    def lst(ctx: Ctx = Depends(ctx_company)):
        return simple_list(slug, ctx)

    def new(ctx: Ctx = Depends(ctx_company)):
        return simple_new(slug, ctx)

    def edit(key: str, ctx: Ctx = Depends(ctx_company)):
        return simple_edit(slug, key, ctx)

    def post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
        return simple_post(slug, ctx, form)

    router.add_api_route(f"/{slug}", lst, methods=["GET"])
    router.add_api_route(f"/{slug}/nieuw", new, methods=["GET"])
    router.add_api_route(f"/{slug}/{{key}}", edit, methods=["GET"])
    router.add_api_route(f"/{slug}", post, methods=["POST"])


for _slug in _kinds():
    _register(_slug)


# ================================================================== Run my Accounts-gegevens (RMA_IMPORT_ANALYSE.md RI7)

@router.get("/rma")
def rma_page(ctx: Ctx = Depends(ctx_company)):
    """Wat er bij het inlezen van een Run my Accounts- / ledger123-back-up is omgezet, bewaard en gecontroleerd."""
    _admin(ctx)
    from sqlledger import rmaimport
    from . import rmareport
    with _open(ctx) as conn:
        report = rmaimport.load_report(conn)
    if report is None:
        raise HTTPException(404, _("Deze administratie is niet uit een back-up van Run my Accounts ingelezen."))
    return render(ctx, "am/rma.html", rma=rmareport.view(report, ctx.user))
