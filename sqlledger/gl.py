"""Grootboek: memoriaalboekingen (port van SL/GL.pm + regels uit bin/mozilla/gl.pl).

Teken-conventie in ``acc_trans.amount`` (ongewijzigd t.o.v. SQL-Ledger):
debet is negatief, credit is positief.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import psycopg

from . import _db, support
from .amounts import opt_decimal, round_amount, to_decimal
from .config import UserConfig
from .dates import from_to, parse_date
from .errors import (ClosedPeriodError, LockedTransactionError, NotFoundError,
                     OutOfBalanceError, ValidationError)
from .models import (GLTransaction, GLTransactionView, TransactionList,
                     TransactionSearch)
from .numbering import next_number

# kolommen van de UNION-query in list_transactions, in volgorde (voor ORDER BY n)
_LIST_COLUMNS = (
    "id", "type", "invoice", "reference", "description", "transdate", "source",
    "amount", "accno", "account_description", "account_translation", "category",
    "ca", "gifi_accno", "notes", "link", "till", "cleared", "department", "memo",
    "name_id", "db", "lineitem", "name", "vcnumber", "address1", "address2",
    "city", "zipcode", "country",
)


def like(value: str) -> str:
    """LIKE-patroon zoals Form::like: zonder % of _ wordt het '%waarde%',
    "tussen aanhalingstekens" betekent exact."""
    if not re.search(r"[%_]", value):
        if re.match(r'^".*"$', value, re.S) and len(value) >= 2:
            return value[1:-1]
        return f"%{value}%"
    return value


def _closedto(defaults: Dict[str, Any], dateformat: str) -> Optional[int]:
    """Afsluitdatum als getal jjjjmmdd, zoals Perl (datetonum + numeriek vergelijken).

    Waarden zonder scheidingsteken worden ongewijzigd als getal gebruikt; in
    bestaande data komt bv. '20173112' voor (geen geldige datum, maar Perl
    vergelijkt het gewoon als getal: alles na 2017 is open).
    """
    value = (defaults.get("closedto") or "").strip()
    if not value:
        return None
    if re.fullmatch(r"\d+", value):
        return int(value)
    d = parse_date(value, dateformat)
    return _num(d)


def _num(d) -> int:
    return d.year * 10000 + d.month * 100 + d.day


def sort_order(columns: List[str], sort: Optional[str], direction: str,
               ordinal: Dict[str, int]) -> str:
    """ORDER BY-clausule zoals Form::sort_order (sort-kolom eerst)."""
    direction = "DESC" if str(direction).upper() == "DESC" else "ASC"
    cols = list(columns)
    if sort and sort in ordinal:
        cols = [sort] + [c for c in cols if c != sort]
    first = cols[0]
    out = [f"{ordinal.get(first, first)} {direction}"]
    out += [str(ordinal.get(c, c)) for c in cols[1:]]
    return ",".join(out)


class GeneralLedger:
    """Boeken, ophalen, verwijderen en zoeken van memoriaalboekingen."""

    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        self.conn = conn
        self.config = config

    # ------------------------------------------------------------ validatie

    def validate(self, trans: GLTransaction, cur: psycopg.Cursor) -> Tuple[int, Dict[str, Any]]:
        """Controleer een boeking voordat die geschreven wordt.

        In SQL-Ledger staan deze controles in het scherm (gl.pl ``post``) en
        niet in GL.pm; hier zitten ze in de kern zodat elke aanroeper ze krijgt.
        """
        if not trans.transdate:
            raise ValidationError("Transaction Date missing!")
        defaults = _db.get_defaults(cur, "closedto", "revtrans", "precision")
        precision = int(to_decimal(defaults.get("precision") or 2))
        closedto = _closedto(defaults, self.config.dateformat)

        if closedto and _num(trans.transdate) <= closedto:
            raise ClosedPeriodError("Cannot post transaction for a closed period!")

        if trans.id:
            cur.execute("SELECT transdate FROM gl WHERE id = %s", (trans.id,))
            existing = cur.fetchone()
            if existing:
                if defaults.get("revtrans") and defaults.get("revtrans") not in ("0", ""):
                    raise LockedTransactionError("Transaction is locked (revtrans)")
                if closedto and existing["transdate"] and _num(existing["transdate"]) <= closedto:
                    raise LockedTransactionError("Cannot change a transaction in a closed period!")

        if not trans.lines:
            raise ValidationError("Transaction has no lines")
        if trans.id:
            holder = support.lock_holder(cur, self.config, trans.id)
            if holder:
                raise LockedTransactionError(f"Transaction is locked by {holder}")
        defaultcurrency = _db.default_currency(cur)
        if (trans.currency and trans.currency != defaultcurrency
                and not to_decimal(trans.exchangerate) > 0):
            raise ValidationError("Exchange rate missing!")

        debit = credit = Decimal(0)
        for line in trans.lines:
            dr, cr = to_decimal(line.debit), to_decimal(line.credit)
            if dr and cr:
                raise ValidationError(
                    "Cannot post transaction with a debit and credit entry for the same account!")
            if dr < 0 or cr < 0:
                raise ValidationError("Debit and credit amounts must not be negative")
            if round_amount(dr, precision) != dr or round_amount(cr, precision) != cr:
                # Perl slaat bv. 0.004 ongewijzigd op; de balanscontrole rondt af,
                # zodat de boeking een fractie uit balans kan raken (README 40)
                raise ValidationError(f"Amount has more than {precision} decimals")
            debit += dr
            credit += cr
        if round_amount(debit, precision) != round_amount(credit, precision):
            raise OutOfBalanceError("Out of balance transaction!")

        accnos = sorted({line.accno for line in trans.lines})
        # Perl controleert dit niet: een onbekende rekening wordt een regel met
        # chart_id NULL. Het scherm biedt alleen open boekrekeningen (charttype A) aan.
        cur.execute("SELECT accno, charttype, closed FROM chart WHERE accno = ANY(%s)", (accnos,))
        known = {r["accno"]: r for r in cur.fetchall()}
        missing = [a for a in accnos if a not in known]
        if missing:
            raise ValidationError(f"Unknown account(s): {', '.join(missing)}")
        not_postable = [a for a in accnos if known[a]["charttype"] != "A" or known[a]["closed"]]
        if not_postable:
            raise ValidationError(f"Account(s) not postable (heading or closed): {', '.join(not_postable)}")
        return precision, defaults

    # ------------------------------------------------------------- boeken

    def post_transaction(self, trans: GLTransaction) -> int:
        """Boek (of vervang) een memoriaalboeking. Geeft het gl.id terug."""
        cfg = self.config
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            precision, _ = self.validate(trans, cur)

            approved = not trans.pending
            action = "posted" if approved else "saved"
            keepcleared = False
            trans_id: Optional[int] = None
            existing_voucher: Optional[Dict[str, Any]] = None

            if trans.id:
                keepcleared = True
                if trans.batch_id:
                    cur.execute("SELECT * FROM vr WHERE trans_id = %s", (trans.id,))
                    existing_voucher = cur.fetchone()
                    cur.execute(
                        "SELECT SUM(amount::numeric) AS s FROM acc_trans WHERE amount < 0 AND trans_id = %s",
                        (trans.id,))
                    _db.update_balance(cur, "br", "amount", trans.batch_id, cur.fetchone()["s"])
                    cur.execute("DELETE FROM vr WHERE trans_id = %s", (trans.id,))

                cur.execute("SELECT id FROM gl WHERE id = %s", (trans.id,))
                row = cur.fetchone()
                if row:
                    trans_id = row["id"]
                    for table in ("acc_trans", "dpt_trans"):
                        cur.execute(f"DELETE FROM {table} WHERE trans_id = %s", (trans_id,))

            if not trans_id:
                cur.execute(
                    """INSERT INTO gl (employee_id, approved)
                       VALUES ((SELECT id FROM employee WHERE login = %s), %s)
                       RETURNING id""",
                    (cfg.login, approved),
                )
                trans_id = cur.fetchone()["id"]

            department_id = int(trans.department_id or 0)
            defaultcurrency = _db.default_currency(cur)
            currency = trans.currency or defaultcurrency
            exchangerate = to_decimal(trans.exchangerate) or Decimal(1)

            # Perl vult <%CURR%> vóórdat de standaardvaluta is ingevuld; daarom hier
            # trans.currency (leeg bij standaardvaluta), zodat de nummers gelijk blijven.
            reference = trans.reference or next_number(
                cur, "glnumber", dateformat=cfg.dateformat, transdate=trans.transdate,
                currency=trans.currency, fields={"description": trans.description})

            cur.execute(
                """UPDATE gl SET reference = %s, description = %s, notes = %s,
                          transdate = %s, department_id = %s, curr = %s, exchangerate = %s
                   WHERE id = %s""",
                (reference, trans.description, trans.notes, trans.transdate,
                 department_id, currency, exchangerate, trans_id),
            )
            if department_id:
                cur.execute("INSERT INTO dpt_trans (trans_id, department_id) VALUES (%s, %s)",
                            (trans_id, department_id))

            support.update_exchangerate(cur, currency, trans.transdate, exchangerate, defaultcurrency)

            bramount = Decimal(0)
            insert = """INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, source,
                                               fx_transaction, project_id, memo, cleared, approved)
                        VALUES (%s, (SELECT id FROM chart WHERE accno = %s), %s, %s, %s,
                                %s, %s, %s, %s, %s)"""
            for line in trans.lines:
                amount = Decimal(0)
                debit, credit = to_decimal(line.debit), to_decimal(line.credit)
                if credit:
                    amount = credit
                    bramount += round_amount(amount * exchangerate, precision)
                if debit:
                    amount = -debit

                cleared = line.cleared if keepcleared else None
                if line.fx_transaction:
                    cleared = trans.transdate

                if amount or line.source or line.memo or line.project_id:
                    cur.execute(insert, (trans_id, line.accno, amount, trans.transdate, line.source,
                                         bool(line.fx_transaction), line.project_id, line.memo,
                                         cleared, approved))
                    if currency != defaultcurrency:
                        fx = round_amount(amount * (exchangerate - 1), precision)
                        if fx:
                            cur.execute(insert, (trans_id, line.accno, fx, trans.transdate,
                                                 line.source, True, line.project_id, line.memo,
                                                 cleared, approved))

            if trans.batch_id:
                vouchernumber = (trans.vouchernumber
                                 or (existing_voucher or {}).get("vouchernumber")
                                 or next_number(cur, "vouchernumber", dateformat=cfg.dateformat,
                                                transdate=trans.transdate, currency=currency))
                cur.execute(
                    "INSERT INTO vr (br_id, trans_id, id, vouchernumber) VALUES (%s, %s, %s, %s)",
                    (trans.batch_id, trans_id, trans_id, vouchernumber))
                _db.update_balance(cur, "br", "amount", trans.batch_id, bramount)

            support.save_references(cur, cfg, trans_id, "gl", trans.references)
            support.audittrail(cur, cfg, trans_id=trans_id, tablename="gl", reference=reference,
                               formname="transaction", action=action)
            support.save_recurring(cur, trans_id, trans.recurring)
            support.remove_locks(cur, cfg, "gl")
        return trans_id

    # ---------------------------------------------------------- verwijderen

    def delete_transaction(self, trans_id: int, batch_id: Optional[int] = None) -> None:
        """Verwijder een memoriaalboeking met alle bijbehorende gegevens."""
        cfg = self.config
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("SELECT id, reference, transdate FROM gl WHERE id = %s", (trans_id,))
            gl = cur.fetchone()
            if not gl:
                raise NotFoundError(f"GL transaction {trans_id} not found")

            defaults = _db.get_defaults(cur, "closedto", "revtrans", "precision")
            if defaults.get("revtrans") and defaults.get("revtrans") not in ("0", ""):
                raise LockedTransactionError("Transaction is locked (revtrans)")
            closedto = _closedto(defaults, cfg.dateformat)
            if closedto and gl["transdate"] and _num(gl["transdate"]) <= closedto:
                raise ClosedPeriodError("Cannot delete a transaction in a closed period!")

            support.audittrail(cur, cfg, trans_id=trans_id, tablename="gl",
                               reference=gl["reference"], formname="transaction", action="deleted")

            if batch_id:
                # Perl heeft hier een typefout ($mount i.p.v. $amount), waardoor het
                # batchtotaal niet wordt bijgewerkt. Hier wel, net als bij wijzigen.
                cur.execute("SELECT SUM(amount::numeric) AS s FROM acc_trans WHERE trans_id = %s AND amount < 0",
                            (trans_id,))
                precision = int(to_decimal(defaults.get("precision") or 2))
                amount = round_amount(cur.fetchone()["s"], precision)
                _db.update_balance(cur, "br", "amount", batch_id, amount)
                cur.execute("DELETE FROM vr WHERE trans_id = %s", (trans_id,))

            cur.execute("DELETE FROM gl WHERE id = %s", (trans_id,))
            cur.execute("SELECT trans_id FROM pay_trans WHERE glid = %s", (trans_id,))
            row = cur.fetchone()
            apid = row["trans_id"] if row else None

            for tid in (trans_id, apid):
                if not tid:
                    continue
                for table in ("acc_trans", "dpt_trans", "yearend", "pay_trans", "status"):
                    cur.execute(f"DELETE FROM {table} WHERE trans_id = %s", (tid,))
            for table in ("recurring", "recurringemail", "recurringprint"):
                cur.execute(f"DELETE FROM {table} WHERE id = %s", (trans_id,))

            support.delete_references(cur, cfg, trans_id)
            support.remove_locks(cur, cfg, "gl")

    # --------------------------------------------------------------- ophalen

    def get_transaction(self, trans_id: int, lock: bool = False) -> GLTransactionView:
        """Haal een boeking op (GL::transaction). Met lock=True wordt het record
        gelockt voor deze gebruiker; is het al gelockt dan is readonly True."""
        cfg = self.config
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            if lock:
                support.remove_locks(cur, cfg, "gl")
            defaults = _db.get_defaults(cur, "closedto", "revtrans", "precision",
                                        "referenceurl", "lock_%")
            precision = int(to_decimal(defaults.get("precision") or 2))

            cur.execute(
                """SELECT g.*, d.description AS department,
                          br.id AS batchid, br.description AS batchdescription
                   FROM gl g
                   LEFT JOIN department d ON (d.id = g.department_id)
                   LEFT JOIN vr ON (vr.trans_id = g.id)
                   LEFT JOIN br ON (br.id = vr.br_id)
                   WHERE g.id = %s""", (trans_id,))
            header = cur.fetchone()
            if not header:
                raise NotFoundError(f"GL transaction {trans_id} not found")
            header["currency"] = (header.get("curr") or "").strip() or None
            header["exchangerate"] = opt_decimal(header.get("exchangerate"))

            cur.execute(
                """SELECT ac.*, c.accno, c.description, p.projectnumber,
                          l.description AS translation
                   FROM acc_trans ac
                   JOIN chart c ON (ac.chart_id = c.id)
                   LEFT JOIN project p ON (p.id = ac.project_id)
                   LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %s)
                   WHERE ac.trans_id = %s
                   ORDER BY c.accno""", (cfg.countrycode, trans_id))
            lines = cur.fetchall()
            fxdr = fxcr = Decimal(0)
            for line in lines:
                line["amount"] = to_decimal(line["amount"])
                if line["translation"]:
                    line["description"] = line["translation"]
                if line["fx_transaction"]:
                    if line["amount"] < 0:
                        fxdr += line["amount"]
                    if line["amount"] > 0:
                        fxcr += line["amount"]
                line["debit"] = -line["amount"] if line["amount"] < 0 else Decimal(0)
                line["credit"] = line["amount"] if line["amount"] > 0 else Decimal(0)

            fxadj = bool((fxdr < 0 or fxcr > 0)
                         and round_amount(-fxdr, precision) != round_amount(fxcr, precision))
            if not fxadj:
                lines = [ln for ln in lines if not ln["fx_transaction"]]

            closedto = _closedto(defaults, cfg.dateformat)
            revtrans = defaults.get("revtrans") not in (None, "", "0")
            defaults["locked"] = revtrans or bool(
                closedto and header["transdate"] and _num(header["transdate"]) <= closedto)

            view = GLTransactionView(
                header=header, lines=lines, fxadj=fxadj,
                recurring=support.get_recurring(cur, trans_id),
                references=support.all_references(cur, cfg, trans_id),
                defaults=defaults,
            )
            if lock:
                holder = support.create_lock(cur, cfg, trans_id, "gl")
                view.readonly = holder is not None
                view.haslock = holder
        return view

    def next_reference(self, transdate=None, currency: Optional[str] = None,
                       description: Optional[str] = None) -> str:
        """Volgend boekingsnummer nu al uitgeven (knop 'Nieuw nummer'; telt de teller op)."""
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            return next_number(cur, "glnumber", dateformat=self.config.dateformat,
                               transdate=transdate, currency=currency,
                               fields={"description": description})

    def exchange_rate(self, currency: Optional[str], transdate) -> Optional[Decimal]:
        """Koers uit de koerstabel op de datum (Form::check_exchangerate); None als
        er geen is of bij de standaardvaluta."""
        if not currency or not transdate:
            return None
        with _db.cursor(self.conn) as cur:
            if currency == _db.default_currency(cur):
                return None
            cur.execute("SELECT exchangerate FROM exchangerate WHERE curr = %s AND transdate = %s",
                        (currency, transdate))
            r = cur.fetchone()
        return opt_decimal(r["exchangerate"]) if r else None

    def unlock(self) -> None:
        """Geef alle GL-locks van deze gebruiker vrij."""
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            support.remove_locks(cur, self.config, "gl")

    # ------------------------------------------------- keuzelijsten voor invoer

    def postable_accounts(self) -> List[Dict[str, Any]]:
        """Bebookbare, niet-gesloten rekeningen (charttype 'A')."""
        with _db.cursor(self.conn) as cur:
            cur.execute(
                """SELECT c.accno, c.description, l.description AS translation
                   FROM chart c
                   LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %s)
                   WHERE c.charttype = 'A' AND c.closed = '0'
                   ORDER BY 1""", (self.config.countrycode,))
            result = cur.fetchall()
        for r in result:
            if r["translation"]:
                r["description"] = r["translation"]
        return result

    def departments(self, vc: Optional[str] = None) -> List[Dict[str, Any]]:
        where = "role = 'P'" if vc == "customer" else "1 = 1"
        with _db.cursor(self.conn) as cur:
            cur.execute(f"SELECT id, description FROM department WHERE {where} ORDER BY rn")
            return cur.fetchall()

    def projects(self, transdate=None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM project pr WHERE (pr.parts_id = 0 OR pr.parts_id IS NULL)"
        params: list = []
        if transdate:
            query += (" AND (startdate IS NULL OR startdate <= %s)"
                      " AND (enddate IS NULL OR enddate >= %s)")
            params += [transdate, transdate]
        query += " ORDER BY pr.projectnumber"
        with _db.cursor(self.conn) as cur:
            cur.execute(query, params)
            return cur.fetchall()

    # --------------------------------------------------------------- zoeken

    def list_transactions(self, search: Optional[TransactionSearch] = None) -> TransactionList:
        """Journaaloverzicht over gl, ar en ap (GL::transactions), inclusief
        tegenrekeningen (contra) per regel."""
        f = search or TransactionSearch()
        cfg = self.config
        info: Dict[str, Any] = {}

        glw: List[str] = ["g.approved = '1'"]
        arw: List[str] = ["a.approved = '1'"]
        apw: List[str] = ["a.approved = '1'"]
        glp: List[Any] = []
        arp: List[Any] = []
        app: List[Any] = []

        def add(gl_sql, ar_sql, ap_sql, *values):
            for where, params, sql in ((glw, glp, gl_sql), (arw, arp, ar_sql), (apw, app, ap_sql)):
                where.append(sql)
                params.extend(values if "%s" in sql else ())

        if f.reference:
            v = like(f.reference.lower())
            add("lower(g.reference) LIKE %s", "lower(a.invnumber) LIKE %s",
                "lower(a.invnumber) LIKE %s", v)
        if f.description:
            v = like(f.description.lower())
            add("lower(g.description) LIKE %s", "lower(a.description) LIKE %s",
                "lower(a.description) LIKE %s", v)
        if f.name:
            v = like(f.name.lower())
            add("lower(g.description) LIKE %s", "lower(ct.name) LIKE %s",
                "lower(ct.name) LIKE %s", v)
        if f.vcnumber:
            v = like(f.vcnumber.lower())
            add("g.id = 0", "lower(ct.customernumber) LIKE %s", "lower(ct.vendornumber) LIKE %s", v)
        if f.department_id:
            add("g.department_id = %s", "a.department_id = %s", "a.department_id = %s",
                int(f.department_id))

        gdescription, lineitem, invoicejoin = "''", "''", ""
        if f.lineitem:
            v = like(f.lineitem.lower())
            add("lower(ac.memo) LIKE %s", "lower(i.description) LIKE %s",
                "lower(i.description) LIKE %s", v)
        if f.lineitem or f.include_lineitem:
            gdescription, lineitem = "ac.memo", "i.description"
            invoicejoin = "LEFT JOIN invoice i ON (i.id = ac.id)"

        if f.source:
            v = like(f.source.lower())
            add("lower(ac.source) LIKE %s", "lower(ac.source) LIKE %s",
                "lower(ac.source) LIKE %s", v)

        with _db.cursor(self.conn) as cur:
            defaults = _db.get_defaults(cur, "precision", "company")
            info.update(defaults)

            def chart_description(accno):
                cur.execute(
                    """SELECT c.description, l.description AS translation FROM chart c
                       LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %s)
                       WHERE c.accno = %s""", (cfg.countrycode, accno))
                r = cur.fetchone()
                return (r["translation"] or r["description"]) if r else None

            if f.accnofrom:
                info["accnofrom_description"] = chart_description(f.accnofrom)
                add("c.accno >= %s", "c.accno >= %s", "c.accno >= %s", f.accnofrom)
            if f.accnoto:
                info["accnoto_description"] = chart_description(f.accnoto)
                add("c.accno <= %s", "c.accno <= %s", "c.accno <= %s", f.accnoto)
            if f.memo:
                v = like(f.memo.lower())
                add("lower(ac.memo) LIKE %s", "lower(ac.memo) LIKE %s", "lower(ac.memo) LIKE %s", v)

            datefrom, dateto = f.datefrom, f.dateto
            if not (datefrom or dateto) and f.year and f.month:
                datefrom, dateto = from_to(f.year, f.month, f.interval)
            if datefrom:
                add("ac.transdate >= %s", "ac.transdate >= %s", "ac.transdate >= %s", datefrom)
            if dateto:
                add("ac.transdate <= %s", "ac.transdate <= %s", "ac.transdate <= %s", dateto)
            if f.amountfrom:
                v = to_decimal(f.amountfrom)
                add("abs(ac.amount) >= %s", "abs(ac.amount) >= %s", "abs(ac.amount) >= %s", v)
            if f.amountto:
                v = to_decimal(f.amountto)
                add("abs(ac.amount) <= %s", "abs(ac.amount) <= %s", "abs(ac.amount) <= %s", v)
            if f.notes:
                v = like(f.notes.lower())
                add("lower(g.notes) LIKE %s", "lower(a.notes) LIKE %s", "lower(a.notes) LIKE %s", v)
            if f.accno:
                add("c.accno = %s", "c.accno = %s", "c.accno = %s", f.accno)
            if f.gifi_accno:
                add("c.gifi_accno = %s", "c.gifi_accno = %s", "c.gifi_accno = %s", f.gifi_accno)
            if f.category != "X":
                add("c.category = %s", "c.category = %s", "c.category = %s", f.category)

            balance: Optional[Decimal] = None
            if f.accno or f.gifi_accno:
                if f.accno:
                    cur.execute(
                        """SELECT c.category, c.link, c.contra, c.description,
                                  l.description AS translation
                           FROM chart c
                           LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %s)
                           WHERE c.accno = %s""", (cfg.countrycode, f.accno))
                    r = cur.fetchone() or {}
                    info.update(category=r.get("category"), link=r.get("link"),
                                contra=r.get("contra"),
                                account_description=r.get("translation") or r.get("description"))
                if f.gifi_accno:
                    cur.execute(
                        """SELECT c.category, c.link, c.contra, g.description FROM chart c
                           LEFT JOIN gifi g ON (g.accno = c.gifi_accno)
                           WHERE c.gifi_accno = %s""", (f.gifi_accno,))
                    r = cur.fetchone() or {}
                    info.update(category=r.get("category"), link=r.get("link"),
                                contra=r.get("contra"),
                                gifi_account_description=r.get("description"))
                if datefrom:
                    balance = self._opening_balance(cur, f.accno or "", datefrom)

            query = f"""
                SELECT g.id, 'gl' AS type, FALSE AS invoice, g.reference,
                       g.description, ac.transdate, ac.source,
                       ac.amount, c.accno, c.description AS account_description,
                       l.description AS account_translation, c.category,
                       c.contra AS ca, c.gifi_accno, g.notes, c.link,
                       '' AS till, ac.cleared, d.description AS department,
                       ac.memo, '0' AS name_id, '' AS db,
                       {gdescription} AS lineitem, '' AS name, '' AS vcnumber,
                       '' AS address1, '' AS address2, '' AS city,
                       '' AS zipcode, '' AS country
                FROM gl g
                JOIN acc_trans ac ON (g.id = ac.trans_id)
                JOIN chart c ON (ac.chart_id = c.id)
                LEFT JOIN department d ON (d.id = g.department_id)
                LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %s)
                WHERE {' AND '.join(glw)}
              UNION ALL
                SELECT a.id, 'ar' AS type, a.invoice, a.invnumber,
                       a.description, ac.transdate, ac.source,
                       ac.amount, c.accno, c.description AS account_description,
                       l.description AS account_translation, c.category,
                       c.contra AS ca, c.gifi_accno, a.notes, c.link,
                       a.till, ac.cleared, d.description AS department,
                       ac.memo, ct.id AS name_id, 'customer' AS db,
                       {lineitem} AS lineitem, ct.name, ct.customernumber,
                       ad.address1, ad.address2, ad.city,
                       ad.zipcode, ad.country
                FROM ar a
                JOIN acc_trans ac ON (a.id = ac.trans_id)
                {invoicejoin}
                JOIN chart c ON (ac.chart_id = c.id)
                JOIN customer ct ON (a.customer_id = ct.id)
                JOIN address ad ON (ad.trans_id = ct.id)
                LEFT JOIN department d ON (d.id = a.department_id)
                LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %s)
                WHERE {' AND '.join(arw)}
              UNION ALL
                SELECT a.id, 'ap' AS type, a.invoice, a.invnumber,
                       a.description, ac.transdate, ac.source,
                       ac.amount, c.accno, c.description AS account_description,
                       l.description AS account_translation, c.category,
                       c.contra AS ca, c.gifi_accno, a.notes, c.link,
                       a.till, ac.cleared, d.description AS department,
                       ac.memo, ct.id AS name_id, 'vendor' AS db,
                       {lineitem} AS lineitem, ct.name, ct.vendornumber,
                       ad.address1, ad.address2, ad.city,
                       ad.zipcode, ad.country
                FROM ap a
                JOIN acc_trans ac ON (a.id = ac.trans_id)
                {invoicejoin}
                JOIN chart c ON (ac.chart_id = c.id)
                JOIN vendor ct ON (a.vendor_id = ct.id)
                JOIN address ad ON (ad.trans_id = ct.id)
                LEFT JOIN department d ON (d.id = a.department_id)
                LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %s)
                WHERE {' AND '.join(apw)}"""
            params = [cfg.countrycode, *glp, cfg.countrycode, *arp, cfg.countrycode, *app]

            ordinal = {name: i + 1 for i, name in enumerate(_LIST_COLUMNS)}
            columns = ["id", "transdate", "reference"] + ([] if f.splitledger else ["accno"])
            order = sort_order(columns, f.sort, f.direction, ordinal)
            if f.splitledger:
                order = f"{ordinal['accno']}, {order}"
            # bij gelijke sorteerwaarden een vaste volgorde (Perl: willekeurig uit PostgreSQL, README 44)
            order += "".join(f", {ordinal[c]}" for c in ("amount", "source", "memo"))
            cur.execute(query + f" ORDER BY {order}", params)
            records = cur.fetchall()

            split_balance: Dict[str, Decimal] = {}
            rows: List[Dict[str, Any]] = []
            for ref in records:
                ref["amount"] = to_decimal(ref["amount"])
                if ref["account_translation"]:
                    ref["account_description"] = ref["account_translation"]
                if f.splitledger:
                    ref["balance"] = Decimal(0)
                    if datefrom:
                        if ref["accno"] not in split_balance:
                            split_balance[ref["accno"]] = self._opening_balance(
                                cur, ref["accno"], datefrom)
                        ref["balance"] = split_balance[ref["accno"]]
                if ref["type"] == "gl":
                    ref["module"] = "gl"
                else:
                    ref["memo"] = ref["memo"] or ref["lineitem"]
                    ref["description"] = ref["description"] or ref["name"]
                    if ref["type"] == "ap":
                        ref["module"] = "ir" if ref["invoice"] else "ap"
                    else:
                        ref["module"] = ("ps" if ref["till"] else "is") if ref["invoice"] else "ar"
                if ref["amount"] < 0:
                    ref["debit"], ref["credit"] = -ref["amount"], Decimal(0)
                else:
                    ref["debit"], ref["credit"] = Decimal(0), ref["amount"]
                ref["address"] = "".join(
                    f"{ref[k] or ''} " for k in ("address1", "address2", "city", "zipcode", "country"))
                ref["contra"] = None
                ref["gifi_contra"] = None
                rows.append(ref)

        if not (f.accnofrom or f.accnoto):
            _assign_contra(rows)

        return TransactionList(rows=rows, datefrom=datefrom, dateto=dateto,
                               balance=balance, info=info, display=_gl_display(rows, balance, info, f))

    @staticmethod
    def _opening_balance(cur: psycopg.Cursor, accno: str, before) -> Decimal:
        """Saldo van een rekening vóór ``before`` over gl, ar en ap."""
        total = Decimal(0)
        for join in ("JOIN gl g ON (g.id = ac.trans_id)",
                     "JOIN ar a ON (a.id = ac.trans_id) JOIN customer ct ON (ct.id = a.customer_id)",
                     "JOIN ap a ON (a.id = ac.trans_id) JOIN vendor ct ON (ct.id = a.vendor_id)"):
            cur.execute(
                f"""SELECT SUM(ac.amount::numeric) AS s FROM acc_trans ac
                    JOIN chart c ON (ac.chart_id = c.id)
                    {join}
                    WHERE c.accno = %s AND ac.transdate < %s""", (accno, before))
            total += to_decimal(cur.fetchone()["s"])
        return total


def _gl_display(rows: List[Dict[str, Any]], balance: Optional[Decimal], info: Dict[str, Any],
                f: TransactionSearch) -> list:
    """gl.pl transactions: regels in schermvolgorde met [debet, credit, saldo].

    Soorten: 'balance' (beginsaldo bij één rekening), 'account' (kop per rekening
    bij splitledger, met beginsaldo), 'row', 'subtotal' (per sorteerwaarde),
    'account_subtotal' (per rekening bij splitledger), 'total' (niet bij één
    rekening). Saldo in weergaveteken; None = geen saldokolom. Zet ook
    ``running`` (lopend saldo) in elke rij als er een saldokolom is.
    Afwijking (README 39): bij splitledger sluit een nieuwe rekening ook de
    groep van het subtotaal af; Perl telde die door over rekeningen heen.
    """
    from .reports import DisplayRow

    zero = Decimal(0)
    sign = {"A": -1, "L": 1, "I": 1, "E": -1, "Q": 1}
    single = bool(f.accno or f.gifi_accno)
    show_balance = single or f.splitledger
    ml = -1 if (info.get("category") or f.category) in ("A", "E") else 1
    cml = -1 if rows and info.get("contra") else 1
    sort = f.sort or "transdate"
    display: list = []
    running = balance or zero

    def bal():
        return running * ml * cml if show_balance else None

    if single:
        display.append(DisplayRow("balance", "", values=[None, None, bal()]))
    sub_d = sub_c = acc_d = acc_c = tot_d = tot_c = zero
    same = rows[0].get(sort) if rows else None
    same_accno = None

    def subtotal():
        nonlocal sub_d, sub_c
        display.append(DisplayRow("subtotal", "", values=[sub_d, sub_c, None]))
        sub_d = sub_c = zero

    for ref in rows:
        if f.splitledger and not f.accno and ref["accno"] != same_accno:
            if same_accno is not None:
                if f.l_subtotal:
                    subtotal()
                    same = ref.get(sort)
                display.append(DisplayRow("account_subtotal", "", values=[acc_d, acc_c, bal()]))
                acc_d = acc_c = zero
            ml = sign.get(ref["category"], 1)
            cml = -1 if ref.get("ca") else 1
            running = ref.get("balance") or zero
            display.append(DisplayRow("account", f"{ref['accno']}--{ref['account_description']}",
                                      ref["accno"], [None, None, bal()]))
        if f.l_subtotal and ref.get(sort) != same:
            subtotal()
            same = ref.get(sort)
        running += ref["amount"]
        if show_balance:
            ref["running"] = bal()
        sub_d += ref["debit"]
        sub_c += ref["credit"]
        acc_d += ref["debit"]
        acc_c += ref["credit"]
        tot_d += ref["debit"]
        tot_c += ref["credit"]
        display.append(DisplayRow("row", ref.get("reference") or "", ref["accno"],
                                  [ref["debit"], ref["credit"], bal()]))
        same_accno = ref["accno"]
    if f.l_subtotal:
        subtotal()
    if f.splitledger:
        display.append(DisplayRow("account_subtotal", "", values=[acc_d, acc_c, bal()]))
        running = zero
    if not f.accno:
        display.append(DisplayRow("total", "", values=[tot_d, tot_c,
                                                       None if f.splitledger or not show_balance else bal()]))
    return display


def _assign_contra(rows: List[Dict[str, Any]]) -> None:
    """Bepaal per regel de tegenrekening(en), zoals het slot van GL::transactions.

    Afwijkingen van Perl (die daar van de willekeurige hash-volgorde afhangen):
    regels worden in vaste volgorde doorlopen en de 'al gezien'-administratie
    geldt per boeking in plaats van over het hele overzicht.
    """
    by_id: Dict[Any, List[int]] = {}
    for i, r in enumerate(rows):
        by_id.setdefault(r["id"], []).append(i)

    def amount(i: int) -> Decimal:
        return rows[i]["debit"] + rows[i]["credit"]

    def by_amount_desc(indices: List[int]) -> List[int]:
        # Perl: reverse sort { a <=> b } -> aflopend, gelijken in omgekeerde volgorde
        return list(reversed(sorted(indices, key=amount)))

    for indices in by_id.values():
        ordered = by_amount_desc(indices)
        if any(rows[i]["type"] in ("ar", "ap") for i in ordered):
            arap, gifi_arap, paid, gifi_paid = "", "", "", ""
            arap_rows: List[int] = []
            paid_rows: List[int] = []
            others: List[int] = []
            for i in ordered:
                r = rows[i]
                if r["type"] not in ("ar", "ap"):
                    continue
                ARAP = r["type"].upper()
                link = r["link"] or ""
                if link == ARAP:
                    arap, gifi_arap = r["accno"], r["gifi_accno"] or ""
                    arap_rows.append(i)
                elif re.search(f"{ARAP}_paid", link):
                    paid, gifi_paid = r["accno"], r["gifi_accno"] or ""
                    paid_rows.append(i)
                else:
                    others.append(i)
            for i in paid_rows:
                rows[i]["contra"], rows[i]["gifi_contra"] = arap, gifi_arap
            if paid_rows and arap_rows:
                # Perl neemt de laatste AR/AP-regel na sorteren op bedrag (de kleinste).
                # Bij gelijke bedragen hangt dat in Perl af van de hash-volgorde; hier
                # wint dan de betalingskant (AR credit / AP debet).
                smallest = amount(arap_rows[-1])
                tied = [k for k in arap_rows if amount(k) == smallest]
                pay_side = [k for k in tied
                            if (rows[k]["type"] == "ar" and rows[k]["amount"] > 0)
                            or (rows[k]["type"] == "ap" and rows[k]["amount"] < 0)]
                i = pay_side[-1] if pay_side else arap_rows[-1]
                arap_rows.remove(i)
                rows[i]["contra"], rows[i]["gifi_contra"] = paid, gifi_paid
            seen = set()
            for i in arap_rows:
                for o in others:
                    key = f"{rows[o]['accno']}{rows[o]['transdate']}"
                    # elke rekening maar één keer per regel (ook bij meerdere datums)
                    if key not in seen and rows[o]["accno"] not in (rows[i]["contra"] or "").split():
                        rows[i]["contra"] = (rows[i]["contra"] or "") + f"{rows[o]['accno']} "
                    seen.add(key)
                    gifi = rows[o]["gifi_accno"] or ""
                    gkey = f"{gifi}{rows[o]['transdate']}"
                    if gkey not in seen and (not gifi or gifi not in (rows[i]["gifi_contra"] or "").split()):
                        rows[i]["gifi_contra"] = (rows[i]["gifi_contra"] or "") + f"{gifi} "
                    seen.add(gkey)
            for o in others:
                rows[o]["contra"], rows[o]["gifi_contra"] = arap, gifi_arap
            continue

        # zuivere memoriaalboeking
        for i in ordered:
            r = rows[i]
            amt = amount(i)
            rev = "credit" if r["debit"] else "debit"
            if not amt:
                continue
            found = False
            # Perl kiest bij meerdere passende regels via de hash-volgorde; hier
            # bij voorkeur een andere rekening dan de eigen.
            matches = [k for k in indices if rows[k][rev] == amt]
            matches.sort(key=lambda k: rows[k]["accno"] == r["accno"])
            if matches:
                k = matches[0]
                r["contra"], r["gifi_contra"] = rows[k]["accno"], rows[k]["gifi_accno"]
                found = True
            if found:
                continue
            contra, gifi = "", ""
            remaining = amt
            for k in by_amount_desc(indices):
                if rows[k][rev]:
                    contra += f"{rows[k]['accno']} "
                    gifi += f"{rows[k]['gifi_accno'] or ''} "
                    remaining = round_amount(remaining - rows[k][rev], 10)
                    if remaining <= 0:
                        break
            r["contra"] = " ".join(sorted(_perl_split(contra)))
            r["gifi_contra"] = " ".join(sorted(_perl_split(gifi)))


def _perl_split(text: str) -> List[str]:
    """Perl ``split / /``: splitsen op spatie, lege velden achteraan vervallen."""
    parts = text.split(" ")
    while parts and parts[-1] == "":
        parts.pop()
    return parts
