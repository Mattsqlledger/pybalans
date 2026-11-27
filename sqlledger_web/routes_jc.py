"""Uren > Tijdkaarten (UREN_ANALYSE.md U-1): lijst en formulier, port van jc.pl (type timecard, project).

Formulierregels zoals jc.pl ``update`` (``sqlledger.timecards.form_update``): gewijzigde tijden = aantal = geklokt −
niet te declareren; een andere dienst = prijs (en omschrijving, als je die niet zelf had ingevuld) van de dienst.
"Opslaan" rekent eerst opnieuw, zodat één klik genoeg is. Bekijken: iedereen met de module; wijzigen: boekhouder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal
from typing import Any, Dict, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from starlette.datastructures import FormData

from sqlledger.amounts import format_amount, parse_amount, to_decimal
from sqlledger.errors import LedgerError, NotFoundError
from sqlledger.timecards import TimeCard, TimeCards, TimeCardSearch, clocked, form_update

from .companies import connect_company
from .i18n import N_, gettext as _
from .libmsg import lib_message
from .web import Ctx, Redirect, csrf_form, ctx_company, render, require_role

router = APIRouter()

NOTICES = {"saved": N_("Tijdkaart opgeslagen."), "deleted": N_("Tijdkaart verwijderd.")}
#: bron van het vastgelegde interne tarief (v1.1 interne uren, IN3)
COST_SOURCES = {"assignment": N_("intern tarief van de toewijzing"), "employee": N_("intern uurtarief van de medewerker"),
                "service": N_("kostprijs van de dienst")}
#: Engelse SL-meldingen uit sqlledger.timecards
MESSAGES = {"Date missing!": N_("Vul de datum in."), "Project Number missing!": N_("Kies een project."),
            "Service Code missing!": N_("Kies een dienst."),
            "Cannot save time card for a closed period!": N_("Deze datum valt in een afgesloten periode.")}


def _err(exc: Exception) -> str:
    text = str(exc)
    return _(MESSAGES[text]) if text in MESSAGES else lib_message(exc)


def require_jc(ctx: Ctx, role: str = "readonly") -> None:
    if "projects" not in (ctx.company["modules"] or []):
        raise HTTPException(403, _("Deze module staat uit voor deze administratie."))
    require_role(ctx, role)


def _lib(ctx: Ctx):
    try:
        conn = connect_company(ctx.settings, ctx.company)
    except psycopg.Error:
        raise HTTPException(503, _("De administratie is niet bereikbaar."))
    return conn, TimeCards(conn, ctx.user_config())


@dataclass
class CardForm:
    id: Optional[int] = None
    transdate: str = ""
    project_id: str = ""
    employee_id: str = ""
    parts_id: str = ""
    description: str = ""
    checkedin: str = ""
    checkedout: str = ""
    noncharge: str = ""
    qty: str = ""
    sellprice: str = ""
    notes: str = ""
    allocated: Decimal = Decimal(0)
    # vorige waarden (voor de regels van jc.pl update)
    old_in: str = ""
    old_out: str = ""
    old_qty: str = ""
    old_parts_id: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


def _time(text: str) -> Optional[time]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        h, m = (text.split(":") + ["0"])[:2]
        return time(int(h), int(m))
    except ValueError:
        raise ValueError(_("Ongeldige tijd: {value}").format(value=text)) from None


def _num(text: str, nf: str) -> Decimal:
    return parse_amount(text, nf) if (text or "").strip() else Decimal(0)


def _from_card(tc: TimeCard, nf: str, places: int) -> CardForm:
    def a(v):
        return format_amount(v, nf, places) if v else ""
    c = clocked(tc.checkedin, tc.checkedout, places)
    f = CardForm(id=tc.id, transdate=tc.transdate.isoformat() if tc.transdate else "",
                 project_id=str(tc.project_id or ""), employee_id=str(tc.employee_id or ""),
                 parts_id=str(tc.parts_id or ""), description=tc.description,
                 checkedin=tc.checkedin.strftime("%H:%M") if tc.checkedin else "",
                 checkedout=tc.checkedout.strftime("%H:%M") if tc.checkedout else "",
                 noncharge=a(c - tc.qty) if c is not None and c != tc.qty else "", qty=a(tc.qty),
                 sellprice=format_amount(tc.sellprice, nf, places), notes=tc.notes, allocated=tc.allocated)
    f.old_in, f.old_out, f.old_qty, f.old_parts_id = f.checkedin, f.checkedout, f.qty, f.parts_id
    return f


def _from_form(form: FormData) -> CardForm:
    g = lambda k: str(form.get(k, "")).strip()  # noqa: E731
    f = CardForm(**{k: g(k) for k in ("transdate", "project_id", "employee_id", "parts_id", "description",
                                        "checkedin", "checkedout", "noncharge", "qty", "sellprice", "notes",
                                        "old_in", "old_out", "old_qty", "old_parts_id")})
    f.id = int(g("id")) if g("id").isdigit() else None
    return f


def _to_card(ctx: Ctx, lib: TimeCards, f: CardForm, links: Dict[str, Any]) -> TimeCard:
    """Formulier → tijdkaart met de regels van jc.pl update (aantal uit de tijden, prijs/omschrijving van de dienst)."""
    nf = ctx.user["numberformat"]
    places = links["precision"]
    try:
        transdate = date.fromisoformat(f.transdate) if f.transdate else None
    except ValueError:
        raise ValueError(_("Ongeldige datum: {value}").format(value=f.transdate)) from None
    tc = TimeCard(id=f.id, transdate=transdate, project_id=int(f.project_id) if f.project_id.isdigit() else None,
                  employee_id=int(f.employee_id) if f.employee_id.isdigit() else None,
                  parts_id=int(f.parts_id) if f.parts_id.isdigit() else None, description=f.description,
                  checkedin=_time(f.checkedin), checkedout=_time(f.checkedout), notes=f.notes,
                  sellprice=_num(f.sellprice, nf), qty=_num(f.old_qty, nf))
    services = {str(s["id"]): s for s in links["services"]}
    if f.parts_id and f.parts_id != f.old_parts_id and f.parts_id in services:
        s = services[f.parts_id]
        old = services.get(f.old_parts_id)
        tc.sellprice = s["sellprice"]
        if not f.description or (old and f.description == old["description"]):
            tc.description = s["description"]
    form_update(tc, old_in=_time(f.old_in), old_out=_time(f.old_out), old_qty=_num(f.old_qty, nf),
                qty_given=_num(f.qty, nf) if f.qty else None, noncharge=_num(f.noncharge, nf), places=places)
    return tc


def _page(ctx: Ctx, lib: TimeCards, f: CardForm, status: int = 200, error: Optional[str] = None,
          confirm: Optional[str] = None):
    transdate = None
    try:
        transdate = date.fromisoformat(f.transdate) if f.transdate else None
    except ValueError:
        pass
    links = lib.links(transdate, int(f.project_id) if f.project_id.isdigit() else None,
                      int(f.employee_id) if f.employee_id.isdigit() else None)
    nf = ctx.user["numberformat"]
    c = None
    try:
        c = clocked(_time(f.checkedin), _time(f.checkedout), links["precision"])
    except ValueError:
        pass
    amount = _num(f.qty, nf) * _num(f.sellprice, nf) if f.qty and f.sellprice else None
    can_edit = ctx.has_role("bookkeeper")
    costprice = costsource = ""
    if f.id:                                     # vastgelegd intern tarief (IN3), alleen lezen
        try:
            saved = lib.get(f.id)
        except NotFoundError:
            saved = None
        if saved and saved.costprice is not None:
            costprice = format_amount(saved.costprice, nf, links["precision"], "0")
            costsource = _(COST_SOURCES.get(saved.costsource, saved.costsource))
    return render(ctx, "jc/form.html", status, f=f, links=links, error=error, confirm=confirm,
                  costprice=costprice, costsource=costsource,
                  clocked=format_amount(c, nf, links["precision"]) if c is not None else "",
                  amount=format_amount(amount, nf, links["precision"]) if amount is not None else "",
                  allocated=format_amount(f.allocated, nf, links["precision"]) if f.allocated else "",
                  can_edit=can_edit, notice=_(NOTICES[ctx.request.query_params.get("ok", "")])
                  if ctx.request.query_params.get("ok") in NOTICES else None)


@router.get("/uren/tijdkaart/nieuw", name="jc_new")
def jc_new(ctx: Ctx = Depends(ctx_company)):
    require_jc(ctx, "bookkeeper")
    conn, lib = _lib(ctx)
    with conn:
        f = CardForm(transdate=date.today().isoformat())
        q = ctx.request.query_params
        f.project_id = q.get("project", "")
        return _page(ctx, lib, f)


@router.get("/uren/tijdkaart/{card_id}", name="jc_edit")
def jc_edit(card_id: int, ctx: Ctx = Depends(ctx_company)):
    require_jc(ctx)
    conn, lib = _lib(ctx)
    with conn:
        try:
            tc = lib.get(card_id)
        except NotFoundError:
            raise HTTPException(404, _("Tijdkaart niet gevonden."))
        places = lib.links()["precision"]
        return _page(ctx, lib, _from_card(tc, ctx.user["numberformat"], places))


@router.post("/uren/tijdkaart", name="jc_post")
def jc_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    require_jc(ctx, "bookkeeper")
    conn, lib = _lib(ctx)
    action = form.get("action")
    f = _from_form(form)
    with conn:
        if f.id:
            try:
                f.allocated = lib.get(f.id).allocated
            except NotFoundError:
                raise HTTPException(404, _("Tijdkaart niet gevonden."))
        if action == "delete":
            return _page(ctx, lib, f, confirm="delete")
        if action == "delete_confirmed" and f.id:
            try:
                lib.delete(f.id)
            except LedgerError as exc:
                return _page(ctx, lib, f, 400, error=_err(exc))
            ctx.log("timecard_deleted", id=f.id)
            raise Redirect("/uren/tijdkaarten?ok=deleted")
        transdate = None
        try:
            transdate = date.fromisoformat(f.transdate) if f.transdate else None
        except ValueError:
            pass
        links = lib.links(transdate)
        try:
            tc = _to_card(ctx, lib, f, links)
        except ValueError as exc:
            return _page(ctx, lib, f, 400, error=str(exc))
        if action != "save":
            g = _from_card(tc, ctx.user["numberformat"], links["precision"])
            g.allocated = f.allocated
            g.old_in, g.old_out, g.old_qty, g.old_parts_id = (g.checkedin, g.checkedout, g.qty, g.parts_id)
            return _page(ctx, lib, g)
        try:
            card_id = lib.save(tc)
        except LedgerError as exc:
            return _page(ctx, lib, f, 400, error=_err(exc))
        ctx.log("timecard_saved", id=card_id)
        raise Redirect(f"/uren/tijdkaart/{card_id}?ok=saved")


@router.get("/uren/tijdkaarten", name="jc_list")
def jc_list(ctx: Ctx = Depends(ctx_company)):
    require_jc(ctx)
    q = ctx.request.query_params
    conn, lib = _lib(ctx)
    with conn:
        def d(k):
            try:
                return date.fromisoformat(q.get(k)) if q.get(k) else None
            except ValueError:
                return None
        status = q.get("status", "all")
        s = TimeCardSearch(project_id=int(q["project"]) if q.get("project", "").isdigit() else None,
                           employee_id=int(q["employee"]) if q.get("employee", "").isdigit() else None,
                           datefrom=d("van"), dateto=d("tot"), description=q.get("omschrijving", ""),
                           open=status in ("all", "open"), closed=status in ("all", "closed"))
        rows = lib.search(s)
        links = lib.links()
        total_qty = sum((r["qty"] for r in rows), Decimal(0))
        total_amount = sum((r["amount"] for r in rows), Decimal(0))
        return render(ctx, "jc/list.html", rows=rows, links=links, q=q, status=status, total_qty=total_qty,
                      total_amount=total_amount, places=links["precision"], can_edit=ctx.has_role("bookkeeper"),
                      notice=_(NOTICES[q.get("ok", "")]) if q.get("ok") in NOTICES else None)


# ------------------------------------------------------------------ verkooporders uit uren (U-2, pe.pl)

def _so_filters(q) -> Dict[str, Any]:
    def d(k):
        try:
            return date.fromisoformat(q.get(k)) if q.get(k) else None
        except ValueError:
            return None
    return dict(project_id=int(q["project"]) if str(q.get("project", "")).isdigit() else None,
                employee_id=int(q["employee"]) if str(q.get("employee", "")).isdigit() else None,
                datefrom=d("van"), dateto=d("tot"), summary=q.get("samenvatten") == "1")


def _so_page(ctx: Ctx, conn, lib: TimeCards, q, status: int = 200, error: Optional[str] = None,
             orders: Optional[list] = None, chosen: Optional[set] = None):
    from sqlledger.timecards import open_items
    items = open_items(conn, **_so_filters(q))
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, customernumber FROM customer WHERE enddate IS NULL OR enddate > now() "
                    "ORDER BY name")
        customers = [dict(id=r[0], name=r[1], number=r[2]) for r in cur.fetchall()]
    links = lib.links()
    return render(ctx, "jc/orders.html", status, items=items, q=q, links=links, customers=customers, error=error,
                  orders=orders or [], chosen=chosen, places=links["precision"], can_edit=ctx.has_role("bookkeeper"))


@router.get("/uren/verkooporders", name="jc_orders")
def jc_orders(ctx: Ctx = Depends(ctx_company)):
    require_jc(ctx, "bookkeeper")   # zoals het menu-item (F36)
    if "oe" not in (ctx.company["modules"] or []):
        raise HTTPException(403, _("Deze module staat uit voor deze administratie."))
    conn, lib = _lib(ctx)
    with conn:
        return _so_page(ctx, conn, lib, ctx.request.query_params)


@router.post("/uren/verkooporders", name="jc_orders_post")
def jc_orders_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    require_jc(ctx, "bookkeeper")
    if "oe" not in (ctx.company["modules"] or []):
        raise HTTPException(403, _("Deze module staat uit voor deze administratie."))
    from sqlledger.timecards import generate_sales_orders, open_items
    conn, lib = _lib(ctx)
    q = {k: str(form.get(k, "")) for k in ("project", "employee", "van", "tot", "samenvatten")}
    chosen = {int(v) for v in form.getlist("row") if str(v).isdigit()}
    with conn:
        items = open_items(conn, **_so_filters(q))
        if not chosen:
            return _so_page(ctx, conn, lib, q, 400, error=_("Vink minstens één regel aan."), chosen=chosen)
        picked, customers = [], {}
        for n, it in enumerate(items, 1):
            if n in chosen:
                c = str(form.get(f"customer_{n}", ""))
                if not it.customer_id and c.isdigit():
                    customers[len(picked)] = int(c)
                picked.append(it)
        try:
            ids = generate_sales_orders(conn, ctx.user_config(), picked, customers)
        except LedgerError as exc:
            msg = {"Customer missing!": _("Kies een klant bij elke regel van een project zonder klant."),
                   "Nothing to generate!": _("Er is niets om op een order te zetten.")}.get(str(exc))
            return _so_page(ctx, conn, lib, q, 400, error=msg or _err(exc), chosen=chosen)
        with conn.cursor() as cur:
            cur.execute("SELECT o.id, o.ordnumber, c.name, o.amount FROM oe o JOIN customer c ON c.id = o.customer_id "
                        "WHERE o.id = ANY(%s) ORDER BY o.id", (ids,))
            orders = [dict(id=r[0], ordnumber=r[1], name=r[2], amount=to_decimal(r[3])) for r in cur.fetchall()]
        ctx.log("timecard_orders", orders=ids)
        return _so_page(ctx, conn, lib, q, orders=orders)


# ------------------------------------------------------------------ maandstaat en toewijzingen (U-3)

def _month(text: str) -> tuple:
    try:
        y, m = (int(x) for x in (text or "").split("-")[:2])
        if 1 <= m <= 12 and 1900 < y < 3000:
            return y, m
    except ValueError:
        pass
    t = date.today()
    return t.year, t.month


def _own_employee(conn, ctx: Ctx) -> Optional[int]:
    from sqlledger import _db
    with _db.cursor(conn) as cur:
        _, eid = _db.get_employee(cur, ctx.user_config().short_login)
    return eid or None


def _sheet_page(ctx: Ctx, conn, employee_id: Optional[int], year: int, month: int, own: bool, status: int = 200,
                error: Optional[str] = None, notice: Optional[str] = None, posted: Optional[Dict] = None):
    from sqlledger.timecards import month_days, month_sheet
    lib = TimeCards(conn, ctx.user_config())
    links = lib.links(include_employee=employee_id)
    rows = month_sheet(conn, ctx.user_config(), employee_id, year, month) if employee_id else []
    days = month_days(year, month)
    nf = ctx.user["numberformat"]
    places = links["precision"]

    def cell(row, d):
        if posted is not None and (row.project_id, row.parts_id, d) in posted:
            return posted[(row.project_id, row.parts_id, d)]
        v = row.hours.get(d)
        return format_amount(v, nf, places) if v else ""
    totals = {d.day: sum((r.hours.get(d.day, Decimal(0)) for r in rows), Decimal(0)) for d in days}
    prev = date(year - (month == 1), 12 if month == 1 else month - 1, 1)
    nxt = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return render(ctx, "jc/sheet.html", status, rows=rows, days=days, cell=cell, totals=totals, links=links,
                  employee_id=employee_id, own=own, year=year, month=month, error=error, notice=notice,
                  month_value=f"{year:04d}-{month:02d}", prev=prev.strftime("%Y-%m"), next=nxt.strftime("%Y-%m"),
                  places=places, total=sum(totals.values(), Decimal(0)),
                  action="/uren/mijn" if own else "/uren/maandstaat")


def _save_sheet(ctx: Ctx, conn, employee_id: int, year: int, month: int, form: FormData, own: bool):
    from sqlledger.timecards import save_month
    nf = ctx.user["numberformat"]
    cells, posted = {}, {}
    for k, v in form.multi_items():
        m = __import__("re").fullmatch(r"h_(\d+)_(\d+)_(\d+)", k)
        if not m:
            continue
        key = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        posted[key] = str(v)
        try:
            cells[key] = _num(str(v), nf)
        except Exception:  # noqa: BLE001 - ongeldig getal
            return _sheet_page(ctx, conn, employee_id, year, month, own, 400, posted=posted,
                               error=_("Ongeldig aantal uren: {value}").format(value=v))
    try:
        n = save_month(conn, ctx.user_config(), employee_id, year, month, cells)
    except LedgerError as exc:
        return _sheet_page(ctx, conn, employee_id, year, month, own, 400, posted=posted, error=_err(exc))
    ctx.log("timesheet_saved", employee_id=employee_id, month=f"{year}-{month:02d}", changes=n)
    return _sheet_page(ctx, conn, employee_id, year, month, own,
                       notice=_("Maandstaat opgeslagen ({n} wijzigingen).").format(n=n))


@router.get("/uren/mijn", name="jc_mine")
def jc_mine(ctx: Ctx = Depends(ctx_company)):
    require_jc(ctx, "hours")
    year, month = _month(ctx.request.query_params.get("maand", ""))
    conn, _lib_ = _lib(ctx)
    with conn:
        eid = _own_employee(conn, ctx)
        return _sheet_page(ctx, conn, eid, year, month, own=True,
                           error=None if eid else _("Uw login is niet aan een medewerker gekoppeld."))


@router.post("/uren/mijn", name="jc_mine_post")
def jc_mine_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    require_jc(ctx, "hours")
    year, month = _month(str(form.get("maand", "")))
    conn, _lib_ = _lib(ctx)
    with conn:
        eid = _own_employee(conn, ctx)          # nooit uit het formulier (UR4)
        if not eid:
            raise HTTPException(403, _("Uw login is niet aan een medewerker gekoppeld."))
        return _save_sheet(ctx, conn, eid, year, month, form, own=True)


@router.get("/uren/maandstaat", name="jc_sheet")
def jc_sheet(ctx: Ctx = Depends(ctx_company)):
    require_jc(ctx, "bookkeeper")
    q = ctx.request.query_params
    year, month = _month(q.get("maand", ""))
    conn, _lib_ = _lib(ctx)
    with conn:
        eid = int(q["medewerker"]) if q.get("medewerker", "").isdigit() else None
        return _sheet_page(ctx, conn, eid, year, month, own=False)


@router.post("/uren/maandstaat", name="jc_sheet_post")
def jc_sheet_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    require_jc(ctx, "bookkeeper")
    year, month = _month(str(form.get("maand", "")))
    eid = str(form.get("medewerker", ""))
    if not eid.isdigit():
        raise HTTPException(400, _("Kies een medewerker."))
    conn, _lib_ = _lib(ctx)
    with conn:
        return _save_sheet(ctx, conn, int(eid), year, month, form, own=False)


def _assignment_page(ctx: Ctx, conn, f: Optional[Dict[str, Any]] = None, status: int = 200,
                     error: Optional[str] = None, notice: Optional[str] = None):
    from sqlledger.timecards import Assignments
    lib = TimeCards(conn, ctx.user_config())
    rows = Assignments(conn, ctx.user_config()).list()
    return render(ctx, "jc/assignments.html", status, rows=rows, f=f, links=lib.links(), error=error, notice=notice)


@router.get("/uren/toewijzingen", name="jc_assignments")
def jc_assignments(ctx: Ctx = Depends(ctx_company)):
    require_jc(ctx, "bookkeeper")
    conn, _lib_ = _lib(ctx)
    with conn:
        q = ctx.request.query_params
        f = None
        if q.get("nieuw"):
            f = {"id": "", "employee_id": "", "project_id": "", "parts_id": "", "validfrom": date.today().isoformat(),
                 "validto": "", "sellprice": "", "projectleader_id": "", "notes": "", "internalrate": ""}
        elif q.get("id", "").isdigit():
            from sqlledger.timecards import Assignments
            try:
                a = Assignments(conn, ctx.user_config()).get(int(q["id"]))
            except NotFoundError:
                raise HTTPException(404)
            nf = ctx.user["numberformat"]
            f = {"id": str(a.id), "employee_id": str(a.employee_id), "project_id": str(a.project_id),
                 "parts_id": str(a.parts_id), "validfrom": a.validfrom.isoformat(),
                 "validto": a.validto.isoformat() if a.validto else "",
                 "sellprice": format_amount(a.sellprice, nf, 2) if a.sellprice is not None else "",
                 "projectleader_id": str(a.projectleader_id or ""), "notes": a.notes,
                 "internalrate": format_amount(a.internalrate, nf, 2, "0") if a.internalrate is not None else ""}
        return _assignment_page(ctx, conn, f, notice=_("Toewijzing opgeslagen.") if q.get("ok") == "saved" else None)


@router.post("/uren/toewijzingen", name="jc_assignments_post")
def jc_assignments_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    require_jc(ctx, "bookkeeper")
    from sqlledger.timecards import Assignment, Assignments
    f = {k: str(form.get(k, "")).strip() for k in ("id", "employee_id", "project_id", "parts_id", "validfrom",
                                                     "validto", "sellprice", "projectleader_id", "notes",
                                                     "internalrate")}
    conn, _lib_ = _lib(ctx)
    with conn:
        try:
            def d(k):
                return date.fromisoformat(f[k]) if f[k] else None
            a = Assignment(employee_id=int(f["employee_id"] or 0), project_id=int(f["project_id"] or 0),
                           parts_id=int(f["parts_id"] or 0), validfrom=d("validfrom"), validto=d("validto"),
                           sellprice=_num(f["sellprice"], ctx.user["numberformat"]) if f["sellprice"] else None,
                           projectleader_id=int(f["projectleader_id"]) if f["projectleader_id"] else None,
                           notes=f["notes"], id=int(f["id"]) if f["id"] else None,
                           internalrate=_num(f["internalrate"], ctx.user["numberformat"])
                           if f["internalrate"] else None)
            Assignments(conn, ctx.user_config()).save(a, ctx.employee_login)
        except ValueError:
            return _assignment_page(ctx, conn, f, 400, error=_("Controleer de datums en de prijs."))
        except LedgerError as exc:
            return _assignment_page(ctx, conn, f, 400, error=_err(exc))
    ctx.log("assignment_saved", employee_id=f["employee_id"], project_id=f["project_id"])
    raise Redirect("/uren/toewijzingen?ok=saved")


# ------------------------------------------------------------------ urenstaat als PDF (U-4, UR3)

@router.get("/uren/urenstaat.pdf", name="jc_timesheet_pdf")
def jc_timesheet_pdf(ctx: Ctx = Depends(ctx_company)):
    """Urenstaat per medewerker en maand (eventueel één project) via de documentengine (huisstijl). Een urenschrijver
    krijgt altijd zijn eigen staat; de boekhouder kiest de medewerker."""
    from fastapi.responses import Response
    from sqlledger import _db
    from sqlledger.timecards import timesheet_rows
    from .routes_docs import renderer, safe_filename
    require_jc(ctx, "hours")
    q = ctx.request.query_params
    year, month = _month(q.get("maand", ""))
    conn, _lib_ = _lib(ctx)
    with conn:
        if ctx.has_role("bookkeeper") and q.get("medewerker", "").isdigit():
            eid = int(q["medewerker"])
        else:
            eid = _own_employee(conn, ctx)
        if not eid:
            raise HTTPException(403, _("Uw login is niet aan een medewerker gekoppeld."))
        project_id = int(q["project"]) if q.get("project", "").isdigit() else None
        rows = timesheet_rows(conn, eid, year, month, project_id)
        nf = ctx.user["numberformat"]
        with _db.cursor(conn) as cur:
            places = _db.get_precision(cur)
            cur.execute("SELECT name FROM employee WHERE id = %s", (eid,))
            employee = (cur.fetchone() or {}).get("name") or ""
            v: Dict[str, Any] = {k: val or "" for k, val in _db.get_defaults(
                cur, "company", "address", "tel", "fax", "companyemail", "companywebsite", "businessnumber").items()}
            if project_id:
                cur.execute("""SELECT pr.projectnumber, pr.description, pr.customer_id FROM project pr
                               WHERE pr.id = %s""", (project_id,))
                pr = cur.fetchone() or {}
                v["projectnumber"], v["projectdescription"] = pr.get("projectnumber") or "", pr.get("description") or ""
                if pr.get("customer_id"):
                    from sqlledger.printdata import PrintData
                    pd = PrintData(conn, ctx.user_config(), doc_language=ctx.company.get("doc_language") or "nl")
                    v.update({k: val for k, val in pd.company_details(cur, "customer", pr["customer_id"], None).items()
                              if k in ("name", "address1", "address2", "city", "state", "zipcode", "country",
                                       "contact")})
        total = sum((r["qty"] for r in rows), Decimal(0))
        from sqlledger.dates import format_date
        v.update(employee=employee, period=f"{month:02d}-{year}", total=format_amount(total, nf, places),
                 rows=[dict(transdate=format_date(r["transdate"], ctx.user["dateformat"]),
                            projectnumber=r["projectnumber"], partnumber=r["partnumber"],
                            description=r["description"] or "", notes=r["notes"] or "",
                            qty=format_amount(r["qty"], nf, places)) for r in rows])
        pdf = renderer(ctx, conn).render_pdf("timesheet", v)
    ctx.log("timesheet_printed", employee_id=eid, month=f"{year}-{month:02d}", project_id=project_id)
    name = safe_filename(f"urenstaat-{employee}-{year}-{month:02d}") + ".pdf"
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"'})


# ------------------------------------------------------------------ v1.1 interne uren: intern uurtarief (IN3)

def _rates_page(ctx: Ctx, conn, f: Optional[Dict[str, str]] = None, status: int = 200, error: Optional[str] = None,
                notice: Optional[str] = None):
    from sqlledger.timecards import InternalRates
    lib = TimeCards(conn, ctx.user_config())
    rates = InternalRates(conn, ctx.user_config())
    rows = rates.list()
    today = date.today()
    current = {}
    for r in rows:                               # lijst per medewerker met de nieuwste datum eerst
        if r["validfrom"] <= today and r["employee_id"] not in current:
            current[r["employee_id"]] = r["id"]
    return render(ctx, "jc/rates.html", status, rows=rows, current=set(current.values()), links=lib.links(),
                  f=f or {"employee_id": "", "validfrom": today.isoformat(), "rate": "", "notes": ""},
                  error=error, notice=notice, can_edit=ctx.has_role("bookkeeper"))


@router.get("/uren/tarieven", name="jc_rates")
def jc_rates(ctx: Ctx = Depends(ctx_company)):
    require_jc(ctx)
    conn, _lib_ = _lib(ctx)
    q = ctx.request.query_params
    notice = {"saved": _("Intern tarief opgeslagen."), "deleted": _("Intern tarief verwijderd.")}.get(q.get("ok", ""))
    with conn:
        f = None
        if q.get("medewerker", "").isdigit():
            f = {"employee_id": q["medewerker"], "validfrom": date.today().isoformat(), "rate": "", "notes": ""}
        return _rates_page(ctx, conn, f, notice=notice)


@router.post("/uren/tarieven", name="jc_rates_post")
def jc_rates_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    require_jc(ctx, "bookkeeper")
    from sqlledger.timecards import InternalRates
    f = {k: str(form.get(k, "")).strip() for k in ("employee_id", "validfrom", "rate", "notes")}
    action = str(form.get("action", "save"))
    conn, _lib_ = _lib(ctx)
    with conn:
        rates = InternalRates(conn, ctx.user_config())
        if action == "delete":
            rid = str(form.get("id", ""))
            try:
                rates.delete(int(rid) if rid.isdigit() else 0)
            except LedgerError as exc:
                return _rates_page(ctx, conn, None, 400, error=_err(exc))
            ctx.log("internal_rate_deleted", id=rid)
            raise Redirect("/uren/tarieven?ok=deleted")
        try:
            validfrom = date.fromisoformat(f["validfrom"]) if f["validfrom"] else None
            if not f["rate"]:
                raise ValueError
            rate = _num(f["rate"], ctx.user["numberformat"])
        except ValueError:
            return _rates_page(ctx, conn, f, 400, error=_("Controleer de datum en het tarief."))
        try:
            rates.save(int(f["employee_id"]) if f["employee_id"].isdigit() else 0, validfrom, rate, f["notes"],
                       ctx.employee_login)
        except LedgerError as exc:
            return _rates_page(ctx, conn, f, 400, error=_err(exc))
    ctx.log("internal_rate_saved", employee_id=f["employee_id"], validfrom=f["validfrom"])
    raise Redirect("/uren/tarieven?ok=saved")
