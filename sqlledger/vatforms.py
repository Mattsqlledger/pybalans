"""Btw-aangifteformulieren per land (BTW_LANDEN_ANALYSE.md BL1): rubrieken, koppelingen, afronding en voorstel.

De rekenkern (``sqlledger.vatreturn``) verdeelt per boeking de grondslag en de btw over **koppelingen**
(``Target``): de keuze die op *Btw > Btw-rubrieken* bij een btw-rekening staat (``x_vat_box.box``) of bij een klant
voor omzet zonder btw (``x_vat_customer_box.box``). Een koppeling zegt in welk **vak** van het formulier (``Box``)
de btw komt en in welke vakken de grondslag. In Nederland is dat één op één (rubriek 1A: grondslag én btw in 1A); in
het Verenigd Koninkrijk komt de btw van een verkooprekening in box 1 en de grondslag in box 6.

Een formulier kent verder de totalen (``round``: afronden volgens de bron en de totaalformules), het vak met het te
betalen bedrag (``payable``, positief = betalen) en een voorstel per btw-rekening (``suggest``). Labels staan in de
webapp (vertaalbaar).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

ZERO = Decimal(0)


@dataclass(frozen=True)
class Box:
    """Een vak op het formulier."""
    code: str
    side: str           # out (verschuldigd) | in (voorbelasting)
    base: bool          # grondslag op het formulier
    tax: bool           # btw op het formulier


@dataclass(frozen=True)
class Target:
    """Een koppeling: waar de btw en de grondslag van een btw-rekening (of klant) op het formulier komen."""
    code: str
    side: str                   # out: verkoop/verschuldigd (credit positief) | in: aftrek (debet positief)
    tax: Optional[str]          # vak voor de btw (None = geen btw)
    base: Tuple[str, ...]       # vakken voor de grondslag


@dataclass(frozen=True)
class VatForm:
    country: str
    boxes: Tuple[Box, ...]
    targets: Tuple[Target, ...]
    account_choices: Tuple[str, ...]        # keuzes bij een btw-rekening (zonder "none")
    customer_choices: Tuple[str, ...]       # keuzes bij een klant voor omzet zonder btw ("" = de standaard)
    default_untaxed: str                    # koppeling voor omzet zonder btw zonder keuze bij de klant
    untaxed_purchases: Optional[str]        # koppeling voor inkoop zonder btw (None = telt niet mee)
    totals: Tuple[str, ...]                 # berekende vakken in formuliervolgorde
    payable: str                            # vak met het te betalen (positief) of terug te vragen bedrag
    rounding: Callable[[Dict[str, Any]], Dict[str, Decimal]]
    suggest: Callable[[Mapping[str, Any]], str]
    deadline: Callable[..., date]            # (einde tijdvak, soort tijdvak) → uiterste datum
    reverse: Tuple[str, ...] = ()           # koppelingen die een negatief tarief mogen hebben (verlegde btw)
    quarter_offsets: Tuple[int, ...] = (0,)  # 0 = kalenderkwartalen; 1/2 = kwartalen die een of twee maanden later eindigen
    #: BL3: grondslag van de inkoop per kosten-/activarekening (BE 81/82/83); leeg = niet
    expense_boxes: Tuple[str, ...] = ()
    expense_default: str = ""
    expense_suggest: Optional[Callable[[Mapping[str, Any]], str]] = None
    #: per boeking na het verdelen (BE: creditnota's naar eigen roosters, alle roosters positief)
    adjust: Optional[Callable[[Dict[str, Any]], None]] = None
    manual: Tuple[str, ...] = ()            # BL11: vakken die niet uit de boekhouding komen (zelf invullen)

    @property
    def box(self) -> Dict[str, Box]:
        return {b.code: b for b in self.boxes}

    @property
    def target(self) -> Dict[str, Target]:
        return {t.code: t for t in self.targets}

    @property
    def account_boxes(self) -> Tuple[str, ...]:
        return self.account_choices + ("none",)


def _floor(v: Decimal) -> Decimal:
    return Decimal(math.floor(v))


def _ceil(v: Decimal) -> Decimal:
    return Decimal(math.ceil(v))


def _end_of_next_month(end: date, kind: str = "quarter") -> date:
    nxt = end + timedelta(days=1)
    first_after = date(nxt.year + (nxt.month == 12), nxt.month % 12 + 1, 1)
    return first_after - timedelta(days=1)


# ---------------------------------------------------------------------------- Nederland (BA1–BA9)

NL_BOXES: Tuple[Box, ...] = (
    Box("1A", "out", True, True), Box("1B", "out", True, True), Box("1C", "out", True, True),
    Box("1D", "out", True, True), Box("1E", "out", True, False),
    Box("2A", "out", True, True),
    Box("3A", "out", True, False), Box("3B", "out", True, False), Box("3C", "out", True, False),
    Box("4A", "out", True, True), Box("4B", "out", True, True),
    Box("5B", "in", False, True),
)


def nl_round(boxes: Dict[str, Any]) -> Dict[str, Decimal]:
    """BA5: hele euro's in uw voordeel; totalen uit de afgeronde bedragen."""
    side = {b.code: b.side for b in NL_BOXES}
    a5 = ZERO
    for code, b in boxes.items():
        if side[code] == "out":
            b.base = _floor(b.base_exact)
            b.tax = _floor(b.tax_exact)
            a5 += b.tax
        else:
            b.base = ZERO
            b.tax = _ceil(b.tax_exact)
    b5 = boxes["5B"].tax
    c5 = a5 - b5
    d5 = ZERO
    return {"5A": a5, "5B": b5, "5C": c5, "5D": d5, "5G": c5 - d5}


