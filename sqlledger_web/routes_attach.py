"""Bijlagen in de webapp: boekingen (fase 4 stap 4e, IU6/D17), facturen met artikelen, orders/offertes,
relaties en artikelen (stap 6a, RB1).

- ``GET /bijlage/{archive_id}``: bekijken (pdf/jpg/png in de browser) of ``?download=1``; andere
  bestanden die SQL-Ledger wel toeliet alleen als download, nooit in de browser.
- ``GET /bijlage/tijdelijk/{token}``: een nog niet geboekt bestand (alleen voor wie het uploadde).
- ``POST /bijlagen/toevoegen`` en ``/bijlagen/{id}/verwijderen``: bij een opgeslagen boeking,
  meteen, zonder de boeking opnieuw te boeken (dus ook in een afgesloten periode).
- Hulpfuncties voor de boekingsschermen: een bestand bij een nieuwe boeking gaat als token mee
  in het formulier en wordt pas bij Opslaan een archiefstuk (zoals SL met referencetmpfile).
"""

from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import quote, urlencode

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from starlette.datastructures import FormData

from sqlledger._db import cursor
from sqlledger.amounts import format_amount
from sqlledger.attachments import (FORMNAMES, KINDS, MB, TYPES, Attachment, Attachments, Holder, detect_type,
                                   duplicates, max_size, record_exists)
from sqlledger.dates import format_date
from sqlledger.errors import NotFoundError, ValidationError

from .companies import connect_company
from .i18n import N_, gettext as _
from .libmsg import lib_message
from . import routes_gl
from .uploads import PendingFiles, read_upload
from .web import TEMPLATES, Ctx, Redirect, ctx_company, render, require_role, upload_form

router = APIRouter()

#: tabel -> scherm (orders/offertes: per soort)
SCREENS = {"ar": "/verkoop/{id}", "ap": "/inkoop/{id}", "gl": "/memoriaal/{id}", "customer": "/klanten/{id}",
           "vendor": "/leveranciers/{id}", "parts": "/artikelen/{id}"}
FORM_SCREENS = {"ar_invoice": "/verkoopfactuur/{id}", "ar_credit_invoice": "/verkoopfactuur/{id}",
                "credit_invoice": "/verkoopfactuur/{id}", "ap_invoice": "/inkoopfactuur/{id}",
                "ap_debit_invoice": "/inkoopfactuur/{id}", "debit_invoice": "/inkoopfactuur/{id}",
                "sales_order": "/verkooporder/{id}", "purchase_order": "/inkooporder/{id}",
                "sales_quotation": "/offerte/{id}", "request_quotation": "/offerteaanvraag/{id}"}
#: soort -> module die aan moet staan (zoals de schermen zelf)
FORM_MODULES = {"sales_order": ("oe",), "purchase_order": ("oe",), "sales_quotation": ("quotations",),
                "request_quotation": ("quotations",), "customer": ("ar",), "vendor": ("ap",),
                "gl": ("gl", "cash")}
TABLE_MODULES = {"ar": ("ar",), "ap": ("ap",), "parts": ("ic",)}
#: soort -> naam op het scherm ("ook bij Verkooporder 123")
FORM_LABELS = {"ar_transaction": N_("Verkoopboeking"), "credit_note": N_("Creditnota"),
               "ar_credit_note": N_("Creditnota"), "ar_invoice": N_("Verkoopfactuur"),
               "credit_invoice": N_("Creditfactuur"), "ar_credit_invoice": N_("Creditfactuur"),
               "ap_transaction": N_("Inkoopboeking"), "debit_note": N_("Debetnota"), "ap_debit_note": N_("Debetnota"),
               "ap_invoice": N_("Inkoopfactuur"), "debit_invoice": N_("Debetnota met artikelen"),
               "ap_debit_invoice": N_("Debetnota met artikelen"), "gl": N_("Memoriaalboeking"),
               "sales_order": N_("Verkooporder"), "purchase_order": N_("Inkooporder"),
               "sales_quotation": N_("Offerte"), "request_quotation": N_("Offerteaanvraag"),
               "customer": N_("Klant"), "vendor": N_("Leverancier"), "part": N_("Artikel"),
               "service": N_("Dienst"), "assembly": N_("Samengesteld artikel"), "labor": N_("Loon/overhead"),
               "kit": N_("Kit")}


