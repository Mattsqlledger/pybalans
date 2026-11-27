"""Back-up maken van een administratie (FASE6_ANALYSE.md 6d-1, I7).

``pg_dump`` in platte SQL zonder eigenaar en rechten, met gzip, zodat ook de eigen ``x_``-tabellen meegaan (de
back-up van SQL-Ledger zelf neemt die niet mee). Bovenaan een eigen kop met administratie, versie en datum; het
bestand is weer in te lezen met Systeem > Back-up inlezen (``slbackup``, soort ``app``). ``pg_dump`` draait als
subproces; het wachtwoord gaat via ``PGPASSWORD`` (niet op de opdrachtregel). Er wordt eerst naar een tijdelijk
bestand geschreven en pas aan het eind hernoemd: een afgebroken back-up laat geen half bestand achter.
"""

from __future__ import annotations

import gzip
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Optional

from .errors import LedgerError

#: eerste regel van een back-up van deze versie (``slbackup.info`` herkent hem)
HEADER = "-- PyBalans back-up"
#: kop van back-ups van vóór de naam PyBalans (v1.0.1, NB1); blijven leesbaar
OLD_HEADERS = ("-- SQL-Ledger (Python) back-up",)
#: laatste regel van een volledige pg_dump
COMPLETE = b"-- PostgreSQL database dump complete"
CHUNK = 1024 * 1024


class DumpError(LedgerError):
    pass


@dataclass
class DumpResult:
    path: Path
    size: int                 # bytes (gzip)
    version: str              # defaults.version van de administratie
    made: datetime


def header(dataset: str, version: str, company: str, made: datetime) -> str:
    # bewust geen regels "-- Dataset:" en "-- Version:": daaraan herkent SQL-Ledger (am.pl restore) zijn eigen
    # back-up, en zijn terugzetten wist eerst alle tabellen en kan een pg_dump (COPY, functies) niet lezen; zo
    # weigert SQL-Ledger dit bestand met "Not a SQL-Ledger backup!" (README open punten 6d-1, keuze 1a)
    return (f"{HEADER}\n-- App-Dataset: {dataset}\n-- App-Version: {version}\n-- Date: {made:%Y-%m-%d %H:%M}\n"
            f"-- Company: {company}\n--\n")


def dump(conninfo: Mapping[str, object], path: Path, *, version: str = "", company: str = "",
         pg_dump: str = "pg_dump", timeout: int = 3600, now: Optional[datetime] = None) -> DumpResult:
    """Database ``conninfo['dbname']`` als ``.sql.gz`` naar ``path``."""
    path = Path(path)
    made = now or datetime.now()
    env = {**os.environ, "PGCONNECT_TIMEOUT": "10"}
    if conninfo.get("password"):
        env["PGPASSWORD"] = str(conninfo["password"])
    args = [pg_dump, "--format=plain", "--no-owner", "--no-privileges", "--encoding=UTF8"]
    for opt, key in (("--host", "host"), ("--port", "port"), ("--username", "user")):
        if conninfo.get(key):
            args += [opt, str(conninfo[key])]
    args += ["--dbname", str(conninfo["dbname"])]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmpname = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmpname)
    try:
        with os.fdopen(fd, "wb") as raw, tempfile.TemporaryFile() as err:
            try:
                proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=err, env=env)
            except OSError as exc:
                raise DumpError(f"pg_dump niet gevonden of niet uit te voeren ({pg_dump}): {exc.strerror}") from exc
            tail = b""
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
                gz.write(header(str(conninfo["dbname"]), version, company, made).encode("utf-8"))
                assert proc.stdout is not None
                while True:
                    block = proc.stdout.read(CHUNK)
                    if not block:
                        break
                    gz.write(block)
                    tail = (tail + block)[-200:]
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                raise DumpError("pg_dump duurde te lang en is afgebroken.")
            if code != 0:
                err.seek(0)
                lines = err.read().decode("utf-8", "replace").strip().splitlines()
                detail = lines[-1] if lines else f"code {code}"
                raise DumpError(f"pg_dump mislukt: {detail}")
            if COMPLETE not in tail:
                raise DumpError("pg_dump gaf geen volledige back-up.")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return DumpResult(path, path.stat().st_size, version, made)


def filename(dbname: str, made: datetime, suffix: str = ".sql.gz") -> str:
    """``<dbname>-JJJJMMDD-UUMM.sql.gz``; sorteert op datum."""
    return f"{dbname}-{made:%Y%m%d-%H%M}{suffix}"


def rotate(folder: Path, dbname: str, keep: int, suffix: str = ".sql.gz") -> list:
    """Alleen de ``keep`` nieuwste back-ups van ``dbname`` (met dit achtervoegsel) laten staan; geeft de
    verwijderde bestanden. Alleen bestanden met precies de vorm van ``filename`` tellen mee."""
    import re
    pat = re.compile(rf"^{re.escape(dbname)}-\d{{8}}-\d{{4}}{re.escape(suffix)}$")
    files = sorted((p for p in Path(folder).iterdir() if p.is_file() and pat.match(p.name)),
                   key=lambda p: p.name, reverse=True)
    gone = files[keep:] if keep > 0 else []
    for p in gone:
        p.unlink()
    return gone