def nl_suggest(acc: Mapping[str, Any]) -> str:
    """Voorstel bij de eerste keer (BA3): uit naam en tarief."""
    d = (acc.get("description") or "").lower()
    link = acc.get("link") or ""
    if any(w in d for w in ("voorbelasting", "te vorderen", "vorderen", "input")) or (
            "AP_tax" in link and "AR_tax" not in link):
        return "5B"
    rate = acc.get("rate")
    if rate is None:
        return "none"
    if rate >= Decimal("0.15"):
        return "1A"
    if rate > 0:
        return "1B"
    return "1E"


NL = VatForm(
    country="NL", boxes=NL_BOXES,
    targets=tuple(Target(b.code, b.side, b.code, (b.code,)) for b in NL_BOXES),
    account_choices=tuple(b.code for b in NL_BOXES),
    customer_choices=("1E", "3A", "3B"), default_untaxed="1E", untaxed_purchases=None,
    totals=("5A", "5B", "5C", "5D", "5G"), payable="5G",
    rounding=nl_round, suggest=nl_suggest, deadline=_end_of_next_month,
    reverse=("2A", "4A", "4B"),
)


# ---------------------------------------------------------------------------- Verenigd Koninkrijk (UK1–UK6)
# VAT Notice 700/12: box 1 VAT due on sales and other outputs, 2 VAT due on acquisitions (alleen goederen
# Noord-Ierland ↔ EU), 3 = 1 + 2, 4 VAT reclaimed, 5 = 3 − 4, 6 sales ex VAT (alle omzet), 7 purchases ex VAT (ook
# import en verlegd, geen lonen), 8/9 goederen NI → EU / EU → NI. HMRC kent pence in box 1–5; op verzoek van de
# gebruiker (2026-10-13) staat elke aangifte in hele bedragen.

GB_BOXES: Tuple[Box, ...] = (
    Box("1", "out", False, True), Box("2", "out", False, True), Box("4", "in", False, True),
    Box("6", "out", True, False), Box("7", "in", True, False), Box("8", "out", True, False),
    Box("9", "in", True, False),
)
GB_TARGETS: Tuple[Target, ...] = (
    Target("1", "out", "1", ("6",)),        # btw op verkoop (ook verlegd: diensten uit het buitenland)
    Target("1R", "out", "1", ()),           # verlegd binnenland (CIS) en uitgestelde invoer-btw (PIVA): waarde alleen in 7
    Target("2", "out", "2", ("9",)),        # verwerving goederen EU → Noord-Ierland
    Target("4", "in", "4", ("7",)),         # voorbelasting; de grondslag is de inkoop
    Target("6", "out", None, ("6",)),       # omzet zonder btw (klant)
    Target("8", "out", None, ("6", "8")),   # goederen Noord-Ierland → EU (klant)
    Target("7", "in", None, ("7",)),        # inkoop zonder btw
)