def screen_url(formname: str, trans_id: int) -> Optional[str]:
    pattern = FORM_SCREENS.get(formname) or SCREENS.get(FORMNAMES.get(formname) or "")
    return pattern.format(id=trans_id) if pattern else None


def form_label(formname: str) -> str:
    return _(FORM_LABELS[formname]) if formname in FORM_LABELS else formname


def holder_text(ctx: Ctx, h: Holder) -> str:
    """"Inkoopboeking 2024-0815 (Leverancier X, € 121,00)" voor de waarschuwing bij een dubbel bestand."""
    extra = [h.name] if h.name and h.name != h.number else []
    if h.amount is not None:
        extra.append(f"{h.curr + ' ' if h.curr else ''}{format_amount(h.amount, ctx.user['numberformat'], 2)}"
                     .replace("&nbsp;", " "))
    return f"{form_label(h.formname)} {h.number}".strip() + (f" ({', '.join(extra)})" if extra else "")


TEMPLATES.env.globals["attach_url"] = screen_url
TEMPLATES.env.globals["attach_label"] = form_label
TEMPLATES.env.globals["attach_holder"] = holder_text


#: bestand in de browser: niets uitvoeren, alleen in een frame van onszelf
FILE_CSP = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'self'"


def store(ctx: Ctx) -> PendingFiles:
    return PendingFiles(ctx.settings.data_dir)


def owner(ctx: Ctx) -> str:
    """Eigenaar van een tijdelijk bestand: de app-gebruiker (niet de SL-login, die kan gedeeld zijn)."""
    return f"u{ctx.user['id']}"


def upload_limit(ctx: Ctx, conn=None) -> int:
    """Maximale uploadgrootte van de administratie in bytes (stap 4f, ``defaults.max_upload_size``),
    eenmaal per verzoek gelezen; zonder ``conn`` met een eigen verbinding."""
    state = ctx.request.state
    limit = getattr(state, "upload_limit", None)
    if limit is None:
        if conn is None:
            c, _att = _lib(ctx)
            with c, cursor(c) as cur:
                limit = max_size(cur)
        else:
            with cursor(conn) as cur:
                limit = max_size(cur)
        state.upload_limit = limit
    return limit


def upload_mb(ctx: Ctx) -> int:
    """Voor de sjablonen (sleepvak): de grens in MB."""
    return upload_limit(ctx) // MB


TEMPLATES.env.globals["upload_mb"] = upload_mb


def module_allowed(ctx: Ctx, formname: Optional[str]) -> bool:
    """Staat de module van deze soort bijlage aan? Onbekende soort (uit SQL-Ledger, zonder scherm): ja,
    bekijken mag dan zoals in rd.pl."""
    modules = ctx.company["modules"] or []
    need = FORM_MODULES.get(formname or "") or TABLE_MODULES.get(FORMNAMES.get(formname or "") or "")
    return need is None or any(m in modules for m in need)


def _lib(ctx: Ctx):
    try:
        conn = connect_company(ctx.settings, ctx.company)
    except psycopg.Error:
        raise HTTPException(503, _("De administratie is niet bereikbaar."))
    return conn, Attachments(conn, ctx.user_config())


def file_response(data: bytes, filename: str, download: bool) -> Response:
    kind = detect_type(data)
    inline = kind is not None and not download
    plain = re.sub(r"[^A-Za-z0-9._ -]", "_", filename) or "bijlage"
    disposition = f"{'inline' if inline else 'attachment'}; filename=\"{plain}\"; filename*=UTF-8''{quote(filename)}"
    return Response(data, media_type=TYPES[kind] if kind else "application/octet-stream",
                    headers={"Content-Disposition": disposition, "Content-Security-Policy": FILE_CSP,
                             "X-Frame-Options": "SAMEORIGIN"})


# ------------------------------------------------------------------ bekijken

@router.get("/bijlage/tijdelijk/{token}")
def pending_file(token: str, download: str = "", ctx: Ctx = Depends(ctx_company)):
    pf = store(ctx).get(token, ctx.company["id"], owner(ctx))
    if not pf:
        raise HTTPException(404, _("Bijlage niet gevonden."))
    return file_response(store(ctx).data(pf), pf.filename, download == "1")


