"""Helpteksten per scherm (HELP_ANALYSE.md HL1–HL12): hulpjes voor de sjablonen in ``templates/help/``.

- ``ht("zin met {b}", b=…)``: vertaalde zin, veilig opgemaakt; plaatshouders krijgen een ``hl``/``hm`` of tekst.
- ``hl("Opslaan")``: knop- of veldnaam zoals op het scherm, vertaald en tussen aanhalingstekens van de taal.
- ``hm("Verkoop", "Verkoopfactuur")``: menupad, cursief, met de menunamen in de taal van de gebruiker.

De knop-/veldnamen zijn dezelfde teksten als op de schermen; ``tests/web/test_i18n.py`` controleert dat ze daar
ook echt voorkomen, zodat de help niet uit de pas loopt als een label verandert.
"""

from __future__ import annotations

from markupsafe import Markup, escape

from . import i18n

#: aanhalingstekens per taal
QUOTES = {"nl": ("“", "”"), "en": ("“", "”"), "de": ("„", "“"), "fr": ("« ", " »")}


def ht(text: str, **values) -> Markup:
    return Markup(escape(i18n.gettext(text))).format(**values)


def hl(label: str) -> Markup:
    left, right = QUOTES.get(i18n.lang(), QUOTES["nl"])
    return Markup("{}{}{}").format(left, i18n.gettext(label), right)


def hm(*parts: str) -> Markup:
    return Markup("<em>{}</em>").format(" > ".join(i18n.pgettext("menu", p) for p in parts))


HELPERS = {"ht": ht, "hl": hl, "hm": hm}
