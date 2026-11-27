"""Verbinding met een SQL-Ledger-administratie (S2)."""

from __future__ import annotations

import re
from typing import Optional

import psycopg

import sqlledger
from sqlledger._db import cursor, get_defaults, get_employee

from .settings import Settings

DBNAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,62}$")


#: laagste SQL-Ledger-databaseversie die de app aankan (ES12 A); oudere eerst bijwerken (README "Oude versie")
MIN_VERSION = (3, 2, 4)


def version_tuple(version: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", version or "")[:3])


def too_old(version: str) -> bool:
    """True bij een bekende versie lager dan MIN_VERSION (onbekend = niet te oud: dat meldt de verbinding zelf)."""
    v = version_tuple(version)
    return bool(v) and v < MIN_VERSION


#: hoogste databaseversie van SQL-Ledger 3.2.12; een nieuwere (4.x) mag gekoppeld worden, maar wordt niet ondersteund
MAX_VERSION = (3, 2, 4)


def too_new(version: str) -> bool:
    """True bij een bekende versie hoger dan MAX_VERSION."""
    v = version_tuple(version)
    return bool(v) and v > MAX_VERSION


def connect_company(settings: Settings, company: dict) -> psycopg.Connection:
    info = settings.company_conninfo(company["dbname"], company.get("dbhost"), company.get("dbport"))
    return sqlledger.connect(connect_timeout=5, **info)


def probe(settings: Settings, company: dict, employee_login: Optional[str] = None) -> dict:
    """Controleer de verbinding; geeft bedrijfsnaam, versie en (optioneel) medewerker.

    Gooit ``psycopg.Error`` als de database niet bereikbaar of geen SQL-Ledger is.
    """
    with connect_company(settings, company) as conn:
        cur = cursor(conn)
        d = get_defaults(cur, "company", "version", "closedto", "precision")
        info = {"company": d.get("company") or "", "version": d.get("version") or "",
                "closedto": d.get("closedto") or ""}
        if employee_login is not None:
            name, emp_id = get_employee(cur, employee_login.split("@", 1)[0])
            info["employee"] = name if emp_id else None
    return info