def _trunc(v: Decimal) -> Decimal:
    """Hele ponden, het deel achter de komma valt weg (ook bij een negatief bedrag)."""
    return v.quantize(Decimal(1), rounding=ROUND_DOWN)


def gb_round(boxes: Dict[str, Any]) -> Dict[str, Decimal]:
    """Hele ponden (keuze gebruiker 2026-10-13: elke aangifte in hele bedragen, zoals NL BA5): verschuldigde btw
    (box 1, 2) omlaag, voorbelasting (box 4) omhoog, waardes (box 6–9) afgekapt; 3 = 1 + 2, 5 = 3 − 4 (positief =
    betalen). De centen gaan bij de btw-boeking naar de rekening afrondingsverschillen."""
    for code, b in boxes.items():
        if code in ("6", "7", "8", "9"):
            b.base, b.tax = _trunc(b.base_exact), ZERO
        elif code == "4":
            b.base, b.tax = ZERO, _ceil(b.tax_exact)
        else:
            b.base, b.tax = ZERO, _floor(b.tax_exact)
    b3 = boxes["1"].tax + boxes["2"].tax
    b5 = b3 - boxes["4"].tax
    return {"3": b3, "5": b5}


def gb_suggest(acc: Mapping[str, Any]) -> str:
    d = (acc.get("description") or "").lower()
    link = acc.get("link") or ""
    if acc.get("category") in ("I", "E") or any(w in d for w in ("payment", "hmrc", "hmc&e", "liability",
                                                                 "control")):
        return "none"
    rate = acc.get("rate")
    if rate is not None and rate < 0:           # te betalen helft van een paar verlegde btw (BL5)
        return "1R" if any(w in d for w in ("domestic", "binnenland", "postponed", "import", "cis")) else "1"
    if any(w in d for w in ("purchase", "input", "reclaim", "inkoop", "voorbelasting", "vorderen", "vorsteuer",
                            "déductible", "récupérer")) or (
            "AP_tax" in link and "AR_tax" not in link):
        return "4"
    if rate is None:
        return "none"
    return "1"


def gb_deadline(end: date, kind: str = "quarter") -> date:
    """1 kalendermaand en 7 dagen na het einde van het tijdvak (gov.uk/submit-vat-return)."""
    m = end.month + 1
    y = end.year + (m > 12)
    m = (m - 1) % 12 + 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(end.day, last)) + timedelta(days=7)


GB = VatForm(
    country="GB", boxes=GB_BOXES, targets=GB_TARGETS,
    account_choices=("1", "1R", "2", "4"),
    customer_choices=("6", "8"), default_untaxed="6", untaxed_purchases="7",
    totals=("3", "5"), payable="5",
    rounding=gb_round, suggest=gb_suggest, deadline=gb_deadline,
    reverse=("1", "1R", "2"), quarter_offsets=(0, 1, 2),
)


# ---------------------------------------------------------------------------- België (BE1–BE6)
# Periodieke btw-aangifte (model bijlage I KB nr. 1; omschrijving per rooster: Intervat-help kader II–V, gelezen
# 2026-10-13). Kader II uitgaand: 00 bijzondere regeling, 01/02/03 grondslag 6/12/21 %, 44 diensten EU (afnemer
# verlegt), 45 medecontractant, 46 IC-leveringen en ABC, 47 overige vrijgesteld/buitenland, 48/49 uitgereikte
# creditnota's (op 44+46 / de rest). Kader III inkomend: 81 handelsgoederen, grond- en hulpstoffen, 82 diensten en
# diverse goederen, 83 bedrijfsmiddelen (aftrekbare btw niet inbegrepen, "rekening houdend met de ontvangen
# creditnota's"), 84/85 ontvangen creditnota's (op 86+88 / de rest), 86 IC-verwerving goederen, 87 overige verlegging
# (o.a. invoer), 88 IC-diensten. Kader IV: 54 op 01–03, 55 op 86+88, 56 op 87 (zonder invoer), 57 invoer verlegd,
# 61 regularisaties, 63 op ontvangen creditnota's; XX = 54+55+56+57+61+63. Kader V: 59 aftrekbaar, 62 regularisaties,
# 64 op uitgereikte creditnota's; YY = 59+62+64. Kader VI: 71 = XX − YY of 72 = YY − XX. Rooster 91 bestaat niet
# meer (voorschot december afgeschaft per 2021). Intervat kent centen; op verzoek van de gebruiker (2026-10-13) staat
# elke aangifte in hele bedragen.

