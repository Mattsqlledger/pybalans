"""Overzicht verkoop-/inkoopboekingen (port van AA::transactions + de rij- en
totaalberekening uit bin/mozilla/aa.pl transactions/subtotal).

Levert data: één rij per boeking (samenvatting) of per journaalregel (detail),
met nettobedrag, btw, totaal, betaald en openstaand, optioneel per valuta, plus
een ``display``-lijst met rijen, subtotalen en het totaal zoals het scherm.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

import psycopg

from . import _db
from .amounts import to_decimal
from .config import UserConfig
from .dates import from_to
from .gl import like
from .reports import DisplayRow

ZERO = Decimal(0)

SORTABLE = ("transdate", "id", "invnumber", "ordnumber", "ponumber", "description", "name", "customernumber",
            "vendornumber", "taxnumber", "address", "city", "zipcode", "country", "netamount", "tax", "amount",
            "paid", "paymentaccount", "paymentmethod", "curr", "datepaid", "duedate", "notes", "till",
            "employee", "shippingpoint", "shipvia", "waybill", "dcn", "paymentdiff", "department", "accno",
            "source", "projectnumber")


@dataclass
class ArapSearch:
    """Zoekvelden van het scherm 'Verkoop/Inkoop — Overzicht'.

    outstanding: alleen openstaand (betaald berekend t/m transdateto), altijd samenvatting.
    summary: één rij per boeking; anders één rij per journaalregel (met debet/credit).
    open/closed: niets aan = beide.
    department: begin van de afdelingsnaam (zoals Perl: LIKE 'x%').
    """

    vc_id: Optional[int] = None
    name: Optional[str] = None
    vcnumber: Optional[str] = None
    employee_id: Optional[int] = None
    department: Optional[str] = None
    invnumber: Optional[str] = None
    ordnumber: Optional[str] = None
    ponumber: Optional[str] = None
    shipvia: Optional[str] = None
    shippingpoint: Optional[str] = None
    waybill: Optional[str] = None
    notes: Optional[str] = None
    description: Optional[str] = None
    memo: Optional[str] = None
    source: Optional[str] = None
    transdatefrom: Optional[date] = None
    transdateto: Optional[date] = None
    year: Optional[int] = None
    month: Optional[int] = None
    interval: Optional[int] = None
    open: bool = False
    closed: bool = False
    paidlate: bool = False
    paidearly: bool = False
    onhold: bool = False
    till: bool = False
    arap_accno: Optional[str] = None
    outstanding: bool = False
    summary: bool = True
    l_memo: bool = False
    sort: Optional[str] = None
    direction: str = "ASC"
    l_subtotal: bool = False
    l_curr: bool = False


@dataclass
class ArapList:
    rows: List[Dict[str, Any]]
    currencies: List[str]                 # vreemde valuta in de selectie
    totals: Dict[str, Decimal]
    totals_curr: Dict[str, Dict[str, Decimal]]
    display: List[DisplayRow]
    columns: List[str]                    # volgorde van de bedragkolommen in ``display``
    defaultcurrency: Optional[str] = None
    precision: int = 2
    search: ArapSearch = field(default_factory=ArapSearch)


def list_transactions(conn: psycopg.Connection, config: UserConfig, vc: str,
                      search: Optional[ArapSearch] = None) -> ArapList:
    """Zoek verkoop- (vc='customer') of inkoopboekingen (vc='vendor').

    Afwijkingen van Perl:
    - zoeken op memo: Perl voegde 'OR lower(i.description) LIKE ..' zonder haakjes
      toe, waardoor alle andere filters (ook 'goedgekeurd') werden omzeild;
    - detailweergave: Perl telde het factuurbedrag, betaald en openstaand per
      journaalregel op (een factuur met vier regels telde vier keer); hier één
      keer per boeking;
    - subtotaal openstaand per valuta: typefout in Perl ({mount}) gaf -betaald;
    - gelijke sorteerwaarden: daarna op boekingsnummer (deterministisch);
    - zoeken op memo in de samenvatting: Perl gaf dan altijd een lege lijst
      ('a.id = 0' zonder detailregels); hier werkt het in beide weergaven.
    """
    if vc not in ("customer", "vendor"):
        raise ValueError("vc moet 'customer' of 'vendor' zijn")
    s = search or ArapSearch()
    table, ARAP, ml = ("ar", "AR", 1) if vc == "customer" else ("ap", "AP", -1)
    summary = s.summary or s.outstanding
    l_memo = s.l_memo and not s.outstanding
    datefrom, dateto = s.transdatefrom, s.transdateto
    if not (datefrom or dateto) and s.year and s.month:
        datefrom, dateto = from_to(s.year, s.month, s.interval)

    params: List[Any] = []
    if s.outstanding:
        paid = (f"""(SELECT SUM(ac.amount) * -1 * {ml} FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id)
                     WHERE ac.trans_id = a.id AND ac.approved = '1'
                       AND (c.link LIKE '%%{ARAP}_paid%%' OR c.link LIKE '%%{ARAP}_discount%%' OR c.link = '')"""
                + (" AND ac.transdate <= %s" if dateto else "") + ")")
        if dateto:
            params.append(dateto)
    else:
        paid = "a.paid"
    taxfld = ("""(SELECT sum(ac.amount)::numeric FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id)
                  WHERE ac.trans_id = a.id AND ac.approved = '1' AND c.link LIKE '%%_tax%%'"""
              + (" AND ac.transdate <= %s" if dateto else "") + ")")
    tax_params = [dateto] if dateto else []

    detail = not summary or l_memo
    lines_flds = lines_join = ""
    if detail:
        lines_flds = """, ch.accno, ac.source, pr.projectnumber, ac.memo, ac.amount AS linetotal,
                         i.description AS linedescription, ac.fx_transaction, ac.id AS line_id"""
        lines_join = f"""JOIN acc_trans ac ON (a.id = ac.trans_id) JOIN chart ch ON (ch.id = ac.chart_id)
                         LEFT JOIN project pr ON (pr.id = ac.project_id)
                         LEFT JOIN invoice i ON (i.id = ac.id{' AND i.trans_id = a.id' if summary else ''})"""

    where = ["a.approved = '1'"]
    wparams: List[Any] = []
    if s.vc_id:
        where.append(f"a.{vc}_id = %s")
        wparams.append(s.vc_id)
    else:
        if s.name:
            where.append("lower(vc.name) LIKE %s")
            wparams.append(like(s.name.lower()))
        if s.vcnumber:
            where.append(f"lower(vc.{vc}number) LIKE %s")
            wparams.append(like(s.vcnumber.lower()))
    if s.employee_id:
        where.append("a.employee_id = %s")
        wparams.append(s.employee_id)
    if s.department:
        with _db.cursor(conn) as cur:
            cur.execute("SELECT id FROM department WHERE description LIKE %s", (f"{s.department}%",))
            ids = [r["id"] for r in cur.fetchall()]
        if ids:
            where.append("a.department_id = ANY(%s)")
            wparams.append(ids)
    for fld in ("invnumber", "ordnumber", "ponumber", "shipvia", "shippingpoint", "waybill", "notes",
                "description"):
        v = getattr(s, fld)
        if v:
            where.append(f"lower(a.{fld}) LIKE %s")
            wparams.append(like(v.lower()))
    if s.source:
        if detail:
            where.append("lower(ac.source) LIKE %s")
            wparams.append(like(s.source.lower()))
        else:
            where.append("a.id = 0")
    if datefrom:
        where.append("a.transdate >= %s")
        wparams.append(datefrom)
    if dateto:
        where.append("a.transdate <= %s")
        wparams.append(dateto)
    is_open, is_closed = s.open, s.closed
    if s.paidlate or s.paidearly:
        is_open, is_closed = False, True
        if s.paidlate and not s.paidearly:
            where.append("a.datepaid > a.duedate")
        if s.paidearly and not s.paidlate:
            where.append("a.datepaid <= a.duedate")
    if s.onhold:
        where.append("a.onhold = '1'")
    if is_open and not is_closed:
        where.append("a.amount != a.paid")
    if is_closed and not is_open:
        where.append("a.amount = a.paid")
    if s.till:
        where.append("a.invoice = '1' AND a.till IS NOT NULL")
    if s.arap_accno:
        where.append("""a.id IN (SELECT ac2.trans_id FROM acc_trans ac2 JOIN chart c2 ON (c2.id = ac2.chart_id)
                                 WHERE c2.accno = %s)""")
        wparams.append(s.arap_accno)
    if s.memo:
        where.append("""(a.id IN (SELECT DISTINCT trans_id FROM acc_trans WHERE lower(memo) LIKE %s)
                         OR a.id IN (SELECT DISTINCT trans_id FROM invoice WHERE lower(description) LIKE %s))""")
        wparams += [like(s.memo.lower())] * 2

    sort = s.sort if s.sort in SORTABLE else None
    if s.sort and not sort:
        raise ValueError(f"onbekende sorteerkolom: {s.sort!r}")
    direction = "DESC" if (s.direction or "").upper() == "DESC" else "ASC"
    order = [c for c in ([sort] if sort else []) + ["transdate", "invnumber", "name"]]
    order = list(dict.fromkeys(order))
    order_sql = ", ".join(f"{_sortexpr(c, detail)} {direction if i == 0 else 'ASC'}"
                          for i, c in enumerate(order)) + ", a.id" + (", ac.id" if detail else "")

    query = f"""
        SELECT a.id, a.invnumber, a.ordnumber, a.transdate, a.duedate, ({taxfld}) * {ml} AS tax,
               a.amount, {paid} AS paid, a.invoice, a.datepaid, a.terms, a.notes, a.shipvia, a.waybill,
               a.shippingpoint, e.name AS employee, vc.name, vc.{vc}number AS vcnumber, a.{vc}_id AS vc_id,
               a.till, a.curr, a.exchangerate, d.description AS department, a.ponumber, a.description,
               a.dcn, pm.description AS paymentmethod, a.datepaid - a.duedate AS paymentdiff,
               ad.address1, ad.address2, ad.city, ad.zipcode, ad.country, c.description AS paymentaccount,
               vc.taxnumber {lines_flds}
        FROM {table} a
        JOIN {vc} vc ON (a.{vc}_id = vc.id)
        JOIN address ad ON (ad.trans_id = vc.id)
        LEFT JOIN employee e ON (a.employee_id = e.id)
        LEFT JOIN department d ON (a.department_id = d.id)
        LEFT JOIN paymentmethod pm ON (pm.id = a.paymentmethod_id)
        LEFT JOIN chart c ON (c.id = a.bank_id)
        {lines_join}
        WHERE {' AND '.join(where)}
        ORDER BY {order_sql}"""

    with _db.cursor(conn) as cur:
        defaults = _db.get_defaults(cur, "precision")
        precision = int(to_decimal(defaults.get("precision") or 2))
        defaultcurrency = _db.default_currency(cur)
        cur.execute(query, tax_params + params + wparams)
        raw = cur.fetchall()

    rows: List[Dict[str, Any]] = []
    currencies = set()
    last_id = None
    for r in raw:
        r = dict(r)
        r["curr"] = (r["curr"] or "").strip() or None
        currencies.add(r["curr"])
        r["exchangerate"] = to_decimal(r["exchangerate"]) or Decimal(1)
        for k in ("amount", "paid", "tax"):
            r[k] = to_decimal(r[k])
        r["netamount"] = r["amount"] - r["tax"]
        if detail:
            lt = to_decimal(r["linetotal"])
            r["debit"], r["credit"] = (-lt, ZERO) if lt <= 0 else (ZERO, lt)
            if r["invoice"]:
                r["memo"] = r["memo"] or r["linedescription"]
        if s.outstanding and round(r["amount"], precision) == round(r["paid"], precision):
            continue
        r["address"] = f"{r['address1'] or ''} {r['address2'] or ''} "
        if summary:
            if r["id"] != last_id:
                rows.append(r)
            elif r.get("memo") and not r.get("fx_transaction"):
                prev = rows[-1]
                prev["memo"] = f"{prev['memo']}\n{r['memo']}" if prev.get("memo") else r["memo"]
        else:
            r["first_line"] = r["id"] != last_id
            rows.append(r)
        last_id = r["id"]
    foreign = sorted(c for c in currencies if c and c != defaultcurrency)

    return _display(rows, foreign, defaultcurrency, precision, s, sort or "transdate", detail and not summary)


def _sortexpr(col: str, detail: bool) -> str:
    return {"name": "vc.name", "customernumber": "vc.customernumber", "vendornumber": "vc.vendornumber",
            "taxnumber": "vc.taxnumber", "address": "ad.address1", "city": "ad.city", "zipcode": "ad.zipcode",
            "country": "ad.country", "employee": "e.name", "department": "d.description",
            "paymentmethod": "pm.description", "paymentaccount": "c.description",
            "paymentdiff": "a.datepaid - a.duedate", "netamount": "a.netamount", "tax": "a.amount - a.netamount",
            "accno": "ch.accno" if detail else "a.id", "source": "ac.source" if detail else "a.id",
            "projectnumber": "pr.projectnumber" if detail else "a.id"}.get(col, f"a.{col}")


def _display(rows, foreign, defaultcurrency, precision, s: ArapSearch, sort: str, detail: bool) -> ArapList:
    """Rijen, subtotalen en totaal zoals aa.pl transactions (bedragen afgerond
    zoals het scherm ze toont; optellen gebeurt exact)."""
    columns = []
    base = ["netamount", "tax", "amount", "paid", "due"]
    if sort in base:        # Form::sort_columns: de sorteerkolom komt vooraan
        base.remove(sort)
        base.insert(0, sort)
    for c in base:
        columns.append(c)
        if s.l_curr:
            columns += [f"{cur}_{c}" for cur in foreign]
    if detail:
        columns += ["debit", "credit"]

    def zero():
        return {"netamount": ZERO, "amount": ZERO, "paid": ZERO, "debit": ZERO, "credit": ZERO}

    total, sub = zero(), zero()
    tcur = {c: {"netamount": ZERO, "amount": ZERO, "paid": ZERO} for c in foreign}
    scur = {c: {"netamount": ZERO, "amount": ZERO, "paid": ZERO} for c in foreign}
    display: List[DisplayRow] = []

    def subtotal_row():
        vals = []
        for c in columns:
            if c == "netamount" or c.endswith("_netamount"):
                v = None
            elif c in ("tax", "amount", "paid", "due", "debit", "credit"):
                v = {"tax": sub["amount"] - sub["netamount"], "due": sub["amount"] - sub["paid"]}.get(c, sub.get(c))
            else:
                cur, k = c.split("_", 1)
                t = scur[cur]
                v = {"tax": t["amount"] - t["netamount"], "due": t["amount"] - t["paid"]}.get(k, t.get(k))
            vals.append(v)
        display.append(DisplayRow("subtotal", "", values=vals))
        for k in sub:
            sub[k] = ZERO
        for c in scur:
            scur[c] = {"netamount": ZERO, "amount": ZERO, "paid": ZERO}

    same = rows[0].get(sort) if rows else None
    for r in rows:
        if s.l_subtotal and r.get(sort) != same:
            subtotal_row()
            same = r.get(sort)
        once = r.get("first_line", True)
        fx = {}
        if r["curr"] and r["curr"] != defaultcurrency:
            for k in ("netamount", "amount", "paid"):
                fx[k] = r[k] / r["exchangerate"]
            if once:
                for t in (tcur, scur):
                    if r["curr"] in t:
                        for k in fx:
                            t[r["curr"]][k] += fx[k]
        vals = []
        for c in columns:
            if c in ("netamount", "amount", "paid"):
                v = r[c]
            elif c == "tax":
                v = r["amount"] - r["netamount"]
            elif c == "due":
                v = r["amount"] - r["paid"]
            elif c in ("debit", "credit"):
                v = r[c]
            else:
                cur, k = c.split("_", 1)
                if cur != r["curr"]:
                    v = None
                else:
                    v = {"tax": fx["amount"] - fx["netamount"], "due": fx["amount"] - fx["paid"]}.get(k, fx.get(k))
            vals.append(v)
        display.append(DisplayRow("row", r.get("invnumber") or "", values=vals))
        if once:
            for t in (total, sub):
                for k in ("netamount", "amount", "paid"):
                    t[k] += r[k]
        if detail:
            for t in (total, sub):
                t["debit"] += r["debit"]
                t["credit"] += r["credit"]
    if s.l_subtotal:
        subtotal_row()

    vals = []
    for c in columns:
        if c in ("netamount", "amount", "paid", "debit", "credit"):
            v = total[c]
        elif c == "tax":
            v = total["amount"] - total["netamount"]
        elif c == "due":
            v = total["amount"] - total["paid"]
        else:
            cur, k = c.split("_", 1)
            t = tcur[cur]
            v = {"tax": t["amount"] - t["netamount"], "due": t["amount"] - t["paid"]}.get(k, t.get(k))
        vals.append(v)
    display.append(DisplayRow("total", "", values=vals))
    totals = {"netamount": total["netamount"], "tax": total["amount"] - total["netamount"],
              "amount": total["amount"], "paid": total["paid"], "due": total["amount"] - total["paid"]}
    if detail:
        totals.update(debit=total["debit"], credit=total["credit"])
    return ArapList(rows=rows, currencies=foreign, totals=totals,
                    totals_curr={c: {"netamount": t["netamount"], "tax": t["amount"] - t["netamount"],
                                     "amount": t["amount"], "paid": t["paid"], "due": t["amount"] - t["paid"]}
                                 for c, t in tcur.items()},
                    display=display, columns=columns, defaultcurrency=defaultcurrency,
                    precision=precision, search=s)
