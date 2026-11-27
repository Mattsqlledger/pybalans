"""Ondersteunende administratie rond een boeking (Form.pm):
audittrail, record-locks, wisselkoersen, referentiedocumenten en
terugkerende boekingen.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Dict, List, Optional, Sequence

import psycopg

from . import _db, attachments
from .amounts import Number, to_decimal
from .config import UserConfig
from .models import Recurring, Reference

RECURRING_UNITS = ("days", "weeks", "months", "years")


# --------------------------------------------------------------- audittrail

def audittrail(cur: psycopg.Cursor, config: UserConfig, *, trans_id: int, tablename: str,
               reference: Optional[str], formname: str, action: str) -> None:
    """Schrijf een audittrail-regel als ``defaults.audittrail`` aan staat."""
    if not trans_id:
        return
    if not _db.get_defaults(cur, "audittrail").get("audittrail"):
        return
    _, employee_id = _db.get_employee(cur, config.short_login)
    # Perl: id 1 wordt NULL (eigenaardigheid behouden)
    tid = None if trans_id == 1 else trans_id
    cur.execute(
        """INSERT INTO audittrail (trans_id, tablename, reference, formname, action, employee_id)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (tid, tablename, reference, formname, action, employee_id),
    )


# --------------------------------------------------------------------- locks

def remove_locks(cur: psycopg.Cursor, config: UserConfig, module: Optional[str] = None) -> None:
    if module:
        cur.execute("DELETE FROM semaphore WHERE login = %s AND module = %s", (config.login, module))
    else:
        cur.execute("DELETE FROM semaphore WHERE login = %s", (config.login,))


def create_lock(cur: psycopg.Cursor, config: UserConfig, trans_id: int, module: str,
                add: bool = False) -> Optional[str]:
    """Zet een lock op een record. Geeft de naam van de huidige lockhouder
    terug als het record al door iemand anders gelockt is (dan read-only)."""
    now = int(time.time())
    cur.execute("DELETE FROM semaphore WHERE expires < %s", (str(now),))
    if not trans_id:
        return None
    cur.execute("SELECT id, login FROM semaphore WHERE id = %s", (trans_id,))
    row = cur.fetchone()
    if row and row["id"] and not add:
        login = (row["login"] or "").split("@", 1)[0]
        cur.execute("SELECT name FROM employee WHERE login = %s", (login,))
        emp = cur.fetchone()
        return (emp["name"] if emp else None) or "admin"
    cur.execute(
        "INSERT INTO semaphore (id, login, module, expires) VALUES (%s, %s, %s, %s)",
        (trans_id, config.login, module, str(now + int(config.timeout))),
    )
    return None


def lock_holder(cur: psycopg.Cursor, config: UserConfig, trans_id: int) -> Optional[str]:
    """Naam van iemand anders die dit record (nog geldig) gelockt heeft, anders None."""
    cur.execute("SELECT login FROM semaphore WHERE id = %s AND login <> %s AND expires >= %s",
                (trans_id, config.login, str(int(time.time()))))
    row = cur.fetchone()
    if not row:
        return None
    login = (row["login"] or "").split("@", 1)[0]
    cur.execute("SELECT name FROM employee WHERE login = %s", (login,))
    emp = cur.fetchone()
    return (emp["name"] if emp else None) or login or "admin"


# -------------------------------------------------------------- wisselkoers

def update_exchangerate(cur: psycopg.Cursor, curr: Optional[str], transdate: date,
                        exchangerate: Number, defaultcurrency: Optional[str]) -> None:
    if not curr or curr == defaultcurrency:
        return
    rate = to_decimal(exchangerate)
    cur.execute(
        "SELECT curr FROM exchangerate WHERE curr = %s AND transdate = %s FOR UPDATE",
        (curr, transdate),
    )
    if cur.fetchone():
        cur.execute(
            "UPDATE exchangerate SET exchangerate = %s WHERE curr = %s AND transdate = %s",
            (rate, curr, transdate),
        )
    else:
        cur.execute(
            "INSERT INTO exchangerate (curr, exchangerate, transdate) VALUES (%s, %s, %s)",
            (curr, rate, transdate),
        )


