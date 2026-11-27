"""Bijlagen bij boekingen, orders/offertes, relaties en artikelen (fase 4 stap 4e, D17/IU6; stap 6a, RB1).

Bestanden staan zoals in SQL-Ledger in ``reference`` (verwijzing per boeking: omschrijving,
formname, vertrouwelijk = login), ``archive`` (bestandsnaam) en ``archivedata`` (inhoud):
Form::save_reference leest het bestand in blokken van 512 bytes en bewaart elk blok als
``pack 'u'`` (uuencode, nul als backtick) met volgnummer ``rn``; Form::get_reference plakt de
blokken weer aan elkaar. Dezelfde codering hier, zodat SQL-Ledger onze bijlagen opent en wij die
van SQL-Ledger (rd.pl).

Alleen pdf, jpg en png, gecontroleerd op de inhoud (niet op de bestandsnaam), tot de maximale
uploadgrootte van de administratie (stap 4f, MU1–MU5): ``defaults.max_upload_size`` in MB zoals
SQL-Ledger 4.x, leeg (of 0, of weg doordat SQL-Ledger 3.2.12 de standaarden opnieuw schreef) = 10 MB,
hoogstens 25 MB.
Vertrouwelijk: alleen de gebruiker die de bijlage toevoegde ziet hem (``reference.login``).
"""

from __future__ import annotations

import binascii
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Tuple

import psycopg

from . import _db, images
from .config import UserConfig
from .errors import NotFoundError, ValidationError

MB = 1024 * 1024
#: maximale uploadgrootte (MU2): standaard en grenzen, in MB
DEFAULT_MB, MIN_MB, MAX_MB = 10, 1, 25
MAX_SIZE = DEFAULT_MB * MB
CHUNK = 512
#: soort (extensie) -> MIME-type
TYPES = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg"}
EXTENSIONS = {"pdf": ("pdf",), "png": ("png",), "jpg": ("jpg", "jpeg")}
#: formname -> tabel (rd.pl formnames, voor zover er een scherm is; RB1). Naast de namen van rd.pl ook wat
#: SQL-Ledger zelf schrijft: AA.pm/IS.pm/IR.pm bewaren "${table}_$type" (ar_credit_note, ap_debit_invoice …),
#: IC.pm het soort artikel (ook kit, dat rd.pl niet kent).
FORMNAMES = {"ar_transaction": "ar", "credit_note": "ar", "ar_invoice": "ar", "credit_invoice": "ar",
             "ar_credit_note": "ar", "ar_credit_invoice": "ar",
             "ap_transaction": "ap", "debit_note": "ap", "ap_invoice": "ap", "debit_invoice": "ap",
             "ap_debit_note": "ap", "ap_debit_invoice": "ap",
             "gl": "gl",
             "sales_order": "oe", "purchase_order": "oe", "sales_quotation": "oe", "request_quotation": "oe",
             "customer": "customer", "vendor": "vendor",
             "part": "parts", "service": "parts", "assembly": "parts", "labor": "parts", "kit": "parts"}
#: extra voorwaarde: de soort moet bij het record passen (een offerte is geen order, een dienst geen artikel)
_OE = {"sales_order": (False, "customer"), "purchase_order": (False, "vendor"),
       "sales_quotation": (True, "customer"), "request_quotation": (True, "vendor")}

__all__ = ["Attachment", "Attachments", "Holder", "DEFAULT_MB", "MAX_MB", "MAX_SIZE", "MB", "MIN_MB", "TYPES", "FORMNAMES",
           "Duplicates", "KINDS", "OverviewRow", "check_file", "decode", "describe", "duplicates", "ensure_hashes", "prepare", "record_exists",
           "save_hash", "sha256", "detect_type", "encode", "max_size", "safe_filename", "setting_mb", "store"]


