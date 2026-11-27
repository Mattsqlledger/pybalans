"""Beheer > Huisstijl (HUISSTIJL_ANALYSE.md HS1–HS12): logo, kleur, lettertype en ligging van de documenten,
zonder HTML. Opslag en controle in ``sqlledger.docstyle``; het logo is hetzelfde bestand als bij Sjablonen
(``TemplateStore.save_logo``).

Het live voorbeeld (HS6) is een eigen adres ``/beheer/huisstijl/voorbeeld`` met de instellingen in de querystring
(nog niet opgeslagen), zodat het in een kader kan staan met een eigen, strenge CSP: de app zelf staat geen inline
opmaak toe en een ``srcdoc``-kader zou die regels erven. "PDF bekijken" is hetzelfde adres met ``pdf=1``.

"Overnemen van …" (HS7): administraties waar de gebruiker ook beheerder is; laden zet de instellingen en het logo
van die administratie in het formulier en het voorbeeld (nog niets opgeslagen); opslaan kopieert ``style.*`` en het
logobestand (geen logo daar = eigen logo weg). Alleen beheerders (HS8).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.datastructures import FormData

from sqlledger import _db, docsettings, docstyle
from sqlledger.docstyle import DocStyle
from sqlledger.errors import NotFoundError, ValidationError

from . import docfonts, doctypes
from .companies import connect_company
from .documents import MAX_LOGO, PARTIALS, DocumentRenderer, TemplateError, TemplateStore
from .i18n import N_, gettext as _
from .routes_arap import _lib
from .routes_docs import renderer, safe_filename
from .routes_tpl import _audit, _msg, _read, _sample, upload_form
from .web import Ctx, Redirect, ctx_company, ctx_user, render, require_role

router = APIRouter()

#: CSP van het voorbeeld in het kader: alleen eigen lettertypen, plaatjes als data-URL, inline opmaak; geen scripts
PREVIEW_CSP = ("default-src 'none'; img-src data:; font-src 'self'; style-src 'unsafe-inline'; "
               "frame-ancestors 'self'; base-uri 'none'; form-action 'none'")
FONT_URL = "/lettertypen/"

LOGO_SIZE_LABELS = {"small": N_("Klein"), "medium": N_("Middel"), "large": N_("Groot")}
POSITION_LABELS = {"left": N_("Links"), "center": N_("Midden"), "right": N_("Rechts")}
SWATCH_LABELS = {"zwart": N_("Zwart"), "donkerblauw": N_("Donkerblauw"), "petrol": N_("Petrol"),
                 "donkergroen": N_("Donkergroen"), "bordeaux": N_("Bordeaux"), "antraciet": N_("Antraciet")}
NOTICES = {"saved": N_("Huisstijl opgeslagen."), "logo": N_("Logo opgeslagen."), "logo_deleted": N_("Logo verwijderd.")}

#: voorbeeldfactuur als er nog geen verkoopfactuur is (keuze 5b), per documenttaal
SAMPLE_TEXTS = {
    "nl": {"name": "Voorbeeldklant B.V.", "address1": "Voorbeeldstraat 1", "zipcode": "1234 AB", "city": "Voorbeeldstad",
           "lines": ("Advieswerk", "Artikel A", "Verzendkosten"), "units": ("uur", "stuks", ""),
           "number": "VOORBEELD", "tax": "Btw"},
    "en": {"name": "Sample Customer Ltd", "address1": "1 Sample Street", "zipcode": "AB1 2CD", "city": "Sampleton",
           "lines": ("Consultancy", "Item A", "Shipping"), "units": ("hours", "pcs", ""),
           "number": "SAMPLE", "tax": "VAT"},
    "de": {"name": "Musterkunde GmbH", "address1": "Musterstraße 1", "zipcode": "12345", "city": "Musterstadt",
           "lines": ("Beratung", "Artikel A", "Versandkosten"), "units": ("Std.", "Stück", ""),
           "number": "MUSTER", "tax": "MwSt."},
    "fr": {"name": "Client Exemple SARL", "address1": "1 rue de l'Exemple", "zipcode": "75001", "city": "Exempleville",
           "lines": ("Conseil", "Article A", "Frais de port"), "units": ("h", "pcs", ""),
           "number": "EXEMPLE", "tax": "TVA"},
}


# ================================================================== instellingen uit formulier of querystring

def style_from(values: Any) -> DocStyle:
    """DocStyle uit formulier- of querywaarden (onbekende waarden blijven staan; ``validate`` keurt ze af)."""
    def get(name: str, default: str = "") -> str:
        return str(values.get(name) or default).strip()

    return DocStyle(logo_size=get("logo_size", "medium"), logo_pos=get("logo_pos", "left"),
                    color=docstyle.normalize_color(get("color", docstyle.DEFAULT_COLOR)) or get("color"),
                    font=get("font"),
                    address_pos=get("address_pos", "left"), show_head=get("show_head", "1") == "1",
                    show_footer=get("show_footer", "1") == "1", saved=True)


def _admin_companies(ctx: Ctx) -> List[Dict[str, Any]]:
    """Andere administraties waar de gebruiker beheerder is (HS7)."""
    return ctx.conn.execute(
        "SELECT c.id, c.name, c.dbname, c.dbhost, c.dbport FROM membership m JOIN company c ON c.id = m.company_id "
        "WHERE m.user_id = %s AND m.role = 'admin' AND c.active AND c.id <> %s ORDER BY c.name",
        (ctx.user["id"], ctx.company["id"])).fetchall()


def _source_company(ctx: Ctx, value: Any) -> Optional[Dict[str, Any]]:
    """De gekozen bron-administratie, alleen als de gebruiker daar beheerder is; anders 403."""
    text = str(value or "").strip()
    if not text:
        return None
    if not text.isdigit():
        raise HTTPException(400, _("Onbekende administratie."))
    for c in _admin_companies(ctx):
        if c["id"] == int(text):
            return c
    raise HTTPException(403, _("U bent geen beheerder van die administratie."))


def preview_query(s: DocStyle, source: Optional[Dict[str, Any]] = None) -> str:
    q = {"logo_size": s.logo_size, "logo_pos": s.logo_pos, "color": s.color, "font": s.font,
         "address_pos": s.address_pos, "show_head": "1" if s.show_head else "0",
         "show_footer": "1" if s.show_footer else "0"}
    if source:
        q["bron"] = str(source["id"])
    return urlencode(q)


def _company_renderer(ctx: Ctx, company_id: int) -> DocumentRenderer:
    return DocumentRenderer(ctx.settings.data_dir, company_id)


def _own_templates(store: TemplateStore) -> Dict[str, Any]:
    """Eigen sjablonen (HS5): ``base`` = eigen basissjabloon; ``kinds`` = [(label, gebruikt huisstijl)]."""
    out: Dict[str, Any] = {"base": None, "kinds": []}
    for variant in [""] + store.r.variants():
        for name in tuple(PARTIALS) + tuple(doctypes.TEMPLATE_KINDS):
            try:
                if not store.own(name, variant):
                    continue
                uses = "style." in store.source(name, variant)
            except TemplateError:
                continue
            if name == "_base":
                out["base"] = out["base"] or uses
                continue
            label = _(doctypes.long_label(name)) if name in doctypes.TEMPLATE_KINDS else f"{name}.html"
            out["kinds"].append({"label": label + (f" ({_('variant')} {variant})" if variant else ""), "uses": uses})
    if out["base"] is False:
        out["base"] = "own"
    elif out["base"] is True:
        out["base"] = "uses"
    return out


def _page(ctx: Ctx, conn, s: DocStyle, status: int = 200, *, source: Optional[Dict[str, Any]] = None, **kw):
    store = TemplateStore(renderer(ctx, conn), doctypes.TEMPLATE_KINDS)
    logo_r = _company_renderer(ctx, source["id"]) if source else store.r
    footer_text = any(docsettings.load(conn).text(lang, "footer") for lang in ("nl", "en", "de", "fr"))
    ok = ctx.request.query_params.get("ok")
    if ok in NOTICES:
        kw.setdefault("notice", _(NOTICES[ok]))
    fonts = [{"key": "", "label": _("Standaard (zoals nu)"), "family": docfonts.STANDARD_FAMILY}] + [
        {"key": k, "label": f.label, "family": docfonts.family(k)} for k, f in docfonts.FONTS.items()]
    return render(ctx, "am/style.html", status, s=s, source=source, companies=_admin_companies(ctx),
                  logo=logo_r.logo(), own_logo=bool(store.logo_file()), max_logo_kb=MAX_LOGO // 1024,
                  sizes=LOGO_SIZE_LABELS, positions=POSITION_LABELS, fonts=fonts,
                  swatches=[(c, SWATCH_LABELS[n]) for c, n in docstyle.SWATCHES],
                  own=_own_templates(store), footer_text=footer_text, min_contrast=docstyle.MIN_CONTRAST,
                  default=DocStyle(), preview_qs=preview_query(s, source), **kw)


def _wants_json(ctx: Ctx) -> bool:
    """Logo opslaan via fetch (static/style.js, PB11): antwoord als JSON in plaats van een nieuwe pagina."""
    return "application/json" in ctx.request.headers.get("accept", "")


# ================================================================== scherm

@router.get("/beheer/huisstijl", name="docstyle")
def style_page(ctx: Ctx = Depends(ctx_company)):
    require_role(ctx, "admin")
    conn, lib = _lib(ctx)
    with conn:
        return _page(ctx, conn, docstyle.load(conn))


@router.post("/beheer/huisstijl")
def style_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(upload_form)):
    require_role(ctx, "admin")
    action = str(form.get("action") or "save")
    conn, lib = _lib(ctx)
    with conn:
        store = TemplateStore(renderer(ctx, conn), doctypes.TEMPLATE_KINDS)
        if action in ("logo", "logo_delete"):
            quick = action == "logo" and _wants_json(ctx)
            try:
                if action == "logo":
                    data = _read(form.get("logo"), MAX_LOGO)
                    if data is None:
                        if quick:
                            return JSONResponse({"ok": False, "error": _("Kies een bestand.")}, 400)
                        return _page(ctx, conn, style_from(form), 400, error=_("Kies een bestand."))
                    store.save_logo(data)
                    _audit(ctx, "logo", "logo", "")
                elif store.delete_logo():
                    _audit(ctx, "logo_deleted", "logo", "")
            except TemplateError as exc:
                if quick:
                    return JSONResponse({"ok": False, "error": _msg(exc)}, 400)
                return _page(ctx, conn, style_from(form), 400, error=_msg(exc))
            if quick:
                # PB11: meteen opgeslagen bij kiezen of slepen; het scherm ververst miniatuur en voorbeeld zelf
                return JSONResponse({"ok": True, "notice": _(NOTICES["logo"]), "logo": store.r.logo()})
            raise Redirect(f"/beheer/huisstijl?ok={action if action == 'logo' else 'logo_deleted'}")
        if action == "load":
            source = _source_company(ctx, form.get("bron"))
            if source is None:
                return _page(ctx, conn, style_from(form), 400, error=_("Kies een administratie."))
            try:
                with connect_company(ctx.settings, source) as other:
                    s = docstyle.load(other)
            except psycopg.Error:
                return _page(ctx, conn, docstyle.load(conn), 503,
                             error=_("Administratie {naam} is niet bereikbaar.").format(naam=source["name"]))
            s.saved = True
            return _page(ctx, conn, s, source=source)
        if action != "save":
            raise HTTPException(400, _("Onbekende actie."))
        source = _source_company(ctx, form.get("bron"))
        s = style_from(form)
        errors = docstyle.validate(s)
        if errors:
            return _page(ctx, conn, s, 400, source=source, error=" ".join(_(e) for e in errors))
        if source is not None:
            src = _company_renderer(ctx, source["id"])
            src_store = TemplateStore(src, doctypes.TEMPLATE_KINDS)
            logo = src_store.logo_file()
            try:
                if logo is not None:
                    store.save_logo(logo.read_bytes())
                    _audit(ctx, "logo", "logo", "")
                elif store.delete_logo():
                    _audit(ctx, "logo_deleted", "logo", "")
            except TemplateError as exc:
                return _page(ctx, conn, s, 400, source=source, error=_msg(exc))
        try:
            changed = docstyle.save(conn, s, login=lib.cfg.login, source=source["name"] if source else "")
        except ValidationError as exc:
            return _page(ctx, conn, s, 400, source=source, error=_(str(exc)))
    ctx.log("docstyle_saved", keys=changed, source=source["id"] if source else None)
    raise Redirect("/beheer/huisstijl?ok=saved")


# ================================================================== voorbeeld (HS6)

def _sample_vars(conn, lang: str) -> Dict[str, Any]:
    """Voorbeeldfactuur met de eigen bedrijfsgegevens als er nog geen verkoopfactuur is (keuze 5b)."""
    tx = SAMPLE_TEXTS.get(lang, SAMPLE_TEXTS["nl"])
    with _db.cursor(conn) as cur:
        d = _db.get_defaults(cur, "company", "address", "tel", "fax", "companyemail", "companywebsite",
                             "businessnumber")
        cur.execute("""SELECT t.rate FROM chart c JOIN tax t ON (t.chart_id = c.id)
                       WHERE c.link LIKE '%%AR_tax%%' AND t.validto IS NULL ORDER BY c.accno LIMIT 1""")
        r = cur.fetchone()
        rate = float(r["rate"] or 0) if r else 0.0
        cur.execute("""SELECT bk.iban, bk.bic FROM chart c JOIN bank bk ON (bk.id = c.id)
                       WHERE c.link LIKE '%%AR_paid%%' AND coalesce(bk.iban, '') <> '' ORDER BY c.accno LIMIT 1""")
        bank = cur.fetchone() or {}
    sep, th = (".", ",") if lang == "en" else (",", ".")

    def amt(x: float) -> str:
        whole, frac = f"{x:,.2f}".split(".")
        return whole.replace(",", th) + sep + frac

    lines = [(tx["lines"][0], 4, 85.00, tx["units"][0], "A-100"), (tx["lines"][1], 2, 12.50, tx["units"][1], "A-200"),
             (tx["lines"][2], 1, 7.95, tx["units"][2], "")]
    subtotal = sum(q * p for _, q, p, _, _ in lines)
    tax = round(subtotal * rate, 2)
    n = len(lines)
    v: Dict[str, Any] = {k: str(d.get(k) or "") for k in ("company", "address", "tel", "fax", "companyemail",
                                                         "companywebsite", "businessnumber")}
    v.update({
        "invnumber": tx["number"], "invdate": "", "duedate": "", "ordnumber": "", "ponumber": "",
        "name": tx["name"], "address1": tx["address1"], "address2": "", "zipcode": tx["zipcode"], "city": tx["city"],
        "state": "", "country": "", "contact": "", "currency": "EUR", "language_code": lang if lang != "nl" else "",
        "runningnumber": [str(i + 1) for i in range(n)], "number": [x[4] for x in lines],
        "description": [x[0] for x in lines], "qty": [str(x[1]) for x in lines], "unit": [x[3] for x in lines],
        "sellprice": [amt(x[2]) for x in lines], "linetotal": [amt(x[1] * x[2]) for x in lines],
        "discountrate": [""] * n, "deliverydate": [""] * n, "serialnumber": [""] * n, "itemnotes": [""] * n,
        "bom": [""] * n, "subtotal": amt(subtotal), "invtotal": amt(subtotal + tax), "total": amt(subtotal + tax),
        "paid": "", "iban": bank.get("iban") or "", "bic": bank.get("bic") or "", "dcn": "", "vc": "customer",
    })
    if tax:
        pct = f"{rate * 100:g}"
        v.update({"taxdescription": [f"{tx['tax']} {pct}%"], "taxbase": [amt(subtotal)], "tax": [amt(tax)],
                  "taxrate": [pct], "taxnumber": [""], "taxbaseinclusive": [amt(subtotal + tax)]})
    return v


#: alleen in het browservoorbeeld: A4-blad met marges, voetregel onderaan, envelopvenster als stippellijn
PREVIEW_CSS = """<style>
  html { background: #fff; }
  body { width: 210mm; min-height: 297mm; padding: 18mm 16mm 24mm 20mm; margin: 0; position: relative;
         box-sizing: border-box; }
  #pagefooter { position: absolute; left: 20mm; right: 16mm; bottom: 12mm; }
  #sl-pagenr { position: absolute; right: 16mm; bottom: 12mm; font-size: 8pt; color: #555; }
  #sl-window { position: absolute; top: 45mm; width: 90mm; height: 45mm; border: 1px dashed #c0392b;
               pointer-events: none; }
  #sl-window span { position: absolute; top: -4.2mm; left: 0; font: 7pt sans-serif; color: #c0392b; }