@router.get("/bijlage/{archive_id}")
def archived_file(archive_id: int, download: str = "", ctx: Ctx = Depends(ctx_company)):
    conn, att = _lib(ctx)
    with conn:
        try:
            meta, data = att.get(archive_id)
        except NotFoundError:
            raise HTTPException(404, _("Bijlage niet gevonden."))
    if not module_allowed(ctx, meta.formname):
        raise HTTPException(403, _("Deze module staat uit voor deze administratie."))
    ctx.log("attach_viewed", archive_id=archive_id, trans_id=meta.trans_id)
    return file_response(data, meta.filename, download == "1")


# ------------------------------------------------------------------ toevoegen en verwijderen

@router.post("/bijlagen/toevoegen")
def add_file(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(upload_form)):
    formname = str(form.get("formname", ""))
    if not screen_url(formname, 0):
        raise HTTPException(400, _("Onbekende actie."))
    if not module_allowed(ctx, formname):
        raise HTTPException(403, _("Deze module staat uit voor deze administratie."))
    require_role(ctx, "bookkeeper")       # wie het scherm mag bewerken (RB6)
    tid = str(form.get("trans_id", ""))
    if not tid.isdigit():
        raise HTTPException(404, _("Boeking niet gevonden."))
    description = str(form.get("description", ""))
    confidential = form.get("confidential") == "1"
    files = store(ctx)
    conn, att = _lib(ctx)
    with conn:
        # bevestigd na de waarschuwing "hangt al elders" (RB5): het bestand staat dan tijdelijk klaar
        pf = files.get(str(form.get("token", "")), ctx.company["id"], owner(ctx)) if form.get("token") else None
        if pf:
            name, data = pf.filename, files.data(pf)
        else:
            up = read_upload(form, "bestand", upload_limit(ctx, conn))
            if not up:
                raise HTTPException(400, _("Kies een bestand."))
            name, data = up
        if form.get("dup_ok") != "1":
            try:
                if not pf:
                    pf = files.save(ctx.company["id"], owner(ctx), name, data, limit=upload_limit(ctx, conn))
            except ValidationError as exc:
                raise HTTPException(400, lib_message(exc))
            with cursor(conn) as cur:
                if not record_exists(cur, formname, int(tid)):
                    files.discard(pf.token)
                    raise HTTPException(404, _("Boeking niet gevonden."))
                d = duplicates(cur, ctx.user_config().short_login, pf.sha256, int(tid))
            if d.here:
                files.discard(pf.token)
                raise HTTPException(400, _("Dit bestand hangt hier al als bijlage."))
            if d:
                return render(ctx, "attach_confirm.html", d=d, pf=pf, trans_id=tid, formname=formname,
                              description=description, confidential=confidential)
            name, data = pf.filename, files.data(pf)
        try:
            a = att.add(int(tid), formname, name, data, description=description, confidential=confidential)
        except NotFoundError:
            raise HTTPException(404, _("Boeking niet gevonden."))
        except ValidationError as exc:
            raise HTTPException(400, lib_message(exc))
        finally:                     # F44: het tijdelijke bestand ook weg als koppelen mislukt
            if pf:
                files.discard(pf.token)
    ctx.log("attach_added", trans_id=a.trans_id, archive_id=a.archive_id, filename=a.filename, formname=formname,
            duplicate=form.get("dup_ok") == "1")
    raise Redirect(screen_url(formname, a.trans_id) + "?ok=attach_added")


@router.post("/bijlagen/annuleren")
def cancel_file(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(upload_form)):
    """"Annuleren" op de waarschuwing "hangt al elders" (F44): het tijdelijke bestand weggooien en terug naar het
    scherm van het record (vroeger een gewone link; het bestand bleef dan staan tot een volgende upload na 24 uur)."""
    formname, tid = str(form.get("formname", "")), str(form.get("trans_id", ""))
    pf = store(ctx).get(str(form.get("token", "")), ctx.company["id"], owner(ctx))
    if pf:
        store(ctx).discard(pf.token)
    url = screen_url(formname, int(tid)) if tid.isdigit() else None
    raise Redirect(url or "/")