def detect_type(data: bytes) -> Optional[str]:
    """Soort bestand op grond van de eerste bytes: 'pdf', 'png', 'jpg' of None."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    return None


def setting_mb(raw) -> Optional[int]:
    """``defaults.max_upload_size`` als hele MB binnen MIN_MB–MAX_MB, of None (= standaard).

    Leeg, 0 (SQL-Ledger 4.x schrijft 0 voor een leeg veld) en onleesbaar: None. 4.x laat ook breuken en
    grotere getallen toe: naar beneden afgerond en begrensd."""
    text = str(raw or "").strip().replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        return None
    if not value > 0 or value != value or value == float("inf"):
        return None
    return min(MAX_MB, max(MIN_MB, int(value)))


def max_size(cur) -> int:
    """Maximale uploadgrootte van deze administratie in bytes."""
    cur.execute("SELECT fldvalue FROM defaults WHERE fldname = 'max_upload_size'")
    row = cur.fetchone()
    return (setting_mb(row["fldvalue"] if row else None) or DEFAULT_MB) * MB


def check_file(filename: str, data: bytes, limit: int = MAX_SIZE) -> str:
    """Controle bij uploaden; geeft de soort terug of ValidationError. ``limit`` in bytes."""
    if not data:
        raise ValidationError("Het bestand is leeg.")
    if len(data) > limit:
        raise ValidationError(f"Het bestand is groter dan {limit // MB} MB.")
    kind = detect_type(data)
    if not kind:
        raise ValidationError("Alleen PDF, JPG of PNG kan als bijlage.")
    return kind


def safe_filename(name: str, kind: str) -> str:
    """Bestandsnaam zonder pad en stuurtekens, hoogstens 200 tekens, met een extensie die bij de
    inhoud past (SQL-Ledger leidt het contenttype af uit de extensie)."""
    name = re.split(r"[\\/]", name or "")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip().lstrip(".")
    if not name:
        name = "bijlage"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in EXTENSIONS[kind]:
        name = f"{name}.{kind}"
        ext = kind
    if len(name) > 200:
        name = name[:200 - len(ext) - 1] + "." + ext
    return name


def encode(data: bytes) -> List[str]:
    """Inhoud als archivedata-rijen: per 512 bytes Perl ``pack 'u'``."""
    rows = []
    for i in range(0, len(data), CHUNK):
        block = data[i:i + CHUNK]
        rows.append("".join(binascii.b2a_uu(block[j:j + 45], backtick=True).decode("ascii")
                            for j in range(0, len(block), 45)))
    return rows


def decode(rows: Iterable[str]) -> bytes:
    """Rijen uit archivedata (op volgorde van rn) terug naar de inhoud (``unpack 'u'``)."""
    out = bytearray()
    for row in rows:
        for line in (row or "").split("\n"):
            if line:
                out += binascii.a2b_uu(line)
    return bytes(out)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def prepare(filename: str, data: bytes, limit: int = MAX_SIZE) -> Tuple[str, bytes]:
    """Controle (soort, grootte) en wat er bewaard wordt: foto verkleind en zonder metadata (RB4), pdf
    ongewijzigd. Geeft (soort, inhoud)."""
    kind = check_file(filename, data, limit)
    return kind, images.normalize(data, kind)


def _archive_hash_column(cur) -> bool:
    """Heeft ``archive`` de kolom ``hash`` (SQL-Ledger 4.x)? Dan daar, anders in ``x_archive_hash``."""
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_schema = current_schema() AND table_name = 'archive' AND column_name = 'hash'""")
    return cur.fetchone() is not None


def _hash_table(cur) -> Tuple[str, str]:
    """(join, kolom) om bij een archiefstuk ``a`` de hash te vinden."""
    if _archive_hash_column(cur):
        return "", "a.hash"
    from .extensions import ensure_schema
    ensure_schema(cur.connection)
    return "LEFT JOIN x_archive_hash h ON (h.archive_id = a.id)", "h.hash"


def save_hash(cur, archive_id: int, digest: str) -> None:
    if _archive_hash_column(cur):
        cur.execute("UPDATE archive SET hash = %s WHERE id = %s", (digest, archive_id))
    else:
        _hash_table(cur)
        cur.execute("""INSERT INTO x_archive_hash (archive_id, hash) VALUES (%s, %s)
                       ON CONFLICT (archive_id) DO UPDATE SET hash = excluded.hash""", (archive_id, digest))