</style>
"""


def _preview_html(html: str, s: DocStyle, label: str) -> str:
    left = "left: 20mm" if s.address_pos == "left" else "right: 16mm"
    overlay = (f'<div id="sl-window" style="{left}" aria-hidden="true"><span>{label}</span></div>'
               '<div id="sl-pagenr">1 / 1</div>')
    html = html.replace("</head>", PREVIEW_CSS + "</head>", 1)
    return html.replace("<body>", "<body>" + overlay, 1)


@router.get("/beheer/huisstijl/voorbeeld")
def style_preview(request: Request, ctx: Ctx = Depends(ctx_company)):
    """Voorbeeld met de (nog niet opgeslagen) instellingen uit de querystring; ``pdf=1`` = de echte PDF.
    Zet niets op 'geprint' en schrijft niets in het logboek."""
    require_role(ctx, "admin")
    q = request.query_params
    source = _source_company(ctx, q.get("bron"))
    s = style_from(q)
    if docstyle.validate(s, contrast=False):            # te lichte kleur: wel tonen (keuze 2d)
        raise HTTPException(400, _("Ongeldige instelling."))
    pdf = q.get("pdf") == "1"
    conn, lib = _lib(ctx)
    with conn:
        rnd = renderer(ctx, conn)
        try:
            kind, v = _sample(ctx, conn, lib, "invoice", "")
        except NotFoundError:
            kind, v = None, None
        if v is None:
            kind, v = "invoice", _sample_vars(conn, ctx.company.get("doc_language") or "nl")
    rnd.settings.style = s
    if source is not None:
        other = _company_renderer(ctx, source["id"])
        rnd.logo = other.logo                       # type: ignore[method-assign]
    if not pdf:
        rnd.font_url_prefix = FONT_URL
    try:
        if pdf:
            data = rnd.render_pdf(kind, v)
            return Response(data, media_type="application/pdf",
                            headers={"Content-Disposition": f'inline; filename="{safe_filename(_("Huisstijl"))}.pdf"',
                                     "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        html = _preview_html(rnd.render_html(kind, v), s, _("venster envelop"))
    except Exception as exc:                        # fout in een eigen sjabloon: laten zien, niet 500
        raise HTTPException(400, _("Het sjabloon gaf een fout: {fout}").format(fout=f"{type(exc).__name__}: {exc}"[:300]))
    return Response(html, media_type="text/html; charset=utf-8",
                    headers={"Content-Security-Policy": PREVIEW_CSP, "X-Frame-Options": "SAMEORIGIN",
                             "Cache-Control": "no-store"})


# ================================================================== lettertypen en licenties

@router.get("/lettertypen", name="fonts")
def fonts_page(ctx: Ctx = Depends(ctx_user)):
    """Licentievermelding van de meegeleverde lettertypen (voorwaarde gebruiker bij het meeleveren)."""
    fonts = [{"key": k, "f": f, "license": docfonts.license_text(k)} for k, f in docfonts.FONTS.items()]
    return render(ctx, "am/fonts.html", fonts=fonts, license_name="SIL Open Font License 1.1")


@router.get("/lettertypen/lettertypen.css")
def fonts_css(ctx: Ctx = Depends(ctx_user)):
    """``@font-face`` + een klasse per lettertype, om de namen in de keuzelijst in hun eigen letter te tonen
    (de CSP van de app staat geen inline opmaak toe)."""
    css = "\n".join(docfonts.face_css(k, FONT_URL) + f"\n.font-{k} {{ font-family: {docfonts.family(k)}; }}"
                     for k in docfonts.FONTS)
    return Response(css + f"\n.font-standard {{ font-family: {docfonts.STANDARD_FAMILY}; }}\n",
                    media_type="text/css; charset=utf-8", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/lettertypen/{key}/{name}")
def font_file(key: str, name: str, ctx: Ctx = Depends(ctx_user)):
    p = docfonts.font_file(key, name)
    if p is None:
        raise HTTPException(404)
    media = "font/ttf" if name.endswith(".ttf") else "text/plain; charset=utf-8"
    return FileResponse(p, media_type=media, headers={"Cache-Control": "private, max-age=86400"})
