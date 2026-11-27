"""Beheer > Sjablonen (fase 4, D5/DS11): eigen documentsjablonen per administratie en variant.

Downloaden (eigen of standaard als startpunt), uploaden (eerst gecontroleerd: opbouw in de sandbox en een
proef-PDF met het laatste document van die soort), terug naar standaard, vorige versies terugzetten,
voorbeeld-PDF, logo, varianten. Geen bewerken in de browser. Elke wijziging in het logboek van de app en
in de audittrail van de administratie.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from starlette.datastructures import FormData, UploadFile

from sqlledger import _db
from sqlledger.errors import NotFoundError

from . import doctypes
from .documents import MAX_LOGO, MAX_TEMPLATE, PARTIALS, TemplateError, TemplateStore, VARIANT_RE
from .i18n import N_, gettext as _
from .routes_arap import _lib
from .routes_docs import print_data, renderer, safe_filename
from .web import Ctx, Redirect, csrf_form, ctx_company, render, require_role

router = APIRouter()

#: namen van de gedeelde delen
PARTIAL_LABELS = {"_base": N_("Basis (kop, voet, opmaak)"), "_invoice": N_("Facturen (gedeeld deel)"),
                  "_order": N_("Orders en offertes (gedeeld deel)"),
                  "_transaction": N_("Zonder artikelen (gedeeld deel)"), "_parties": N_("Adresblok"),
                  "_lines": N_("Regeltabel en gewichtsoverzicht (gedeeld deel)"),
                  "_reminder": N_("Aanmaningen (gedeeld deel)"),
                  "_payment": N_("Betalingsspecificaties (gedeeld deel)")}

OK = {"saved": N_("Sjabloon opgeslagen."), "reset": N_("Terug naar de standaard."),
      "restored": N_("Vorige versie teruggezet."), "logo": N_("Logo opgeslagen."), "logo_deleted": N_("Logo verwijderd."),
      "variant": N_("Variant toegevoegd."), "variant_removed": N_("Variant verwijderd.")}


def _store(ctx: Ctx) -> TemplateStore:
    return TemplateStore(renderer(ctx), doctypes.TEMPLATE_KINDS)


def _variant(value: Optional[str]) -> str:
    v = (value or "").strip()
    if v and not VARIANT_RE.match(v):
        raise HTTPException(400, _("Ongeldige sjabloonvariant."))
    return v


def _msg(exc: TemplateError) -> str:
    return _(exc.text).format(**exc.values) if exc.values else _(exc.text)


def _page(ctx: Ctx, variant: str, status: int = 200, **kw):
    store = _store(ctx)
    conn, lib = _lib(ctx)
    with conn:
        languages = [{"code": c, "label": d or c} for c, d in
                     conn.execute("SELECT code, description FROM language ORDER BY code").fetchall()]
    variants = store.r.variants()
    if variant and variant not in variants:
        variant = ""
    rows = []
    for key in list(doctypes.TEMPLATE_KINDS) + list(PARTIALS):
        rows.append({"name": key, "label": PARTIAL_LABELS.get(key) or doctypes.long_label(key),
                     "partial": key in PARTIALS, "own": store.own(key, variant),
                     "own_base": bool(variant) and store.own(key, ""), "versions": store.versions(key, variant)})
    logo = store.logo_file()
    ok = ctx.request.query_params.get("ok") or ""
    if ok in OK:
        kw.setdefault("notice", _(OK[ok]))
    return render(ctx, "am/doctemplates.html", status, rows=rows, variant=variant, variants=variants,
                  languages=[l for l in languages if l["code"] not in variants and VARIANT_RE.match(l["code"])],
                  logo=store.r.logo() if logo else "", logo_name=logo.name if logo else "",
                  max_kb=MAX_TEMPLATE // 1024, max_logo_kb=MAX_LOGO // 1024, **kw)


def _sample(ctx: Ctx, conn, lib, name: str, variant: str, number: str = ""):
    """(soort, variabelen) van een document om een voorbeeld of proef-PDF mee te maken, of (None, None).
    Brieven per klant (aanmaning, rekeningoverzicht) uit de stand van vandaag (stap 4a), betalingsspecificatie
    van de laatst geboekte betaling (stap 4b)."""
    kind = doctypes.PARTIAL_KIND.get(name, name)
    if kind in doctypes.LETTERS:
        from .routes_dunning import sample_vars
        v = sample_vars(ctx, conn, lib, kind)
        return (kind, v) if v is not None else (None, None)
    if kind in doctypes.PAYMENT_DOCS:
        from .routes_payspec import sample_vars as payment_sample
        v = payment_sample(ctx, conn, lib, kind)
        return (kind, v) if v is not None else (None, None)
    loc = doctypes.find_document(conn, name, number)
    if loc is None:
        return None, None
    return loc.kind.key, doctypes.print_vars(print_data(ctx, conn, lib), loc, variant or None)


@router.get("/beheer/sjablonen", name="doctemplates")
def doctemplates(ctx: Ctx = Depends(ctx_company)):
    require_role(ctx, "admin")
    return _page(ctx, _variant(ctx.request.query_params.get("variant")))


@router.get("/beheer/sjablonen/bestand")
def download(request: Request, ctx: Ctx = Depends(ctx_company)):
    """Sjabloon downloaden: het eigen bestand, anders de standaard (startpunt), of een vorige versie."""
    require_role(ctx, "admin")
    q = request.query_params
    name, variant, stamp = q.get("naam") or "", _variant(q.get("variant")), q.get("versie") or ""
    store = _store(ctx)
    try:
        text = store.version_text(name, variant, stamp) if stamp else store.source(name, variant)
    except TemplateError as exc:
        raise HTTPException(404, _msg(exc))
    fname = safe_filename(f"{name}{'-' + variant if variant else ''}{'-' + stamp if stamp else ''}") + ".html"
    return Response(text.encode("utf-8"), media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"', "Cache-Control": "no-store",
                             "X-Content-Type-Options": "nosniff"})


@router.get("/beheer/sjablonen/voorbeeld")
def preview(request: Request, ctx: Ctx = Depends(ctx_company)):
    """Voorbeeld-PDF met het laatste document van die soort (of op nummer); zet geen vinkje 'geprint'."""
    require_role(ctx, "admin")
    q = request.query_params
    name, variant, number = q.get("naam") or "", _variant(q.get("variant")), (q.get("nummer") or "").strip()
    store = _store(ctx)
    try:
        store.check(name, variant)
    except TemplateError as exc:
        raise HTTPException(404, _msg(exc))
    conn, lib = _lib(ctx)
    with conn:
        try:
            kind, v = _sample(ctx, conn, lib, name, variant, number)
        except NotFoundError:
            raise HTTPException(404, _("Document niet gevonden."))
        if kind is None:
            raise HTTPException(404, _("Geen document gevonden om een voorbeeld van te maken."))
    v = dict(v, language_code=variant or v.get("language_code") or "")
    try:
        pdf = store.r.render_pdf(kind, v)
    except Exception as exc:                        # fout in een eigen sjabloon: laten zien, niet 500
        raise HTTPException(400, _("Het sjabloon gaf een fout: {fout}").format(fout=f"{type(exc).__name__}: {exc}"[:300]))
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{safe_filename(name)}-voorbeeld.pdf"',
                             "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


async def upload_form(request: Request) -> FormData:
    size = request.headers.get("content-length")
    if size and size.isdigit() and int(size) > MAX_LOGO + 64 * 1024:
        raise HTTPException(413, _("Het bestand is te groot."))
    return await csrf_form(request)


def _read(upload, limit: int) -> Optional[bytes]:
    if not isinstance(upload, UploadFile) or not upload.filename:
        return None
    return upload.file.read(limit + 1)


def _audit(ctx: Ctx, action: str, name: str, variant: str) -> None:
    """Audittrail van de administratie (als die aan staat) + logboek van de app."""
    conn, lib = _lib(ctx)
    with conn, _db.cursor(conn) as cur:
        if _db.get_defaults(cur, "audittrail").get("audittrail"):
            _, employee_id = _db.get_employee(cur, lib.cfg.short_login)
            cur.execute("""INSERT INTO audittrail (trans_id, tablename, reference, formname, action, employee_id)
                           VALUES (NULL, 'templates', %s, %s, %s, %s)""",
                        (f"{variant}/{name}" if variant else name, name, action, employee_id))
    ctx.log(f"doctemplate_{action}", name=name, variant=variant)


@router.post("/beheer/sjablonen")
def doctemplates_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(upload_form)):
    require_role(ctx, "admin")
    action = str(form.get("action") or "")
    variant = _variant(str(form.get("variant") or ""))
    name = str(form.get("naam") or "")
    store = _store(ctx)
    back = f"/beheer/sjablonen?variant={variant}" if variant else "/beheer/sjablonen"
    try:
        if action == "upload":
            store.check(name, variant)
            data = _read(form.get("bestand"), MAX_TEMPLATE)
            if data is None:
                return _page(ctx, variant, 400, error=_("Kies een bestand."))
            text = store.decode(data)
            conn, lib = _lib(ctx)
            with conn:
                kind, sample = _sample(ctx, conn, lib, name, variant)
                if sample is not None:
                    sample = dict(sample, language_code=variant)
            note = store.validate(name, variant, text, sample, kind)
            store.save(name, variant, text)
            _audit(ctx, "saved", name, variant)
            if note:
                return _page(ctx, variant, notice=_("Sjabloon opgeslagen.") + " " + _(note))
            raise Redirect(back + ("&" if "?" in back else "?") + "ok=saved")
        if action == "reset":
            store.check(name, variant)
            return _page(ctx, variant, confirm={"action": "reset_confirmed", "naam": name})
        if action == "reset_confirmed":
            if store.reset(name, variant):
                _audit(ctx, "reset", name, variant)
            raise Redirect(back + ("&" if "?" in back else "?") + "ok=reset")
        if action == "restore":
            store.restore(name, variant, str(form.get("versie") or ""))
            _audit(ctx, "restored", name, variant)
            raise Redirect(back + ("&" if "?" in back else "?") + "ok=restored")
        if action == "logo":
            data = _read(form.get("logo"), MAX_LOGO)
            if data is None:
                return _page(ctx, variant, 400, error=_("Kies een bestand."))
            store.save_logo(data)
            _audit(ctx, "logo", "logo", "")
            raise Redirect(back + ("&" if "?" in back else "?") + "ok=logo")
        if action == "logo_delete":
            if store.delete_logo():
                _audit(ctx, "logo_deleted", "logo", "")
            raise Redirect(back + ("&" if "?" in back else "?") + "ok=logo_deleted")
        if action == "variant_add":
            code = _variant(str(form.get("code") or ""))
            if not code:
                return _page(ctx, variant, 400, error=_("Kies een taal."))
            store.add_variant(code)
            _audit(ctx, "variant", code, code)
            raise Redirect(f"/beheer/sjablonen?variant={code}&ok=variant")
        if action == "variant_remove":                      # eerst bevestigen (F26 A)
            return _page(ctx, variant, confirm={"action": "variant_remove_confirmed", "naam": variant,
                                                "versions": store.removable_variant(variant)})
        if action == "variant_remove_confirmed":
            store.remove_variant(variant)
            _audit(ctx, "variant_removed", variant, variant)
            raise Redirect("/beheer/sjablonen?ok=variant_removed")
    except TemplateError as exc:
        return _page(ctx, variant, 400, error=_msg(exc), failed=name)
    raise HTTPException(400, _("Onbekende actie."))