def ensure_hashes(cur) -> int:
    """Archiefstukken zonder hash (van SQL-Ledger, of van vóór stap 6b) alsnog een hash geven, zoals SQL-Ledger
    4.x bij het eerste openen; geeft het aantal."""
    join, col = _hash_table(cur)
    cur.execute(f"SELECT a.id FROM archive a {join} WHERE {col} IS NULL ORDER BY a.id")
    ids = [r["id"] for r in cur.fetchall()]
    for archive_id in ids:
        cur.execute("SELECT bt FROM archivedata WHERE archive_id = %s ORDER BY rn", (archive_id,))
        save_hash(cur, archive_id, sha256(decode(x["bt"] for x in cur.fetchall())))
    return len(ids)


def store(cur, filename: str, data: bytes, login: Optional[str] = None) -> int:
    """Nieuw archiefstuk (archive + archivedata + sha256); geeft archive.id. Controleert het bestand (ook de
    maximale uploadgrootte van de administratie) en verkleint/schoont foto's op (RB4). Een identiek bestand
    elders wordt niet gedeeld (RB5): altijd een nieuw archiefstuk."""
    kind, data = prepare(filename, data, max_size(cur))
    cur.execute("INSERT INTO archive (filename) VALUES (%s) RETURNING id", (safe_filename(filename, kind),))
    archive_id = cur.fetchone()["id"]
    for rn, bt in enumerate(encode(data), 1):
        cur.execute("INSERT INTO archivedata (rn, archive_id, bt) VALUES (%s, %s, %s)", (rn, archive_id, bt))
    save_hash(cur, archive_id, sha256(data))
    from .extensions import ensure_schema
    ensure_schema(cur.connection)
    cur.execute("INSERT INTO x_archive_added (archive_id, login) VALUES (%s, %s)", (archive_id, login))
    return archive_id


def record_exists(cur, formname: str, trans_id: int) -> bool:
    """Bestaat het record waar de bijlage bij hoort, en past de soort erbij?"""
    table = FORMNAMES.get(formname)
    if not table:
        return False
    if table == "oe":
        quotation, vc = _OE[formname]
        cur.execute(f"SELECT 1 FROM oe WHERE id = %s AND quotation = %s AND {vc}_id > 0", (trans_id, quotation))
        return cur.fetchone() is not None
    if table == "parts":
        from .parts import kind_of
        cur.execute("SELECT * FROM parts WHERE id = %s", (trans_id,))
        row = cur.fetchone()
        return row is not None and kind_of(row) == formname
    cur.execute(f"SELECT 1 FROM {table} WHERE id = %s", (trans_id,))
    return cur.fetchone() is not None


@dataclass
class Holder:
    """Waar een archiefstuk (ook) hangt: soort, record, nummer en naam (voor "ook bij …")."""

    formname: str
    trans_id: int
    number: str = ""
    name: str = ""
    amount: Optional[float] = None          # boeking/order: totaalbedrag
    curr: str = ""

    @property
    def table(self) -> Optional[str]:
        return FORMNAMES.get(self.formname)


