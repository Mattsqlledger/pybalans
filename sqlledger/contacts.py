"""Klanten en leveranciers (port van SL/CT.pm + regels uit bin/mozilla/ct.pl).

Eén relatie = een rij in ``customer`` of ``vendor`` met daaromheen: adres
(``address``), contactpersoon (``contact``, de eerste; andere blijven staan),
bankgegevens (``bank`` met ``id`` = relatie, bankadres in ``address`` met
``id`` = ``trans_id`` = ``bank.address_id``), btw-rekeningen (``customertax`` /
``vendortax``), afwijkend verzendadres (``shipto`` met ``trans_id`` = relatie)
en referentiedocumenten.

Verschillen met Perl (zie README "Bewuste afwijkingen"):

* Opslaan werkt per tabel gericht bij: alleen wat gewijzigd is wordt
  geschreven. Perl gooide adres, contactpersoon, bank, btw en verzendadres weg
  en schreef ze opnieuw, waarbij kolommen die het scherm niet kent verloren
  gingen (``bank.dcn``/``rvc``, ``contact.parent_id``, ``shipto.shiptorecurring``)
  en lege waarden '' werden. Ongewijzigd opslaan verandert hier niets.
* Controles in de bibliotheek: naam verplicht, nummer uniek (bestaande dubbelen
  mogen blijven zolang het nummer niet verandert), ``lock_<vc>number``, IBAN
  (mod 97) en BIC-vorm bij een gewijzigde waarde, veldlengtes uit het schema.
* Verwijderen alleen als de relatie nergens gebruikt wordt (servercontrole).
* Alle SQL geparametriseerd.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import psycopg

from . import _db, support
from .amounts import to_decimal
from .config import UserConfig
from .errors import NotFoundError, ValidationError
from .gl import like
from .models import Reference
from .numbering import next_number

VC = ("customer", "vendor")
ARAP = {"customer": "AR", "vendor": "AP"}
TABLE = {"customer": "ar", "vendor": "ap"}

ADDRESS_FIELDS = ("address1", "address2", "city", "state", "zipcode", "country")
CONTACT_FIELDS = ("salutation", "firstname", "lastname", "contacttitle", "occupation", "mobile", "gender")
SHARED_FIELDS = ("phone", "fax", "email")       # zowel in de relatie als bij de contactpersoon (zoals Perl)
BANK_FIELDS = ("bankname", "iban", "bic", "membernumber", "clearingnumber")
BANKADDRESS_FIELDS = tuple("bank" + f for f in ADDRESS_FIELDS)
SHIPTO_FIELDS = ("shiptoname", "shiptoaddress1", "shiptoaddress2", "shiptocity", "shiptostate", "shiptozipcode",
                 "shiptocountry", "shiptocontact", "shiptophone", "shiptofax", "shiptoemail")
ACCOUNT_FIELDS = ("arap_accno", "payment_accno", "discount_accno", "prepayment_accno")

#: velden van de relatietabel zelf (veld -> kolom); nummer/contact/rekeningen apart
VC_COLUMNS = {
    "name": "name", "cc": "cc", "bcc": "bcc", "notes": "notes", "terms": "terms",
    "discount": "discount", "creditlimit": "creditlimit", "taxincluded": "taxincluded",
    "business_id": "business_id", "taxnumber": "taxnumber", "sic_code": "sic_code",
    "employee_id": "employee_id", "language_code": "language_code", "pricegroup_id": "pricegroup_id",
    "curr": "curr", "startdate": "startdate", "enddate": "enddate", "cashdiscount": "cashdiscount",
    "threshold": "threshold", "discountterms": "discountterms", "paymentmethod_id": "paymentmethod_id",
    "remittancevoucher": "remittancevoucher", "gifi_accno": "gifi_accno",
}

LABELS = {
    "number": "Nummer", "name": "Naam", "address1": "Adres", "address2": "Adres (2e regel)", "city": "Plaats",
    "state": "Provincie/staat", "zipcode": "Postcode", "country": "Land", "salutation": "Aanhef",
    "firstname": "Voornaam", "lastname": "Achternaam", "contacttitle": "Titel", "occupation": "Functie",
    "phone": "Telefoon", "fax": "Fax", "mobile": "Mobiel", "email": "E-mail", "taxnumber": "Btw-nummer",
    "sic_code": "SIC-code", "gifi_accno": "GIFI", "language_code": "Taal", "bankname": "Bank",
    "iban": "IBAN", "bic": "BIC", "contact": "Contactpersoon", "typeofcontact": "Soort", "gender": "Geslacht",
    "shiptoname": "Naam verzendadres", "shiptoaddress1": "Verzendadres", "shiptoaddress2": "Verzendadres (2e regel)",
    "shiptocity": "Plaats verzendadres", "shiptostate": "Provincie verzendadres",
    "shiptozipcode": "Postcode verzendadres", "shiptocountry": "Land verzendadres",
    "shiptocontact": "Contactpersoon verzendadres", "shiptophone": "Telefoon verzendadres",
    "shiptofax": "Fax verzendadres", "bankaddress1": "Adres bank", "bankaddress2": "Adres bank (2e regel)",
    "bankcity": "Plaats bank", "bankstate": "Provincie bank", "bankzipcode": "Postcode bank",
    "bankcountry": "Land bank", "curr": "Valuta",
}


@dataclass
class Relation:
    """Klant (vc='customer') of leverancier (vc='vendor') met alles wat het scherm toont.

    Percentages (``discount``, ``cashdiscount``) zijn hier procenten (2 = 2%); in de
    database staan ze als fractie (0.02), zoals Perl. ``taxaccounts``: rekeningnummers
    van de btw-rekeningen die voor deze relatie gelden. Rekeningen als rekeningnummer.
    """

    vc: str
    id: Optional[int] = None
    number: str = ""
    name: str = ""
    typeofcontact: str = "company"
    address1: str = ""
    address2: str = ""
    city: str = ""
    state: str = ""
    zipcode: str = ""
    country: str = ""
    salutation: str = ""
    firstname: str = ""
    lastname: str = ""
    contacttitle: str = ""
    occupation: str = ""
    gender: str = ""
    phone: str = ""
    fax: str = ""
    mobile: str = ""
    email: str = ""
    cc: str = ""
    bcc: str = ""
    notes: str = ""
    terms: int = 0
    discount: Decimal = Decimal(0)
    cashdiscount: Decimal = Decimal(0)
    discountterms: int = 0
    creditlimit: Decimal = Decimal(0)
    threshold: Decimal = Decimal(0)
    taxincluded: bool = False
    taxaccounts: List[str] = field(default_factory=list)
    arap_accno: str = ""
    payment_accno: str = ""
    discount_accno: str = ""
    prepayment_accno: str = ""
    curr: str = ""
    language_code: str = ""
    business_id: Optional[int] = None
    employee_id: Optional[int] = None
    paymentmethod_id: Optional[int] = None
    pricegroup_id: Optional[int] = None
    taxnumber: str = ""
    sic_code: str = ""
    gifi_accno: str = ""
    remittancevoucher: bool = False
    startdate: Optional[date] = None
    enddate: Optional[date] = None
    bankname: str = ""
    iban: str = ""
    bic: str = ""
    membernumber: str = ""
    clearingnumber: str = ""
    bankaddress1: str = ""
    bankaddress2: str = ""
    bankcity: str = ""
    bankstate: str = ""
    bankzipcode: str = ""
    bankcountry: str = ""
    shiptoname: str = ""
    shiptoaddress1: str = ""
    shiptoaddress2: str = ""
    shiptocity: str = ""
    shiptostate: str = ""
    shiptozipcode: str = ""
    shiptocountry: str = ""
    shiptocontact: str = ""
    shiptophone: str = ""
    shiptofax: str = ""
    shiptoemail: str = ""
    references: List[Reference] = field(default_factory=list)
    # alleen gelezen (get): niet opgeslagen
    contactid: Optional[int] = None
    addressid: Optional[int] = None
    in_use: bool = False

    @property
    def table(self) -> str:
        return TABLE[self.vc]

    @property
    def active(self) -> bool:
        return self.enddate is None or self.enddate > date.today()


#: soort per veld, voor het vergelijken (gewijzigd of niet)
_KIND: Dict[str, str] = {}
for _f in fields(Relation):
    if _f.name in ("vc", "id", "contactid", "addressid", "in_use"):
        continue
    _KIND[_f.name] = {"int": "int", "Decimal": "dec", "bool": "bool", "Optional[int]": "id",
                      "Optional[date]": "date", "List[str]": "set", "List[Reference]": "refs"}.get(
        str(_f.type), "str")


def _norm(kind: str, v: Any) -> Any:
    """Vergelijkbare waarde: '' = NULL, 0 = NULL bij koppelingen, getallen exact."""
    if kind == "str":       # een browser stuurt regeleinden altijd als CRLF
        return ("" if v is None else str(v)).replace("\r\n", "\n").strip()
    if kind in ("int", "dec"):
        return to_decimal(v)
    if kind == "bool":
        return bool(v)
    if kind == "id":
        return int(v) if v else None
    if kind == "set":
        return sorted({str(x).strip() for x in (v or []) if str(x).strip()})
    if kind == "refs":
        return [(r.description or "", r.code or "", r.archive_id or None, r.folder or "", bool(r.confidential))
                for r in (v or []) if r.description]
    return v


def changed_fields(old: Relation, new: Relation) -> set:
    """Velden waarvan de waarde (genormaliseerd) verschilt."""
    return {k for k, kind in _KIND.items() if _norm(kind, getattr(old, k)) != _norm(kind, getattr(new, k))}


@dataclass
class ContactSearch:
    """Zoekvelden van het scherm 'Klanten/Leveranciers zoeken' (ct.pl search).

    status: 'all' | 'active' | 'inactive' | 'orphaned' (= ongebruikt).
    Tekstvelden zoeken als Form::like (deel van de tekst, "aanhalingstekens" = exact).
    """

    name: Optional[str] = None
    number: Optional[str] = None
    contact: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zipcode: Optional[str] = None
    country: Optional[str] = None
    employee_id: Optional[int] = None
    business_id: Optional[int] = None
    startdatefrom: Optional[date] = None
    startdateto: Optional[date] = None
    status: str = "all"
    sort: str = "name"
    direction: str = "ASC"


#: sorteerkolommen van de lijst (ct.pl list_names: kolomkoppen met &sort=)
SORT_COLUMNS = {
    "name": "lower(c.name)", "number": "lower(c.{vc}number)", "city": "lower(ad.city)",
    "state": "lower(ad.state)", "zipcode": "ad.zipcode", "country": "lower(ad.country)",
    "contact": "lower(ct.lastname), lower(ct.firstname)", "occupation": "lower(ct.occupation)",
    "phone": "c.phone", "fax": "c.fax", "email": "lower(c.email)", "cc": "lower(c.cc)", "bcc": "lower(c.bcc)",
    "notes": "c.notes", "paymentmethod": "pm.description", "taxnumber": "c.taxnumber",
    "gifi_accno": "c.gifi_accno", "sic_code": "c.sic_code", "business": "b.description",
    "employee": "e.name", "pricegroup": "g.pricegroup", "language": "l.description",
    "startdate": "c.startdate", "enddate": "c.enddate",
}


def iban_valid(iban: str) -> bool:
    """IBAN-controle: vorm en mod 97 (ISO 13616)."""
    s = re.sub(r"\s", "", iban or "").upper()
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", s):
        return False
    digits = "".join(str(int(ch, 36)) for ch in s[4:] + s[:4])
    return int(digits) % 97 == 1


def bic_valid(bic: str) -> bool:
    """BIC-vorm (ISO 9362): 4 letters bank, 2 letters land, 2 tekens plaats, optioneel 3 tekens filiaal."""
    return bool(re.fullmatch(r"[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?", (bic or "").strip().upper()))


def clean_name(name: str) -> str:
    """Naam opschonen zoals CT::save: spaties rond weg, dubbele spaties enkel,
    '--' wordt '-' en geen '-' aan het eind ('--' is in Perl het scheidingsteken in keuzelijsten)."""
    name = (name or "").strip()
    name = name.replace("  ", " ")
    name = re.sub(r"--+", "-", name)
    return re.sub(r"-+$", "", name)


def _none(v: str) -> Optional[str]:
    v = "" if v is None else str(v)
    return v if v.strip() else None


class Contacts:
    """Klanten en leveranciers: lezen, opslaan, verwijderen, inactief maken, zoeken."""

    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        self.conn = conn
        self.config = config

    # ------------------------------------------------------------ hulp

    @staticmethod
    def _check_vc(vc: str) -> None:
        if vc not in VC:
            raise ValueError("vc moet 'customer' of 'vendor' zijn")

    def _usage(self, cur, vc: str, vc_id: int) -> Dict[str, int]:
        """Waar de relatie gebruikt wordt (boekingen, orders, betalingen)."""
        cur.execute(f"""SELECT (SELECT count(*) FROM {TABLE[vc]} WHERE {vc}_id = %(id)s) AS arap,
                               (SELECT count(*) FROM oe WHERE {vc}_id = %(id)s) AS oe,
                               (SELECT count(*) FROM pay_trans WHERE trans_id = %(id)s) AS pay_trans,
                               (SELECT count(*) FROM acc_trans WHERE trans_id = %(id)s) AS acc_trans""",
                    {"id": vc_id})
        return dict(cur.fetchone())

    def in_use(self, vc: str, vc_id: int) -> bool:
        self._check_vc(vc)
        with _db.cursor(self.conn) as cur:
            return any(self._usage(cur, vc, vc_id).values())

    def _load(self, cur, vc: str, vc_id: int, lock: bool = False) -> Tuple[Relation, Dict[str, Any]]:
        """Relatie + ruwe gegevens (bank-adres-id, ...) voor opslaan."""
        cur.execute(f"""SELECT c.*, c.{vc}number AS number,
                               c1.accno AS arap_accno, c2.accno AS payment_accno,
                               c3.accno AS discount_accno, c4.accno AS prepayment_accno
                        FROM {vc} c
                        LEFT JOIN chart c1 ON (c1.id = c.arap_accno_id)
                        LEFT JOIN chart c2 ON (c2.id = c.payment_accno_id)
                        LEFT JOIN chart c3 ON (c3.id = c.discount_accno_id)
                        LEFT JOIN chart c4 ON (c4.id = c.prepayment_accno_id)
                        WHERE c.id = %s{' FOR UPDATE OF c' if lock else ''}""", (vc_id,))
        row = cur.fetchone()
        if not row:
            raise NotFoundError(f"{vc} {vc_id} not found")
        r = Relation(vc=vc, id=vc_id)
        for k in VC_COLUMNS:
            if k in row:
                setattr(r, k, row[k])
        for k in ("number", "phone", "fax", "email") + ACCOUNT_FIELDS:
            setattr(r, k, row[k])
        r.curr = (row["curr"] or "").strip()
        r.discount = _pct(row["discount"])
        r.cashdiscount = _pct(row["cashdiscount"])
        for k in ("creditlimit", "threshold"):
            setattr(r, k, to_decimal(row[k]))
        for k in ("terms", "discountterms"):
            setattr(r, k, int(row[k] or 0))
        raw: Dict[str, Any] = {"vc": dict(row)}

        cur.execute("SELECT * FROM address WHERE trans_id = %s ORDER BY id LIMIT 1", (vc_id,))
        ad = cur.fetchone()
        raw["address"] = ad
        if ad:
            r.addressid = ad["id"]
            for k in ADDRESS_FIELDS:
                setattr(r, k, ad[k])

        cur.execute("SELECT * FROM contact WHERE trans_id = %s ORDER BY id LIMIT 1", (vc_id,))
        ct = cur.fetchone()
        raw["contact"] = ct
        if ct:
            r.contactid = ct["id"]
            for k in CONTACT_FIELDS:
                setattr(r, k, ct[k])
            for k in SHARED_FIELDS:         # Perl toonde die van de contactpersoon; leeg -> die van de relatie
                if (ct[k] or "").strip():
                    setattr(r, k, ct[k])
            r.typeofcontact = ct["typeofcontact"] or "company"
            r.gender = (ct["gender"] or "").strip()

        cur.execute("""SELECT b.*, a.id AS bank_address_row, a.address1, a.address2, a.city, a.state,
                              a.zipcode, a.country
                       FROM bank b LEFT JOIN address a ON (a.id = b.address_id)
                       WHERE b.id = %s""", (vc_id,))
        bk = cur.fetchone()
        raw["bank"] = bk
        if bk:
            r.bankname, r.iban, r.bic = bk["name"], bk["iban"], bk["bic"]
            r.membernumber, r.clearingnumber = bk["membernumber"], bk["clearingnumber"]
            for k in ADDRESS_FIELDS:
                setattr(r, "bank" + k, bk[k])

        cur.execute("SELECT * FROM shipto WHERE trans_id = %s LIMIT 1", (vc_id,))
        sh = cur.fetchone()
        raw["shipto"] = sh
        if sh:
            for k in SHIPTO_FIELDS:
                setattr(r, k, sh[k])

        cur.execute(f"""SELECT c.accno FROM chart c JOIN {vc}tax t ON (t.chart_id = c.id)
                        WHERE t.{vc}_id = %s ORDER BY c.accno""", (vc_id,))
        r.taxaccounts = [x["accno"] for x in cur.fetchall()]

        r.references = [Reference(description=x["description"], code=x["code"], archive_id=x["archive_id"],
                                  folder=x["folder"], confidential=x["confidential"])
                        for x in support.all_references(cur, self.config, vc_id)]
        for k, kind in _KIND.items():
            if kind == "str" and getattr(r, k) is None:
                setattr(r, k, "")
        r.in_use = any(self._usage(cur, vc, vc_id).values())
        return r, raw

    # ------------------------------------------------------------ lezen

    def get(self, vc: str, vc_id: int) -> Relation:
        """Relatie met adres, contactpersoon, bank, btw-rekeningen, standaardrekeningen,
        verzendadres en referenties (CT::create_links)."""
        self._check_vc(vc)
        with _db.cursor(self.conn) as cur:
            return self._load(cur, vc, vc_id)[0]

    def new(self, vc: str) -> Relation:
        """Lege relatie zoals ct.pl add: startdatum vandaag, standaardvaluta, soort uit
        defaults.typeofcontact (anders bedrijf), geslacht M."""
        self._check_vc(vc)
        with _db.cursor(self.conn) as cur:
            d = _db.get_defaults(cur, "typeofcontact")
            curr = _db.default_currency(cur) or ""
        t = d.get("typeofcontact") if d.get("typeofcontact") in ("company", "person") else "company"
        return Relation(vc=vc, startdate=date.today(), curr=curr, typeofcontact=t, gender="M")

    def choices(self, vc: str, include: Optional[Relation] = None) -> Dict[str, Any]:
        """Keuzelijsten voor het scherm (CT::create_links): rekeningen per koppeling
        (alleen open, plus wat de relatie nu heeft), btw-rekeningen, branches,
        medewerkers (verkopers + de huidige), talen, betaalmethoden, valuta."""
        self._check_vc(vc)
        A = ARAP[vc]
        out: Dict[str, Any] = {"arap": [], "payment": [], "discount": [], "tax": []}
        cc = self.config.countrycode
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT c.accno, COALESCE(l.description, c.description) AS description, c.link, c.closed
                           FROM chart c
                           LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %s)
                           WHERE c.link LIKE %s ORDER BY c.accno""", (cc, f"%{A}%"))
            rows = cur.fetchall()
            keep = set()
            if include:
                keep = {getattr(include, k) for k in ACCOUNT_FIELDS} | set(include.taxaccounts)
            for r in rows:
                links = (r["link"] or "").split(":")
                usable = not r["closed"] or r["accno"] in keep
                if not usable:
                    continue
                item = {"accno": r["accno"], "description": r["description"]}
                if A in links:
                    out["arap"].append(item)
                if f"{A}_paid" in links:
                    out["payment"].append(item)
                if f"{A}_discount" in links:
                    out["discount"].append(item)
            cur.execute("""SELECT DISTINCT c.accno, COALESCE(l.description, c.description) AS description, c.closed
                           FROM chart c JOIN tax t ON (t.chart_id = c.id)
                           LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %s)
                           WHERE c.link LIKE %s ORDER BY c.accno""", (cc, f"%{A}_tax%"))
            out["tax"] = [{"accno": r["accno"], "description": r["description"]} for r in cur.fetchall()
                          if not r["closed"] or r["accno"] in keep]
            cur.execute("SELECT id, description FROM business ORDER BY rn NULLS LAST, description, id")
            out["business"] = cur.fetchall()
            emp = int(include.employee_id or 0) if include else 0
            cur.execute("""SELECT id, name FROM employee WHERE enddate IS NULL AND sales = '1'
                           UNION SELECT id, name FROM employee WHERE id = %s ORDER BY 2, 1""", (emp,))
            out["employees"] = cur.fetchall()
            cur.execute("SELECT code, description FROM language ORDER BY code")
            out["languages"] = cur.fetchall()
            cur.execute("SELECT id, description FROM paymentmethod ORDER BY rn, id")
            out["paymentmethods"] = cur.fetchall()
            cur.execute("SELECT id, pricegroup FROM pricegroup ORDER BY pricegroup, id")
            out["pricegroups"] = cur.fetchall()
            out["currencies"] = [c for c in _db.currencies(cur) if c]
            d = _db.get_defaults(cur, f"lock_{vc}number", "precision")
            out["lock_number"] = d.get(f"lock_{vc}number") not in (None, "", "0")
            out["precision"] = int(to_decimal(d.get("precision") or 2))
        return out

    # ------------------------------------------------------------ nummer

    def next_number(self, vc: str, rel: Optional[Relation] = None, peek: bool = False) -> str:
        """Volgend relatienummer (defaults.customernumber/vendornumber, met sjablonen
        <%name%>, <%business%>, <%phone%>). peek=True: alleen tonen, de teller blijft
        staan (Perl hoogde bij "Nieuw nummer" de teller meteen op, ook zonder opslaan)."""
        self._check_vc(vc)
        with self.conn.transaction(force_rollback=peek), _db.cursor(self.conn) as cur:
            return self._next_number(cur, vc, rel)

    def _next_number(self, cur, vc: str, rel: Optional[Relation]) -> str:
        business = None
        if rel and rel.business_id:
            cur.execute("SELECT description FROM business WHERE id = %s", (rel.business_id,))
            b = cur.fetchone()
            business = b["description"] if b else None
        flds = {"name": rel.name if rel else None, "business": business, "phone": rel.phone if rel else None}
        return next_number(cur, f"{vc}number", dateformat=self.config.dateformat, fields=flds)

    # ------------------------------------------------------------ opslaan

    def _lengths(self, cur) -> Dict[Tuple[str, str], int]:
        cur.execute("""SELECT table_name, column_name, character_maximum_length AS n
                       FROM information_schema.columns
                       WHERE table_schema = current_schema() AND character_maximum_length IS NOT NULL
                         AND table_name IN ('customer', 'vendor', 'contact', 'address', 'bank', 'shipto')""")
        return {(r["table_name"], r["column_name"]): r["n"] for r in cur.fetchall()}

    def _chart_id(self, cur, accno: str, label: str) -> Optional[int]:
        if not accno:
            return None
        cur.execute("SELECT id FROM chart WHERE accno = %s", (accno,))
        row = cur.fetchone()
        if not row:
            raise ValidationError(f"Onbekende rekening bij {label}: {accno}")
        return row["id"]

    def save(self, rel: Relation, reserved_number: Optional[str] = None) -> int:
        """Nieuwe relatie toevoegen of een bestaande wijzigen (CT::save). Geeft het id.

        Bij wijzigen worden alleen gewijzigde velden geschreven; niet getoonde kolommen
        blijven staan. Een leeg nummer wordt het volgende nummer. reserved_number: het
        nummer dat "Nieuw nummer" alleen getoond heeft; staat dat er nog, dan wordt de
        teller nu pas opgehoogd.
        """
        self._check_vc(rel.vc)
        vc = rel.vc
        new = Relation(**{f.name: getattr(rel, f.name) for f in fields(Relation)})
        for k, kind in _KIND.items():
            if kind == "str" and getattr(new, k) is None:
                setattr(new, k, "")
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            old: Optional[Relation] = None
            raw: Dict[str, Any] = {}
            if new.id:
                old, raw = self._load(cur, vc, new.id, lock=True)
            changed = set(_KIND) if old is None else changed_fields(old, new)
            if old is not None and not changed:
                return new.id

            # ---- naam (CT::save): persoon = achternaam voornaam, opschonen
            recompute = new.typeofcontact == "person" and (
                old is None or bool({"firstname", "lastname", "typeofcontact"} & changed) or not new.name.strip())
            if recompute:
                new.name = f"{new.lastname} {new.firstname}"
            if old is None or recompute or "name" in changed:
                new.name = clean_name(new.name)
                if old is not None:
                    if _norm("str", old.name) != new.name:
                        changed.add("name")
                    else:
                        changed.discard("name")
            if not new.name.strip():
                raise ValidationError("Vul een naam in.")

            # ---- controles
            self._validate(cur, new, old, changed)

            # ---- nummer
            locked = _db.get_defaults(cur, f"lock_{vc}number").get(f"lock_{vc}number") not in (None, "", "0")
            number = (new.number or "").strip()
            if old is not None and "number" in changed and locked:
                raise ValidationError("Het nummer kan niet gewijzigd worden (vergrendeld in de instellingen).")
            if reserved_number and number == reserved_number.strip():
                number = self._next_number(cur, vc, new)
            elif not number:
                number = self._next_number(cur, vc, new)
            if old is None or _norm("str", old.number) != number:
                changed.add("number")
                cur.execute(f"SELECT id, name FROM {vc} WHERE {vc}number = %s AND id <> %s LIMIT 1",
                            (number, int(new.id or 0)))
                dup = cur.fetchone()
                if dup:
                    raise ValidationError(f"Nummer {number} bestaat al ({dup['name']}).")
            new.number = number
            self._check_lengths(cur, new, old, changed)

            if old is None:
                return self._insert(cur, new)
            self._update(cur, new, old, raw, changed)
            return new.id

    def _validate(self, cur, new: Relation, old: Optional[Relation], changed: set) -> None:
        if new.typeofcontact not in ("company", "person"):
            raise ValidationError("Soort relatie moet bedrijf of persoon zijn.")
        if new.gender not in ("", "M", "F"):
            raise ValidationError("Ongeldig geslacht.")
        for k in ("terms", "discountterms"):
            v = to_decimal(getattr(new, k))
            if v != v.to_integral_value() or not -32768 <= v <= 32767:
                raise ValidationError(f"Ongeldig aantal dagen: {getattr(new, k)}")
        for k in ("discount", "cashdiscount"):      # alleen bij een nieuwe waarde (oude data: bv. 333%)
            if k in changed and not 0 <= to_decimal(getattr(new, k)) <= 100:
                raise ValidationError("Een kortingspercentage ligt tussen 0 en 100.")
        if "iban" in changed and new.iban.strip():
            new.iban = re.sub(r"\s", "", new.iban).upper()
            if not iban_valid(new.iban):
                raise ValidationError(f"IBAN {new.iban} is niet geldig (controle mislukt).")
        if "bic" in changed and new.bic.strip():
            new.bic = new.bic.strip().upper()
            if not bic_valid(new.bic):
                raise ValidationError(f"BIC {new.bic} heeft niet de juiste vorm (8 of 11 tekens).")
        if new.startdate and new.enddate and new.enddate < new.startdate and {"startdate", "enddate"} & changed:
            raise ValidationError("De einddatum ligt voor de startdatum.")
        if "curr" in changed and new.curr:
            if new.curr not in _db.currencies(cur):
                raise ValidationError(f"Onbekende valuta: {new.curr}")
        if "language_code" in changed and new.language_code:
            cur.execute("SELECT 1 FROM language WHERE code = %s", (new.language_code,))
            if not cur.fetchone():
                raise ValidationError(f"Onbekende taal: {new.language_code}")
        for k, table in (("business_id", "business"), ("employee_id", "employee"),
                         ("paymentmethod_id", "paymentmethod"), ("pricegroup_id", "pricegroup")):
            if k in changed and getattr(new, k):
                cur.execute(f"SELECT 1 FROM {table} WHERE id = %s", (getattr(new, k),))
                if not cur.fetchone():
                    raise ValidationError(f"Onbekende keuze ({k}): {getattr(new, k)}")
        if "taxaccounts" in changed:
            for accno in new.taxaccounts:
                self._chart_id(cur, accno, "btw")

    def _check_lengths(self, cur, new: Relation, old: Optional[Relation], changed: set) -> None:
        maxlen = self._lengths(cur)
        vc = new.vc
        where = {**{k: (vc, VC_COLUMNS[k]) for k in VC_COLUMNS}, "number": (vc, f"{vc}number"),
                 **{k: ("address", k) for k in ADDRESS_FIELDS},
                 **{k: ("contact", k) for k in CONTACT_FIELDS + SHARED_FIELDS},
                 "bankname": ("bank", "name"), "iban": ("bank", "iban"), "bic": ("bank", "bic"),
                 **{"bank" + k: ("address", k) for k in ADDRESS_FIELDS},
                 **{k: ("shipto", k) for k in SHIPTO_FIELDS}}
        for k in sorted(changed):
            if k not in where or _KIND.get(k, "str") != "str" and k != "number":
                continue
            n = maxlen.get(where[k])
            value = (getattr(new, k) or "")
            if n and len(value) > n:
                raise ValidationError(f"{LABELS.get(k, k)} is te lang (maximaal {n} tekens).")
        contact = f"{new.firstname} {new.lastname}"
        n = maxlen.get((vc, "contact"))
        if n and len(contact) > n and {"firstname", "lastname"} & changed:
            raise ValidationError(f"Voornaam en achternaam samen zijn te lang (maximaal {n - 1} tekens).")

    def _vc_values(self, cur, r: Relation, keys: Iterable[str]) -> Dict[str, Any]:
        """Kolomwaarden voor de relatietabel voor de opgegeven velden."""
        vc = r.vc
        out: Dict[str, Any] = {}
        for k in keys:
            if k == "number":
                out[f"{vc}number"] = r.number
            elif k in ACCOUNT_FIELDS:
                out[f"{k}_id"] = self._chart_id(cur, getattr(r, k), {"arap_accno": ARAP[vc],
                                                                     "payment_accno": "betaling",
                                                                     "discount_accno": "korting",
                                                                     "prepayment_accno": "vooruitbetaling"}[k])
            elif k in SHARED_FIELDS:
                out[k] = _none(getattr(r, k))
            elif k in ("firstname", "lastname"):
                out["contact"] = f"{r.firstname} {r.lastname}"        # zoals Perl, ook " " als beide leeg
            elif k in VC_COLUMNS:
                if k == "gifi_accno" and vc != "vendor":
                    continue
                v = getattr(r, k)
                kind = _KIND[k]
                if k in ("discount", "cashdiscount"):
                    v = to_decimal(v) / 100
                elif kind == "str":
                    v = _none(v)
                elif kind in ("int", "dec"):
                    v = to_decimal(v)
                elif kind == "id":
                    v = int(v) if v else None
                elif kind == "bool":
                    v = bool(v)
                out[VC_COLUMNS[k]] = v
        return out

    def _insert(self, cur, r: Relation) -> int:
        """Nieuwe relatie; volgorde van de rijen (en dus van de id-reeksen) zoals CT::save."""
        vc = r.vc
        keys = ["number", "name", "firstname"] + list(SHARED_FIELDS) + list(ACCOUNT_FIELDS) + \
            [k for k in VC_COLUMNS if k != "name"]
        vals = self._vc_values(cur, r, keys)
        cols = list(vals)
        cur.execute(f"INSERT INTO {vc} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
                    [vals[c] for c in cols])
        r.id = cur.fetchone()["id"]
        self._write_bank(cur, r, None)
        self._insert_address(cur, r.id, r)
        self._insert_contact(cur, r)
        self._write_taxes(cur, r)
        self._write_shipto(cur, r, None)
        refs = [x for x in r.references if x.description]
        if refs:
            support.save_references(cur, self.config, r.id, vc, refs)
        return r.id

    def _insert_address(self, cur, trans_id: int, r: Relation) -> None:
        cur.execute("""INSERT INTO address (trans_id, address1, address2, city, state, zipcode, country)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    [trans_id] + [_none(getattr(r, k)) for k in ADDRESS_FIELDS])

    def _insert_contact(self, cur, r: Relation) -> None:
        cols = CONTACT_FIELDS + SHARED_FIELDS
        cur.execute(f"""INSERT INTO contact (trans_id, typeofcontact, {', '.join(cols)})
                        VALUES (%s, %s, {', '.join(['%s'] * len(cols))})""",
                    [r.id, r.typeofcontact] + [_none(getattr(r, k)) for k in cols])

    def _update(self, cur, new: Relation, old: Relation, raw: Dict[str, Any], changed: set) -> None:
        vc = new.vc
        vals = self._vc_values(cur, new, changed)
        if vals:
            cur.execute(f"UPDATE {vc} SET {', '.join(f'{c} = %s' for c in vals)} WHERE id = %s",
                        list(vals.values()) + [new.id])
        # adres
        ch = [k for k in ADDRESS_FIELDS if k in changed]
        if ch:
            if raw["address"]:
                cur.execute(f"UPDATE address SET {', '.join(f'{k} = %s' for k in ch)} WHERE id = %s",
                            [_none(getattr(new, k)) for k in ch] + [raw["address"]["id"]])
            else:
                self._insert_address(cur, new.id, new)
        # contactpersoon (de eerste; andere blijven staan)
        ch = [k for k in CONTACT_FIELDS + SHARED_FIELDS if k in changed]
        if "typeofcontact" in changed:
            ch.append("typeofcontact")
        if ch:
            if raw["contact"]:
                cur.execute(f"UPDATE contact SET {', '.join(f'{k} = %s' for k in ch)} WHERE id = %s",
                            [new.typeofcontact if k == "typeofcontact" else _none(getattr(new, k)) for k in ch]
                            + [raw["contact"]["id"]])
            else:
                self._insert_contact(cur, new)
        if set(BANK_FIELDS + BANKADDRESS_FIELDS) & changed:
            self._write_bank(cur, new, raw["bank"], changed)
        if "taxaccounts" in changed:
            self._write_taxes(cur, new)
        if set(SHIPTO_FIELDS) & changed:
            self._write_shipto(cur, new, raw["shipto"])
        if "references" in changed:
            support.save_references(cur, self.config, new.id, vc, [x for x in new.references if x.description])

    def _write_bank(self, cur, r: Relation, bk: Optional[Dict[str, Any]], changed: Optional[set] = None) -> None:
        """Bankgegevens. Bestaande rij: alleen gewijzigde kolommen; dcn/rvc blijven staan.
        Naam en adres van de bank worden niet meer in hoofdletters gezet (Perl: uc)."""
        any_bank = any(_none(getattr(r, k)) for k in BANK_FIELDS)
        any_addr = any(_none(getattr(r, k)) for k in BANKADDRESS_FIELDS)
        colmap = {"bankname": "name", "iban": "iban", "bic": "bic", "membernumber": "membernumber",
                  "clearingnumber": "clearingnumber"}
        if bk is None:
            if not (any_bank or any_addr):
                return
            cur.execute("""INSERT INTO bank (id, name, iban, bic, membernumber, clearingnumber)
                           VALUES (%s, %s, %s, %s, %s, %s) RETURNING address_id""",
                        [r.id] + [_none(getattr(r, k)) for k in BANK_FIELDS])
            address_id = cur.fetchone()["address_id"]
            if any_addr:
                self._bank_address(cur, address_id, r, exists=False)
            return
        if not (any_bank or any_addr) and not (bk["dcn"] or bk["rvc"]):
            if bk["address_id"]:
                cur.execute("DELETE FROM address WHERE trans_id = %s", (bk["address_id"],))
            cur.execute("DELETE FROM bank WHERE id = %s", (r.id,))
            return
        ch = [k for k in BANK_FIELDS if changed is None or k in changed]
        if ch:
            cur.execute(f"UPDATE bank SET {', '.join(f'{colmap[k]} = %s' for k in ch)} WHERE id = %s",
                        [_none(getattr(r, k)) for k in ch] + [r.id])
        if changed is None or set(BANKADDRESS_FIELDS) & changed:
            address_id = bk["address_id"]
            if address_id is None:
                cur.execute("UPDATE bank SET address_id = nextval('addressid') WHERE id = %s RETURNING address_id",
                            (r.id,))
                address_id = cur.fetchone()["address_id"]
            if any_addr:
                self._bank_address(cur, address_id, r, exists=bk["bank_address_row"] is not None)
            elif bk["bank_address_row"] is not None:
                cur.execute("DELETE FROM address WHERE id = %s", (address_id,))

    def _bank_address(self, cur, address_id: int, r: Relation, exists: bool) -> None:
        values = [_none(getattr(r, "bank" + k)) for k in ADDRESS_FIELDS]
        if exists:
            cur.execute(f"UPDATE address SET {', '.join(f'{k} = %s' for k in ADDRESS_FIELDS)} WHERE id = %s",
                        values + [address_id])
        else:
            cur.execute("""INSERT INTO address (id, trans_id, address1, address2, city, state, zipcode, country)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""", [address_id, address_id] + values)

    def _write_taxes(self, cur, r: Relation) -> None:
        vc = r.vc
        cur.execute(f"DELETE FROM {vc}tax WHERE {vc}_id = %s", (r.id,))
        for accno in sorted(set(a for a in r.taxaccounts if a)):
            cur.execute(f"INSERT INTO {vc}tax ({vc}_id, chart_id) VALUES (%s, %s)",
                        (r.id, self._chart_id(cur, accno, "btw")))

    def _write_shipto(self, cur, r: Relation, sh: Optional[Dict[str, Any]]) -> None:
        """Verzendadres (Form::add_shipto): alleen bewaard als een veld gevuld is en
        afwijkt van het factuuradres; ``shiptorecurring`` van een bestaande rij blijft."""
        recurring = bool(sh["shiptorecurring"]) if sh else False
        cur.execute("DELETE FROM shipto WHERE trans_id = %s", (r.id,))
        billing = {"name": r.name, "contact": f"{r.firstname} {r.lastname}",
                   **{k: getattr(r, k) for k in ADDRESS_FIELDS + SHARED_FIELDS}}
        differs = any((getattr(r, k) or "") != "" and (getattr(r, k) or "") != (billing.get(k[6:]) or "")
                      for k in SHIPTO_FIELDS)
        if not differs:
            return
        cur.execute(f"""INSERT INTO shipto (trans_id, {', '.join(SHIPTO_FIELDS)}, shiptorecurring)
                        VALUES (%s, {', '.join(['%s'] * len(SHIPTO_FIELDS))}, %s)""",
                    [r.id] + [_none(getattr(r, k)) for k in SHIPTO_FIELDS] + [recurring])

    # ------------------------------------------------------------ inactief / verwijderen

    def set_inactive(self, vc: str, vc_id: int, enddate: Optional[date] = None) -> None:
        """Relatie inactief maken: einddatum zetten (standaard vandaag); verder niets."""
        self._check_vc(vc)
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute(f"UPDATE {vc} SET enddate = %s WHERE id = %s RETURNING id",
                        (enddate or date.today(), vc_id))
            if not cur.fetchone():
                raise NotFoundError(f"{vc} {vc_id} not found")

    def delete(self, vc: str, vc_id: int) -> None:
        """Verwijder een ongebruikte relatie (CT::delete), met adres, contactpersonen,
        bank, btw-koppelingen, prijsafspraken, verzendadres en referenties; audittrail.

        Geweigerd als de relatie in verkoop/inkoop, orders/offertes of betalingen voorkomt
        (Perl controleerde dat alleen door de knop te verbergen)."""
        self._check_vc(vc)
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            self._delete(cur, vc, vc_id)

    def delete_many(self, vc: str, ids: Sequence[int]) -> List[str]:
        """Meerdere ongebruikte relaties verwijderen (CT::batch_delete), alles of niets.
        Geeft de namen terug."""
        self._check_vc(vc)
        names = []
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            for vc_id in ids:
                names.append(self._delete(cur, vc, int(vc_id)))
        return names

    def _delete(self, cur, vc: str, vc_id: int) -> str:
        cur.execute(f"SELECT id, name FROM {vc} WHERE id = %s FOR UPDATE", (vc_id,))
        row = cur.fetchone()
        if not row:
            raise NotFoundError(f"{vc} {vc_id} not found")
        usage = self._usage(cur, vc, vc_id)
        if any(usage.values()):
            label = "Klant" if vc == "customer" else "Leverancier"
            raise ValidationError(f"{label} {row['name']} wordt gebruikt in boekingen of orders en kan niet "
                                  f"verwijderd worden; maak de relatie inactief.")
        cur.execute(f"DELETE FROM {vc} WHERE id = %s", (vc_id,))
        cur.execute(f"DELETE FROM {vc}tax WHERE {vc}_id = %s", (vc_id,))
        cur.execute(f"DELETE FROM parts{vc} WHERE {vc}_id = %s", (vc_id,))
        cur.execute("DELETE FROM shipto WHERE trans_id = %s", (vc_id,))
        cur.execute("DELETE FROM contact WHERE trans_id = %s", (vc_id,))
        if vc == "vendor":                  # vaste kostenrekening (stap 4e), eigen tabel
            cur.execute("SELECT to_regclass('x_vendor_default') IS NOT NULL AS t")
            if cur.fetchone()["t"]:
                cur.execute("DELETE FROM x_vendor_default WHERE vendor_id = %s", (vc_id,))
        cur.execute("SELECT 1 FROM employee WHERE id = %s", (vc_id,))
        if not (vc == "vendor" and cur.fetchone()):        # CT::delete: adres/bank horen dan bij de medewerker
            cur.execute("SELECT address_id FROM bank WHERE id = %s", (vc_id,))
            bk = cur.fetchone()
            if bk and bk["address_id"]:
                cur.execute("DELETE FROM address WHERE id = %s", (bk["address_id"],))
            cur.execute("DELETE FROM address WHERE trans_id = %s", (vc_id,))
            cur.execute("DELETE FROM bank WHERE id = %s", (vc_id,))
            support.delete_references(cur, self.config, vc_id)
        support.audittrail(cur, self.config, trans_id=vc_id, tablename=vc, reference=row["name"],
                           formname="", action="deleted")
        return row["name"]

    # ------------------------------------------------------------ zoeken

    def countries(self, vc: str) -> List[str]:
        """Landen die bij deze relaties voorkomen (Form::all_countries)."""
        self._check_vc(vc)
        with _db.cursor(self.conn) as cur:
            cur.execute(f"""SELECT DISTINCT a.country FROM address a JOIN {vc} vc ON (vc.id = a.trans_id)
                            WHERE a.country <> '' ORDER BY 1""")
            return [r["country"] for r in cur.fetchall()]

    def search(self, vc: str, s: Optional[ContactSearch] = None) -> List[Dict[str, Any]]:
        """Relaties zoeken (CT::search + ct.pl list_names), één rij per relatie.

        Anders dan Perl: adres en contactpersoon met LEFT JOIN (een relatie zonder
        adres of contactpersoon bleef in Perl onzichtbaar; met twee contactpersonen
        stond ze er twee keer in); 'actief' = geen einddatum of een einddatum in de
        toekomst; 'ongebruikt' kijkt ook naar betalingen; openstaand en beschikbaar
        krediet altijd uitgerekend.
        """
        self._check_vc(vc)
        s = s or ContactSearch()
        A = TABLE[vc]
        where, params = ["1 = 1"], []

        def text(col: str, value: Optional[str]) -> None:
            if value not in (None, ""):
                where.append(f"lower({col}) LIKE %s")
                params.append(like(value.lower()))
        text(f"c.{vc}number", s.number)
        text("c.name", s.name)
        text("c.contact", s.contact)
        text("c.notes", s.notes)
        text("c.phone", s.phone)
        text("c.email", s.email)
        text("ad.city", s.city)
        text("ad.state", s.state)
        text("ad.zipcode", s.zipcode)
        if s.address:
            where.append("(lower(ad.address1) LIKE %s OR lower(ad.address2) LIKE %s)")
            params += [like(s.address.lower())] * 2
        if s.country:
            where.append("ad.country = %s")
            params.append(s.country)
        if s.employee_id:
            where.append("c.employee_id = %s")
            params.append(s.employee_id)
        if s.business_id:
            where.append("c.business_id = %s")
            params.append(s.business_id)
        if s.startdatefrom:
            where.append("c.startdate >= %s")
            params.append(s.startdatefrom)
        if s.startdateto:
            where.append("c.startdate <= %s")
            params.append(s.startdateto)
        if s.status == "active":
            where.append("(c.enddate IS NULL OR c.enddate > current_date)")
        elif s.status == "inactive":
            where.append("c.enddate <= current_date")
        elif s.status == "orphaned":
            where.append(f"""NOT EXISTS (SELECT 1 FROM {A} a WHERE a.{vc}_id = c.id)
                             AND NOT EXISTS (SELECT 1 FROM oe WHERE oe.{vc}_id = c.id)
                             AND NOT EXISTS (SELECT 1 FROM pay_trans p WHERE p.trans_id = c.id)
                             AND NOT EXISTS (SELECT 1 FROM acc_trans t WHERE t.trans_id = c.id)""")
        sort = SORT_COLUMNS.get(s.sort or "name", SORT_COLUMNS["name"]).format(vc=vc)
        direction = "DESC" if str(s.direction).upper() == "DESC" else "ASC"
        sort = ", ".join(f"{part} {direction}" for part in sort.split(", "))
        gifi = "c.gifi_accno" if vc == "vendor" else "NULL::text AS gifi_accno"
        query = f"""
            SELECT c.id, c.{vc}number AS number, c.name, c.contact, c.phone, c.fax, c.email, c.cc, c.bcc,
                   c.notes, c.terms, c.discount, c.cashdiscount, c.discountterms, c.creditlimit, c.threshold,
                   c.taxnumber, {gifi}, c.sic_code, c.startdate, c.enddate, c.remittancevoucher, c.curr,
                   b.description AS business, e.name AS employee, g.pricegroup, l.description AS language,
                   pm.description AS paymentmethod,
                   ad.address1, ad.address2, ad.city, ad.state, ad.zipcode, ad.country,
                   ct.salutation, ct.firstname, ct.lastname, ct.contacttitle, ct.occupation, ct.mobile,
                   ct.gender, COALESCE(ct.typeofcontact, 'company') AS typeofcontact,
                   bk.name AS bankname, bk.iban, bk.bic, bk.membernumber, bk.clearingnumber,
                   ba.address1 AS bankaddress1, ba.address2 AS bankaddress2, ba.city AS bankcity,
                   ba.state AS bankstate, ba.zipcode AS bankzipcode, ba.country AS bankcountry,
                   c1.accno AS arap_accno, c2.accno AS payment_accno, c3.accno AS discount_accno,
                   c4.accno AS prepayment_accno,
                   (SELECT string_agg(ch.accno, ' ' ORDER BY ch.accno) FROM {vc}tax t
                      JOIN chart ch ON (ch.id = t.chart_id) WHERE t.{vc}_id = c.id) AS taxaccounts,
                   COALESCE((SELECT sum(a.amount::numeric) - sum(a.paid::numeric) FROM {A} a
                             WHERE a.{vc}_id = c.id AND a.amount <> a.paid), 0) AS outstanding,
                   (EXISTS (SELECT 1 FROM {A} a WHERE a.{vc}_id = c.id)
                    OR EXISTS (SELECT 1 FROM oe WHERE oe.{vc}_id = c.id)
                    OR EXISTS (SELECT 1 FROM pay_trans p WHERE p.trans_id = c.id)
                    OR EXISTS (SELECT 1 FROM acc_trans t WHERE t.trans_id = c.id)) AS in_use
            FROM {vc} c
            LEFT JOIN address ad ON (ad.id = (SELECT min(x.id) FROM address x WHERE x.trans_id = c.id))
            LEFT JOIN contact ct ON (ct.id = (SELECT min(x.id) FROM contact x WHERE x.trans_id = c.id))
            LEFT JOIN business b ON (b.id = c.business_id)
            LEFT JOIN employee e ON (e.id = c.employee_id)
            LEFT JOIN pricegroup g ON (g.id = c.pricegroup_id)
            LEFT JOIN language l ON (l.code = c.language_code)
            LEFT JOIN paymentmethod pm ON (pm.id = c.paymentmethod_id)
            LEFT JOIN bank bk ON (bk.id = c.id)
            LEFT JOIN address ba ON (ba.id = bk.address_id)
            LEFT JOIN chart c1 ON (c1.id = c.arap_accno_id)
            LEFT JOIN chart c2 ON (c2.id = c.payment_accno_id)
            LEFT JOIN chart c3 ON (c3.id = c.discount_accno_id)
            LEFT JOIN chart c4 ON (c4.id = c.prepayment_accno_id)
            WHERE {' AND '.join(where)}
            ORDER BY {sort}, c.id"""
        with _db.cursor(self.conn) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
        for r in rows:
            r["curr"] = (r["curr"] or "").strip()
            r["discount"] = _pct(r["discount"])
            r["cashdiscount"] = _pct(r["cashdiscount"])
            r["creditlimit"] = to_decimal(r["creditlimit"])
            r["threshold"] = to_decimal(r["threshold"])
            r["outstanding"] = to_decimal(r["outstanding"])
            r["availablecredit"] = r["creditlimit"] - r["outstanding"]
            # ct.pl: termijn als "korting%/kortingsdagen/netto dagen"
            parts = []
            if r["cashdiscount"]:
                parts.append(format(r["cashdiscount"].normalize(), "f"))
            if r["discountterms"]:
                parts.append(str(r["discountterms"]))
            if r["terms"]:
                parts.append(str(r["terms"]))
            r["termstext"] = "/".join(parts)
            r["address"] = " ".join(x for x in (r[k] for k in ADDRESS_FIELDS) if x)
        return rows


def _pct(v) -> Decimal:
    """Fractie uit de database (real) als percentage: 0.02 -> 2."""
    d = to_decimal(v)
    return (d * 100).normalize() if d else Decimal(0)