BE_OUT_BASE = ("00", "01", "02", "03", "44", "45", "46", "47", "48", "49")
BE_IN_BASE = ("81", "82", "83", "84", "85", "86", "87", "88")
BE_OUT_TAX = ("54", "55", "56", "57", "61", "63")
BE_IN_TAX = ("59", "62", "64")
BE_BOXES: Tuple[Box, ...] = (
    tuple(Box(c, "out", True, False) for c in BE_OUT_BASE) + tuple(Box(c, "in", True, False) for c in BE_IN_BASE)
    + tuple(Box(c, "out", False, True) for c in BE_OUT_TAX) + tuple(Box(c, "in", False, True) for c in BE_IN_TAX))
BE_TARGETS: Tuple[Target, ...] = (
    Target("01", "out", "54", ("01",)), Target("02", "out", "54", ("02",)), Target("03", "out", "54", ("03",)),
    # verlegde btw (BL5): de te betalen helft van een paar (negatief tarief), de aftrek op 59
    Target("86", "out", "55", ("86",)),     # IC-verwerving goederen
    Target("88", "out", "55", ("88",)),     # IC-diensten ontvangen
    Target("87", "out", "56", ("87",)),     # overige verlegging binnenland (medecontractant, bouw)
    Target("57", "out", "57", ("87",)),     # invoer met verlegging (grondslag ook in 87)
    Target("59", "in", "59", ()),           # aftrekbare btw; de grondslag komt per kostenrekening in 81/82/83
    # omzet zonder btw (klant, BL4)
    Target("00", "out", None, ("00",)), Target("44", "out", None, ("44",)), Target("45", "out", None, ("45",)),
    Target("46", "out", None, ("46",)), Target("47", "out", None, ("47",)),
    # inkoop per kostenrekening (BL3/BE2)
    Target("81", "in", None, ("81",)), Target("82", "in", None, ("82",)), Target("83", "in", None, ("83",)),
)


def be_adjust(boxes: Dict[str, Any]) -> None:
    """BE1: per boeking (``rubriek -> [grondslag, btw]``) negatieve bedragen naar de creditnota-roosters, zodat alle
    roosters positief blijven zoals Intervat eist. Uitgereikt: grondslag 44/46 → 48, overige → 49, verschuldigde btw
    → 64. Ontvangen: grondslag 86/88 → 84, 87 → 85, aftrekbare btw → 63; 81/82/83 blijven verlaagd ("rekening houdend
    met de ontvangen creditnota's") en het deel dat niet in 84 of 85 staat komt ook in 85."""
    def take(code: str, slot: int) -> Decimal:
        v = boxes.get(code)
        if not v or v[slot] >= 0:
            return ZERO
        amount = -v[slot]
        v[slot] = ZERO
        if not v[0] and not v[1]:
            del boxes[code]
        return amount

    def put(code: str, slot: int, amount: Decimal) -> None:
        if amount:
            v = boxes.setdefault(code, [ZERO, ZERO])
            v[slot] += amount

    for code in ("44", "46"):
        put("48", 0, take(code, 0))
    for code in ("00", "01", "02", "03", "45", "47"):
        put("49", 0, take(code, 0))
    for code in ("54", "55", "56", "57"):
        put("64", 1, take(code, 1))
    put("63", 1, take("59", 1))
    to84 = take("86", 0) + take("88", 0)
    to85 = take("87", 0)
    put("84", 0, to84)
    received = -sum((boxes[c][0] for c in ("81", "82", "83") if c in boxes and boxes[c][0] < 0), ZERO)
    put("85", 0, to85 + max(ZERO, received - to84 - to85))