def describe(cur, formname: str, trans_id: int) -> Optional[Holder]:
    """Nummer en naam van het record achter een verwijzing; None als het record niet (meer) bestaat."""
    table = FORMNAMES.get(formname)
    if table in ("ar", "ap"):
        vc = "customer" if table == "ar" else "vendor"
        cur.execute(f"""SELECT t.invnumber AS number, c.name, t.amount, t.curr FROM {table} t
                        LEFT JOIN {vc} c ON (c.id = t.{vc}_id) WHERE t.id = %s""", (trans_id,))
    elif table == "gl":
        cur.execute("SELECT reference AS number, description AS name FROM gl WHERE id = %s", (trans_id,))
    elif table == "oe":
        vc = _OE[formname][1]
        cur.execute(f"""SELECT CASE WHEN o.quotation THEN o.quonumber ELSE o.ordnumber END AS number, c.name,
                               o.amount, o.curr
                        FROM oe o LEFT JOIN {vc} c ON (c.id = o.{vc}_id) WHERE o.id = %s""", (trans_id,))
    elif table in ("customer", "vendor"):
        cur.execute(f"SELECT {table}number AS number, name FROM {table} WHERE id = %s", (trans_id,))
    elif table == "parts":
        cur.execute("SELECT partnumber AS number, description AS name FROM parts WHERE id = %s", (trans_id,))
    else:
        return None
    r = cur.fetchone()
    if not r:
        return None
    return Holder(formname=formname, trans_id=trans_id, number=r["number"] or "", name=r["name"] or "",
                  amount=float(r["amount"]) if r.get("amount") is not None else None,
                  curr=(r.get("curr") or "").strip())


@dataclass
class Duplicates:
    """Waar hetzelfde bestand (zelfde sha256) al hangt (RB5)."""

    here: bool = False                                   # al bij dit record
    holders: List[Holder] = field(default_factory=list)  # bij andere records, voor zover zichtbaar
    hidden: int = 0                                      # vertrouwelijk bij een ander: wel melden, niet waar
    loose: int = 0                                       # los archiefstuk zonder koppeling (rd.pl)

    def __bool__(self) -> bool:
        return bool(self.here or self.holders or self.hidden or self.loose)


def duplicates(cur, login: str, digest: str, trans_id: Optional[int] = None) -> Duplicates:
    """Zoek archiefstukken met dezelfde inhoud. ``trans_id``: het record waar het bestand bij komt."""
    ensure_hashes(cur)
    join, col = _hash_table(cur)
    cur.execute(f"""SELECT a.id, r.trans_id, r.formname, r.login FROM archive a {join}
                    LEFT JOIN reference r ON (r.archive_id = a.id)
                    WHERE {col} = %s ORDER BY r.trans_id, r.id""", (digest,))
    out = Duplicates()
    seen = set()
    for r in cur.fetchall():
        if r["trans_id"] is None:
            out.loose += 1
        elif r["login"] and r["login"] != login:
            out.hidden += 1                  # ook bij dit record: niet verraden dat het er is
        elif trans_id is not None and r["trans_id"] == trans_id:
            out.here = True
        elif (r["trans_id"], r["formname"]) not in seen:
            seen.add((r["trans_id"], r["formname"]))
            h = describe(cur, r["formname"] or "", r["trans_id"])
            if h:
                out.holders.append(h)
    return out


#: soorten in het overzicht (RB3): sleutel -> formnames; "los" = archiefstuk zonder verwijzing (rd.pl "Document
#: toevoegen"), "overig" = formnames zonder scherm (project, urenstaat … uit SQL-Ledger)
KINDS = {
    "verkoop": ("ar_transaction", "credit_note", "ar_credit_note", "ar_invoice", "credit_invoice", "ar_credit_invoice"),
    "inkoop": ("ap_transaction", "debit_note", "ap_debit_note", "ap_invoice", "debit_invoice", "ap_debit_invoice"),
    "memoriaal": ("gl",),
    "orders": ("sales_order", "purchase_order"),
    "offertes": ("sales_quotation", "request_quotation"),
    "klanten": ("customer",),
    "leveranciers": ("vendor",),
    "artikelen": ("part", "service", "assembly", "labor", "kit"),
}


@dataclass
class OverviewRow:
    """Een regel in het bijlagenoverzicht: een verwijzing met bestand, of een los archiefstuk."""

    archive_id: int
    filename: str
    reference_id: Optional[int] = None
    trans_id: Optional[int] = None
    formname: str = ""
    description: str = ""
    confidential: bool = False
    number: str = ""
    name: str = ""
    added: Any = None                 # datetime of None (van vóór stap 6c of uit SQL-Ledger)
    added_by: str = ""

    @property
    def loose(self) -> bool:
        return self.reference_id is None


