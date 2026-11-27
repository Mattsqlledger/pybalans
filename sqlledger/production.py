"""Productie (samengestelde artikelen) in het menu (v1.0.1, PB20 keuze A).

Assembleren, Stuklijsten en Productiehistorie zijn alleen zinvol als de administratie samengestelde artikelen
heeft (``parts.assembly``). De beheerder kan ze ook zonder aanzetten (Beheer > Instellingen), bijvoorbeeld om het
eerste samengestelde artikel te maken; die keuze staat in ``x_setting`` (``show_production`` = '1'), zodat
SQL-Ledger niets merkt.
"""

from __future__ import annotations

from typing import Optional

import psycopg

from . import _db
from .extensions import ensure_schema

SETTING = "show_production"


def has_assemblies(conn: psycopg.Connection) -> bool:
    """Is er een samengesteld artikel (ook een verouderd)?"""
    with _db.cursor(conn) as cur:
        cur.execute("SELECT EXISTS (SELECT 1 FROM parts WHERE assembly) AS x")
        return bool(cur.fetchone()["x"])


def setting(conn: psycopg.Connection) -> bool:
    """De schakelaar van de beheerder (leest alleen: geen ``ensure_schema``, het menu vraagt dit bij elke pagina)."""
    with _db.cursor(conn) as cur:
        cur.execute("SELECT to_regclass('x_setting') AS t")
        if not cur.fetchone()["t"]:
            return False
        cur.execute("SELECT value FROM x_setting WHERE key = %s", (SETTING,))
        r = cur.fetchone()
        return bool(r) and r["value"] == "1"


def visible(conn: psycopg.Connection) -> bool:
    """Menu-items voor productie tonen: schakelaar aan of samengestelde artikelen aanwezig."""
    return setting(conn) or has_assemblies(conn)


def save_setting(conn: psycopg.Connection, on: bool, login: Optional[str] = None) -> bool:
    """Schakelaar opslaan; geeft True als er iets veranderde."""
    if setting(conn) == on:
        return False
    ensure_schema(conn)
    with conn.transaction(), _db.cursor(conn) as cur:
        cur.execute("""INSERT INTO x_setting (key, value, login) VALUES (%s, %s, %s)
                       ON CONFLICT (key) DO UPDATE SET value = excluded.value, login = excluded.login,
                                                       updated = now()""",
                    (SETTING, "1" if on else "0", login))
    return True
