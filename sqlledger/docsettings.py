"""Documentinstellingen per administratie (fase 4 stap 3, DI1–DI4).

Wat de beheerder zonder HTML-kennis instelt voor facturen, orders, pakbonnen enz.: btw-nummer, welke
bankrekening op het document staat, teksten per documenttaal (retouradres, voet, slottekst, opmerking onder
de totalen), kolommen per groep documenten met eigen namen, en een gewichtsoverzicht.

Opslag in de tabel ``x_doc_setting`` (migratie 8) in de database van de administratie: één regel per
instelling, alleen wat afwijkt van de standaard. Zonder regels is elk document precies gelijk aan de
standaardset; dat is getoetst (dev/doc_html_dump.py). Niet in ``defaults``: SQL-Ledger (3.2.12 en 4.x)
wist bij opslaan van Systeem > Standaarden alle sleutels die het niet kent.

De teksten zijn platte tekst met lichte opmaak (``**vet**``, ``*cursief*``, ``[tekst](https://…)``); het
omzetten naar HTML gebeurt bij het maken van het document (sqlledger_web.documents), altijd ge-escaped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import psycopg

from . import _db
from .docstyle import DocStyle
from .errors import ValidationError
from .extensions import ensure_schema

#: documenttalen (zoals company.doc_language en de standaardsjablonen)
LANGUAGES = ("nl", "en", "de", "fr")

#: documentsoorten (sleutels zoals sqlledger_web.doctypes.KINDS)
KINDS = ("invoice", "credit_invoice", "vendor_invoice", "debit_invoice", "packing_list", "pick_list",
         "sales_order", "purchase_order", "sales_quotation", "request_quotation", "ar_transaction",
         "credit_note", "ap_transaction", "debit_note", "reminder1", "reminder2", "reminder3", "statement",
         "receipt", "check")
#: brieven per klant (stap 4a) en betalingsspecificaties (stap 4b): geen regelkolommen, wel een inleidende tekst
LETTER_KINDS = ("reminder1", "reminder2", "reminder3", "statement", "receipt", "check")

#: groepen documenten met dezelfde kolommen
GROUPS: Dict[str, Tuple[str, ...]] = {
    "sales_invoice": ("invoice", "credit_invoice"),
    "purchase_invoice": ("vendor_invoice", "debit_invoice"),
    "order": ("sales_order", "purchase_order", "sales_quotation", "request_quotation"),
    "packing_list": ("packing_list",),
    "pick_list": ("pick_list",),
}
GROUP_OF = {k: g for g, kinds in GROUPS.items() for k in kinds}

#: weergave van een veld per regel
OFF, NOTE, AUTO, COLUMN = "off", "note", "auto", "column"
MODES = (OFF, NOTE, AUTO, COLUMN)


@dataclass(frozen=True)
class Field:
    label: str                  # kolomkop (Nederlands, vertaald via de documentteksten)
    modes: Tuple[str, ...]      # toegestane weergaven
    note_label: str = ""        # voorvoegsel als regel onder de omschrijving (standaard)


ALL = (OFF, NOTE, AUTO, COLUMN)
#: velden per regel (volgorde = kolomvolgorde op facturen en orders)
FIELDS: Dict[str, Field] = {
    "number": Field("Artikelnr.", (COLUMN, OFF)),
    "barcode": Field("EAN", ALL),
    "hscode": Field("Goederencode", ALL),
    "description": Field("Omschrijving", (COLUMN,)),
    "countryorigin": Field("Land van herkomst", ALL),
    "projectnumber": Field("Project", ALL),
    "deliverydate": Field("Leverdatum", ALL, note_label="Leverdatum"),
    "serialnumber": Field("Serienummer", ALL),
    "package": Field("Verpakking", ALL),
    "bin": Field("Locatie", ALL),
    "qty": Field("Aantal", (COLUMN,)),
    "unit": Field("Eenheid", (COLUMN, OFF)),
    "sellprice": Field("Prijs", (COLUMN,)),
    "discount": Field("Korting", (AUTO, COLUMN, OFF)),
    "netprice": Field("Nettoprijs", (OFF, AUTO, COLUMN)),
    "grossweight": Field("Gewicht", ALL),
    "netweight": Field("Nettogewicht", ALL),
    "linetotal": Field("Bedrag", (COLUMN,)),
}
#: velden en kolomvolgorde per groep (pakbon en picklijst houden de volgorde van hun standaardsjabloon)
_PRICED = tuple(FIELDS)
GROUP_FIELDS: Dict[str, Tuple[str, ...]] = {
    "sales_invoice": _PRICED, "purchase_invoice": _PRICED, "order": _PRICED,
    "packing_list": ("number", "barcode", "hscode", "description", "countryorigin", "projectnumber", "deliverydate",
                     "serialnumber", "qty", "unit", "package", "bin", "grossweight", "netweight"),
    "pick_list": ("bin", "number", "barcode", "hscode", "description", "countryorigin", "projectnumber",
                  "deliverydate", "serialnumber", "package", "qty", "unit", "grossweight", "netweight"),
}

#: standaard = de standaardset zoals die was vóór de instellingen
_BASE = {"number": COLUMN, "description": COLUMN, "qty": COLUMN, "unit": COLUMN, "sellprice": COLUMN,
         "linetotal": COLUMN, "discount": AUTO, "deliverydate": NOTE, "serialnumber": NOTE}
DEFAULT_MODES: Dict[str, Dict[str, str]] = {
    "sales_invoice": _BASE, "purchase_invoice": _BASE, "order": _BASE,
    "packing_list": {"number": COLUMN, "description": COLUMN, "qty": COLUMN, "unit": COLUMN, "serialnumber": NOTE,
                     "package": AUTO, "grossweight": AUTO},
    "pick_list": {"number": COLUMN, "description": COLUMN, "qty": COLUMN, "unit": COLUMN, "bin": AUTO},
}

#: vrije teksten: naam -> geldt per documentsoort
TEXTS = ("return_line", "footer")
KIND_TEXTS = ("closing", "note", "intro")      # intro alleen bij LETTER_KINDS
MAX_TEXT = 2000
MAX_LABEL = 60
MAX_TAXNUMBER = 40

BANK_BOOKING, BANK_FIXED = "booking", "fixed"


def default_mode(group: str, name: str) -> str:
    return DEFAULT_MODES[group].get(name, OFF)


@dataclass
class Column:
    mode: str
    labels: Dict[str, str] = field(default_factory=dict)      # taal -> eigen naam


@dataclass
class DocSettings:
    taxnumber: str = ""
    bank_mode: str = BANK_BOOKING
    bank_accno: str = ""
    bank_name: bool = False            # banknaam tonen bij IBAN
    payinfo: bool = True               # betaalzin onder de factuur
    return_line: bool = True           # retouradresregel boven het adres
    texts: Dict[str, Dict[str, str]] = field(default_factory=dict)        # taal -> naam -> tekst
    columns: Dict[str, Dict[str, Column]] = field(default_factory=dict)   # groep -> veld -> kolom
    weights: Dict[str, bool] = field(default_factory=dict)                # groep -> gewichtsoverzicht
    #: huisstijl (``style.*``, eigen scherm Beheer > Huisstijl; ``docstyle``): alleen gelezen, niet via ``save``
    style: DocStyle = field(default_factory=DocStyle)

    # ------------------------------------------------------------ lezen

    def text(self, lang: str, name: str, kind: Optional[str] = None) -> str:
        key = f"{name}:{kind}" if kind else name
        return (self.texts.get(lang) or {}).get(key, "")

    def column(self, group: str, name: str) -> Column:
        c = (self.columns.get(group) or {}).get(name)
        return c if c is not None else Column(default_mode(group, name))

    def is_default(self) -> bool:
        return not self.to_rows()

    # ------------------------------------------------------------ opslag

    def to_rows(self) -> Dict[str, str]:
        """Alleen wat afwijkt van de standaard, als sleutel -> tekst."""
        out: Dict[str, str] = {}
        if self.taxnumber:
            out["taxnumber"] = self.taxnumber
        bank = {}
        if self.bank_mode != BANK_BOOKING:
            bank["mode"], bank["accno"] = self.bank_mode, self.bank_accno
        if self.bank_name:
            bank["name"] = True
        if not self.payinfo:
            bank["payinfo"] = False
        if bank:
            out["bank"] = json.dumps(bank, sort_keys=True)
        if not self.return_line:
            out["return_line"] = "0"
        for lang in LANGUAGES:
            for key, text in sorted((self.texts.get(lang) or {}).items()):
                if text:
                    out[f"text:{lang}:{key}"] = text
        for group in GROUPS:
            cols = {}
            for name in GROUP_FIELDS[group]:
                c = (self.columns.get(group) or {}).get(name)
                if c is None:
                    continue
                labels = {k: v for k, v in sorted(c.labels.items()) if v}
                if c.mode != default_mode(group, name) or labels:
                    cols[name] = {"mode": c.mode, **({"labels": labels} if labels else {})}
            if cols:
                out[f"columns:{group}"] = json.dumps(cols, sort_keys=True, ensure_ascii=False)
            if self.weights.get(group):
                out[f"weights:{group}"] = "1"
        return out

    @classmethod
    def from_rows(cls, rows: Dict[str, str]) -> "DocSettings":
        """Lezen; onbekende of kapotte regels worden overgeslagen (dan geldt de standaard)."""
        s = cls(taxnumber=rows.get("taxnumber", ""), return_line=rows.get("return_line") != "0",
                style=DocStyle.from_rows(rows))
        try:
            bank = json.loads(rows.get("bank") or "{}")
        except ValueError:
            bank = {}
        if bank.get("mode") == BANK_FIXED and bank.get("accno"):
            s.bank_mode, s.bank_accno = BANK_FIXED, str(bank["accno"])
        s.bank_name = bool(bank.get("name"))
        s.payinfo = bank.get("payinfo", True) is not False
        for key, value in rows.items():
            parts = key.split(":", 2)
            if parts[0] == "text" and len(parts) == 3 and parts[1] in LANGUAGES and _text_key_ok(parts[2]):
                s.texts.setdefault(parts[1], {})[parts[2]] = value
            elif parts[0] == "columns" and len(parts) == 2 and parts[1] in GROUPS:
                try:
                    cols = json.loads(value)
                except ValueError:
                    continue
                for name, c in (cols.items() if isinstance(cols, dict) else ()):
                    if name in GROUP_FIELDS[parts[1]] and isinstance(c, dict) and c.get("mode") in FIELDS[name].modes:
                        labels = {k: str(v) for k, v in (c.get("labels") or {}).items() if k in LANGUAGES}
                        s.columns.setdefault(parts[1], {})[name] = Column(c["mode"], labels)
            elif parts[0] == "weights" and len(parts) == 2 and parts[1] in GROUPS:
                s.weights[parts[1]] = value == "1"
        return s


def _text_key_ok(key: str) -> bool:
    if key in TEXTS:
        return True
    name, _, kind = key.partition(":")
    if name == "intro":
        return kind in LETTER_KINDS
    return name in KIND_TEXTS and kind in KINDS


# ================================================================== database

def _has_table(cur) -> bool:
    cur.execute("SELECT to_regclass('x_doc_setting') IS NOT NULL AS ok")
    return bool(cur.fetchone()["ok"])


def load(conn: psycopg.Connection) -> DocSettings:
    """Instellingen van de administratie (standaard als er niets is ingesteld of de tabel nog niet bestaat)."""
    with _db.cursor(conn) as cur:
        if not _has_table(cur):
            return DocSettings()
        cur.execute("SELECT key, value FROM x_doc_setting")
        return DocSettings.from_rows({r["key"]: r["value"] for r in cur.fetchall()})


def bank_accounts(conn: psycopg.Connection) -> List[Dict[str, Any]]:
    """Rekeningen met bankgegevens (Beheer > Rekeningschema, tabel ``bank``) voor de keuze 'vaste rekening'."""
    with _db.cursor(conn) as cur:
        cur.execute("""SELECT c.accno, c.description, bk.name, bk.iban, bk.bic, bk.membernumber, bk.dcn, bk.rvc
                       FROM chart c JOIN bank bk ON (bk.id = c.id)
                       WHERE c.charttype = 'A' ORDER BY c.accno""")
        return [{k: (v or "") for k, v in r.items()} for r in cur.fetchall()]


def validate(conn: psycopg.Connection, s: DocSettings) -> List[str]:
    """Meldingen (Nederlands, vaste teksten; de webapp vertaalt ze); leeg = in orde."""
    errors: List[str] = []
    if len(s.taxnumber) > MAX_TAXNUMBER:
        errors.append("Het btw-nummer is te lang.")
    if s.bank_mode not in (BANK_BOOKING, BANK_FIXED):
        errors.append("Onbekende keuze voor de bankrekening.")
    if s.bank_mode == BANK_FIXED:
        accounts = {a["accno"]: a for a in bank_accounts(conn)}
        if s.bank_accno not in accounts:
            errors.append("Kies een bankrekening met bankgegevens.")
        elif not accounts[s.bank_accno]["iban"]:
            errors.append("Deze bankrekening heeft geen IBAN.")
    for lang, texts in s.texts.items():
        if lang not in LANGUAGES:
            errors.append("Onbekende documenttaal.")
        for key, text in texts.items():
            if not _text_key_ok(key):
                errors.append("Onbekende tekst.")
            elif len(text) > MAX_TEXT:
                errors.append("Een tekst is te lang (maximaal 2000 tekens).")
    for group, cols in s.columns.items():
        if group not in GROUPS:
            errors.append("Onbekende documentgroep.")
            continue
        for name, c in cols.items():
            if name not in GROUP_FIELDS[group] or c.mode not in FIELDS[name].modes:
                errors.append("Ongeldige kolominstelling.")
            if any(len(v) > MAX_LABEL or "\n" in v for v in c.labels.values()):
                errors.append("Een kolomnaam is te lang (maximaal 60 tekens, één regel).")
    return list(dict.fromkeys(errors))


def save(conn: psycopg.Connection, s: DocSettings, *, login: str = "") -> List[str]:
    """Opslaan (eerst gecontroleerd). Geeft de gewijzigde sleutels terug; schrijft de audittrail als die aan staat."""
    errors = validate(conn, s)
    if errors:
        raise ValidationError(" ".join(errors))
    ensure_schema(conn)
    new = s.to_rows()
    with conn.transaction(), _db.cursor(conn) as cur:
        # de huisstijl (style.*) heeft een eigen scherm en blijft hier buiten
        cur.execute("SELECT key, value FROM x_doc_setting WHERE key NOT LIKE 'style.%%' FOR UPDATE")
        old = {r["key"]: r["value"] for r in cur.fetchall()}
        changed = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
        for key in changed:
            if key in new:
                cur.execute("""INSERT INTO x_doc_setting (key, value, login) VALUES (%s, %s, %s)
                               ON CONFLICT (key) DO UPDATE SET value = excluded.value, login = excluded.login,
                               updated = now()""", (key, new[key], login or None))
            else:
                cur.execute("DELETE FROM x_doc_setting WHERE key = %s", (key,))
        if changed and _db.get_defaults(cur, "audittrail").get("audittrail"):
            _, employee_id = _db.get_employee(cur, login.split("@")[0]) if login else (None, None)
            for key in changed:
                cur.execute("""INSERT INTO audittrail (trans_id, tablename, reference, formname, action, employee_id)
                               VALUES (NULL, 'x_doc_setting', %s, 'docsettings', %s, %s)""",
                            (key[:64], "saved" if key in new else "deleted", employee_id))
    return changed
