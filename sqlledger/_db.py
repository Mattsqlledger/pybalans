"""Interne database-hulpfuncties (Form.pm get_defaults, get_employee, ...).

Alle query's zijn geparametriseerd; de Perl-code plakt waarden direct in de
SQL-tekst.
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional, Tuple

import psycopg
from psycopg.rows import dict_row

from .amounts import Number, to_decimal
from .dates import yearend_month


def connect(conninfo: str = "", **kwargs) -> psycopg.Connection:
    """Open een verbinding zoals de bibliotheek die verwacht (autocommit;
    elke schrijfactie gebruikt zelf een transactie)."""
    return psycopg.connect(conninfo, autocommit=True, **kwargs)


def cursor(conn: psycopg.Connection) -> psycopg.Cursor:
    return conn.cursor(row_factory=dict_row)


def get_defaults(cur: psycopg.Cursor, *patterns: str) -> Dict[str, Optional[str]]:
    """Lees velden uit ``defaults``; patronen mogen LIKE-wildcards bevatten."""
    result: Dict[str, Optional[str]] = {}
    for pattern in patterns or ("%",):
        cur.execute("SELECT fldname, fldvalue FROM defaults WHERE fldname LIKE %s", (pattern,))
        for row in cur.fetchall():
            result[row["fldname"]] = row["fldvalue"]
    return result


def get_yearend(cur: psycopg.Cursor) -> int:
    """Laatste maand van het boekjaar (``defaults.yearend``, JA1); leeg = 12 (december)."""
    return yearend_month(get_defaults(cur, "yearend").get("yearend"))


def get_precision(cur: psycopg.Cursor) -> int:
    value = get_defaults(cur, "precision").get("precision")
    return int(to_decimal(value)) if value not in (None, "") else 2


def default_currency(cur: psycopg.Cursor) -> Optional[str]:
    """Standaardvaluta = eerste valuta in ``curr`` (gl.pl: ``$curr[0]``)."""
    cur.execute("SELECT curr FROM curr ORDER BY rn LIMIT 1")
    row = cur.fetchone()
    return row["curr"].strip() if row and row["curr"] else None


def currencies(cur: psycopg.Cursor) -> Tuple[str, ...]:
    cur.execute("SELECT curr FROM curr ORDER BY rn")
    return tuple(r["curr"].strip() for r in cur.fetchall())


def get_employee(cur: psycopg.Cursor, short_login: str) -> Tuple[Optional[str], int]:
    """(naam, id) van de medewerker; id 0 als die niet bestaat."""
    cur.execute("SELECT name, id FROM employee WHERE login = %s", (short_login,))
    row = cur.fetchone()
    if not row:
        return None, 0
    return row["name"], int(row["id"] or 0)


def update_balance(cur: psycopg.Cursor, table: str, field: str, where_id: int,
                   value: Number) -> None:
    """Tel ``value`` op bij ``table.field`` voor record ``id`` (Form::update_balance).

    Alleen voor vaste, interne tabel/veldnamen; nooit gebruikersinvoer.
    """
    amount = to_decimal(value)
    if not amount:
        return
    cur.execute(f"SELECT {field} FROM {table} WHERE id = %s FOR UPDATE", (where_id,))
    row = cur.fetchone()
    balance = to_decimal(row[field] if row else None) + amount
    cur.execute(f"UPDATE {table} SET {field} = %s WHERE id = %s", (balance, where_id))


def rows(cur: psycopg.Cursor, query: str, params: Iterable = ()) -> list:
    cur.execute(query, tuple(params))
    return cur.fetchall()
