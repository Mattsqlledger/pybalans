"""Een SQL-Ledger-back-up inlezen als nieuwe administratie (FASE6_ANALYSE.md F1/F2, stap 6a).

Bestanden: de back-up van SQL-Ledger (admin.pl/am.pl ``AM::backup``: ``-- SQL-Ledger Backup``, ook gzip/bzip2) en een
``pg_dump`` in platte SQL (``COPY … FROM stdin``). Een geüpload SQL-bestand is code: het wordt hier **niet** aan psql
gegeven, maar per statement gelezen (rekening houdend met aanhalingstekens, ``$$``-blokken en commentaar) en alleen
uitgevoerd als het in een back-up hoort (tabellen, sequences, indexen, views, functies/triggers in plpgsql of sql,
INSERT, COPY FROM stdin, setval, veilige SET-opdrachten). Eigenaar-, rechten- en commentaarregels worden overgeslagen;
al het andere (COPY TO/PROGRAM, extensies, andere talen, rollen, psql-opdrachten) = geweigerd met het regelnummer.
Een back-up van Run my Accounts of ledger123 (herkend aan een RMA-tabel of een versie 2.8.11 … 2.8.x) volgt het
RMA-pad (RMA_IMPORT_ANALYSE.md RI1–RI7, ``rmaimport``): daar mogen ook ``CREATE DATABASE``/``\\connect`` (``pg_dump -C``)
en ENUM-typen, de extensie fuzzystrmatch wordt overgeslagen; vóór de keten gaat alles buiten het originele
2.8.10-schema naar schema ``rma``.
Terugzetten in een **nieuwe** database als de gewone databasegebruiker, in één transactie; daarna bijwerken naar 3.2.4
(``slupgrade``). Mislukt er iets, dan gaat de nieuwe database weer weg.
"""

from __future__ import annotations

import bz2
import gzip
import io
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Dict, Iterator, List, Optional, Tuple

import psycopg
from psycopg import sql as psql

from .dbbackup import HEADER, OLD_HEADERS
from .errors import LedgerError, ValidationError
from .slupgrade import RMA_TABLES, UpgradeResult, calc_version, db_version, upgrade


_RMA_BYTES = re.compile((r"^CREATE TABLE (?:public\.)?(?:%s) *\(" % "|".join(RMA_TABLES)).encode(), re.M | re.I)


class BackupError(LedgerError):
    pass


@dataclass
class Statement:
    line: int
    text: str
    copy_data: Optional[str] = None       # rijen van COPY … FROM stdin
    end: int = 0                          # positie (teken) na het statement, voor de voortgang (PB4)


@dataclass
class BackupInfo:
    kind: str                             # sl | pg_dump | app (Back-up maken van deze versie, 6d-1)
    dataset: str = ""
    version: str = ""
    date: str = ""
    statements: int = 0
    # controlescherm vóór het inlezen (v1.0.1 PB1/PB2/PB14; ``preview``)
    company: str = ""                     # defaults fldname 'company'
    last_date: Optional[date] = None      # laatste datum in acc_trans (leeg als niet te bepalen)
    employees: List[Dict[str, str]] = field(default_factory=list)   # id, login, name, enddate
    language: str = ""                    # documenttaal (``x_setting`` doc_language, alleen back-up van deze app; F25)
    family: str = ""                      # "rma": Run my Accounts / ledger123 (RI7); leeg = SQL-Ledger of deze app


