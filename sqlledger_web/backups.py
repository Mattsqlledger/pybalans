"""Back-up maken (FASE6_ANALYSE.md 6d-1, I7): gedeeld door Systeem > Back-up maken en ``pybalans backup``.

Per administratie twee bestanden:

* de database: ``sqlledger.dbbackup`` (pg_dump, gzip), weer in te lezen met Systeem > Back-up inlezen;
* de eigen sjablonen en het logo uit ``<data_dir>/templates/<id>/`` als zip (die staan niet in de database). Bij
  inlezen gaan ze via dezelfde controles als Beheer > Sjablonen en Huisstijl (``TemplateStore``); vorige versies van
  sjablonen gaan niet mee.

De datum van de laatste back-up staat in de app-database (``company.last_backup``, app-migratie 11): het dashboard
herinnert de beheerder als die ouder is dan ``REMIND_DAYS`` dagen.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import psycopg
from psycopg.conninfo import conninfo_to_dict

from sqlledger import dbbackup
from sqlledger._db import cursor, get_defaults

from .companies import connect_company
from .settings import Settings

#: herinnering op het dashboard als de laatste back-up ouder is (I7)
REMIND_DAYS = 7
#: grens voor een zip met sjablonen bij inlezen (sjablonen ≤ 512 kB, logo ≤ 1 MB)
MAX_TEMPLATES_ZIP = 20 * 1024 * 1024
TEMPLATES_SUFFIX = "-sjablonen.zip"


def company_info(settings: Settings, company: dict) -> Tuple[str, str]:
    """(versie, bedrijfsnaam) uit ``defaults`` van de administratie."""
    with connect_company(settings, company) as conn:
        d = get_defaults(cursor(conn), "version", "company")
    return d.get("version") or "", d.get("company") or company.get("name") or ""


def dump_company(settings: Settings, company: dict, folder: Path, now: Optional[datetime] = None
                 ) -> dbbackup.DumpResult:
    """Database van de administratie als ``<dbname>-JJJJMMDD-UUMM.sql.gz`` in ``folder``."""
    made = now or datetime.now()
    version, name = company_info(settings, company)
    info = settings.company_conninfo(company["dbname"], company.get("dbhost"), company.get("dbport"))
    return dbbackup.dump(info, Path(folder) / dbbackup.filename(company["dbname"], made), version=version,
                         company=name, pg_dump=settings.pg_dump, now=made)


def dump_app(settings: Settings, folder: Path, now: Optional[datetime] = None) -> dbbackup.DumpResult:
    """De app-database (gebruikers, rechten, 2FA, logboek): alleen via de opdrachtregel; terugzetten met psql."""
    made = now or datetime.now()
    info = conninfo_to_dict(settings.app_conninfo())
    info.setdefault("host", settings.db_host)
    info.setdefault("port", settings.db_port)
    if settings.db_user:
        info.setdefault("user", settings.db_user)
    return dbbackup.dump(info, Path(folder) / dbbackup.filename(info["dbname"], made), company="app",
                         pg_dump=settings.pg_dump, now=made)


def templates_dir(settings: Settings, company_id: int) -> Path:
    return Path(settings.data_dir).expanduser() / "templates" / str(int(company_id))


def has_templates(settings: Settings, company_id: int) -> bool:
    d = templates_dir(settings, company_id)
    return d.is_dir() and any(p.is_file() for p in d.rglob("*"))


def templates_zip(settings: Settings, company_id: int) -> Optional[bytes]:
    """Alle bestanden onder ``templates/<id>/`` (ook vorige versies) als zip; None als er niets is."""
    d = templates_dir(settings, company_id)
    files = sorted(p for p in d.rglob("*") if p.is_file() and not p.name.startswith(".")) if d.is_dir() else []
    if not files:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, p.relative_to(d).as_posix())
    return buf.getvalue()


def restore_templates(store, data: bytes) -> Tuple[List[str], List[str]]:
    """Sjablonen en logo uit een zip van ``templates_zip`` in een administratie zetten via ``TemplateStore``
    (zelfde controles als bij uploaden). Eerst alles controleren, dan pas opslaan (logo eerst): een fout in één
    sjabloon laat niets half achter. Geeft (opgeslagen, overgeslagen) als namen in de zip."""
    from .documents import LOGO_NAMES, VARIANT_RE, TemplateError
    from .i18n import N_, gettext as _
    too_big = N_("Het zip-bestand met sjablonen is te groot (maximaal 20 MB).")
    if len(data) > MAX_TEMPLATES_ZIP:
        raise TemplateError(too_big)
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise TemplateError(N_("Het bestand met sjablonen is geen zip-bestand."))
    logo: Optional[Tuple[str, bytes]] = None
    texts: List[Tuple[str, str, str, str]] = []       # (naam in zip, sjabloon, variant, tekst)
    skipped: List[str] = []
    with z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if sum(i.file_size for i in infos) > 5 * MAX_TEMPLATES_ZIP:
            raise TemplateError(too_big)
        for i in infos:
            parts = i.filename.split("/")
            variant, name = (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0] if len(parts) == 1 else "")
            ok = bool(name) and (not variant or bool(VARIANT_RE.match(variant)))
            if ok and not variant and name in LOGO_NAMES:
                logo = (i.filename, z.read(i))
            elif ok and name.endswith(".html") and name[:-5] in store.names:
                text = store.decode(z.read(i))
                try:
                    store.validate(name[:-5], variant, text)    # opbouw (Jinja-sandbox), geen proef-PDF
                except TemplateError as exc:
                    raise TemplateError(N_("Sjabloon {bestand}: {fout}"), bestand=i.filename,
                                        fout=_(exc.text).format(**exc.values))
                texts.append((i.filename, name[:-5], variant, text))
            else:
                skipped.append(i.filename)                      # vorige versies, onbekende bestanden
    saved: List[str] = []
    if logo:
        store.save_logo(logo[1])
        saved.append(logo[0])
    for fname, name, variant, text in texts:
        store.save(name, variant, text)
        saved.append(fname)
    return saved, skipped


def record(conn: psycopg.Connection, company_id: int) -> None:
    conn.execute("UPDATE company SET last_backup = now() WHERE id = %s", (company_id,))


def overdue(company: dict, now: Optional[datetime] = None) -> bool:
    """Nog nooit een back-up met de app, of de laatste is ouder dan ``REMIND_DAYS`` dagen."""
    last = company.get("last_backup")
    now = now or datetime.now(timezone.utc)
    return last is None or now - last > timedelta(days=REMIND_DAYS)
