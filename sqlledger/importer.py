"""Importeren en exporteren van stamgegevens en memoriaal (FASE4_ANALYSE.md stap 7, D18–D20, keuzes 1–12).

Zoals SQL-Ledger im.pl/IM.pm: een CSV- of xlsx-bestand met een kopregel, kolommen gekoppeld aan veldnamen (de
SL-namen werken altijd), per rij nieuw of bijwerken op de sleutel (relatienummer, artikelnummer, rekeningnummer,
groepsnaam; memoriaal per referentie). Anders dan SL:

* het voorbeeld is een **proefrun**: alle rijen gaan door dezelfde bibliotheek als de schermen (``Contacts.save``,
  ``Parts.save``, ``Admin.save_account``, ``Parts.save_group``, ``GeneralLedger.post_transaction``) in één
  transactie die daarna wordt teruggedraaid; zo toont het voorbeeld precies welke rij een fout geeft;
* importeren gebeurt in één transactie: alles of niets;
* een lege cel laat het veld ongewijzigd; CSV met regeleinden binnen aanhalingstekens; memoriaal groepeert alle rijen
  met dezelfde referentie (SL alleen opeenvolgende) en weigert een boeking die niet in balans is.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field, fields as dc_fields
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import psycopg

from . import _db
from .admin import Account, Admin
from .amounts import format_amount, parse_amount, to_decimal
from .contacts import Contacts, Relation, changed_fields
from .dates import parse_date
from .errors import LedgerError, ValidationError
from .gl import GeneralLedger
from .models import GLTransaction, JournalLine
from .parts import Part, Parts

KINDS = ("customer", "vendor", "part", "service", "partsgroup", "coa", "gifi", "gl",
         "sales_invoice", "sales_order", "purchase_order", "payment")
#: documenten (bouwstap 4, BEHEER_EXTRA_ANALYSE.md §3 IM1–IM8): meerdere rijen per document, altijd nieuw
DOC_KINDS = ("sales_invoice", "sales_order", "purchase_order")
MAX_ROWS = 5000


@dataclass(frozen=True)
class Field:
    """Een importeerbaar veld: naam = SL-veldnaam (ook de kop van onze export), soort, label (vertaalbaar)."""
    name: str
    kind: str            # str int dec pct bool date accno taxes ref group curr
    label: str
    target: str = ""     # attribuut in het model (standaard = name)
    ref: str = ""        # bij kind 'ref': tabel/kolom om op omschrijving te zoeken

    @property
    def attr(self) -> str:
        return self.target or self.name


def _f(name, kind, label, target="", ref=""):
    return Field(name, kind, label, target, ref)


_VC_COMMON = [
    _f("name", "str", "Naam"), _f("typeofcontact", "str", "Soort relatie"),
    _f("address1", "str", "Adres"), _f("address2", "str", "Adres 2"), _f("zipcode", "str", "Postcode"),
    _f("city", "str", "Plaats"), _f("state", "str", "Provincie"), _f("country", "str", "Land"),
    _f("salutation", "str", "Aanhef"), _f("firstname", "str", "Voornaam"), _f("lastname", "str", "Achternaam"),
    _f("contacttitle", "str", "Titel"), _f("occupation", "str", "Functie"), _f("gender", "str", "Geslacht"),
    _f("phone", "str", "Telefoon"), _f("fax", "str", "Fax"), _f("mobile", "str", "Mobiel"),
    _f("email", "str", "E-mail"), _f("cc", "str", "Cc"), _f("bcc", "str", "Bcc"), _f("notes", "str", "Notities"),
    _f("terms", "int", "Betalingstermijn"), _f("discount", "dec", "Korting %"),
    _f("cashdiscount", "dec", "Betalingskorting %"), _f("discountterms", "int", "Kortingstermijn"),
    _f("creditlimit", "dec", "Kredietlimiet"), _f("threshold", "dec", "Drempel"),
    _f("taxincluded", "bool", "Prijzen incl. btw"), _f("taxaccounts", "taxes", "Btw"),
    _f("arap_accno", "accno", "Debiteuren-/crediteurenrekening"), _f("payment_accno", "accno", "Betaalrekening"),
    _f("discount_accno", "accno", "Kortingsrekening"), _f("curr", "curr", "Valuta"),
    _f("language_code", "str", "Taal"), _f("business", "ref", "Branche", "business_id", "business.description"),
    _f("employee", "ref", "Verkoper", "employee_id", "employee.name"),
    _f("paymentmethod", "ref", "Betaalmethode", "paymentmethod_id", "paymentmethod.description"),
    _f("pricegroup", "ref", "Prijsgroep", "pricegroup_id", "pricegroup.pricegroup"),
    _f("taxnumber", "str", "Btw-nummer"), _f("sic_code", "str", "SBI-code"),
    _f("remittancevoucher", "bool", "Acceptgiro"), _f("startdate", "date", "Begindatum"),
    _f("enddate", "date", "Einddatum"), _f("iban", "str", "IBAN"), _f("bic", "str", "BIC"),
    _f("bankname", "str", "Bank"), _f("bankaddress1", "str", "Adres bank"), _f("bankzipcode", "str", "Postcode bank"),
    _f("bankcity", "str", "Plaats bank"), _f("bankstate", "str", "Provincie bank"),
    _f("bankcountry", "str", "Land bank"), _f("membernumber", "str", "Lidnummer"),
    _f("clearingnumber", "str", "Clearingnummer"),
]
_PART = [
    _f("partnumber", "str", "Artikelnummer"), _f("description", "str", "Omschrijving"), _f("unit", "str", "Eenheid"),
    _f("partsgroup", "group", "Artikelgroep", "partsgroup_id"), _f("sellprice", "dec", "Verkoopprijs"),
    _f("listprice", "dec", "Lijstprijs"), _f("lastcost", "dec", "Inkoopprijs"),
    _f("priceupdate", "date", "Prijs bijgewerkt"), _f("weight", "dec", "Gewicht"), _f("rop", "dec", "Bestelniveau"),
    _f("bin", "str", "Locatie"), _f("barcode", "str", "Barcode"), _f("notes", "str", "Notities"),
    _f("obsolete", "bool", "Vervallen"), _f("inventory_accno", "accno", "Voorraadrekening"),
    _f("income_accno", "accno", "Opbrengstrekening"), _f("expense_accno", "accno", "Kostenrekening"),
    _f("taxaccounts", "taxes", "Btw"),
]
#: kop van een factuur/order (na nummer, relatie, datums en omschrijving)
_DOC_HEAD = [
    _f("ordnumber", "str", "Ordernummer"), _f("ponumber", "str", "Inkoopordernummer"), _f("curr", "curr", "Valuta"),
    _f("exchangerate", "dec", "Koers"), _f("taxincluded", "bool", "Prijzen incl. btw"),
    _f("notes", "str", "Notities"), _f("intnotes", "str", "Interne notities"),
    _f("employeenumber", "ref", "Medewerkernummer", "employee_id", "employee.employeenumber"),
    _f("department", "ref", "Afdeling", "department_id", "department.description"),
]
#: regel van een factuur/order
_DOC_LINE = [
    _f("partnumber", "str", "Artikelnummer"), _f("description", "str", "Omschrijving"), _f("qty", "dec", "Aantal"),
    _f("sellprice", "dec", "Prijs"), _f("discount", "dec", "Korting %"), _f("unit", "str", "Eenheid"),
    _f("projectnumber", "ref", "Project", "project_id", "project.projectnumber"),
]
FIELDS: Dict[str, List[Field]] = {
    "customer": [_f("customernumber", "str", "Klantnummer", "number")] + _VC_COMMON,
    "vendor": [_f("vendornumber", "str", "Leveranciersnummer", "number")] + _VC_COMMON,
    "part": _PART,
    "service": [f for f in _PART if f.name not in ("inventory_accno", "rop", "bin")],
    "partsgroup": [_f("partsgroup", "str", "Artikelgroep"), _f("code", "str", "Code")],
    "coa": [_f("accno", "str", "Rekeningnummer"), _f("description", "str", "Omschrijving"),
            _f("charttype", "str", "Rekening of kop (A/H)"), _f("category", "str", "Soort (A/L/Q/I/E)"),
            _f("link", "str", "Koppelingen"), _f("gifi_accno", "str", "GIFI"), _f("contra", "bool", "Contra"),
            _f("closed", "bool", "Gesloten"), _f("bankname", "str", "Bank"), _f("iban", "str", "IBAN"),
            _f("bic", "str", "BIC")],
    "gifi": [_f("accno", "str", "Code"), _f("description", "str", "Omschrijving")],      # GF5
    "sales_invoice": [_f("invnumber", "str", "Factuurnummer"), _f("customernumber", "str", "Klantnummer"),
                      _f("transdate", "date", "Factuurdatum"), _f("duedate", "date", "Vervaldatum"),
                      _f("invoicedescription", "str", "Omschrijving factuur")] + _DOC_HEAD + [
                      _f("AR", "accno", "Debiteurenrekening"), _f("dcn", "str", "Betalingskenmerk"),
                      _f("paid_1", "dec", "Betaald bedrag"), _f("datepaid_1", "date", "Betaaldatum"),
                      _f("AR_paid_1", "accno", "Betaalrekening"), _f("source_1", "str", "Bron betaling"),
                      _f("memo_1", "str", "Memo betaling")] + _DOC_LINE,
    "sales_order": [_f("ordnumber", "str", "Ordernummer"), _f("customernumber", "str", "Klantnummer"),
                    _f("transdate", "date", "Orderdatum"), _f("reqdate", "date", "Leverdatum"),
                    _f("orderdescription", "str", "Omschrijving order")] + _DOC_HEAD[1:] + _DOC_LINE,
    "purchase_order": [_f("ordnumber", "str", "Ordernummer"), _f("vendornumber", "str", "Leveranciersnummer"),
                       _f("transdate", "date", "Orderdatum"), _f("reqdate", "date", "Leverdatum"),
                       _f("orderdescription", "str", "Omschrijving order")] + _DOC_HEAD[1:] + _DOC_LINE,
    # IM7: één rij = één betaling op één factuur
    "payment": [_f("datepaid", "date", "Betaaldatum"), _f("amount", "dec", "Bedrag"), _f("credit", "dec", "Credit"),
                _f("debit", "dec", "Debet"), _f("invnumber", "str", "Factuurnummer"),
                _f("dcn", "str", "Betalingskenmerk"), _f("customernumber", "str", "Klantnummer"),
                _f("vendornumber", "str", "Leveranciersnummer"), _f("accno", "accno", "Betaalrekening"),
                _f("source", "str", "Bron"), _f("memo", "str", "Memo"), _f("exchangerate", "dec", "Koers")],
    "gl": [_f("reference", "str", "Referentie"), _f("transdate", "date", "Datum"),
           _f("description", "str", "Omschrijving"), _f("notes", "str", "Notities"),
           _f("department", "ref", "Afdeling", "department_id", "department.description"),
           _f("curr", "curr", "Valuta"), _f("exchangerate", "dec", "Koers"), _f("accno", "accno", "Rekening"),
           _f("debit", "dec", "Debet"), _f("credit", "dec", "Credit"), _f("source", "str", "Bron"),
           _f("memo", "str", "Memo"), _f("project", "ref", "Project", "project_id", "project.projectnumber")],
}
#: sleutel per soort (leeg = altijd nieuw)
KEYS = {"customer": "customernumber", "vendor": "vendornumber", "part": "partnumber", "service": "partnumber",
        "partsgroup": "partsgroup", "coa": "accno", "gifi": "accno", "gl": "reference",
        "sales_invoice": "invnumber", "sales_order": "ordnumber", "purchase_order": "ordnumber",
        "payment": "invnumber"}
#: documenten: kolom met het relatienummer en de soort relatie
DOC_VC = {"sales_invoice": ("customernumber", "customer"), "sales_order": ("customernumber", "customer"),
          "purchase_order": ("vendornumber", "vendor")}
_SHIPTO = tuple("shipto" + x for x in ("name", "address1", "address2", "city", "state", "zipcode", "country",
                                       "contact", "phone", "fax", "email"))
#: SL-velden die we kennen maar (nog) niet inlezen: in de koppeling als "niet ondersteund"
UNSUPPORTED = {
    "customer": ("address", "shiptoname", "shiptoaddress1", "shiptocity", "prepayment_accno", "gifi_accno"),
    "part": ("image", "drawing", "microfiche", "make", "model", "code", "tariff_hscode", "countryorigin",
             "toolnumber", "vendornumber", "vendorpartnumber", "leadtime", "vendorcurr", "assembly"),
    "gl": ("exchangerate_fx",),
    "sales_invoice": _SHIPTO + ("shippingpoint", "shipvia", "waybill", "warehouse", "serialnumber", "paid_2",
                                "datepaid_2", "ar_paid_2"),
    "sales_order": _SHIPTO + ("shippingpoint", "shipvia", "waybill", "warehouse", "serialnumber", "quonumber"),
}
UNSUPPORTED["purchase_order"] = UNSUPPORTED["sales_order"]
UNSUPPORTED["vendor"] = UNSUPPORTED["customer"]
UNSUPPORTED["service"] = UNSUPPORTED["part"]


def field_map(kind: str) -> Dict[str, Field]:
    return {f.name: f for f in FIELDS[kind]}


# ------------------------------------------------------------------ bestand lezen

class ImportFileError(LedgerError):
    """Het bestand kan niet gelezen worden (geen kop, te veel rijen, onbekende vorm)."""


def _decode(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def read_file(data: bytes, filename: str = "") -> Tuple[List[str], List[List[Any]]]:
    """Kop en rijen uit een CSV- of xlsx-bestand (keuze 3). Lege rijen vallen weg; cellen als tekst of
    (xlsx) als getal/datum."""
    if data[:2] == b"PK" or filename.lower().endswith((".xlsx", ".xlsm")):
        try:
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001 - elk leesprobleem = geen geldig xlsx
            raise ImportFileError("Dit is geen leesbaar xlsx-bestand.") from exc
        ws = wb.worksheets[0]
        raw = [list(r) for r in ws.iter_rows(values_only=True)]
        wb.close()
    else:
        text = _decode(data)
        first = text.split("\n", 1)[0]
        delim = max((";", ",", "\t"), key=first.count)
        raw = list(csv.reader(io.StringIO(text, newline=""), delimiter=delim))
    rows = [[("" if c is None else c) for c in r] for r in raw]
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        raise ImportFileError("Het bestand is leeg.")
    header = [str(c).strip() for c in rows[0]]
    body = rows[1:]
    if len(body) > MAX_ROWS:
        raise ImportFileError(f"Te veel rijen ({len(body)}); hoogstens {MAX_ROWS} per keer.")
    width = len(header)
    body = [(r + [""] * width)[:width] for r in body]
    return header, body


def _norm_head(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def auto_map(kind: str, header: Sequence[str], aliases: Optional[Dict[str, Sequence[str]]] = None,
             saved: Optional[Dict[str, str]] = None) -> List[str]:
    """Veld per kolom (keuze 4): eerst een bewaarde koppeling (op kop), dan de SL-veldnaam, dan het label
    (``aliases``: veldnaam → koppen in de schermtalen). Onbekend = ''."""
    names = {_norm_head(f.name): f.name for f in FIELDS[kind]}
    for f in FIELDS[kind]:
        for a in [f.label] + list((aliases or {}).get(f.name, ())):
            names.setdefault(_norm_head(a), f.name)
    out, used = [], set()
    for h in header:
        target = (saved or {}).get(h) if saved and h in saved else names.get(_norm_head(h), "")
        if target and (target in used or target not in field_map(kind)):
            target = ""
        used.add(target)
        out.append(target or "")
    return out


# ------------------------------------------------------------------ waarden

class CellError(ValueError):
    """Een cel die niet te lezen is: ``code`` (number int date bool curr ref) en de waarde."""

    def __init__(self, code: str, value: str = ""):
        super().__init__(f"{code}: {value}")
        self.code, self.value = code, value


def cell_error(f: "Field", exc: CellError) -> ValidationError:
    """Volledige melding (vertaalbaar sjabloon in sqlledger_web.libmsg) voor een cel van kolom ``f``."""
    label, value = f.label, exc.value
    if exc.code == "number":
        return ValidationError(f"Kolom {label}: {value} is geen getal.")
    if exc.code == "int":
        return ValidationError(f"Kolom {label}: {value} is geen geheel getal.")
    if exc.code == "date":
        return ValidationError(f"Kolom {label}: {value} is geen datum.")
    if exc.code == "bool":
        return ValidationError(f"Kolom {label}: {value} is geen ja of nee.")
    if exc.code == "curr":
        return ValidationError(f"Kolom {label}: valuta {value} bestaat niet.")
    return ValidationError(f"Kolom {label}: {value} bestaat niet.")


def _text(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    if len(s) > 1 and s[0] == "'" and s[1] in "=+-@":     # apostrof van onze export (formule-injectie)
        s = s[1:]
    return s


_PLAIN = re.compile(r"-?\d+(\.\d+)?")
_THOUSANDS_DOT = re.compile(r"-?\d{1,3}(\.\d{3})+")


def parse_number(v: Any, nf: str) -> Decimal:
    """Getal in de notatie van de gebruiker, of 1234.56 (keuze 9)."""
    if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
        return to_decimal(v)
    s = _text(v).replace(" ", "").replace(" ", "")
    comma = nf in ("1.000,00", "1000,00")
    if _PLAIN.fullmatch(s) and not (comma and _THOUSANDS_DOT.fullmatch(s)):
        return Decimal(s)
    if not re.fullmatch(r"-?[\d.,']+", s):
        raise CellError("number", s)
    try:
        return parse_amount(s, nf)
    except (InvalidOperation, ValueError) as exc:
        raise CellError("number", s) from exc


def parse_cell_date(v: Any, df: str) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        d = parse_date(_text(v), df)
    except (ValueError, IndexError) as exc:
        raise CellError("date", _text(v)) from exc
    if d is None:
        raise CellError("date", "")
    return d


def parse_bool(v: Any) -> bool:
    s = _text(v).lower()
    if s in ("1", "true", "ja", "yes", "j", "y", "x", "✓", "oui", "o"):
        return True
    if s in ("0", "false", "nee", "no", "n", "non", "nein"):
        return False
    raise CellError("bool", s)


# ------------------------------------------------------------------ voorbeeld en import

@dataclass
class Item:
    """Eén te importeren eenheid: een rij (stamgegevens) of een boeking (memoriaal, meerdere rijen)."""
    rows: List[int]                           # rijnummers in het bestand (1 = eerste rij na de kop)
    key: str = ""
    raw: str = ""                             # sleutel precies zoals in het bestand
    label: str = ""
    status: str = ""                          # new update same error
    changes: List[str] = field(default_factory=list)
    error: str = ""
    warning: str = ""
    selected: bool = True
    #: documenten (IM5): valuta, netto, btw en totaal zoals op het scherm; betalingen: valuta, bedrag, openstaand
    amounts: Optional[Tuple[Any, ...]] = None


@dataclass
class Result:
    items: List[Item]
    added: int = 0
    updated: int = 0

    @property
    def errors(self) -> List[Item]:
        return [i for i in self.items if i.status == "error"]


class _Rollback(Exception):
    pass


class Importer:
    """Proefrun (``preview``) en import (``run``) van één bestand voor één soort."""

    def __init__(self, conn: psycopg.Connection, config, kind: str, numberformat: str = "1,000.00",
                 dateformat: str = "yyyy-mm-dd"):
        if kind not in KINDS:
            raise ValidationError("Onbekende soort import.")
        self.conn = conn
        self.config = config
        self.kind = kind
        self.nf = numberformat
        self.df = dateformat
        self.fields = field_map(kind)
        self.new_groups: List[str] = []

    # ---------------------------------------------------------------- rijen → items

    def _records(self, mapping: Sequence[str], rows: Sequence[Sequence[Any]]) -> List[Dict[str, Any]]:
        out = []
        for r in rows:
            rec = {}
            for name, v in zip(mapping, r):
                if name and _text(v) != "" and not (isinstance(v, str) and v.strip() == ""):
                    rec[name] = v
            out.append(rec)
        return out

    def _items(self, recs: List[Dict[str, Any]]) -> List[Tuple[Item, List[Dict[str, Any]]]]:
        key = KEYS[self.kind]
        if self.kind in DOC_KINDS:
            return self._doc_items(recs)
        if self.kind != "gl":
            return [(Item(rows=[n], key=_text(rec.get(key, "")), raw=_raw(rec.get(key, ""))), [rec])
                    for n, rec in enumerate(recs, 1)]
        groups: Dict[str, Tuple[Item, List[Dict[str, Any]]]] = {}
        order: List[Tuple[Item, List[Dict[str, Any]]]] = []
        current: Optional[Tuple[Item, List[Dict[str, Any]]]] = None
        for n, rec in enumerate(recs, 1):
            ref = _text(rec.get("reference", ""))
            if ref:
                if ref not in groups:
                    groups[ref] = (Item(rows=[], key=ref), [])
                    order.append(groups[ref])
                current = groups[ref]
            elif rec.get("transdate") or current is None:
                current = (Item(rows=[], key=""), [])
                order.append(current)
            current[0].rows.append(n)
            current[1].append(rec)
        return order

    def _doc_items(self, recs: List[Dict[str, Any]]) -> List[Tuple[Item, List[Dict[str, Any]]]]:
        """Documenten groeperen (IM2 A): alle rijen met hetzelfde nummer en dezelfde relatie, ook niet-opeenvolgend
        (een rij met nummer maar zonder relatie hoort bij het laatste document met dat nummer); een rij zonder
        nummer hoort bij het vorige document, tenzij de relatie of de datum anders is: dan begint een nieuw
        document dat bij het boeken het volgende nummer krijgt."""
        numcol, vccol = KEYS[self.kind], DOC_VC[self.kind][0]
        groups: Dict[Tuple[str, str], Tuple[Item, List[Dict[str, Any]]]] = {}
        last: Dict[str, Tuple[Item, List[Dict[str, Any]]]] = {}
        order: List[Tuple[Item, List[Dict[str, Any]]]] = []
        current: Optional[Tuple[Item, List[Dict[str, Any]]]] = None
        head: Dict[int, Tuple[str, str]] = {}            # id(item) -> relatie en datum van het document
        for n, rec in enumerate(recs, 1):
            number = _text(rec.get(numcol, ""))
            vc = _text(rec.get(vccol, "")).lower()
            td = _text(rec.get("transdate", ""))
            if number:
                g = groups.get((number, vc)) if vc else last.get(number)
                if g is None:
                    g = (Item(rows=[], key=number, raw=_raw(rec.get(numcol, ""))), [])
                    groups[(number, vc)] = g
                    order.append(g)
                last[number] = g
            else:
                hv, hd = head.get(id(current[0]), ("", "")) if current else ("", "")
                if current is None or (vc and hv and vc != hv) or (td and hd and td != hd):
                    current = None
                g = current or (Item(rows=[], key=""), [])
                if current is None:
                    order.append(g)
            current = g
            hv, hd = head.get(id(g[0]), ("", ""))
            head[id(g[0])] = (hv or vc, hd or td)
            g[0].rows.append(n)
            g[1].append(rec)
        return order

    # ---------------------------------------------------------------- waarden

    def _lookup(self, cur, ref: str, value: str) -> int:
        table, col = ref.split(".")
        cur.execute(f"SELECT id FROM {table} WHERE lower(trim({col})) = lower(trim(%s)) ORDER BY id", (value,))
        rows = cur.fetchall()
        if not rows:
            raise CellError("ref", value)
        return rows[0]["id"]

    def _value(self, cur, f: Field, v: Any) -> Any:
        k = f.kind
        if k in ("str", "accno"):
            return _text(v)
        if k == "curr":
            c = _text(v).upper()
            if c not in [x for x in _db.currencies(cur) if x]:
                raise CellError("curr", c)
            return c
        if k == "int":
            n = parse_number(v, self.nf)
            if n != n.to_integral_value():
                raise CellError("int", _text(v))
            return int(n)
        if k == "dec":
            return parse_number(v, self.nf)
        if k == "bool":
            return parse_bool(v)
        if k == "date":
            return parse_cell_date(v, self.df)
        if k == "taxes":
            return [t for t in re.split(r"[\s:;,]+", _text(v)) if t]
        if k == "ref":
            return self._lookup(cur, f.ref, _text(v))
        if k == "group":        # onbekende groep: aanmaken zoals SL (IM.pm import_item)
            name = _text(v)
            cur.execute("SELECT id FROM partsgroup WHERE lower(trim(partsgroup)) = lower(%s) ORDER BY id", (name,))
            row = cur.fetchone()
            if row:
                return row["id"]
            self.new_groups.append(name)
            return Parts(self.conn, self.config).save_group(name)
        raise ValueError("onbekende veldsoort")

    def _apply(self, cur, obj: Any, rec: Dict[str, Any], skip: Sequence[str] = ()) -> None:
        for name, v in rec.items():
            if name in skip:
                continue
            f = self.fields[name]
            current = getattr(obj, f.attr, None)
            if f.kind in ("ref", "group") and current and self._ref_name(cur, f, current) == _text(v).lower():
                continue          # zelfde naam: de huidige koppeling houden (ook als de naam dubbel bestaat)
            try:
                value = self._value(cur, f, v)
            except CellError as exc:
                raise cell_error(f, exc) from None
            if f.kind == "str" and isinstance(current, str) and current.strip() == value:
                continue          # alleen spaties eromheen verschillen: niets wijzigen
            setattr(obj, f.attr, value)

    def _ref_name(self, cur, f: Field, ref_id: int) -> str:
        table, col = (f.ref or "partsgroup.partsgroup").split(".")
        cur.execute(f"SELECT {col} AS v FROM {table} WHERE id = %s", (ref_id,))
        row = cur.fetchone()
        return (row["v"] or "").strip().lower() if row else ""

    # ---------------------------------------------------------------- per soort

    def _one(self, item: Item, recs: List[Dict[str, Any]]) -> None:
        with _db.cursor(self.conn) as cur:
            getattr(self, f"_do_{'vc' if self.kind in ('customer', 'vendor') else self.kind}")(cur, item, recs)

    def _find(self, cur, table: str, col: str, item: Item, what: str) -> Optional[int]:
        """Id op de sleutel: exact zoals in het bestand (ook met spaties), anders zonder spaties eromheen; staat het
        nummer er meer dan eens, dan is niet te kiezen welke bedoeld is (fout)."""
        rows: List[Dict[str, Any]] = []
        for sql, value in ((f"SELECT id FROM {table} WHERE {col} = %s", item.raw),
                           (f"SELECT id FROM {table} WHERE {col} = %s", item.key),
                           (f"SELECT id FROM {table} WHERE trim({col}) = %s", item.key)):
            if value:
                cur.execute(sql, (value,))
                rows = cur.fetchall()
                if rows:
                    break
        if len(rows) > 1:
            raise ValidationError(f"{what} {item.key} komt meer dan eens voor in de administratie.")
        return rows[0]["id"] if rows else None

    def _do_vc(self, cur, item: Item, recs) -> None:
        vc = self.kind
        rec = recs[0]
        lib = Contacts(self.conn, self.config)
        number = item.key
        vc_id = self._find(cur, vc, f"{vc}number", item, "Nummer") if number else None
        old = lib.get(vc, vc_id) if vc_id else None
        rel = lib.get(vc, vc_id) if vc_id else lib.new(vc)
        self._apply(cur, rel, rec)
        if old is None and not rel.name:
            raise ValidationError("Naam ontbreekt.")
        item.label = rel.name
        if old is not None:
            changes = sorted(changed_fields(old, rel))
            if not changes:
                item.status = "same"
                return
            item.status, item.changes = "update", changes
        else:
            item.status = "new"
        new_id = lib.save(rel)
        if not item.key:
            cur.execute(f"SELECT {vc}number AS n FROM {vc} WHERE id = %s", (new_id,))
            item.key = cur.fetchone()["n"]

    def _do_part(self, cur, item: Item, recs) -> None:
        rec = recs[0]
        lib = Parts(self.conn, self.config)
        pid = self._find(cur, "parts", "partnumber", item, "Artikelnummer") if item.key else None
        if pid:
            old = lib.get(pid)
            kind = "service" if self.kind == "service" else "part"
            if (old.kind == "service") != (kind == "service"):
                raise ValidationError("Dit nummer is een dienst; kies de andere soort import." if old.kind == "service"
                                      else "Dit nummer is een artikel; kies de andere soort import.")
            p = lib.get(pid)
        else:
            old, p = None, lib.new(self.kind)
            # standaardrekeningen uit defaults zoals SL (IM.pm import_item); past die niet bij de soort (dienst:
            # IC_income/IC_expense), dan de eerste rekening met de juiste koppeling
            d = _db.get_defaults(cur, "inventory_accno_id", "income_accno_id", "expense_accno_id")
            links = lib.links(self.kind)
            for side in (("income", "expense") if self.kind == "service" else ("inventory", "income", "expense")):
                ok = [a["accno"] for a in links[side]]
                accno = ""
                if d.get(f"{side}_accno_id"):
                    cur.execute("SELECT accno FROM chart WHERE id = %s", (int(d[f"{side}_accno_id"]),))
                    row = cur.fetchone()
                    accno = row["accno"] if row else ""
                setattr(p, f"{side}_accno", accno if accno in ok else (ok[0] if ok else ""))
        groups_before = len(self.new_groups)
        self._apply(cur, p, rec)
        if len(self.new_groups) > groups_before:
            item.warning = f"Nieuwe artikelgroep: {', '.join(self.new_groups[groups_before:])}"
        if old is None and not p.description:
            raise ValidationError("Omschrijving ontbreekt.")
        item.label = p.description
        if old is not None:
            names = [f.name for f in dc_fields(Part) if f.name in {x.attr for x in self.fields.values()}]
            changes = [n for n in names if _cmp(getattr(old, n)) != _cmp(getattr(p, n))]
            if not changes:
                item.status = "same"
                return
            item.status, item.changes = "update", changes
        else:
            item.status = "new"
        new_id = lib.save(p)
        if not item.key:
            cur.execute("SELECT partnumber AS n FROM parts WHERE id = %s", (new_id,))
            item.key = cur.fetchone()["n"]

    _do_service = _do_part

    def _do_partsgroup(self, cur, item: Item, recs) -> None:
        rec = recs[0]
        name = item.key
        if not name:
            raise ValidationError("Artikelgroep ontbreekt.")
        item.label = name
        cur.execute("SELECT id, partsgroup, code FROM partsgroup WHERE partsgroup = %s", (item.raw,))
        rows = cur.fetchall()
        if not rows:
            cur.execute("SELECT id, partsgroup, code FROM partsgroup WHERE lower(trim(partsgroup)) = lower(%s)",
                        (name,))
            rows = cur.fetchall()
        if len(rows) > 1:
            raise ValidationError(f"Artikelgroep {name} komt meer dan eens voor in de administratie.")
        row = rows[0] if rows else None
        code = _text(rec["code"]) if "code" in rec else None
        lib = Parts(self.conn, self.config)
        if row:
            if code is None or (row["code"] or "") == code:
                item.status = "same"
                return
            item.status, item.changes = "update", ["code"]
            lib.save_group(row["partsgroup"], code, row["id"])
        else:
            item.status = "new"
            lib.save_group(name, code or "")

    def _do_coa(self, cur, item: Item, recs) -> None:
        rec = recs[0]
        if not item.key:
            raise ValidationError("Rekeningnummer ontbreekt.")
        lib = Admin(self.conn, self.config)
        cid = self._find(cur, "chart", "accno", item, "Rekening")
        old = lib.get_account(cid).account if cid else None
        acc = lib.get_account(cid).account if cid else Account(accno=item.key, charttype="A", category="A")
        self._apply(cur, acc, rec, skip=("link",))
        if "link" in rec:
            acc.link = set(filter(None, re.split(r"[:\s]+", _text(rec["link"]))))
        acc.charttype = (acc.charttype or "A").upper()
        acc.category = (acc.category or "").upper()
        item.label = acc.description
        gifi = Admin.clean_gifi(acc.gifi_accno or "")[0]
        if gifi and (old is None or gifi != (old.gifi_accno or "")):
            cur.execute("SELECT 1 FROM gifi WHERE accno = %s", (gifi,))
            if not cur.fetchone():
                # GF4: zoals op het rekeningscherm een waarschuwing, geen fout (de GIFI-import kan ook later)
                item.warning = f"GIFI-code {gifi} staat niet in de GIFI-indeling."
        if old is not None:
            names = ("description", "charttype", "category", "link", "gifi_accno", "contra", "closed",
                     "bankname", "iban", "bic")
            changes = [n for n in names if _cmp(getattr(old, n)) != _cmp(getattr(acc, n))]
            if not changes:
                item.status = "same"
                return
            item.status, item.changes = "update", changes
        else:
            item.status = "new"
        lib.save_account(acc)

    def _do_gifi(self, cur, item: Item, recs) -> None:
        """GIFI-code (GF5): nieuw of omschrijving bijwerken; een code wijzigen kan alleen op het scherm."""
        rec = recs[0]
        code = Admin.clean_gifi(item.key)[0]
        if not code:
            raise ValidationError("Code ontbreekt.")
        item.key = code
        if isinstance(rec.get("accno"), (int, float)) and not isinstance(rec.get("accno"), bool):
            item.warning = "De code staat in het bestand als getal; een voorloopnul (020) kan verdwenen zijn."
        description = Admin.clean_gifi("", _text(rec["description"]))[1] if "description" in rec else None
        cur.execute("SELECT accno, description FROM gifi WHERE accno = %s", (code,))
        row = cur.fetchone()
        item.label = description if description is not None else (row["description"] if row else "")
        lib = Admin(self.conn, self.config)
        if row:
            if description is None or Admin.clean_gifi("", row["description"])[1] == description:
                item.status = "same"
                return
            item.status, item.changes = "update", ["description"]
            lib.save_gifi(code, description, code)
        else:
            item.status = "new"
            lib.save_gifi(code, description or "")

    def _do_gl(self, cur, item: Item, recs) -> None:
        head = recs[0]
        t = GLTransaction(transdate=date.today(), lines=[])
        if "transdate" not in head:
            raise ValidationError("Datum ontbreekt op de eerste regel.")
        values: Dict[str, Any] = {}
        for name in ("transdate", "description", "notes", "department", "curr", "exchangerate"):
            if name in head:
                try:
                    values[name] = self._value(cur, self.fields[name], head[name])
                except CellError as exc:
                    raise cell_error(self.fields[name], exc) from None
        t.transdate = values["transdate"]
        t.reference = item.key or None
        t.description = values.get("description")
        t.notes = values.get("notes")
        t.department_id = values.get("department")
        t.currency = values.get("curr")
        if "exchangerate" in values:
            t.exchangerate = values["exchangerate"]
        for n, rec in zip(item.rows, recs):
            line = JournalLine(accno="")
            for name in ("accno", "debit", "credit", "source", "memo", "project"):
                if name in rec:
                    f = self.fields[name]
                    try:
                        setattr(line, f.attr, self._value(cur, f, rec[name]))
                    except CellError as exc:
                        raise cell_error(f, exc) from None
            if not line.accno and not (line.debit or line.credit):
                continue
            if not line.accno:
                raise ValidationError(f"Op rij {n} ontbreekt de rekening.")
            t.lines.append(line)
        item.label = t.description or ""
        if item.key:
            cur.execute("SELECT 1 FROM gl WHERE reference = %s LIMIT 1", (item.key,))
            if cur.fetchone():
                item.warning = "Deze referentie bestaat al in het memoriaal."
                item.selected = False
        item.status = "new"
        trans_id = GeneralLedger(self.conn, self.config).post_transaction(t)
        if not item.key:
            cur.execute("SELECT reference FROM gl WHERE id = %s", (trans_id,))
            item.key = cur.fetchone()["reference"] or ""

    # ---------------------------------------------------------------- documenten (IM1–IM6)

    def _head(self, cur, recs, names: Sequence[str]) -> Dict[str, Any]:
        """Kopvelden: per kolom de eerste niet-lege cel van het document (de kop mag dus op de eerste rij staan)."""
        out: Dict[str, Any] = {}
        for name in names:
            if name not in self.fields:
                continue
            for rec in recs:
                if name in rec:
                    f = self.fields[name]
                    try:
                        out[name] = self._value(cur, f, rec[name])
                    except CellError as exc:
                        raise cell_error(f, exc) from None
                    break
        return out

    def _doc_vc(self, cur, vc: str, number: str) -> Tuple[str, int, str]:
        vccol = f"{vc}number"
        if not number:
            raise ValidationError("Klantnummer ontbreekt." if vc == "customer" else "Leveranciersnummer ontbreekt.")
        rows: List[Dict[str, Any]] = []
        for sql in (f"SELECT id, name FROM {vc} WHERE {vccol} = %s", f"SELECT id, name FROM {vc} WHERE trim({vccol}) = %s"):
            cur.execute(sql, (number,))
            rows = cur.fetchall()
            if rows:
                break
        if not rows:        # IM6: niets automatisch aanmaken
            raise ValidationError(f"Klant {number} bestaat niet." if vc == "customer"
                                  else f"Leverancier {number} bestaat niet.")
        if len(rows) > 1:
            raise ValidationError(f"Relatienummer {number} komt meer dan eens voor in de administratie.")
        return vc, rows[0]["id"], rows[0]["name"] or ""

    def _doc_part(self, cur, row: int, number: str) -> int:
        rows: List[Dict[str, Any]] = []
        for sql in ("SELECT id FROM parts WHERE partnumber = %s", "SELECT id FROM parts WHERE trim(partnumber) = %s"):
            cur.execute(sql, (number,))
            rows = cur.fetchall()
            if rows:
                break
        if not rows:
            raise ValidationError(f"Rij {row}: artikel {number} bestaat niet.")
        if len(rows) > 1:
            raise ValidationError(f"Rij {row}: artikelnummer {number} komt meer dan eens voor.")
        return rows[0]["id"]

    def _doc_lines(self, cur, item: Item, recs, vc: str, rate: Optional[Decimal], vc_discount: Decimal
                   ) -> List[Dict[str, Any]]:
        """Regels: artikel verplicht; omschrijving, eenheid, prijs en korting zoals het scherm ze voorinvult."""
        from .invoice import InvoiceLedger
        il = InvoiceLedger(self.conn, self.config)
        out = []
        for n, rec in zip(item.rows, recs):
            vals = {}
            for name in ("partnumber", "description", "qty", "sellprice", "discount", "unit", "projectnumber"):
                if name in rec:
                    f = self.fields[name]
                    try:
                        vals[name] = self._value(cur, f, rec[name])
                    except CellError as exc:
                        raise cell_error(f, exc) from None
            if not vals.get("partnumber"):
                if any(k in vals for k in ("qty", "sellprice", "discount", "description")):
                    raise ValidationError(f"Rij {n}: artikelnummer ontbreekt.")
                continue           # rij met alleen kopgegevens
            pid = self._doc_part(cur, n, vals["partnumber"])
            d = il.item_defaults(vc, pid, rate)
            out.append({"parts_id": pid, "qty": vals.get("qty", Decimal(1)),
                        "sellprice": vals["sellprice"] if "sellprice" in vals else d["price"],
                        "discount": vals["discount"] if "discount" in vals else vc_discount,
                        "description": vals.get("description") or d["description"] or "",
                        "unit": vals.get("unit") or (d["unit"] or "")[:5], "project_id": vals.get("projectnumber"),
                        "cost": d["cost"] if vc == "customer" else None})
        if not out:
            raise ValidationError("Geen regels met een artikel.")
        return out

    def _doc_common(self, cur, item: Item, recs, extra: Sequence[str]):
        """Relatie, kop en regels van een factuur of order, met de standaardwaarden van de relatie (AA::get_name)."""
        from .arap import ArapLedger
        vccol, vc = DOC_VC[self.kind]
        vc, vc_id, name = self._doc_vc(cur, vc, _text(next((r[vccol] for r in recs if vccol in r), "")))
        item.label = name
        head = self._head(cur, recs, ["transdate", "ordnumber", "ponumber", "curr", "exchangerate", "taxincluded",
                                      "notes", "intnotes", "employeenumber", "department"] + list(extra))
        if "transdate" not in head:
            raise ValidationError("Datum ontbreekt.")
        td = head["transdate"]
        arap = ArapLedger(self.conn, self.config)
        d = arap.vc_defaults(vc, vc_id, td)
        defcur = _db.default_currency(cur)
        currency = head.get("curr") or d["currency"] or defcur
        rate = Decimal(1)
        if currency != defcur:
            rate = head.get("exchangerate") or GeneralLedger(self.conn, self.config).exchange_rate(currency, td)
            if not rate:
                raise ValidationError(f"Koers ontbreekt voor {currency} op {td.isoformat()}.")
        vc_discount = to_decimal(d.get("discount") or 0) * 100        # VW7
        lines = self._doc_lines(cur, item, recs, vc, rate if currency != defcur else None, vc_discount)
        return vc, vc_id, head, d, currency, rate, lines

    def _doc_exists(self, cur, item: Item) -> None:
        """IM3 A: een bestaand nummer is een fout (nooit vervangen); per soort document."""
        if not item.key:
            return
        if self.kind == "sales_invoice":
            cur.execute("SELECT 1 FROM ar WHERE invnumber = %s LIMIT 1", (item.key,))
            if cur.fetchone():
                raise ValidationError(f"Factuurnummer {item.key} bestaat al.")
            return
        col = "customer_id" if self.kind == "sales_order" else "vendor_id"
        cur.execute(f"SELECT 1 FROM oe WHERE ordnumber = %s AND {col} > 0 AND NOT quotation LIMIT 1", (item.key,))
        if cur.fetchone():
            raise ValidationError(f"Ordernummer {item.key} bestaat al.")

    def _do_sales_invoice(self, cur, item: Item, recs) -> None:
        from .arap import ArapLedger, ArapPayment
        from .invoice import Invoice, InvoiceLedger, InvoiceLine
        vc, vc_id, head, d, currency, rate, lines = self._doc_common(
            cur, item, recs, ["duedate", "invoicedescription", "AR", "dcn", "paid_1", "datepaid_1", "AR_paid_1",
                              "source_1", "memo_1"])
        self._doc_exists(cur, item)
        td = head["transdate"]
        acc = ArapLedger(self.conn, self.config).accounts(vc)
        bank = d["payment_accno"] or (acc["AR_paid"][0]["accno"] if acc["AR_paid"] else None)
        terms = int(d["terms"] or 0)
        pays = []
        if head.get("paid_1"):
            datepaid = head.get("datepaid_1") or td
            prate = None
            if currency != _db.default_currency(cur):
                prate = rate if datepaid == td else GeneralLedger(self.conn, self.config).exchange_rate(
                    currency, datepaid)
                if not prate:
                    raise ValidationError(f"Koers ontbreekt voor {currency} op {datepaid.isoformat()}.")
            pays.append(ArapPayment(accno=head.get("AR_paid_1") or bank or "", amount=head["paid_1"],
                                    datepaid=datepaid, source=head.get("source_1"), memo=head.get("memo_1"),
                                    exchangerate=prate))
        inv = Invoice(
            vc=vc, vc_id=vc_id, transdate=td,
            arap_accno=head.get("AR") or d["arap_accno"] or (acc["AR"][0]["accno"] if acc["AR"] else ""),
            lines=[InvoiceLine(parts_id=l["parts_id"], qty=l["qty"], sellprice=l["sellprice"], discount=l["discount"],
                               description=l["description"], unit=l["unit"], project_id=l["project_id"],
                               cost=l["cost"], vendor_id=0) for l in lines],
            invnumber=item.key or None, ordnumber=head.get("ordnumber"), ponumber=head.get("ponumber"),
            duedate=head.get("duedate") or td + timedelta(days=terms), terms=terms,
            description=head.get("invoicedescription"), notes=head.get("notes"), intnotes=head.get("intnotes"),
            currency=currency, exchangerate=rate,
            taxincluded=head["taxincluded"] if "taxincluded" in head else bool(d["taxincluded"]),
            department_id=head.get("department"),
            employee_id=head.get("employeenumber") or d["employee_id"], language_code=d["language_code"],
            payments=pays, cashdiscount=to_decimal(d["cashdiscount"] or 0) * 100,
            discountterms=int(d["discountterms"] or 0), bank_accno=bank,
            paymentmethod_id=d["paymentmethod_id"], dcn=head.get("dcn"))
        lib = InvoiceLedger(self.conn, self.config)
        calc = lib.calculate(inv)
        item.amounts = (currency, calc.fxtotal - calc.fxtax, calc.fxtax, calc.fxtotal)
        item.status = "new"
        trans_id = lib.post_invoice(inv)
        if not item.key:
            cur.execute("SELECT invnumber FROM ar WHERE id = %s", (trans_id,))
            item.key = cur.fetchone()["invnumber"] or ""

    def _do_order(self, cur, item: Item, recs) -> None:
        from .order import Order, OrderLedger, OrderLine
        vc, vc_id, head, d, currency, rate, lines = self._doc_common(cur, item, recs,
                                                                     ["reqdate", "orderdescription"])
        self._doc_exists(cur, item)
        # termijn en leverdatum zoals IM.pm import_order: leverdatum = orderdatum + termijn van de relatie, of de
        # termijn uit een opgegeven leverdatum; de regels krijgen de leverdatum zoals op het orderscherm
        td = head["transdate"]
        if head.get("reqdate"):
            reqdate, terms = head["reqdate"], (head["reqdate"] - td).days
        else:
            terms = int(d["terms"] or 0)
            reqdate = td + timedelta(days=max(terms, 0))
        o = Order(
            type=self.kind, vc_id=vc_id, transdate=head["transdate"],
            lines=[OrderLine(parts_id=l["parts_id"], qty=l["qty"], sellprice=l["sellprice"], discount=l["discount"],
                             description=l["description"], unit=l["unit"], project_id=l["project_id"],
                             cost=l["cost"] if l["cost"] is not None else Decimal(0), vendor_id=0,
                             reqdate=reqdate) for l in lines],
            ordnumber=item.key or "", quonumber="", ponumber=head.get("ponumber") or "",
            reqdate=reqdate, terms=terms, description=head.get("orderdescription") or "",
            notes=head.get("notes") or "", intnotes=head.get("intnotes") or "", currency=currency, exchangerate=rate,
            taxincluded=head["taxincluded"] if "taxincluded" in head else bool(d["taxincluded"]),
            department_id=head.get("department"), employee_id=head.get("employeenumber") or d["employee_id"],
            language_code=d["language_code"] or "")
        lib = OrderLedger(self.conn, self.config)
        calc = lib.calculate(o)
        tax = sum(calc.taxes.values(), Decimal(0))
        item.amounts = (currency, calc.amount - tax, tax, calc.amount)
        item.status = "new"
        oe_id = lib.save_order(o)
        if not item.key:
            cur.execute("SELECT ordnumber FROM oe WHERE id = %s", (oe_id,))
            item.key = cur.fetchone()["ordnumber"] or ""

    _do_sales_order = _do_order
    _do_purchase_order = _do_order

    # ---------------------------------------------------------------- betalingen (IM7)

    def _pay_find(self, cur, sides: Sequence[str], col: str, value: str, vc_ids: Dict[str, Optional[int]]
                  ) -> List[Dict[str, Any]]:
        hits = []
        for vc in sides:
            table = "ar" if vc == "customer" else "ap"
            sql = f"SELECT id, {vc}_id AS vc_id, invnumber FROM {table} WHERE {col} = %s"
            params: List[Any] = [value]
            if vc_ids.get(vc):
                sql += f" AND {vc}_id = %s"
                params.append(vc_ids[vc])
            cur.execute(sql + " ORDER BY id", params)
            hits += [{**r, "vc": vc} for r in cur.fetchall()]
        return hits

    def _do_payment(self, cur, item: Item, recs) -> None:
        """Betaling koppelen op factuurnummer (+ relatie), anders betalingskenmerk; nooit alleen op bedrag (IM7 A).
        Boeken via ``Payments.post_payment`` zoals het betaalscherm, alleen op een openstaande post en niet meer
        dan wat openstaat."""
        from .arap import ArapLedger
        from .payments import Payment, PaymentLine, Payments
        v = self._head(cur, recs, [f.name for f in FIELDS["payment"]])
        if "datepaid" not in v:
            raise ValidationError("Betaaldatum ontbreekt.")
        if "customernumber" in v and "vendornumber" in v:
            raise ValidationError("Vul een klantnummer of een leveranciersnummer in, niet allebei.")
        amounts = [(k, v[k]) for k in ("amount", "credit", "debit") if v.get(k)]
        if len(amounts) != 1:
            raise ValidationError("Vul precies één bedrag in (Bedrag, Credit of Debet).")
        what, amount = amounts[0]
        if amount <= 0:
            raise ValidationError("Het bedrag moet groter dan nul zijn.")
        sides: List[str] = ["customer", "vendor"]
        vc_ids: Dict[str, Optional[int]] = {}
        for vc in ("customer", "vendor"):
            if f"{vc}number" in v:
                vc_ids[vc] = self._doc_vc(cur, vc, v[f"{vc}number"])[1]
                sides = [vc]
        if what == "credit":
            sides = [s for s in sides if s == "customer"]
        elif what == "debit":
            sides = [s for s in sides if s == "vendor"]
        if not sides:
            raise ValidationError("Credit is een ontvangst van een klant, Debet een betaling aan een leverancier.")
        inv, dcn = v.get("invnumber", ""), v.get("dcn", "")
        if not inv and not dcn:
            raise ValidationError("Factuurnummer of betalingskenmerk ontbreekt.")
        hits = self._pay_find(cur, sides, "invnumber", inv, vc_ids) if inv else []
        ref = inv
        if not hits and dcn:
            hits, ref = self._pay_find(cur, sides, "dcn", dcn, vc_ids), dcn
        if not hits:
            number = v.get("customernumber") or v.get("vendornumber")
            if number:
                raise ValidationError(f"Geen factuur gevonden voor {inv or dcn} bij {number}.")
            raise ValidationError(f"Geen factuur gevonden voor {inv or dcn}.")
        if len(hits) > 1:
            raise ValidationError(f"Meer dan één factuur gevonden voor {ref}; vul het klant- of leveranciersnummer in.")
        hit = hits[0]
        vc, vc_id, trans_id = hit["vc"], hit["vc_id"], hit["id"]
        item.key = hit["invnumber"] or ref
        datepaid = v["datepaid"]
        lib = Payments(self.conn, self.config)
        open_ = [o for o in lib.open_invoices(vc, vc_id, datepaid=datepaid) if o.id == trans_id]
        cur.execute(f"SELECT name FROM {vc} WHERE id = %s", (vc_id,))
        item.label = cur.fetchone()["name"] or ""
        if not open_:
            raise ValidationError(f"Factuur {item.key} staat niet open.")
        o = open_[0]
        currency = o.currency or _db.default_currency(cur)
        item.amounts = (currency, amount, o.due)
        if amount > o.due:
            shown, due = (format_amount(x, self.nf, 2) for x in (amount, o.due))
            raise ValidationError(f"Het bedrag {shown} is meer dan wat openstaat ({due}).")
        rate = None
        if currency != _db.default_currency(cur):
            rate = v.get("exchangerate") or GeneralLedger(self.conn, self.config).exchange_rate(currency, datepaid)
            if not rate:
                raise ValidationError(f"Koers ontbreekt voor {currency} op {datepaid.isoformat()}.")
        ARAP = "AR" if vc == "customer" else "AP"
        account = v.get("accno") or ArapLedger(self.conn, self.config).vc_defaults(vc, vc_id)["payment_accno"]
        if not account:
            paid = ArapLedger(self.conn, self.config).accounts(vc)[f"{ARAP}_paid"]
            account = paid[0]["accno"] if len(paid) == 1 else ""
        if not account:
            raise ValidationError("Betaalrekening ontbreekt.")
        item.status = "new"
        lib.post_payment(Payment(vc=vc, vc_id=vc_id, datepaid=datepaid, payment_accno=account,
                                 lines=[PaymentLine(trans_id, amount, Decimal(0), o.due)], amount=amount,
                                 currency=currency, exchangerate=rate, source=v.get("source") or None,
                                 memo=v.get("memo")))

    # ---------------------------------------------------------------- proefrun en import

    def _walk(self, mapping: Sequence[str], rows: Sequence[Sequence[Any]],
              selected: Optional[Callable[[Item], bool]], commit: bool) -> Result:
        recs = self._records(mapping, rows)
        pairs = self._items(recs)
        keys: Dict[str, int] = {}
        res = Result(items=[p[0] for p in pairs])
        try:
            with self.conn.transaction():
                for item, rs in pairs:
                    if self.kind not in ("gl", "payment") and item.key:
                        k = item.key.lower() if self.kind == "partsgroup" else item.key
                        if k in keys:
                            item.status, item.error = "error", f"Komt ook voor in rij {keys[k]}."
                            item.selected = False
                            continue
                        keys[k] = item.rows[0]
                    if selected is not None and not selected(item):
                        item.selected = False
                        continue
                    try:
                        with self.conn.transaction():
                            self._one(item, rs)
                    except (LedgerError, ValueError, psycopg.Error) as exc:
                        item.status, item.error, item.selected = "error", _message(exc), False
                        if commit:
                            raise ImportFailed(item) from exc
                        continue
                    if item.status == "same":
                        item.selected = False
                    elif item.status == "new":
                        res.added += 1
                    elif item.status == "update":
                        res.updated += 1
                if not commit:
                    raise _Rollback
        except _Rollback:
            pass
        return res

    def preview(self, mapping: Sequence[str], rows: Sequence[Sequence[Any]]) -> Result:
        """Proefrun: wat er zou gebeuren (niets wordt opgeslagen)."""
        if not any(mapping):
            raise ValidationError("Koppel minstens één kolom.")
        key = KEYS[self.kind]
        if self.kind in ("partsgroup", "coa", "gifi") and key not in mapping:
            raise ValidationError("De kolom met de sleutel ontbreekt: " + self.fields[key].label + ".")
        if self.kind == "payment":
            if "datepaid" not in mapping:
                raise ValidationError(f"De kolom {self.fields['datepaid'].label} ontbreekt.")
            if not {"amount", "credit", "debit"} & set(mapping):
                raise ValidationError("Koppel een kolom met het bedrag (Bedrag, Credit of Debet).")
            if not {"invnumber", "dcn"} & set(mapping):
                raise ValidationError("Koppel een kolom met het factuurnummer of het betalingskenmerk.")
        if self.kind in DOC_KINDS:
            for name in (DOC_VC[self.kind][0], "transdate", "partnumber"):
                if name not in mapping:
                    label = self.fields[name].label
                    raise ValidationError(f"De kolom {label} ontbreekt.")
        return self._walk(mapping, rows, None, commit=False)

    def run(self, mapping: Sequence[str], rows: Sequence[Sequence[Any]], chosen: Sequence[int]) -> Result:
        """Importeer de items waarvan de eerste rij in ``chosen`` staat; alles of niets."""
        want = set(chosen)
        return self._walk(mapping, rows, lambda it: it.rows[0] in want, commit=True)


class ImportFailed(LedgerError):
    def __init__(self, item: Item):
        super().__init__(f"Rij {item.rows[0]}: {item.error}")
        self.item = item


def _raw(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = "" if v is None else str(v)
    return s[1:] if len(s) > 1 and s[0] == "'" and s[1] in "=+-@" else s


def _message(exc: Exception) -> str:
    return str(exc).strip() or exc.__class__.__name__


def _cmp(v: Any) -> Any:
    if isinstance(v, (set, list, tuple)):
        return sorted(str(x) for x in v)
    if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
        return to_decimal(v).normalize()
    if v is None:
        return ""
    return v


# ------------------------------------------------------------------ export (D20, keuze 11)

def export_rows(conn: psycopg.Connection, config, kind: str) -> Tuple[List[str], List[List[Any]]]:
    """Kop (SL-veldnamen) en rijen in precies het importformaat."""
    if kind == "gl":
        raise ValidationError("Het memoriaal exporteert u via de lijst Boekingen.")
    flds = FIELDS[kind]
    header = [f.name for f in flds]
    rows: List[List[Any]] = []
    with _db.cursor(conn) as cur:
        names: Dict[str, Dict[int, str]] = {}
        for f in flds:
            if f.kind in ("ref", "group"):
                table, col = (f.ref or "partsgroup.partsgroup").split(".")
                cur.execute(f"SELECT id, {col} AS v FROM {table}")
                names[f.name] = {r["id"]: r["v"] or "" for r in cur.fetchall()}
        if kind in ("customer", "vendor"):
            cur.execute(f"SELECT id FROM {kind} ORDER BY {kind}number, id")
            ids = [r["id"] for r in cur.fetchall()]
            lib = Contacts(conn, config)
            objs = [lib.get(kind, i) for i in ids]
        elif kind in ("part", "service"):
            cur.execute("SELECT id FROM parts ORDER BY partnumber, id")
            lib = Parts(conn, config)
            objs = [p for p in (lib.get(r["id"]) for r in cur.fetchall()) if p.kind == kind]
        elif kind == "partsgroup":
            cur.execute("SELECT partsgroup, code FROM partsgroup ORDER BY partsgroup")
            return header, [[r["partsgroup"] or "", r["code"] or ""] for r in cur.fetchall()]
        elif kind == "gifi":
            cur.execute("SELECT accno, description FROM gifi ORDER BY accno")
            return header, [[r["accno"] or "", r["description"] or ""] for r in cur.fetchall()]
        else:
            cur.execute("SELECT id FROM chart ORDER BY accno")
            lib = Admin(conn, config)
            objs = [lib.get_account(r["id"]).account for r in cur.fetchall()]
    for o in objs:
        row = []
        for f in flds:
            v = getattr(o, f.attr, "")
            if f.kind in ("ref", "group"):
                v = names[f.name].get(v, "") if v else ""
            elif f.kind == "taxes":
                v = " ".join(v or [])
            elif f.name == "link":
                v = o.link_string
            elif f.kind == "bool":
                v = "1" if v else "0"
            elif v is None:
                v = ""
            row.append(v)
        rows.append(row)
    return header, rows


def _cell_out(v: Any, nf: str, df: str) -> Any:
    from .amounts import format_amount
    from .dates import format_date
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (Decimal, float)) or (isinstance(v, int) and not isinstance(v, bool)):
        d = to_decimal(v).normalize()
        places = max(0, -d.as_tuple().exponent)
        comma = nf in ("1.000,00", "1000,00")
        return format_amount(d, "1000,00" if comma else "1000.00", places, "0")
    if isinstance(v, date):
        return format_date(v, df)
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s      # geen formules (zoals tables.safe_cell)


def export_file(conn: psycopg.Connection, config, kind: str, fmt: str = "csv", numberformat: str = "1,000.00",
                dateformat: str = "yyyy-mm-dd") -> bytes:
    """De export als CSV (``;``, UTF-8 met BOM) of xlsx, getallen en datums in de notatie van de gebruiker."""
    header, rows = export_rows(conn, config, kind)
    cells = [[_cell_out(v, numberformat, dateformat) for v in r] for r in rows]
    if fmt == "xlsx":
        from openpyxl import Workbook
        from openpyxl.styles import Font
        wb = Workbook()
        ws = wb.active
        ws.title = kind
        ws.append(header)
        for c in ws[1]:
            c.font = Font(bold=True)
        for r in cells:
            ws.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()
    out = io.StringIO()
    w = csv.writer(out, delimiter=";", lineterminator="\r\n")
    w.writerow(header)
    w.writerows(cells)
    return ("﻿" + out.getvalue()).encode("utf-8")