def unpack(data: bytes) -> bytes:
    """Uitpakken als het gzip (SQL-Ledger, RMA via het scherm) of bzip2 is (RMA via mojo-admin, RI8 S4)."""
    if data[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(data)
        except OSError as exc:
            raise BackupError("Het gzip-bestand is beschadigd.") from exc
    if data[:3] == b"BZh":
        try:
            return bz2.decompress(data)
        except (OSError, ValueError) as exc:
            raise BackupError("Het bz2-bestand is beschadigd.") from exc
    return data


def decode(data: bytes) -> str:
    data = unpack(data)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def info(text: str) -> BackupInfo:
    head = text[:4000]
    if head.startswith(("-- SQL-Ledger Backup", HEADER) + OLD_HEADERS):
        b = BackupInfo("sl" if head.startswith("-- SQL-Ledger Backup") else "app")
        for key, attr in (("Dataset", "dataset"), ("Version", "version"), ("Date", "date")):
            # back-up van deze versie: "-- App-Dataset:"/"-- App-Version:" (dbbackup.header); de eerste back-ups
            # van 6d-1 hadden nog "-- Dataset:"/"-- Version:" zoals SL
            m = re.search(rf"^-- (?:App-)?{key}: (.*)$", head, re.M)
            if m:
                setattr(b, attr, m.group(1).strip())
        return b
    if "PostgreSQL database dump" in head:
        return BackupInfo("pg_dump")
    raise BackupError("Dit is geen back-up van SQL-Ledger (verwacht: '-- SQL-Ledger Backup' of een pg_dump in "
                      "platte SQL).")


#: ``\\restrict <sleutel>`` / ``\\unrestrict <sleutel>``: nieuwere pg_dump-versies zetten deze psql-opdrachten om de
#: dump heen (alleen een beveiliging van psql zelf); overslaan, al het andere met ``\\`` blijft geweigerd
_RESTRICT = re.compile(r"^\\(un)?restrict\s+[A-Za-z0-9]+$")
_CONNECT = re.compile(r"^\\connect\s+(-reuse-previous=on\s+)?(\"[^\"]+\"|'[^']+'|[\w-]+)$")
_DOLLAR = re.compile(r"\$([A-Za-z_]\w*)?\$")
#: tekens zonder betekenis voor ``split`` (alles behalve aanhalingstekens, $, ;, regeleinde en het begin van
#: commentaar)
_PLAIN = re.compile(r"[^'\"$;\n/-]+")


def split(text: str, rma: bool = False) -> Iterator[Statement]:
    """Statements met hun eerste regelnummer; COPY … FROM stdin neemt de regels tot ``\\.`` mee. ``rma``: één
    ``\\connect`` (na ``CREATE DATABASE`` van ``pg_dump -C``, BK5) wordt overgeslagen."""
    i, n, line = 0, len(text), 1
    connects = 1 if rma else 0
    start, buf = None, []
    while i < n:
        c = text[i]
        if start is None:
            if c in " \t\r\n":
                if c == "\n":
                    line += 1
                i += 1
                continue
            if text.startswith("--", i):
                j = text.find("\n", i)
                i = n if j < 0 else j
                continue
            if c == "\\":                                       # psql-opdracht op een eigen regel
                j = text.find("\n", i)
                cmd = text[i:n if j < 0 else j].strip()
                if _RESTRICT.match(cmd):                          # pg_dump ≥ 15.14/16.10/17.6 (6d-1)
                    i = n if j < 0 else j
                    continue
                if connects and _CONNECT.match(cmd):
                    connects -= 1
                    i = n if j < 0 else j
                    continue
                raise BackupError(f"Regel {line}: psql-opdracht {cmd[:30]} is niet toegestaan.")
            start = line
        if text.startswith("--", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                raise BackupError(f"Regel {line}: commentaar zonder einde.")
            line += text.count("\n", i, j)
            i = j + 2
            continue
        if c == "'":
            j = i + 1
            while True:
                j = text.find("'", j)
                if j < 0:
                    raise BackupError(f"Regel {start}: tekst zonder afsluitend aanhalingsteken.")
                if text.startswith("''", j):
                    j += 2
                    continue
                break
            buf.append(text[i:j + 1])
            line += text.count("\n", i, j)
            i = j + 1
            continue
        if c == '"':
            j = text.find('"', i + 1)
            if j < 0:
                raise BackupError(f"Regel {start}: naam zonder afsluitend aanhalingsteken.")
            buf.append(text[i:j + 1])
            i = j + 1
            continue
        if c == "$":
            m = _DOLLAR.match(text, i)
            if m:
                tag = m.group(0)
                j = text.find(tag, i + len(tag))
                if j < 0:
                    raise BackupError(f"Regel {start}: {tag}-blok zonder einde.")
                buf.append(text[i:j + len(tag)])
                line += text.count("\n", i, j)
                i = j + len(tag)
                continue
        if c == ";":
            stmt = "".join(buf).strip()
            buf, i = [], i + 1
            copy_data = None
            if re.match(r"(?is)^copy\s.+\sfrom\s+stdin\b", stmt):
                j = text.find("\n", i)
                k = text.find("\n\\.", j)
                if j < 0 or k < 0:
                    raise BackupError(f"Regel {start}: COPY zonder einde (\\.).")
                copy_data = text[j + 1:k + 1]
                line += text.count("\n", i, k + 3)
                e = text.find("\n", k + 1)
                i = n if e < 0 else e
            yield Statement(start, stmt, copy_data, i)
            start = None
            continue
        if c == "\n":
            line += 1
            buf.append(c)
            i += 1
            continue
        m = _PLAIN.match(text, i)                 # gewone tekens in één keer (administratie A: 16 s → enkele s)
        if m:
            buf.append(m.group())
            i = m.end()
            continue
        buf.append(c)
        i += 1
    rest = "".join(buf).strip()
    if rest:
        yield Statement(start or line, rest, None, n)


#: overslaan (hoort bij een dump maar is niet nodig of hoort bij de oude server)
_SKIP = re.compile(r"(?is)^(drop\s|comment\s+on\s|grant\s|revoke\s|alter\s[^;]*?\bowner\s+to\s|"
                   r"create\s+schema\s|set\s+default_with_oids|select\s+pg_catalog\.set_config\s*\(\s*'search_path')")
_SET_OK = {"datestyle", "client_encoding", "standard_conforming_strings", "statement_timeout", "lock_timeout",
           "idle_in_transaction_session_timeout", "check_function_bodies", "xmloption", "client_min_messages",
           "row_security", "default_tablespace", "default_table_access_method", "escape_string_warning",
           "transaction_timeout"}
_ALLOW = re.compile(r"(?is)^(create\s+(table|sequence|(unique\s+)?index|view)\s|alter\s+(table|sequence)\s|"
                    r"insert\s+into\s|copy\s+[\w.\"]+(\s*\([^)]*\))?\s+from\s+stdin\b|"
                    r"select\s+(pg_catalog\.)?(setval|nextval)\s*\(|create\s+(or\s+replace\s+)?(function|trigger)\s)")


#: alleen in het RMA-pad (BK5, RI3): de database van ``pg_dump -C`` en de extensie fuzzystrmatch (alleen gebruikt
#: voor zoeken in RMA's banklaag) overslaan; ENUM-typen toestaan
_RMA_SKIP = re.compile(r"(?is)^(create\s+database\s|alter\s+database\s|"
                       r"create\s+extension\s+(if\s+not\s+exists\s+)?\"?fuzzystrmatch\"?(\s+with\s+schema\s+[\w.\"]+)?$)")
_ENUM = re.compile(r"(?is)^create\s+type\s+[\w.\"]+\s+as\s+enum\s*\(\s*(?:'(?:[^']|'')*+'(?:\s*,\s*'(?:[^']|'')*+')*+)?\s*\)$")


#: back-up van deze app van een ingelezen RMA-administratie (BK7): schema rma en de ENUM-typen daarin
_APP_RMA_SCHEMA = re.compile(r'(?is)^create\s+schema\s+(if\s+not\s+exists\s+)?"?rma"?$')
_APP_RMA_ENUM = re.compile(r'(?is)^create\s+type\s+"?rma"?\.')


def check(st: Statement, rma: bool = False, app: bool = False) -> str:
    """'skip', 'run' of BackupError. ``rma``: RMA-pad (``_RMA_SKIP``, ENUM). ``app``: back-up van deze app (schema rma
    en ENUM-typen in rma, BK7)."""
    t = st.text.strip()
    if app and (_APP_RMA_SCHEMA.match(t) or (_APP_RMA_ENUM.match(t) and _ENUM.match(t))):
        return "run"
    if _SKIP.match(t):
        return "skip"
    if rma:
        if _RMA_SKIP.match(t):
            return "skip"
        if _ENUM.match(t):
            return "run"
    m = re.match(r"(?is)^set\s+(session\s+)?([a-z_]+)", t)
    if m:
        if m.group(2).lower() in _SET_OK:
            return "run"
        raise BackupError(f"Regel {st.line}: SET {m.group(2)} is niet toegestaan.")
    if not _ALLOW.match(t):
        raise BackupError(f"Regel {st.line}: dit hoort niet in een back-up: {' '.join(t.split())[:60]}")
    if re.match(r"(?is)^create\s+(or\s+replace\s+)?function\s", t):
        lang = re.search(r"(?is)\blanguage\s+'?\"?(\w+)", t)
        if not lang or lang.group(1).lower() not in ("plpgsql", "sql"):
            raise BackupError(f"Regel {st.line}: een functie mag alleen in plpgsql of sql.")
    return "run"


# ------------------------------------------------------------------ voorbeeld (v1.0.1 PB1/PB2/PB14)

#: begin van de rijen van een tabel: SL-back-up (``INSERT INTO t (kolommen) VALUES (``) of pg_dump/back-up van deze
#: app (``COPY public.t (kolommen) FROM stdin;``)
_ROWS = r"^(?:INSERT INTO (?:public\.)?{t} \(([^)]*)\) VALUES \(|COPY (?:public\.)?\"?{t}\"? \(([^)]*)\) FROM stdin;\n)"
_BARE = re.compile(rb"[^,)]*")
_DATESTYLE = re.compile(rb"(?im)^set\s+datestyle\s+to\s+'([^']*)'")
_COPY_ESC = re.compile(rb"\\(.)")
_COPY_MAP = {b"t": b"\t", b"n": b"\n", b"r": b"\r", b"\\": b"\\"}


def _text(b: Optional[bytes]) -> Optional[str]:
    if b is None:
        return None
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp1252", errors="replace")


def _values(buf: bytes, i: int) -> Tuple[List[Optional[bytes]], int]:
    """Waarden van ``VALUES (…)`` vanaf positie ``i`` (na het haakje): tekst tussen enkele aanhalingstekens
    (``''`` = één), NULL of een los woord. Geeft (waarden, positie na het sluithaakje); ValueError als het niet
    te lezen is."""
    out: List[Optional[bytes]] = []
    n = len(buf)
    while i < n:
        while i < n and buf[i] in b" \t\r\n":
            i += 1
        if buf[i:i + 1] == b"'":
            j, parts = i + 1, []
            while True:
                k = buf.find(b"'", j)
                if k < 0:
                    raise ValueError("open string")
                if buf[k + 1:k + 2] == b"'":
                    parts.append(buf[j:k + 1])
                    j = k + 2
                    continue
                parts.append(buf[j:k])
                i = k + 1
                break
            out.append(b"".join(parts))
        else:
            m = _BARE.match(buf, i)
            tok = m.group().strip()
            i = m.end()
            out.append(None if tok.upper() == b"NULL" else tok)
        while i < n and buf[i] in b" \t\r\n":
            i += 1
        if buf[i:i + 1] == b",":
            i += 1
            continue
        if buf[i:i + 1] == b")":
            return out, i + 1
        raise ValueError("geen , of )")
    raise ValueError("geen einde")


def _columns(spec: bytes) -> List[str]:
    return [c.strip().strip(b'"').decode("ascii", "replace").lower() for c in spec.split(b",")]


def table_rows(data: bytes, table: str) -> Iterator[Dict[str, Optional[str]]]:
    """Rijen van ``table`` uit een (uitgepakte) back-up zonder die uit te voeren of helemaal te ontleden: elke
    ``INSERT INTO`` (SQL-Ledger) of het ``COPY``-blok (pg_dump, back-up van deze app). Waarden als tekst (None =
    NULL); een rij die niet te lezen is, wordt overgeslagen."""
    rx = re.compile(_ROWS.format(t=re.escape(table)).encode(), re.M)
    for m in rx.finditer(data):
        if m.group(1) is not None:
            cols = _columns(m.group(1))
            try:
                vals, _end = _values(data, m.end())
            except (ValueError, IndexError):
                continue
            if len(vals) == len(cols):
                yield {c: _text(v) for c, v in zip(cols, vals)}
            continue
        cols = _columns(m.group(2))
        end = data.find(b"\n\\.\n", m.end() - 1)
        block = data[m.end():end + 1 if end >= 0 else len(data)]
        for line in block.split(b"\n"):
            if not line:
                continue
            vals = [None if v == b"\\N" else _COPY_ESC.sub(lambda x: _COPY_MAP.get(x.group(1), x.group(1)), v)
                    for v in line.split(b"\t")]
            if len(vals) == len(cols):
                yield {c: _text(v) for c, v in zip(cols, vals)}


def _date_reader(data: bytes) -> Callable[[str], Optional[date]]:
    """Datum uit een back-up: ISO (pg_dump) of zoals de DateStyle van de SL-back-up (``POSTGRES, EUROPEAN`` =
    dd-mm-jjjj; anders mm-dd-jjjj)."""
    m = _DATESTYLE.search(data[:2_000_000])
    style = (m.group(1) if m else b"").upper()
    dmy = b"EURO" in style or b"DMY" in style or b"GERMAN" in style

    def read(value: str) -> Optional[date]:
        v = (value or "").strip()
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v[:10]) and (len(v) == 10 or not v[10:11].isdigit()):
                return date.fromisoformat(v[:10])
            p = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", v)
            if p:
                a, b, y = int(p.group(1)), int(p.group(2)), int(p.group(3))
                return date(y, b, a) if dmy else date(y, a, b)
        except ValueError:
            return None
        return None
    return read


def rma_family(data: bytes, b: BackupInfo) -> bool:
    """Run my Accounts / ledger123 (RI7): een pg_dump of SL-back-up met een RMA-tabel of met versie 2.8.11 … 2.8.x
    (die bestaat in SQL-Ledger zelf niet); niet een back-up van deze app (die heeft de RMA-tabellen al in ``rma``). Zet ``b.family`` en ``b.version`` (uit ``defaults``); ``data`` uitgepakt."""
    if b.kind not in ("pg_dump", "sl"):
        return False
    version = next((r.get("fldvalue") or "" for r in table_rows(data, "defaults") if r.get("fldname") == "version"), "")
    v = calc_version(version)
    if not (_RMA_BYTES.search(data) or calc_version("2.8.10") < v < calc_version("2.9.0")):
        return False
    b.family, b.version = "rma", version.strip()
    return True


def preview(data: bytes) -> BackupInfo:
    """Wat er in de back-up staat, voor het controlescherm vóór het inlezen: soort, dataset, versie en datum (kop),
    bedrijfsnaam (``defaults`` 'company'; staat in een SL-back-up vaak helemaal achteraan), laatste datum in
    ``acc_trans``, de medewerkers en de documenttaal (F25). Leest alleen; de volledige controle gebeurt bij het inlezen (``scan``)."""
    data = unpack(data)
    b = info(decode(data[:8000]))
    rma_family(data, b)
    for r in table_rows(data, "defaults"):
        if r.get("fldname") == "company":
            b.company = (r.get("fldvalue") or "").strip()
            break
    else:
        m = re.search(r"^-- Company: (.*)$", decode(data[:4000]), re.M)          # kop van deze app
        b.company = m.group(1).strip() if m else ""
    read = _date_reader(data)
    last: Optional[date] = None
    for r in table_rows(data, "acc_trans"):
        d = read(r.get("transdate") or "")
        if d and (last is None or d > last):
            last = d
    b.last_date = last
    for r in table_rows(data, "employee"):
        b.employees.append({"id": r.get("id") or "", "login": (r.get("login") or "").strip(),
                            "name": (r.get("name") or "").strip(), "enddate": r.get("enddate") or ""})
    b.employees.sort(key=lambda e: (e["name"].lower(), e["login"].lower()))
    for r in table_rows(data, "x_setting"):
        if r.get("key") == "doc_language":
            b.language = (r.get("value") or "").strip()
            break
    return b


@dataclass
class RestoreResult:
    info: BackupInfo
    upgrade: Optional[UpgradeResult] = None
    skipped: int = 0
    rows_copied: int = 0
    warnings: List[str] = field(default_factory=list)
    templates: Optional[tuple] = None     # webapp: (opgeslagen, overgeslagen) sjablonen uit de zip (6d-1)
    rma: Optional[dict] = None            # inleesrapport Run my Accounts / ledger123 (RI6/RI7, ``rmaimport``)


#: voortgang (v1.0.1 PB4): ``progress(fase, gedaan, totaal)``; fase "check" (lezen en controleren, tekens),
#: "restore" (terugzetten, tekens), "upgrade" (bijwerken, stappen), "finish" (eigen tabellen)
Progress = Callable[[str, int, int], None]


def _none(phase: str, done: int, total: int) -> None:
    pass


def scan(data: bytes, progress: Optional[Progress] = None) -> Tuple[BackupInfo, List[Statement]]:
    """Lezen en controleren zonder iets uit te voeren (voor het voorbeeld en vóór het aanmaken)."""
    progress = progress or _none
    data = unpack(data)
    text = decode(data)
    b = info(text)
    rma = rma_family(data, b)
    total = len(text)
    progress("check", 0, total)
    stmts = []
    for st in split(text, rma):
        check(st, rma, b.kind == "app")
        stmts.append(st)
        if len(stmts) % 2000 == 0:
            progress("check", st.end, total)
    progress("check", total, total)
    b.statements = len(stmts)
    return b, stmts


def restore(admin: psycopg.Connection, connect: Callable[[str], psycopg.Connection], dbname: str,
            data: bytes, progress: Optional[Progress] = None) -> RestoreResult:
    """Nieuwe database ``dbname`` maken en de back-up erin terugzetten, daarna bijwerken naar 3.2.4. ``progress``:
    zie ``Progress``."""
    progress = progress or _none
    b, stmts = scan(data, progress)
    if admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone():
        raise ValidationError("Er bestaat al een database met deze naam.")
    admin.execute(psql.SQL("CREATE DATABASE {} ENCODING 'UTF8' TEMPLATE template0").format(psql.Identifier(dbname)))
    res = RestoreResult(info=b)
    total = stmts[-1].end if stmts else 0
    try:
        with connect(dbname) as conn:
            with conn.transaction():
                progress("restore", 0, total)
                for n, st in enumerate(stmts, 1):
                    if n % 500 == 0:
                        progress("restore", st.end, total)
                    if check(st, b.family == "rma", b.kind == "app") == "skip":
                        res.skipped += 1
                        continue
                    try:
                        if st.copy_data is not None:
                            with conn.cursor() as cur, cur.copy(st.text) as cp:
                                cp.write(st.copy_data)
                            res.rows_copied += st.copy_data.count("\n")
                        else:
                            conn.execute(st.text)
                    except psycopg.Error as exc:
                        raise BackupError(f"Regel {st.line}: {str(exc).strip().splitlines()[0]}") from exc
                progress("restore", total, total)
            if db_version(conn) is None:
                raise BackupError("Na het terugzetten staat er geen versie in defaults: geen SQL-Ledger-database.")
            prep = None
            if b.family == "rma":
                from . import rmaimport
                prep = rmaimport.prepare(conn, b.version)                 # RI2/RI4: extra's naar rma, 2.8.10
            res.upgrade = upgrade(conn, progress=lambda done, n: progress("upgrade", done, n))
            if prep is not None:
                res.upgrade.from_version = b.version              # de keten liep vanaf 2.8.10; tonen: de RMA-versie
            progress("finish", 0, 1)
            from .extensions import ensure_schema
            ensure_schema(conn)
            if prep is not None:
                res.rma = rmaimport.finish(conn, prep, res.upgrade.errors)   # RI5/RI6 + rapport (na x_-tabellen)
    except BaseException:
        admin.execute(psql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(psql.Identifier(dbname)))
        raise
    return res