# ---------------------------------------------------------------- referenties

def _login_filter() -> str:
    return "(login = %s OR login = '' OR login IS NULL)"


def save_references(cur: psycopg.Cursor, config: UserConfig, trans_id: int, formname: str,
                    references: Sequence[Reference]) -> None:
    """Sla referentiedocumenten op (Form::save_reference); een verwijzing met ``data`` krijgt een
    nieuw archiefstuk (bijlage, stap 4e).

    Bestaande referenties van deze gebruiker worden vervangen; archiefstukken
    waar geen referentie meer naar wijst, worden verwijderd.
    """
    login = config.short_login
    # een bestaand archiefstuk alleen koppelen als deze gebruiker het mag zien: een zichtbare verwijzing, of een
    # los stuk zonder verwijzing (rd.pl "Document toevoegen", voor iedereen zichtbaar). Het nummer komt uit het
    # formulier; anders kon een verborgen veld een vertrouwelijk stuk van een ander binnenhalen.
    wanted = sorted({ref.archive_id for ref in references if ref.archive_id})
    visible = set()
    if wanted:
        cur.execute(f"""SELECT a.id FROM archive a WHERE a.id = ANY(%s)
                        AND (EXISTS (SELECT 1 FROM reference r WHERE r.archive_id = a.id AND {_login_filter()})
                             OR NOT EXISTS (SELECT 1 FROM reference r WHERE r.archive_id = a.id))""",
                    (wanted, login))
        visible = {r["id"] for r in cur.fetchall()}
    cur.execute(
        f"SELECT archive_id FROM reference WHERE trans_id = %s AND {_login_filter()} AND formname = %s",
        (trans_id, login, formname),
    )
    orphaned = {r["archive_id"] for r in cur.fetchall() if r["archive_id"]}
    cur.execute(f"DELETE FROM reference WHERE trans_id = %s AND {_login_filter()}", (trans_id, login))

    for ref in references:
        if ref.description and ref.archive_id:
            orphaned.discard(ref.archive_id)
    for archive_id in sorted(orphaned):
        _delete_unused_archive(cur, archive_id)

    for ref in references:
        archive_id = ref.archive_id if ref.archive_id in visible else None
        description = ref.description
        if ref.data is not None:            # nieuw bestand (Perl: referencetmpfile_i)
            archive_id = attachments.store(cur, ref.filename or "", ref.data, login)
            if not description:
                cur.execute("SELECT filename FROM archive WHERE id = %s", (archive_id,))
                description = cur.fetchone()["filename"]
        if not description:
            continue
        cur.execute(
            """INSERT INTO reference (code, trans_id, description, archive_id, login, formname, folder)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (ref.code, trans_id, description, archive_id,
             login if ref.confidential else "", formname, ref.folder),
        )


def all_references(cur: psycopg.Cursor, config: UserConfig, trans_id: int,
                   formname: Optional[str] = None) -> List[Dict]:
    login = config.short_login
    query = """SELECT r.*, a.filename FROM reference r
               LEFT JOIN archive a ON (a.id = r.archive_id)
               WHERE r.trans_id = %s"""
    params: list = [trans_id]
    if formname:
        query += " AND r.formname = %s"
        params.append(formname)
    cur.execute(query, params)
    result = []
    for ref in cur.fetchall():
        if ref["login"] and ref["login"] != login:
            continue
        ref["confidential"] = login == ref["login"]
        result.append(ref)
    return result


def delete_references(cur: psycopg.Cursor, config: UserConfig, trans_id: int) -> None:
    """Alle verwijzingen van een record dat verdwijnt, ook vertrouwelijke van andere gebruikers (RB7;
    Form::delete_references liet die als wees achter); een archiefstuk gaat pas weg bij de laatste
    verwijzing."""
    cur.execute("SELECT archive_id FROM reference WHERE trans_id = %s", (trans_id,))
    archives = sorted({r["archive_id"] for r in cur.fetchall() if r["archive_id"]})
    cur.execute("DELETE FROM reference WHERE trans_id = %s", (trans_id,))
    for archive_id in archives:
        _delete_unused_archive(cur, archive_id)


def _delete_unused_archive(cur: psycopg.Cursor, archive_id: int) -> None:
    """Archiefstuk weg als geen enkele verwijzing er meer naar wijst. Perl (Form::save_reference,
    Form::delete_references) wiste het altijd, ook als rd.pl "Koppelen aan" het met een andere
    boeking deelt (Perl-fout 28); RD::delete_document telde wel."""
    cur.execute("DELETE FROM archive WHERE id = %s AND NOT EXISTS (SELECT 1 FROM reference WHERE archive_id = %s)",
                (archive_id, archive_id))


# --------------------------------------------------------- terugkerend

def _add_interval(cur: psycopg.Cursor, start: date, amount: int, unit: str) -> date:
    cur.execute("SELECT (%s::date + %s::interval)::date AS d", (start, f"{amount} {unit}"))
    return cur.fetchone()["d"]


def save_recurring(cur: psycopg.Cursor, trans_id: int, recurring: Optional[Recurring],
                   today: Optional[date] = None) -> None:
    """Vervang het herhaalschema van een boeking (Form::save_recurring)."""
    for table in ("recurring", "recurringemail", "recurringprint"):
        cur.execute(f"DELETE FROM {table} WHERE id = %s", (trans_id,))
    if not recurring:
        return

    s = recurring
    unit = s.unit.lower()
    if unit not in RECURRING_UNITS:
        raise ValueError(f"onbekende eenheid voor herhaling: {s.unit!r}")
    repeat, howmany = int(s.repeat), int(s.howmany)

    enddate = _add_interval(cur, s.startdate, repeat * (howmany - 1), unit)

    today = today or date.today()
    x = (today - s.startdate).days
    y = (enddate - today).days
    advance = int((x / (x + y)) * howmany + 1) * repeat if (x + y) else 0

    nextdate: Optional[date] = enddate
    if advance > 0:
        if advance < repeat * howmany:
            nextdate = _add_interval(cur, s.startdate, advance, unit)
    else:
        nextdate = s.startdate

    if s.nextdate:
        nextdate = s.nextdate
        if (enddate - nextdate).days < 0:
            nextdate = None

    cur.execute(
        """INSERT INTO recurring (id, reference, description, startdate, enddate, nextdate,
                                  repeat, unit, howmany, payment)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (trans_id, s.reference, s.description, s.startdate, enddate, nextdate,
         repeat, unit, howmany, bool(s.payment)),
    )
    for formname, fmt in s.email:
        cur.execute(
            "INSERT INTO recurringemail (id, formname, format, message) VALUES (%s, %s, %s, %s)",
            (trans_id, formname, fmt, s.message),
        )
    for formname, fmt, printer in s.print:
        cur.execute(
            "INSERT INTO recurringprint (id, formname, format, printer) VALUES (%s, %s, %s, %s)",
            (trans_id, formname, fmt, printer or ""),
        )


def get_recurring(cur: psycopg.Cursor, trans_id: int) -> Optional[Recurring]:
    cur.execute("SELECT * FROM recurring WHERE id = %s", (trans_id,))
    row = cur.fetchone()
    if not row:
        return None
    cur.execute("SELECT formname, format, message FROM recurringemail WHERE id = %s", (trans_id,))
    emails = cur.fetchall()
    cur.execute("SELECT formname, format, printer FROM recurringprint WHERE id = %s", (trans_id,))
    prints = cur.fetchall()
    return Recurring(
        reference=row["reference"], description=row["description"],
        startdate=row["startdate"], repeat=row["repeat"], unit=(row["unit"] or "").strip(),
        howmany=row["howmany"], payment=bool(row["payment"]),
        nextdate=row["nextdate"], enddate=row["enddate"],
        email=[(e["formname"], e["format"]) for e in emails],
        print=[(p["formname"], p["format"], p["printer"]) for p in prints],
        message=emails[0]["message"] if emails else None,
    )
