"""Documenten als PDF (fase 4, D7/D8/DS1): ``/document/<soort>/<id>.pdf``.

Zoals de knop Printen met "scherm" in SQL-Ledger, maar altijd als PDF; de browser print. Het vinkje
"geprint" in de statustabel wordt gezet (Perl deed dat alleen bij een printer; die is er hier niet).
Welke soorten er zijn en bij welk scherm ze horen staat in ``doctypes``.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from sqlledger import docsettings
from sqlledger.errors import NotFoundError
from sqlledger.printdata import PrintData, mark_status

from . import doctypes
from .documents import DocumentRenderer, VARIANT_RE
from .i18n import gettext as _
from .routes_arap import _lib
from .web import Ctx, ctx_company

router = APIRouter()


def renderer(ctx: Ctx, conn=None) -> DocumentRenderer:
    """Renderer met de documentinstellingen van de administratie (via ``conn`` of een eigen verbinding)."""
    own = conn is None
    if own:
        conn, _lib_ = _lib(ctx)
    try:
        settings, banks = docsettings.load(conn), docsettings.bank_accounts(conn)
    finally:
        if own:
            conn.close()
    return DocumentRenderer(ctx.settings.data_dir, ctx.company["id"], ctx.company.get("doc_language") or "nl",
                            settings=settings, banks=banks)


def print_data(ctx: Ctx, conn, lib) -> PrintData:
    return PrintData(conn, lib.cfg, username=ctx.user.get("name") or "", useremail=ctx.user.get("email") or "",
                     doc_language=ctx.company.get("doc_language") or "nl")


def safe_filename(text: str) -> str:
    keep = "".join(c if c.isalnum() or c in "-_." else "_" for c in text)
    return keep.strip("._") or "document"


def formname_label(key: str) -> str:
    return doctypes.label(key)


def filename(loc: doctypes.Located) -> str:
    return safe_filename(f"{_(loc.kind.label)}-{loc.number or loc.trans_id}") + ".pdf"


def check_variant(variant: Optional[str]) -> None:
    if variant and not VARIANT_RE.match(variant):
        raise HTTPException(400, _("Ongeldige sjabloonvariant."))


def require_doc_module(ctx: Ctx, loc: doctypes.Located) -> None:
    if loc.module not in (ctx.company["modules"] or []):
        raise HTTPException(403, _("Deze module staat uit voor deze administratie."))


def load(ctx: Ctx, key: str, trans_id: int, variant: Optional[str]):
    """Document zoeken en de sjabloonvariabelen maken. Geeft (conn, lib, loc, vars); 404 als de soort
    niet bij dit document past."""
    if key not in doctypes.KINDS:
        raise HTTPException(404, _("Onbekende documentsoort."))
    check_variant(variant)
    conn, lib = _lib(ctx)
    try:
        loc = doctypes.locate(conn, key, trans_id)
        if loc is None:
            raise HTTPException(404, _("Document niet gevonden."))
        require_doc_module(ctx, loc)
        v = doctypes.print_vars(print_data(ctx, conn, lib), loc, variant)
    except NotFoundError:
        conn.close()
        raise HTTPException(404, _("Document niet gevonden."))
    except BaseException:
        conn.close()
        raise
    return conn, lib, loc, v


@router.get("/document/{key}/{trans_id}.pdf", name="document_pdf")
def document_pdf(key: str, trans_id: int, request: Request, ctx: Ctx = Depends(ctx_company)):
    variant: Optional[str] = request.query_params.get("variant")
    conn, lib, loc, v = load(ctx, key, trans_id, variant)
    with conn:
        pdf = renderer(ctx, conn).render_pdf(key, v)
        if ctx.has_role("bookkeeper") and request.query_params.get("voorbeeld") != "1":
            mark_status(conn, lib.cfg, trans_id, loc.kind.formname, printed=True, table=loc.table,
                        reference=loc.number)
    ctx.log("document_printed", formname=key, trans_id=trans_id)
    disposition = "attachment" if request.query_params.get("download") == "1" else "inline"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'{disposition}; filename="{filename(loc)}"',
                             "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
