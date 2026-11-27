"""Dashboard op het startscherm (fase 4 stap 5b, DB1–DB13; ``sqlledger.dashboard``).

De pagina (``/``) toont meteen de indeling; elk blok laadt daarna los via HTMX (DB7), zodat een traag blok de rest
niet ophoudt. Blokken volgen de modules en rollen (DB8): alleen-lezen ziet de getallen, geen actieknoppen.
Grafieken zijn SVG die de server tekent (DB6). Elk getal klikt door naar het rapport waar het vandaan komt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlencode

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from starlette.datastructures import FormData

from sqlledger.amounts import format_amount
from sqlledger.dashboard import BUCKETS, Dashboard
from sqlledger.dates import fiscal_start_year, format_date

from sqlledger import LedgerError, _db

from .companies import connect_company, probe
from .libmsg import lib_message
from .plchart import nice_step as _nice, short_amount
from .i18n import N_, gettext as _
from .web import Ctx, Redirect, csrf_form, ctx_company, render

router = APIRouter()
log = logging.getLogger(__name__)

#: blok -> module die aan moet staan (None = altijd)
BLOCKS = {"start": None, "tegels": None, "bank": "cash", "verkoop": "ar", "inkoop": "ap", "resultaat": "reports", "tedoen": None}
MONTHS = (N_("jan"), N_("feb"), N_("mrt"), N_("apr"), N_("mei"), N_("jun"),
          N_("jul"), N_("aug"), N_("sep"), N_("okt"), N_("nov"), N_("dec"))
BUCKET_LABELS = {"current": N_("niet vervallen"), "d30": "1–30", "d60": "31–60", "older": "> 60"}
QUARTERS = {1: N_("1e kwartaal"), 2: N_("2e kwartaal"), 3: N_("3e kwartaal"), 4: N_("4e kwartaal")}


def _modules(ctx: Ctx) -> List[str]:
    return list(ctx.company["modules"] or [])


def visible_blocks(ctx: Ctx) -> List[str]:
    mods = set(_modules(ctx))
    return [b for b, m in BLOCKS.items() if (m is None or m in mods) and (b != "start" or ctx.role == "admin")]


def page(ctx: Ctx):
    """Het startscherm: indeling met lege blokken + "Over deze administratie" (vroegere startpagina)."""
    try:
        info, error = probe(ctx.settings, ctx.company, ctx.employee_login), None
    except psycopg.Error as exc:
        info, error = None, str(exc).strip().splitlines()[0]
    if info:
        from .routes_main import _closedto
        info["closedto"] = _closedto(info["closedto"], ctx.user["dateformat"])
    return render(ctx, "dashboard/page.html", info=info, db_error=error, blocks=visible_blocks(ctx))


# ------------------------------------------------------------------ opmaak

class View:
    """Opmaak en links in de notatie van de gebruiker."""

    def __init__(self, ctx: Ctx, today: date):
        self.nf, self.df = ctx.user["numberformat"], ctx.user["dateformat"]
        self.today = today
        self.bookkeeper = ctx.has_role("bookkeeper")
        self.modules = set(_modules(ctx))
        self.vat_return = bool(ctx.company.get("vat_return"))      # btw-aangifte ingesteld (BA1/BA8)

    def amount(self, v, places: int = 2) -> str:
        return format_amount(v or 0, self.nf, places, "0").replace("&nbsp;", "").strip()

    def date(self, d: Optional[date]) -> str:
        return format_date(d, self.df) if d else ""

    def short(self, v) -> str:
        """Kort bedrag voor een as: 1,5K / 12K / 1,2M."""
        return short_amount(v, self.nf)

    @staticmethod
    def report(key: str, **params) -> str:
        return f"/rapporten/{key}?" + urlencode([("run", "1")] + [(k, v) for k, v in params.items()])


@dataclass
class Bar:
    x: float
    y: float
    w: float
    h: float
    label: str
    value: str
    title: str
    cls: str
    href: Optional[str] = None


@dataclass
class Chart:
    width: int
    height: int
    bars: List[Bar]
    ticks: List[Dict[str, Any]]
    labels: List[Dict[str, Any]]
    zero: float
    left: int


def bar_chart(groups: Sequence[Dict[str, Any]], view: View, width: int = 300, height: int = 170,
              show_values: bool = True) -> Chart:
    """Staafgrafiek. ``groups``: [{label, bars: [{value, cls, title, href}]}]; negatieve waarden onder de nullijn."""
    left, top, bottom = 34, 18, 26
    values = [float(b["value"] or 0) for g in groups for b in g["bars"]]
    hi, lo = max(values + [0.0]), min(values + [0.0])
    step = _nice(((hi - lo) or 1) / 4)
    top_v = step * max(1, -(-hi // step)) if hi > 0 else 0
    low_v = -step * max(1, -(-(-lo) // step)) if lo < 0 else 0
    span = (top_v - low_v) or 1
    plot_h = height - top - bottom

    def y_of(v: float) -> float:
        return top + (top_v - v) / span * plot_h

    zero = y_of(0)
    ticks = []
    v = low_v
    while v <= top_v + step / 2:
        ticks.append({"y": round(y_of(v), 1), "label": view.short(v)})
        v += step
    slot = (width - left - 4) / max(1, len(groups))
    n = max(len(g["bars"]) for g in groups) if groups else 1
    bw = min(46.0, slot * 0.7 / n)
    bars, labels = [], []
    for i, g in enumerate(groups):
        x0 = left + i * slot + (slot - bw * n) / 2
        for j, b in enumerate(g["bars"]):
            val = float(b["value"] or 0)
            y1, y2 = sorted((y_of(val), zero))
            bars.append(Bar(round(x0 + j * bw, 1), round(y1, 1), round(bw - (2 if n > 1 else 0), 1),
                            round(max(y2 - y1, 0), 1), g["label"],
                            view.short(val) if show_values and val else "",
                            b.get("title", ""), b.get("cls", ""), b.get("href")))
        labels.append({"x": round(left + i * slot + slot / 2, 1), "label": g["label"]})
    return Chart(width, height, bars, ticks, labels, round(zero, 1), left)


# ------------------------------------------------------------------ blokken

@router.get("/dashboard/blok/{name}")
def block(name: str, ctx: Ctx = Depends(ctx_company)):
    if name not in BLOCKS:
        raise HTTPException(404)
    if name not in visible_blocks(ctx):
        raise HTTPException(403, _("Deze module staat uit voor deze administratie."))
    today = date.today()
    view = View(ctx, today)
    try:
        conn = connect_company(ctx.settings, ctx.company)
    except psycopg.Error:                   # details (server, poort) alleen in het log (keuze 12)
        log.exception("dashboardblok %s: geen verbinding", name)
        return render(ctx, "dashboard/_error.html", block=name, message="")
    try:
        with conn:
            dash = Dashboard(conn, ctx.user_config())
            data = BUILDERS[name](dash, view, ctx)
    except Exception as exc:                # een blok mag de rest nooit ophouden (DB7)
        log.exception("dashboardblok %s", name)
        return render(ctx, "dashboard/_error.html", block=name,
                      message=lib_message(exc) if isinstance(exc, LedgerError) else "")
    return render(ctx, f"dashboard/_{name}.html", v=view, block=name, **data)


def _tiles(dash: Dashboard, view: View, ctx: Ctx) -> Dict[str, Any]:
    mods = view.modules
    out: Dict[str, Any] = {}
    if "cash" in mods:
        accounts = dash.bank(view.today)
        out["bank"] = {"total": sum(a.balance for a in accounts),
                       "open_lines": sum(a.open_lines for a in accounts),
                       "href": view.report("balans", period="", todate=view.today.isoformat())}
    for arap, key in (("ar", "verkoop"), ("ap", "inkoop")):
        if arap in mods:
            oi = dash.open_items(arap, view.today)
            out[arap] = {"oi": oi, "href": view.report("ouderdom-verkoop" if arap == "ar" else "ouderdom-inkoop",
                                                       period="", todate=view.today.isoformat(), overdue="0")}
    if "reports" in mods:
        res = dash.results(view.today)
        if res:
            out["result"] = {"r": res, "href": view.report("winst-en-verlies", period="",
                                                         fromdate=res.fromdate.isoformat(),
                                                         todate=res.todate.isoformat(), previousyear="1")}
    return out


def _bank(dash: Dashboard, view: View, ctx: Ctx) -> Dict[str, Any]:
    accounts = dash.bank(view.today)
    shown = [a for a in accounts if a.active]
    with _db.cursor(dash.conn) as cur:
        ye = _db.get_yearend(cur)
    if ye == 12:
        period = dict(period="year", year=str(view.today.year))
    else:                                   # JA3 B/JA6: lopend boekjaar
        period = dict(period="fiscal", fyear=str(fiscal_start_year(view.today, ye)))
    links = {a.accno: view.report("rekeningmutaties", accno=a.accno, **period) for a in accounts}
    return {"accounts": shown, "hidden": len(accounts) - len(shown), "total": sum(a.balance for a in accounts),
            "links": links}


def _open(arap: str):
    def build(dash: Dashboard, view: View, ctx: Ctx) -> Dict[str, Any]:
        oi = dash.open_items(arap, view.today)
        key = "ouderdom-verkoop" if arap == "ar" else "ouderdom-inkoop"
        href = view.report(key, period="", todate=view.today.isoformat(), overdue="1", summary="0")
        cls = {"current": "b-ok", "d30": "b-late1", "d60": "b-late2", "older": "b-late3"}
        groups = [{"label": _(BUCKET_LABELS[b]),
                   "bars": [{"value": oi.buckets[b], "cls": cls[b], "href": href,
                             "title": f"{_(BUCKET_LABELS[b])}: {view.amount(oi.buckets[b])}"}]} for b in BUCKETS]
        return {"oi": oi, "arap": arap, "chart": bar_chart(groups, view),
                "href": view.report(key, period="", todate=view.today.isoformat(), overdue="0")}
    return build


def _result(dash: Dashboard, view: View, ctx: Ctx) -> Dict[str, Any]:
    res = dash.results(view.today)
    if not res:
        return {"r": None}
    groups = []
    from sqlledger.dates import last_day_of_month
    for start, (this, before) in zip(res.month_starts, res.months):
        m = start.month
        end = min(last_day_of_month(start), res.todate)
        href = view.report("winst-en-verlies", period="", fromdate=start.isoformat(), todate=end.isoformat(),
                           previousyear="1") if this is not None else None
        bars = [{"value": before or 0, "cls": "b-prev", "href": href,
                 "title": f"{_(MONTHS[m - 1])} {start.year - 1}: {view.amount(before)}"},
                {"value": this or 0, "cls": "b-this", "href": href,
                 "title": f"{_(MONTHS[m - 1])} {start.year}: {view.amount(this) if this is not None else '–'}"}]
        groups.append({"label": _(MONTHS[m - 1]), "bars": bars})
    return {"r": res, "chart": bar_chart(groups, view, width=440, height=200, show_values=False),
            "href": view.report("winst-en-verlies", period="", fromdate=res.fromdate.isoformat(),
                                todate=res.todate.isoformat(), previousyear="1"),
            "month_href": view.report("winst-en-verlies", period="", fromdate=res.month_from.isoformat(),
                                      todate=res.month_to.isoformat(), previousyear="1"),
            "month_name": _(MONTHS[res.month - 1])}


def _todo(dash: Dashboard, view: View, ctx: Ctx) -> Dict[str, Any]:
    items = []
    todo = dash.todo(sorted(view.modules), view.today)
    for t in todo:
        items.append(_todo_item(t, view))
    at = 1 if todo and todo[0].kind == "vat" else 0           # direct onder de btw-regel
    items[at:at] = _icp_items(dash, view, ctx)
    items.extend(_backup_items(ctx, view))
    return {"items": items}


def _backup_items(ctx: Ctx, view: View) -> List[Dict[str, Any]]:
    """Back-up (6d-1, I7): beheerder en systeembeheerder zien een regel als er nog nooit een back-up met de app is
    gemaakt of de laatste ouder is dan ``backups.REMIND_DAYS`` dagen. De link alleen voor de systeembeheerder."""
    from . import backups
    if ctx.role != "admin" and not ctx.user["is_sysadmin"]:
        return []
    if not backups.overdue(ctx.company):
        return []
    last = ctx.company.get("last_backup")
    text = (_("Laatste back-up is van {date}; maak een nieuwe").format(date=view.date(last.astimezone().date()))
            if last else _("Nog geen back-up gemaakt met deze app"))
    return [{"text": text, "href": "/systeem/back-up-maken" if ctx.user["is_sysadmin"] else None, "level": "warn"}]


def _icp_items(dash: Dashboard, view: View, ctx: Ctx) -> List[Dict[str, Any]]:
    """ICP-opgaaf (ICP1): eigen regel bij de btw-taak (ook als het btw-tijdvak al is afgevinkt)."""
    if not ctx.company.get("icp") or "ar" not in view.modules:
        return []
    from .routes_icp import icp_todo
    item = icp_todo(dash.conn, ctx, view.today, view)
    return [dict(item, icp=True)] if item else []


def _todo_item(t, view: View) -> Dict[str, Any]:
    bk = view.bookkeeper
    if t.kind == "vat":
        vr = t.detail["vat"]
        if vr.period == "quarter" and not vr.number:     # kwartaal dat later eindigt (VK, BL7)
            what = _("Btw {period}").format(period=f"{_(MONTHS[vr.fromdate.month - 1])} – "
                                                   f"{_(MONTHS[vr.todate.month - 1])} {vr.todate.year}")
            params = dict(period="quarter", year=str(vr.todate.year), quarter=str((vr.todate.month - 1) // 3 + 1))
        elif vr.period == "quarter":
            what = _("Btw {period} {year}").format(period=_(QUARTERS[vr.number]), year=vr.todate.year)
            params = dict(period="quarter", year=str(vr.todate.year), quarter=str(vr.number))
        elif vr.period == "month":
            what = _("Btw {month} {year}").format(month=_(MONTHS[vr.number - 1]), year=vr.todate.year)
            params = dict(period="month", year=str(vr.todate.year), month=str(vr.number))
        else:
            what = _("Btw {year}").format(year=vr.todate.year)
            params = dict(period="year", year=str(vr.todate.year))
        text = _("{what}: aangifte vóór {date}").format(what=what, date=view.date(vr.deadline))
        if view.vat_return:
            href = f"/btw/aangifte?tijdvak={vr.fromdate.isoformat()}_{vr.todate.isoformat()}"
        else:
            href = view.report("btw-verkoop" if "ar" in view.modules else "btw-inkoop", **params)
        return {"text": text, "href": href, "level": "warn",
                "done": vr.todate.isoformat() if bk else None}
    if t.kind == "bank":
        accs = ", ".join(r["description"] or r["accno"] for r in t.detail["accounts"])
        return {"text": _("{n} bankregels te verwerken ({accounts})").format(n=t.count, accounts=accs)
                if t.count != 1 else _("1 bankregel te verwerken ({accounts})").format(accounts=accs),
                "href": "/bank/regels" if bk else None, "level": "warn"}
    if t.kind == "ar_overdue":
        return {"text": _("{n} vervallen verkoopfacturen: {amount}").format(n=t.count, amount=view.amount(t.amount))
                if t.count != 1 else _("1 vervallen verkoopfactuur: {amount}").format(amount=view.amount(t.amount)),
                "href": "/aanmaningen", "level": "red"}
    if t.kind == "ap_due":
        return {"text": _("{n} inkoopfacturen vervallen of vervallen binnen 7 dagen: {amount}").format(
                    n=t.count, amount=view.amount(t.amount)) if t.count != 1 else
                _("1 inkoopfactuur vervallen of vervalt binnen 7 dagen: {amount}").format(amount=view.amount(t.amount)),
                "href": "/betalingen" if bk else view.report("openstaand-inkoop", period=""),
                "level": "red" if t.detail.get("overdue") else "warn"}
    if t.kind == "recurring":
        return {"text": _("{n} terugkerende boekingen staan klaar").format(n=t.count) if t.count != 1
                else _("1 terugkerende boeking staat klaar"),
                "href": "/terugkerend" if bk else None, "level": "blue"}
    if t.kind == "parts_short":
        return {"text": _("{n} artikelen onder bestelniveau").format(n=t.count) if t.count != 1
                else _("1 artikel onder bestelniveau"),
                "href": "/artikelen?" + urlencode({"run": "1", "status": "short"}), "level": "blue"}
    if t.kind == "orders_late":
        return {"text": _("{n} verkooporders met verstreken leverdatum").format(n=t.count) if t.count != 1
                else _("1 verkooporder met verstreken leverdatum"),
                "href": "/verkooporders?" + urlencode({"run": "1", "open": "1", "sort": "reqdate"}),
                "level": "blue"}
    return {"text": t.kind, "href": None, "level": "blue"}


#: startlijst (eerste start, keuze 12): tekst en link per punt; logo en huisstijl samen (HS10 A)
START_HINTS = {"style": N_("Kies logo, kleur en lettertype voor facturen en andere documenten.")}
START_TEXTS = {
    "company": (N_("Bedrijfsgegevens invullen (naam en adres)"), "/beheer/instellingen"),
    "style": (N_("Logo en huisstijl"), "/beheer/huisstijl"),
    "vat": (N_("Btw-aangiftetijdvak kiezen"), "/beheer/instellingen"),
    "bank": (N_("IBAN van de bankrekening invullen"), "/beheer/rekeningschema"),
    "opening": (N_("Beginbalans boeken"), "/memoriaal/nieuw"),
    "invoice": (N_("Eerste verkoopfactuur maken"), "/verkoop/nieuw"),
}


def _start(dash: Dashboard, view: View, ctx: Ctx) -> Dict[str, Any]:
    from sqlledger import switchover
    from . import routes_switch
    info = switchover.restore_info(dash.conn)
    if info is not None:                  # overstap uit SQL-Ledger (6d-2) in plaats van de startlijst
        return {"items": routes_switch.items(ctx, dash.conn, dash, info, _modules(ctx)), "switch": True}
    items = dash.start_list(_modules(ctx))
    out = []
    for i in items or []:
        text, href = START_TEXTS[i.key]
        if i.key == "invoice" and "ic" in view.modules:
            href = "/verkoopfactuur/nieuw"
        if i.key == "bank" and i.target:
            href = f"/beheer/rekeningschema/{int(i.target)}#bank"
        out.append({"key": i.key, "text": _(text), "href": href, "done": i.done, "skipped": i.skipped,
                    "hint": _(START_HINTS[i.key]) if i.key in START_HINTS else ""})
    return {"items": out}


BUILDERS = {"start": _start, "tegels": _tiles, "bank": _bank, "verkoop": _open("ar"), "inkoop": _open("ap"),
            "resultaat": _result, "tedoen": _todo}


# ------------------------------------------------------------------ startlijst: niet nodig / verbergen (keuze 12)

@router.post("/dashboard/startlijst")
def start_mark(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    if ctx.role != "admin":
        raise HTTPException(403, _("Alleen een beheerder kan dit doen."))
    key = str(form.get("item") or "")
    if key not in START_TEXTS and key != "hidden":
        raise HTTPException(400, _("Onbekend punt."))
    with connect_company(ctx.settings, ctx.company) as conn:
        Dashboard(conn, ctx.user_config()).start_mark(key, ctx.employee_login)
    raise Redirect("/")


# ------------------------------------------------------------------ btw afvinken (DB5)

@router.post("/dashboard/btw-afgehandeld")
def vat_done(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    if not ctx.has_role("bookkeeper"):
        raise HTTPException(403, _("Alleen een boekhouder of beheerder kan dit doen."))
    if not set(_modules(ctx)) & {"ar", "ap"}:
        raise HTTPException(403, _("Deze module staat uit voor deze administratie."))
    try:
        period_end = date.fromisoformat(str(form.get("period_end") or ""))
    except ValueError:
        raise HTTPException(400, _("Ongeldige datum."))
    with connect_company(ctx.settings, ctx.company) as conn:
        dash = Dashboard(conn, ctx.user_config())
        vr = dash.vat_return(date.today())
        if vr is None or vr.todate != period_end:
            raise Redirect("/")                     # al afgevinkt of niet (meer) het tijdvak van nu
        dash.vat_done(period_end, ctx.employee_login)
    ctx.log("vat_return_done", period_end=period_end.isoformat())
    raise Redirect("/?ok=vat_done")
