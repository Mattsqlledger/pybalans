"""Rekeningschema: rekeningen met saldo en mutaties per rekening (port van SL/CA.pm)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

import psycopg

from . import _db
from .amounts import to_decimal
from .config import UserConfig
from .dates import DateLike, from_to, parse_date
from .gl import sort_order
from .models import AccountTransactions

_TX_COLUMNS = ("id", "reference", "description", "transdate", "invoice", "amount",
               "module", "cleared", "source", "till", "chart_id", "vc_id")


class ChartOfAccounts:

    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        self.conn = conn
        self.config = config

    def all_accounts(self) -> List[Dict[str, Any]]:
        """Alle rekeningen met hun saldo over goedgekeurde boekingen (CA::all_accounts).

        Saldo < 0 staat in 'debit' (positief gemaakt), anders in 'credit';
        rekeningen zonder boekingen hebben amount/debit/credit None.
        """
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT c.accno, SUM(ac.amount::numeric) AS amount
                           FROM chart c JOIN acc_trans ac ON (ac.chart_id = c.id)
                           WHERE ac.approved = '1'
                           GROUP BY c.accno""")
            amounts = {r["accno"]: to_decimal(r["amount"]) for r in cur.fetchall()}
            cur.execute("SELECT accno, description FROM gifi")
            gifi = {r["accno"]: r["description"] for r in cur.fetchall()}
            cur.execute("""SELECT c.id, c.accno, c.description, c.charttype, c.gifi_accno,
                                  c.category, c.link, c.contra, c.closed,
                                  l.description AS translation
                           FROM chart c
                           LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %s)
                           ORDER BY c.accno""", (self.config.countrycode,))
            accounts = cur.fetchall()

        for ref in accounts:
            amount = amounts.get(ref["accno"])
            ref["amount"] = amount
            ref["gifi_description"] = gifi.get(ref["gifi_accno"])
            ref["debit"] = -amount if amount is not None and amount < 0 else None
            ref["credit"] = amount if amount is None or amount >= 0 else None
            if ref["translation"]:
                ref["description"] = ref["translation"]
        return accounts

    def account_transactions(
        self,
        accno: Optional[str] = None,
        *,
        gifi_accno: Optional[str] = None,
        fromdate: DateLike = None,
        todate: DateLike = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
        interval: Optional[int] = None,
        department_id: Optional[int] = None,
        project_id: Optional[int] = None,
        sort: Optional[str] = None,
        direction: str = "ASC",
        l_subtotal: bool = False,
    ) -> AccountTransactions:
        """Mutaties op één rekening (of GIFI-rekening) met beginsaldo (CA::all_transactions),
        plus de weergave van ca.pl list_transactions (lopend saldo, subtotaal per
        sorteerwaarde als ``l_subtotal``, totaal)."""
        cfg = self.config
        use_gifi = gifi_accno is not None and accno is None
        fdate = parse_date(fromdate, cfg.dateformat)
        tdate = parse_date(todate, cfg.dateformat)
        if not (fdate or tdate) and year and month:
            fdate, tdate = from_to(year, month, interval)

        info: Dict[str, Any] = {}
        balance: Optional[Decimal] = None
        rows: List[Dict[str, Any]] = []

        with _db.cursor(self.conn) as cur:
            info.update(_db.get_defaults(cur, "precision", "company"))

            if use_gifi:
                cur.execute("SELECT id FROM chart WHERE gifi_accno = %s", (gifi_accno,))
            else:
                cur.execute("SELECT id FROM chart WHERE accno = %s", (accno,))
            chart_ids = [r["id"] for r in cur.fetchall()]

            if accno or gifi_accno:
                if use_gifi:
                    cur.execute("""SELECT description, category, link, contra FROM chart
                                   WHERE gifi_accno = %s AND charttype = 'A'""", (gifi_accno,))
                else:
                    cur.execute("""SELECT c.description, c.category, c.link, c.contra,
                                          l.description AS translation
                                   FROM chart c
                                   LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %s)
                                   WHERE c.accno = %s""", (cfg.countrycode, accno))
                r = cur.fetchone() or {}
                info.update(description=r.get("translation") or r.get("description"),
                            category=r.get("category"), link=r.get("link"),
                            contra=r.get("contra"))

                if fdate:
                    balance = self._opening_balance(cur, use_gifi, gifi_accno if use_gifi else accno,
                                                    fdate, department_id, project_id)

            if not chart_ids:
                return AccountTransactions(rows=[], balance=balance, info=info,
                                           fromdate=fdate, todate=tdate,
                                           display=_display([], balance, info, sort, l_subtotal))

            extra, extra_params = "", []
            if fdate:
                extra += " AND ac.transdate >= %s"
                extra_params.append(fdate)
            if tdate:
                extra += " AND ac.transdate <= %s"
                extra_params.append(tdate)
            dpt_join = ""
            if department_id:
                dpt_join = "JOIN department t ON (t.id = a.department_id)"
                extra += " AND t.id = %s"
                extra_params.append(int(department_id))
            if project_id:
                extra += " AND ac.project_id = %s"
                extra_params.append(int(project_id))

            parts, params = [], []
            for cid in chart_ids:
                parts.append(f"""
                    SELECT a.id, a.reference, a.description, ac.transdate,
                           FALSE AS invoice, ac.amount, 'gl' AS module, ac.cleared,
                           ac.source, '' AS till, ac.chart_id, '0' AS vc_id
                    FROM gl a JOIN acc_trans ac ON (ac.trans_id = a.id) {dpt_join}
                    WHERE ac.chart_id = %s AND ac.approved = '1' {extra}
                  UNION ALL
                    SELECT a.id, a.invnumber, c.name, ac.transdate,
                           a.invoice, ac.amount, 'ar' AS module, ac.cleared,
                           ac.source, a.till, ac.chart_id, c.id AS vc_id
                    FROM ar a JOIN acc_trans ac ON (ac.trans_id = a.id)
                    JOIN customer c ON (a.customer_id = c.id) {dpt_join}
                    WHERE ac.chart_id = %s AND ac.approved = '1' {extra}
                  UNION ALL
                    SELECT a.id, a.invnumber, v.name, ac.transdate,
                           a.invoice, ac.amount, 'ap' AS module, ac.cleared,
                           ac.source, a.till, ac.chart_id, v.id AS vc_id
                    FROM ap a JOIN acc_trans ac ON (ac.trans_id = a.id)
                    JOIN vendor v ON (a.vendor_id = v.id) {dpt_join}
                    WHERE ac.chart_id = %s AND ac.approved = '1' {extra}""")
                params += [cid, *extra_params] * 3

            ordinal = {name: i + 1 for i, name in enumerate(_TX_COLUMNS)}
            order = sort_order(["transdate", "reference", "description"], sort, direction, ordinal)
            cur.execute(" UNION ALL ".join(parts) + f" ORDER BY {order}", params)
            records = cur.fetchall()

            arap_query = """SELECT c.id, c.accno FROM chart c
                            JOIN acc_trans ac ON (ac.chart_id = c.id)
                            WHERE ac.amount {op} 0
                              AND (c.link = 'AR' OR c.link = 'AP')
                              AND ac.approved = '1'
                              AND ac.trans_id = %s"""
            for ref in records:
                ref["amount"] = to_decimal(ref["amount"])
                module = ref["module"]
                if module == "gl":
                    ref["vc_id"] = 0
                    ref["db"] = ""
                elif module == "ap":
                    ref["module"] = "ps" if ref["till"] else ("ir" if ref["invoice"] else "ap")
                    ref["db"] = "vendor"
                elif module == "ar":
                    ref["module"] = "ps" if ref["till"] else ("is" if ref["invoice"] else "ar")
                    ref["db"] = "customer"

                if not ref["amount"]:
                    continue
                op = ">=" if ref["amount"] < 0 else "<"
                if ref["amount"] < 0:
                    ref["debit"], ref["credit"] = -ref["amount"], Decimal(0)
                else:
                    ref["debit"], ref["credit"] = Decimal(0), ref["amount"]
                cur.execute(arap_query.format(op=op), (ref["id"],))
                others = sorted({r["accno"] for r in cur.fetchall() if r["id"] != ref["chart_id"]})
                ref["accno"] = [f"{a} " for a in others] or None
                rows.append(ref)

        return AccountTransactions(rows=rows, balance=balance, info=info,
                                   fromdate=fdate, todate=tdate,
                                   display=_display(rows, balance, info, sort, l_subtotal))

    def journal_entry(self, trans_id: int) -> Optional[Dict[str, Any]]:
        """Eén boeking (memoriaal, verkoop of inkoop) met al haar journaalregels,
        om te bekijken. None als het nummer niet bestaat.

        Geeft {header, lines, total_debit, total_credit}; header.module is
        'gl', 'ar' of 'ap'. Regels in boekvolgorde (datum, regelnummer)."""
        with _db.cursor(self.conn) as cur:
            cur.execute("""
                SELECT 'gl' AS module, g.id, g.reference, g.description, g.transdate,
                       NULL::date AS duedate, g.notes, g.curr, g.exchangerate, NULL AS name,
                       NULL::int AS vc_id, NULL::float AS amount, NULL::float AS netamount,
                       NULL::float AS paid, FALSE AS invoice, NULL AS till, g.approved,
                       d.description AS department, e.name AS employee
                FROM gl g LEFT JOIN department d ON d.id = g.department_id
                LEFT JOIN employee e ON e.id = g.employee_id WHERE g.id = %(id)s
              UNION ALL
                SELECT 'ar', a.id, a.invnumber, a.description, a.transdate, a.duedate, a.notes,
                       a.curr, a.exchangerate, c.name, c.id, a.amount, a.netamount, a.paid,
                       a.invoice, a.till, a.approved, d.description, e.name
                FROM ar a JOIN customer c ON c.id = a.customer_id
                LEFT JOIN department d ON d.id = a.department_id
                LEFT JOIN employee e ON e.id = a.employee_id WHERE a.id = %(id)s
              UNION ALL
                SELECT 'ap', a.id, a.invnumber, a.description, a.transdate, a.duedate, a.notes,
                       a.curr, a.exchangerate, v.name, v.id, a.amount, a.netamount, a.paid,
                       a.invoice, a.till, a.approved, d.description, e.name
                FROM ap a JOIN vendor v ON v.id = a.vendor_id
                LEFT JOIN department d ON d.id = a.department_id
                LEFT JOIN employee e ON e.id = a.employee_id WHERE a.id = %(id)s""",
                        {"id": int(trans_id)})
            header = cur.fetchone()
            if not header:
                return None
            for k in ("amount", "netamount", "paid", "exchangerate"):
                header[k] = None if header[k] is None else to_decimal(header[k])
            cur.execute("""SELECT ac.transdate, c.accno, coalesce(l.description, c.description) AS description,
                                  ac.amount, ac.source, ac.memo, ac.cleared, ac.fx_transaction,
                                  ac.approved, p.projectnumber
                           FROM acc_trans ac JOIN chart c ON c.id = ac.chart_id
                           LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %s)
                           LEFT JOIN project p ON p.id = ac.project_id
                           WHERE ac.trans_id = %s
                           ORDER BY ac.transdate, ac.id, c.accno""",
                        (self.config.countrycode, int(trans_id)))
            lines = cur.fetchall()
        total_debit = total_credit = Decimal(0)
        for ln in lines:
            amount = to_decimal(ln.pop("amount"))
            ln["debit"] = -amount if amount < 0 else Decimal(0)
            ln["credit"] = amount if amount > 0 else Decimal(0)
            total_debit += ln["debit"]
            total_credit += ln["credit"]
        return {"header": header, "lines": lines,
                "total_debit": total_debit, "total_credit": total_credit}

    @staticmethod
    def _opening_balance(cur, use_gifi: bool, account: str, before, department_id,
                         project_id) -> Decimal:
        """Saldo vóór ``before``.

        Afwijking van Perl: met een afdeling telt Perl alleen het gl-deel mee
        (de lus over ar/ap/gl overschrijft steeds de query); hier worden gl, ar
        en ap alle drie opgeteld.
        """
        column = "c.gifi_accno" if use_gifi else "c.accno"
        project_sql = " AND ac.project_id = %s" if project_id else ""
        project_params = [int(project_id)] if project_id else []
        if department_id:
            total = Decimal(0)
            for table in ("ar", "ap", "gl"):
                cur.execute(
                    f"""SELECT SUM(ac.amount::numeric) AS s FROM acc_trans ac
                        JOIN {table} a ON (a.id = ac.trans_id)
                        JOIN chart c ON (ac.chart_id = c.id)
                        WHERE {column} = %s AND ac.approved = '1' AND ac.transdate < %s
                          AND a.department_id = %s{project_sql}""",
                    [account, before, int(department_id), *project_params])
                total += to_decimal(cur.fetchone()["s"])
            return total
        cur.execute(
            f"""SELECT SUM(ac.amount::numeric) AS s FROM acc_trans ac
                JOIN chart c ON (ac.chart_id = c.id)
                WHERE {column} = %s AND ac.approved = '1' AND ac.transdate < %s{project_sql}""",
            [account, before, *project_params])
        return to_decimal(cur.fetchone()["s"])


