"""RGS-lijst inlezen (Systeem) en RGS-koppeling per administratie (Beheer), REKENINGSCHEMA_ANALYSE.md RS13 keuze A.

Systeem > RGS-lijst (systeembeheerder): het officiële bestand van referentiegrootboekschema.nl (``RGS 3.8-def.xlsx``)
uploaden; ``sqlledger.rgs`` zet het om naar JSON in de datamap, voor alle administraties van deze installatie. De lijst
wordt niet meegeleverd.

Beheer > RGS-koppeling (beheerder): per rekening de huidige code en tot drie voorstellen (zoals Exact Online), een
andere code of geen; er wordt niets automatisch gekoppeld. Standaard alleen de rekeningen zonder code; zoeken in de
lijst op code of woorden.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import psycopg

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.datastructures import FormData

from sqlledger import rgs
from sqlledger.errors import LedgerError

from .i18n import N_, gettext as _
from .libmsg import lib_message
from .companies import connect_company
from .routes_am import CATEGORIES, _open
from .uploads import read_upload
from .web import Ctx, Redirect, csrf_form, ctx_company, ctx_sysadmin, render, require_role

router = APIRouter()

MAX_RGS = 32 * 1024 * 1024
#: Kennisbank van referentiegrootboekschema.nl: daar staat het Excel-bestand bij "Definitieve versie RGS 3.x" (PB13)
RGS_KBASE = "https://www.referentiegrootboekschema.nl/kbase"
#: nieuwste definitieve versie bij het uitbrengen van deze versie van de app
RGS_CURRENT = "3.8"
#: tekenen van een alfa-/bètaversie (``rgs.prerelease_signs``)
PRERELEASE = {"filename": N_("de bestandsnaam"), "sheet": N_("de naam van het blad Totaal-RGS"),
              "recap": N_("de versietekst op het blad Recap")}
SHOW = {"open": N_("Zonder code"), "all": N_("Alle rekeningen")}
SEARCH_MAX = 40


# ------------------------------------------------------------------ Systeem > RGS-lijst

async def rgs_form(request: Request) -> FormData:
    size = request.headers.get("content-length")
    if size and size.isdigit() and int(size) > MAX_RGS + 2**20:
        raise HTTPException(413, _("Het bestand is te groot."))
    return await csrf_form(request)


def _list_page(ctx: Ctx, status: int = 200, **kw):
    lst = rgs.load_list(ctx.settings.data_dir)
    counts = None
    if lst:
        codes = list(lst.codes.values())
        counts = {"all": len(codes), "mkb": sum(1 for c in codes if c.mkb), "basis": sum(1 for c in codes if c.basis),
                  "history": ", ".join(sorted(lst.history)) or "-"}
    signs = ", ".join(_(PRERELEASE[k]) for k in (lst.prerelease if lst else []) if k in PRERELEASE)
    return render(ctx, "system/rgs.html", status, lst=lst, counts=counts, kbase=RGS_KBASE, current=RGS_CURRENT,
                  prerelease=signs, versions=rgs.list_versions(ctx.settings.data_dir), **kw)


@router.get("/systeem/rgs", name="rgs_list")
def rgs_list(ctx: Ctx = Depends(ctx_sysadmin)):
    ok = ctx.request.query_params.get("ok")
    notice = {"rgs_loaded": _("De RGS-lijst is ingelezen."),
              "rgs_deleted": _("De RGS-lijst is verwijderd.")}.get(ok or "")
    return _list_page(ctx, notice=notice)


def _mapped_per_company(ctx: Ctx, version: str) -> List[Dict]:
    """Administraties met koppelingen aan deze RGS-versie (voor de bevestiging); niet bereikbaar = overgeslagen."""
    out = []
    for c in ctx.conn.execute("SELECT * FROM company WHERE active ORDER BY name").fetchall():
        try:
            with connect_company(ctx.settings, c) as conn:
                n = rgs.mapped_count(conn, version)
        except psycopg.Error:
            continue
        if n:
            out.append({"name": c["name"], "n": n})
    return out


@router.post("/systeem/rgs/verwijderen", name="rgs_delete")
def rgs_delete(ctx: Ctx = Depends(ctx_sysadmin), form: FormData = Depends(csrf_form)):
    """Een ingelezen RGS-versie verwijderen (v1.0.1 keuze 10): eerst bevestigen, dan weg; koppelingen blijven."""
    version = str(form.get("versie", "")).strip()
    if version not in {v.version for v in rgs.list_versions(ctx.settings.data_dir)}:
        return _list_page(ctx, 404, error=_("Deze RGS-versie is niet ingelezen."))
    if form.get("action") != "confirmed":
        return _list_page(ctx, confirm=version, mapped=_mapped_per_company(ctx, version))
    rgs.delete_list(ctx.settings.data_dir, version)
    ctx.log("rgs_list_deleted", version=version)
    raise Redirect("/systeem/rgs?ok=rgs_deleted")


@router.post("/systeem/rgs", name="rgs_upload")
def rgs_upload(ctx: Ctx = Depends(ctx_sysadmin), form: FormData = Depends(rgs_form)):
    got = read_upload(form, "bestand", MAX_RGS)
    if not got:
        return _list_page(ctx, 400, error=_("Kies een bestand."))
    filename, data = got
    if len(data) > MAX_RGS:
        return _list_page(ctx, 413, error=_("Het bestand is groter dan {mb} MB.").format(mb=MAX_RGS // 2**20))
    try:
        lst = rgs.parse_xlsx(data, filename)
    except LedgerError as exc:
        return _list_page(ctx, 400, error=lib_message(exc))
    rgs.save_list(ctx.settings.data_dir, lst)
    ctx.log("rgs_list", version=lst.version, codes=len(lst.codes), filename=filename,
            prerelease=lst.prerelease or None)
    raise Redirect("/systeem/rgs?ok=rgs_loaded")


# ------------------------------------------------------------------ Beheer > RGS-koppeling

def _search(lst: rgs.RgsList, q: str) -> List[rgs.RgsCode]:
    q = (q or "").strip()
    if not q:
        return []
    if q in lst.codes:
        return [lst.codes[q]]
    words = rgs._norm(q).split()
    out = [c for c in lst.codes.values() if not c.inactive and
           (c.code.lower().startswith(q.lower()) or all(w in rgs._norm(c.short + " " + c.long) for w in words))]
    out.sort(key=lambda c: (c.level, c.sort or c.code))
    return out[:SEARCH_MAX]


def _rows(mapping: rgs.RgsMapping, lst: rgs.RgsList, show: str) -> List[Dict]:
    codes = mapping.codes()
    rows = []
    for acc in mapping.accounts():
        mine = codes.get(acc["id"], {})
        current = mine.get(lst.version)
        if show == "open" and (current or acc["closed"] or acc["charttype"] != "A"):
            continue
        prev = None if current else rgs.previous_code(mine, lst.version)
        sugg = rgs.suggest(acc, lst, 3, prev)
        if current and current not in [s.code for s in sugg]:
            sugg = sugg[:2]
        rows.append({**acc, "current": lst.codes.get(current) if current else None, "current_code": current,
                     "previous": prev, "suggestions": sugg})
    return rows


def _show(value) -> str:
    return value if value in SHOW else "open"


def _map_page(ctx: Ctx, show: str, q: str = "", status: int = 200, error: Optional[str] = None,
              saved: Optional[int] = None):
    require_role(ctx, "admin")
    lst = rgs.load_list(ctx.settings.data_dir)
    if not lst:
        return render(ctx, "am/rgs.html", status, lst=None, show=show, shows=SHOW)
    with _open(ctx) as conn:
        mapping = rgs.RgsMapping(conn, ctx.employee_login)
        rows = _rows(mapping, lst, show)
        # F20: zelfde telling als de overstaplijst (open boekbare rekeningen, geen kopregels)
        codes = mapping.codes()
        open_ids = [a["id"] for a in mapping.accounts() if a["charttype"] == "A" and not a["closed"]]
        total = len(open_ids)
        mapped = sum(1 for i in open_ids if lst.version in codes.get(i, {}))
    return render(ctx, "am/rgs.html", status, lst=lst, rows=rows, show=show, shows=SHOW, q=q, found=_search(lst, q),
                  categories=CATEGORIES, error=error, total=total, mapped=mapped,
                  notice=None if saved is None else _("1 koppeling opgeslagen.") if saved == 1
                  else _("{n} koppelingen opgeslagen.").format(n=saved))


@router.get("/beheer/rgs", name="rgs_mapping")
def rgs_mapping(ctx: Ctx = Depends(ctx_company)):
    qp = ctx.request.query_params
    saved = qp.get("opgeslagen")
    return _map_page(ctx, _show(qp.get("toon")), qp.get("zoek", ""), saved=int(saved) if saved and saved.isdigit() else None)


@router.post("/beheer/rgs", name="rgs_mapping_save")
def rgs_mapping_save(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    require_role(ctx, "admin")
    lst = rgs.load_list(ctx.settings.data_dir)
    if not lst:
        raise Redirect("/beheer/rgs")
    show = _show(str(form.get("toon", "")))
    choices: Dict[int, Optional[str]] = {}
    for v in form.getlist("acc"):
        if not str(v).isdigit():
            continue
        cid = int(v)
        pick = str(form.get(f"c_{cid}", "")).strip()
        if pick == "":
            continue                                    # niets gekozen: laten zoals het is
        if pick == "none":
            choices[cid] = None
        elif pick == "other":
            other = str(form.get(f"o_{cid}", "")).strip()
            if other:
                choices[cid] = other
        else:
            choices[cid] = pick
    with _open(ctx) as conn:
        mapping = rgs.RgsMapping(conn, ctx.employee_login)
        known = {a["id"] for a in mapping.accounts()}
        choices = {k: v for k, v in choices.items() if k in known}
        try:
            changed = mapping.save(lst.version, choices, lst)
        except LedgerError as exc:
            return _map_page(ctx, show, status=400, error=lib_message(exc))
    if changed:
        ctx.log("rgs_mapping", version=lst.version, changed=changed)
    raise Redirect(f"/beheer/rgs?toon={show}&opgeslagen={changed}")
