"""Grootboekrapporten: proefbalans, winst- en verliesrekening, balans.

Port van SL/RP.pm (trial_balance, income_statement, balance_sheet,
get_accounts, add_accounts) plus de rekenlogica uit bin/mozilla/rp.pl
(subtotalen per kop, totalen, resultaat, resultaat lopend boekjaar).

Bedragen in ``acc_trans`` zijn debet negatief / credit positief. In de
weergaverijen (``display``) staan ze zoals SQL-Ledger ze toont: opbrengsten,
kosten, activa, passiva en eigen vermogen positief.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import psycopg

from . import _db
from .amounts import round_amount, to_decimal
from .config import UserConfig
from .dates import DateLike, add_date, from_to, last_day_of_month, parse_date

ZERO = Decimal(0)
Column = Tuple[str, int]          # ('this' | 'previous', periode-index)


@dataclass
class DisplayRow:
    """Eén regel zoals het SQL-Ledger-scherm hem toont.

    kind: 'section', 'heading', 'account', 'subtotal', 'current_earnings',
          'total', 'result' (W&V) -- of bij de proefbalans 'heading',
          'account', 'subtotal', 'total'.
    values: per kolom (zie ``columns`` van het rapport).
    """

    kind: str
    description: str
    accno: Optional[str] = None
    values: List[Optional[Decimal]] = field(default_factory=list)
    category: Optional[str] = None


@dataclass
class TrialBalance:
    rows: List[Dict[str, Any]]
    display: List[DisplayRow]
    total_debit: Decimal
    total_credit: Decimal
    fromdate: Optional[date] = None
    todate: Optional[date] = None
    columns: Tuple[str, ...] = ("begbalance", "debit", "credit", "endbalance")
    info: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StatementPeriod:
    index: int
    this: Tuple[Optional[date], Optional[date]]
    previous: Optional[Tuple[Optional[date], Optional[date]]] = None


@dataclass
class FinancialStatement:
    """Winst- en verliesrekening of balans.

    columns   -- kolomvolgorde als (jaar, periode); jaar is 'this' of 'previous'
    periods   -- periode-index -> datums
    accounts  -- categorie -> accno -> {description, charttype, amounts{kolom: bedrag}}
                 (bedragen in weergaveteken)
    totals    -- categorie -> kolom -> totaal (weergaveteken)
    result    -- W&V: resultaat per kolom; balans: resultaat lopend boekjaar per kolom
    display   -- regels zoals het scherm ze toont
    """

    kind: str
    columns: List[Column]
    periods: Dict[int, StatementPeriod]
    accounts: Dict[str, Dict[str, Dict[str, Any]]]
    totals: Dict[str, Dict[Column, Decimal]]
    result: Dict[Column, Decimal]
    display: List[DisplayRow]
    currency: Optional[str] = None
    exchangerate: Decimal = Decimal(1)
    info: Dict[str, Any] = field(default_factory=dict)


CATEGORY_LABELS = {"I": "Income", "E": "Expenses", "A": "Assets", "L": "Liabilities", "Q": "Equity"}
COMPANY_FIELDS = ("company", "address", "businessnumber", "companywebsite", "companyemail", "tel", "fax")


class _GLReports:

    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        self.conn = conn
        self.config = config

    # ================================================================ proefbalans

    def trial_balance(
        self,
        *,
        fromdate: DateLike = None,
        todate: DateLike = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
        interval: Optional[int] = None,
        department_id: Optional[int] = None,
        project_id: Optional[int] = None,
        accounttype: str = "standard",
        all_accounts: bool = False,
        language_code: Optional[str] = None,
        l_heading: bool = True,
        l_subtotal: bool = True,
    ) -> TrialBalance:
        """Proefbalans (RP::trial_balance + rp.pl list_accounts).

        Per rekening: beginsaldo (vóór fromdate), debet en credit in de periode
        en eindsaldo. Zonder all_accounts vallen rekeningen zonder mutaties in
        de periode weg (zoals in SQL-Ledger).
        """
        cfg = self.config
        lang = cfg.countrycode if language_code is None else language_code
        gifi = accounttype == "gifi"
        fdate = parse_date(fromdate, cfg.dateformat)
        tdate = parse_date(todate, cfg.dateformat)
        if not (fdate or tdate) and year and month:
            fdate, tdate = from_to(year, month, interval)

        with _db.cursor(self.conn) as cur:
            info = _db.get_defaults(cur, "precision", "company")
            precision = int(to_decimal(info.get("precision") or 2))

            dpt_join, dpt_where, project = "", "", ""
            params: Dict[str, Any] = {"lang": lang, "dept": department_id, "project": project_id,
                                      "fromdate": fdate, "todate": tdate}
            if department_id:
                dpt_join = "JOIN dpt_trans t ON (ac.trans_id = t.trans_id)"
                dpt_where = "AND t.department_id = %(dept)s"
            if project_id:
                project = "AND ac.project_id = %(project)s"

            trb: Dict[str, Dict[str, Any]] = {}
            balance: Dict[str, Decimal] = {}

            # beginsaldi
            if fdate:
                if gifi:
                    q = f"""SELECT g.accno, c.category, SUM(ac.amount::numeric) AS amount,
                                   g.description, c.contra, NULL AS translation
                            FROM acc_trans ac
                            JOIN chart c ON (ac.chart_id = c.id)
                            JOIN gifi g ON (c.gifi_accno = g.accno)
                            {dpt_join}
                            WHERE ac.transdate < %(fromdate)s AND ac.approved = '1'
                            {dpt_where} {project}
                            GROUP BY g.accno, c.category, g.description, c.contra"""
                else:
                    q = f"""SELECT c.accno, c.category, SUM(ac.amount::numeric) AS amount,
                                   c.description, c.contra, l.description AS translation
                            FROM acc_trans ac
                            JOIN chart c ON (ac.chart_id = c.id)
                            LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %(lang)s)
                            {dpt_join}
                            WHERE ac.transdate < %(fromdate)s AND ac.approved = '1'
                            {dpt_where} {project}
                            GROUP BY c.accno, c.category, c.description, c.contra, translation"""
                cur.execute(q, params)
                for r in cur.fetchall():
                    balance[r["accno"]] = round_amount(r["amount"], precision)
                    if all_accounts:
                        trb[r["accno"]] = {"description": r["translation"] or r["description"],
                                           "charttype": "A", "category": r["category"],
                                           "contra": r["contra"]}

            # koppen
            if gifi:
                cur.execute("""SELECT g.accno, g.description, c.category, c.contra
                               FROM gifi g JOIN chart c ON (c.gifi_accno = g.accno)
                               WHERE c.charttype = 'H' ORDER BY g.accno""")
            else:
                cur.execute("""SELECT c.accno, c.description, c.category, c.contra,
                                      l.description AS translation
                               FROM chart c
                               LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %(lang)s)
                               WHERE c.charttype = 'H' ORDER BY c.accno""", params)
            headings: List[str] = []
            for r in cur.fetchall():
                # Perl haalt contra alleen bij GIFI-koppen op
                trb[r["accno"]] = {"description": r.get("translation") or r["description"],
                                   "charttype": "H", "category": r["category"],
                                   "contra": r["contra"] if gifi else None}
                headings.append(r["accno"])

            where = "ac.approved = '1'"
            if fdate:
                where += " AND ac.transdate >= %(fromdate)s"
            if tdate:
                where += " AND ac.transdate <= %(todate)s"

            key = "c.gifi_accno" if gifi else "c.accno"
            if gifi:
                q = f"""SELECT g.accno, g.description, c.category,
                               SUM(ac.amount::numeric) AS amount, c.contra, NULL AS translation
                        FROM acc_trans ac
                        JOIN chart c ON (c.id = ac.chart_id)
                        JOIN gifi g ON (c.gifi_accno = g.accno)
                        {dpt_join}
                        WHERE {where} {dpt_where} {project}
                        GROUP BY g.accno, g.description, c.category, c.contra
                        ORDER BY g.accno"""
            else:
                q = f"""SELECT c.accno, c.description, c.category,
                               SUM(ac.amount::numeric) AS amount, c.contra,
                               l.description AS translation
                        FROM acc_trans ac
                        JOIN chart c ON (c.id = ac.chart_id)
                        LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %(lang)s)
                        {dpt_join}
                        WHERE {where} {dpt_where} {project}
                        GROUP BY c.accno, c.description, c.category, c.contra, translation
                        ORDER BY c.accno"""
            cur.execute(q, params)
            for r in cur.fetchall():
                t = trb.setdefault(r["accno"], {})
                t.update(description=r["translation"] or r["description"], charttype="A",
                         category=r["category"], contra=r["contra"])
                t["amount"] = t.get("amount", ZERO) + to_decimal(r["amount"])

            drcr = f"""SELECT
                  (SELECT SUM(ac.amount::numeric) * -1 FROM acc_trans ac
                   JOIN chart c ON (c.id = ac.chart_id) {dpt_join}
                   WHERE {where} {dpt_where} {project} AND ac.amount < 0 AND {key} = %(acc)s) AS debit,
                  (SELECT SUM(ac.amount::numeric) FROM acc_trans ac
                   JOIN chart c ON (c.id = ac.chart_id) {dpt_join}
                   WHERE {where} {dpt_where} {project} AND ac.amount > 0 AND {key} = %(acc)s) AS credit"""

            rows: List[Dict[str, Any]] = []
            for accno in sorted(trb):
                t = trb[accno]
                ref: Dict[str, Any] = {"accno": accno}
                for k in ("description", "category", "contra", "charttype", "amount"):
                    ref[k] = t.get(k)
                ref["balance"] = balance.get(accno)
                if t.get("charttype") == "A":
                    if project_id:
                        amount = to_decimal(ref["amount"])
                        if amount < 0:
                            ref["debit"] = -amount
                        else:
                            ref["credit"] = amount
                        if round_amount(amount, precision) == 0:
                            continue
                    else:
                        cur.execute(drcr, {**params, "acc": accno})
                        r = cur.fetchone()
                        ref["debit"] = to_decimal(r["debit"])
                        ref["credit"] = to_decimal(r["credit"])
                    ref["debit"] = round_amount(ref.get("debit"), precision)
                    ref["credit"] = round_amount(ref.get("credit"), precision)
                    if not all_accounts and round_amount(ref["debit"] + ref["credit"], precision) == 0:
                        continue
                heading = _last_heading(headings, accno)
                if heading:
                    h = trb[heading]
                    h["debit"] = h.get("debit", ZERO) + to_decimal(ref.get("debit"))
                    h["credit"] = h.get("credit", ZERO) + to_decimal(ref.get("credit"))
                rows.append(ref)

        for ref in rows:
            if ref["charttype"] == "H":
                ref["debit"] = trb[ref["accno"]].get("debit")
                ref["credit"] = trb[ref["accno"]].get("credit")

        for ref in rows:
            ml = -1 if ref["category"] in ("A", "E") else 1
            if ref["contra"]:
                ml = -ml
            bal = to_decimal(ref["balance"])
            ref["begbalance"] = bal * ml
            ref["endbalance"] = (bal + to_decimal(ref["amount"])) * ml

        display, total_debit, total_credit = _trial_balance_display(
            rows, all_accounts=all_accounts, l_heading=l_heading, l_subtotal=l_subtotal)
        return TrialBalance(rows=rows, display=display, total_debit=total_debit,
                            total_credit=total_credit, fromdate=fdate, todate=tdate, info=info)

    # =================================================== winst- en verliesrekening

    def income_statement(
        self,
        *,
        fromdate: DateLike = None,
        todate: DateLike = None,
        fromyear: Optional[int] = None,
        frommonth: Optional[int] = None,
        interval: Optional[int] = None,
        includeperiod: Optional[str] = None,
        previousyear: bool = False,
        currency: Optional[str] = None,
        decimalplaces: Optional[int] = None,
        department_id: Optional[int] = None,
        project_id: Optional[int] = None,
        accounttype: str = "standard",
        method: str = "accrual",
        language_code: Optional[str] = None,
        reversedisplay: bool = False,
        l_heading: bool = True,
        l_subtotal: bool = True,
    ) -> FinancialStatement:
        """Winst- en verliesrekening (RP::income_statement + rp.pl).

        includeperiod: None, 'month' of 'quarter' voor extra periodekolommen.
        method: 'accrual' (factuurstelsel) of 'cash' (kasstelsel).
        Jaarafsluitingsboekingen worden niet meegeteld.
        """
        cfg = self.config
        fdate = parse_date(fromdate, cfg.dateformat)
        tdate = parse_date(todate, cfg.dateformat)
        if not (fdate or tdate) and fromyear and frommonth:
            fdate, tdate = from_to(fromyear, frommonth, interval)

        with _db.cursor(self.conn) as cur:
            if not fdate:
                cur.execute("SELECT transdate FROM acc_trans ORDER BY transdate LIMIT 1")
                r = cur.fetchone()
                fdate = r["transdate"] if r else None
            tdate = tdate or date.today()

            opts = self._options(cur, currency, tdate, decimalplaces, department_id, project_id,
                                 accounttype, method, language_code)
            st = _StatementBuilder()
            periods: Dict[int, StatementPeriod] = {}

            def period(idx: int, year: str, f, t):
                p = periods.setdefault(idx, StatementPeriod(index=idx, this=(None, None)))
                if year == "this":
                    p.this = (f, t)
                else:
                    p.previous = (f, t)

            dd = fdate.day if fdate else 1
            fdm = _month_starts(fdate) if fdate else {}

            for cat in ("I", "E"):
                f, t = fdate, tdate
                period(0, "this", f, t)
                st.add(self._get_accounts(cur, f, t, cat, opts, excludeyearend=True), "this", 0, cat, opts)

                if includeperiod == "month" and fdate:
                    t = add_date(last_day_of_month(fdate), dd - 1, "days")
                    if t < tdate:
                        period(1, "this", f, t)
                        st.add(self._get_accounts(cur, f, t, cat, opts, True), "this", 1, cat, opts)
                        n = min(int(interval or 12), 12)
                        for i in range(1, n):
                            f = add_date(fdm[i][0], dd - 1, "days")
                            t = add_date(fdm[i][1], dd - 1, "days")
                            done = t >= tdate
                            if done:
                                t = tdate
                            period(i + 1, "this", f, t)
                            st.add(self._get_accounts(cur, f, t, cat, opts, True), "this", i + 1, cat, opts)
                            if done:
                                break

                if includeperiod == "quarter" and fdate:
                    t = add_date(fdm[2][1], dd - 1, "days")
                    if t < tdate:
                        period(1, "this", f, t)
                        st.add(self._get_accounts(cur, f, t, cat, opts, True), "this", 1, cat, opts)
                        n = int(int(interval or 0) / 3) or 4
                        for i in range(1, min(n, 4)):
                            j, k = i * 3, i * 3 + 2
                            f = add_date(fdm[j][0], dd - 1, "days")
                            t = add_date(fdm[k][1], dd - 1, "days")
                            done = t >= tdate
                            if done:
                                t = tdate
                            period(i + 1, "this", f, t)
                            st.add(self._get_accounts(cur, f, t, cat, opts, True), "this", i + 1, cat, opts)
                            if done:
                                break

            if previousyear and fdate:
                pfrom = add_date(fdate, -1, "year")
                pto = add_date(tdate, -1, "year")
                for cat in ("I", "E"):
                    f, t = pfrom, pto
                    period(0, "previous", f, t)
                    st.add(self._get_accounts(cur, f, t, cat, opts, True), "previous", 0, cat, opts)

                    if includeperiod == "month":
                        t = add_date(add_date(last_day_of_month(fdate), -1, "year"), dd - 1, "days")
                        if t < pto:
                            period(1, "previous", f, t)
                            st.add(self._get_accounts(cur, f, t, cat, opts, True), "previous", 1, cat, opts)
                            n = min(int(interval or 12), 12)
                            for i in range(1, n):
                                f = add_date(add_date(fdm[i][0], -1, "year"), dd - 1, "days")
                                t = add_date(add_date(fdm[i][1], -1, "year"), dd - 1, "days")
                                done = t >= pto
                                if done:
                                    t = pto
                                period(i + 1, "previous", f, t)
                                st.add(self._get_accounts(cur, f, t, cat, opts, True), "previous", i + 1, cat, opts)
                                if done:
                                    break

                    if includeperiod == "quarter":
                        t = add_date(add_date(fdm[2][1], -1, "year"), dd - 1, "days")
                        if t < pto:
                            period(1, "previous", f, t)
                            st.add(self._get_accounts(cur, f, t, cat, opts, True), "previous", 1, cat, opts)
                            n = int(int(interval or 0) / 3) or 4
                            for i in range(1, min(n, 4)):
                                j, k = i * 3, i * 3 + 2
                                f = add_date(add_date(fdm[j][0], -1, "year"), dd - 1, "days")
                                t = add_date(add_date(fdm[k][1], -1, "year"), dd - 1, "days")
                                done = t >= pto
                                if done:
                                    t = pto
                                period(i + 1, "previous", f, t)
                                st.add(self._get_accounts(cur, f, t, cat, opts, True), "previous", i + 1, cat, opts)
                                if done:
                                    break

            info = _db.get_defaults(cur, *COMPANY_FIELDS)

        # rp.pl: kolommen = totaal (0) gevolgd door de periodes van laatste naar eerste
        order = [0] + sorted((p for p in periods if p != 0), reverse=True)
        return st.statement("income_statement", ("I", "E"), periods, order, previousyear,
                            reversedisplay, l_heading, l_subtotal, opts, info, fdate, tdate)

    # ======================================================================= balans

    def balance_sheet(
        self,
        *,
        todate: DateLike = None,
        toyear: Optional[int] = None,
        tomonth: Optional[int] = None,
        includeperiod: Optional[str] = None,
        previousyear: bool = False,
        currency: Optional[str] = None,
        decimalplaces: Optional[int] = None,
        department_id: Optional[int] = None,
        project_id: Optional[int] = None,
        accounttype: str = "standard",
        method: str = "accrual",
        language_code: Optional[str] = None,
        reversedisplay: bool = False,
        l_heading: bool = True,
        l_subtotal: bool = True,
    ) -> FinancialStatement:
        """Balans per datum (RP::balance_sheet + rp.pl), inclusief
        'resultaat lopend boekjaar' (Current Earnings) in het eigen vermogen.

        includeperiod 'month' => 12 maandkolommen, 'quarter' => 4 kwartaalkolommen.
        """
        cfg = self.config
        tdate = parse_date(todate, cfg.dateformat)
        if not tdate and toyear and tomonth:
            _, tdate = from_to(toyear, tomonth)
        tdate = tdate or date.today()

        with _db.cursor(self.conn) as cur:
            opts = self._options(cur, currency, tdate, decimalplaces, department_id, project_id,
                                 accounttype, method, language_code)
            st = _StatementBuilder()
            periods: Dict[int, StatementPeriod] = {
                0: StatementPeriod(0, (None, tdate), (None, add_date(tdate, -1, "year")))}
            k, p = 0, 1
            if includeperiod == "month":
                k = 11
            if includeperiod == "quarter":
                k, p = 3, 3

            for cat in ("A", "L", "Q"):
                st.add(self._get_accounts(cur, None, tdate, cat, opts), "this", 0, cat, opts)
                if previousyear:
                    st.add(self._get_accounts(cur, None, add_date(tdate, -1, "year"), cat, opts),
                           "previous", 0, cat, opts)
                for i in range(1, k + 1):
                    t = add_date(tdate, -i * p, "month")
                    periods.setdefault(i, StatementPeriod(i, (None, t)))
                    st.add(self._get_accounts(cur, None, t, cat, opts), "this", i, cat, opts)
                    if previousyear:
                        pt = add_date(t, -1, "year")
                        periods[i].previous = (None, pt)
                        st.add(self._get_accounts(cur, None, pt, cat, opts), "previous", i, cat, opts)

            info = _db.get_defaults(cur, *COMPANY_FIELDS)

        order = sorted(periods)
        return st.statement("balance_sheet", ("A", "L", "Q"), periods, order, previousyear,
                            reversedisplay, l_heading, l_subtotal, opts, info, None, tdate)

    # ============================================================ intern

    def _options(self, cur, currency, tdate, decimalplaces, department_id, project_id,
                 accounttype, method, language_code) -> Dict[str, Any]:
        defaultcurrency = _db.default_currency(cur)
        rate = Decimal(1)
        if currency and currency != defaultcurrency:
            cur.execute("SELECT exchangerate FROM exchangerate WHERE curr = %s AND transdate = %s",
                        (currency, tdate))
            r = cur.fetchone()
            rate = to_decimal(r["exchangerate"]) if r and r["exchangerate"] else Decimal(1)
        if decimalplaces is None:
            # Perl: W&V zonder decimalplaces => 0, balans => 0 ('*= 1'); hier de
            # valuta-precisie, zodat kleine saldi niet uit het rapport vallen.
            decimalplaces = _db.get_precision(cur)
        return {
            "currency": currency or defaultcurrency, "exchangerate": rate,
            "decimalplaces": int(decimalplaces), "department_id": department_id,
            "project_id": project_id, "gifi": accounttype == "gifi", "cash": method == "cash",
            "lang": self.config.countrycode if language_code is None else language_code,
        }

    def _get_accounts(self, cur, fromdate: Optional[date], todate: Optional[date], category: str,
                      o: Dict[str, Any], excludeyearend: bool = False) -> Dict[str, Dict[str, Any]]:
        """Saldi per rekening voor één categorie, met optelling naar de kop
        erboven (RP::get_accounts)."""
        prm: Dict[str, Any] = {"cat": category, "lang": o["lang"], "dept": o["department_id"],
                               "project": o["project_id"]}
        c: Dict[str, Dict[str, Any]] = {}

        if o["gifi"]:
            cur.execute("""SELECT g.accno, g.description, c.category, '' AS translation
                           FROM gifi g JOIN chart c ON (c.gifi_accno = g.accno)
                           WHERE c.charttype = 'H' AND c.category = %(cat)s
                           UNION
                           SELECT c.accno, c.description, c.category, l.description AS translation
                           FROM chart c
                           LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %(lang)s)
                           WHERE c.charttype = 'H' AND c.category = %(cat)s
                           ORDER BY 1""", prm)
        else:
            cur.execute("""SELECT c.accno, c.description, c.category, l.description AS translation
                           FROM chart c
                           LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %(lang)s)
                           WHERE c.charttype = 'H' AND c.category = %(cat)s
                           ORDER BY c.accno""", prm)
        headings: List[str] = []
        for r in cur.fetchall():
            c[r["accno"]] = {"accno": r["accno"], "description": r["translation"] or r["description"],
                             "charttype": "H"}
            headings.append(r["accno"])

        cash = o["cash"]
        if cash and not todate:
            todate = date.today()

        where, subwhere, glwhere = "1 = 1", "", ""
        if fromdate:
            prm["fromdate"] = fromdate
            if cash:
                subwhere += " AND ac.transdate >= %(fromdate)s"
                glwhere = " AND ac.transdate >= %(fromdate)s"
            else:
                where += " AND ac.transdate >= %(fromdate)s"
        if todate:
            prm["todate"] = todate
            where += " AND ac.transdate <= %(todate)s"
            subwhere += " AND ac.transdate <= %(todate)s"

        ywhere = ""
        if excludeyearend:
            if fromdate and todate:
                ywhere = """ AND ac.trans_id NOT IN (SELECT trans_id FROM yearend
                             WHERE transdate >= %(fromdate)s AND transdate <= %(todate)s)"""
            elif fromdate:
                ywhere = " AND ac.trans_id NOT IN (SELECT trans_id FROM yearend WHERE transdate >= %(fromdate)s)"
            elif todate:
                ywhere = " AND ac.trans_id NOT IN (SELECT trans_id FROM yearend WHERE transdate <= %(todate)s)"
            else:
                ywhere = " AND ac.trans_id NOT IN (SELECT trans_id FROM yearend)"

        dpt_join = dpt_where = project = ""
        if o["department_id"]:
            if cash:
                dpt_join = "JOIN department t ON (a.department_id = t.id)"
                dpt_where = "AND t.id = %(dept)s"
            else:
                dpt_join = "JOIN dpt_trans t ON (t.trans_id = ac.trans_id)"
                dpt_where = "AND t.department_id = %(dept)s"
        if o["project_id"]:
            project = "AND ac.project_id = %(project)s"

        def paid(link: str) -> str:
            return f"""AND ac.trans_id IN (SELECT ac.trans_id FROM acc_trans ac
                       JOIN chart c ON (ac.chart_id = c.id)
                       WHERE c.link LIKE '%%{link}%%' AND ac.approved = '1' {subwhere})"""

        if o["gifi"]:
            if cash:
                parts = []
                for table, link, appr in (("ar", "AR_paid", "a"), ("ap", "AP_paid", "a")):
                    parts.append(f"""
                        SELECT g.accno, SUM(ac.amount::numeric) AS amount, g.description, c.category
                        FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id)
                        JOIN {table} a ON (a.id = ac.trans_id)
                        JOIN gifi g ON (g.accno = c.gifi_accno) {dpt_join}
                        WHERE {where} AND {appr}.approved = '1' AND c.category = %(cat)s
                        {ywhere} {dpt_where} {paid(link)} {project}
                        GROUP BY g.accno, g.description, c.category""")
                    parts.append(f"""
                        SELECT '' AS accno, SUM(ac.amount::numeric) AS amount, '' AS description, c.category
                        FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id)
                        JOIN {table} a ON (a.id = ac.trans_id) {dpt_join}
                        WHERE {where} AND {'ac' if table == 'ar' else 'a'}.approved = '1'
                        AND c.category = %(cat)s {ywhere} {dpt_where} AND c.gifi_accno = ''
                        {paid(link)} {project}
                        GROUP BY c.category""")
                parts.append(f"""
                    SELECT g.accno, SUM(ac.amount::numeric) AS amount, g.description, c.category
                    FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id)
                    JOIN gifi g ON (g.accno = c.gifi_accno)
                    JOIN gl a ON (a.id = ac.trans_id) {dpt_join}
                    WHERE {where} AND a.approved = '1' AND c.category = %(cat)s
                    {ywhere} {glwhere} {dpt_where}
                    AND NOT (c.link = 'AR' OR c.link = 'AP') {project}
                    GROUP BY g.accno, g.description, c.category""")
                parts.append(f"""
                    SELECT '' AS accno, SUM(ac.amount::numeric) AS amount, '' AS description, c.category
                    FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id)
                    JOIN gl a ON (a.id = ac.trans_id) {dpt_join}
                    WHERE {where} AND a.approved = '1' AND c.category = %(cat)s
                    {ywhere} {glwhere} {dpt_where} AND c.gifi_accno = ''
                    AND NOT (c.link = 'AR' OR c.link = 'AP') {project}
                    GROUP BY c.category""")
                query = " UNION ALL ".join(parts)
            else:
                query = f"""
                    SELECT g.accno, SUM(ac.amount::numeric) AS amount, g.description, c.category
                    FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id)
                    JOIN gifi g ON (c.gifi_accno = g.accno) {dpt_join}
                    WHERE {where} AND ac.approved = '1' AND c.category = %(cat)s
                    {ywhere} {dpt_where} {project}
                    GROUP BY g.accno, g.description, c.category
                  UNION ALL
                    SELECT '' AS accno, SUM(ac.amount::numeric) AS amount, '' AS description, c.category
                    FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id) {dpt_join}
                    WHERE {where} AND ac.approved = '1' AND c.category = %(cat)s
                    {ywhere} {dpt_where} AND c.gifi_accno = '' {project}
                    GROUP BY c.category"""
        else:
            translation = "LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %(lang)s)"
            if cash:
                query = f"""
                    SELECT c.accno, SUM(ac.amount::numeric) AS amount, c.description, c.category,
                           l.description AS translation
                    FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id)
                    JOIN ar a ON (a.id = ac.trans_id) {translation} {dpt_join}
                    WHERE {where} AND ac.approved = '1' AND c.category = %(cat)s
                    {ywhere} {dpt_where} {paid('AR_paid')} {project}
                    GROUP BY c.accno, c.description, c.category, translation
                  UNION ALL
                    SELECT c.accno, SUM(ac.amount::numeric) AS amount, c.description, c.category,
                           l.description AS translation
                    FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id)
                    JOIN ap a ON (a.id = ac.trans_id) {translation} {dpt_join}
                    WHERE {where} AND a.approved = '1' AND c.category = %(cat)s
                    {ywhere} {dpt_where} {paid('AP_paid')} {project}
                    GROUP BY c.accno, c.description, c.category, translation
                  UNION ALL
                    SELECT c.accno, SUM(ac.amount::numeric) AS amount, c.description, c.category,
                           l.description AS translation
                    FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id)
                    JOIN gl a ON (a.id = ac.trans_id) {translation} {dpt_join}
                    WHERE {where} AND a.approved = '1' AND c.category = %(cat)s
                    {ywhere} {glwhere} {dpt_where}
                    AND NOT (c.link = 'AR' OR c.link = 'AP') {project}
                    GROUP BY c.accno, c.description, c.category, translation"""
            else:
                query = f"""
                    SELECT c.accno, SUM(ac.amount::numeric) AS amount, c.description, c.category,
                           l.description AS translation
                    FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id)
                    {translation} {dpt_join}
                    WHERE {where} AND ac.approved = '1' AND c.category = %(cat)s
                    {ywhere} {dpt_where} {project}
                    GROUP BY c.accno, c.description, c.category, translation"""

        cur.execute(query, prm)
        rate = o["exchangerate"]
        for r in cur.fetchall():
            amount = to_decimal(r["amount"]) / rate
            heading = _last_heading(headings, r["accno"])
            if heading and heading != r["accno"]:
                c[heading]["amount"] = c[heading].get("amount", ZERO) + amount
            entry = c.setdefault(r["accno"], {})
            entry.update(accno=r["accno"], description=r.get("translation") or r["description"],
                         charttype="A")
            entry["amount"] = entry.get("amount", ZERO) + amount
        return c


# ---------------------------------------------------------------- hulpfuncties

def _last_heading(headings: List[str], accno: str) -> Optional[str]:
    """Laatste kop met accno <= rekening (tekstvergelijking, zoals Perl 'le')."""
    found = None
    for h in headings:
        if h <= accno:
            found = h
    return found


def _month_starts(d: date) -> Dict[int, Tuple[date, date]]:
    """Form::fdld: eerste/laatste dag van de maanden 1..11 na de maand van d."""
    out = {}
    for i in range(1, 12):
        m, y = d.month + i, d.year
        if m > 12:
            m -= 12
            y += 1
        first = date(y, m, 1)
        out[i] = (first, last_day_of_month(first))
    return out


class _StatementBuilder:
    """Verzamelt get_accounts-resultaten per (categorie, accno, jaar, periode)
    zoals RP::add_accounts en rekent daarna de schermlogica van rp.pl door."""

    def __init__(self):
        self.data: Dict[str, Dict[str, Dict[Column, Dict[str, Any]]]] = {}
        self.accounts: Dict[str, Dict[str, Any]] = {}

    def add(self, c: Dict[str, Dict[str, Any]], year: str, period: int, category: str,
            o: Dict[str, Any]) -> None:
        for accno, e in c.items():
            if not round_amount(e.get("amount"), o["decimalplaces"]):
                continue
            self.accounts[accno] = {"description": e.get("description"), "charttype": e.get("charttype")}
            self.data.setdefault(category, {}).setdefault(accno, {})[(year, period)] = dict(e)

    def statement(self, kind, categories, periods, order, previousyear, reversedisplay,
                  l_heading, l_subtotal, o, info, fdate, tdate) -> FinancialStatement:
        years = ["this", "previous"] if previousyear else ["this"]
        if reversedisplay:
            order = list(reversed(order))
            if previousyear:
                years = ["previous", "this"]
        columns: List[Column] = [(y, p) for p in order for y in years]

        ml_of = {"I": 1, "E": -1, "A": -1, "L": 1, "Q": 1}
        totals: Dict[str, Dict[Column, Decimal]] = {}
        raw_total: Dict[Column, Decimal] = {col: ZERO for col in columns}
        accounts_out: Dict[str, Dict[str, Dict[str, Any]]] = {}
        display: List[DisplayRow] = []
        result: Dict[Column, Decimal] = {}

        for cat in categories:
            ml = ml_of[cat]
            totals[cat] = {col: ZERO for col in columns}
            accounts_out[cat] = {}
            display.append(DisplayRow("section", CATEGORY_LABELS[cat], category=cat))
            sub: Optional[Dict[str, Any]] = None      # lopend subtotaal onder de laatste kop

            def flush():
                if sub is not None and l_subtotal:
                    display.append(DisplayRow("subtotal", sub["description"], sub["accno"],
                                              [sub["values"][col] for col in columns], cat))

            for accno in sorted(self.data.get(cat, {})):
                entries = self.data[cat][accno]
                is_heading = any(entries.get((y, 0), {}).get("charttype") == "H"
                                 for y in ("this", "previous"))
                if is_heading:
                    flush()
                    sub = {"accno": accno, "description": self.accounts[accno]["description"],
                           "values": {col: to_decimal(entries.get(col, {}).get("amount")) * ml
                                      if entries.get(col, {}).get("charttype") == "H" else ZERO
                                      for col in columns}}
                elif any(e.get("charttype") == "H" for e in entries.values()):
                    # kop in een andere periode dan periode 0 (bv. alleen vorig jaar een bedrag): rp.pl
                    # section_display zet dan per periode het subtotaal en toont de regel als rekening
                    if sub is None:
                        sub = {"values": {col: ZERO for col in columns}}
                    sub["accno"], sub["description"] = accno, self.accounts[accno]["description"]
                    for col in columns:
                        if entries.get(col, {}).get("charttype") == "H":
                            sub["values"][col] = to_decimal(entries[col].get("amount")) * ml
                values = []
                amounts = {}
                for col in columns:
                    e = entries.get(col)
                    amt = ZERO
                    if e and e.get("charttype") != "H":
                        amt = to_decimal(e.get("amount"))
                    totals[cat][col] += amt * ml
                    raw_total[col] += amt
                    values.append(amt * ml)
                    amounts[col] = amt * ml
                accounts_out[cat][accno] = {"description": self.accounts[accno]["description"],
                                            "charttype": "H" if is_heading else "A",
                                            "amounts": amounts if not is_heading else
                                            {col: to_decimal(entries.get(col, {}).get("amount")) * ml
                                             for col in columns}}
                if is_heading and not l_heading:
                    continue
                display.append(DisplayRow("heading" if is_heading else "account",
                                          self.accounts[accno]["description"], accno, values, cat))

            if cat == "Q":
                earnings = {}
                for col in columns:
                    earnings[col] = totals["A"][col] - totals["L"][col] - totals["Q"][col]
                display.append(DisplayRow("current_earnings", "Current Earnings", None,
                                          [earnings[col] for col in columns], cat))
                for col in columns:
                    if sub is not None and l_subtotal and l_heading:
                        sub["values"][col] += earnings[col]
                    totals["Q"][col] += earnings[col]
                result = earnings
            flush()
            display.append(DisplayRow("total", f"Total {CATEGORY_LABELS[cat]}", None,
                                      [totals[cat][col] for col in columns], cat))

        if kind == "income_statement":
            result = dict(raw_total)
            display.append(DisplayRow("result", "Income / (Loss)", None,
                                      [raw_total[col] for col in columns]))

        info = dict(info)
        info.update(fromdate=fdate, todate=tdate, decimalplaces=o["decimalplaces"])
        return FinancialStatement(kind=kind, columns=columns, periods=periods,
                                  accounts=accounts_out, totals=totals, result=result,
                                  display=display, currency=o["currency"],
                                  exchangerate=o["exchangerate"], info=info)


def _trial_balance_display(rows, *, all_accounts, l_heading, l_subtotal):
    """Weergave van de proefbalans zoals rp.pl list_accounts."""
    display: List[DisplayRow] = []
    total_debit = total_credit = ZERO
    sub: Optional[Dict[str, Any]] = None
    show_sub = False

    def flush():
        if show_sub and sub is not None and l_subtotal:
            display.append(DisplayRow("subtotal", sub["description"], sub["accno"],
                                      [sub["beg"], sub["debit"], sub["credit"], sub["end"]]))

    for ref in sorted(rows, key=lambda r: r["accno"]):
        if ref["charttype"] == "H":
            flush()
            sub = {"accno": ref["accno"], "description": ref["description"],
                   "debit": to_decimal(ref.get("debit")), "credit": to_decimal(ref.get("credit")),
                   "beg": ZERO, "end": ZERO}
            active = (sub["debit"] + sub["credit"]) != 0
            if l_heading:
                show_sub = all_accounts or active
                if show_sub:
                    display.append(DisplayRow("heading", ref["description"], ref["accno"],
                                              [None, None, None, None]))
            else:
                show_sub = all_accounts or (l_subtotal and active)
            continue
        display.append(DisplayRow("account", ref["description"], ref["accno"],
                                  [ref["begbalance"], to_decimal(ref.get("debit")),
                                   to_decimal(ref.get("credit")), ref["endbalance"]], ref["category"]))
        total_debit += to_decimal(ref.get("debit"))
        total_credit += to_decimal(ref.get("credit"))
        if sub is not None:
            cml = -1 if ref["contra"] else 1
            sub["beg"] += ref["begbalance"] * cml
            sub["end"] += ref["endbalance"] * cml
    flush()
    display.append(DisplayRow("total", "Total", None, [None, total_debit, total_credit, None]))
    return display, total_debit, total_credit


from .reports_arap import ArapReports  # noqa: E402  (na DisplayRow i.v.m. circulaire import)


class Reports(_GLReports, ArapReports):
    """Alle rapporten: grootboek (proefbalans, W&V, balans), debiteuren/
    crediteuren (ouderdomsanalyse, aanmaningen), btw, betalingen en
    jaarafsluiting."""
