"""Nieuwe administratie aanmaken (REKENINGSCHEMA_ANALYSE.md, RS1).

Zoals SQL-Ledger ``User::dbcreate``: een nieuwe PostgreSQL-database met het SQL-Ledger-schema van 3.2.12
(``sqlledger/slschema``: ``Pg-tables.sql``, ``Mimetype.sql``, ``Pg-indices.sql``, ongewijzigd uit de broncode;
databaseversie 3.2.4), zodat SQL-Ledger hem kan openen. Daarna een standaardschema uit
``sqlledger/charts`` (``chartfiles``), de instellingen in ``defaults``, de standaardvaluta in ``curr``, onze
``x_``-tabellen, de btw-rubrieken, de btw-rekeningen voor de aangifte en de RGS-codes (``x_account_rgs``).

Een bestaande database wordt nooit overschreven; mislukt er iets na het aanmaken, dan gaat de nieuwe database weer weg.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import Any, Dict, Optional

import psycopg
from psycopg import sql

from . import _db
from .chartfiles import ChartFile, DEFAULT_KEYS, check
from .dashboard import START_SETTING
from .errors import ValidationError
from .extensions import ensure_schema
from .vatreturn import ROUNDING_SETTING, SETTLEMENT_SETTING

SCHEMA_FILES = ("Pg-tables.sql", "Mimetype.sql", "Pg-indices.sql")
#: x_setting-sleutels met de herkomst van de administratie
COUNTRY_SETTING = "country"
CHART_SETTING = "chart"


@dataclass
class NewCompany:
    dbname: str
    company: str                      # naam in defaults.company (op documenten en in rapporten)
    chart: ChartFile
    language: str = ""                # taal van de omschrijvingen; leeg = eerste taal van het schema
    currency: str = ""                # standaardvaluta; leeg = valuta van het schema
    yearend: int = 12                 # laatste maand van het boekjaar
    employee_login: str = ""          # medewerker voor de aanmaker (leeg = geen)
    employee_name: str = ""
    login: Optional[str] = None       # voor de x_-tabellen (wie)


def _validate(new: NewCompany) -> None:
    check(new.chart)
    new.language = new.language or new.chart.languages[0]
    if new.language not in new.chart.languages:
        raise ValidationError("Dit rekeningschema is er niet in deze taal.")
    new.currency = (new.currency or new.chart.currency).strip().upper()
    if len(new.currency) != 3 or not new.currency.isalpha():
        raise ValidationError("Valuta: drie letters (bv. EUR).")
    if not 1 <= int(new.yearend) <= 12:
        raise ValidationError("Kies de laatste maand van het boekjaar.")
    if not new.company.strip():
        raise ValidationError("Naam is verplicht.")


def database_exists(admin: psycopg.Connection, dbname: str) -> bool:
    return admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone() is not None


def create(admin: psycopg.Connection, connect, new: NewCompany) -> Dict[str, Any]:
    """Maak de database ``new.dbname`` en vul hem.

    ``admin``: verbinding (autocommit) met een andere database op dezelfde server, met recht CREATEDB.
    ``connect``: functie ``connect(dbname) -> psycopg.Connection`` naar de nieuwe database.
    Geeft een samenvatting terug (aantal rekeningen, btw-rekeningen, valuta, taal).
    """
    _validate(new)
    if database_exists(admin, new.dbname):
        raise ValidationError("Er bestaat al een database met deze naam.")
    admin.execute(sql.SQL("CREATE DATABASE {} ENCODING 'UTF8' TEMPLATE template0")
                  .format(sql.Identifier(new.dbname)))
    try:
        with connect(new.dbname) as conn:
            info = fill(conn, new)
    except BaseException:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(new.dbname)))
        raise
    return info


def fill(conn: psycopg.Connection, new: NewCompany) -> Dict[str, Any]:
    """Een lege database vullen: SQL-Ledger-schema, rekeningschema, instellingen, x_-tabellen."""
    _validate(new)
    chart = new.chart
    with conn.transaction():
        cur = conn.cursor()
        for name in SCHEMA_FILES:
            cur.execute(resources.files("sqlledger.slschema").joinpath(name).read_text(encoding="utf-8"))
        ids: Dict[str, int] = {}
        for r in sorted(chart.rows, key=lambda r: r.accno):
            link = ":".join(r.link)
            cur.execute("""INSERT INTO chart (accno, description, charttype, gifi_accno, category, link, contra)
                           VALUES (%s, %s, %s, '', %s, %s, %s) RETURNING id""",
                        (r.accno, r.descriptions[new.language], r.charttype, r.category, link, r.contra))
            ids[r.accno] = cur.fetchone()[0]
            if r.rate is not None:
                cur.execute("INSERT INTO tax (chart_id, rate) VALUES (%s, %s)", (ids[r.accno], r.rate))
        values = {"company": new.company.strip(), "roundchange": "0.01", "precision": "2", "weightunit": "kg",
                  "yearend": str(int(new.yearend)), "audittrail": "1"}
        for key, target in DEFAULT_KEYS.items():
            if target.endswith("_accno_id"):
                values[target] = str(ids[chart.defaults[key]])
        for key, value in values.items():
            cur.execute("INSERT INTO defaults (fldname, fldvalue) VALUES (%s, %s)", (key, value))
        cur.execute("INSERT INTO curr (rn, curr, prec) VALUES (1, %s, 2)", (new.currency,))
        if new.employee_login:
            cur.execute("INSERT INTO employee (login, name) VALUES (%s, %s)",
                        (new.employee_login, (new.employee_name or new.employee_login)[:64]))
    ensure_schema(conn)
    with conn.transaction():
        cur = conn.cursor()
        settings = {COUNTRY_SETTING: chart.country, CHART_SETTING: chart.id,
                    SETTLEMENT_SETTING: chart.defaults["btw-afdracht"],
                    ROUNDING_SETTING: chart.defaults["btw-afronding"], START_SETTING: "1"}
        for key, value in settings.items():
            cur.execute("INSERT INTO x_setting (key, value, login) VALUES (%s, %s, %s)", (key, value, new.login))
        for r in chart.accounts:
            if r.box and r.rate is None:        # BL3: rubriek van de inkoop bij een kosten-/activarekening (BE 81–83)
                cur.execute("INSERT INTO x_vat_account_box (chart_id, box, country, login) VALUES (%s, %s, %s, %s)",
                            (ids[r.accno], r.box, chart.country, new.login))
            elif r.box:
                cur.execute("INSERT INTO x_vat_box (chart_id, box, country, login) VALUES (%s, %s, %s, %s)",
                            (ids[r.accno], r.box, chart.country, new.login))
        for r in chart.rows:
            if r.rgs:
                cur.execute("INSERT INTO x_account_rgs (chart_id, version, code, login) VALUES (%s, %s, %s, %s)",
                            (ids[r.accno], chart.rgs_version, r.rgs, new.login))
    return {"accounts": len(chart.accounts), "tax_accounts": sum(1 for r in chart.accounts if r.rate is not None),
            "currency": new.currency, "language": new.language, "chart": chart.id, "country": chart.country}


#: documenttaal van de administratie ook in de administratie zelf (F25 A), zodat een back-up hem meeneemt;
#: de app leest ``company.doc_language``
DOC_LANGUAGE_SETTING = "doc_language"


def setting(conn: psycopg.Connection, key: str) -> str:
    ensure_schema(conn)
    with _db.cursor(conn) as cur:
        cur.execute("SELECT value FROM x_setting WHERE key = %s", (key,))
        r = cur.fetchone()
    return r["value"] if r else ""


def save_setting(conn: psycopg.Connection, key: str, value: str, login: Optional[str] = None) -> None:
    ensure_schema(conn)
    with conn.transaction(), _db.cursor(conn) as cur:
        cur.execute("""INSERT INTO x_setting (key, value, login) VALUES (%s, %s, %s)
                       ON CONFLICT (key) DO UPDATE SET value = excluded.value, login = excluded.login,
                                                       updated = now()""", (key, value, login))
