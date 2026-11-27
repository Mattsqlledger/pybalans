"""Debiteuren/crediteuren-rapporten en jaarafsluiting.

Port van SL/RP.pm (aging, reminder, save_level, get_taxaccounts, tax_report,
paymentaccounts, payments, yearend_statement) en SL/AM.pm (post_yearend,
earningsaccounts), plus de rekenlogica uit bin/mozilla/rp.pl en am.pl
(subtotalen, totalen, resultaatregel van de jaarafsluiting).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import psycopg

from . import _db, support
from .amounts import round_amount, to_decimal
from .dates import DateLike, from_to, parse_date
from .errors import ClosedPeriodError, ValidationError
from .gl import _closedto, _num, like, sort_order
from .numbering import next_number
from .reports import DisplayRow

ZERO = Decimal(0)
BUCKETS = ("c0", "c15", "c30", "c45", "c60", "c75", "c90")
BUCKET_DAYS = {"c0": 0, "c15": 15, "c30": 30, "c45": 45, "c60": 60, "c75": 75, "c90": 90}


def open_asof(arap: str, param: str = "todate") -> str:
    """SQL: openstaand bedrag van boeking ``a`` op de peildatum ``%(param)s`` (standaardvaluta).

    Perl rekent met het huidige ``paid`` (RP.pm aging/reminder): een overzicht met een oude datum toont
    dan betalingen van ná die datum al als betaald. Hier: huidig openstaand + wat er ná de peildatum op de
    debiteuren-/crediteurenrekening van de boeking is afgeboekt (betalingen staan daar tegen de koers van
    de factuur, dus zonder koersverschillen). Met een peildatum op of na de laatste betaling is dit
    gelijk aan ``amount - paid``."""
    link, sign = ("AR", "+") if arap == "ar" else ("AP", "-")
    return (f"(a.amount::numeric - a.paid::numeric {sign} COALESCE((SELECT SUM(ac.amount::numeric) "
            f"FROM acc_trans ac JOIN chart ch ON (ch.id = ac.chart_id) WHERE ac.trans_id = a.id "
            f"AND ac.transdate > %({param})s::date AND '{link}' = ANY(string_to_array(ch.link, ':'))), 0))")


def _vc(arap: str) -> str:
    if arap not in ("ar", "ap"):
        raise ValueError("arap moet 'ar' of 'ap' zijn")
    return "customer" if arap == "ar" else "vendor"


@dataclass
class AgingReport:
    """Ouderdomsanalyse. rows: één regel per openstaande factuur met de
    gekozen ouderdomsklassen (c0..c90, omgerekend naar de standaardvaluta);
    subtotals per (valuta, relatie); totals per valuta."""

    arap: str
    todate: date
    buckets: List[str]
    rows: List[Dict[str, Any]]
    subtotals: List[Dict[str, Any]]
    totals: Dict[str, Dict[str, Decimal]]
    display: List[DisplayRow]
    info: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReminderReport:
    rows: List[Dict[str, Any]]
    info: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TaxReport:
    """Btw-rapport. rows per factuur (samenvatting) of per regel (detail);
    account_totals per btw-rekening; total over alles."""

    db: str
    rows: List[Dict[str, Any]]
    account_totals: List[Dict[str, Any]]
    total: Dict[str, Decimal]
    display: List[DisplayRow]
    fromdate: Optional[date] = None
    todate: Optional[date] = None
    info: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PaymentReport:
    """Betalingen per betaalrekening, met totaal per rekening en eindtotaal."""

    db: str
    accounts: List[Dict[str, Any]]      # {accno, description, id, payments: [...], total}
    total: Decimal
    display: List[DisplayRow]
    info: Dict[str, Any] = field(default_factory=dict)


@dataclass
class YearendPreview:
    """Voorstel voor de afsluitboeking: opbrengsten en kosten naar nul,
    resultaat naar de rekening 'onverdeelde winst'."""

    todate: date
    lines: List[Dict[str, Any]]          # {accno, debit, credit}
    earnings: Decimal


class ArapReports:
    """Mixin voor Reports: rapporten over debiteuren, crediteuren, btw en betalingen."""

    conn: psycopg.Connection

    # ========================================================== ouderdomsanalyse

    def aging(
        self,
        arap: str = "ar",
        *,
        todate: DateLike = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
        buckets: Sequence[str] = BUCKETS,
        overdue: bool = False,
        vc_id: Optional[int] = None,
        name: Optional[str] = None,
        vcnumber: Optional[str] = None,
        department_id: Optional[int] = None,
        sort: Optional[str] = None,
        summary: bool = False,
    ) -> AgingReport:
        """Openstaande posten per ouderdomsklasse (RP::aging + rp.pl aging).

        buckets: welke kolommen ('c0' = lopend, 'c15' .. 'c90' dagen). De
        laatste gekozen kolom bevat alles wat ouder is. overdue=True rekent
        vanaf de vervaldatum in plaats van de factuurdatum.
        """
        cfg = self.config
        vc = _vc(arap)
        tdate = parse_date(todate, cfg.dateformat)
        if not tdate and year and month:
            _, tdate = from_to(year, month)
        tdate = tdate or date.today()
        datecol = "duedate" if overdue else "transdate"
        sel = [b for b in BUCKETS if b in set(buckets)]
        numcol = f"{vc}number"
        sortcol = sort if sort in ("name", numcol, "id") else "name"

        with _db.cursor(self.conn) as cur:
            info = _db.get_defaults(cur, "company", "address", "businessnumber", "tel", "fax",
                                    "precision", "companyemail", "companywebsite")
            precision = int(to_decimal(info.get("precision") or 2))
            currencies = list(_db.currencies(cur))

            where = ["a.approved = '1'"]
            params: Dict[str, Any] = {"todate": tdate}
            if vc_id:
                where.append("vc.id = %(vc_id)s")
                params["vc_id"] = int(vc_id)
            else:
                if name:
                    where.append("lower(vc.name) LIKE %(name)s")
                    params["name"] = like(name.lower())
                if vcnumber:
                    where.append(f"lower(vc.{numcol}) LIKE %(vcnumber)s")
                    params["vcnumber"] = like(vcnumber.lower())
            if department_id:
                where.append("a.department_id = %(dept)s")
                params["dept"] = int(department_id)
            openamt = open_asof(arap)
            cur.execute(
                f"""SELECT DISTINCT vc.id, vc.name, vc.{numcol}, vc.language_code
                    FROM {vc} vc JOIN {arap} a ON (a.{vc}_id = vc.id)
                    WHERE {' AND '.join(where)} AND a.{datecol} <= %(todate)s AND {openamt} != 0
                    ORDER BY vc.{sortcol}""", params)
            outstanding = cur.fetchall()

            rows: List[Dict[str, Any]] = []
            if sel:
                cond: Dict[str, str] = {}
                last = sel[-1]
                # Perl (RP.pm r1331): ook bij één gekozen kolom "<", dan vallen boekingen van de peildatum
                # zelf weg (met alleen "lopend"); hier dan "<=" (bewuste afwijking)
                op = "<" if len(sel) > 1 else "<="
                cond[last] = f"AND a.{datecol} {op} %(todate)s::date - {BUCKET_DAYS[last]}"
                if len(sel) > 1:
                    cond[sel[0]] = (f"AND (a.{datecol} <= %(todate)s::date - {BUCKET_DAYS[sel[0]]} "
                                    f"AND a.{datecol} >= %(todate)s::date - {BUCKET_DAYS[sel[1]]})")
                for i in range(1, len(sel) - 1):
                    cond[sel[i]] = (f"AND (a.{datecol} < %(todate)s::date - {BUCKET_DAYS[sel[i]]} "
                                    f"AND a.{datecol} >= %(todate)s::date - {BUCKET_DAYS[sel[i + 1]]})")
                dept = "AND a.department_id = %(dept)s" if department_id else ""
                parts = []
                for b in sel:
                    flds = ", ".join(f"{openamt} AS {x}" if x == b else f"0.00 AS {x}" for x in BUCKETS)
                    # Perl joint ook op contact en shipto; bij meerdere contactpersonen
                    # telde een factuur dan dubbel. Hier één contactpersoon per relatie.
                    parts.append(f"""
                        SELECT c.id AS vc_id, c.{numcol}, c.name,
                               ad.address1, ad.address2, ad.city, ad.state, ad.zipcode, ad.country,
                               c.contact, c.phone AS {vc}phone, c.fax AS {vc}fax,
                               c.taxnumber AS {vc}taxnumber, a.description AS invdescription,
                               a.invnumber, a.transdate, a.till, a.ordnumber, a.ponumber, a.notes,
                               {flds}, a.duedate, a.invoice, a.id, a.curr,
                               (SELECT exchangerate FROM exchangerate e
                                WHERE a.curr = e.curr AND e.transdate = a.transdate) AS exchangerate,
                               ct.firstname, ct.lastname, ct.salutation, ct.typeofcontact
                        FROM {arap} a
                        JOIN {vc} c ON (a.{vc}_id = c.id)
                        JOIN address ad ON (ad.trans_id = c.id)
                        LEFT JOIN LATERAL (SELECT * FROM contact x WHERE x.trans_id = c.id
                                           ORDER BY x.id LIMIT 1) ct ON TRUE
                        WHERE {openamt} != 0 AND a.approved = '1'
                          AND c.id = %(vc)s AND a.curr = %(curr)s {dept}
                          {cond[b]}""")
                # Perl: ORDER BY 1, 17, 16 (vc_id, transdate, invnumber) op zijn eigen
                # kolomlijst; hier op naam omdat de kolomlijst korter is
                cols = ["vc_id", "transdate", "invnumber"]
                if sort in ("name", numcol):
                    cols = [sort] + cols
                order = ", ".join(f"{c} ASC" if i == 0 else c for i, c in enumerate(cols))
                query = " UNION ".join(parts) + f" ORDER BY {order}"
                invoice_module = "is" if arap == "ar" else "ir"
                for curr in currencies or [""]:
                    for vcrow in outstanding:
                        cur.execute(query, {"todate": tdate, "vc": vcrow["id"], "curr": curr,
                                            "dept": department_id})
                        for ref in cur.fetchall():
                            ref["curr"] = (ref["curr"] or "").strip()
                            ref["module"] = invoice_module if ref["invoice"] else arap
                            if ref["till"]:
                                ref["module"] = "ps"
                            ref["exchangerate"] = to_decimal(ref["exchangerate"]) or Decimal(1)
                            ref["language_code"] = vcrow["language_code"]
                            for b in BUCKETS:
                                ref[b] = round_amount(to_decimal(ref[b]) / ref["exchangerate"], precision)
                            rows.append(ref)

        subtotals, totals, display = _aging_display(rows, sel, summary)
        info.update(currencies=currencies)
        return AgingReport(arap=arap, todate=tdate, buckets=sel, rows=rows, subtotals=subtotals,
                           totals=totals, display=display, info=info)

    # ================================================================ aanmaningen

    def reminders(
        self,
        *,
        customer_id: Optional[int] = None,
        name: Optional[str] = None,
        customernumber: Optional[str] = None,
        department_id: Optional[int] = None,
        asof: Optional[date] = None,
    ) -> ReminderReport:
        """Vervallen verkoopfacturen met voorgesteld aanmaanniveau (RP::reminder).

        level = hoogste opgeslagen niveau + 1 (1 als er nog geen aanmaning was).
        """
        asof = asof or date.today()
        with _db.cursor(self.conn) as cur:
            info = _db.get_defaults(cur, "company", "companyemail", "companywebsite", "address",
                                    "businessnumber", "tel", "fax", "precision")
            currencies = list(_db.currencies(cur))
            where = ["a.approved = '1'"]
            params: Dict[str, Any] = {"asof": asof}
            if customer_id:
                where.append("vc.id = %(vc_id)s")
                params["vc_id"] = int(customer_id)
            else:
                if name:
                    where.append("lower(vc.name) LIKE %(name)s")
                    params["name"] = like(name.lower())
                if customernumber:
                    where.append("lower(vc.customernumber) LIKE %(vcnumber)s")
                    params["vcnumber"] = like(customernumber.lower())
            if department_id:
                where.append("a.department_id = %(dept)s")
                params["dept"] = int(department_id)
            openamt = open_asof("ar", "asof")
            cur.execute(f"""SELECT DISTINCT vc.id, vc.name, vc.customernumber, vc.language_code
                            FROM customer vc JOIN ar a ON (a.customer_id = vc.id)
                            WHERE {' AND '.join(where)} AND a.duedate <= %(asof)s AND {openamt} != 0
                            ORDER BY vc.name""", params)
            outstanding = cur.fetchall()

            dept = "AND a.department_id = %(dept)s" if department_id else ""
            query = f"""
                SELECT c.id AS vc_id, c.customernumber, c.name, c.terms,
                       ad.address1, ad.address2, ad.city, ad.state, ad.zipcode, ad.country,
                       c.contact, c.phone AS customerphone, c.fax AS customerfax,
                       c.taxnumber AS customertaxnumber,
                       a.dcn, a.bank_id, a.description AS invdescription,
                       a.invnumber, a.transdate, a.till, a.ordnumber, a.ponumber, a.notes,
                       {openamt} AS due, a.duedate, a.invoice, a.id, a.curr,
                       (SELECT exchangerate FROM exchangerate e
                        WHERE a.curr = e.curr AND e.transdate = a.transdate) AS exchangerate,
                       ct.firstname, ct.lastname, ct.salutation, ct.typeofcontact
                FROM ar a
                JOIN customer c ON (a.customer_id = c.id)
                JOIN address ad ON (ad.trans_id = c.id)
                LEFT JOIN LATERAL (SELECT * FROM contact x WHERE x.trans_id = c.id
                                   ORDER BY x.id LIMIT 1) ct ON TRUE
                WHERE a.duedate <= %(asof)s AND {openamt} != 0 AND a.approved = '1'
                  AND c.id = %(vc)s AND a.curr = %(curr)s {dept}
                ORDER BY vc_id ASC, transdate, invnumber"""
            rows = []
            for curr in currencies or [""]:
                for vcrow in outstanding:
                    cur.execute(query, {"asof": asof, "vc": vcrow["id"], "curr": curr,
                                        "dept": department_id})
                    for ref in cur.fetchall():
                        ref["curr"] = (ref["curr"] or "").strip()
                        ref["module"] = "ps" if ref["till"] else ("is" if ref["invoice"] else "ar")
                        ref["exchangerate"] = to_decimal(ref["exchangerate"]) or Decimal(1)
                        ref["due"] = to_decimal(ref["due"])
                        ref["language_code"] = vcrow["language_code"]
                        cur.execute("SELECT b.*, a.address1 AS bankaddress1, a.city AS bankcity "
                                    "FROM bank b LEFT JOIN address a ON (a.id = b.address_id) "
                                    "WHERE b.id = %s", (ref["bank_id"],))
                        bank = cur.fetchone() or {}
                        for k in ("rvc", "iban", "bic", "membernumber", "clearingnumber"):
                            ref[k] = bank.pop(k, None)
                        for k, v in bank.items():
                            ref[f"bank{k}"] = v
                        # Perl voegt bij meerdere aanmaanregels dezelfde factuur meermaals
                        # toe, met het niveau van de láágste; hier één regel, hoogste + 1.
                        cur.execute("""SELECT s.formname FROM status s JOIN ar a ON (a.id = s.trans_id)
                                       WHERE s.formname LIKE 'reminder_' AND s.trans_id = %s
                                         AND a.curr = %s ORDER BY s.formname DESC""",
                                    (ref["id"], curr))
                        levels = [int(r["formname"][-1]) for r in cur.fetchall()
                                  if r["formname"][-1].isdigit()]
                        ref["level"] = (max(levels) if levels else 0) + 1
                        rows.append(ref)
        info.update(currencies=currencies, asof=asof)
        return ReminderReport(rows=rows, info=info)

    def save_reminder_levels(self, levels: Mapping[int, int]) -> None:
        """Sla per factuur het aanmaanniveau op (RP::save_level). Niveau 0 wist het."""
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            for trans_id, level in levels.items():
                cur.execute("DELETE FROM status WHERE trans_id = %s AND formname LIKE 'reminder_'",
                            (int(trans_id),))
                if int(level):
                    cur.execute("INSERT INTO status (trans_id, formname) VALUES (%s, %s)",
                                (int(trans_id), f"reminder{int(level)}"))

    # ===================================================================== btw

    def tax_accounts(self, db: str = "ar") -> Dict[str, List[Dict[str, Any]]]:
        """Btw-rekeningen voor verkoop ('ar') of inkoop ('ap') (RP::get_taxaccounts)."""
        _vc(db)
        ARAP = db.upper()
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT DISTINCT c.accno, c.description, l.description AS translation
                           FROM chart c JOIN tax t ON (c.id = t.chart_id)
                           LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %s)
                           WHERE c.link LIKE %s AND c.closed = '0'
                           ORDER BY c.accno""", (self.config.countrycode, f"%{ARAP}_tax%"))
            accounts = cur.fetchall()
            cur.execute("""SELECT DISTINCT g.accno, g.description FROM gifi g
                           JOIN chart c ON (c.gifi_accno = g.accno)
                           JOIN tax t ON (c.id = t.chart_id)
                           WHERE c.link LIKE %s ORDER BY g.accno""", (f"%{ARAP}_tax%",))
            gifi = cur.fetchall()
        return {"taxaccounts": accounts, "gifi_taxaccounts": gifi}

    def tax_report(
        self,
        db: str = "ar",
        *,
        accounts: Optional[Iterable[str]] = None,
        gifi_accounts: Optional[Iterable[str]] = None,
        fromdate: DateLike = None,
        todate: DateLike = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
        interval: Optional[int] = None,
        department_id: Optional[int] = None,
        method: str = "accrual",
        summary: bool = True,
        nontaxable: bool = False,
        sort: Optional[str] = None,
        direction: str = "ASC",
        l_subtotal: bool = False,
    ) -> TaxReport:
        """Btw-rapport (RP::tax_report + rp.pl generate_tax_report).

        accounts: btw-rekeningnummers (of gifi_accounts). summary=True geeft één
        regel per factuur en rekening, anders per journaal-/factuurregel.
        nontaxable=True: alleen onbelaste omzet. method='cash': alleen
        volledig betaalde facturen, op betaaldatum.
        """
        cfg = self.config
        vc = _vc(db)
        ARAP = db.upper()
        ml = 1 if db == "ar" else -1
        fdate = parse_date(fromdate, cfg.dateformat)
        tdate = parse_date(todate, cfg.dateformat)
        if not (fdate or tdate) and year and month:
            fdate, tdate = from_to(year, month, interval)

        p: Dict[str, Any] = {"dept": department_id, "fromdate": fdate, "todate": tdate,
                             "arap": ARAP, "paid": f"%{ARAP}_paid%"}
        where = "a.approved = '1'"
        if department_id:
            where += " AND a.department_id = %(dept)s"
        gifi = [a for a in (gifi_accounts or [])]
        accs = [a for a in (accounts or [])]
        if gifi:
            p["accs"] = gifi
            accno = " AND ch.gifi_accno = ANY(%(accs)s)"
        else:
            p["accs"] = accs or ["0"]
            accno = " AND ch.accno = ANY(%(accs)s)"
        if fdate:
            where += " AND a.transdate >= %(fromdate)s"
        if tdate:
            where += " AND a.transdate <= %(todate)s"

        transdate = "a.transdate"
        cashwhere = ""
        if method == "cash":
            transdate = "a.datepaid"
            p["cashto"] = tdate or date.today()
            cashwhere = """ AND ac.trans_id IN (SELECT trans_id FROM acc_trans
                              JOIN chart ON (chart_id = chart.id)
                              WHERE link LIKE %(paid)s AND a.approved = '1'
                                AND a.datepaid <= %(cashto)s AND a.paid = a.amount)"""

        num = f"{vc}number"
        base_from = f"""FROM acc_trans ac
                        JOIN {db} a ON (a.id = ac.trans_id)
                        JOIN chart ch ON (ch.id = ac.chart_id)
                        JOIN {vc} n ON (n.id = a.{vc}_id)
                        JOIN address ad ON (ad.trans_id = n.id)"""
        cols = (f"a.invnumber, n.name, n.{num}, n.taxnumber, "
                "ad.address1, ad.address2, ad.city, ad.zipcode, ad.country")
        group = (f"GROUP BY a.id, a.invoice, {transdate}, a.invnumber, n.name, a.netamount, a.till, "
                 f"n.id, a.description, n.{num}, n.taxnumber, ad.address1, ad.address2, ad.city, "
                 "ad.zipcode, ad.country, ch.accno")
        prev = cashwhere and fdate

        if nontaxable:
            if summary:
                def q(w):
                    return f"""SELECT DISTINCT a.id, a.invoice, {transdate} AS transdate,
                               a.invnumber, n.name, n.{num},
                               ad.address1, ad.address2, ad.city, ad.zipcode, ad.country,
                               a.netamount, a.description, a.till, n.id AS vc_id
                               FROM acc_trans ac JOIN {db} a ON (a.id = ac.trans_id)
                               JOIN {vc} n ON (n.id = a.{vc}_id)
                               JOIN address ad ON (ad.trans_id = n.id)
                               WHERE {w} AND a.netamount = a.amount {cashwhere}"""
                query = q(where) + (" UNION " + q("a.datepaid >= %(fromdate)s") if prev else "")
            else:
                def q(w):
                    return f"""SELECT a.id, '0' AS invoice, {transdate} AS transdate,
                               a.invnumber, n.name, n.{num},
                               ad.address1, ad.address2, ad.city, ad.zipcode, ad.country,
                               ac.amount::numeric * {ml} AS netamount, ac.memo AS description,
                               a.till, n.id AS vc_id
                               FROM acc_trans ac JOIN {db} a ON (a.id = ac.trans_id)
                               JOIN {vc} n ON (n.id = a.{vc}_id)
                               JOIN address ad ON (ad.trans_id = n.id)
                               JOIN chart ch ON (ch.id = ac.chart_id)
                               WHERE {w} AND a.invoice = '0' AND a.netamount = a.amount
                               AND NOT (ch.link LIKE '%%_paid' OR ch.link = %(arap)s) {cashwhere}
                             UNION ALL
                               SELECT a.id, '1' AS invoice, {transdate} AS transdate,
                               a.invnumber, n.name, n.{num},
                               ad.address1, ad.address2, ad.city, ad.zipcode, ad.country,
                               ac.sellprice::numeric * ac.qty::numeric * {ml} AS netamount, ac.description,
                               a.till, n.id AS vc_id
                               FROM invoice ac JOIN {db} a ON (a.id = ac.trans_id)
                               JOIN {vc} n ON (n.id = a.{vc}_id)
                               JOIN address ad ON (ad.trans_id = n.id)
                               WHERE {w} AND a.invoice = '1' AND a.amount = a.netamount {cashwhere}"""
                query = q(where)
                if prev:
                    query += " UNION " + q("a.datepaid >= %(fromdate)s").replace(" UNION ALL ", " UNION ")
        elif summary:
            def q(w):
                return f"""SELECT a.id, a.invoice, {transdate} AS transdate, {cols},
                           a.netamount, a.description, SUM(ac.amount::numeric) * {ml} AS tax,
                           a.till, n.id AS vc_id, ch.accno
                           {base_from}
                           WHERE {w} {accno} {cashwhere} {group}"""
            query = q(where) + (" UNION " + q("a.datepaid >= %(fromdate)s") if prev else "")
        else:
            def q(w, union):
                return f"""SELECT a.id, '0' AS invoice, {transdate} AS transdate, {cols},
                           a.netamount, ac.memo AS description, ac.amount::numeric * {ml} AS tax,
                           a.till, n.id AS vc_id, ch.accno
                           {base_from}
                           WHERE {w} {accno} AND a.invoice = '0'
                           AND NOT (ch.link LIKE '%%_paid' OR ch.link = %(arap)s) {cashwhere}
                         {union}
                           SELECT a.id, '1' AS invoice, {transdate} AS transdate, {cols},
                           i.sellprice::numeric * i.qty::numeric * {ml} AS netamount, i.description,
                           i.sellprice::numeric * i.qty::numeric * {ml} *
                           (SELECT tx.rate::numeric FROM tax tx WHERE tx.chart_id = ch.id
                            AND (tx.validto > {transdate} OR tx.validto IS NULL)
                            ORDER BY tx.validto LIMIT 1) AS tax,
                           a.till, n.id AS vc_id, ch.accno
                           {base_from}
                           JOIN {vc}tax t ON (t.{vc}_id = n.id AND t.chart_id = ch.id)
                           JOIN invoice i ON (i.trans_id = a.id)
                           JOIN partstax pt ON (pt.parts_id = i.parts_id AND pt.chart_id = ch.id)
                           WHERE {w} {accno} AND a.invoice = '1'
                           AND i.parts_id IN (SELECT parts_id FROM partstax) {cashwhere}"""
            query = q(where, "UNION ALL")
            if prev:
                query += " UNION " + q("a.datepaid >= %(fromdate)s", "UNION")

        with _db.cursor(self.conn) as cur:
            info = _db.get_defaults(cur, "precision", "company")
            precision = int(to_decimal(info.get("precision") or 2))
            cur.execute(f"SELECT * FROM ({query}) x LIMIT 0", p)
            names = [d.name for d in cur.description]
            ordinal = {n: i + 1 for i, n in enumerate(names)}
            order = sort_order(["transdate", "invnumber", "name"], sort, direction, ordinal)
            if not nontaxable:
                order = f"{ordinal['accno']} ASC, {order}"
            cur.execute(f"{query} ORDER BY {order}", p)
            rows = []
            for ref in cur.fetchall():
                ref["netamount"] = round_amount(to_decimal(ref["netamount"]), precision)
                ref["tax"] = round_amount(to_decimal(ref.get("tax")), precision)
                ref["total"] = ref["netamount"] + ref["tax"]
                ref["address"] = "".join(f"{ref[k]} " for k in ("address1", "address2", "city", "zipcode")
                                         if ref.get(k))
                if (ref["netamount"] if nontaxable else ref["tax"]):
                    rows.append(ref)

        account_totals, total, display = _tax_display(rows, sort, l_subtotal)
        return TaxReport(db=db, rows=rows, account_totals=account_totals, total=total,
                         display=display, fromdate=fdate, todate=tdate, info=info)

    # ================================================================ betalingen

    def payment_accounts(self, db: str = "ar") -> List[Dict[str, Any]]:
        """Betaalrekeningen (link AR_paid / AP_paid)."""
        _vc(db)
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT c.accno, c.description, l.description AS translation
                           FROM chart c
                           LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %s)
                           WHERE c.link LIKE %s ORDER BY c.accno""",
                        (self.config.countrycode, f"%{db.upper()}_paid%"))
            rows = cur.fetchall()
        for r in rows:
            r["description"] = r["translation"] or r["description"]
        return rows

    def payments(
        self,
        db: str = "ar",
        *,
        accounts: Optional[Iterable[str]] = None,
        fromdate: DateLike = None,
        todate: DateLike = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
        interval: Optional[int] = None,
        department_id: Optional[int] = None,
        description: Optional[str] = None,
        source: Optional[str] = None,
        memo: Optional[str] = None,
        name: Optional[str] = None,
        vcnumber: Optional[str] = None,
        fx_transaction: bool = False,
        till: Optional[str] = None,
        sort: Optional[str] = None,
        direction: str = "ASC",
        l_subtotal: bool = False,
    ) -> PaymentReport:
        """Ontvangsten ('ar') of betalingen ('ap') per betaalrekening (RP::payments)."""
        cfg = self.config
        vc = _vc(db)
        ml = -1 if db == "ar" else 1
        fdate = parse_date(fromdate, cfg.dateformat)
        tdate = parse_date(todate, cfg.dateformat)
        if not (fdate or tdate) and year and month:
            fdate, tdate = from_to(year, month, interval)
        if accounts is None:
            accounts = [a["accno"] for a in self.payment_accounts(db)]

        p: Dict[str, Any] = {"dept": department_id, "fromdate": fdate, "todate": tdate}
        where = "1 = 1"
        dpt_join = ""
        with_gl = till is None or till == ""
        if department_id:
            dpt_join = "JOIN dpt_trans t ON (t.trans_id = ac.trans_id)"
            where += " AND t.department_id = %(dept)s"
        if fdate:
            where += " AND ac.transdate >= %(fromdate)s"
        if tdate:
            where += " AND ac.transdate <= %(todate)s"
        if not fx_transaction:
            where += " AND ac.fx_transaction = '0'"
        for key, col, val in (("description", "a.description", description),
                              ("source", "ac.source", source), ("memo", "ac.memo", memo)):
            if val:
                where += f" AND lower({col}) LIKE %({key})s"
                p[key] = like(val.lower())
        num = f"{vc}number"
        if vcnumber:
            where += f" AND lower(c.{num}) LIKE %(vcnumber)s"
            p["vcnumber"] = like(vcnumber.lower())
            with_gl = False
        if name:
            where += " AND lower(c.name) LIKE %(name)s"
            p["name"] = like(name.lower())
            with_gl = False

        result = []
        grand = ZERO
        with _db.cursor(self.conn) as cur:
            info = _db.get_defaults(cur, "precision", "company")
            for accno in accounts:
                cur.execute("""SELECT c.id, c.accno, c.description, l.description AS translation
                               FROM chart c
                               LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %s)
                               WHERE c.accno = %s""", (cfg.countrycode, accno))
                acc = cur.fetchone()
                if not acc:
                    continue
                acc["description"] = acc["translation"] or acc["description"]
                p["chart"] = acc["id"]
                query = f"""SELECT a.description, c.name, ac.transdate,
                                   SUM(ac.amount::numeric) * {ml} AS paid,
                                   ac.source, ac.memo, e.name AS employee, a.till, a.curr,
                                   '{db}' AS module, ac.trans_id, c.id AS vcid, a.invoice,
                                   c.{num}, a.invnumber AS reference
                            FROM acc_trans ac
                            JOIN {db} a ON (ac.trans_id = a.id)
                            JOIN {vc} c ON (c.id = a.{vc}_id)
                            LEFT JOIN employee e ON (a.employee_id = e.id)
                            {dpt_join}
                            WHERE {where} AND ac.chart_id = %(chart)s AND ac.approved = '1'"""
                if till:
                    query += " AND a.invoice = '1' AND NOT a.till IS NULL"
                query += f""" GROUP BY a.description, c.name, ac.transdate, ac.source, ac.memo,
                              e.name, a.till, a.curr, ac.trans_id, c.id, a.invoice, c.{num}, a.invnumber"""
                if with_gl:
                    query += f"""
                      UNION ALL
                        SELECT a.description, '' AS name, ac.transdate,
                               SUM(ac.amount::numeric) * {ml} AS paid, ac.source, ac.memo,
                               e.name AS employee, '' AS till, '' AS curr, 'gl' AS module,
                               ac.trans_id, '0' AS vcid, '0' AS invoice, '' AS {num}, a.reference
                        FROM acc_trans ac
                        JOIN gl a ON (a.id = ac.trans_id)
                        LEFT JOIN employee e ON (a.employee_id = e.id)
                        {dpt_join}
                        WHERE {where} AND ac.chart_id = %(chart)s AND a.approved = '1'
                          AND (ac.amount * {ml}) > 0
                        GROUP BY a.description, ac.transdate, ac.source, ac.memo, e.name,
                                 ac.trans_id, a.reference"""
                cur.execute(f"SELECT * FROM ({query}) x LIMIT 0", p)
                ordinal = {d.name: i + 1 for i, d in enumerate(cur.description)}
                order = sort_order(["name", "transdate", "employee"], sort, direction, ordinal)
                # gelijke sorteerwaarden: vaste volgorde (Perl: willekeurige databasevolgorde)
                order += f", {ordinal['trans_id']}, {ordinal['source']}, {ordinal['paid']}"
                cur.execute(f"{query} ORDER BY {order}", p)
                pays = cur.fetchall()
                for pr in pays:
                    pr["paid"] = to_decimal(pr["paid"])
                    pr["curr"] = (pr["curr"] or "").strip()
                    if pr["module"] != "gl":
                        pr["module"] = (("ps" if pr["till"] else ("is" if db == "ar" else "ir"))
                                        if pr["invoice"] else db)
                acc["payments"] = pays
                result.append(acc)

        display, grand = _payment_display(result, sort, till, l_subtotal,
                                          int(to_decimal(info.get("precision") or 2)))
        return PaymentReport(db=db, accounts=result, total=grand, display=display, info=info)

    # ============================================================ jaarafsluiting

    def earnings_accounts(self) -> List[Dict[str, Any]]:
        """Rekeningen voor het resultaat (eigen vermogen, AM::earningsaccounts)."""
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT c.accno, c.description, l.description AS translation
                           FROM chart c
                           LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %s)
                           WHERE c.charttype = 'A' AND c.category = 'Q'
                           ORDER BY c.accno""", (self.config.countrycode,))
            rows = cur.fetchall()
        for r in rows:
            r["description"] = r["translation"] or r["description"]
        return rows

    def last_yearend_account(self) -> Optional[str]:
        """Resultaatrekening van de laatste jaarafsluiting (voorstel op het scherm, golf 2 F4), of None."""
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT c.accno
                           FROM yearend y
                           JOIN acc_trans ac ON (ac.trans_id = y.trans_id)
                           JOIN chart c ON (c.id = ac.chart_id)
                           WHERE c.charttype = 'A' AND c.category = 'Q'
                           ORDER BY y.transdate DESC, y.trans_id DESC
                           LIMIT 1""")
            row = cur.fetchone()
        return row["accno"] if row else None

    def yearend_preview(self, todate: DateLike, retained_earnings: str,
                        method: str = "accrual") -> YearendPreview:
        """Bereken de afsluitboeking per todate zonder iets te schrijven.

        Afwijkingen van Perl: Perl verwijdert al bij het berekenen latere
        jaarafsluitingen (buiten de transactie), en telt het resultaat op uit
        onafgeronde bedragen terwijl de regels wel worden afgerond -- de
        boeking kon daardoor een fractie uit balans zijn. Hier is het resultaat
        de som van de afgeronde regels.

        Bestaande afsluitingen op of na todate worden bij boeken eerst verwijderd;
        het voorbeeld rekent daarom zonder die afsluitingen (verwijderen in een
        transactie die daarna wordt teruggedraaid), zodat het precies toont wat
        post_yearend zou boeken.
        """
        result: List[YearendPreview] = []
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            result.append(self._yearend_lines(cur, parse_date(todate, self.config.dateformat),
                                              retained_earnings, method, delete_later=True))
            raise psycopg.Rollback()
        return result[0]

    def _yearend_lines(self, cur, tdate: date, accno: str, method: str,
                       delete_later: bool) -> YearendPreview:
        if not tdate:
            raise ValidationError("Yearend date missing!")
        if delete_later:
            cur.execute("SELECT trans_id FROM yearend WHERE transdate >= %s", (tdate,))
            for (r) in cur.fetchall():
                for table, col in (("gl", "id"), ("acc_trans", "trans_id"), ("yearend", "trans_id")):
                    cur.execute(f"DELETE FROM {table} WHERE {col} = %s", (r["trans_id"],))
        precision = _db.get_precision(cur)
        opts = self._options(cur, None, tdate, precision, None, None, "standard", method, None)
        opts["exchangerate"] = Decimal(1)
        lines = []
        earnings = ZERO
        for cat, side in (("I", "debit"), ("E", "credit")):
            c = self._get_accounts(cur, None, tdate, cat, opts)
            for acc in sorted(c):
                e = c[acc]
                if e.get("charttype") != "A":
                    continue
                amount = round_amount(to_decimal(e.get("amount")), precision)
                if not amount:
                    continue
                earnings += amount
                lines.append({"accno": acc, "debit": amount if side == "debit" else ZERO,
                              "credit": -amount if side == "credit" else ZERO})
        if earnings > 0:
            lines.append({"accno": accno, "debit": ZERO, "credit": earnings})
        elif earnings < 0:
            lines.append({"accno": accno, "debit": -earnings, "credit": ZERO})
        return YearendPreview(todate=tdate, lines=lines, earnings=earnings)

    def post_yearend(self, todate: DateLike, retained_earnings: str, *,
                     reference: Optional[str] = None, description: Optional[str] = None,
                     notes: Optional[str] = None, method: str = "accrual") -> int:
        """Boek de jaarafsluiting (RP::yearend_statement + am.pl + AM::post_yearend).

        Verwijdert eerst bestaande jaarafsluitingen op of na todate, boekt dan
        opbrengsten en kosten naar nul tegen de resultaatrekening. Alles in één
        transactie. Geeft het gl-id terug.
        """
        cfg = self.config
        tdate = parse_date(todate, cfg.dateformat)
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("SELECT 1 FROM chart WHERE accno = %s AND charttype = 'A' AND category = 'Q'",
                        (retained_earnings,))
            if not cur.fetchone():
                raise ValidationError(f"Not a retained earnings account: {retained_earnings}")
            # Perl controleerde dit niet: een afsluiting in een afgesloten periode (en het
            # verwijderen van latere afsluitingen daarin) veranderde afgesloten jaren (README 41)
            closedto = _closedto(_db.get_defaults(cur, "closedto"), cfg.dateformat)
            if closedto and _num(tdate) <= closedto:
                raise ClosedPeriodError("Cannot post transaction for a closed period!")
            pre = self._yearend_lines(cur, tdate, retained_earnings, method, delete_later=True)
            if not pre.lines or not pre.earnings:
                raise ValidationError("Nothing to do!")
            curr = _db.default_currency(cur)
            cur.execute("""INSERT INTO gl (employee_id, curr)
                           VALUES ((SELECT id FROM employee WHERE login = %s), %s) RETURNING id""",
                        (cfg.login, curr))
            trans_id = cur.fetchone()["id"]
            reference = reference or next_number(cur, "glnumber", dateformat=cfg.dateformat,
                                                 transdate=tdate, fields={"description": description})
            cur.execute("""UPDATE gl SET reference = %s, description = %s, notes = %s, transdate = %s,
                                  department_id = 0, exchangerate = 1 WHERE id = %s""",
                        (reference, description, notes, tdate, trans_id))
            for line in pre.lines:
                amount = line["credit"] or -line["debit"]
                if amount:
                    cur.execute("""INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, source)
                                   VALUES (%s, (SELECT id FROM chart WHERE accno = %s), %s, %s, %s)""",
                                (trans_id, line["accno"], amount, tdate, reference))
            cur.execute("INSERT INTO yearend (trans_id, transdate) VALUES (%s, %s)", (trans_id, tdate))
            support.audittrail(cur, cfg, trans_id=trans_id, tablename="gl", reference=reference,
                               formname="yearend", action="posted")
        return trans_id


# ------------------------------------------------------------------ weergave

def _aging_display(rows, sel, summary):
    """rp.pl aging: regels per factuur (met lopend totaal per relatie),
    subtotaal per relatie, totaal per valuta."""
    display: List[DisplayRow] = []
    subtotals: List[Dict[str, Any]] = []
    totals: Dict[str, Dict[str, Decimal]] = {}
    curr = None
    vc_id = None
    sub = {b: ZERO for b in sel}
    linetotal = ZERO
    for k, ref in enumerate(rows):
        if curr != ref["curr"]:
            if curr is not None:
                display.append(DisplayRow("total", curr, None,
                                          [totals[curr][b] for b in sel] + [totals[curr]["total"]]))
            vc_id = None
            totals[ref["curr"]] = {**{b: ZERO for b in sel}, "total": ZERO}
        curr = ref["curr"]
        if vc_id != ref["vc_id"]:
            linetotal = ZERO
        vc_id = ref["vc_id"]
        t = totals[curr]
        for b in sel:
            t[b] += ref[b]
            sub[b] += ref[b]
            linetotal += ref[b]
            t["total"] += ref[b]
        ref["linetotal"] = linetotal
        if not summary:
            display.append(DisplayRow("row", ref["name"], ref.get("invnumber"),
                                      [ref[b] for b in sel] + [linetotal]))
        nxt = rows[k + 1] if k + 1 < len(rows) else None
        if nxt is None or nxt["vc_id"] != vc_id or nxt["curr"] != curr:
            entry = {"curr": curr, "vc_id": vc_id, "name": ref["name"], **dict(sub), "total": linetotal}
            subtotals.append(entry)
            if summary:
                display.append(DisplayRow("row", ref["name"], None,
                                          [sub[b] for b in sel] + [linetotal]))
            else:
                display.append(DisplayRow("subtotal", ref["name"], None, [sub[b] for b in sel]))
            sub = {b: ZERO for b in sel}
    if curr is not None:
        display.append(DisplayRow("total", curr, None,
                                  [totals[curr][b] for b in sel] + [totals[curr]["total"]]))
    else:
        display.append(DisplayRow("total", "", None, [ZERO for _ in sel] + [ZERO]))
    return subtotals, totals, display


def _tax_display(rows, sort, l_subtotal):
    """rp.pl generate_tax_report: totaal per btw-rekening, optioneel subtotaal
    per sorteerwaarde, eindtotaal."""
    display: List[DisplayRow] = []
    account_totals: List[Dict[str, Any]] = []
    keys = ("netamount", "tax", "total")
    zero = lambda: {k: ZERO for k in keys}  # noqa: E731
    total, subtotal, acctotal = zero(), zero(), zero()
    same_item = rows[0].get(sort) if rows and sort else None
    same_acc = None
    has_acc = False
    for ref in rows:
        if l_subtotal and ref.get(sort) != same_item:
            display.append(DisplayRow("subtotal", "subtotal", None, [subtotal[k] for k in keys]))
            subtotal = zero()
            same_item = ref.get(sort)
        if ref.get("accno") != same_acc:
            if has_acc:
                display.append(DisplayRow("subtotal", "acctotal", same_acc, [acctotal[k] for k in keys]))
                account_totals.append({"accno": same_acc, **acctotal})
                acctotal = zero()
            has_acc = True
        same_acc = ref.get("accno")
        for agg in (total, subtotal, acctotal):
            for k in keys:
                agg[k] += ref[k]
        display.append(DisplayRow("row", ref.get("name") or "", ref.get("accno"), [ref[k] for k in keys]))
    if l_subtotal:
        display.append(DisplayRow("subtotal", "subtotal", None, [subtotal[k] for k in keys]))
    display.append(DisplayRow("subtotal", "acctotal", same_acc, [acctotal[k] for k in keys]))
    if has_acc:
        account_totals.append({"accno": same_acc, **acctotal})
    display.append(DisplayRow("total", "Total", None, [total[k] for k in keys]))
    return account_totals, total, display


def _payment_display(accounts, sort, till, l_subtotal, precision=2):
    """rp.pl list_payments: regels per rekening, subtotaal per sorteerwaarde
    (alleen als niet nul), totaal per rekening en eindtotaal."""
    display: List[DisplayRow] = []
    grand = ZERO
    for acc in sorted(accounts, key=lambda a: a["accno"]):
        pays = acc["payments"]
        if not pays:
            acc["total"] = ZERO
            continue
        same = pays[0].get(sort) if sort else None
        sub = acct = ZERO
        for pr in pays:
            if l_subtotal and pr.get(sort) != same:
                # alleen tonen als het subtotaal op centen niet nul is (float-ruis in data)
                if round_amount(sub, precision):
                    display.append(DisplayRow("subtotal", "", acc["accno"], [sub]))
                sub = ZERO
            if till and not pr.get("till"):
                continue
            sub += pr["paid"]
            acct += pr["paid"]
            grand += pr["paid"]
            display.append(DisplayRow("row", pr.get("name") or "", acc["accno"], [pr["paid"]]))
            same = pr.get(sort) if sort else None
        if l_subtotal and round_amount(sub, precision):
            display.append(DisplayRow("subtotal", "", acc["accno"], [sub]))
        acc["total"] = acct
        display.append(DisplayRow("total", acc["accno"], acc["accno"], [acct]))
    display.append(DisplayRow("total", "Total", None, [grand]))
    return display, grand
