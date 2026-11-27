"""Beheer > Importeren en Beheer > Exporteren (FASE4_ANALYSE.md stap 7, D18–D20, keuzes 1–12).

Importeren (alleen de beheerder): soort kiezen en een CSV- of xlsx-bestand uploaden → het bestand staat tijdelijk in de
datamap (``import/``, token in het formulier, alleen voor dezelfde administratie en gebruiker, na een dag opgeruimd) →
kolommen koppelen + voorbeeld (proefrun, ``sqlledger.importer``) → rijen aanvinken → importeren in één transactie. De
koppeling per soort wordt onthouden in de administratie (``x_setting`` ``import_map.<soort>``).

Exporteren: dezelfde soorten in precies het importformaat (kop = SL-veldnaam), CSV of xlsx.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from starlette.datastructures import FormData

from sqlledger import _db
from sqlledger.errors import LedgerError, ValidationError
from sqlledger.extensions import ensure_schema
from sqlledger.importer import (DOC_KINDS, FIELDS, KINDS, UNSUPPORTED, ImportFailed, ImportFileError, Importer, auto_map,
                                export_file, read_file)

from . import i18n_de, i18n_en, i18n_fr
from .i18n import N_, gettext as _
from .libmsg import lib_message
from .routes_attach import upload_limit
from .routes_arap import _lib
from .uploads import read_upload
from .web import Ctx, Redirect, csrf_form, ctx_company, render, require_role

router = APIRouter()

KIND_LABELS = {"customer": N_("Klanten"), "vendor": N_("Leveranciers"), "part": N_("Artikelen"),
               "service": N_("Diensten"), "partsgroup": N_("Artikelgroepen"), "coa": N_("Rekeningschema"),
               "gifi": N_("GIFI-indeling"), "gl": N_("Memoriaal"), "sales_invoice": N_("Verkoopfacturen"),
               "sales_order": N_("Verkooporders"), "purchase_order": N_("Inkooporders"), "payment": N_("Betalingen")}
#: wie mag exporteren: rol en module (keuze 1)
EXPORT_RIGHTS = {"customer": ("readonly", "ar"), "vendor": ("readonly", "ap"), "part": ("readonly", "ic"),
                 "service": ("readonly", "ic"), "partsgroup": ("readonly", "ic"), "coa": ("admin", None),
                 "gifi": ("admin", None)}
STATUS_LABELS = {"new": N_("nieuw"), "update": N_("bijwerken"), "same": N_("ongewijzigd"), "error": N_("fout")}
#: veldlabels uit de bibliotheek, voor de vertaalcatalogus (tests/web/test_i18n.py)
FIELD_LABELS = [
    N_("Klantnummer"), N_("Leveranciersnummer"), N_("Naam"), N_("Soort relatie"), N_("Adres"), N_("Adres 2"),
    N_("Postcode"), N_("Plaats"), N_("Provincie"), N_("Land"), N_("Aanhef"), N_("Voornaam"), N_("Achternaam"),
    N_("Titel"), N_("Functie"), N_("Geslacht"), N_("Telefoon"), N_("Fax"), N_("Mobiel"), N_("E-mail"), N_("Cc"),
    N_("Bcc"), N_("Notities"), N_("Betalingstermijn"), N_("Korting %"), N_("Betalingskorting %"),
    N_("Kortingstermijn"), N_("Kredietlimiet"), N_("Drempel"), N_("Prijzen incl. btw"), N_("Btw"),
    N_("Debiteuren-/crediteurenrekening"), N_("Betaalrekening"), N_("Kortingsrekening"), N_("Valuta"), N_("Taal"),
    N_("Branche"), N_("Verkoper"), N_("Betaalmethode"), N_("Prijsgroep"), N_("Btw-nummer"), N_("SBI-code"),
    N_("Acceptgiro"), N_("Begindatum"), N_("Einddatum"), N_("IBAN"), N_("BIC"), N_("Bank"), N_("Adres bank"),
    N_("Postcode bank"), N_("Plaats bank"), N_("Provincie bank"), N_("Land bank"), N_("Lidnummer"),
    N_("Clearingnummer"), N_("Artikelnummer"), N_("Omschrijving"), N_("Eenheid"), N_("Artikelgroep"),
    N_("Verkoopprijs"), N_("Lijstprijs"), N_("Inkoopprijs"), N_("Prijs bijgewerkt"), N_("Gewicht"),
    N_("Bestelniveau"), N_("Locatie"), N_("Barcode"), N_("Vervallen"), N_("Voorraadrekening"),
    N_("Opbrengstrekening"), N_("Kostenrekening"), N_("Code"), N_("Rekeningnummer"), N_("Rekening of kop (A/H)"),
    N_("Soort (A/L/Q/I/E)"), N_("Koppelingen"), N_("GIFI"), N_("Contra"), N_("Gesloten"), N_("Referentie"),
    N_("Datum"), N_("Afdeling"), N_("Koers"), N_("Rekening"), N_("Debet"), N_("Credit"), N_("Bron"), N_("Memo"),
    N_("Project"), N_("Factuurnummer"), N_("Factuurdatum"), N_("Vervaldatum"), N_("Debiteurenrekening"),
    N_("Betalingskenmerk"), N_("Ordernummer"), N_("Orderdatum"), N_("Leverdatum"), N_("Inkoopordernummer"),
    N_("Interne notities"), N_("Aantal"), N_("Prijs"), N_("Bedrag"), N_("Betaald bedrag"), N_("Betaaldatum"),
    N_("Bron betaling"), N_("Memo betaling"), N_("Omschrijving factuur"), N_("Omschrijving order"),
    N_("Medewerkernummer")]
MAP_SETTING = "import_map."
MAX_AGE = 24 * 3600
TOKEN = re.compile(r"[0-9a-f]{32}")


# ------------------------------------------------------------------ tijdelijk bestand

class ImportFiles:
    """Het geüploade bestand tussen voorbeeld en importeren (zoals ``uploads.PendingFiles``, eigen map)."""

    def __init__(self, data_dir: str):
        self.dir = Path(data_dir) / "import"

    def save(self, company_id: int, user_id: int, kind: str, filename: str, data: bytes) -> str:
        self.cleanup()
        self.dir.mkdir(parents=True, exist_ok=True)
        token = secrets.token_hex(16)
        (self.dir / f"{token}.bin").write_bytes(data)
        (self.dir / f"{token}.json").write_text(json.dumps(
            {"company_id": company_id, "user_id": user_id, "kind": kind, "filename": filename}))
        return token

    def get(self, token: str, company_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        if not TOKEN.fullmatch(token or ""):
            return None
        try:
            meta = json.loads((self.dir / f"{token}.json").read_text())
            data = (self.dir / f"{token}.bin").read_bytes()
        except (OSError, ValueError):
            return None
        if meta.get("company_id") != company_id or meta.get("user_id") != user_id:
            return None
        return {**meta, "data": data, "token": token}

    def discard(self, token: str) -> None:
        if TOKEN.fullmatch(token or ""):
            for ext in ("bin", "json"):
                (self.dir / f"{token}.{ext}").unlink(missing_ok=True)

    def cleanup(self) -> None:
        if not self.dir.is_dir():
            return
        limit = time.time() - MAX_AGE
        for p in self.dir.iterdir():
            try:
                if p.stat().st_mtime < limit:
                    p.unlink()
            except OSError:
                pass


def _files(ctx: Ctx) -> ImportFiles:
    return ImportFiles(ctx.settings.data_dir)


# ------------------------------------------------------------------ hulp

def _aliases() -> Dict[str, List[str]]:
    """Kolomkoppen in de schermtalen per veld (keuze 4): het label vertaald naar en/de/fr."""
    out: Dict[str, List[str]] = {}
    for flds in FIELDS.values():
        for f in flds:
            out.setdefault(f.name, [])
            for table in (i18n_en.EN, i18n_de.TEXTS, i18n_fr.TEXTS):
                if f.label in table and table[f.label] not in out[f.name]:
                    out[f.name].append(table[f.label])
    return out


def _saved_map(conn, kind: str) -> Dict[str, str]:
    with _db.cursor(conn) as cur:
        cur.execute("SELECT to_regclass('x_setting') AS t")
        if not cur.fetchone()["t"]:
            return {}
        cur.execute("SELECT value FROM x_setting WHERE key = %s", (MAP_SETTING + kind,))
        r = cur.fetchone()
    try:
        return json.loads(r["value"]) if r else {}
    except ValueError:
        return {}


def _store_map(conn, kind: str, header: List[str], mapping: List[str], login: str) -> None:
    ensure_schema(conn)
    value = json.dumps({h: m for h, m in zip(header, mapping) if h}, ensure_ascii=False)
    with conn.transaction(), _db.cursor(conn) as cur:
        cur.execute("""INSERT INTO x_setting (key, value, login) VALUES (%s, %s, %s)
                       ON CONFLICT (key) DO UPDATE SET value = excluded.value, login = excluded.login,
                                                       updated = now()""", (MAP_SETTING + kind, value, login))


def _importer(ctx: Ctx, conn, kind: str) -> Importer:
    cfg = ctx.user_config()
    return Importer(conn, cfg, kind, cfg.numberformat, cfg.dateformat)


def _cell(v) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return "" if v is None else str(v)


def _page(ctx: Ctx, status: int = 200, **kw):
    kw.setdefault("kinds", KIND_LABELS)
    return render(ctx, "am/import.html", status, **kw)


async def upload_form(request: Request) -> FormData:
    size = request.headers.get("content-length")
    if size and size.isdigit() and int(size) > 64 * 1024 * 1024:
        raise HTTPException(413, _("Het bestand is te groot."))
    return await csrf_form(request)


# ------------------------------------------------------------------ importeren

@router.get("/beheer/importeren", name="import_form")
def import_form(ctx: Ctx = Depends(ctx_company)):
    require_role(ctx, "admin")
    return _page(ctx, kind=ctx.request.query_params.get("soort") or "customer")


@router.post("/beheer/importeren", name="import_upload")
def import_upload(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(upload_form)):
    require_role(ctx, "admin")
    kind = str(form.get("kind", ""))
    if kind not in KINDS:
        return _page(ctx, 400, kind="customer", error=_("Kies wat u wilt importeren."))
    limit = upload_limit(ctx)
    got = read_upload(form, "bestand", limit)
    if not got:
        return _page(ctx, 400, kind=kind, error=_("Kies een bestand."))
    filename, data = got
    if len(data) > limit:
        return _page(ctx, 413, kind=kind, error=_("Het bestand is groter dan {mb} MB.").format(mb=limit // 2**20))
    try:
        read_file(data, filename)
    except ImportFileError as exc:
        return _page(ctx, 400, kind=kind, error=lib_message(exc))
    token = _files(ctx).save(ctx.company["id"], ctx.user["id"], kind, re.sub(r"[^\w .\-]", "_", filename)[:200], data)
    raise Redirect(f"/beheer/importeren/{token}")


def _preview_page(ctx: Ctx, f: Dict[str, Any], mapping: Optional[List[str]] = None, status: int = 200,
                  error: Optional[str] = None, chosen: Optional[set] = None):
    kind = f["kind"]
    header, rows = read_file(f["data"], f["filename"])
    conn, _lib_ = _lib(ctx)
    with conn:
        if mapping is None:
            mapping = auto_map(kind, header, _aliases(), _saved_map(conn, kind))
        imp = _importer(ctx, conn, kind)
        try:
            res = imp.preview(mapping, rows)
        except ValidationError as exc:
            res, error = None, error or lib_message(exc)
    if res and chosen is not None:
        for it in res.items:
            it.selected = it.rows[0] in chosen and it.status in ("new", "update")
    unsupported = {h for h in header if h.lower() in UNSUPPORTED.get(kind, ())}
    return render(ctx, "am/import_preview.html", status, f=f, kind=kind, kind_label=KIND_LABELS[kind],
                  header=header, mapping=mapping, fields=FIELDS[kind], unsupported=unsupported, res=res,
                  rows=rows, cell=_cell, error=error, status_labels=STATUS_LABELS, msg=lib_message,
                  docs=kind in DOC_KINDS, pay=kind == "payment",
                  labels={fl.attr: fl.label for fl in FIELDS[kind]})


@router.get("/beheer/importeren/{token}", name="import_preview")
def import_preview(token: str, ctx: Ctx = Depends(ctx_company)):
    require_role(ctx, "admin")
    f = _files(ctx).get(token, ctx.company["id"], ctx.user["id"])
    if not f:
        raise Redirect("/beheer/importeren?ok=import_expired")
    return _preview_page(ctx, f)


@router.post("/beheer/importeren/{token}", name="import_run")
def import_run(token: str, ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    require_role(ctx, "admin")
    files = _files(ctx)
    f = files.get(token, ctx.company["id"], ctx.user["id"])
    if not f:
        raise Redirect("/beheer/importeren?ok=import_expired")
    kind = f["kind"]
    header, rows = read_file(f["data"], f["filename"])
    valid = {fl.name for fl in FIELDS[kind]}
    mapping = [str(form.get(f"col_{i}", "")) for i in range(len(header))]
    mapping = [m if m in valid else "" for m in mapping]
    if len({m for m in mapping if m}) != len([m for m in mapping if m]):
        return _preview_page(ctx, f, mapping, 400, error=_("Een veld is aan meer dan één kolom gekoppeld."))
    action = form.get("action")
    if action == "cancel":
        files.discard(token)
        raise Redirect("/beheer/importeren")
    chosen = {int(v) for v in form.getlist("row") if str(v).isdigit()}
    if action != "import":
        return _preview_page(ctx, f, mapping, chosen=chosen if form.get("chosen_set") else None)
    if not chosen:
        return _preview_page(ctx, f, mapping, 400, error=_("Vink minstens één rij aan."), chosen=chosen)
    conn, _lib_ = _lib(ctx)
    with conn:
        imp = _importer(ctx, conn, kind)
        try:
            res = imp.run(mapping, rows, sorted(chosen))
        except ImportFailed as exc:
            return _preview_page(ctx, f, mapping, 400, chosen=chosen,
                                 error=_("Niets geïmporteerd. Rij {row}: {error}").format(
                                     row=exc.item.rows[0], error=lib_message(exc.item.error)))
        except LedgerError as exc:
            return _preview_page(ctx, f, mapping, 400, chosen=chosen, error=lib_message(exc))
        if form.get("remember"):
            _store_map(conn, kind, header, mapping, ctx.employee_login)
    files.discard(token)
    done = [i for i in res.items if i.status in ("new", "update") and i.selected is not False]
    ctx.log("import", kind=kind, filename=f["filename"], added=res.added, updated=res.updated)
    return render(ctx, "am/import_done.html", kind=kind, kind_label=KIND_LABELS[kind], res=res, done=done,
                  filename=f["filename"], status_labels=STATUS_LABELS, docs=kind in DOC_KINDS,
                  pay=kind == "payment")


# ------------------------------------------------------------------ exporteren

def _may_export(ctx: Ctx, kind: str) -> bool:
    role, module = EXPORT_RIGHTS[kind]
    return ctx.has_role(role) and (module is None or module in (ctx.company["modules"] or []))


@router.get("/beheer/exporteren", name="export_form")
def export_form(ctx: Ctx = Depends(ctx_company)):
    kinds = {k: v for k, v in KIND_LABELS.items() if k in EXPORT_RIGHTS and _may_export(ctx, k)}
    if not kinds:
        require_role(ctx, "admin")
    return render(ctx, "am/export.html", kinds=kinds)


@router.get("/beheer/exporteren/{kind}.{fmt}", name="export_file")
def export_download(kind: str, fmt: str, ctx: Ctx = Depends(ctx_company)):
    if kind not in EXPORT_RIGHTS or fmt not in ("csv", "xlsx"):
        raise HTTPException(404)
    if not _may_export(ctx, kind):
        require_role(ctx, "admin")
        raise HTTPException(403, _("Uw rol in deze administratie geeft hier geen toegang toe."))
    cfg = ctx.user_config()
    conn, _lib_ = _lib(ctx)
    with conn:
        data = export_file(conn, cfg, kind, fmt, cfg.numberformat, cfg.dateformat)
    ctx.log("export", kind=kind, format=fmt)
    name = f"{_(KIND_LABELS[kind]).lower().replace(' ', '-')}.{fmt}"
    media = "text/csv; charset=utf-8" if fmt == "csv" else \
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})