def _display(rows, balance, info, sort, l_subtotal):
    """ca.pl list_transactions: saldo in weergaveteken (activa/kosten debet
    positief, omgekeerd bij een contrarekening), lopend vanaf het beginsaldo.
    Zet ook ``balance`` (lopend saldo) in elke rij."""
    from .reports import DisplayRow  # reports -> reports_arap -> ...: pas hier importeren

    ml = -1 if info.get("category") in ("A", "E") else 1
    if info.get("contra"):
        ml = -ml
    zero = Decimal(0)
    running = balance or zero
    display = []
    if balance:
        display.append(DisplayRow("balance", "", values=[None, None, running * ml]))
    sub_debit = sub_credit = total_debit = total_credit = zero
    key = sort if sort in ("transdate", "reference", "description") else None
    same = rows[0].get(key) if rows and key else None
    for ref in rows:
        if l_subtotal and (ref.get(key) if key else None) != same:
            display.append(DisplayRow("subtotal", "", values=[sub_debit, sub_credit, None]))
            sub_debit = sub_credit = zero
            same = ref.get(key)
        running += ref["amount"]
        ref["balance"] = running * ml
        sub_debit += ref["debit"]
        sub_credit += ref["credit"]
        total_debit += ref["debit"]
        total_credit += ref["credit"]
        display.append(DisplayRow("row", ref.get("reference") or "", None,
                                  [ref["debit"], ref["credit"], ref["balance"]]))
    if l_subtotal:
        display.append(DisplayRow("subtotal", "", values=[sub_debit, sub_credit, None]))
    display.append(DisplayRow("total", "", values=[total_debit, total_credit, running * ml]))
    return display