#: nummer en naam van het record achter een verwijzing, in één query (trans_id is uniek over deze tabellen: SQL-Ledger
#: gebruikt voor alle de reeks ``id``)
_HOLDER_JOINS = """
    LEFT JOIN ar ON (ar.id = r.trans_id) LEFT JOIN customer arc ON (arc.id = ar.customer_id)
    LEFT JOIN ap ON (ap.id = r.trans_id) LEFT JOIN vendor apv ON (apv.id = ap.vendor_id)
    LEFT JOIN gl ON (gl.id = r.trans_id)
    LEFT JOIN oe ON (oe.id = r.trans_id) LEFT JOIN customer oec ON (oec.id = oe.customer_id)
                                         LEFT JOIN vendor oev ON (oev.id = oe.vendor_id)
    LEFT JOIN customer c ON (c.id = r.trans_id) LEFT JOIN vendor v ON (v.id = r.trans_id)
    LEFT JOIN parts p ON (p.id = r.trans_id)"""
_HOLDER_NUMBER = """COALESCE(ar.invnumber, ap.invnumber, gl.reference,
                    CASE WHEN oe.quotation THEN oe.quonumber ELSE oe.ordnumber END,
                    c.customernumber, v.vendornumber, p.partnumber, '')"""
_HOLDER_NAME = "COALESCE(arc.name, apv.name, gl.description, oec.name, oev.name, c.name, v.name, p.description, '')"


@dataclass
class Attachment:
    """Een verwijzing (reference) met een bestand."""

    id: int                     # reference.id
    trans_id: Optional[int]
    archive_id: int
    description: str
    filename: str
    formname: str
    confidential: bool
    #: hetzelfde archiefstuk hangt ook hier (omzetten, RB2; rd.pl "Koppelen aan"), voor zover zichtbaar
    elsewhere: List[Holder] = field(default_factory=list)

    @property
    def module(self) -> Optional[str]:
        return FORMNAMES.get(self.formname)


#: zichtbaar voor deze gebruiker (Form::all_references); parameter: de login
_VISIBLE = "(r.login = %s OR r.login = '' OR r.login IS NULL)"


