"""Standaardrekeningschema's als leesbare bestanden (REKENINGSCHEMA_ANALYSE.md, RS9 en RS10).

Elk schema is één bestand ``sqlledger/charts/<id>.csv``, los van de database:

* kopregels ``# sleutel: waarde`` met id, land (ISO-code), talen (de eerste is de taal van het land), valuta,
  naam per taal (``naam.nl`` …), bron, licentie en de standaardrekeningen (``voorraad``, ``omzet``, ``kosten``,
  ``koersverschil``, ``btw-afdracht``, ``btw-afronding``) en ``rgs`` (versie van de RGS-codes, bv. 3.8, alleen
  als er codes in staan) en ``volgorde`` (plaats in de keuzelijst); overige ``#``-regels zijn commentaar;
* daarna een tabel met ``;`` als scheidingsteken: rekening, soort (A = rekening, H = kop), categorie (A/L/Q/I/E),
  koppeling (SQL-Ledger ``link``, ``:`` ertussen), contra (1 = waardecorrectie), tarief (btw-rekening, bv. 0.21;
  negatief = te betalen helft van een paar verlegde btw, alleen op 2A/4A/4B),
  rubriek (btw-aangifte: NL de rubrieken van ``vatreturn``, CH de MWST-Ziffern, GB de VAT100-boxen, FR de CA3-regels, BE de roosters), rgs (referentiecode RGS) en een omschrijving per taal.

``check`` doet de controles die een schema altijd moet doorstaan (de toets draait hem voor elk bestand).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from importlib import resources
from typing import Dict, List, Optional, Tuple

from .admin import CATEGORIES, TAX_LINKS, Account, Admin
from .errors import ValidationError


class ChartFileError(Exception):
    """Fout in een meegeleverd schemabestand: voor de ontwikkelaar (de toets vangt hem), daarom niet vertaald."""

    def __init__(self, name: str, message: str):
        super().__init__(f"{name}: {message}")
from .vatreturn import ACCOUNT_BOXES

#: talen van de omschrijvingen: de vier van de webapp en Italiaans (alleen rekeningnamen, CH; keuze 2 deelstap 3)
LANGUAGES = ("nl", "en", "de", "fr", "it")
#: standaardrekeningen in de kopregels → waar ze terechtkomen
DEFAULT_KEYS = {
    "voorraad": "inventory_accno_id",
    "omzet": "income_accno_id",
    "kosten": "expense_accno_id",
    "koersverschil": "fxgainloss_accno_id",
    "btw-afdracht": "vat_settlement_accno",
    "btw-afronding": "vat_rounding_accno",
}
COLUMNS = ("rekening", "soort", "categorie", "koppeling", "contra", "tarief", "rubriek", "rgs")
#: landen (ISO-code) in de volgorde van de keuzelijst; de namen staan in de webapp
COUNTRIES = ("NL", "BE", "GB", "FR", "DE", "CH")
#: vorm van een RGS-referentiecode (B of W, daarna groepen van een hoofdletter en kleine letters/cijfers)
RGS_CODE = re.compile(r"[BW](?:[A-Z][a-z0-9]{2,3})*")
#: rubrieken waar een negatief tarief hoort: de te betalen helft van een paar verlegde btw (−21 % hier, +21 % op 5B)
REVERSE_BOXES = ("2A", "4A", "4B")
#: per land: rubrieken met een negatief tarief en de rubriek van de aftrekhelft (BTW_LANDEN_ANALYSE.md BL5)
COUNTRY_REVERSE = {"NL": (REVERSE_BOXES, "5B"), "GB": (("1", "1R"), "4"), "BE": (("86", "88", "87", "57"), "59")}
#: MWST-Ziffern (formulier ESTV, effectieve methode, vanaf 2024) die een Zwitsers schema aan een btw-rekening koppelt
#: (RS9: vastgelegd met landcode, geen aangiftescherm): omzet 8,1/2,6/3,8 %, voorbelasting materiaal/diensten en investeringen
CH_BOXES = ("303", "313", "343", "400", "405")
#: VAT100-koppelingen (HMRC, ``sqlledger.vatforms.GB``): verkoop-btw → box 1, verlegd binnenland/uitgestelde
#: invoer-btw → 1R (btw in box 1, waarde alleen in 7), verwerving goederen EU in Noord-Ierland → 2, voorbelasting → 4;
#: box 6/7/9 (netto omzet/inkoop) zijn de grondslag van die rekeningen
GB_BOXES = ("1", "1R", "2", "4")
#: regels van de Franse aangifte CA3 (3310-CA3-SD, formulier 2026): te betalen 20 % → 08, 5,5 % → 09, 10 % → 9B, 2,1 %
#: in continentaal Frankrijk → T6; voorbelasting immobilisations → 19, autres biens et services → 20
FR_BOXES = ("08", "09", "9B", "T6", "19", "20")
#: Belgische btw-aangifte (``sqlledger.vatforms.BE``): verkoop-btw → rooster van de grondslag 01/02/03 (btw in 54),
#: aftrekbare btw → 59, te betalen helft van verlegde btw → 86/88 (btw in 55), 87 (56) of 57 (invoer)
BE_BOXES = ("01", "02", "03", "59", "86", "88", "87", "57")
#: BL3: rubriek bij een kosten-/activarekening (geen btw-rekening): de Belgische roosters 81/82/83 voor de inkoop
EXPENSE_BOXES = {"BE": ("81", "82", "83")}
#: rubrieken in het schema per land in de kolom ``rubriek``
COUNTRY_BOXES = {"NL": ACCOUNT_BOXES, "CH": CH_BOXES, "GB": GB_BOXES, "FR": FR_BOXES, "BE": BE_BOXES}


@dataclass
class ChartRow:
    accno: str
    charttype: str
    category: str
    link: Tuple[str, ...]
    contra: bool
    rate: Optional[Decimal]
    box: str
    rgs: str
    descriptions: Dict[str, str]


@dataclass
class ChartFile:
    id: str
    country: str
    languages: Tuple[str, ...]
    currency: str
    names: Dict[str, str]
    source: str
    license: str
    defaults: Dict[str, str]           # sleutel uit DEFAULT_KEYS → rekeningnummer
    rows: List[ChartRow] = field(default_factory=list)
    rgs_version: str = ""
    order: int = 50                    # kopregel ``volgorde``: plaats in de keuzelijst binnen het land

    def name(self, lang: str) -> str:
        return self.names.get(lang) or self.names.get(self.languages[0]) or self.id

    @property
    def accounts(self) -> List[ChartRow]:
        return [r for r in self.rows if r.charttype == "A"]


def parse(text: str, name: str = "?") -> ChartFile:
    """Lees een schemabestand; gooit ``ChartFileError`` met bestandsnaam en regel bij een fout."""
    meta: Dict[str, str] = {}
    table: List[str] = []
    for line in text.splitlines():
        if not table and line.startswith("#"):
            key, sep, value = line[1:].partition(":")
            key = key.strip()
            if sep and key and " " not in key:
                meta[key] = value.strip()
            continue
        if line.strip():
            table.append(line)
    def need(key: str) -> str:
        if not meta.get(key):
            raise ChartFileError(name, f"kopregel '{key}' ontbreekt.")
        return meta[key]
    languages = tuple(need("talen").split())
    if not languages or any(lang not in LANGUAGES for lang in languages):
        raise ChartFileError(name, f"onbekende taal in 'talen'.")
    chart = ChartFile(
        id=need("id"), country=need("land").upper(), languages=languages, currency=need("valuta").upper(),
        names={lang: meta[f"naam.{lang}"] for lang in LANGUAGES if meta.get(f"naam.{lang}")},
        source=need("bron"), license=need("licentie"),
        defaults={key: meta[key] for key in DEFAULT_KEYS if meta.get(key)}, rgs_version=meta.get("rgs", ""),
        order=int(meta["volgorde"]) if meta.get("volgorde", "").isdigit() else 50)
    reader = csv.reader(io.StringIO("\n".join(table)), delimiter=";")
    header = next(reader, None)
    if not header or tuple(header[:len(COLUMNS)]) != COLUMNS or tuple(header[len(COLUMNS):]) != languages:
        raise ChartFileError(name, f"kolommen moeten zijn: {';'.join(COLUMNS + languages)}.")
    for n, cells in enumerate(reader, start=2):
        if len(cells) != len(header):
            raise ChartFileError(name, f"tabelregel {n}: {len(cells)} kolommen in plaats van {len(header)}.")
        v = dict(zip(header, (c.strip() for c in cells)))
        rate = None
        if v["tarief"]:
            try:
                rate = Decimal(v["tarief"])
            except InvalidOperation:
                raise ChartFileError(name, f"rekening {v['rekening']}: ongeldig tarief.") from None
        chart.rows.append(ChartRow(
            accno=v["rekening"], charttype=v["soort"], category=v["categorie"],
            link=tuple(k for k in v["koppeling"].split(":") if k), contra=v["contra"] == "1", rate=rate,
            box=v["rubriek"], rgs=v["rgs"], descriptions={lang: v[lang] for lang in languages}))
    return chart


def check(chart: ChartFile) -> None:
    """De vaste controles (RS10): koppelingen, standaardrekeningen, unieke nummers, kopjes, btw."""
    name = chart.id
    if len(chart.currency) != 3 or not chart.currency.isalpha():
        raise ChartFileError(name, f"ongeldige valuta.")
    seen = set()
    for r in chart.rows:
        if r.accno in seen:
            raise ChartFileError(name, f"rekening {r.accno} staat er dubbel in.")
        seen.add(r.accno)
        if not r.accno or not r.accno.isalnum():
            raise ChartFileError(name, f"ongeldig rekeningnummer '{r.accno}'.")
        if r.accno == "0":          # Perl: "0" is onwaar, SQL-Ledger laat zo'n rekening in rapporten weg
            raise ChartFileError(name, f"rekeningnummer 0 kan niet (SQL-Ledger slaat het over in rapporten).")
        if any(not r.descriptions[lang] for lang in chart.languages):
            raise ChartFileError(name, f"rekening {r.accno}: omschrijving ontbreekt.")
        if r.charttype == "H" and (r.link or r.rate is not None or r.box or r.contra):
            raise ChartFileError(name, f"kop {r.accno}: een kop heeft geen koppeling, tarief, rubriek of contra.")
        try:
            Admin.check_account(Account(accno=r.accno, description=r.descriptions[chart.languages[0]],
                                      charttype=r.charttype, category=r.category, link=set(r.link)))
        except ValidationError as exc:
            raise ChartFileError(name, f"rekening {r.accno}: {exc}") from None
        is_tax = any(k in r.link for k in ("AR_tax", "AP_tax"))
        if is_tax != (r.rate is not None):
            raise ChartFileError(name, f"rekening {r.accno}: een btw-rekening (AR_tax/AP_tax) heeft een tarief "
                                  "en alleen die.")
        if r.rate is not None and not (-1 < r.rate < 1):
            raise ChartFileError(name, f"rekening {r.accno}: tarief als fractie (0.21), niet als percentage.")
        reverse = COUNTRY_REVERSE.get(chart.country, ((), ""))[0]
        if r.rate is not None and r.rate < 0 and r.box not in reverse:
            raise ChartFileError(name, f"rekening {r.accno}: een negatief tarief (verlegde btw) alleen op rubriek "
                                  f"{', '.join(reverse) or '(geen in dit land)'}.")
        if r.rgs and not RGS_CODE.fullmatch(r.rgs):
            raise ChartFileError(name, f"rekening {r.accno}: ongeldige RGS-code '{r.rgs}'.")
        if r.box and not is_tax and r.box in EXPENSE_BOXES.get(chart.country, ()):
            if r.category not in ("A", "E") or r.contra:
                raise ChartFileError(name, f"rekening {r.accno}: rubriek {r.box} alleen bij een kosten- of activarekening.")
        elif r.box and (r.box not in COUNTRY_BOXES.get(chart.country, ()) or not is_tax):
            raise ChartFileError(name, f"rekening {r.accno}: rubriek {r.box} kan hier niet.")
    # elke rekening valt onder een kop van dezelfde categorie (zoals de rapporten optellen, RP::get_accounts)
    ordered = sorted(chart.rows, key=lambda r: r.accno)
    last_heading: Dict[str, str] = {}
    for r in ordered:
        if r.charttype == "H":
            last_heading[r.category] = r.accno
        elif r.category not in last_heading:
            raise ChartFileError(name, f"rekening {r.accno}: geen kop van categorie "
                                  f"{CATEGORIES[r.category]} ervoor.")
    accounts = {r.accno: r for r in chart.accounts}
    links = {k for r in chart.accounts for k in r.link}
    for needed in ("AR", "AP", "AR_amount", "AP_amount", "AR_paid", "AP_paid", "AR_tax", "AP_tax",
                   "IC", "IC_sale", "IC_cogs", "IC_income", "IC_expense", "IC_taxpart", "IC_taxservice"):
        if needed not in links:
            raise ChartFileError(name, f"geen rekening met koppeling {needed}.")
    missing = [k for k in DEFAULT_KEYS if k not in chart.defaults]
    if missing:
        raise ChartFileError(name, f"standaardrekening ontbreekt: {', '.join(missing)}.")
    want = {"voorraad": ("IC",), "omzet": ("IC_sale", "IC_income"), "kosten": ("IC_cogs", "IC_expense")}
    for key, accno in chart.defaults.items():
        acc = accounts.get(accno)
        if not acc:
            raise ChartFileError(name, f"standaardrekening {key} = {accno} bestaat niet.")
        if key in want and not any(k in acc.link for k in want[key]):
            raise ChartFileError(name, f"standaardrekening {key} = {accno} mist de koppeling "
                                  f"{' of '.join(want[key])}.")
        if key == "koersverschil" and acc.category not in ("I", "E"):
            raise ChartFileError(name, f"koersverschil moet een opbrengst- of kostenrekening zijn.")
        if key == "btw-afdracht" and (any(k in acc.link for k in TAX_LINKS) or acc.category != "L"):
            raise ChartFileError(name, f"de btw-afdrachtrekening is een schuld en geen btw-rekening.")
        if key == "btw-afronding" and acc.category not in ("I", "E"):
            raise ChartFileError(name, f"afrondingsverschillen moet een opbrengst- of kostenrekening zijn.")
    if chart.defaults["btw-afdracht"] == chart.defaults["btw-afronding"]:
        raise ChartFileError(name, f"btw-afdracht en btw-afronding moeten verschillen.")
    if chart.country == "NL" and any(r.rate is not None and not r.box for r in chart.accounts):
        raise ChartFileError(name, f"elke btw-rekening heeft in Nederland een rubriek.")
    if any(r.rgs for r in chart.rows) and not chart.rgs_version:
        raise ChartFileError(name, f"kopregel 'rgs' (versie van de RGS-codes) ontbreekt.")
    input_box = COUNTRY_REVERSE.get(chart.country, ((), ""))[1]
    for r in chart.accounts:
        if r.rate is not None and r.rate < 0 and not any(
                o.rate == -r.rate and o.box == input_box and "AP_tax" in o.link for o in chart.accounts):
            raise ChartFileError(name, f"rekening {r.accno}: bij een negatief tarief hoort een btw-rekening "
                                  f"te vorderen ({input_box}) met tarief {-r.rate}.")


def available() -> List[ChartFile]:
    """Alle meegeleverde schema's, per land in de volgorde van ``COUNTRIES``, daarbinnen op kopregel ``volgorde``
    (zonder: na de andere), het lege schema als laatste."""
    out = []
    for item in resources.files("sqlledger.charts").iterdir():
        if item.name.endswith(".csv"):
            out.append(parse(item.read_text(encoding="utf-8"), item.name))
    order = list(COUNTRIES)
    return sorted(out, key=lambda c: (order.index(c.country) if c.country in order else len(order),
                                      c.id.startswith("leeg"), c.order, c.id))


def get(chart_id: str) -> ChartFile:
    for chart in available():
        if chart.id == chart_id:
            return chart
    raise ValidationError("Onbekend rekeningschema.")
