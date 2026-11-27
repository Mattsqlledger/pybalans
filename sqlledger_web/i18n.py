"""Vertalingen. De teksten in de code zijn Nederlands; andere talen komen als
woordenboek per taal in ``CATALOGS`` (B8).

- ``_("tekst")`` vertaalt meteen (in een verzoek: naar de taal van de gebruiker).
- ``pgettext("menu", tekst)``: als dezelfde Nederlandse tekst in een context anders heet.
- ``N_("tekst")`` markeert alleen: voor teksten in vaste lijsten/woordenboeken
  op moduleniveau, die pas bij het tonen met ``_(…)`` vertaald worden.
- Teksten met variabelen: ``_("Boeking {n} niet gevonden.").format(n=…)``.

De taal wordt per verzoek gezet (``main.py``, uit ``app_user.language``; zonder
sessie uit de browsertaal, TL4). ``tests/web/test_i18n.py`` controleert dat elke
gemarkeerde tekst een Engelse, Duitse en Franse vertaling heeft.

Een taal toevoegen (DT6): ``i18n_<taal>.py`` met dezelfde opbouw als ``i18n_de.py``
en hieronder registreren.
"""

from __future__ import annotations

import re
from contextvars import ContextVar

from . import i18n_de, i18n_fr
from .i18n_en import CONTEXT, EN, LIBRARY

LANGUAGES = {"nl": "Nederlands", "en": "English", "de": "Deutsch", "fr": "Français"}
CATALOGS: dict[str, dict[str, str]] = {
    "nl": {}, "en": {**LIBRARY, **EN},
    "de": {**i18n_de.LIBRARY, **i18n_de.TEXTS}, "fr": {**i18n_fr.LIBRARY, **i18n_fr.TEXTS}}
#: vertalingen die per context afwijken (bv. menu-item "Valuta" = "Currencies", veld "Valuta" = "Currency")
CONTEXTS: dict[str, dict[str, dict[str, str]]] = {
    "nl": {}, "en": CONTEXT, "de": i18n_de.CONTEXT, "fr": i18n_fr.CONTEXT}
#: bibliotheekmeldingen in de talen naast nl/en (``libmsg``): sjablonen, termen, woorddelen
LIBMSG: dict[str, object] = {"de": i18n_de, "fr": i18n_fr}
#: getal- en datumnotatie die bij een nieuwe gebruiker bij de taal wordt voorgesteld (TL6)
FORMATS = {"nl": ("1.000,00", "dd-mm-yy"), "en": ("1,000.00", "dd/mm/yy"),
           "de": ("1.000,00", "dd.mm.yy"), "fr": ("1000,00", "dd/mm/yy")}

#: eigen woorden per installatie (TL9, ``overrides.py``): taal -> (teksten, contexten); gaan vóór de catalogus
OVERRIDES: dict[str, tuple[dict[str, str], dict[str, dict[str, str]]]] = {}

current_language: ContextVar[str] = ContextVar("current_language", default="nl")


def gettext(text: str) -> str:
    if not text or not isinstance(text, str):
        return text
    language = current_language.get()
    own = OVERRIDES.get(language)
    if own and text in own[0]:
        return own[0][text]
    return CATALOGS.get(language, {}).get(text, text)


_ = gettext


def pgettext(context: str, text: str) -> str:
    """Vertaling binnen een context (``"menu"``); anders gewoon ``gettext``."""
    language = current_language.get()
    own = OVERRIDES.get(language)
    found = (own[1].get(context, {}).get(text) if own else None) \
        or CONTEXTS.get(language, {}).get(context, {}).get(text)
    return found if found else gettext(text)


def N_(text: str) -> str:
    """Markeer een tekst voor vertaling zonder te vertalen (vertalen gebeurt bij het tonen)."""
    return text


def lang() -> str:
    return current_language.get()


def browser_language(header: str | None) -> str:
    """Eerste taal uit ``Accept-Language`` die de app kent (q-waarden tellen), anders Nederlands."""
    best, best_q = "nl", 0.0
    for i, part in enumerate((header or "").split(",")[:20]):
        m = re.fullmatch(r"\s*([A-Za-z]{1,8})(?:-[A-Za-z0-9]{1,8})*\s*(?:;\s*q\s*=\s*([01](?:\.\d{0,3})?))?\s*", part)
        if not m:
            continue
        code, q = m.group(1).lower(), float(m.group(2)) if m.group(2) else 1.0
        if code in LANGUAGES and q > best_q:
            best, best_q = code, q
    return best
