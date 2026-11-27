"""Back-up inlezen in twee stappen met voortgang (v1.0.1 PB1–PB5).

* **Klaarzetten** (``StagedBackups``): het geüploade bestand staat tot het inlezen in de datamap (``restore/``), met
  wat er in staat (``slbackup.preview``) in een JSON-bestand ernaast, zoals bijlagen tot het boeken in ``uploads/``
  staan (``uploads.PendingFiles``). Alleen dezelfde gebruiker kan het zien of inlezen; na het inlezen, bij Annuleren
  of na een dag verdwijnt het.
* **Inlezen als achtergrondtaak** (``Job``): een eigen thread met eigen databaseverbindingen; de pagina vraagt elke
  seconde de stand op. Het venster sluiten stopt de taak niet. Er loopt hoogstens één inlezen tegelijk per
  installatie (keuze 3 in README "v1.0.1 inlezen en overstap"): een back-up van 100 MB vraagt veel geheugen en
  databasewerk, en zo kan dezelfde databasenaam ook nooit twee keer tegelijk worden aangemaakt.

De taken staan alleen in het geheugen van het serverproces: na een herstart is de stand weg (een half ingelezen
database is dan al weer verwijderd door ``slbackup.restore``, behalve bij een harde stop).
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from starlette.datastructures import UploadFile

from sqlledger import slbackup
from sqlledger.errors import ValidationError

#: klaargezette back-ups en afgeronde taken zo lang bewaren
MAX_AGE = 24 * 3600
TOKEN = re.compile(r"[0-9a-f]{32}")
CHUNK = 1024 * 1024


class StagedBackups:
    """Geüploade back-ups tot het inlezen (``<data_dir>/restore/<token>.bin`` + ``.json``, eventueel ``.zip``)."""

    def __init__(self, data_dir: str):
        self.dir = Path(data_dir).expanduser() / "restore"

    def _path(self, token: str, ext: str) -> Path:
        return self.dir / f"{token}.{ext}"

    def save(self, user_id: int, upload: UploadFile, limit: int, templates: Optional[bytes] = None) -> str:
        """Bestand wegschrijven (hoogstens ``limit`` bytes, anders ValidationError), voorbeeld maken
        (``BackupError`` als het geen back-up is: dan blijft er niets staan). Geeft het token."""
        self.cleanup()
        self.dir.mkdir(parents=True, exist_ok=True)
        token = secrets.token_hex(16)
        content = self._path(token, "bin")
        try:
            size = 0
            upload.file.seek(0)
            with content.open("wb") as out:
                while True:
                    chunk = upload.file.read(CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > limit:
                        raise ValidationError("too big")
                    out.write(chunk)
            info = slbackup.preview(content.read_bytes())
            if templates:
                self._path(token, "zip").write_bytes(templates)
            meta = {"user_id": int(user_id), "filename": upload.filename or "", "size": size,
                    "created": time.time(), "templates": bool(templates),
                    "info": {"kind": info.kind, "dataset": info.dataset, "version": info.version, "date": info.date,
                             "company": info.company,
                             "last_date": info.last_date.isoformat() if info.last_date else "",
                             "employees": info.employees, "language": info.language, "family": info.family}}
            self._path(token, "json").write_text(json.dumps(meta, ensure_ascii=False))
        except BaseException:
            self.delete(token)
            raise
        return token

    def get(self, token: str, user_id: int) -> Optional[Dict[str, Any]]:
        """Gegevens van een klaargezette back-up, alleen voor dezelfde gebruiker; None als hij er niet (meer) is."""
        if not TOKEN.fullmatch(token or ""):
            return None
        try:
            meta = json.loads(self._path(token, "json").read_text())
        except (OSError, ValueError):
            return None
        if meta.get("user_id") != int(user_id) or not self._path(token, "bin").is_file():
            return None
        if time.time() - float(meta.get("created") or 0) > MAX_AGE:
            self.delete(token)
            return None
        meta["token"] = token
        last = (meta.get("info") or {}).get("last_date")
        meta["info"]["last_date"] = date.fromisoformat(last) if last else None
        return meta

    def data(self, token: str) -> bytes:
        return self._path(token, "bin").read_bytes()

    def templates(self, token: str) -> Optional[bytes]:
        p = self._path(token, "zip")
        return p.read_bytes() if p.is_file() else None

    def delete(self, token: str) -> None:
        if not TOKEN.fullmatch(token or ""):
            return
        for ext in ("bin", "json", "zip"):
            self._path(token, ext).unlink(missing_ok=True)

    def cleanup(self) -> None:
        """Wat ouder is dan een dag weg (ook als de gebruiker nooit terugkwam)."""
        if not self.dir.is_dir():
            return
        limit = time.time() - MAX_AGE
        for p in self.dir.iterdir():
            try:
                if p.stat().st_mtime < limit:
                    p.unlink()
            except OSError:
                pass


# ------------------------------------------------------------------ taken

#: fasen in volgorde; "done"/"failed" = klaar
PHASES = ("start", "check", "restore", "upgrade", "finish")


@dataclass
class Job:
    id: str
    user_id: int
    token: str
    dbname: str
    name: str
    wizard: bool = False
    phase: str = "start"
    done: int = 0
    total: int = 0
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    error: Optional[BaseException] = None
    result: Dict[str, Any] = field(default_factory=dict)

    def progress(self, phase: str, done: int, total: int) -> None:
        self.phase, self.done, self.total = phase, done, total

    @property
    def running(self) -> bool:
        return self.finished is None

    @property
    def percent(self) -> int:
        """Voortgang van de huidige fase in hele procenten."""
        if not self.total:
            return 0
        return max(0, min(100, int(self.done * 100 / self.total)))


_JOBS: Dict[str, Job] = {}
_LOCK = threading.Lock()


def _prune() -> None:
    limit = time.time() - MAX_AGE
    for k in [k for k, j in _JOBS.items() if j.finished and j.finished < limit]:
        del _JOBS[k]


def running() -> Optional[Job]:
    with _LOCK:
        return next((j for j in _JOBS.values() if j.running), None)


def get(job_id: str, user_id: int) -> Optional[Job]:
    with _LOCK:
        j = _JOBS.get(job_id or "")
    return j if j and j.user_id == int(user_id) else None


def for_user(user_id: int) -> List[Job]:
    """Lopende en afgeronde taken van deze gebruiker (laatste dag), nieuwste eerst."""
    with _LOCK:
        _prune()
        return sorted((j for j in _JOBS.values() if j.user_id == int(user_id)), key=lambda j: -j.started)


def start(job: Job, work: Callable[[Job], None]) -> bool:
    """Taak starten in een eigen thread; False als er al een inlezen loopt (keuze 3)."""
    with _LOCK:
        _prune()
        if any(j.running for j in _JOBS.values()):
            return False
        _JOBS[job.id] = job

    def run() -> None:
        try:
            work(job)
        except BaseException as exc:          # elke fout wordt een melding op de pagina, nooit een losse thread-fout
            job.error = exc
        finally:
            job.finished = time.time()

    threading.Thread(target=run, name=f"restore-{job.dbname}", daemon=True).start()
    return True


def new_id() -> str:
    return secrets.token_hex(16)


# ------------------------------------------------------------------ harde stop tijdens inlezen (v1.0.1 keuze 17)
#
# Een markering per lopend inlezen in ``<data_dir>/restore-runs/<taak>.json`` (overleeft een herstart): databasenaam,
# naam, gebruiker, start, en ``created`` zodra ``slbackup.restore`` de database heeft aangemaakt (eerste
# voortgangsmelding van de fase "restore"; vóór die tijd bestond de database aantoonbaar nog niet, want restore weigert
# een bestaande). Na afloop (gelukt of mislukt) gaat de markering weg. Staat er bij de start van de server nog een
# markering zonder lopende taak, dan is het inlezen afgebroken: de database gaat alleen weg als ``created`` waar is én
# er geen administratie met die databasenaam is; daarna blijft een melding een dag staan (``aborted-…json``).

def _runs(data_dir: str) -> Path:
    return Path(data_dir).expanduser() / "restore-runs"


def mark_start(data_dir: str, job: Job, login: str) -> Path:
    d = _runs(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{job.id}.json"
    p.write_text(json.dumps({"job": job.id, "dbname": job.dbname, "name": job.name, "user_id": job.user_id,
                             "login": login, "started": time.time(), "created": False}, ensure_ascii=False))
    return p


def mark_created(path: Path) -> None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    if not data.get("created"):
        data["created"] = True
        path.write_text(json.dumps(data, ensure_ascii=False))


def mark_end(path: Path) -> None:
    path.unlink(missing_ok=True)


def recover(data_dir: str, admin_connect: Callable[[], Any], registered: Callable[[str], bool],
            log: Callable[..., None]) -> List[Dict[str, Any]]:
    """Afgebroken inlezen opruimen (bij de start van de server en bij het openen van Back-up inlezen). Slaat
    markeringen van taken die in dit proces nog lopen over. ``registered(dbname)``: is er een administratie met die
    database (dan nooit droppen). Geeft de opgeruimde markeringen."""
    d = _runs(data_dir)
    if not d.is_dir():
        return []
    with _LOCK:
        live = {j.id for j in _JOBS.values() if j.running}
    out = []
    for p in sorted(d.glob("*.json")):
        if p.name.startswith("aborted-"):
            continue
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if data.get("job") in live:
            continue
        dbname = str(data.get("dbname") or "")
        dropped = False
        if data.get("created") and re.fullmatch(r"[a-z][a-z0-9_]{0,62}", dbname) and not registered(dbname):
            with admin_connect() as admin:
                admin.autocommit = True
                if admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone():
                    admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
                    dropped = True
        data.update(dropped=dropped, found=time.time())
        (d / f"aborted-{p.name}").write_text(json.dumps(data, ensure_ascii=False))
        p.unlink(missing_ok=True)
        log(data)
        out.append(data)
    return out


def pending_dbnames(data_dir: str) -> set:
    """Databasenamen met een markering van een (lopend of afgebroken, nog niet opgeruimd) inlezen. Een
    administratie met zo'n database ontkoppelen zou ``recover`` de database laten weggooien (F27b)."""
    d = _runs(data_dir)
    out = set()
    for p in d.glob("*.json") if d.is_dir() else ():
        if p.name.startswith("aborted-"):
            continue
        try:
            out.add(str(json.loads(p.read_text()).get("dbname") or ""))
        except (OSError, ValueError):
            continue
    return out


def aborted(data_dir: str) -> List[Dict[str, Any]]:
    """Meldingen van afgebroken inlezen van de laatste dag (oudere worden opgeruimd)."""
    d = _runs(data_dir)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("aborted-*.json")):
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if time.time() - float(data.get("found") or 0) > MAX_AGE:
            p.unlink(missing_ok=True)
            continue
        out.append(data)
    return out
