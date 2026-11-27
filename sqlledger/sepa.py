"""SEPA-betaalbestand van te betalen inkoopfacturen (BEHEER_EXTRA_ANALYSE.md IM9 A1, keuzes SP1–SP13).

Nieuw ten opzichte van SQL-Ledger (3.2.12 kent alleen een Zwitsers DTA-bestand). Er wordt **niets geboekt** (A1): de bank voert de overboekingen uit
en het bankafschrift lettert ze later af via de bestaande koppeling (:mod:`sqlledger.bankimport`, stap (a)
betalingskenmerk of (b) factuurnummer in de end-to-end-id).

* :meth:`SepaExport.accounts` — betaalrekeningen (``AP_paid``) met hun IBAN/BIC uit ``bank`` (SP8).
* :meth:`SepaExport.candidates` — open inkoopfacturen in euro, goedgekeurd en niet geblokkeerd, met het
  voorgestelde bedrag (openstaand min contante korting op de uitvoerdatum, SP3), IBAN/BIC van de leverancier,
  omschrijving (SP5), een reden als de post niet mee kan (SP7) en de datum van een eerder betaalbestand (SP11).
  Creditnota's en posten in een andere valuta gaan niet mee; die komen als melding terug (SP4, SP2).
* :meth:`SepaExport.create` — controleert de keuze opnieuw tegen de database (openstaand bedrag zoals het scherm
  het toonde), maakt de XML (pain.001.001.03 of .09, SP9; één overboeking per factuur, ``BtchBookg`` false, SP4/
  SP6), controleert hem tegen de XSD (SP10) en legt hem vast in ``x_sepa_export``/``x_sepa_item`` met een
  audittrailregel per factuur (formname ``sepa``, actie ``exported``).

Tekst gaat in de SEPA-tekenset (EPC: ``a-z A-Z 0-9 / - ? : ( ) . , ' +`` en spatie): accenten eraf, ``&`` wordt
``+``, de rest een spatie.
"""

from __future__ import annotations

import io
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import psycopg

from . import _db, support
from .amounts import round_amount
from .bankimport import is_valid_nl_reference, is_valid_rf_reference, norm_iban, norm_ref
from .config import UserConfig
from .contacts import bic_valid, iban_valid
from .errors import ValidationError
from .extensions import ensure_schema
from .payments import Payments

ZERO = Decimal(0)

#: versie (keuzelijst) -> naam van het bericht; de eerste is de standaard (SP9)
VERSIONS = {"03": "pain.001.001.03", "09": "pain.001.001.09"}
DEFAULT_VERSION = "03"

#: landen met een IBAN die aan SEPA meedoen (EPC-lijst; Albanië, Moldavië, Montenegro, Noord-Macedonië en Servië
#: sinds 2025). Een IBAN uit een ander land kan niet in een SEPA-bestand (SP7).
SEPA_COUNTRIES = frozenset(
    "AD AL AT BE BG CH CY CZ DE DK EE ES FI FR GB GG GI GR HR HU IE IM IS IT JE LI LT LU LV MC MD ME MK MT NL NO "
    "PL PT RO RS SE SI SK SM VA".split())
#: SEPA-landen buiten de EER: daar is het adres van de begunstigde verplicht
NON_EEA = frozenset("AD AL CH GB GG IM JE MC MD ME MK RS SM VA".split())

#: redenen waarom een post niet in het bestand kan (SP7); de webapp toont de tekst (routes_sepa.PROBLEM_LABELS)
PROBLEMS = ("iban_missing", "iban_invalid", "iban_not_sepa", "address_missing")

MAX_AMOUNT = Decimal("999999999.99")
XSD_DIR = Path(__file__).with_name("sepa_xsd")


# ------------------------------------------------------------------ tekst

_ALLOWED = re.compile(r"[^A-Za-z0-9/\-?:().,'+ ]")
_SPECIAL = {"ß": "ss", "Æ": "AE", "æ": "ae", "Ø": "O", "ø": "o", "Œ": "OE", "œ": "oe", "Ł": "L", "ł": "l",
            "Đ": "D", "đ": "d", "Þ": "Th", "þ": "th", "&": "+", "€": "EUR", "‘": "'", "’": "'"}