@router.post("/bijlagen/{ref_id}/verwijderen")
def delete_file(ref_id: int, ctx: Ctx = Depends(ctx_company), form: FormData = Depends(upload_form)):
    require_role(ctx, "bookkeeper")
    conn, att = _lib(ctx)
    with conn:
        with cursor(conn) as cur:
            cur.execute("SELECT formname FROM reference WHERE id = %s", (ref_id,))
            row = cur.fetchone()
        formname = row["formname"] if row else ""
        if not screen_url(formname or "", 0):
            raise HTTPException(404, _("Bijlage niet gevonden."))
        if not module_allowed(ctx, formname):
            raise HTTPException(403, _("Deze module staat uit voor deze administratie."))
        try:
            visible = att.delete(ref_id)
        except NotFoundError:
            raise HTTPException(404, _("Bijlage niet gevonden."))
    ctx.log("attach_deleted", trans_id=visible.trans_id, archive_id=visible.archive_id, filename=visible.filename)
    raise Redirect(screen_url(formname, visible.trans_id) + "?ok=attach_deleted")


# ------------------------------------------------------------------ overzicht (stap 6c, RB3)

#: soort in het overzicht -> label (sleutels van attachments.KINDS + los/overig)
KIND_LABELS = {"verkoop": N_("Verkoop"), "inkoop": N_("Inkoop"), "memoriaal": N_("Memoriaal"),
               "orders": N_("Orders"), "offertes": N_("Offertes"), "klanten": N_("Klanten"),
               "leveranciers": N_("Leveranciers"), "artikelen": N_("Artikelen"),
               "los": N_("Los document (zonder koppeling)"), "overig": N_("Overig (uit SQL-Ledger)")}
OVERVIEW_LIMIT = 1000


def _date(text: str):
    from datetime import date
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


@router.get("/bijlagen")
def overview(ctx: Ctx = Depends(ctx_company)):
    """Rapporten > Bijlagen: alle bijlagen die je mag zien, met zoeken, link naar het record, bekijken en downloaden;
    losse archiefstukken (uit SQL-Ledger) ook, weggooien alleen door de beheerder."""
    if "reports" not in (ctx.company["modules"] or []):
        raise HTTPException(403, _("Deze module staat uit voor deze administratie."))
    q = ctx.request.query_params
    v = {k: (q.get(k) or "").strip() for k in ("text", "kind", "linked", "datefrom", "dateto", "zonderdatum")}
    if v["kind"] not in KIND_LABELS:
        v["kind"] = ""
    hide = [f for fs in KINDS.values() for f in fs if not module_allowed(ctx, f)]
    kinds = [(k, label) for k, label in KIND_LABELS.items()
             if k in ("los", "overig") or any(module_allowed(ctx, f) for f in KINDS[k])]
    conn, att = _lib(ctx)
    with conn:
        rows, total, hidden = att.overview(text=v["text"], kind=v["kind"], linked=v["linked"],
                                           datefrom=_date(v["datefrom"]), dateto=_date(v["dateto"]),
                                           hide_formnames=hide, limit=OVERVIEW_LIMIT, undated=v["zonderdatum"] == "1")
    # F43: "N bijlagen zonder datum niet getoond — tonen" / "— verbergen": zelfde zoekopdracht, zonderdatum aan/uit
    def toggle(on: bool) -> str:
        return "/bijlagen?" + urlencode({**{k: x for k, x in v.items() if x and k != "zonderdatum"},
                                         **({"zonderdatum": "1"} if on else {})})
    dated = bool(v["datefrom"] or v["dateto"])
    dateformat = ctx.user["dateformat"]
    return render(ctx, "attach_overview.html", v=v, rows=rows, total=total, limit=OVERVIEW_LIMIT, kinds=kinds,
                  hidden=hidden, show_undated=toggle(True) if hidden else "",
                  hide_undated=toggle(False) if dated and v["zonderdatum"] == "1" else "",
                  is_admin=ctx.has_role("admin"),
                  when=lambda d: f"{format_date(d.date(), dateformat)} {d:%H:%M}" if d else "")


@router.post("/bijlagen/los/{archive_id}/verwijderen")
def delete_loose(archive_id: int, ctx: Ctx = Depends(ctx_company), form: FormData = Depends(upload_form)):
    require_role(ctx, "admin")
    conn, att = _lib(ctx)
    with conn:
        try:
            name = att.delete_loose(archive_id)
        except NotFoundError:
            raise HTTPException(404, _("Bijlage niet gevonden."))
    ctx.log("attach_deleted", archive_id=archive_id, filename=name, loose=True)
    raise Redirect("/bijlagen?ok=attach_deleted")


