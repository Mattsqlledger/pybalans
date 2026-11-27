"""Menu-items die van de inhoud van de administratie afhangen: productie (PB20: samengestelde artikelen of de
schakelaar, ``sqlledger.production``), het land (RGS alleen NL, btw-rubrieken alleen landen met een aangifteformulier,
betaalbestand alleen SEPA-landen) en GIFI (alleen als de administratie GIFI-codes heeft).

Het menu staat op elke pagina; om niet bij elke pagina de administratie te openen wordt de uitkomst per administratie
kort onthouden (``TTL`` seconden) en vergeten zodra een artikel of de instellingen worden opgeslagen. Een andere
werker van dezelfde server ziet de wijziging uiterlijk na ``TTL`` seconden. Directe adressen blijven altijd werken.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional, Tuple

import psycopg

from sqlledger import production
from sqlledger._db import cursor, default_currency
from sqlledger.vatforms import CURRENCY_COUNTRY, FORMS
from sqlledger.vatreturn import COUNTRY_SETTING

from .companies import connect_company

#: betaalbestand (pain.001, SEPA): EU/EER; Zwitserland heeft een eigen variant (nog niet), het VK eigen formaten
SEPA_COUNTRIES = frozenset("AT BE BG CY CZ DE DK EE ES FI FR GR HR HU IE IS IT LI LT LU LV MT NL NO PL PT RO SE SI SK"
                           .split())

TTL = 60.0

_cache: Dict[Tuple, Tuple[Dict[str, Any], float]] = {}
_lock = threading.Lock()


def _key(company: dict) -> Tuple:
    return (company["id"], company.get("dbname"), company.get("dbhost"), company.get("dbport"))


def _country(cur) -> Optional[str]:
    """Land zoals de btw-aangifte het kiest (``VatReturns.country``/``suggest_country``), zonder het schema bij te
    werken: instelling vat_country, anders country, anders uit de valuta (niet bij Run my Accounts met EUR)."""
    cur.execute("SELECT to_regclass('x_setting') IS NOT NULL AS x, to_regclass('rma.import_report') IS NOT NULL AS r")
    r = cur.fetchone()
    if r["x"]:
        cur.execute("SELECT key, value FROM x_setting WHERE key IN (%s, 'country')", (COUNTRY_SETTING,))
        got = {k["key"]: (k["value"] or "").strip().upper() for k in cur.fetchall()}
        if got.get(COUNTRY_SETTING) or got.get("country"):
            return got.get(COUNTRY_SETTING) or got.get("country")
    curr = (default_currency(cur) or "").upper()
    if r["r"] and curr != "CHF":
        return None
    return CURRENCY_COUNTRY.get(curr) or None


def _read(conn) -> Dict[str, Any]:
    with cursor(conn) as cur:
        country = _country(cur)
        cur.execute("""SELECT EXISTS (SELECT 1 FROM gifi) OR EXISTS (SELECT 1 FROM chart
                                                                    WHERE coalesce(gifi_accno, '') <> '') AS g""")
        gifi = cur.fetchone()["g"]
    return dict(production=production.visible(conn), country=country, gifi=bool(gifi))


def flags(settings, company: dict) -> Dict[str, Any]:
    """Productie, land (None = onbekend: dan alles tonen) en GIFI, kort onthouden; bij een fout (administratie niet
    bereikbaar) alles tonen."""
    key = _key(company)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
    if hit and now - hit[1] < TTL:
        return hit[0]
    try:
        with connect_company(settings, company) as conn:
            value = _read(conn)
    except psycopg.Error:
        return dict(production=True, country=None, gifi=True)
    with _lock:
        _cache[key] = (value, now)
    return value


def visible(settings, company: dict) -> bool:
    """Productie tonen in het menu."""
    return bool(flags(settings, company)["production"])


def menu_flags(settings, company: Optional[dict]) -> set:
    """Menuvlaggen uit de inhoud van de administratie (zie ``menu.Item.flag``)."""
    if not company:
        return set()
    f = flags(settings, company)
    c = f["country"]
    out = set()
    if c in (None, "NL"):
        out.add("nl")
    if c is None or c in FORMS:
        out.add("vatform")
    if c is None or c in SEPA_COUNTRIES:
        out.add("sepa")
    if f["gifi"]:
        out.add("gifi")
    return out


def forget(company: dict) -> None:
    with _lock:
        _cache.pop(_key(company), None)


def clear() -> None:
    with _lock:
        _cache.clear()
