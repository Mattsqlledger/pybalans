"""Systeem > Back-up maken (FASE6_ANALYSE.md 6d-1, I7): per administratie de database (``.sql.gz``) en de eigen
sjablonen met het logo (``.zip``) downloaden. Alleen systeembeheerders: de back-up bevat alles, ook vertrouwelijke
bijlagen. Inplannen kan met ``pybalans backup`` (zie README). Logboek ``backup_made``/``backup_failed``.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask
from starlette.datastructures import FormData

from sqlledger import dbbackup
from sqlledger.errors import LedgerError

from . import backups
from .i18n import gettext as _
from .libmsg import lib_message
from .web import Ctx, csrf_form, ctx_sysadmin, render

router = APIRouter()
#: voorbeeld op het scherm (een opdracht, in elke taal hetzelfde)
COMMAND = "pybalans backup --dir /srv/sqlledger-backup --keep 30"
#: "Hoe werkt het?": elke nacht via launchd (Mac) of cron (Linux); code, geen vertaalde tekst
PLIST_FILE = "~/Library/LaunchAgents/nl.sqlledger.backup.plist"
PLIST_LOAD = f"launchctl load {PLIST_FILE}"
PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>nl.sqlledger.backup</string>
  <key>ProgramArguments</key><array>
    <string>/path/to/pybalans</string><string>--config</string><string>/path/to/app.toml</string>
    <string>backup</string><string>--dir</string><string>/Volumes/Backup/sqlledger</string><string>--keep</string><string>30</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>0</integer></dict>
</dict></plist>"""
CRON = "0 3 * * * pybalans --config /path/to/app.toml backup --dir /srv/backup --keep 30"


def _companies(ctx: Ctx) -> list:
    rows = ctx.conn.execute("SELECT * FROM company ORDER BY lower(name)").fetchall()
    for r in rows:
        r["templates"] = backups.has_templates(ctx.settings, r["id"])
        r["overdue"] = backups.overdue(r)
    return rows


def _page(ctx: Ctx, status: int = 200, error: str = ""):
    return render(ctx, "system/backup.html", status, companies=_companies(ctx), error=error,
                  remind_days=backups.REMIND_DAYS, COMMAND=COMMAND,
                  PLIST_FILE=PLIST_FILE, PLIST_LOAD=PLIST_LOAD, PLIST=PLIST, CRON=CRON)


def _company(ctx: Ctx, company_id: int) -> dict:
    row = ctx.conn.execute("SELECT * FROM company WHERE id = %s", (company_id,)).fetchone()
    if not row:
        raise HTTPException(404, _("Administratie niet gevonden."))
    return row


@router.get("/systeem/back-up-maken", name="backup_page")
def backup_page(ctx: Ctx = Depends(ctx_sysadmin)):
    return _page(ctx)


@router.post("/systeem/back-up-maken/{company_id}", name="backup_make")
def backup_make(company_id: int, ctx: Ctx = Depends(ctx_sysadmin), form: FormData = Depends(csrf_form)):
    company = _company(ctx, company_id)
    folder = Path(tempfile.mkdtemp(prefix="sqlledger-backup-"))
    try:
        res = backups.dump_company(ctx.settings, company, folder)
    except (LedgerError, psycopg.Error) as exc:
        shutil.rmtree(folder, ignore_errors=True)
        msg = lib_message(exc) if isinstance(exc, LedgerError) else str(exc).strip().splitlines()[0]
        ctx.log("backup_failed", target=company["name"], dbname=company["dbname"], error=msg[:300])
        return _page(ctx, 500, error=_("Back-up van {name} mislukt: {error}").format(name=company["name"], error=msg))
    backups.record(ctx.conn, company["id"])
    ctx.log("backup_made", target=company["name"], dbname=company["dbname"], kind="database",
            filename=res.path.name, size=res.size, version=res.version, via="web")
    return FileResponse(res.path, media_type="application/gzip", filename=res.path.name,
                        background=BackgroundTask(shutil.rmtree, folder, ignore_errors=True))


@router.post("/systeem/back-up-maken/{company_id}/sjablonen", name="backup_templates")
def backup_templates(company_id: int, ctx: Ctx = Depends(ctx_sysadmin), form: FormData = Depends(csrf_form)):
    company = _company(ctx, company_id)
    data = backups.templates_zip(ctx.settings, company["id"])
    if data is None:
        return _page(ctx, 404, error=_("{name} heeft geen eigen sjablonen of logo.").format(name=company["name"]))
    name = dbbackup.filename(company["dbname"], datetime.now(), backups.TEMPLATES_SUFFIX)
    ctx.log("backup_made", target=company["name"], dbname=company["dbname"], kind="templates", filename=name,
            size=len(data), via="web")
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
