"""Back-up inlezen (FASE6_ANALYSE.md stap 6a, F1–F4): Systeem > Back-up inlezen en dezelfde stap in
de eerste start (``/welkom/administratie/back-up``, de tegel die tot nu toe grijs was).

Een SQL-Ledger-back-up (``.sql`` of ``.sql.gz`` van admin.pl) of een ``pg_dump`` in platte SQL wordt veilig ingelezen
(``sqlledger.slbackup``: alleen wat in een back-up hoort, als de gewone databasegebruiker, in één transactie) in een
nieuwe database, bijgewerkt naar 3.2.4 (``sqlledger.slupgrade``) en als administratie gekoppeld. Alleen
systeembeheerders.

v1.0.1 (PB1–PB5, PB14): in twee stappen. **Uploaden** (met voortgang in %) zet het bestand klaar in de datamap
(``restorejobs.StagedBackups``); het **controlescherm** toont wat er in de back-up staat (soort, dataset, versie,
datum, bedrijfsnaam, laatste boeking) met naam en databasenaam vooringevuld en de keuze "Jij bent in deze
administratie:" (medewerker). **Inlezen** loopt als achtergrondtaak (``restorejobs.Job``); de pagina toont de
voortgang (controleren/terugzetten in %, bijwerken stap x van n) en daarna het resultaat.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import FormData, UploadFile

import sqlledger
from sqlledger import UserConfig, newcompany, rmaimport, slbackup, switchover
from sqlledger.amounts import format_amount
from sqlledger.dates import format_date
from sqlledger.errors import LedgerError, ValidationError

from . import accounts, backups, restorejobs, rmareport
from .appdb import MODULES
from .i18n import N_, gettext as _
from .libmsg import lib_message
from .restorejobs import Job, StagedBackups
from .routes_am import DOC_LANGUAGES
from .routes_vat import company_flags
from .settings import Settings
from .uploads import read_upload
from .web import Ctx, Redirect, csrf_form, ctx_sysadmin, render

router = APIRouter()

#: een back-up kan groot zijn (een echte administratie: ±100 MB); los van de uploadgrens voor bijlagen
MAX_BACKUP = 512 * 1024 * 1024
NEW_DBNAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
#: keuze "U bent in deze administratie:" (PB14)
EMP_NEW, EMP_NONE = "new", "none"
#: tekst per fase van het inlezen (PB4)
PHASE_TEXT = {
    "start": N_("Inlezen wordt gestart…"),
    "check": N_("Back-up lezen en controleren: {percent}%"),
    "restore": N_("Terugzetten: {percent}%"),
    "upgrade": N_("Bijwerken naar 3.2.4: stap {done} van {total}"),
    "finish": N_("Afronden…"),
}
PHASE_LABEL = (("check", N_("Lezen en controleren")), ("restore", N_("Terugzetten")),
               ("upgrade", N_("Bijwerken")), ("finish", N_("Afronden")))
CONFLICT = N_("Er is al een administratie met deze naam of code.")
#: bibliotheekmelding (``slbackup.restore``); de vertaling staat in LIBRARY
DB_EXISTS = "Er bestaat al een database met deze naam."
BUSY = N_("Er wordt al een back-up ingelezen; wacht tot die klaar is.")


async def backup_form(request: Request) -> FormData:
    size = request.headers.get("content-length")
    if size and size.isdigit() and int(size) > MAX_BACKUP + 64 * 1024:
        raise HTTPException(413, _("Het bestand is te groot (maximaal 512 MB)."))
    return await csrf_form(request)


def _base(wizard: bool) -> str:
    return "/welkom/administratie/back-up" if wizard else "/systeem/back-up"


def _staged(ctx: Ctx) -> StagedBackups:
    return StagedBackups(ctx.settings.data_dir)


# ------------------------------------------------------------------ stap 1: uploaden

def recover(settings: Settings, pool) -> list:
    """Afgebroken inlezen (harde stop van de server) opruimen; bij de start en op Back-up inlezen (keuze 17)."""
    def registered(dbname: str) -> bool:
        with pool.connection() as conn:
            return conn.execute("SELECT 1 FROM company WHERE dbname = %s", (dbname,)).fetchone() is not None

    def log(data: dict) -> None:
        with pool.connection() as conn:
            accounts.log_event(conn, "restore_aborted", login=data.get("login"), target=data.get("name"),
                               dbname=data.get("dbname"), dropped=bool(data.get("dropped")))
    try:
        return restorejobs.recover(
            settings.data_dir, lambda: sqlledger.connect(**settings.company_conninfo("postgres"), connect_timeout=5),
            registered, log)
    except (psycopg.Error, OSError):                # niet bereikbaar: volgende keer opnieuw
        return []


def _upload_page(ctx: Ctx, wizard: bool, status: int = 200, error: Optional[str] = None):
    recover(ctx.settings, ctx.request.app.state.pool)
    jobs = restorejobs.for_user(ctx.user["id"])
    aborted = []
    for a in restorejobs.aborted(ctx.settings.data_dir):
        name = a.get("name") or a.get("dbname")
        if a.get("dropped"):
            aborted.append(_("Een inlezen van {name} is afgebroken (server gestopt); de halve database is opgeruimd. "
                             "Probeer het opnieuw.").format(name=name))
        elif not a.get("created"):
            aborted.append(_("Een inlezen van {name} is afgebroken (server gestopt) voordat er iets was aangemaakt. "
                             "Probeer het opnieuw.").format(name=name))
    return render(ctx, "system/company_restore.html", status, error=error, wizard=wizard, step=4,
                  action=_base(wizard), jobs=[_job_view(j) for j in jobs[:3]], aborted=aborted)


def upload(ctx: Ctx, form: FormData, wizard: bool):
    f = form.get("bestand")
    if not isinstance(f, UploadFile) or not (f.filename or "").strip():
        return _upload_page(ctx, wizard, 400, _("Kies het back-upbestand."))
    # sjablonen en logo (Back-up maken, 6d-1): nu al controleren, zodat een fout meteen te zien is
    zgot = read_upload(form, "sjablonen", backups.MAX_TEMPLATES_ZIP)
    if zgot:
        error = _check_templates(zgot[1])
        if error:
            return _upload_page(ctx, wizard, 400, error)
    try:
        token = _staged(ctx).save(ctx.user["id"], f, MAX_BACKUP, zgot[1] if zgot else None)
    except ValidationError:
        return _upload_page(ctx, wizard, 400, _("Het bestand is te groot (maximaal 512 MB)."))
    except LedgerError as exc:
        return _upload_page(ctx, wizard, 400, lib_message(exc))
    raise Redirect(f"{_base(wizard)}/controle/{token}")


def _check_templates(data: bytes, into: Optional[Path] = None):
    """Zip met sjablonen controleren; zonder ``into``: foutmelding of None. Met ``into`` (een map) ook opslaan:
    foutmelding (tekst) of het resultaat van ``backups.restore_templates``."""
    from . import doctypes
    from .documents import DocumentRenderer, TemplateError, TemplateStore
    folder = into or Path(tempfile.mkdtemp(prefix="sqlledger-tpl-"))
    try:
        res = backups.restore_templates(TemplateStore(DocumentRenderer(folder, 0), doctypes.TEMPLATE_KINDS), data)
    except TemplateError as exc:
        return _(exc.text).format(**exc.values)
    finally:
        if into is None:
            shutil.rmtree(folder, ignore_errors=True)
    return res if into is not None else None


# ------------------------------------------------------------------ stap 2: controlescherm

def _norm(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return " ".join(text.split())


def match_employee(employees: List[Dict[str, str]], user: dict) -> str:
    """Vooringevulde keuze (PB14): de medewerker met dezelfde naam als de app-gebruiker (bij meer: met login en
    zonder einddatum eerst, dan de nieuwste), anders met dezelfde login; anders "geen"."""
    name = _norm(user.get("name") or "")
    same = [e for e in employees if name and _norm(e["name"]) == name]
    if not same:
        same = [e for e in employees if e["login"] and e["login"].lower() == (user.get("login") or "").lower()]
    if not same:
        return EMP_NONE
    same.sort(key=lambda e: (not e["login"], bool(e.get("enddate")),
                             -int(e["id"]) if str(e["id"]).isdigit() else 0))
    return f"e:{same[0]['id']}"


def _defaults(meta: dict, user: dict) -> Dict[str, Any]:
    from .routes_system import suggest_dbname
    info = meta["info"]
    stem = re.sub(r"(\.sql)?(\.gz)?$", "", meta.get("filename") or "")
    name = info.get("company") or info.get("dataset") or stem
    return {"name": name, "code": "", "dbname": suggest_dbname(info.get("dataset") or name or "administratie"),
            "add_me": True, "employee": match_employee(info.get("employees") or [], user),
            "language": default_language(info, user)}


def default_language(info: dict, user: dict) -> str:
    """Documenttaal vooraf (F25 A): uit de back-up (back-up van deze app), anders de taal van de gebruiker,
    anders Nederlands."""
    for lang in (info.get("language"), user.get("language")):
        if lang in dict(DOC_LANGUAGES):
            return lang
    return "nl"


def file_size(size: int, nf: str) -> str:
    """Bestandsgrootte voor het scherm (v1.0.1): tot 999 kB in hele kB (naar boven, minimaal 1 kB), daarboven in MB
    met één decimaal. Vroeger altijd MB: een klein bestand gaf "( MB)", want format_amount geeft bij 0 een lege tekst."""
    kb = max(1, -(-size // 1024))
    if kb < 1000:
        return format_amount(kb, nf, 0) + " kB"
    return format_amount(size / 1048576, nf, 1) + " MB"


def already_there(ctx: Ctx, info: dict, values: dict) -> List[str]:
    """Waarschuwingen (niet blokkeren, K3) als deze back-up of dataset al als administratie bestaat: herkomst van
    elke gekoppelde administratie (``switchover.origin``) naast de kop van de back-up, plus gelijke naam of
    databasenaam. Een administratie die niet te openen is, wordt overgeslagen."""
    out: List[str] = []
    dataset, bdate = (info.get("dataset") or "").strip(), (info.get("date") or "").strip()
    rows = ctx.conn.execute("SELECT id, name, dbname FROM company ORDER BY name").fetchall()
    for c in rows:
        o = None
        if dataset:
            try:
                with sqlledger.connect(**ctx.settings.company_conninfo(c["dbname"]), connect_timeout=3) as conn:
                    o = switchover.origin(conn)
            except psycopg.Error:
                o = None
        if o and dataset and (o.get("dataset") or "").strip() == dataset:
            if bdate and (o.get("date") or "").strip() == bdate:
                out.append(_("Deze back-up is al ingelezen als {name}.").format(name=c["name"]))
            else:
                out.append(_("Er is al een administratie uit dataset {dataset}: {name} (back-up van {date}).").format(
                    dataset=dataset, name=c["name"], date=o.get("date") or "—"))
        elif c["dbname"] == values.get("dbname"):
            out.append(_("De databasenaam {dbname} is al in gebruik door {name}.").format(
                dbname=c["dbname"], name=c["name"]))
        elif (c["name"] or "").lower() == (values.get("name") or "").lower():
            out.append(_("Er is al een administratie met de naam {name}.").format(name=c["name"]))
    return out


def _check_page(ctx: Ctx, meta: dict, values: dict, wizard: bool, status: int = 200, error: Optional[str] = None,
                running: Optional[Job] = None):
    last = meta["info"].get("last_date")
    return render(ctx, "system/company_restore_check.html", status, meta=meta, info=meta["info"], values=values,
                  last_date=format_date(last, ctx.user["dateformat"]) if last else "", error=error,
                  size=file_size(meta["size"], ctx.user["numberformat"]), wizard=wizard, step=4, base=_base(wizard),
                  running=_job_view(running) if running else None, EMP_NEW=EMP_NEW, EMP_NONE=EMP_NONE,
                  existing=already_there(ctx, meta["info"], values), languages=DOC_LANGUAGES)


def _meta_or_404(ctx: Ctx, token: str) -> dict:
    meta = _staged(ctx).get(token, ctx.user["id"])
    if not meta:
        raise HTTPException(404, _("Het bestand is niet meer beschikbaar; kies het opnieuw."))
    return meta


def check_form(ctx: Ctx, token: str, wizard: bool):
    meta = _meta_or_404(ctx, token)
    return _check_page(ctx, meta, _defaults(meta, ctx.user), wizard)


def _validate(ctx: Ctx, meta: dict, values: dict) -> Optional[str]:
    from .routes_system import _check_company
    try:
        _check_company(values)
    except accounts.AccountError as exc:
        return str(exc)
    if not NEW_DBNAME_RE.match(values["dbname"]):
        return _("Databasenaam: kleine letters, cijfers en _, beginnend met een letter.")
    if ctx.conn.execute("SELECT 1 FROM company WHERE lower(name) = lower(%s) OR "
                        "(code <> '' AND lower(code) = lower(%s))", (values["name"], values["code"])).fetchone():
        return _(CONFLICT)
    if ctx.conn.execute("SELECT 1 FROM company WHERE dbname = %s", (values["dbname"],)).fetchone():
        return _(DB_EXISTS)
    if values["language"] not in dict(DOC_LANGUAGES):
        return _("Onbekende documenttaal.")
    emp = values["employee"]
    ids = {str(e["id"]) for e in meta["info"].get("employees") or []}
    if emp not in (EMP_NEW, EMP_NONE) and not (emp.startswith("e:") and emp[2:] in ids):
        return _("Kies wie u bent in deze administratie.")
    try:
        with sqlledger.connect(**ctx.settings.company_conninfo("postgres"), connect_timeout=5) as admin:
            if admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (values["dbname"],)).fetchone():
                return _(DB_EXISTS)
    except psycopg.Error as exc:
        return _("Inlezen mislukt: {error}").format(error=str(exc).strip().splitlines()[0])
    return None


def check_post(ctx: Ctx, token: str, form: FormData, wizard: bool):
    meta = _meta_or_404(ctx, token)
    if form.get("action") == "cancel":
        _staged(ctx).delete(token)
        raise Redirect(_base(wizard))
    values = {"name": str(form.get("name", "")).strip(), "code": str(form.get("code", "")).strip(),
              "dbname": str(form.get("dbname", "")).strip(), "add_me": form.get("add_me") == "1",
              "employee": str(form.get("employee", EMP_NONE)), "modules": list(MODULES),
              "language": str(form.get("language", "")) or default_language(meta["info"], ctx.user)}
    if not values["add_me"]:
        values["employee"] = EMP_NONE
    busy = restorejobs.running()
    if busy:
        return _check_page(ctx, meta, values, wizard, 409, _(BUSY),
                           running=busy if busy.user_id == ctx.user["id"] else None)
    error = _validate(ctx, meta, values)
    if error:
        return _check_page(ctx, meta, values, wizard, 400, error)
    job = Job(id=restorejobs.new_id(), user_id=ctx.user["id"], token=token, dbname=values["dbname"],
              name=values["name"], wizard=wizard)
    settings, pool, user, ip = ctx.settings, ctx.request.app.state.pool, dict(ctx.user), ctx.ip
    if not restorejobs.start(job, lambda j: _work(j, settings, pool, user, ip, meta, values)):
        return _check_page(ctx, meta, values, wizard, 409, _(BUSY))
    raise Redirect(f"{_base(wizard)}/taak/{job.id}")


# ------------------------------------------------------------------ het inlezen zelf (achtergrond)

def _work(job: Job, settings: Settings, pool, user: dict, ip: Optional[str], meta: dict, values: dict) -> None:
    """Terugzetten, medewerker koppelen, overstaplijst aan, administratie + lidmaatschap in de app-database,
    sjablonen. Lukt een stap na het terugzetten niet, dan gaat de nieuwe database weer weg (alles of niets)."""
    staged = StagedBackups(settings.data_dir)
    marker = restorejobs.mark_start(settings.data_dir, job, user["login"])      # keuze 17: overleeft een herstart

    def progress(phase: str, done: int, total: int) -> None:
        if phase == "restore" and not done:
            restorejobs.mark_created(marker)        # database bestaat nu, aangemaakt door dit inlezen
        job.progress(phase, done, total)
    try:
        _work_inner(job, settings, pool, user, ip, meta, values, staged, progress)
    finally:
        restorejobs.mark_end(marker)


def _work_inner(job: Job, settings: Settings, pool, user: dict, ip: Optional[str], meta: dict, values: dict,
                staged: StagedBackups, progress) -> None:
    data = staged.data(job.token)
    zipdata = staged.templates(job.token)
    folder = Path(tempfile.mkdtemp(prefix="sqlledger-tpl-")) if zipdata else None
    try:
        tpl = None
        if zipdata:
            tpl = _check_templates(zipdata, folder)
            if isinstance(tpl, str):
                raise ValidationError(tpl)
        dbname = values["dbname"]

        def connect(d: str) -> psycopg.Connection:
            return sqlledger.connect(**settings.company_conninfo(d), connect_timeout=5)

        with connect("postgres") as admin:
            admin.autocommit = True
            res = slbackup.restore(admin, connect, dbname, data, progress=progress)
            del data
            job.progress("finish", 0, 1)
            try:
                with connect(dbname) as conn:
                    emp_login = link_employee(conn, values["employee"], user) if values["add_me"] else user["login"]
                    _enable_switch_list(conn, res, user)
                    # documenttaal ook in de administratie (F25 A); btw/ICP-menu meteen goed (niet pas na opslaan
                    # van de btw-instellingen)
                    newcompany.save_setting(conn, newcompany.DOC_LANGUAGE_SETTING, values["language"], emp_login)
                    flags = company_flags(conn, UserConfig(
                        login=emp_login, numberformat=user["numberformat"], dateformat=user["dateformat"],
                        countrycode=user["language"]))
                    # RI7: menu Beheer > Run my Accounts-gegevens (ook bij een back-up van deze app van zo'n
                    # administratie, BK7)
                    is_rma = bool(res.rma) or rmaimport.load_report(conn) is not None
                with pool.connection() as app:
                    with app.transaction():
                        if app.execute("SELECT 1 FROM company WHERE lower(name) = lower(%s) OR "
                                       "(code <> '' AND lower(code) = lower(%s))",
                                       (values["name"], values["code"])).fetchone():
                            raise ValidationError(CONFLICT)
                        cid = app.execute(
                            "INSERT INTO company (name, code, dbname, modules, active, doc_language, vat_return, icp, "
                            "rma) VALUES (%s, %s, %s, %s, true, %s, %s, %s, %s) RETURNING id",
                            (values["name"], values["code"], dbname, values["modules"], values["language"],
                             flags["vat_return"], flags["icp"], is_rma)).fetchone()["id"]
                        if values["add_me"]:
                            app.execute("INSERT INTO membership (user_id, company_id, role, employee_login) "
                                        "VALUES (%s, %s, 'admin', %s)", (user["id"], cid, emp_login))
                    up = res.upgrade
                    accounts.log_event(
                        app, "company_restored", user=user, ip=ip, target=values["name"], dbname=dbname,
                        filename=meta.get("filename") or "", kind=res.info.family or res.info.kind,
                        dataset=res.info.dataset,
                        version_from=up.from_version if up else "", version_to=up.to_version if up else "",
                        skipped=len(up.errors) if up else 0, templates=len(tpl[0]) if tpl else 0,
                        employee=emp_login if values["add_me"] else "")
            except BaseException:
                admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
                raise
        if folder and (folder / "templates" / "0").is_dir():
            shutil.copytree(folder / "templates" / "0", backups.templates_dir(settings, cid), dirs_exist_ok=True)
        res.templates = tpl
        staged.delete(job.token)
        job.result = {"cid": cid, "values": values, "res": res}
    finally:
        if folder:
            shutil.rmtree(folder, ignore_errors=True)


def link_employee(conn: psycopg.Connection, choice: str, user: dict) -> str:
    """Medewerker van de app-gebruiker in de ingelezen administratie (PB14); geeft de login voor
    ``membership.employee_login``. Een gekozen medewerker zonder login in SQL-Ledger krijgt de login van de
    app-gebruiker (als die nog vrij is); "nieuw" maakt een medewerker met naam en login van de app-gebruiker (of
    gebruikt die met deze login als hij er al is); "geen" = de login van de app-gebruiker, zoals tot nu toe."""
    login = user["login"]
    with conn.transaction(), conn.cursor() as cur:
        if choice.startswith("e:") and choice[2:].isdigit():
            cur.execute("SELECT id, login FROM employee WHERE id = %s", (int(choice[2:]),))
            row = cur.fetchone()
            if row is None:
                return login
            if (row[1] or "").strip():
                return row[1].strip()
            cur.execute("SELECT 1 FROM employee WHERE login = %s", (login,))
            if cur.fetchone() is None:
                cur.execute("UPDATE employee SET login = %s WHERE id = %s", (login, row[0]))
            return login
        if choice == EMP_NEW:
            cur.execute("SELECT 1 FROM employee WHERE login = %s", (login,))
            if cur.fetchone() is None:
                cur.execute("INSERT INTO employee (login, name) VALUES (%s, %s)",
                            (login, (user.get("name") or login)[:64]))
    return login


def _enable_switch_list(conn: psycopg.Connection, res, user: dict) -> None:
    """Overstaplijst aan (6d-2, I4): herkomst van de back-up vastleggen. Lukt dat niet: geen lijst, geen fout."""
    from sqlledger import switchover
    up = res.upgrade
    try:
        switchover.record_restore(conn, {
            "kind": res.info.kind, "family": res.info.family, "dataset": res.info.dataset,
            "version": res.info.version, "date": res.info.date,
            "version_from": up.from_version if up else "", "version_to": up.to_version if up else "",
            "skipped": len(up.errors) if up else 0}, user["login"])
    except psycopg.Error:
        conn.rollback()


# ------------------------------------------------------------------ stap 3: voortgang en resultaat

def error_text(exc: BaseException) -> str:
    """Melding bij een mislukt inlezen, in de taal van wie kijkt (de taak zelf draait zonder taal)."""
    if isinstance(exc, psycopg.errors.InsufficientPrivilege):
        return _("De databasegebruiker mag geen databases aanmaken (recht CREATEDB ontbreekt).")
    if isinstance(exc, LedgerError):
        return lib_message(exc)
    first = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return _("Inlezen mislukt: {error}").format(error=first)


def _job_view(job: Job) -> Dict[str, Any]:
    if job.running:
        text = _(PHASE_TEXT.get(job.phase, PHASE_TEXT["start"])).format(percent=job.percent, done=job.done,
                                                                        total=job.total)
    elif job.error is not None:
        text = error_text(job.error)
    else:
        text = _("Klaar.")
    return {"id": job.id, "name": job.name, "dbname": job.dbname, "running": job.running,
            "failed": not job.running and job.error is not None, "phase": job.phase, "percent": job.percent,
            "text": text, "href": f"{_base(job.wizard)}/taak/{job.id}"}


def _job_or_404(ctx: Ctx, job_id: str) -> Job:
    job = restorejobs.get(job_id, ctx.user["id"])
    if not job:
        raise HTTPException(404, _("Deze taak bestaat niet (meer)."))
    return job


def job_page(ctx: Ctx, job_id: str, wizard: bool):
    job = _job_or_404(ctx, job_id)
    view = _job_view(job)
    if job.running:
        order = [p for p, _l in PHASE_LABEL]
        current = order.index(job.phase) if job.phase in order else -1
        phases = [{"label": _(label), "state": "done" if i < current else ("now" if i == current else "")}
                  for i, (_k, label) in enumerate(PHASE_LABEL)]
        return render(ctx, "system/company_restoring.html", job=view, phases=phases, wizard=wizard, step=4,
                      status_url=f"{_base(wizard)}/taak/{job.id}/stand")
    if job.error is not None:
        meta = _staged(ctx).get(job.token, ctx.user["id"])
        report = getattr(job.error, "report", None)          # RI6: rmaimport.RmaCheckError
        return render(ctx, "system/company_restore_failed.html", job=view, failure=view["text"], wizard=wizard,
                      step=4, retry=f"{_base(wizard)}/controle/{job.token}" if meta else "", base=_base(wizard),
                      rma=rmareport.view(report, ctx.user) if report else None)
    r = job.result
    return render(ctx, "system/company_restored.html", values=r["values"], res=r["res"], cid=r["cid"],
                  wizard=wizard, step=4, rma=rmareport.view(r["res"].rma, ctx.user) if r["res"].rma else None)


def job_status(ctx: Ctx, job_id: str):
    view = _job_view(_job_or_404(ctx, job_id))
    return JSONResponse({"running": view["running"], "failed": view["failed"], "phase": view["phase"],
                         "percent": view["percent"], "text": view["text"]},
                        headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ routes (Systeem en eerste start)

@router.get("/systeem/back-up", name="restore_form")
def restore_form(ctx: Ctx = Depends(ctx_sysadmin)):
    return _upload_page(ctx, False)


@router.post("/systeem/back-up", name="restore_post")
def restore_post(ctx: Ctx = Depends(ctx_sysadmin), form: FormData = Depends(backup_form)):
    return upload(ctx, form, False)


@router.get("/systeem/back-up/controle/{token}", name="restore_check")
def restore_check(token: str, ctx: Ctx = Depends(ctx_sysadmin)):
    return check_form(ctx, token, False)


@router.post("/systeem/back-up/controle/{token}", name="restore_check_post")
def restore_check_post(token: str, ctx: Ctx = Depends(ctx_sysadmin), form: FormData = Depends(csrf_form)):
    return check_post(ctx, token, form, False)


@router.get("/systeem/back-up/taak/{job_id}", name="restore_job")
def restore_job(job_id: str, ctx: Ctx = Depends(ctx_sysadmin)):
    return job_page(ctx, job_id, False)


@router.get("/systeem/back-up/taak/{job_id}/stand", name="restore_job_status")
def restore_job_status(job_id: str, ctx: Ctx = Depends(ctx_sysadmin)):
    return job_status(ctx, job_id)


@router.get("/welkom/administratie/back-up", name="restore_wizard_form")
def restore_wizard_form(ctx: Ctx = Depends(ctx_sysadmin)):
    return _upload_page(ctx, True)


@router.post("/welkom/administratie/back-up", name="restore_wizard_post")
def restore_wizard_post(ctx: Ctx = Depends(ctx_sysadmin), form: FormData = Depends(backup_form)):
    return upload(ctx, form, True)


@router.get("/welkom/administratie/back-up/controle/{token}", name="restore_wizard_check")
def restore_wizard_check(token: str, ctx: Ctx = Depends(ctx_sysadmin)):
    return check_form(ctx, token, True)


@router.post("/welkom/administratie/back-up/controle/{token}", name="restore_wizard_check_post")
def restore_wizard_check_post(token: str, ctx: Ctx = Depends(ctx_sysadmin), form: FormData = Depends(csrf_form)):
    return check_post(ctx, token, form, True)


@router.get("/welkom/administratie/back-up/taak/{job_id}", name="restore_wizard_job")
def restore_wizard_job(job_id: str, ctx: Ctx = Depends(ctx_sysadmin)):
    return job_page(ctx, job_id, True)


@router.get("/welkom/administratie/back-up/taak/{job_id}/stand", name="restore_wizard_job_status")
def restore_wizard_job_status(job_id: str, ctx: Ctx = Depends(ctx_sysadmin)):
    return job_status(ctx, job_id)
