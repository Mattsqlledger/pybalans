"""Afletteren van een rekening tegen een (bank)afschrift (port van SL/RC.pm en
bin/mozilla/rc.pl, fase 2 beslispunten L1–L5).

Het scherm toont per rekening en periode het beginsaldo, de regels uit
``acc_trans`` met een vinkje (afgeletterd = ``acc_trans.cleared`` gevuld), het
afgeletterde saldo en het verschil met het ingevoerde afschriftsaldo. Opslaan
kan alleen bij verschil 0; de afletterdatum is de afschriftdatum (tot-datum).

Een regel aanwijzen: ``acc_trans`` heeft geen sleutel (``acc_trans.id`` is niet
uniek en vaak leeg). Net als ``RC::reconcile`` wordt een regel aangewezen met
(trans_id, transdate, source) op de rekening (``chart_id``): een **sleutel**.
Alle regels met dezelfde sleutel worden dus samen afgeletterd, ook regels die
apart op het scherm staan (bv. twee gelijke betalingen met hetzelfde kenmerk op
één boeking, of de koersverschilregel van een betaling). Daarom krijgen
schermregels die een sleutel delen hetzelfde ``group``: één vinkje voor allemaal.

Verschillen met Perl (README "Bewuste afwijkingen", 95–104): geparametriseerde
SQL; UNION ALL (Perl's UNION voegde identieke regels samen); samenvoegen per
kenmerk verzamelt de boekingen als verzameling (Perl zocht een id als
deelstring: 12 in "123" werd niet afgeletterd); het verschil rekent de server
uit de vinkjes (Perl controleerde een verborgen, geformatteerd veld); bestaande
afletterdatums worden niet overschreven; zonder koersverschilrekening geen
SQL-fout; zonder tot-datum telt het afschriftsaldo alleen afgeletterde regels.
Elke ronde komt in ``x_reconciliation`` (L4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import psycopg

from . import _db, support
from .amounts import round_amount, to_decimal
from .config import UserConfig
from .errors import NotFoundError, OutOfBalanceError, ValidationError
from .extensions import ensure_schema

ZERO = Decimal(0)

#: (trans_id, transdate, source) — source leeg en NULL zijn gelijk (RC::reconcile)
Key = Tuple[int, date, str]


def _r(value, places: int) -> Decimal:
    """Afronden zonder -0 (0 * -1 op een activarekening)."""
    return round_amount(value, places) + ZERO


def key_token(key: Key) -> str:
    """Sleutel als veilige tekst (formulierveld): ``trans_id.jjjjmmdd.hex(source)``."""
    tid, d, src = key
    return f"{int(tid)}.{d:%Y%m%d}.{(src or '').encode('utf-8').hex()}"


def parse_token(token: str) -> Key:
    try:
        tid, d, hx = token.split(".")
        return int(tid), date(int(d[:4]), int(d[4:6]), int(d[6:8])), bytes.fromhex(hx).decode("utf-8")
    except (ValueError, TypeError, UnicodeDecodeError):
        raise ValidationError(f"Invalid line key: {token[:60]}")


def parse_tokens(text: str) -> List[Key]:
    """Een schermregel draagt haar sleutels als ``token token ...``."""
    return [parse_token(t) for t in (text or "").split() if t]


@dataclass
class StatementLine:
    """Eén regel op het afletterscherm (na eventueel samenvoegen per kenmerk)."""

    transdate: date
    source: str
    names: List[str]
    amount: Decimal                 # som acc_trans.amount (negatief = debet)
    fx: bool                        # koersverschilregel (eerste regel)
    cleared: Optional[date]         # afletterdatum van de eerste regel (zoals rc.pl)
    keys: List[Key]
    trans_ids: List[int]
    group: int = 0                  # schermregels met een gedeelde sleutel: zelfde groep
    checked: bool = False
    balance: Decimal = ZERO         # lopend saldo (teken van de rekening)
    #: niet aangevinkt, maar een deel van de regels is al afgeletterd op of vóór de afschriftdatum
    #: (samengevoegde regel met verschillende status); dat deel telt mee en blijft staan
    partial: Decimal = ZERO
    partly: bool = False
    # intern: per acc_trans-regel afletterdatum, koersverschil en bedrag
    _cleared_all: List[Optional[date]] = field(default_factory=list, repr=False)
    _fx_all: List[bool] = field(default_factory=list, repr=False)
    _amount_all: List[Decimal] = field(default_factory=list, repr=False)

    @property
    def debit(self) -> Decimal:
        return -self.amount if self.amount < 0 else ZERO

    @property
    def credit(self) -> Decimal:
        return self.amount if self.amount >= 0 else ZERO

    @property
    def tokens(self) -> str:
        return " ".join(key_token(k) for k in self.keys)


@dataclass
class Statement:
    accno: str
    description: str
    category: str
    chart_id: int
    fromdate: Optional[date]
    todate: Optional[date]
    statementdate: date             # afletterdatum (rc.pl recdate): tot-datum of vandaag
    summary: bool
    fx: bool
    precision: int
    beginbalance: Decimal           # teken van de rekening (activa: debet positief)
    lines: List[StatementLine]
    statementbalance: Decimal       # voorgesteld afschriftsaldo (afgeletterd t/m tot-datum)
    report: bool = False
    previous: Optional[Dict[str, Any]] = None   # vorige afletterronde (x_reconciliation)
    warnings: List[str] = field(default_factory=list)

    @property
    def sign(self) -> int:
        return -1 if self.category == "A" else 1

    @property
    def total_debit(self) -> Decimal:
        return sum((ln.debit for ln in self.lines), ZERO)

    @property
    def total_credit(self) -> Decimal:
        return sum((ln.credit for ln in self.lines), ZERO)

    def groups(self) -> Dict[int, List[StatementLine]]:
        out: Dict[int, List[StatementLine]] = {}
        for ln in self.lines:
            out.setdefault(ln.group, []).append(ln)
        return out

    def selected_groups(self, selected: Iterable[Key]) -> Set[int]:
        sel = set(selected)
        return {ln.group for ln in self.lines if sel.intersection(ln.keys)}

    def cleared_total(self, groups: Optional[Set[int]] = None) -> Decimal:
        """Som van wat na opslaan afgeletterd is (teken van de rekening); standaard de huidige
        vinkjes. Aangevinkt: het hele bedrag. Niet aangevinkt en helemaal afgeletterd: wordt
        uitgevinkt, telt niet. Niet aangevinkt en deels afgeletterd: dat deel blijft staan en telt."""
        if groups is None:
            groups = {ln.group for ln in self.lines if ln.checked}
        total = sum((ln.amount if ln.group in groups else (ZERO if ln.checked else ln.partial)
                     for ln in self.lines), ZERO)
        return _r(total * self.sign, self.precision)

    def cleared_balance(self, groups: Optional[Set[int]] = None) -> Decimal:
        return _r(self.beginbalance + self.cleared_total(groups), self.precision)

    def difference(self, statementbalance: Optional[Decimal] = None, groups: Optional[Set[int]] = None) -> Decimal:
        sb = self.statementbalance if statementbalance is None else statementbalance
        return _r(self.cleared_balance(groups) - sb, self.precision)


@dataclass
class ReconcileResult:
    id: int                 # x_reconciliation.id
    cleared: int            # acc_trans-regels die een afletterdatum kregen
    uncleared: int          # acc_trans-regels waarvan de afletterdatum is weggehaald
    lines: int              # aangevinkte schermregels


class Reconciliation:
    """Afletteren (RC.pm/rc.pl)."""

    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        self.conn = conn
        self.config = config

    # -------------------------------------------------------------- rekeningen

    def accounts(self, alle_balans: bool = False) -> List[Dict[str, Any]]:
        """Rekeningen om af te letteren. Standaard de betaalrekeningen (koppeling
        ``AR_paid``/``AP_paid``); met ``alle_balans`` alle open balansrekeningen.
        (``RC::paymentaccounts`` toonde alle open boekrekeningen, ook W&V.)"""
        cond = "c.category IN ('A', 'L', 'Q')" if alle_balans else "c.link LIKE '%%\\_paid%%'"
        with _db.cursor(self.conn) as cur:
            cur.execute(f"""SELECT c.id, c.accno, COALESCE(l.description, c.description) AS description,
                                   c.category, c.link
                            FROM chart c
                            LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %s)
                            WHERE c.charttype = 'A' AND c.closed = '0' AND {cond}
                            ORDER BY c.accno""", (self.config.countrycode,))
            return cur.fetchall()

    def history(self, accno: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Laatste afletterrondes van een rekening (nieuwste eerst)."""
        with _db.cursor(self.conn) as cur:
            chart = self._chart(cur, accno)
            ensure_schema(self.conn)
            cur.execute("""SELECT * FROM x_reconciliation WHERE chart_id = %s
                           ORDER BY statementdate DESC, id DESC LIMIT %s""", (chart["id"], limit))
            return cur.fetchall()

    # -------------------------------------------------------------- scherm

    def statement(self, accno: str, fromdate: Optional[date] = None, todate: Optional[date] = None,
                  summary: bool = True, fx: bool = True) -> Statement:
        """Het afletterscherm (``RC::payment_transactions`` + ``rc.pl get_payments``)."""
        return self._statement(accno, fromdate, todate, summary, fx, report=False)

    def report(self, accno: str, fromdate: Optional[date] = None, todate: Optional[date] = None,
               summary: bool = True, fx: bool = True) -> Statement:
        """Afgeletterde posten met afletterdatum in de periode (rc.pl ``report=1``)."""
        return self._statement(accno, fromdate, todate, summary, fx, report=True)

    def _statement(self, accno, fromdate, todate, summary, fx, report) -> Statement:
        if fromdate and todate and fromdate > todate:
            raise ValidationError("From date is after to date")
        with _db.cursor(self.conn) as cur:
            chart = self._chart(cur, accno)
            precision = _db.get_precision(cur)
            cid = chart["id"]
            ml = -1 if chart["category"] == "A" else 1
            statementdate = todate or date.today()

            def total(where: str, params: tuple) -> Decimal:
                cur.execute(f"""SELECT coalesce(sum(amount::numeric), 0) AS s FROM acc_trans
                                WHERE chart_id = %s AND approved {where}""", (cid, *params))
                return to_decimal(cur.fetchone()["s"])

            fxw = "" if fx else " AND NOT fx_transaction"
            begin = ZERO
            if fromdate and not report:
                begin = total(" AND transdate < %s AND cleared < %s" + fxw, (fromdate, fromdate))
            if todate:
                ending = total(" AND transdate <= %s AND cleared <= %s" + fxw, (todate, todate))
            else:
                # Perl: zonder tot-datum de som van álle regels (ook niet afgeletterd)
                ending = total(" AND cleared IS NOT NULL" + fxw, ())

            params: List[Any] = [cid]
            if report:
                cond = "ac.cleared IS NOT NULL"
                if fromdate:
                    cond += " AND ac.cleared >= %s"
                    params.append(fromdate)
                if todate:
                    cond += " AND ac.cleared <= %s"
                    params.append(todate)
            else:
                a, b = ["TRUE"], None
                if fromdate:
                    a.append("(ac.transdate >= %s OR ac.cleared >= %s)")
                    params += [fromdate, fromdate]
                if todate:
                    a.append("ac.transdate <= %s")
                    params.append(todate)
                cond = "(" + " AND ".join(a) + ")"
                if fromdate:
                    b = "(ac.transdate < %s AND ac.cleared IS NULL)"
                    params.append(fromdate)
                    cond = f"({cond} OR {b})"
            # alle goedgekeurde regels, ook als de boeking geen gl/ar/ap is (Perl: inner join, weg).
            # Volgorde: datum, kenmerk, koersverschil (Perl ORDER BY 1,2,3); daarbinnen vast op
            # bedrag, afletterdatum, boeking, naam — zoals de sortering die Perl's UNION meestal geeft.
            cur.execute(f"""SELECT ac.trans_id, ac.transdate, coalesce(ac.source, '') AS source,
                                   ac.fx_transaction AS fx, ac.amount::numeric AS amount, ac.cleared,
                                   coalesce(g.description, cu.name, v.name) AS description
                            FROM acc_trans ac
                            LEFT JOIN gl g ON g.id = ac.trans_id
                            LEFT JOIN ar a ON a.id = ac.trans_id
                            LEFT JOIN customer cu ON cu.id = a.customer_id
                            LEFT JOIN ap p ON p.id = ac.trans_id
                            LEFT JOIN vendor v ON v.id = p.vendor_id
                            WHERE ac.chart_id = %s AND ac.approved AND {cond}{fxw.replace('fx_', 'ac.fx_')}
                            ORDER BY 2, ac.source, 4, 5, 6, 1, 7""",
                        tuple(params))
            raw = cur.fetchall()
            previous = None
            if not report:
                ensure_schema(self.conn)
                cur.execute("""SELECT * FROM x_reconciliation WHERE chart_id = %s
                               AND (%s::date IS NULL OR statementdate < %s::date)
                               ORDER BY statementdate DESC, id DESC LIMIT 1""", (cid, fromdate, fromdate))
                previous = cur.fetchone()

        lines = _merge(raw, summary)
        _assign_groups(lines)
        st = Statement(accno=chart["accno"], description=chart["description"], category=chart["category"],
                       chart_id=cid, fromdate=fromdate, todate=todate, statementdate=statementdate,
                       summary=summary, fx=fx, precision=precision,
                       beginbalance=_r(begin * ml, precision), lines=lines,
                       statementbalance=_r(ending * ml, precision), report=report, previous=previous)
        for grp in st.groups().values():
            chk = report or _group_checked(grp, statementdate)
            for ln in grp:
                ln.checked = chk
                if not chk:
                    done = [a for a, c in zip(ln._amount_all, ln._cleared_all)
                            if c is not None and c <= statementdate]
                    ln.partly, ln.partial = bool(done), sum(done, ZERO)
        bal = st.beginbalance
        for ln in lines:
            bal += ln.amount * ml
            ln.balance = _r(bal, precision)
        if previous and fromdate and (fromdate - previous["statementdate"]).days == 1 \
                and _r(to_decimal(previous["statementbalance"]), precision) != st.beginbalance:
            st.warnings.append(
                f"Opening balance {st.beginbalance} differs from the statement balance "
                f"{to_decimal(previous['statementbalance'])} of the previous reconciliation "
                f"({previous['statementdate']})")
        return st

    # -------------------------------------------------------------- opslaan

    def reconcile(self, accno: str, statementdate: date, statementbalance: Decimal,
                  selected_keys: Iterable[Key], fromdate: Optional[date] = None, *,
                  summary: bool = True, fx: bool = True) -> ReconcileResult:
        """Sla de vinkjes op (``rc.pl done`` + ``RC::reconcile``).

        De server bouwt het scherm opnieuw op (tot-datum = ``statementdate``) en
        rekent het verschil uit de vinkjes en het afschriftsaldo; is dat niet 0,
        dan wordt er niets opgeslagen (L2). Nieuw aangevinkte regels krijgen
        afletterdatum = afschriftdatum (L3); regels die al afgeletterd waren en
        aangevinkt blijven houden hun datum; uitgevinkte regels binnen de getoonde
        selectie verliezen hun afletterdatum. Regels met dezelfde sleutel gaan
        altijd samen (zie de moduledocumentatie).
        """
        if not statementdate:
            raise ValidationError("Statement date missing!")
        statementbalance = to_decimal(statementbalance)
        selected = set(selected_keys)
        with self.conn.transaction():
            ensure_schema(self.conn)
            with _db.cursor(self.conn) as cur:
                chart = self._chart(cur, accno)
                # één afletterronde tegelijk per rekening
                cur.execute("SELECT id FROM chart WHERE id = %s FOR UPDATE", (chart["id"],))
            st = self.statement(accno, fromdate, statementdate, summary=summary, fx=fx)
            precision = st.precision
            if round_amount(statementbalance, precision) != statementbalance:
                raise ValidationError(f"Too many decimals: {statementbalance}")
            known = {k for ln in st.lines for k in ln.keys}
            missing = selected - known
            if missing:
                raise ValidationError("The lines changed meanwhile; reload the statement")
            groups = st.selected_groups(selected)
            diff = st.difference(statementbalance, groups)
            if diff != 0:
                raise OutOfBalanceError("Out of balance!")
            to_set: Set[Key] = set()
            to_clear: Set[Key] = set()
            nlines = 0
            for g, grp in st.groups().items():
                keys = {k for ln in grp for k in ln.keys}
                if g in groups:
                    nlines += len(grp)
                    if not all(ln.checked for ln in grp) or not _all_cleared(grp, statementdate):
                        to_set |= keys
                elif all(ln.checked for ln in grp):
                    to_clear |= keys
            n_set = n_clear = 0
            with _db.cursor(self.conn) as cur:
                cid = st.chart_id
                for tid, td, src in sorted(to_set):
                    cur.execute("""UPDATE acc_trans SET cleared = %s
                                   WHERE chart_id = %s AND trans_id = %s AND transdate = %s
                                   AND coalesce(source, '') = %s AND approved
                                   AND (cleared IS NULL OR cleared > %s)""",
                                (statementdate, cid, tid, td, src, statementdate))
                    n_set += cur.rowcount
                for tid, td, src in sorted(to_clear):
                    # alleen wat op het scherm stond: niet de regels die in het beginsaldo zitten
                    cur.execute("""UPDATE acc_trans SET cleared = NULL
                                   WHERE chart_id = %s AND trans_id = %s AND transdate = %s
                                   AND coalesce(source, '') = %s AND approved
                                   AND cleared IS NOT NULL AND cleared <= %s
                                   AND (%s::date IS NULL OR transdate >= %s::date OR cleared >= %s::date)""",
                                (cid, tid, td, src, statementdate, fromdate, fromdate, fromdate))
                    n_clear += cur.rowcount
                _, employee_id = _db.get_employee(cur, self.config.short_login)
                cur.execute("""INSERT INTO x_reconciliation (chart_id, statementdate, fromdate, beginbalance,
                                   statementbalance, cleared_count, employee_id, login)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                            (cid, statementdate, fromdate, st.beginbalance, statementbalance, nlines,
                             employee_id or None, self.config.login))
                rid = cur.fetchone()["id"]
                support.audittrail(cur, self.config, trans_id=rid, tablename="x_reconciliation",
                                   reference=f"{st.accno} {statementdate.isoformat()}",
                                   formname="reconciliation", action="posted")
        return ReconcileResult(id=rid, cleared=n_set, uncleared=n_clear, lines=nlines)

    # -------------------------------------------------------------- intern

    def _chart(self, cur, accno: str) -> Dict[str, Any]:
        cur.execute("""SELECT c.id, c.accno, COALESCE(l.description, c.description) AS description,
                              c.category, c.charttype
                       FROM chart c
                       LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %s)
                       WHERE c.accno = %s""", (self.config.countrycode, accno))
        row = cur.fetchone()
        if not row or row["charttype"] != "A":
            raise NotFoundError(f"Account {accno} not found")
        return row


def _merge(raw: List[Dict[str, Any]], summary: bool) -> List[StatementLine]:
    """Regels samenvoegen zoals ``RC::payment_transactions`` (r237–283): opeenvolgende
    regels met dezelfde datum en hetzelfde kenmerk en hetzelfde teken worden één
    schermregel; koersverschilregels tellen alleen het bedrag mee."""
    lines: List[StatementLine] = []
    sw: Optional[Decimal] = None
    sameitem: Optional[Tuple[date, str]] = None
    samename: Optional[str] = None
    for r in raw:
        amount = to_decimal(r["amount"])
        key: Key = (int(r["trans_id"]), r["transdate"], r["source"])
        item = (r["transdate"], r["source"])
        if sw is None:
            sw = amount
        merge = False
        if summary:
            if (amount > 0 and sw < 0) or (amount < 0 and sw > 0):
                sameitem = None
                sw = amount
            merge = item == sameitem and bool(lines)
        if merge:
            ln = lines[-1]
            ln.amount += amount
            if not r["fx"]:
                if r["description"] != samename and r["description"] is not None:
                    ln.names.append(r["description"])
                if key[0] not in ln.trans_ids:
                    ln.trans_ids.append(key[0])
            if key not in ln.keys:
                ln.keys.append(key)
            ln._cleared_all.append(r["cleared"])
            ln._fx_all.append(bool(r["fx"]))
            ln._amount_all.append(amount)
        else:
            lines.append(StatementLine(
                transdate=r["transdate"], source=r["source"],
                names=[r["description"]] if r["description"] is not None else [],
                amount=amount, fx=bool(r["fx"]), cleared=r["cleared"], keys=[key], trans_ids=[key[0]],
                _cleared_all=[r["cleared"]], _fx_all=[bool(r["fx"])], _amount_all=[amount]))
        sameitem = item
        samename = r["description"]
    return lines


def _assign_groups(lines: List[StatementLine]) -> None:
    """Schermregels die een sleutel delen komen in één groep (union-find)."""
    parent = list(range(len(lines)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: Dict[Key, int] = {}
    for i, ln in enumerate(lines):
        for k in ln.keys:
            if k in owner:
                a, b = find(owner[k]), find(i)
                if a != b:
                    parent[max(a, b)] = min(a, b)
            else:
                owner[k] = i
    for i, ln in enumerate(lines):
        ln.group = find(i)


def _rows_for_status(grp: List[StatementLine]) -> List[Optional[date]]:
    """Afletterdatums die de status bepalen: van de gewone regels; koersverschilregels
    volgen hun betaling (payments.py zet daar bij het boeken al een datum) en tellen
    alleen mee als de groep niets anders heeft."""
    normal = [c for ln in grp for c, f in zip(ln._cleared_all, ln._fx_all) if not f]
    return normal or [c for ln in grp for c in ln._cleared_all]


def _group_checked(grp: List[StatementLine], statementdate: date) -> bool:
    return all(c is not None and c <= statementdate for c in _rows_for_status(grp))


def _all_cleared(grp: List[StatementLine], statementdate: date) -> bool:
    """Alle regels (ook koersverschil) al afgeletterd op of vóór de afschriftdatum."""
    return all(c is not None and c <= statementdate for ln in grp for c in ln._cleared_all)
