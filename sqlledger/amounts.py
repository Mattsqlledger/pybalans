"""Bedragen: inlezen, afronden en opmaken (Form.pm parse/round/format_amount).

Bewuste afwijking van Perl: er wordt met ``Decimal`` gerekend en afgerond met
ROUND_HALF_UP. Perl rekent met floats en telt 0.501 op voor het afkappen,
waardoor bijvoorbeeld 1.00499 naar 1.01 afrondt; hier wordt dat 1.00.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional, Union

Number = Union[Decimal, int, float, str, None]

_LEADING_NUMBER = re.compile(r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")


def to_decimal(value: Number) -> Decimal:
    """Zet een waarde om naar Decimal; None/'' wordt 0.

    Floats uit de database (kolommen zijn ``float``) worden via ``repr``
    omgezet, zodat 10.1 ook echt Decimal('10.1') wordt.
    """
    if value is None or value == "":
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    if isinstance(value, int):
        return Decimal(value)
    # string: zoals Perl's numerieke conversie het numerieke begin pakken
    m = _LEADING_NUMBER.match(str(value))
    if not m:
        return Decimal(0)
    try:
        return Decimal(m.group(1))
    except InvalidOperation:
        return Decimal(0)


def opt_decimal(value: Number) -> Optional[Decimal]:
    """Als to_decimal, maar None blijft None (voor NULL-kolommen)."""
    return None if value is None else to_decimal(value)


def parse_amount(amount: Number, numberformat: str = "1,000.00") -> Decimal:
    """Lees een door de gebruiker ingevoerd bedrag in (Form::parse_amount)."""
    if amount is None:
        return Decimal(0)
    if not isinstance(amount, str):
        return to_decimal(amount)
    s = amount
    if numberformat in ("1.000,00", "1000,00"):
        s = s.replace(".", "")
        s = s.replace(",", ".", 1)
    if numberformat == "1'000.00":
        s = s.replace("'", "")
    s = s.replace(",", "")
    return to_decimal(s)


def round_amount(amount: Number, places: Union[int, str, None] = 0) -> Decimal:
    """Rond af op ``places`` decimalen, half naar boven (weg van nul)."""
    d = to_decimal(amount)
    p = int(to_decimal(places))
    return d.quantize(Decimal(1).scaleb(-p), rounding=ROUND_HALF_UP)


def _plain(d: Decimal) -> str:
    """Decimal als string zoals Perl een getal print: geen exponent, geen
    overbodige nullen achter de komma."""
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def format_amount(
    amount: Number,
    numberformat: str = "1,000.00",
    places: Union[int, str, None] = None,
    dash: str = "",
) -> str:
    """Maak een bedrag op voor weergave (Form::format_amount).

    dash: '-' => negatief tussen haakjes, 'DRCR' => 'DR'/'CR' achtervoegsel,
    '0' => nul tonen als 0.00, anders wordt dash zelf getoond bij nul.
    """
    d = to_decimal(amount)
    if places is not None and re.search(r"\d+", str(places)):
        d = round_amount(d, places)
        places = int(places)
    else:
        places = 0

    negative = d < 0

    if d:
        if not numberformat:
            return _plain(d)
        whole, _, dec = _plain(d).partition(".")
        whole = whole.lstrip("-")
        if places:
            dec = (dec + "0" * places)[:places]

        def group(sep: str) -> str:
            rev = whole[::-1]
            parts = [rev[i:i + 3] for i in range(0, len(rev), 3)]
            return sep.join(parts)[::-1]

        if numberformat == "1,000.00":
            out = group(",") + (f".{dec}" if dec != "" else "")
        elif numberformat == "1'000.00":
            out = group("'") + (f".{dec}" if dec != "" else "")
        elif numberformat == "1.000,00":
            out = group(".") + (f",{dec}" if dec != "" else "")
        elif numberformat == "1000,00":
            out = whole + (f",{dec}" if dec != "" else "")
        elif numberformat == "1000.00":
            out = whole + (f".{dec}" if dec != "" else "")
        elif numberformat == "100000":
            out = whole + dec
        else:
            out = _plain(abs(d))

        if "-" in dash:
            return f"({out})" if negative else out
        if "DRCR" in dash:
            return f"{out} DR" if negative else f"{out} CR"
        return f"-{out}" if negative else out

    if dash == "0" and places:
        if numberformat == "1.000,00":
            return "0," + "0" * places
        return "0." + "0" * places
    return dash if dash != "" else ""
