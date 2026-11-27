"""ICP-opgaaf (opgaaf intracommunautaire prestaties, ICP_ANALYSE.md ICP1–ICP11).

De opgaaf volgt de btw-aangifte (ICP3): precies de verkoopboekingen die in rubriek 3B tellen (``VatReturns.report``),
per afnemer (btw-nummer) gesplitst in leveringen, diensten en ABC-leveringen (vereenvoudigde regeling).

* **Btw-nummer** (ICP4): ``customer.taxnumber``; landcode = de eerste twee letters (EL Griekenland, XI
  Noord-Ierland), spaties/punten/streepjes weg, vorm gecontroleerd per land; een NL-nummer hoort er niet in. Geen
  online VIES-controle. Een klant op rubriek 3B (rubriek bij de klant of een btw-rekening op 3B) moet een geldig
  nummer hebben: zonder nummer kan de opgaaf niet (``require_vat_number``, ook bij leveranciers op rubriek 4B).
* **Leveringen of diensten** (ICP5/ICP6): factuur met artikelen per regel (artikel of samengesteld = levering,
  dienst = dienst); het 3B-bedrag van de boeking wordt naar die verhouding verdeeld. Zonder artikelen: de ICP-soort
  van de klant (``x_icp_customer``: leveringen (standaard), diensten of ABC). Bij een ABC-klant gaan de leveringen
  naar de ABC-lijst.
* **Bedragen** (ICP7): per afnemer en soort de grondslag in de standaardvaluta (creditnota's verrekend), per regel
  rekenkundig afgerond op hele euro's; afnemers met hetzelfde nummer samengevoegd.
* **Vastleggen** (ICP8): ``x_icp_return`` met de afgeronde bedragen; samen met de btw-boeking als de tijdvakken
  gelijk zijn, anders apart. Later gewijzigde boekingen geven per afnemer het verschil (zoals BA7).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import psycopg

from . import _db
from .amounts import round_amount, to_decimal
from .errors import ValidationError
from .extensions import ensure_schema
from .vatreturn import Period, VatReport, VatReturns, free_period, last_closed, period_of

ZERO = Decimal(0)

PERIOD_SETTING = "icp_period"
VAT_PERIOD_SETTING = "vat_return_period"        # sqlledger.dashboard.VAT_SETTING
ICP_PERIODS = ("month", "quarter")
#: ICP-soort bij de klant (ICP5/ICP6); '' = leveringen (standaard)
CUSTOMER_KINDS = ("goods", "services", "abc")
COLUMNS = ("goods", "services", "abc")

#: vorm van het btw-nummer per land, zonder landcode (VIES; XI = Noord-Ierland)
VAT_FORMATS: Dict[str, str] = {
    "AT": r"U\d{8}", "BE": r"[01]\d{9}", "BG": r"\d{9,10}", "CY": r"\d{8}[A-Z]", "CZ": r"\d{8,10}",
    "DE": r"\d{9}", "DK": r"\d{8}", "EE": r"\d{9}", "EL": r"\d{9}", "ES": r"[A-Z0-9]\d{7}[A-Z0-9]",
    "FI": r"\d{8}", "FR": r"[A-HJ-NP-Z0-9]{2}\d{9}", "HR": r"\d{11}", "HU": r"\d{8}",
    "IE": r"\d{7}[A-W][A-IW]?|\d[A-Z+*]\d{5}[A-W]", "IT": r"\d{11}", "LT": r"\d{9}|\d{12}", "LU": r"\d{8}",
    "LV": r"\d{11}", "MT": r"\d{8}", "NL": r"\d{9}B\d{2}", "PL": r"\d{10}", "PT": r"\d{9}", "RO": r"[1-9]\d{1,9}",
    "SE": r"\d{10}01", "SI": r"\d{8}", "SK": r"\d{10}", "XI": r"\d{9}|\d{12}|GD\d{3}|HA\d{3}",
}
#: problemen met een btw-nummer (teksten in de webapp)
PROBLEMS = ("missing", "no_country", "greece", "not_eu", "domestic", "format")


def normalize_vat(text: Optional[str]) -> str:
    """Hoofdletters, zonder spaties, punten en streepjes."""
    return re.sub(r"[\s.\-/]", "", (text or "").upper())


def check_vat(text: Optional[str]) -> Tuple[str, str, str]:
    """(landcode, nummer zonder landcode, probleem); probleem '' = in orde (zie ``PROBLEMS``)."""
    n = normalize_vat(text)
    if not n:
        return "", "", "missing"
    country, rest = n[:2], n[2:]
    if not country.isalpha():
        return "", n, "no_country"
    if country == "GR":
        return country, rest, "greece"
    if country not in VAT_FORMATS:
        return country, rest, "not_eu"
    if country == "NL":
        return country, rest, "domestic"
    if not re.fullmatch(VAT_FORMATS[country], rest):
        return country, rest, "format"
    return country, rest, ""


def free_period_key(key: str) -> Optional[Period]:
    """Tijdvak uit ``<van>_<tot>``; None als dat geen geldig tijdvak is."""
    try:
        fromtext, totext = key.split("_", 1)
        return free_period(date.fromisoformat(fromtext), date.fromisoformat(totext))
    except (ValueError, ValidationError):
        return None


def round_euro(v: Decimal) -> Decimal:
    """ICP7: rekenkundig op hele euro's (ICP kent geen 'in uw voordeel')."""
    return v.quantize(Decimal(1), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------- resultaat

@dataclass
class IcpTrans:
    trans_id: int
    number: str
    transdate: date
    goods: Decimal = ZERO
    services: Decimal = ZERO
    abc: Decimal = ZERO


@dataclass
class IcpLine:
    """Eén afnemer (btw-nummer); bij een probleem één regel per klant."""
    key: str                                # landcode + nummer, of '?<klant-id>'
    country: str
    number: str
    problem: str
    customers: List[Tuple[int, str]] = field(default_factory=list)       # (id, naam)
    exact: Dict[str, Decimal] = field(default_factory=lambda: {c: ZERO for c in COLUMNS})
    rounded: Dict[str, Decimal] = field(default_factory=lambda: {c: ZERO for c in COLUMNS})
    transactions: List[IcpTrans] = field(default_factory=list)

    @property
    def name(self) -> str:
        return ", ".join(n for _i, n in self.customers)


@dataclass
class IcpDifference:
    lines: List[Dict[str, Any]]             # key, country, number, name, old{...}, now{...}


@dataclass
class IcpReport:
    period: Period
    lines: List[IcpLine]                    # leveringen/diensten (3a van het portaal) en ABC samen
    totals_exact: Dict[str, Decimal]
    totals: Dict[str, Decimal]              # som van de afgeronde regels
    box3b_exact: Decimal                    # grondslag rubriek 3B (exact)
    box3b: Decimal                          # afgerond zoals op de aangifte
    unassigned: List[Dict[str, Any]]        # boekingen in 3B zonder klant (memoriaal, inkoop)
    stored: Optional[Dict[str, Any]] = None
    difference: Optional[IcpDifference] = None

    @property
    def problems(self) -> List[IcpLine]:
        return [ln for ln in self.lines if ln.problem]

    @property
    def total_exact(self) -> Decimal:
        return sum(self.totals_exact.values(), ZERO)

    @property
    def unexplained(self) -> Decimal:
        """ICP3: verschil tussen rubriek 3B en de opgaaf (exact); alleen boekingen zonder afnemer."""
        return self.box3b_exact - self.total_exact

    def line(self, key: str) -> Optional[IcpLine]:
        return next((ln for ln in self.lines if ln.key == key), None)

    def has_amounts(self) -> bool:
        return any(ln.exact[c] for ln in self.lines for c in COLUMNS)


# ---------------------------------------------------------------------------- de opgaaf

class IcpReturns:
    def __init__(self, conn: psycopg.Connection, config=None):
        self.conn = conn
        self.config = config

    def _setting(self, key: str) -> str:
        ensure_schema(self.conn)
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT value FROM x_setting WHERE key = %s", (key,))
            r = cur.fetchone()
        return r["value"] if r else ""

    # -------------------------------------------------------------- instellingen (ICP1/ICP2)

    def configured(self) -> bool:
        """ICP1: een btw-rekening of een klant staat op rubriek 3B (alleen bij de Nederlandse aangifte)."""
        ensure_schema(self.conn)
        if VatReturns(self.conn, self.config).country != "NL":
            return False
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT EXISTS (SELECT 1 FROM x_vat_box WHERE box = '3B' AND country = 'NL')
                               OR EXISTS (SELECT 1 FROM x_vat_customer_box WHERE box = '3B' AND country = 'NL')
                           AS on""")
            return bool(cur.fetchone()["on"])

    def period_kind(self) -> str:
        """ICP2: eigen instelling, standaard gelijk aan het btw-tijdvak (btw per jaar of geen → kwartaal)."""
        v = self._setting(PERIOD_SETTING)
        if v in ICP_PERIODS:
            return v
        return "month" if self._setting(VAT_PERIOD_SETTING) == "month" else "quarter"

    def save_period(self, value: str, login: Optional[str] = None) -> bool:
        if value not in ICP_PERIODS:
            raise ValidationError("Onbekend tijdvak voor de ICP-opgaaf.")
        if value == self.period_kind() and self._setting(PERIOD_SETTING) == value:
            return False
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("""INSERT INTO x_setting (key, value, login) VALUES (%s, %s, %s)
                           ON CONFLICT (key) DO UPDATE SET value = excluded.value, login = excluded.login,
                                                           updated = now()""", (PERIOD_SETTING, value, login))
        return True

    def customer_kind(self, customer_id: int) -> str:
        ensure_schema(self.conn)
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT kind FROM x_icp_customer WHERE customer_id = %s", (customer_id,))
            r = cur.fetchone()
        return r["kind"] if r else ""

    def save_customer_kind(self, customer_id: int, kind: str, login: Optional[str] = None) -> bool:
        """'' of 'goods' = leveringen (standaard); geeft True als het gewijzigd is."""
        kind = "" if kind == "goods" else kind
        if kind and kind not in CUSTOMER_KINDS:
            raise ValidationError("Onbekende ICP-soort.")
        if kind == self.customer_kind(customer_id):
            return False
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            if kind:
                cur.execute("""INSERT INTO x_icp_customer (customer_id, kind, login) VALUES (%s, %s, %s)
                               ON CONFLICT (customer_id) DO UPDATE SET kind = excluded.kind, login = excluded.login,
                                                                        updated = now()""",
                            (customer_id, kind, login))
            else:
                cur.execute("DELETE FROM x_icp_customer WHERE customer_id = %s", (customer_id,))
        return True

    def needs_vat_number(self, vc: str, vat_box: str, taxaccounts: Iterable[str]) -> str:
        """De rubriek waardoor deze relatie een EU-btw-nummer nodig heeft ('' = niet nodig): een klant op 3B
        (rubriek bij de klant of een btw-rekening op 3B), een leverancier met een btw-rekening op 4B."""
        want = "3B" if vc == "customer" else "4B"
        if vc == "customer" and vat_box == "3B":
            return want
        accnos = [a for a in taxaccounts if a]
        if not accnos:
            return ""
        ensure_schema(self.conn)
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT 1 FROM x_vat_box b JOIN chart c ON (c.id = b.chart_id AND b.country = 'NL')
                           WHERE b.box = %s AND c.accno = ANY(%s) LIMIT 1""", (want, accnos))
            return want if cur.fetchone() else ""

    def require_vat_number(self, vc: str, taxnumber: str, vat_box: str, taxaccounts: Iterable[str]) -> None:
        """Weigeren als een EU-klant (3B) of EU-leverancier (4B) geen geldig buitenlands btw-nummer heeft."""
        box = self.needs_vat_number(vc, vat_box, taxaccounts)
        if not box:
            return
        _c, _n, problem = check_vat(taxnumber)
        if not problem:
            return
        if problem == "missing":
            raise ValidationError(f"Een relatie in rubriek {box} moet een btw-nummer hebben (met landcode, "
                                  f"bv. DE123456789).")
        if problem == "domestic":
            raise ValidationError(f"Een relatie in rubriek {box} moet een btw-nummer uit een ander EU-land hebben, "
                                  f"geen Nederlands nummer.")
        if problem == "greece":
            raise ValidationError("Gebruik voor Griekenland de landcode EL in het btw-nummer, niet GR.")
        number = normalize_vat(taxnumber)
        raise ValidationError(f"Het btw-nummer {number} is geen geldig EU-btw-nummer (landcode + nummer, "
                              f"bv. DE123456789).")

    # -------------------------------------------------------------- berekenen (ICP3–ICP7)

    def report(self, period: Period, vat: Optional[VatReport] = None) -> IcpReport:
        ensure_schema(self.conn)
        vr = VatReturns(self.conn, self.config, country="NL")
        vat = vat or vr.report(period)
        trans = vat.box_transactions("3B")
        boxes = vr.boxes()
        box3b_accounts = {c for c, b in boxes.items() if b == "3B"}
        ar_ids = [t.trans_id for t in trans if t.kind == "ar"]
        with _db.cursor(self.conn) as cur:
            heads: Dict[int, Dict[str, Any]] = {}
            taxes: Dict[int, set] = {}
            lines: Dict[int, List[Tuple[Decimal, bool, frozenset]]] = {}
            if ar_ids:
                cur.execute("""SELECT a.id, a.customer_id, c.name, c.taxnumber, k.kind, b.box AS cbox
                               FROM ar a JOIN customer c ON (c.id = a.customer_id)
                               LEFT JOIN x_icp_customer k ON (k.customer_id = a.customer_id)
                               LEFT JOIN x_vat_customer_box b ON (b.customer_id = a.customer_id
                                                                  AND b.country = 'NL')
                               WHERE a.id = ANY(%s)""", (ar_ids,))
                heads = {r["id"]: r for r in cur.fetchall()}
                cur.execute("""SELECT DISTINCT trans_id, chart_id FROM acc_trans
                               WHERE trans_id = ANY(%s) AND chart_id IN (SELECT chart_id FROM tax)""", (ar_ids,))
                for r in cur.fetchall():
                    taxes.setdefault(r["trans_id"], set()).add(r["chart_id"])
                cur.execute("""SELECT i.trans_id, i.qty, i.sellprice,
                                      (p.inventory_accno_id IS NOT NULL OR coalesce(p.assembly, false)) AS goods,
                                      array(SELECT pt.chart_id FROM partstax pt WHERE pt.parts_id = i.parts_id) AS t
                               FROM invoice i JOIN parts p ON (p.id = i.parts_id)
                               WHERE i.trans_id = ANY(%s) AND NOT coalesce(i.assemblyitem, false)""", (ar_ids,))
                for r in cur.fetchall():
                    amount = to_decimal(r["qty"] or 0) * to_decimal(r["sellprice"] or 0)
                    lines.setdefault(r["trans_id"], []).append((amount, bool(r["goods"]), frozenset(r["t"] or ())))

        result: Dict[str, IcpLine] = {}
        unassigned: List[Dict[str, Any]] = []
        for t in trans:
            base = t.boxes["3B"][0]
            h = heads.get(t.trans_id)
            if t.kind != "ar" or not h:
                unassigned.append({"trans_id": t.trans_id, "kind": t.kind, "number": t.number,
                                   "transdate": t.transdate, "name": t.name, "base": base})
                continue
            split = _split(base, lines.get(t.trans_id) if t.invoice else None, taxes.get(t.trans_id, set()),
                           box3b_accounts, h["cbox"] == "3B", h["kind"] or "")
            country, number, problem = check_vat(h["taxnumber"])
            key = f"?{h['customer_id']}" if problem else country + number
            ln = result.get(key)
            if ln is None:
                ln = result[key] = IcpLine(key, country, number, problem)
            if h["customer_id"] not in {i for i, _n in ln.customers}:
                ln.customers.append((h["customer_id"], h["name"] or ""))
            it = IcpTrans(t.trans_id, t.number, t.transdate, **split)
            ln.transactions.append(it)
            for c in COLUMNS:
                ln.exact[c] += split[c]

        out = sorted(result.values(), key=lambda x: (bool(x.problem), x.country, x.number, x.key))
        totals_exact = {c: ZERO for c in COLUMNS}
        totals = {c: ZERO for c in COLUMNS}
        for ln in out:
            for c in COLUMNS:
                ln.rounded[c] = round_euro(ln.exact[c])
                totals_exact[c] += ln.exact[c]
                totals[c] += ln.rounded[c]
        rep = IcpReport(period=period, lines=out, totals_exact=totals_exact, totals=totals,
                        box3b_exact=vat.boxes["3B"].base_exact, box3b=vat.boxes["3B"].base, unassigned=unassigned)
        rep.stored = self.stored(period)
        if rep.stored:
            rep.difference = compare(rep.stored["snapshot"], rep)
        return rep

    # -------------------------------------------------------------- vastleggen (ICP8)

    def register(self, rep: IcpReport, gl_id: Optional[int] = None, login: Optional[str] = None) -> None:
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("""INSERT INTO x_icp_return (fromdate, todate, gl_id, snapshot, login)
                           VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (fromdate, todate) DO UPDATE SET gl_id = excluded.gl_id,
                               snapshot = excluded.snapshot, login = excluded.login, created = now()""",
                        (rep.period.fromdate, rep.period.todate, gl_id, json.dumps(snapshot(rep)), login))

    def register_with_vat(self, vat: VatReport, gl_id: int, login: Optional[str] = None) -> bool:
        """Na de btw-boeking: de ICP-opgaaf van hetzelfde tijdvak meteen vastleggen (ICP8); False als het
        ICP-tijdvak anders is of er niets in staat."""
        if not self.configured():
            return False
        p = vat.period
        mine = period_of(self.period_kind(), p.todate)
        if (mine.fromdate, mine.todate) != (p.fromdate, p.todate):
            return False
        rep = self.report(p, vat)
        if not rep.lines and not rep.stored:
            return False
        self.register(rep, gl_id, login)
        return True

    def stored(self, period: Period) -> Optional[Dict[str, Any]]:
        ensure_schema(self.conn)
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT r.*, g.reference FROM x_icp_return r LEFT JOIN gl g ON (g.id = r.gl_id)
                           WHERE r.fromdate = %s AND r.todate = %s""", (period.fromdate, period.todate))
            return cur.fetchone()

    def due(self, today: date) -> Optional[Dict[str, Any]]:
        """Dashboard (ICP1): herinnering voor het laatst afgesloten ICP-tijdvak als het bedragen heeft, niet is
        vastgelegd en de termijn (einde van de maand na het tijdvak, zoals de btw) nog niet voorbij is."""
        if not self.configured():
            return None
        p = last_closed(self.period_kind(), today)
        deadline = period_of("month", date.fromordinal(p.todate.toordinal() + 1)).todate
        if today > deadline or self.stored(p):        # herinnering: na de termijn vervalt hij
            return None
            return None
        rep = self.report(p)
        if not rep.has_amounts():
            return None
        return {"period": p, "deadline": deadline, "count": len(rep.lines)}


def _split(base: Decimal, lines: Optional[List[Tuple[Decimal, bool, frozenset]]], taxes: set,
           box3b_accounts: set, customer_3b: bool, kind: str) -> Dict[str, Decimal]:
    """ICP5/ICP6: het 3B-bedrag van één boeking over leveringen, diensten en ABC."""
    goods_total = services_total = ZERO
    for amount, goods, charts in lines or ():
        hit = charts & taxes
        if (hit & box3b_accounts) or (not hit and customer_3b):
            if goods:
                goods_total += amount
            else:
                services_total += amount
    out = {c: ZERO for c in COLUMNS}
    if goods_total or services_total:
        goods = round_amount(base * goods_total / (goods_total + services_total), 2)
        out["abc" if kind == "abc" else "goods"] = goods
        out["services"] = base - goods
    else:
        out[kind or "goods"] = base
    return out


def snapshot(rep: IcpReport) -> Dict[str, Any]:
    return {"lines": {ln.key: {"country": ln.country, "number": ln.number, "name": ln.name,
                               **{c: str(ln.rounded[c]) for c in COLUMNS}} for ln in rep.lines},
            "totals": {c: str(v) for c, v in rep.totals.items()}}


def compare(snap: Mapping[str, Any], rep: IcpReport) -> Optional[IcpDifference]:
    """ICP8: vastgelegde bedragen per afnemer tegen die van nu; None als alles gelijk is."""
    old = snap.get("lines", {})
    now = {ln.key: ln for ln in rep.lines}
    out: List[Dict[str, Any]] = []
    for key in sorted(set(old) | set(now)):
        o = old.get(key) or {}
        n = now.get(key)
        ov = {c: to_decimal(o.get(c, "0")) for c in COLUMNS}
        nv = dict(n.rounded) if n else {c: ZERO for c in COLUMNS}
        if ov != nv:
            out.append({"key": key, "country": n.country if n else o.get("country", ""),
                        "number": n.number if n else o.get("number", ""),
                        "name": n.name if n else o.get("name", ""), "old": ov, "now": nv})
    return IcpDifference(out) if out else None


def vat_number_problems(conn: psycopg.Connection, vat: VatReport) -> List[Dict[str, Any]]:
    """Btw-aangifte: klanten in rubriek 3B en leveranciers in rubriek 4B zonder geldig EU-btw-nummer."""
    want = {"3B": ("ar", "customer"), "4B": ("ap", "vendor")}
    ids: Dict[str, set] = {"3B": set(), "4B": set()}
    for box in want:
        ids[box] = {t.trans_id for t in vat.box_transactions(box) if t.kind == want[box][0]}
    out: List[Dict[str, Any]] = []
    with _db.cursor(conn) as cur:
        for box, (table, vc) in want.items():
            if not ids[box]:
                continue
            cur.execute(f"""SELECT DISTINCT n.id, n.name, n.taxnumber FROM {table} a
                            JOIN {vc} n ON (n.id = a.{vc}_id) WHERE a.id = ANY(%s) ORDER BY n.name""",
                        (list(ids[box]),))
            for r in cur.fetchall():
                problem = check_vat(r["taxnumber"])[2]
                if problem:
                    out.append({"vc": vc, "id": r["id"], "name": r["name"] or "", "box": box, "problem": problem,
                                "taxnumber": normalize_vat(r["taxnumber"])})
    return out
