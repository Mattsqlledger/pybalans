"""Bedrag in woorden (SL/Num2text.pm en locale/<taal>/Num2text, via CP->new).

SQL-Ledger kiest de taal uit ``language_code`` van het document, anders uit de landcode van de
gebruiker; zonder eigen Num2text-bestand voor die taal valt het terug op Engels. Hier zijn Engels,
Nederlands, Duits en Frans overgenomen (Duits en Frans correct geschreven; de Perl-versies hadden fouten,
zie ``_de`` en ``_fr``).

Bewuste afwijkingen (Nederlands, locale/nl/Num2text): spelfouten "twalf"/"achtien" verbeterd
("twaalf"/"achttien"), een rond honderdtal geeft geen "nul" meer ("eenhonderdnul" → "eenhonderd"),
en het trema in "tweeëntwintig", "drieëndertig" e.d. (README fase 4).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Union

_EN = {0: "Zero", 1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight",
       9: "Nine", 10: "Ten", 11: "Eleven", 12: "Twelve", 13: "Thirteen", 14: "Fourteen", 15: "Fifteen",
       16: "Sixteen", 17: "Seventeen", 18: "Eighteen", 19: "Nineteen", 20: "Twenty", 30: "Thirty",
       40: "Forty", 50: "Fifty", 60: "Sixty", 70: "Seventy", 80: "Eighty", 90: "Ninety",
       100: "Hundred", 10 ** 3: "Thousand", 10 ** 6: "Million", 10 ** 9: "Billion", 10 ** 12: "Trillion"}

_NL = {0: "nul", 1: "een", 2: "twee", 3: "drie", 4: "vier", 5: "vijf", 6: "zes", 7: "zeven", 8: "acht",
       9: "negen", 10: "tien", 11: "elf", 12: "twaalf", 13: "dertien", 14: "veertien", 15: "vijftien",
       16: "zestien", 17: "zeventien", 18: "achttien", 19: "negentien", 20: "twintig", 30: "dertig",
       40: "veertig", 50: "vijftig", 60: "zestig", 70: "zeventig", 80: "tachtig", 90: "negentig",
       100: "honderd", 10 ** 3: "duizend", 10 ** 6: "miljoen", 10 ** 9: "miljard", 10 ** 12: "biljoen"}

_DE = {0: "null", 1: "ein", 2: "zwei", 3: "drei", 4: "vier", 5: "fünf", 6: "sechs", 7: "sieben", 8: "acht",
       9: "neun", 10: "zehn", 11: "elf", 12: "zwölf", 13: "dreizehn", 14: "vierzehn", 15: "fünfzehn",
       16: "sechzehn", 17: "siebzehn", 18: "achtzehn", 19: "neunzehn", 20: "zwanzig", 30: "dreißig",
       40: "vierzig", 50: "fünfzig", 60: "sechzig", 70: "siebzig", 80: "achtzig", 90: "neunzig"}

_FR = {0: "zéro", 1: "un", 2: "deux", 3: "trois", 4: "quatre", 5: "cinq", 6: "six", 7: "sept", 8: "huit",
       9: "neuf", 10: "dix", 11: "onze", 12: "douze", 13: "treize", 14: "quatorze", 15: "quinze", 16: "seize",
       17: "dix-sept", 18: "dix-huit", 19: "dix-neuf", 20: "vingt", 30: "trente", 40: "quarante",
       50: "cinquante", 60: "soixante"}

#: talen met een eigen omzetting (CP->new: locale/<code>/Num2text bestaat)
LANGUAGES = {"nl": "nl", "benl": "nl", "de": "de", "ch": "de", "fr": "fr", "befr": "fr", "cafr": "fr"}


def language_for(language_code: str = "", countrycode: str = "") -> str:
    """Taal zoals IS::invoice_details: language_code van het document, anders de landcode."""
    code = (language_code or countrycode or "").strip()
    return LANGUAGES.get(code, "en")


def document_language(code: str = "", default: str = "nl") -> str:
    """Taal van een document in de webapp (D6/DT2): variant/taalcode die met een taal begint (en/us/uk/gb,
    nl/benl, de/ch/at, fr/befr/cafr), anders de documenttaal van de administratie (administratie C: Factor, Euros)."""
    c = (code or "").lower()
    if c.startswith(("en", "us", "uk", "gb")):
        return "en"
    if c.startswith(("nl", "benl")):
        return "nl"
    if c.startswith(("de", "ch", "at")):
        return "de"
    if c.startswith(("fr", "befr", "cafr")):
        return "fr"
    return default if default in ("nl", "en", "de", "fr") else "nl"


def _blocks(n: int):
    s = str(abs(n))
    out = []
    while s:
        out.append(int(s[-3:]))
        s = s[:-3]
    return out          # laagste blok eerst


def _en(n: int) -> str:
    if not n:
        return _EN[0]
    words = []
    blocks = _blocks(n)
    for i in range(len(blocks) - 1, -1, -1):
        b = blocks[i]
        if b == 0:
            continue
        if b > 99:
            words += [_EN[b // 100], _EN[100]]
            b %= 100
        if b > 20:
            words.append(_EN[b // 10 * 10] + (f" {_EN[b % 10]}" if b % 10 else ""))
        elif b > 0:
            words.append(_EN[b])
        if i:
            words.append(_EN[10 ** (i * 3)])
    return " ".join(words)


def _nl_ten(b: int) -> str:
    if b > 20 and b % 10:
        one = _NL[b % 10]
        tens = _NL[b // 10 * 10]
        glue = "ën" if one.endswith("e") else "en"
        return one + glue + tens
    return _NL[b]


def _nl(n: int) -> str:
    if not n:
        return _NL[0]
    words = ["**"]
    blocks = _blocks(n)
    for i in range(len(blocks) - 1, -1, -1):
        b = blocks[i]
        if b == 0:
            continue
        if b > 99:
            words += [_NL[b // 100], _NL[100]]
            b %= 100
        if b > 9:
            words.append(_nl_ten(b))
        elif b > 0:
            words.append(_NL[b])
        if i:
            words.append(_NL[10 ** (i * 3)])
    words.append("**")
    return "".join(words)


def _de_below_1000(b: int, last: bool) -> str:
    """0 < b < 1000 in het Duits; ``last``: het laatste blok (1 = "eins", anders "ein")."""
    out = ""
    if b > 99:
        out += _DE[b // 100] + "hundert"
        b %= 100
    if b == 1:
        out += "eins" if last else "ein"
    elif b > 20 and b % 10:
        out += _DE[b % 10] + "und" + _DE[b // 10 * 10]
    elif b:
        out += _DE[b]
    return out


def _de(n: int) -> str:
    """Duits als één woord, zoals op een cheque: 1023 = eintausenddreiundzwanzig (locale/de/Num2text gaf
    "eintausendzwanzigdrei"); 1.000.000 = einemillion, 2.000.000 = zweimillionen."""
    if not n:
        return _DE[0]
    blocks = _blocks(n)
    words = []
    big = {2: ("million", "millionen"), 3: ("milliarde", "milliarden"), 4: ("billion", "billionen")}
    for i in range(len(blocks) - 1, -1, -1):
        b = blocks[i]
        if b == 0:
            continue
        if i == 0:
            words.append(_de_below_1000(b, True))
        elif i == 1:
            words.append(_de_below_1000(b, False) + "tausend")
        else:
            one, many = big[i]
            words.append(("eine" + one) if b == 1 else (_de_below_1000(b, False) + many))
    return "".join(words)


def _fr_below_100(b: int) -> str:
    if b < 17 or b in (20, 30, 40, 50, 60):
        return _FR[b] if b in _FR else ""
    if b < 20:
        return _FR[b]
    tens, one = divmod(b, 10)
    if tens in (7, 9):                        # 70-79 = soixante-dix…, 90-99 = quatre-vingt-dix…
        base = "soixante" if tens == 7 else "quatre-vingt"
        rest = b - (60 if tens == 7 else 80)
        return base + (" et " if rest == 11 and tens == 7 else "-") + _FR[rest]
    if tens == 8:
        return "quatre-vingts" if not one else "quatre-vingt-" + _FR[one]
    return _FR[tens * 10] + (" et un" if one == 1 else "-" + _FR[one])


def _fr_below_1000(b: int, plural_ok: bool) -> str:
    """0 < b < 1000; ``plural_ok``: "cents"/"quatre-vingts" mag een s krijgen (niet vóór mille)."""
    parts = []
    h, rest = divmod(b, 100)
    if h:
        parts.append("cent" if h == 1 else _FR[h] + " cent" + ("s" if not rest and plural_ok else ""))
    if rest:
        t = _fr_below_100(rest)
        if t == "quatre-vingts" and not plural_ok:
            t = "quatre-vingt"
        parts.append(t)
    return " ".join(parts)


def _fr(n: int) -> str:
    """Frans (traditionele spelling): 21 vingt et un, 91 quatre-vingt-onze, 200 deux cents, 2000 deux mille
    (locale/fr/Num2text gaf "Mille" en "Quatre-vingt Onze"), 1.000.000 un million."""
    if not n:
        return _FR[0]
    blocks = _blocks(n)
    words = []
    big = {2: "million", 3: "milliard", 4: "billion"}
    for i in range(len(blocks) - 1, -1, -1):
        b = blocks[i]
        if b == 0:
            continue
        if i == 0:
            words.append(_fr_below_1000(b, True))
        elif i == 1:
            words.append("mille" if b == 1 else _fr_below_1000(b, False) + " mille")
        else:
            words.append(_fr_below_1000(b, True) + " " + big[i] + ("s" if b > 1 else ""))
    return " ".join(words)


def num2text(amount: Union[int, Decimal, str], language: str = "en") -> str:
    """Geheel getal in woorden (CP::num2text); ``language`` 'en', 'nl', 'de' of 'fr'."""
    n = int(Decimal(str(amount or 0)))
    return {"nl": _nl, "de": _de, "fr": _fr}.get(language, _en)(n)