def sepa_text(value: Optional[str], maxlen: int) -> str:
    """Tekst in de SEPA-tekenset, spaties samengevoegd, hoogstens ``maxlen`` tekens."""
    s = "".join(_SPECIAL.get(c, c) for c in (value or ""))
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", _ALLOWED.sub(" ", s)).strip()
    return s[:maxlen].rstrip()


def sepa_id(value: Optional[str], maxlen: int = 35) -> str:
    """Kenmerk (MsgId, EndToEndId): SEPA-tekenset zonder spaties, niet beginnend/eindigend met '/' en zonder '//'."""
    s = sepa_text(value, 200).replace(" ", "")
    s = re.sub(r"/{2,}", "/", s).strip("/")
    return s[:maxlen].rstrip("/")


def next_workday(day: date) -> date:
    """Eerstvolgende werkdag ná ``day`` (zaterdag en zondag overgeslagen; feestdagen niet, SP6)."""
    d = day + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def remittance(invnumber: Optional[str], dcn: Optional[str]) -> Tuple[str, str]:
    """Omschrijving (SP5): ('nl'|'rf', kenmerk) als het betaalkenmerk een geldig NL- of RF-kenmerk is,
    anders ('', vrije tekst): factuurnummer, met een ander (ongestructureerd) kenmerk erachter."""
    ref = norm_ref(dcn)
    if ref and is_valid_nl_reference(ref):
        return "nl", ref
    if ref and is_valid_rf_reference(ref):
        return "rf", ref
    parts = [p for p in ((invnumber or "").strip(), (dcn or "").strip()) if p]
    if len(parts) == 2 and norm_ref(parts[0]) == norm_ref(parts[1]):
        parts = parts[:1]
    return "", sepa_text(" ".join(parts), 140)


# ------------------------------------------------------------------ datamodel

@dataclass
class SepaAccount:
    accno: str
    description: str
    iban: str
    bic: str


@dataclass
class Candidate:
    """Eén open inkoopfactuur op het scherm."""

    trans_id: int
    invnumber: Optional[str]
    transdate: date
    duedate: Optional[date]
    vc_id: int
    vcnumber: Optional[str]
    name: str
    due: Decimal                       # openstaand (euro)
    discount: Decimal                  # contante korting die op de uitvoerdatum nog geldt
    suggested: Decimal                 # voorgesteld bedrag
    iban: str
    bic: str
    ref_kind: str                      # 'nl', 'rf' of '' (vrije tekst)
    ref: str
    problem: Optional[str] = None      # sleutel uit PROBLEMS
    exported: Optional[date] = None    # laatste betaalbestand met deze factuur
    address: List[str] = field(default_factory=list)

    @property
    def selectable(self) -> bool:
        return self.problem is None

    def default_checked(self, execdate: date) -> bool:
        return self.selectable and not self.exported and (self.duedate or self.transdate) <= execdate


@dataclass
class CreditNote:
    trans_id: int
    invnumber: Optional[str]
    vc_id: int
    name: str
    due: Decimal                       # negatief


@dataclass
class Selection:
    candidates: List[Candidate]
    credit_notes: List[CreditNote]
    other_currency: int                # aantal open posten in een andere valuta dan euro


@dataclass
class Pick:
    trans_id: int
    amount: Decimal
    shown_due: Optional[Decimal] = None   # openstaand zoals het scherm het toonde


@dataclass
class ExportResult:
    id: int
    msgid: str
    version: str
    filename: str
    count: int
    total: Decimal
    execdate: date
    xml: bytes


class SepaChangedError(ValidationError):
    """Een gekozen post is intussen veranderd (betaald, gewijzigd of weg): niets gemaakt."""


# ------------------------------------------------------------------ bibliotheek