class Attachments:
    """Bijlagen bij een boeking lezen, toevoegen en verwijderen (los van het opnieuw boeken)."""

    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        self.conn = conn
        self.config = config

    def _row(self, r) -> Attachment:
        return Attachment(id=r["id"], trans_id=r["trans_id"], archive_id=r["archive_id"],
                          description=r["description"] or "", filename=r["filename"] or "",
                          formname=r["formname"] or "", confidential=bool(r["login"]))

    def list(self, trans_id: int) -> List[Attachment]:
        """Bijlagen (verwijzingen met een bestand) die deze gebruiker mag zien, op volgorde van toevoegen."""
        login = self.config.short_login
        with _db.cursor(self.conn) as cur:
            cur.execute(f"""SELECT r.*, a.filename FROM reference r JOIN archive a ON (a.id = r.archive_id)
                            WHERE r.trans_id = %s AND {_VISIBLE} ORDER BY r.id""", (trans_id, login))
            out = [self._row(r) for r in cur.fetchall()]
            for a in out:
                cur.execute(f"""SELECT DISTINCT r.trans_id, r.formname FROM reference r
                                WHERE r.archive_id = %s AND r.trans_id <> %s AND {_VISIBLE}
                                ORDER BY r.trans_id""", (a.archive_id, trans_id, login))
                for o in cur.fetchall():
                    h = describe(cur, o["formname"] or "", o["trans_id"])
                    if h:
                        a.elsewhere.append(h)
            return out

    def get(self, archive_id: int) -> Tuple[Attachment, bytes]:
        """Bijlage met inhoud (Form::get_reference); NotFoundError als hij niet bestaat of
        vertrouwelijk is voor een ander. Een los archiefstuk (zonder verwijzing) mag iedereen openen."""
        login = self.config.short_login
        with _db.cursor(self.conn) as cur:
            # ook een los archiefstuk zonder verwijzing (rd.pl "Document toevoegen"; zichtbaar voor iedereen)
            cur.execute(f"""SELECT r.id, r.trans_id, a.id AS archive_id, r.description, r.formname, r.login,
                                   a.filename
                            FROM archive a LEFT JOIN reference r ON (r.archive_id = a.id)
                            WHERE a.id = %s AND (r.id IS NULL OR {_VISIBLE}) ORDER BY r.id LIMIT 1""",
                        (archive_id, login))
            r = cur.fetchone()
            if not r:
                raise NotFoundError(f"Attachment {archive_id} not found")
            cur.execute("SELECT bt FROM archivedata WHERE archive_id = %s ORDER BY rn", (archive_id,))
            data = decode(x["bt"] for x in cur.fetchall())
        return self._row(r), data

    def add(self, trans_id: int, formname: str, filename: str, data: bytes, description: Optional[str] = None,
            confidential: bool = False) -> Attachment:
        """Bestand bij een bestaande boeking, order/offerte, relatie of artikel zetten (RD::save_document
        met tmpfile). Het record zelf blijft ongemoeid (ook in een afgesloten periode of bij een gesloten
        order, RB6)."""
        table = FORMNAMES.get(formname)
        if not table:
            raise ValueError(f"Unknown form: {formname}")
        login = self.config.short_login
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            _kind, prepared = prepare(filename, data, max_size(cur))
            if not record_exists(cur, formname, trans_id):
                raise NotFoundError(f"{table} {trans_id} not found")
            if duplicates(cur, login, sha256(prepared), trans_id).here:
                raise ValidationError("Dit bestand hangt hier al als bijlage.")
            archive_id = store(cur, filename, prepared, login)
            cur.execute("SELECT filename FROM archive WHERE id = %s", (archive_id,))
            name = cur.fetchone()["filename"]
            cur.execute("""INSERT INTO reference (code, trans_id, description, archive_id, login, formname, folder)
                           VALUES (NULL, %s, %s, %s, %s, %s, NULL) RETURNING id""",
                        (trans_id, (description or "").strip()[:200] or name, archive_id,
                         login if confidential else "", formname))
            ref_id = cur.fetchone()["id"]
        return Attachment(id=ref_id, trans_id=trans_id, archive_id=archive_id,
                          description=(description or "").strip()[:200] or name, filename=name,
                          formname=formname, confidential=confidential)

    def delete(self, reference_id: int) -> Attachment:
        """Verwijzing weghalen; het archiefstuk gaat mee als er geen andere verwijzing meer naar
        wijst (RD::delete_document)."""
        login = self.config.short_login
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute(f"""SELECT r.*, a.filename FROM reference r JOIN archive a ON (a.id = r.archive_id)
                            WHERE r.id = %s AND {_VISIBLE} FOR UPDATE OF r""", (reference_id, login))
            r = cur.fetchone()
            if not r:
                raise NotFoundError(f"Attachment {reference_id} not found")
            cur.execute("DELETE FROM reference WHERE id = %s", (reference_id,))
            cur.execute("SELECT count(*) AS n FROM reference WHERE archive_id = %s", (r["archive_id"],))
            if cur.fetchone()["n"] == 0:
                cur.execute("DELETE FROM archive WHERE id = %s", (r["archive_id"],))
        return self._row(r)

    def overview(self, *, text: str = "", kind: str = "", linked: str = "", datefrom=None, dateto=None,
                 hide_formnames: Iterable[str] = (), limit: int = 1000,
                 undated: bool = False) -> Tuple[List[OverviewRow], int, int]:
        """Alle bijlagen die deze gebruiker mag zien (RB3; rd.pl "Documenten zoeken"): ``text`` in omschrijving of
        bestandsnaam, ``kind`` een sleutel van KINDS of "los"/"overig", ``linked`` in nummer of naam van het
        record, toegevoegd tussen ``datefrom`` en ``dateto``. ``hide_formnames``: soorten waarvan de module uit
        staat. Bijlagen zonder datum (uit SQL-Ledger) vallen bij een datumfilter weg, tenzij ``undated`` (F43).
        Geeft (regels, totaal, aantal weggelaten zonder datum); hoogstens ``limit`` regels, nieuwste eerst."""
        login = self.config.short_login
        where = [f"(r.id IS NULL OR {_VISIBLE})"]
        params: list = [login]
        hide = sorted(set(hide_formnames))
        if hide:
            where.append("(r.formname IS NULL OR r.formname <> ALL(%s))")
            params.append(hide)
        if text.strip():
            where.append("(r.description ILIKE %s OR a.filename ILIKE %s)")
            params += [f"%{text.strip()}%"] * 2
        if kind == "los":
            where.append("r.id IS NULL")
        elif kind == "overig":
            known = sorted({f for fs in KINDS.values() for f in fs})
            where.append("r.id IS NOT NULL AND (r.formname IS NULL OR r.formname <> ALL(%s))")
            params.append(known)
        elif kind in KINDS:
            where.append("r.formname = ANY(%s)")
            params.append(list(KINDS[kind]))
        if linked.strip():
            where.append(f"({_HOLDER_NUMBER} ILIKE %s OR {_HOLDER_NAME} ILIKE %s)")
            params += [f"%{linked.strip()}%"] * 2
        dates, dparams = [], []
        if datefrom:
            dates.append("x.added >= %s")
            dparams.append(datefrom)
        if dateto:
            dates.append("x.added < %s::date + 1")
            dparams.append(dateto)
        frm = f"""FROM archive a LEFT JOIN reference r ON (r.archive_id = a.id)
                  LEFT JOIN x_archive_added x ON (x.archive_id = a.id) {_HOLDER_JOINS}"""
        hidden = 0
        with _db.cursor(self.conn) as cur:
            from .extensions import ensure_schema
            ensure_schema(self.conn)
            if dates and not undated:
                cur.execute(f"SELECT count(*) AS n {frm} WHERE {' AND '.join(where)} AND x.added IS NULL", params)
                hidden = cur.fetchone()["n"]
            if dates:
                clause = " AND ".join(dates)
                where.append(f"(x.added IS NULL OR ({clause}))" if undated else clause)
                params += dparams
            base = f"{frm} WHERE {' AND '.join(where)}"
            cur.execute(f"SELECT count(*) AS n {base}", params)
            total = cur.fetchone()["n"]
            cur.execute(f"""SELECT a.id AS archive_id, a.filename, r.id AS reference_id, r.trans_id, r.formname,
                                   r.description, r.login, x.added, x.login AS added_by,
                                   {_HOLDER_NUMBER} AS number, {_HOLDER_NAME} AS name
                            {base} ORDER BY x.added DESC NULLS LAST, a.id DESC, r.id LIMIT %s""", params + [limit])
            rows = [OverviewRow(archive_id=r["archive_id"], filename=r["filename"] or "",
                                reference_id=r["reference_id"], trans_id=r["trans_id"], formname=r["formname"] or "",
                                description=r["description"] or "", confidential=bool(r["login"]),
                                number=r["number"] or "", name=r["name"] or "", added=r["added"],
                                added_by=r["added_by"] or "") for r in cur.fetchall()]
        return rows, total, hidden

    def delete_loose(self, archive_id: int) -> str:
        """Los archiefstuk (zonder verwijzing) weggooien; geeft de bestandsnaam. NotFoundError als het niet
        bestaat of nog ergens aan hangt."""
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("""SELECT filename FROM archive a WHERE id = %s
                           AND NOT EXISTS (SELECT 1 FROM reference r WHERE r.archive_id = a.id) FOR UPDATE""",
                        (archive_id,))
            r = cur.fetchone()
            if not r:
                raise NotFoundError(f"Attachment {archive_id} not found")
            cur.execute("DELETE FROM archive WHERE id = %s", (archive_id,))
        return r["filename"] or ""
