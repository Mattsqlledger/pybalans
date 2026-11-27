"""Balkje "Overstap: … – Terug naar de overstaplijst" (v1.0.1 PB10).

Een punt op de overstaplijst (dashboard of Beheer > Ingebruikname) linkt met ``?overstap=<punt>&terug=<lijst>``.
De middleware (``main.basics``) onthoudt dat in een cookie (``sl_overstap``, 2 uur), zodat het balkje ook na Opslaan
(POST en doorverwijzing) blijft staan; ``base.html`` toont het op de pagina's die bij dat punt horen
(``PAGES``). Terug naar de lijst (een GET op de lijst zelf) wist het cookie. Alleen punten uit de lijst en alleen de
twee lijstpagina's als terugadres: geen vrije tekst of open redirect.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

from .i18n import N_, gettext as _

COOKIE = "sl_overstap"
MAX_AGE = 2 * 3600
#: toegestane terugadressen: de lijst op het dashboard en op Beheer > Ingebruikname
BACKS = ("/", "/beheer/ingebruikname")
#: per punt: paden (begin) waar het balkje verschijnt, en links in het balkje als een punt twee pagina's raakt
PAGES: Dict[str, Tuple[Tuple[str, ...], Tuple[Tuple[str, str], ...]]] = {
    "check": (("/beheer/ingebruikname/controleblad",), ()),
    "company": (("/beheer/instellingen",), ()),
    "vat": (("/btw/rubrieken", "/beheer/instellingen"),
            ((N_("Btw-rubrieken"), "/btw/rubrieken"), (N_("Aangiftetijdvak"), "/beheer/instellingen#btw-tijdvak"))),
    "bank": (("/beheer/rekeningschema", "/bank/"),
             ((N_("IBAN bij de bankrekening"), "/beheer/rekeningschema"),
              (N_("Bankafschrift inlezen"), "/bank/inlezen"))),
    "style": (("/beheer/huisstijl",), ()),
    "numbering": (("/beheer/instellingen",), ()),
    "recurring": (("/terugkerend", "/herhalen/"), ()),
    "users": (("/systeem/gebruikers",), ()),
    "backup": (("/systeem/back-up-maken",), ()),
    "closedto": (("/beheer/afsluiten",), ()),
    "rgs": (("/beheer/rgs",), ()),
}


def valid(key: str, back: str) -> Optional[Tuple[str, str]]:
    if key not in PAGES:
        return None
    return key, back if back in BACKS else "/beheer/ingebruikname"


def href(url: str, key: str, back: str) -> str:
    """Link van een punt (of deelstap) met ``overstap`` en ``terug``; een anker blijft achteraan."""
    if not url or key not in PAGES or not url.startswith("/"):
        return url
    path, _sep, anchor = url.partition("#")
    q = urlencode({"overstap": key, "terug": back if back in BACKS else "/beheer/ingebruikname"}, quote_via=quote)
    return f"{path}{'&' if '?' in path else '?'}{q}" + (f"#{anchor}" if anchor else "")


def _matches(path: str, prefixes: Tuple[str, ...]) -> bool:
    return any(path == p.rstrip("/") or path.startswith(p.rstrip("/") + "/") for p in prefixes)


def from_request(request) -> Optional[Tuple[str, str]]:
    """(punt, terug) uit de vraag (``?overstap=``) of anders uit het cookie."""
    q = request.query_params
    if q.get("overstap"):
        return valid(q.get("overstap", ""), q.get("terug", ""))
    raw = request.cookies.get(COOKIE) or ""
    key, _sep, back = raw.partition("|")
    return valid(key, back) if key else None


def bar(ctx) -> Optional[Dict[str, object]]:
    """Gegevens voor het balkje op deze pagina, of None."""
    if not ctx or not ctx.company:
        return None
    found = from_request(ctx.request)
    if not found:
        return None
    key, back = found
    prefixes, links = PAGES[key]
    if not _matches(ctx.request.url.path, prefixes):
        return None
    from .routes_switch import ITEMS
    out: List[Dict[str, str]] = [{"label": _(label), "href": url} for label, url in links]
    return {"text": _(ITEMS[key][0]), "back": back, "links": out}


def update_cookie(request, response, secure: bool) -> None:
    """Middleware: punt onthouden bij een link uit de lijst; vergeten bij terugkomen op de lijst."""
    if request.method != "GET":
        return
    q = request.query_params
    if q.get("overstap"):
        found = valid(q.get("overstap", ""), q.get("terug", ""))
        if found:
            response.set_cookie(COOKIE, "|".join(found), max_age=MAX_AGE, httponly=True, secure=secure,
                                samesite="lax", path="/")
    elif request.url.path in BACKS and request.cookies.get(COOKIE):
        response.delete_cookie(COOKIE, path="/", secure=secure, httponly=True)