class SepaExport:
    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        self.conn = conn
        self.config = config
        ensure_schema(conn)

    # --- keuzes

    def accounts(self) -> List[SepaAccount]:
        """Betaalrekeningen voor leveranciers (AP_paid, niet gesloten) met IBAN; zonder IBAN niet kiesbaar."""
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT c.accno, c.description, b.iban, b.bic FROM chart c
                           LEFT JOIN bank b ON (b.id = c.id)
                           WHERE c.link LIKE '%%AP_paid%%' AND c.closed = '0' ORDER BY c.accno""")
            rows = cur.fetchall()
        out = []
        for r in rows:
            iban = norm_iban(r["iban"]) or ""
            out.append(SepaAccount(r["accno"], r["description"] or "", iban if iban_valid(iban) else "",
                                   (r["bic"] or "").strip().upper()))
        return out

    def exports(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Laatst gemaakte betaalbestanden (nieuwste eerst)."""
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT e.id, e.msgid, e.version, e.execdate, e.total, e.count, e.login, e.created,
                                  c.accno FROM x_sepa_export e LEFT JOIN chart c ON (c.id = e.chart_id)
                           ORDER BY e.id DESC LIMIT %s""", (limit,))
            return cur.fetchall()

    def export_file(self, export_id: int) -> Optional[Tuple[str, bytes]]:
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT id, execdate, xml FROM x_sepa_export WHERE id = %s", (export_id,))
            r = cur.fetchone()
        return (_filename(r["execdate"], r["id"]), r["xml"].encode("utf-8")) if r else None

    # --- selectie

    def candidates(self, execdate: date, *, duedateto: Optional[date] = None, vendor: str = "",
                   trans_ids: Optional[Iterable[int]] = None) -> Selection:
        """Open inkoopfacturen in euro. ``duedateto``: alleen posten die uiterlijk dan vervallen (zonder
        vervaldatum telt de factuurdatum); ``vendor``: deel van naam of nummer; ``trans_ids``: alleen deze."""
        only = set(trans_ids) if trans_ids is not None else None
        invs = Payments(self.conn, self.config).open_invoices("vendor", currency="EUR", datepaid=execdate)
        text = vendor.strip().lower()
        if text:
            invs = [i for i in invs if text in (i.name or "").lower() or text in (i.vcnumber or "").lower()]
        if only is not None:
            invs = [i for i in invs if i.id in only]
        ids = [i.id for i in invs]
        vc_ids = sorted({i.vc_id for i in invs})
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT id, dcn FROM ap WHERE id = ANY(%s)", (ids,))
            dcn = {r["id"]: r["dcn"] for r in cur.fetchall()}
            cur.execute("""SELECT i.trans_id, max(e.created)::date AS d FROM x_sepa_item i
                           JOIN x_sepa_export e ON (e.id = i.export_id) WHERE i.trans_id = ANY(%s)
                           GROUP BY i.trans_id""", (ids,))
            exported = {r["trans_id"]: r["d"] for r in cur.fetchall()}
            cur.execute("SELECT id, iban, bic FROM bank WHERE id = ANY(%s)", (vc_ids,))
            bank = {r["id"]: r for r in cur.fetchall()}
            cur.execute("""SELECT DISTINCT ON (trans_id) trans_id, address1, address2, zipcode, city, country
                           FROM address WHERE trans_id = ANY(%s) ORDER BY trans_id, id""", (vc_ids,))
            addr = {r["trans_id"]: r for r in cur.fetchall()}
            other = 0
            if only is None:
                cur.execute("""SELECT count(*) AS n FROM ap WHERE amount <> paid AND approved = '1'
                               AND onhold = '0' AND coalesce(curr, '') <> 'EUR'""")
                other = cur.fetchone()["n"]
        cands: List[Candidate] = []
        credits: List[CreditNote] = []
        for inv in invs:
            if inv.due <= 0:
                if inv.due < 0:
                    credits.append(CreditNote(inv.id, inv.invnumber, inv.vc_id, inv.name or "", inv.due))
                continue
            if duedateto and (inv.duedate or inv.transdate) > duedateto and inv.id not in (only or ()):
                continue
            b = bank.get(inv.vc_id) or {}
            iban = norm_iban(b.get("iban")) or ""
            bic = (b.get("bic") or "").strip().upper()
            a = addr.get(inv.vc_id)
            lines = _address_lines(a)
            problem = None
            if not iban:
                problem = "iban_missing"
            elif not iban_valid(iban):
                problem = "iban_invalid"
            elif iban[:2] not in SEPA_COUNTRIES:
                problem = "iban_not_sepa"
            elif iban[:2] in NON_EEA and not (a and (a["city"] or "").strip()):
                problem = "address_missing"
            kind, ref = remittance(inv.invnumber, dcn.get(inv.id))
            discount = inv.due - inv.paid_suggested
            cands.append(Candidate(
                trans_id=inv.id, invnumber=inv.invnumber, transdate=inv.transdate, duedate=inv.duedate,
                vc_id=inv.vc_id, vcnumber=inv.vcnumber, name=inv.name or "", due=inv.due, discount=discount,
                suggested=inv.paid_suggested, iban=iban, bic=bic if bic_valid(bic) else "", ref_kind=kind, ref=ref,
                problem=problem, exported=exported.get(inv.id), address=lines))
        cands.sort(key=lambda c: ((c.duedate or c.transdate), c.name.lower(), c.invnumber or "", c.trans_id))
        # alleen creditnota's van leveranciers die op het scherm staan
        shown = {c.vc_id for c in cands}
        return Selection(cands, [c for c in credits if c.vc_id in shown], other)

    # --- bestand maken

    def create(self, accno: str, execdate: date, version: str, picks: List[Pick], *,
               today: Optional[date] = None) -> ExportResult:
        """Maak het betaalbestand, controleer het tegen de XSD en leg het vast. Fout = niets vastgelegd."""
        today = today or date.today()
        if version not in VERSIONS:
            raise ValidationError("Onbekende versie van het betaalbestand.")
        if execdate < today:
            raise ValidationError("De uitvoerdatum ligt in het verleden.")
        if not picks:
            raise ValidationError("Kies minstens één factuur.")
        acc = next((a for a in self.accounts() if a.accno == accno), None)
        if acc is None:
            raise ValidationError("Kies een betaalrekening.")
        if not acc.iban:
            raise ValidationError(f"Betaalrekening {accno} heeft geen geldige IBAN.")
        if acc.iban[:2] not in SEPA_COUNTRIES:
            raise ValidationError(f"Betaalrekening {accno} heeft een IBAN van een land buiten SEPA.")
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            # één tegelijk: twee gebruikers die hetzelfde bestand maken zien elkaars vastlegging
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('x_sepa_export'))")
            sel = self.candidates(execdate, trans_ids=[p.trans_id for p in picks])
            by_id = {c.trans_id: c for c in sel.candidates}
            changed = []
            transfers: List[Tuple[Candidate, Decimal]] = []
            for p in picks:
                c = by_id.get(p.trans_id)
                if c is None or (p.shown_due is not None and p.shown_due != c.due):
                    changed.append(p.trans_id)
                    continue
                if c.problem:
                    raise ValidationError(f"Factuur {c.invnumber} van {c.name} kan niet in het betaalbestand.")
                amount = round_amount(p.amount, 2)
                if amount <= 0:
                    raise ValidationError(f"Factuur {c.invnumber} van {c.name}: het bedrag moet groter zijn dan 0.")
                if amount > c.due:
                    raise ValidationError(f"Factuur {c.invnumber} van {c.name}: het bedrag is hoger dan wat openstaat "
                                          f"({c.due}).")
                if amount > MAX_AMOUNT:
                    raise ValidationError(f"Factuur {c.invnumber} van {c.name}: het bedrag is te hoog.")
                transfers.append((c, amount))
            if changed:
                raise SepaChangedError("Een of meer facturen zijn intussen veranderd of niet meer open. "
                                       "Controleer de lijst en maak het bestand opnieuw.")
            defaults = _db.get_defaults(cur, "company", "address")
            company = sepa_text(defaults.get("company"), 70)
            if not company:
                raise ValidationError("Vul eerst de bedrijfsnaam in (Beheer > Instellingen).")
            total = sum((a for _, a in transfers), ZERO)
            cur.execute("""INSERT INTO x_sepa_export (msgid, version, chart_id, execdate, total, count, login, xml)
                           VALUES ('', %s, (SELECT id FROM chart WHERE accno = %s), %s, %s, %s, %s, '')
                           RETURNING id, created""",
                        (version, accno, execdate, total, len(transfers), self.config.login))
            row = cur.fetchone()
            export_id, created = row["id"], row["created"]
            msgid = sepa_id(f"PB-{created:%Y%m%d%H%M%S}-{export_id}")
            e2e = _end_to_end_ids([c for c, _ in transfers])
            needs_address = any(c.iban[:2] in NON_EEA for c, _ in transfers)
            xml = build_xml(version, msgid=msgid, created=created, execdate=execdate, company=company,
                            company_address=_lines(defaults.get("address")) if needs_address else [],
                            account=acc, transfers=[(c, a, e2e[c.trans_id]) for c, a in transfers])
            validate(version, xml)
            cur.execute("UPDATE x_sepa_export SET msgid = %s, xml = %s WHERE id = %s",
                        (msgid, xml.decode("utf-8"), export_id))
            for c, a in transfers:
                cur.execute("""INSERT INTO x_sepa_item (export_id, trans_id, amount, endtoend)
                               VALUES (%s, %s, %s, %s)""", (export_id, c.trans_id, a, e2e[c.trans_id]))
                support.audittrail(cur, self.config, trans_id=c.trans_id, tablename="ap", reference=c.invnumber,
                                   formname="sepa", action="exported")
        return ExportResult(export_id, msgid, version, _filename(execdate, export_id), len(transfers), total,
                            execdate, xml)


# ------------------------------------------------------------------ XML

def _filename(execdate: date, export_id: int) -> str:
    return f"betaalbestand-{execdate.isoformat()}-{export_id}.xml"


def _lines(text: Optional[str]) -> List[str]:
    return [s for s in (sepa_text(l, 70) for l in (text or "").splitlines()) if s][:2]


def _address_lines(a: Optional[Dict[str, Any]]) -> List[str]:
    if not a:
        return []
    street = " ".join(x for x in (a["address1"], a["address2"]) if x and x.strip())
    town = " ".join(x for x in (a["zipcode"], a["city"]) if x and x.strip())
    return [s for s in (sepa_text(street, 70), sepa_text(town, 70)) if s]


def _end_to_end_ids(cands: List[Candidate]) -> Dict[int, str]:
    """EndToEndId = factuurnummer (SP12), uniek binnen het bestand; komt in camt.053 terug."""
    out: Dict[int, str] = {}
    seen = set()
    for c in cands:
        base = sepa_id(c.invnumber) or f"PB{c.trans_id}"
        value, n = base, 1
        while value in seen:
            n += 1
            suffix = f"-{n}"
            value = base[:35 - len(suffix)] + suffix
        seen.add(value)
        out[c.trans_id] = value
    return out


def _amount(value: Decimal) -> str:
    return f"{round_amount(value, 2):.2f}"


def build_xml(version: str, *, msgid: str, created: datetime, execdate: date, company: str,
              company_address: List[str], account: SepaAccount,
              transfers: List[Tuple[Candidate, Decimal, str]]) -> bytes:
    """pain.001 (Customer Credit Transfer Initiation) met één betaalopdracht en één overboeking per factuur."""
    ns = f"urn:iso:std:iso:20022:tech:xsd:{VERSIONS[version]}"
    bic_tag = "BIC" if version == "03" else "BICFI"
    ET.register_namespace("", ns)

    def el(parent, tag, text=None, **attrs):
        e = ET.SubElement(parent, f"{{{ns}}}{tag}", attrs)
        if text is not None:
            e.text = text
        return e

    def agent(parent, tag, bic):
        fin = el(el(parent, tag), "FinInstnId")
        if bic:
            el(fin, bic_tag, bic)
        else:
            el(el(fin, "Othr"), "Id", "NOTPROVIDED")

    def party(parent, tag, name, lines, country):
        p = el(parent, tag)
        el(p, "Nm", name)
        if lines:
            adr = el(p, "PstlAdr")
            el(adr, "Ctry", country)
            for line in lines:
                el(adr, "AdrLine", line)
        return p

    total = sum((a for _, a, _ in transfers), ZERO)
    doc = ET.Element(f"{{{ns}}}Document")
    root = el(doc, "CstmrCdtTrfInitn")
    hdr = el(root, "GrpHdr")
    el(hdr, "MsgId", msgid)
    el(hdr, "CreDtTm", created.replace(tzinfo=None, microsecond=0).isoformat())
    el(hdr, "NbOfTxs", str(len(transfers)))
    el(hdr, "CtrlSum", _amount(total))
    el(el(hdr, "InitgPty"), "Nm", company)

    pmt = el(root, "PmtInf")
    el(pmt, "PmtInfId", sepa_id(f"{msgid}-1"))
    el(pmt, "PmtMtd", "TRF")
    el(pmt, "BtchBookg", "false")
    el(pmt, "NbOfTxs", str(len(transfers)))
    el(pmt, "CtrlSum", _amount(total))
    el(el(el(pmt, "PmtTpInf"), "SvcLvl"), "Cd", "SEPA")
    if version == "03":
        el(pmt, "ReqdExctnDt", execdate.isoformat())
    else:
        el(el(pmt, "ReqdExctnDt"), "Dt", execdate.isoformat())
    party(pmt, "Dbtr", company, company_address, account.iban[:2])
    el(el(el(pmt, "DbtrAcct"), "Id"), "IBAN", account.iban)
    agent(pmt, "DbtrAgt", account.bic if bic_valid(account.bic) else "")
    el(pmt, "ChrgBr", "SLEV")

    for c, amount, e2e in transfers:
        tx = el(pmt, "CdtTrfTxInf")
        el(el(tx, "PmtId"), "EndToEndId", e2e)
        el(el(tx, "Amt"), "InstdAmt", _amount(amount), Ccy="EUR")
        if c.bic:
            agent(tx, "CdtrAgt", c.bic)
        party(tx, "Cdtr", sepa_text(c.name, 70) or "?", c.address if c.iban[:2] in NON_EEA else [], c.iban[:2])
        el(el(el(tx, "CdtrAcct"), "Id"), "IBAN", c.iban)
        rmt = el(tx, "RmtInf")
        if c.ref_kind:
            ref = el(el(rmt, "Strd"), "CdtrRefInf")
            tp = el(ref, "Tp")
            el(el(tp, "CdOrPrtry"), "Cd", "SCOR")
            if c.ref_kind == "nl":
                el(tp, "Issr", "CUR")
            el(ref, "Ref", c.ref)
        else:
            el(rmt, "Ustrd", c.ref or sepa_text(c.invnumber, 140) or str(c.trans_id))
    ET.indent(doc)
    return ET.tostring(doc, encoding="UTF-8", xml_declaration=True)


@lru_cache(maxsize=None)
def _schema(version: str):
    import xmlschema
    return xmlschema.XMLSchema(str(XSD_DIR / f"{VERSIONS[version]}.xsd"))


def validate(version: str, xml: bytes) -> None:
    """Controle tegen de officiële XSD (SP10); fout = ValidationError met de eerste reden."""
    errors = list(_schema(version).iter_errors(io.BytesIO(xml)))
    if errors:
        e = errors[0]
        raise ValidationError(f"Het betaalbestand voldoet niet aan de XSD: {e.reason} ({e.path}).")