# ------------------------------------------------------------------ voor de boekingsschermen

def saved_attachments(ctx: Ctx, conn, trans_id: Optional[int]) -> List[Attachment]:
    if not trans_id:
        return []
    return Attachments(conn, ctx.user_config()).list(trans_id)


def check_pending(ctx: Ctx, refs: List["routes_gl.RefRow"], form: Optional[FormData] = None) -> None:
    """Tokens uit het formulier nakijken: alleen eigen, bestaande bestanden; naam van de server; het vinkje
    "toch toevoegen" (RB5) uit ``form``."""
    keep = []
    for r in refs:
        if r.token:
            pf = store(ctx).get(r.token, ctx.company["id"], owner(ctx))
            if not pf:
                continue
            r.filename = pf.filename
            r.description = r.description or pf.filename
            if form is not None:
                r.dup_ok = form.get(f"dup_ok_{r.token}") == "1"
        keep.append(r)
    refs[:] = keep


def mark_duplicates(ctx: Ctx, conn, refs: List["routes_gl.RefRow"]) -> None:
    """Bij elk nog niet opgeslagen bestand: hangt hetzelfde bestand al elders (RB5)? Vult ``dups``."""
    for r in refs:
        if r.token:
            pf = store(ctx).get(r.token, ctx.company["id"], owner(ctx))
            if pf:
                with cursor(conn) as cur:
                    r.dups = duplicates(cur, ctx.user_config().short_login, pf.sha256) or None


def take_upload(ctx: Ctx, form: FormData, refs: List["routes_gl.RefRow"], name: str = "bijlage") -> Optional[str]:
    """Gekozen/gesleept bestand bij een nieuwe boeking tijdelijk bewaren; geeft een foutmelding."""
    limit = upload_limit(ctx)
    up = read_upload(form, name, limit)
    if not up:
        return None
    try:
        pf = store(ctx).save(ctx.company["id"], owner(ctx), *up, limit=limit)
    except ValidationError as exc:
        return lib_message(exc)
    for r in refs:                      # twee keer hetzelfde bestand bij één boeking: weigeren (RB5)
        other = store(ctx).get(r.token, ctx.company["id"], owner(ctx)) if r.token else None
        if other and other.sha256 == pf.sha256:
            store(ctx).discard(pf.token)
            return _("Dit bestand staat al bij de bijlagen.")
    refs.append(routes_gl.RefRow(description=pf.filename, token=pf.token, filename=pf.filename))
    return None


def drop_pending(ctx: Ctx, form: FormData, refs: List["routes_gl.RefRow"]) -> bool:
    """Knop "weghalen" bij een nog niet opgeslagen bijlage."""
    token = str(form.get("detach", ""))
    if not token:
        return False
    if token.startswith("a") and token[1:].isdigit():     # meegekomen uit de order/offerte (RB2)
        refs[:] = [r for r in refs if r.token or r.archive_id != token[1:]]
        return True
    if any(r.token == token for r in refs):
        refs[:] = [r for r in refs if r.token != token]
        store(ctx).discard(token)
    return True


def resolve_pending(ctx: Ctx, refs: List["routes_gl.RefRow"]) -> Optional[str]:
    """Vlak voor het boeken: inhoud van de tijdelijke bestanden erbij; een bestand dat al elders hangt alleen
    met "toch toevoegen" (RB5; ``mark_duplicates`` vult ``dups``)."""
    for r in refs:
        if r.token and r.dups and not r.dup_ok:
            return _("Het bestand {name} hangt al bij een andere boeking of relatie; vink \"toch toevoegen\" aan of "
                     "haal het weg.").format(name=r.filename)
    for r in refs:
        if r.token:
            pf = store(ctx).get(r.token, ctx.company["id"], owner(ctx))
            if not pf:
                return _("De bijlage {name} is niet meer beschikbaar; voeg hem opnieuw toe.").format(name=r.filename)
            r.data = store(ctx).data(pf)
    return None


def discard_pending(ctx: Ctx, refs: List["routes_gl.RefRow"]) -> None:
    for r in refs:
        if r.token:
            store(ctx).discard(r.token)
