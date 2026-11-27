"""Een SQL-Ledger-database van een oudere versie bijwerken naar 3.2.4 (FASE6_ANALYSE.md F3/F4, port van
``User::dbupdate`` + ``User::process_query``).

De 70 upgradescripts van SQL-Ledger 3.2.12 staan ongewijzigd in ``sqlledger/slschema/upgrade/``. Zelfde volgorde
(``script_version``), zelfde versieberekening (``calc_version``) en zelfde manier van splitsen in statements (regels
met ``--`` en lege regels overslaan, een statement eindigt op ``;`` aan het eind van een regel, functie/trigger tot
``-- end function``; ``\\'`` wordt ``''``). Anders dan SL: alles in **één transactie**; een statement dat faalt wordt
(zoals SL) overgeslagen, maar via een savepoint en met bestand, regel en melding in het resultaat, zodat de beheerder
ziet wat er niet ging. Een database met een versie nieuwer dan 3.2.4 wordt geweigerd (F4), net als een database van
Run my Accounts (RMA_IMPORT_ANALYSE.md RM6): die nummert na 2.8.10 eigen versies (2.8.11 … 2.8.52) en de originele
keten zou er stil kolommen uit weghalen. Een RMA-back-up gaat via ``rmaimport.prepare`` (extra's naar schema rma,
versie 2.8.10) en komt hier daarna als gewone 2.8.10 binnen; de weigering blijft als vangnet.

Eén bewuste correctie (``PATCHES``, OPEN_PUNTEN_ANALYSE.md AD1, ADM-15): het .sql-bestand blijft ongewijzigd, het
statement wordt bij het uitvoeren vervangen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib import resources
from typing import Callable, List, Optional, Tuple

import psycopg

from .errors import LedgerError

TARGET = "3.2.4"


class UpgradeError(LedgerError):
    pass


# (script, exact oorspronkelijk statement) → vervanging. Pg-upgrade-2.9.1-2.9.2 nam alleen de inkoopkoers (buy)
# over en gooide koersen met alleen een verkoopkoers (sell) weg (ADM-15); nu: buy, anders sell.
PATCHES = {
    ("Pg-upgrade-2.9.1-2.9.2.sql", "insert into temp select curr, transdate, buy from exchangerate"):
        "insert into temp select curr, transdate, CASE WHEN coalesce(buy,0) <> 0 THEN buy ELSE sell END "
        "from exchangerate",
}


#: tabellen die alleen Run my Accounts (ledger123-uitbreidingen + eigen reeks 2.8.11 …) heeft
RMA_TABLES = ("invoicetax", "acc_trans_log", "vat_settlement", "imported_transaction")


def calc_version(v: str) -> int:
    """User::calc_version: 2.8.37 → 2008037."""
    out = 0
    for part in str(v or "").split("."):
        out = out * 1000 + int(re.sub(r"\D", "", part) or 0)
    return out


def _bounds(name: str) -> Tuple[int, int]:
    m = re.fullmatch(r"Pg-upgrade-(.+)-(.+)\.sql", name)
    return calc_version(m.group(1)), calc_version(m.group(2))


def scripts() -> List[str]:
    """Upgradescripts in de volgorde van ``script_version`` (eerst van-versie, dan naar-versie)."""
    names = [p.name for p in resources.files("sqlledger.slschema").joinpath("upgrade").iterdir()
             if re.fullmatch(r"Pg-upgrade-.+\.sql", p.name)]
    return sorted(names, key=_bounds)


def statements(text: str) -> List[Tuple[int, str]]:
    """``process_query``: (regelnummer, statement)."""
    out: List[Tuple[int, str]] = []
    query, loop, start = "", False, 0
    for n, line in enumerate(text.splitlines(keepends=True), 1):
        if loop and re.match(r"^--\s*end\s*(procedure|function|trigger)", line, re.I):
            out.append((start, query))
            query, loop = "", False
            continue
        if loop or re.match(r"^create *(or replace)? *(procedure|function|trigger)", line, re.I):
            if not loop:
                start = n
            loop = True
            if re.fullmatch(r"(--.*|\s+)\n?", line):
                continue
            query += line
            continue
        if re.fullmatch(r"(--.*|\s+)\n?", line) or not line.strip():
            continue
        if not query:
            start = n
        query += line
        if re.search(r";\s*$", line):
            query = re.sub(r";\s*$", "", query).replace("\\'", "''")
            out.append((start, query))
            query = ""
    return out


@dataclass
class UpgradeResult:
    from_version: str
    to_version: str
    applied: List[str] = field(default_factory=list)
    errors: List[Tuple[str, int, str]] = field(default_factory=list)      # (script, regel, melding)


def db_version(conn: psycopg.Connection) -> Optional[str]:
    """Versie uit ``defaults`` (nieuwe vorm fldname/fldvalue of de oude kolom ``version``)."""
    with conn.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'defaults' "
                    "AND table_schema = current_schema() ORDER BY ordinal_position")
        cols = [r[0] for r in cur.fetchall()]
        if not cols:
            return None
        if cols[0] == "fldname":
            cur.execute("SELECT fldvalue FROM defaults WHERE fldname = 'version'")
        else:
            cur.execute("SELECT version FROM defaults")
        r = cur.fetchone()
        return str(r[0]).strip() if r and r[0] else None


def is_rma(conn: psycopg.Connection, version: str) -> bool:
    """Database van Run my Accounts: een versie tussen 2.8.10 en 2.9.0 (bestaat in SQL-Ledger zelf niet) of een van de
    RMA-tabellen."""
    v = calc_version(version)
    if calc_version("2.8.10") < v < calc_version("2.9.0"):
        return True
    return conn.execute("SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() "
                        "AND table_name = ANY(%s)", (list(RMA_TABLES),)).fetchone() is not None


def plan(version: int, goal: int) -> List[str]:
    """Scripts die ``User::dbupdate`` van ``version`` naar ``goal`` uitvoert, in volgorde (elk script brengt de
    database op zijn naar-versie)."""
    out: List[str] = []
    for name in scripts():
        mindb, maxdb = _bounds(name)
        if version >= maxdb:
            continue
        if version < mindb or version >= goal:
            break
        out.append(name)
        version = maxdb
    return out


def upgrade(conn: psycopg.Connection, target: str = TARGET,
            progress: Optional[Callable[[int, int], None]] = None) -> UpgradeResult:
    """Werk de database bij tot ``target`` (``User::dbupdate``). Geen SQL-Ledger-database of nieuwer dan het doel:
    ``UpgradeError``. ``progress(gedaan, totaal)``: na elk script (v1.0.1 PB4, "stap x van n")."""
    current = db_version(conn)
    if current is None:
        raise UpgradeError("Dit is geen SQL-Ledger-database (geen versie in defaults).")
    if is_rma(conn, current):
        raise UpgradeError(f"Dit is een database van Run my Accounts (versie {current}); die kan deze app nog niet "
                           "inlezen.")
    version, goal = calc_version(current), calc_version(target)
    if version > goal:
        raise UpgradeError(f"Deze database heeft versie {current}, nieuwer dan {target}; die kan "
                           "deze app niet openen.")
    res = UpgradeResult(from_version=current, to_version=current)
    base = resources.files("sqlledger.slschema").joinpath("upgrade")
    todo = plan(version, goal)
    if progress:
        progress(0, len(todo))
    with conn.transaction():
        for n, name in enumerate(todo, 1):
            for line, sql in statements(base.joinpath(name).read_text(encoding="utf-8", errors="replace")):
                sql = PATCHES.get((name, sql.strip()), sql)
                try:
                    with conn.transaction():
                        conn.execute(sql)
                except psycopg.Error as exc:
                    res.errors.append((name, line, str(exc).strip().splitlines()[0]))
            res.applied.append(name)
            if progress:
                progress(n, len(todo))
    res.to_version = db_version(conn) or res.to_version
    return res