def be_round(boxes: Dict[str, Any]) -> Dict[str, Decimal]:
    """Hele euro's (keuze gebruiker 2026-10-13, zoals NL BA5): grondslagen en verschuldigde btw (54–57, 61, 63)
    omlaag, aftrekbare btw (59, 62, 64) omhoog; XX, YY en 71/72 uit de afgeronde bedragen. ``saldo`` = XX − YY
    (positief = betalen) voor de btw-boeking."""
    for code, b in boxes.items():
        if code in BE_IN_TAX:
            b.base, b.tax = ZERO, _ceil(b.tax_exact)
        elif code in BE_OUT_TAX:
            b.base, b.tax = ZERO, _floor(b.tax_exact)
        else:
            b.base, b.tax = _floor(b.base_exact), ZERO
    xx = sum((boxes[c].tax for c in BE_OUT_TAX), ZERO)
    yy = sum((boxes[c].tax for c in BE_IN_TAX), ZERO)
    saldo = xx - yy
    return {"XX": xx, "YY": yy, "71": max(saldo, ZERO), "72": max(-saldo, ZERO), "saldo": saldo}


def be_suggest(acc: Mapping[str, Any]) -> str:
    """Voorstel per btw-rekening: tarief 6/12/21 % → 01/02/03 (btw in 54), aftrek → 59, negatief tarief (verlegd) uit
    de omschrijving → 86/88/87/57."""
    d = (acc.get("description") or "").lower()
    link = acc.get("link") or ""
    if acc.get("category") in ("I", "E") or any(w in d for w in ("rekening-courant", "compte courant", "current account")):
        return "none"
    rate = acc.get("rate")
    if rate is not None and rate < 0:
        if any(w in d for w in ("invoer", "import", "einfuhr")):
            return "57"
        if any(w in d for w in ("dienst", "service", "leistung")):
            return "88"
        if any(w in d for w in ("verwerving", "acquisition", "erwerb", "goederen", "biens", "goods")):
            return "86"
        return "87"
    if any(w in d for w in ("terug te vorderen", "aftrekbaar", "récupérer", "déductible", "input", "vorsteuer",
                            "voorbelasting")) or ("AP_tax" in link and "AR_tax" not in link):
        return "59"
    if rate is None:
        return "none"
    if rate >= Decimal("0.17"):
        return "03"
    if rate >= Decimal("0.09"):
        return "02"
    if rate > 0:
        return "01"
    return "none"


def be_expense_suggest(acc: Mapping[str, Any]) -> str:
    """BL3: voorstel per kosten-/activarekening uit de klasse van het minimumrekeningschema (MAR): 60 en voorraden (3)
    → 81, vaste activa (2) → 83, de rest (61–64 e.a.) → 82."""
    accno = acc.get("accno") or ""
    if accno.startswith("60") or accno.startswith("3") or "IC" in (acc.get("link") or "").split(":"):
        return "81"
    if accno.startswith("2"):
        return "83"
    return "82"


def be_deadline(end: date, kind: str = "quarter") -> date:
    """Maandaangifte uiterlijk de 20e, kwartaalaangifte de 25e van de maand na het tijdvak."""
    nxt = end + timedelta(days=1)
    return date(nxt.year, nxt.month, 25 if kind == "quarter" else 20)


BE = VatForm(
    country="BE", boxes=BE_BOXES, targets=BE_TARGETS,
    account_choices=("01", "02", "03", "59", "86", "88", "87", "57"),
    customer_choices=("44", "45", "46", "47", "00"), default_untaxed="47", untaxed_purchases=None,
    totals=("XX", "YY", "71", "72", "saldo"), payable="saldo",
    rounding=be_round, suggest=be_suggest, deadline=be_deadline,
    reverse=("86", "88", "87", "57"),
    expense_boxes=("81", "82", "83"), expense_default="82", expense_suggest=be_expense_suggest,
    adjust=be_adjust, manual=("61", "62"),
)


FORMS: Dict[str, VatForm] = {"NL": NL, "GB": GB, "BE": BE}
#: valuta → voorstel voor het land van de aangifte (BL2)
#: valuta → land voor het voorstel; CHF heeft (nog) geen formulier (VP3: dan geen voorstel i.p.v. Nederland)
CURRENCY_COUNTRY = {"GBP": "GB", "EUR": "NL", "CHF": "CH"}


def form_for(country: Optional[str]) -> VatForm:
    return FORMS.get((country or "").upper(), NL)
