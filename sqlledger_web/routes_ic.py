"""Artikelen en diensten (fase 3, beslispunten AT1–AT7): lijst met filters,
toevoegen, wijzigen, opslaan als nieuw, nieuw nummer, verwijderen (alleen
ongebruikt), prijshistorie, artikelgroepen en voorraadcorrectie. Samengestelde
artikelen (SA1–SA12) gebruiken hetzelfde scherm met een stuklijst; assembleren en
de stuklijstoverzichten staan in ``routes_asm``.

Het scherm volgt ic.pl voor artikel en dienst. Opslaan gaat via
``sqlledger.parts`` en schrijft alleen wat gewijzigd is; de voorraad staat er
alleen ter informatie (wijzigen via Voorraadcorrectie, AT4).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from starlette.datastructures import FormData

from sqlledger import LedgerError, Part, Parts, PartSearch
from sqlledger.amounts import format_amount, parse_amount
from sqlledger.assembly import Component
from sqlledger.parts import EDITABLE_KINDS, STATUSES as LIB_STATUSES

from . import production
from .companies import connect_company
from .i18n import N_, gettext as _
from .libmsg import lib_message
from .tables import Col, Row, Table, cell_text, to_csv, to_xlsx
from .reportpdf import pdf_response
from . import routes_attach as attach
from .web import Ctx, Redirect, csrf_form, ctx_company, render, require_role

router = APIRouter()

PAGE_SIZE = 500

KIND_LABELS = {"part": N_("Artikel"), "service": N_("Dienst"), "labor": N_("Loon/overhead"),
               "assembly": N_("Samengesteld artikel"), "kit": N_("Kit")}
TITLES_ADD = {"part": N_("Artikel toevoegen"), "service": N_("Dienst toevoegen"),
              "assembly": N_("Samengesteld artikel toevoegen")}
TITLES_EDIT = {"part": N_("Artikel wijzigen"), "service": N_("Dienst wijzigen"),
               "assembly": N_("Samengesteld artikel wijzigen")}
#: url-waarde van ?soort= -> soort
KIND_PARAM = {"artikel": "part", "dienst": "service", "samengesteld": "assembly"}
#: maximaal aantal regels in een stuklijst op het scherm
MAX_COMPONENTS = 300

TEXT = ("partnumber", "description", "unit", "bin", "barcode", "notes", "inventory_accno", "income_accno",
        "expense_accno")
PRICES = ("sellprice", "listprice", "lastcost")
NUMBERS = ("weight", "rop")


class PartForm:
    """Formulierwaarden als tekst (zoals de browser ze stuurt)."""

    def __init__(self, **values: Any):
        self.__dict__.update(id=None, kind="part", partsgroup_id="", priceupdate="", obsolete=False,
                             checkinventory=False, taxaccounts=[], number_preview="", components=[],
                             own_sellprice=False)
        for k in TEXT + PRICES + NUMBERS:
            self.__dict__.setdefault(k, "")
        self.__dict__.update(values)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return ""


def _decimals(v: Decimal) -> int:
    return max(0, -v.normalize().as_tuple().exponent) if v else 0


def _num(v: Any, nf: str, places: Optional[int] = None) -> str:
    if not v:
        return ""
    return format_amount(v, nf, places).replace("&nbsp;", "").strip()


def price_text(v: Decimal, nf: str, precision: int) -> str:
    """Prijs met minstens de standaarddecimalen, meer als de prijs die heeft (ic.pl decimalplaces)."""
    return _num(v, nf, max(precision, _decimals(Decimal(v)))) if v else ""


def form_from_part(p: Part, nf: str, precision: int) -> PartForm:
    f = PartForm(id=p.id, kind=p.kind, partsgroup_id=str(p.partsgroup_id or ""),
                 priceupdate=p.priceupdate.isoformat() if p.priceupdate else "", obsolete=p.obsolete,
                 checkinventory=p.checkinventory, taxaccounts=list(p.taxaccounts))
    for k in TEXT:
        setattr(f, k, getattr(p, k) or "")
    for k in PRICES:
        setattr(f, k, price_text(getattr(p, k), nf, precision))
    for k in NUMBERS:
        setattr(f, k, _num(getattr(p, k), nf))
    f.own_sellprice = p.own_sellprice
    f.components = [comp_row(c.parts_id, _num(c.qty, nf, _decimals(c.qty)), c.bom) for c in p.components]
    return f


def comp_row(part_id: Optional[int], qty: str = "", bom: bool = False, find: str = "") -> Dict[str, Any]:
    """Regel van de stuklijst op het scherm."""
    return {"id": part_id, "qty": qty, "bom": bom, "find": find, "choices": []}


def form_from_post(form: FormData) -> PartForm:
    def g(k: str, strip: bool = True) -> str:
        v = str(form.get(k, ""))
        return v.strip() if strip else v
    kind = g("kind") if g("kind") in EDITABLE_KINDS else "part"
    f = PartForm(id=int(g("id")) if g("id").isdigit() else None, kind=kind,
                 partsgroup_id=g("partsgroup_id") if g("partsgroup_id").isdigit() else "",
                 priceupdate=g("priceupdate"), obsolete=g("obsolete") == "1",
                 checkinventory=g("checkinventory") == "1", taxaccounts=[str(x) for x in form.getlist("tax")],
                 number_preview=g("number_preview"))
    for k in TEXT + PRICES + NUMBERS:
        setattr(f, k, g(k, strip=k != "notes")[:2000])
    f.own_sellprice = g("own_sellprice") == "1"
    n = int(g("comp_rows")) if g("comp_rows").isdigit() else 0
    for i in range(1, min(n, MAX_COMPONENTS) + 1):
        pick = g(f"comp_pick_{i}")
        cid = pick if pick.isdigit() else g(f"comp_id_{i}")
        row = comp_row(int(cid) if cid.isdigit() else None, g(f"comp_qty_{i}")[:30], g(f"comp_bom_{i}") == "1",
                       "" if cid.isdigit() else g(f"comp_find_{i}")[:200])
        if row["id"] or row["find"]:
            f.components.append(row)
    return f


def resolve_components(lib: Parts, f: PartForm) -> Optional[str]:
    """Zoektekst in een nieuwe regel omzetten naar een artikel (IC.pm assembly_item). Eén treffer of
    één exact nummer: overnemen (aantal standaard 1); meer: keuzelijst in die regel; geen: melding."""
    errors = []
    for row in f.components:
        if row["id"] or not row["find"]:
            continue
        choices = lib.component_choices(row["find"], f.id)
        exact = [c for c in choices if (c["partnumber"] or "").lower() == row["find"].lower()]
        if len(exact) == 1 or len(choices) == 1:
            row["id"] = (exact or choices)[0]["id"]
            row["find"] = ""
            row["qty"] = row["qty"] or "1"
        elif not choices:
            errors.append(_("Geen artikel gevonden voor “{text}”.").format(text=row["find"]))
        else:
            row["choices"] = choices
            errors.append(_("Meer artikelen gevonden voor “{text}”: kies er een.").format(text=row["find"]))
    return " ".join(errors) or None


def _components(f: PartForm, nf: str) -> List[Component]:
    out = []
    for row in f.components:
        if not row["id"]:
            raise ValueError(_("Kies eerst het artikel bij “{text}”.").format(text=row["find"]))
        qty = row["qty"]
        if qty and not any(c.isdigit() for c in qty):
            raise ValueError(_("Ongeldig getal: {value}").format(value=qty))
        q = _parse(qty, nf)
        if q:                       # aantal leeg of 0: regel vervalt (zoals ic.pl)
            out.append(Component(parts_id=row["id"], qty=q, bom=row["bom"]))
    return out


def _parse(v: str, nf: str) -> Decimal:
    if not v:
        return Decimal(0)
    try:
        return parse_amount(v, nf)
    except (InvalidOperation, ValueError):
        raise ValueError(_("Ongeldig getal: {value}").format(value=v))


def to_part(f: PartForm, nf: str) -> Part:
    p = Part(id=f.id, kind=f.kind)
    for k in TEXT:
        setattr(p, k, getattr(f, k))
    for k in PRICES + NUMBERS:
        v = getattr(f, k)
        if v and not any(c.isdigit() for c in v):
            raise ValueError(_("Ongeldig getal: {value}").format(value=v))
        setattr(p, k, _parse(v, nf))
    p.partsgroup_id = int(f.partsgroup_id) if f.partsgroup_id else None
    try:
        p.priceupdate = date.fromisoformat(f.priceupdate) if f.priceupdate else None
    except ValueError:
        raise ValueError(_("Ongeldige datum: {value}").format(value=f.priceupdate))
    p.obsolete, p.checkinventory = f.obsolete, f.checkinventory
    p.taxaccounts = list(f.taxaccounts)
    if p.kind == "service":
        p.inventory_accno = ""
    if p.kind == "assembly":
        p.inventory_accno = p.expense_accno = ""
        p.own_sellprice = f.own_sellprice
        p.components = _components(f, nf)
    return p


# ------------------------------------------------------------------ hulp

def require_ic(ctx: Ctx) -> None:
    if "ic" not in (ctx.company["modules"] or []):
        raise HTTPException(403, _("Deze module staat uit voor deze administratie."))


def _lib(ctx: Ctx):
    try:
        conn = connect_company(ctx.settings, ctx.company)
    except psycopg.Error:
        raise HTTPException(503, _("De administratie is niet bereikbaar."))
    return conn, Parts(conn, ctx.user_config())


def _precision(conn) -> int:
    from sqlledger import _db
    with _db.cursor(conn) as cur:
        return _db.get_precision(cur)


def _currency(conn) -> str:
    from sqlledger import _db
    with _db.cursor(conn) as cur:
        return _db.default_currency(cur) or ""


def price_label(curr: str) -> str:
    """Kolomkop van de prijs in prijshistorie en "Regels uit" (F39): alle prijzen staan in de standaardvaluta."""
    return _("Prijs ({currency})").format(currency=curr) if curr else _("Prijs")


def doc_url(r: Dict[str, Any], modules: List[str], key: str = "id") -> Optional[str]:
    """Link naar het stuk (F40): een factuur via de boeking, een order of offerte naar het eigen scherm als de module
    aanstaat (dat scherm regelt zelf alleen-lezen en locks, zoals vanuit de orderlijst)."""
    if r["module"] in ("ar", "ap"):
        return f"/boekingen/{r[key]}"
    screen, need = attach.FORM_SCREENS.get(r["type"]), attach.FORM_MODULES.get(r["type"], ())
    if screen and any(m in modules for m in need):
        return screen.format(id=r[key])
    return None


def _page(ctx: Ctx, conn, lib: Parts, f: PartForm, *, status: int = 200, error: Optional[str] = None,
          confirm: Optional[str] = None, saved: Optional[Part] = None, **kw):
    if f.id and saved is None:
        try:
            saved = lib.get(f.id)
        except LedgerError:
            saved = None
    kind = saved.kind if saved else f.kind
    links = lib.links(kind if kind in ("part", "service", "labor", "assembly") else "part")
    # rekening/btw op het formulier die niet (meer) in de lijst staat: toch tonen
    for key, fld in (("inventory", "inventory_accno"), ("income", "income_accno"), ("expense", "expense_accno")):
        v = getattr(f, fld)
        if v and v not in {a["accno"] for a in links[key]}:
            links[key] = links[key] + [{"accno": v, "description": ""}]
    have = {a["accno"] for a in links["tax"]}
    links["tax"] = links["tax"] + [{"accno": a, "description": ""} for a in f.taxaccounts if a not in have]
    readonly = not ctx.has_role("bookkeeper") or bool(saved and not saved.editable)
    locked = bool(saved and saved.in_use)          # rekeningen vast (AT5)
    duplicates = lib.duplicates(f.partnumber, f.id) if f.partnumber else []
    nf = ctx.user["numberformat"]
    if f.id:
        title = TITLES_EDIT.get(kind) or KIND_LABELS[kind]
    else:
        title = TITLES_ADD[f.kind]
    bom = _bom_view(lib, f, saved, nf, _precision(conn), readonly) if kind == "assembly" else None
    return render(ctx, "ic/form.html", status, f=f, saved=saved, kind=kind, links=links, groups=lib.groups(),
                  bom=bom, attachments=attach.saved_attachments(ctx, conn, f.id), can_edit=ctx.has_role("bookkeeper"),
                  error=error, confirm=confirm, title=title, kind_label=KIND_LABELS[kind], readonly=readonly,
                  dis=" disabled" if readonly else "", locked=locked, duplicates=duplicates,
                  onhand=_num(saved.onhand, nf) if saved else "", avgcost=price_text(saved.avgcost, nf, 2)
                  if saved else "", **kw)


def _bom_view(lib: Parts, f: PartForm, saved: Optional[Part], nf: str, precision: int,
              readonly: bool) -> Dict[str, Any]:
    """Stuklijst voor het scherm: regels met artikelgegevens en regeltotalen, berekende prijzen;
    vast (aantallen niet te wijzigen) zolang er voorraad van het product is (SA8)."""
    def q(v: str) -> Decimal:
        try:
            return _parse(v, nf) if v and any(c.isdigit() for c in v) else Decimal(0)
        except ValueError:
            return Decimal(0)
    rows = [(r["id"], q(r["qty"])) for r in f.components if r["id"]]
    pv = lib.bom_preview(f.id, rows)
    locked = bool(saved and saved.kind == "assembly" and saved.onhand)
    lines = []
    for r in f.components:
        u = pv["units"].get(r["id"]) if r["id"] else None
        qty = q(r["qty"])
        line = dict(r)
        if u:
            line.update(partnumber=u["partnumber"], description=u["description"], unit=u["unit"],
                        onhand=_num(u["onhand"], nf) or "0", stocked=u["stocked"], assembly=u["assembly"],
                        obsolete=u["obsolete"],
                        sell=price_text(u["lines"]["sellprice"], nf, precision) or "0",
                        cost=price_text(u["lines"]["lastcost"], nf, precision) or "0")
        lines.append(line)
    if not (readonly or locked) and len(lines) < MAX_COMPONENTS:
        lines.append(comp_row(None))
    total = pv["total"]
    return {"lines": lines, "locked": locked,
            "sellprice": price_text(total["sellprice"], nf, precision) or "0",
            "listprice": price_text(total["listprice"], nf, precision) or "0",
            "lastcost": price_text(total["lastcost"], nf, precision) or "0",
            "weight": _num(total["weight"], nf) or "0"}


# ------------------------------------------------------------------ bewerkscherm

@router.get("/artikelen/nieuw", name="ic_new")
def new_form(ctx: Ctx = Depends(ctx_company)):
    require_ic(ctx)
    require_role(ctx, "bookkeeper")
    kind = KIND_PARAM.get(ctx.request.query_params.get("soort", ""), "part")
    conn, lib = _lib(ctx)
    with conn:
        f = form_from_part(lib.new(kind), ctx.user["numberformat"], _precision(conn))
        return _page(ctx, conn, lib, f)


@router.get("/artikelen/{part_id}", name="ic_edit")
def edit_form(part_id: int, ctx: Ctx = Depends(ctx_company)):
    require_ic(ctx)
    conn, lib = _lib(ctx)
    with conn:
        try:
            p = lib.get(part_id)
        except LedgerError:
            raise HTTPException(404, _("Artikel niet gevonden."))
        f = form_from_part(p, ctx.user["numberformat"], _precision(conn))
        return _page(ctx, conn, lib, f, saved=p)


def do_save(lib: Parts, f: PartForm, nf: str, as_new: bool = False) -> int:
    """Opslaan zoals het scherm; als nieuw: zonder id en met een nieuw nummer als het
    nummer ongewijzigd is (zoals ic.pl save_as_new)."""
    p = to_part(f, nf)
    if as_new:
        if f.id and p.partnumber == lib.get(f.id).partnumber:
            p.partnumber = ""
        p.id = None
    elif not p.partnumber and f.number_preview:
        p.partnumber = ""
    return lib.save(p)


@router.post("/artikelen", name="ic_post")
def post_form(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    require_ic(ctx)
    require_role(ctx, "bookkeeper")
    production.forget(ctx.company)          # menu productie (PB20): misschien nu (geen) samengestelde artikelen
    f = form_from_post(form)
    action = str(form.get("action", "update"))
    nf = ctx.user["numberformat"]
    conn, lib = _lib(ctx)
    with conn:
        saved = None
        if f.id:
            try:
                saved = lib.get(f.id)
            except LedgerError:
                raise HTTPException(404, _("Artikel niet gevonden."))
            f.kind = saved.kind if saved.kind in EDITABLE_KINDS else f.kind
        notice = resolve_components(lib, f) if f.kind == "assembly" else None
        if action == "update":
            return _page(ctx, conn, lib, f, error=notice, status=200)
        if notice and action in ("save", "save_as_new"):
            return _page(ctx, conn, lib, f, status=400, error=notice)
        if action == "new_number":
            f.partnumber = f.number_preview = lib.next_number(peek=True)
            return _page(ctx, conn, lib, f, number_note=True)
        if action in ("delete", "delete_confirmed"):
            if not f.id:
                return _page(ctx, conn, lib, f, status=400, error=_("Er is nog niets opgeslagen."))
            if action == "delete":
                return _page(ctx, conn, lib, f, confirm="delete")
            try:
                lib.delete(f.id)
            except LedgerError as exc:
                return _page(ctx, conn, lib, f, status=400, error=lib_message(exc))
            ctx.log("ic_deleted", id=f.id, partnumber=saved.partnumber if saved else None)
            raise Redirect("/artikelen?ok=ic_deleted")
        if action not in ("save", "save_as_new"):
            raise HTTPException(400, _("Onbekende actie."))
        if action == "save_as_new" and not f.id:
            action = "save"
        new = action == "save_as_new" or not f.id
        try:
            part_id = do_save(lib, f, nf, as_new=action == "save_as_new")
        except (ValueError, LedgerError) as exc:
            return _page(ctx, conn, lib, f, status=400, error=lib_message(exc))
        ctx.log("ic_saved", id=part_id, new=new)
        raise Redirect(f"/artikelen/{part_id}?ok=ic_saved")


# ------------------------------------------------------------------ prijshistorie

@router.get("/artikelen/{part_id}/historie", name="ic_history")
def history(part_id: int, ctx: Ctx = Depends(ctx_company)):
    require_ic(ctx)
    side = "purchases" if ctx.request.query_params.get("kant") == "inkoop" else "sales"
    conn, lib = _lib(ctx)
    with conn:
        try:
            p = lib.get(part_id)
        except LedgerError:
            raise HTTPException(404, _("Artikel niet gevonden."))
        rows = lib.history(part_id, side)
        precision = _precision(conn)
        curr = _currency(conn)
    modules = ctx.company["modules"] or []
    nf, df = ctx.user["numberformat"], ctx.user["dateformat"]
    qdec = max([0] + [_decimals(Decimal(r["qty"] or 0)) for r in rows])
    table = Table([Col("transdate", _("Datum"), "date"), Col("type", _("Soort"), "text"),
                   Col("number", _("Nummer"), "text"), Col("name", _("Klant") if side == "sales" else _("Leverancier"),
                                                           "text"),
                   Col("description", _("Omschrijving"), "text"), Col("qty", _("Aantal"), "amount", qdec),
                   Col("sellprice", price_label(curr), "amount", max([precision] + [_decimals(r["sellprice"] or Decimal(0))
                                                                             for r in rows])),
                   Col("discount", _("Korting %"), "amount", 2), Col("curr", _("Valuta"), "text")],
                  [Row("row", {**r, "type": _(HISTORY_TYPES.get(r["type"], r["type"])),
                               "discount": (r["discount"] or 0) * 100 or None},
                       {"number": doc_url(r, modules)}) for r in rows])
    return render(ctx, "ic/history.html", p=p, side=side, table=table,
                  cell=lambda c, r: cell_text(c, r, nf, df))


HISTORY_TYPES = {"invoice": N_("factuur"), "sales_order": N_("verkooporder"), "purchase_order": N_("inkooporder"),
                 "sales_quotation": N_("offerte"), "request_quotation": N_("offerteaanvraag")}


# ------------------------------------------------------------------ lijst (ic.pl search / generate_report)

COLUMNS = [
    ("partnumber", N_("Nummer"), "text"), ("description", N_("Omschrijving"), "text"),
    ("kind", N_("Soort"), "text"), ("partsgroup", N_("Groep"), "text"), ("unit", N_("Eenheid"), "text"),
    ("onhand", N_("Voorraad"), "qty"), ("rop", N_("Bestelniveau"), "qty"), ("bin", N_("Bak"), "text"),
    ("sellprice", N_("Verkoopprijs"), "price"), ("listprice", N_("Lijstprijs"), "price"),
    ("lastcost", N_("Laatste inkoopprijs"), "price"), ("avgcost", N_("Gemiddelde kostprijs"), "price"),
    ("priceupdate", N_("Prijsdatum"), "date"), ("weight", N_("Gewicht"), "qty"), ("barcode", N_("Barcode"), "text"),
    ("inventory", N_("Voorraadrekening"), "text"), ("income", N_("Opbrengstrekening"), "text"),
    ("expense", N_("Kostenrekening"), "text"), ("tax", N_("Btw"), "text"), ("notes", N_("Notities"), "text"),
]
TCOLUMNS = [
    ("transdate", N_("Datum"), "date"), ("number", N_("Nummer"), "text"), ("type", N_("Soort"), "text"),
    ("name", N_("Relatie"), "text"), ("partnumber", N_("Artikelnummer"), "text"),
    ("description", N_("Omschrijving"), "text"), ("qty", N_("Aantal"), "qty"), ("unit", N_("Eenheid"), "text"),
    ("sellprice", N_("Prijs"), "price"), ("discount", N_("Korting %"), "pct"), ("curr", N_("Valuta"), "text"),
    ("serialnumber", N_("Serienummer"), "text"), ("employee", N_("Medewerker"), "text"),
]
DEFAULT_COLUMNS = ["partnumber", "description", "kind", "onhand", "unit", "sellprice"]
DEFAULT_TCOLUMNS = ["transdate", "number", "type", "name", "partnumber", "description", "qty", "sellprice"]
KINDS = [("all", N_("Alle")), ("part", N_("Artikelen")), ("service", N_("Diensten")),
         ("labor", N_("Loon/overhead")), ("assembly", N_("Samengesteld")), ("kit", N_("Kits"))]
STATUSES = [("all", N_("Alle")), ("active", N_("Actief")), ("obsolete", N_("Vervallen")),
            ("orphaned", N_("Ongebruikt")), ("onhand", N_("Op voorraad")), ("short", N_("Onder bestelniveau"))]
TRANSACTIONS = [("sold", N_("Verkocht")), ("bought", N_("Gekocht")), ("ordered", N_("Verkooporders")),
                ("onorder", N_("Inkooporders")), ("quoted", N_("Offertes")), ("rfq", N_("Offerteaanvragen"))]
SORTS = [("partnumber", N_("Nummer")), ("description", N_("Omschrijving")), ("partsgroup", N_("Groep")),
         ("onhand", N_("Voorraad")), ("sellprice", N_("Verkoopprijs")), ("priceupdate", N_("Prijsdatum"))]
TSORTS = [("transdate", N_("Datum")), ("partnumber", N_("Artikelnummer")), ("description", N_("Omschrijving")),
          ("name", N_("Relatie")), ("number", N_("Nummer"))]
TYPES = {**HISTORY_TYPES, "invoice": N_("factuur")}


def _search_values(q) -> Dict[str, Any]:
    v: Dict[str, Any] = {k: (q.get(k) or "").strip()[:200] for k in ("partnumber", "description", "barcode")}
    v["kind"] = q.get("kind") if q.get("kind") in dict(KINDS) else "all"
    v["status"] = q.get("status") if q.get("status") in LIB_STATUSES else "active"
    v["partsgroup_id"] = q.get("partsgroup_id") if (q.get("partsgroup_id") or "").isdigit() else ""
    v["transactions"] = [t for t in q.getlist("transactions") if t in dict(TRANSACTIONS)]
    run = q.get("run") == "1"
    v["open"] = q.get("open") == "1" if run else True
    v["closed"] = q.get("closed") == "1" if run else True
    for k in ("datefrom", "dateto"):
        try:
            v[k] = date.fromisoformat(q.get(k)).isoformat() if q.get(k) else ""
        except ValueError:
            v[k] = ""
    sorts = TSORTS if v["transactions"] else SORTS
    v["sort"] = q.get("sort") if q.get("sort") in dict(sorts) else sorts[0][0]
    v["desc"] = q.get("direction") == "DESC"
    cols = [c for c in q.getlist("cols") if c]
    default = DEFAULT_TCOLUMNS if v["transactions"] else DEFAULT_COLUMNS
    allowed = {c[0] for c in (TCOLUMNS if v["transactions"] else COLUMNS)}
    # de kolomkeuze hoort bij de vorige soort lijst; net omgeschakeld tussen artikelen en regels (F29): standaard
    mode = "regels" if v["transactions"] else "artikelen"
    if q.get("colmode") and q.get("colmode") != mode:
        cols = []
    v["cols"] = [c for c in cols if c in allowed] if run and cols else list(default)
    if not v["cols"]:
        v["cols"] = list(default)
    return v


def _search(v: Dict[str, Any]) -> PartSearch:
    return PartSearch(kind=v["kind"], partnumber=v["partnumber"], description=v["description"],
                      barcode=v["barcode"], partsgroup_id=int(v["partsgroup_id"]) if v["partsgroup_id"] else None,
                      status=v["status"], transactions=tuple(v["transactions"]), open=v["open"], closed=v["closed"],
                      datefrom=date.fromisoformat(v["datefrom"]) if v["datefrom"] else None,
                      dateto=date.fromisoformat(v["dateto"]) if v["dateto"] else None,
                      sort=v["sort"], desc=v["desc"])


def _table(rows: List[Dict[str, Any]], v: Dict[str, Any], precision: int, curr: str = "",
           modules: Optional[List[str]] = None) -> Table:
    transactions = bool(v["transactions"])
    defs = TCOLUMNS if transactions else COLUMNS
    def dec(key: str, least: int) -> int:     # per kolom zoveel decimalen als de waarden hebben
        return max([least] + [_decimals(Decimal(r[key])) for r in rows if r.get(key)])
    columns = []
    for key, label, kind in defs:
        if key not in v["cols"]:
            continue
        if kind == "price":
            text = price_label(curr) if transactions and key == "sellprice" else _(label)
            columns.append(Col(key, text, "amount", dec(key, precision)))
        elif kind == "qty":
            columns.append(Col(key, _(label), "amount", dec(key, 0)))
        elif kind == "pct":
            columns.append(Col(key, _(label), "amount", 2))
        else:
            columns.append(Col(key, _(label), kind if kind == "date" else "text"))
    out = []
    for r in rows:
        cells = dict(r)
        cells["kind"] = _(KIND_LABELS.get(r["kind"], r["kind"]))
        if transactions:
            cells["type"] = _(TYPES.get(r["type"], r["type"]))
            cells["discount"] = (r.get("discount") or 0) * 100 or None
            links = {"partnumber": f"/artikelen/{r['id']}"}
            url = doc_url(r, modules or [], "trans_id")
            if url:
                links["number"] = url
        else:
            links = {"partnumber": f"/artikelen/{r['id']}", "description": f"/artikelen/{r['id']}"}
        out.append(Row("row", cells, links))
    return Table(columns, out)


@router.get("/artikelen", name="ic_list")
def list_page(ctx: Ctx = Depends(ctx_company)):
    require_ic(ctx)
    q = ctx.request.query_params
    v = _search_values(q)
    conn, lib = _lib(ctx)
    with conn:
        rows = lib.search(_search(v))
        groups = lib.groups()
        precision = _precision(conn)
        curr = _currency(conn)
    table = _table(rows, v, precision, curr, ctx.company["modules"] or [])
    nf, df = ctx.user["numberformat"], ctx.user["dateformat"]
    export = q.get("export")
    if export == "pdf":
        return pdf_response(ctx, [table], _("Artikelen"), [], "artikelen")
    if export in ("csv", "xlsx"):
        ctx.log("report_exported", report="artikelen", format=export)
        if export == "csv":
            return Response(to_csv([table], nf, df), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": 'attachment; filename="artikelen.csv"'})
        return Response(to_xlsx([table], _("Artikelen"), [ctx.company["name"]], nf, df),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="artikelen.xlsx"'})
    p = q.get("page", "1")
    page = int(p) if p.isdigit() else 1
    paged, pages = table.page(page, PAGE_SIZE)
    page = min(max(page, 1), pages)
    export_query = urlencode([(a, b) for a, b in q.multi_items() if a not in ("export", "page", "ok")]
                             or [("run", "1")])
    return render(ctx, "ic/list.html", v=v, table=paged, count=len(rows), page=page, pages=pages,
                  columns=TCOLUMNS if v["transactions"] else COLUMNS, kinds=KINDS, statuses=STATUSES,
                  transactions=TRANSACTIONS, sorts=TSORTS if v["transactions"] else SORTS, groups=groups,
                  can_edit=ctx.has_role("bookkeeper"), export_query=export_query,
                  cell=lambda c, r: cell_text(c, r, nf, df))


# ------------------------------------------------------------------ artikelgroepen

@router.get("/artikelen-groepen", name="ic_groups")
def groups_page(ctx: Ctx = Depends(ctx_company), status: int = 200, error: Optional[str] = None,
                edit: Optional[int] = None):
    require_ic(ctx)
    conn, lib = _lib(ctx)
    with conn:
        groups = lib.groups()
    q = ctx.request.query_params
    edit_id = edit or (int(q.get("bewerk")) if (q.get("bewerk") or "").isdigit() else None)
    return render(ctx, "ic/groups.html", status, groups=groups, error=error, edit_id=edit_id,
                  can_edit=ctx.has_role("bookkeeper"))


@router.post("/artikelen-groepen", name="ic_groups_post")
def groups_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    require_ic(ctx)
    require_role(ctx, "bookkeeper")
    action = str(form.get("action", "save"))
    gid = int(form.get("id")) if str(form.get("id", "")).isdigit() else None
    conn, lib = _lib(ctx)
    with conn:
        try:
            if action == "delete" and gid:
                lib.delete_group(gid)
                ctx.log("ic_group_deleted", id=gid)
            else:
                gid = lib.save_group(str(form.get("partsgroup", "")), str(form.get("code", "")).strip(), gid)
                ctx.log("ic_group_saved", id=gid)
        except LedgerError as exc:
            return groups_page(ctx, status=400, error=lib_message(exc), edit=gid)
    raise Redirect("/artikelen-groepen?ok=saved")


# ------------------------------------------------------------------ voorraadcorrectie (AT4)

@router.get("/artikelen/{part_id}/voorraad", name="ic_stock")
def stock_page(part_id: int, ctx: Ctx = Depends(ctx_company), status: int = 200, error: Optional[str] = None,
               values: Optional[Dict[str, str]] = None):
    require_ic(ctx)
    conn, lib = _lib(ctx)
    with conn:
        try:
            p = lib.get(part_id)
        except LedgerError:
            raise HTTPException(404, _("Artikel niet gevonden."))
        rows = lib.stock_adjustments(part_id)
    nf, df = ctx.user["numberformat"], ctx.user["dateformat"]
    qdec = max([0] + [_decimals(Decimal(r[k])) for r in rows for k in ("oldonhand", "qty", "newonhand")]
               + [_decimals(p.onhand)])
    table = Table([Col("transdate", _("Datum"), "date"), Col("oldonhand", _("Was"), "amount", qdec),
                   Col("qty", _("Verschil"), "amount", qdec), Col("newonhand", _("Wordt"), "amount", qdec),
                   Col("reason", _("Reden"), "text"), Col("employee", _("Medewerker"), "text")],
                  [Row("row", dict(r), {}) for r in rows])
    return render(ctx, "ic/stock.html", status, p=p, onhand=_num(p.onhand, nf, qdec) or "0", table=table,
                  error=error, values=values or {"transdate": date.today().isoformat(), "mode": "new"},
                  can_edit=ctx.has_role("bookkeeper") and p.kind in ("part", "labor", "assembly"),
                  cell=lambda c, r: cell_text(c, r, nf, df))


@router.post("/artikelen/{part_id}/voorraad", name="ic_stock_post")
def stock_post(part_id: int, ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    require_ic(ctx)
    require_role(ctx, "bookkeeper")
    values = {k: str(form.get(k, "")).strip()[:500] for k in ("transdate", "mode", "amount", "reason")}
    nf = ctx.user["numberformat"]
    conn, lib = _lib(ctx)
    with conn:
        try:
            if not values["amount"] or not any(c.isdigit() for c in values["amount"]):
                raise ValueError(_("Vul een aantal in."))
            amount = _parse(values["amount"], nf)
            try:
                d = date.fromisoformat(values["transdate"])
            except ValueError:
                raise ValueError(_("Ongeldige datum: {value}").format(value=values["transdate"]))
            kw = {"newonhand": amount} if values["mode"] != "diff" else {"qty": amount}
            r = lib.adjust_stock(part_id, transdate=d, reason=values["reason"], **kw)
        except (ValueError, LedgerError) as exc:
            return stock_page(part_id, ctx, status=400, error=lib_message(exc), values=values)
    ctx.log("ic_stock_adjusted", id=part_id, qty=str(r["qty"]), newonhand=str(r["newonhand"]))
    raise Redirect(f"/artikelen/{part_id}/voorraad?ok=ic_stock")
