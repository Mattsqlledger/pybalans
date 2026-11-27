"""Tijdkaarten: uren van een medewerker op een project en een dienst (UREN_ANALYSE.md U-1, port van JC.pm/jc.pl).

Eén tijdkaart = één regel in ``jcitems`` (zoals SQL-Ledger): datum en in/uit-tijd (``checkedin``/``checkedout``),
aantal uren (``qty``), wat er al naar een verkooporder ging (``allocated``), verkoopprijs, omschrijving en notities.
Alleen projecten zonder artikel (geen productie-opdracht, UR1) en diensten (geen voorraad, wel opbrengstrekening).

Rekenregels zoals jc.pl ``update``/``prepare_timecard``: geklokte tijd = uit − in in uren (uit vóór in = over
middernacht), afgerond op de nauwkeurigheid; aantal = geklokt − niet te declareren; ontbreekt een tijd, dan is het
aantal vrij in te vullen. Opslaan zoals ``JC::save``: ``checkedin`` = datum + in-tijd (00:00:00 als leeg), ``checkedout``
= datum (+1 dag als uit vóór in) + uit-tijd; ``fxsellprice`` = ``sellprice``; audittrail ``saved``. Anders dan SL
(UR9): nieuw id via ``RETURNING``, aantal niet lager dan wat al op een order staat, verwijderen kan niet als er al
uren op een order staan.

v1.1 interne uren (UREN_ANALYSE.md IN1–IN7): elke tijdkaart krijgt bij opslaan een **intern tarief** in
``x_jcitem_cost`` (toewijzing > medewerker > kostprijs van de dienst); interne projecten (``x_project``) gaan nooit naar
een verkooporder; ``hours_by_project`` is het rapport Uren per project. Geen boeking (IN4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Tuple

import psycopg

from . import _db, support
from .amounts import to_decimal
from .config import UserConfig
from .errors import ClosedPeriodError, NotFoundError, ValidationError
from .gl import _closedto, _num

FORMNAME = "timecard"


@dataclass
class TimeCard:
    transdate: Optional[date] = None
    project_id: Optional[int] = None
    parts_id: Optional[int] = None
    employee_id: Optional[int] = None          # leeg = de medewerker van de gebruiker
    description: str = ""
    qty: Decimal = Decimal(0)
    allocated: Decimal = Decimal(0)
    sellprice: Decimal = Decimal(0)
    checkedin: Optional[time] = None
    checkedout: Optional[time] = None
    notes: str = ""
    serialnumber: str = ""
    id: Optional[int] = None
    # alleen lezen
    partnumber: str = ""
    projectnumber: str = ""
    employee: str = ""
    costprice: Optional[Decimal] = None          # vastgelegd intern tarief (IN3); None = (nog) niet vastgelegd
    costsource: str = ""                         # assignment | employee | service


@dataclass
class TimeCardSearch:
    project_id: Optional[int] = None
    employee_id: Optional[int] = None
    partnumber: str = ""
    description: str = ""
    notes: str = ""
    datefrom: Optional[date] = None
    dateto: Optional[date] = None
    open: bool = True              # nog niet (helemaal) naar een order
    closed: bool = True            # helemaal naar een order


#: soort project in SQL (``pr`` = project, ``xp`` = x_project), zie masterdata.KIND_SQL
_KIND_SQL = ("CASE WHEN xp.kind IS NOT NULL THEN xp.kind WHEN pr.customer_id IS NOT NULL THEN 'customer' "
             "ELSE 'none' END")


def _ensure(conn: psycopg.Connection) -> None:
    from .extensions import ensure_schema
    ensure_schema(conn)


def internal_rate(cur, employee_id: Optional[int], project_id: int, parts_id: int,
                  day: date) -> Tuple[Decimal, str]:
    """Intern tarief op een dag (IN3): intern tarief van de toewijzing > intern uurtarief van de medewerker (laatste
    geldig-vanaf op of vóór de dag) > kostprijs van de dienst (``parts.lastcost``). Geeft (tarief, bron)."""
    if employee_id:
        cur.execute("""SELECT internalrate FROM x_assignment
                       WHERE employee_id = %s AND project_id = %s AND parts_id = %s AND internalrate IS NOT NULL
                         AND validfrom <= %s AND (validto IS NULL OR validto >= %s)
                       ORDER BY validfrom DESC LIMIT 1""", (employee_id, project_id, parts_id, day, day))
        r = cur.fetchone()
        if r:
            return to_decimal(r["internalrate"]), "assignment"
        cur.execute("""SELECT rate FROM x_employee_rate WHERE employee_id = %s AND validfrom <= %s
                       ORDER BY validfrom DESC LIMIT 1""", (employee_id, day))
        r = cur.fetchone()
        if r:
            return to_decimal(r["rate"]), "employee"
    cur.execute("SELECT lastcost FROM parts WHERE id = %s", (parts_id,))
    r = cur.fetchone()
    return to_decimal((r or {}).get("lastcost") or 0), "service"


def _round(v: Decimal, places: int) -> Decimal:
    return v.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def seconds(t: Optional[time]) -> int:
    return 0 if t is None else t.hour * 3600 + t.minute * 60 + t.second


def clocked(checkedin: Optional[time], checkedout: Optional[time], places: int = 2) -> Optional[Decimal]:
    """Geklokte uren zoals jc.pl: uit vóór in = over middernacht; zonder uit-tijd geen geklokte tijd."""
    tin, tout = seconds(checkedin), seconds(checkedout)
    if not tout:
        return None
    if tin > tout:
        tout, tin = 86400 - (tin - tout), 0
    return _round(Decimal(tout - tin) / Decimal(3600), places)


class TimeCards:
    """Tijdkaarten lezen, opslaan, verwijderen en zoeken (JC.pm, type timecard, project)."""

    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        _ensure(conn)
        self.conn = conn
        self.config = config

    # ------------------------------------------------------------ keuzelijsten (JC::jcitems_links, retrieve_item)

    def links(self, transdate: Optional[date] = None, include_project: Optional[int] = None,
              include_employee: Optional[int] = None) -> Dict[str, Any]:
        """Projecten (zonder artikel, actief op de datum, plus het huidige), medewerkers (in dienst op de datum, plus
        de huidige) en diensten (geen voorraad, wel opbrengst, niet vervallen, niet samengesteld)."""
        with _db.cursor(self.conn) as cur:
            where, params = "", []
            if transdate:
                where = " AND (enddate IS NULL OR enddate >= %s) AND (startdate IS NULL OR startdate <= %s)"
                params = [transdate, transdate]
            cur.execute(f"""SELECT pr.id, pr.projectnumber, pr.description, pr.customer_id, {_KIND_SQL} AS kind
                            FROM project pr LEFT JOIN x_project xp ON (xp.project_id = pr.id)
                            WHERE pr.id IN (SELECT id FROM project WHERE parts_id IS NULL{where}
                                            UNION SELECT %s)
                            ORDER BY pr.projectnumber""", params + [include_project or 0])
            projects = cur.fetchall()
            cur.execute(f"""SELECT id, name, employeenumber FROM employee
                            WHERE (enddate IS NULL OR enddate >= %s) AND (startdate IS NULL OR startdate <= %s)
                            UNION SELECT id, name, employeenumber FROM employee WHERE id = %s
                            ORDER BY 2, 1""", (transdate or date.today(), transdate or date.today(),
                                               include_employee or 0))
            employees = cur.fetchall()
            cur.execute("""SELECT id, partnumber, description, sellprice, unit FROM parts
                           WHERE inventory_accno_id IS NULL AND income_accno_id IS NOT NULL
                             AND NOT coalesce(obsolete, false) AND NOT coalesce(assembly, false)
                           ORDER BY partnumber""")
            services = cur.fetchall()
            for s in services:
                s["sellprice"] = to_decimal(s["sellprice"])
            precision = _db.get_precision(cur)
        return {"projects": projects, "employees": employees, "services": services, "precision": precision}

    # ------------------------------------------------------------ lezen

    def get(self, card_id: int) -> TimeCard:
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT j.*, p.partnumber, pr.projectnumber, pr.parts_id AS job, e.name AS employee,
                                  x.costprice, x.source AS costsource
                           FROM jcitems j
                           JOIN parts p ON (p.id = j.parts_id)
                           JOIN project pr ON (pr.id = j.project_id)
                           LEFT JOIN employee e ON (e.id = j.employee_id)
                           LEFT JOIN x_jcitem_cost x ON (x.jcitem_id = j.id)
                           WHERE j.id = %s""", (card_id,))
            r = cur.fetchone()
        if not r:
            raise NotFoundError(f"Tijdkaart {card_id} niet gevonden.")
        tin, tout = r["checkedin"], r["checkedout"]
        return TimeCard(
            id=r["id"], transdate=tin.date() if tin else None, project_id=r["project_id"], parts_id=r["parts_id"],
            employee_id=r["employee_id"], description=r["description"] or "", qty=to_decimal(r["qty"]),
            allocated=to_decimal(r["allocated"]), sellprice=to_decimal(r["sellprice"]),
            checkedin=tin.timetz().replace(tzinfo=None) if tin else None,
            checkedout=tout.timetz().replace(tzinfo=None) if tout else None,
            notes=r["notes"] or "", serialnumber=r["serialnumber"] or "", partnumber=r["partnumber"] or "",
            projectnumber=r["projectnumber"] or "", employee=r["employee"] or "",
            costprice=to_decimal(r["costprice"]) if r["costprice"] is not None else None,
            costsource=r["costsource"] or "")

    # ------------------------------------------------------------ opslaan (jc.pl save, JC::save)

    def _check(self, cur, tc: TimeCard) -> Tuple[int, Optional[int]]:
        if not tc.transdate:
            raise ValidationError("Date missing!")
        if not tc.project_id:
            raise ValidationError("Project Number missing!")
        if not tc.parts_id:
            raise ValidationError("Service Code missing!")
        d = _db.get_defaults(cur, "closedto", "precision")
        closedto = _closedto(d, self.config.dateformat)
        if closedto and _num(tc.transdate) <= closedto:
            raise ClosedPeriodError("Cannot save time card for a closed period!")
        cur.execute("SELECT parts_id FROM project WHERE id = %s", (tc.project_id,))
        pr = cur.fetchone()
        if not pr:
            raise ValidationError("Project Number missing!")
        if pr["parts_id"]:
            raise ValidationError("Een tijdkaart voor een productie-opdracht kan hier niet.")
        cur.execute("SELECT inventory_accno_id, income_accno_id FROM parts WHERE id = %s", (tc.parts_id,))
        p = cur.fetchone()
        if not p or p["inventory_accno_id"] or not p["income_accno_id"]:
            raise ValidationError("Kies een dienst (zonder voorraad, met opbrengstrekening).")
        employee_id = tc.employee_id
        if not employee_id:
            _, employee_id = _db.get_employee(cur, self.config.short_login)
        if to_decimal(tc.qty) < 0:
            raise ValidationError("Het aantal uren kan niet negatief zijn.")
        return int(to_decimal(d.get("precision") or 2)), employee_id or None

    def save(self, tc: TimeCard) -> int:
        """Nieuwe tijdkaart of een bestaande wijzigen. Geeft het id."""
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            _, employee_id = self._check(cur, tc)
            allocated = Decimal(0)
            old = None
            if tc.id:
                cur.execute("""SELECT allocated, checkedin, employee_id, project_id, parts_id FROM jcitems
                               WHERE id = %s FOR UPDATE""", (tc.id,))
                old = cur.fetchone()
                if not old:
                    raise NotFoundError(f"Tijdkaart {tc.id} niet gevonden.")
                allocated = to_decimal(old["allocated"])
                d = _db.get_defaults(cur, "closedto")
                closedto = _closedto(d, self.config.dateformat)
                if closedto and old["checkedin"] and _num(old["checkedin"].date()) <= closedto:
                    raise ClosedPeriodError("Cannot save time card for a closed period!")
                if to_decimal(tc.qty) < allocated:
                    raise ValidationError(f"Er staan al {allocated.normalize()} uren op een verkooporder; het aantal "
                                          "kan niet lager.")
            tin = tc.checkedin or time(0, 0, 0)
            tout = tc.checkedout or time(0, 0, 0)
            outdate = tc.transdate + timedelta(days=1) if seconds(tout) < seconds(tin) else tc.transdate
            values = dict(project_id=tc.project_id, parts_id=tc.parts_id, description=tc.description or None,
                          qty=to_decimal(tc.qty), allocated=allocated, sellprice=to_decimal(tc.sellprice),
                          fxsellprice=to_decimal(tc.sellprice), serialnumber=tc.serialnumber or None,
                          checkedin=datetime.combine(tc.transdate, tin), checkedout=datetime.combine(outdate, tout),
                          employee_id=employee_id, notes=tc.notes or None)
            if tc.id:
                cols = ", ".join(f"{k} = %({k})s" for k in values)
                cur.execute(f"UPDATE jcitems SET {cols} WHERE id = %(id)s", {**values, "id": tc.id})
                card_id = tc.id
            else:
                cols = ", ".join(values)
                cur.execute(f"INSERT INTO jcitems ({cols}) VALUES ({', '.join('%(' + k + ')s' for k in values)}) "
                            "RETURNING id", values)
                card_id = cur.fetchone()["id"]
            self._fix_cost(cur, card_id, old, employee_id, tc)
            support.audittrail(cur, self.config, trans_id=card_id, tablename="jcitems", reference=str(card_id),
                               formname=FORMNAME, action="saved")
            support.remove_locks(cur, self.config, "jcitems")
        tc.id = card_id
        return card_id

    def _fix_cost(self, cur, card_id: int, old: Optional[Dict[str, Any]], employee_id: Optional[int],
                  tc: TimeCard) -> None:
        """Intern tarief vastleggen (IN3): bij een nieuwe tijdkaart altijd; bij wijzigen alleen opnieuw als
        medewerker, project, dienst of datum anders is (of er nog geen tarief vastligt)."""
        if old is not None:
            same = (old["employee_id"], old["project_id"], old["parts_id"],
                    old["checkedin"].date() if old["checkedin"] else None) == \
                (employee_id, tc.project_id, tc.parts_id, tc.transdate)
            cur.execute("SELECT 1 FROM x_jcitem_cost WHERE jcitem_id = %s", (card_id,))
            if same and cur.fetchone():
                return
        rate, source = internal_rate(cur, employee_id, tc.project_id, tc.parts_id, tc.transdate)
        cur.execute("""INSERT INTO x_jcitem_cost (jcitem_id, costprice, source, login) VALUES (%s, %s, %s, %s)
                       ON CONFLICT (jcitem_id) DO UPDATE SET costprice = excluded.costprice, source = excluded.source,
                           login = excluded.login, updated = now()""", (card_id, rate, source, self.config.login))
        tc.costprice, tc.costsource = rate, source

    def delete(self, card_id: int) -> None:
        """Tijdkaart verwijderen (JC::delete); niet als er al uren op een verkooporder staan (UR9)."""
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("SELECT allocated, checkedin FROM jcitems WHERE id = %s FOR UPDATE", (card_id,))
            r = cur.fetchone()
            if not r:
                raise NotFoundError(f"Tijdkaart {card_id} niet gevonden.")
            if to_decimal(r["allocated"]):
                raise ValidationError("Deze uren staan al op een verkooporder; de tijdkaart kan niet weg.")
            closedto = _closedto(_db.get_defaults(cur, "closedto"), self.config.dateformat)
            if closedto and r["checkedin"] and _num(r["checkedin"].date()) <= closedto:
                raise ClosedPeriodError("Cannot save time card for a closed period!")
            support.audittrail(cur, self.config, trans_id=card_id, tablename="jcitems", reference=str(card_id),
                               formname=FORMNAME, action="deleted")
            cur.execute("DELETE FROM jcitems WHERE id = %s", (card_id,))
            cur.execute("DELETE FROM x_jcitem_cost WHERE jcitem_id = %s", (card_id,))
            cur.execute("DELETE FROM status WHERE formname = %s AND trans_id = %s", (FORMNAME, card_id))
            support.remove_locks(cur, self.config, "jcitems")

    # ------------------------------------------------------------ zoeken (JC::jcitems)

    def search(self, s: Optional[TimeCardSearch] = None) -> List[Dict[str, Any]]:
        s = s or TimeCardSearch()
        where, params = ["pr.parts_id IS NULL"], []
        if s.project_id:
            where.append("j.project_id = %s")
            params.append(s.project_id)
        if s.employee_id:
            where.append("j.employee_id = %s")
            params.append(s.employee_id)
        for col, value in (("p.partnumber", s.partnumber), ("j.description", s.description), ("j.notes", s.notes)):
            if value:
                where.append(f"lower({col}) LIKE %s")
                params.append(f"%{value.lower()}%")
        if s.open != s.closed:
            where.append("(j.qty != j.allocated OR j.qty = 0)" if s.open else "j.qty = j.allocated")
        elif not s.open:
            return []
        if s.datefrom:
            where.append("j.checkedin >= %s")
            params.append(s.datefrom)
        if s.dateto:
            where.append("j.checkedout < %s")
            params.append(s.dateto + timedelta(days=1))
        with _db.cursor(self.conn) as cur:
            cur.execute(f"""SELECT j.id, j.description, j.qty, j.allocated, j.sellprice, j.notes,
                                   j.checkedin, j.checkedout, j.checkedin::date AS transdate,
                                   p.partnumber, pr.projectnumber, pr.description AS projectdescription,
                                   e.employeenumber, e.name AS employee, j.employee_id, j.project_id,
                                   {_KIND_SQL} AS kind
                            FROM jcitems j
                            JOIN parts p ON (p.id = j.parts_id)
                            JOIN project pr ON (pr.id = j.project_id)
                            LEFT JOIN x_project xp ON (xp.project_id = pr.id)
                            LEFT JOIN employee e ON (e.id = j.employee_id)
                            WHERE {' AND '.join(where)}
                            ORDER BY e.name, e.employeenumber, j.checkedin::date, pr.projectnumber, j.id""", params)
            out = cur.fetchall()
        for r in out:
            for k in ("qty", "allocated", "sellprice"):
                r[k] = to_decimal(r[k])
            r["amount"] = r["qty"] * r["sellprice"]
        return out


def form_update(tc: TimeCard, *, old_in: Optional[time], old_out: Optional[time], old_qty: Decimal,
                qty_given: Optional[Decimal], noncharge: Decimal = Decimal(0), places: int = 2) -> Optional[Decimal]:
    """Formulierregels van jc.pl ``update`` voor het aantal: zijn de tijden gewijzigd, dan aantal = geklokt − niet te
    declareren; is het aantal zelf gewijzigd (en is er geklokte tijd), dan volgt het niet te declareren deel daaruit.
    Zet ``tc.qty`` en geeft de geklokte uren (of None)."""
    c = clocked(tc.checkedin, tc.checkedout, places)
    times_changed = (seconds(tc.checkedin), seconds(tc.checkedout)) != (seconds(old_in), seconds(old_out))
    if c is not None and times_changed:
        tc.qty = c - to_decimal(noncharge)
    if qty_given is not None and to_decimal(qty_given) != to_decimal(old_qty if not times_changed else tc.qty):
        tc.qty = to_decimal(qty_given)
    return c


# ------------------------------------------------------------------ verkooporder uit project (U-2, PE.pm/pe.pl)

@dataclass
class OpenItem:
    """Een regel voor "Verkooporders maken": één tijdkaart, of (samengevat) alle tijdkaarten met hetzelfde project,
    dezelfde klant en dezelfde dienst achter elkaar (pe.pl ``project_jcitems_list``)."""
    project_id: int
    projectnumber: str
    customer_id: Optional[int]
    customer: str
    customernumber: str
    parts_id: int
    partnumber: str
    unit: str
    description: str
    transdate: date
    qty: Decimal
    sellprice: Decimal
    amount: Decimal
    employee: str
    itemnotes: str
    cards: List[Tuple[int, Decimal]]       # (jcitems.id, aantal) dat naar de order gaat


def _open_rows(cur, project_id, employee_id, datefrom, dateto, summary: bool) -> List[Dict[str, Any]]:
    # interne projecten gaan nooit naar een verkooporder (IN6)
    where, params = ["pr.parts_id IS NULL", "j.allocated != j.qty",
                     "NOT EXISTS (SELECT 1 FROM x_project xp WHERE xp.project_id = j.project_id "
                     "AND xp.kind = 'internal')"], []
    if project_id:
        where.append("j.project_id = %s")
        params.append(project_id)
    if employee_id:
        where.append("j.employee_id = %s")
        params.append(employee_id)
    if datefrom:
        where.append("j.checkedin >= %s")
        params.append(datefrom)
    if dateto:
        where.append("j.checkedout <= %s")
        params.append(dateto + timedelta(days=1))
    desc = "p.description" if summary else "j.description"
    order = "pr.projectnumber, c.name, j.parts_id, j.checkedin::date" if summary else \
        "pr.projectnumber, c.name, j.checkedin::date"
    cur.execute(f"""SELECT j.id, {desc} AS description, j.qty - j.allocated AS qty, j.sellprice, j.parts_id,
                           pr.customer_id, j.project_id, j.checkedin::date AS transdate, j.notes,
                           c.name AS customer, c.customernumber, pr.projectnumber, p.partnumber, p.unit,
                           e.name AS employee
                    FROM jcitems j
                    JOIN project pr ON (pr.id = j.project_id)
                    JOIN employee e ON (e.id = j.employee_id)
                    JOIN parts p ON (p.id = j.parts_id)
                    LEFT JOIN customer c ON (c.id = pr.customer_id)
                    WHERE {' AND '.join(where)}
                    ORDER BY {order}, j.id""", params)
    return cur.fetchall()


def open_items(conn: psycopg.Connection, *, project_id: Optional[int] = None, employee_id: Optional[int] = None,
               datefrom: Optional[date] = None, dateto: Optional[date] = None, summary: bool = False) -> List[OpenItem]:
    """Uren die nog niet (helemaal) op een verkooporder staan (``PE::get_jcitems``; alleen tijdkaarten met een
    medewerker, zoals SL)."""
    out: List[OpenItem] = []
    _ensure(conn)
    with _db.cursor(conn) as cur:
        precision = _db.get_precision(cur)
        for r in _open_rows(cur, project_id, employee_id, datefrom, dateto, summary):
            qty, price = to_decimal(r["qty"]), to_decimal(r["sellprice"])
            key = (r["project_id"], r["customer_id"], r["parts_id"])
            if summary and out and (out[-1].project_id, out[-1].customer_id, out[-1].parts_id) == key:
                last = out[-1]
                last.qty += qty
                last.amount += qty * price
                last.cards.append((r["id"], qty))
                if r["notes"]:
                    last.itemnotes = f"{last.itemnotes}\n\n{r['notes']}" if last.itemnotes else r["notes"]
                continue
            out.append(OpenItem(
                project_id=r["project_id"], projectnumber=r["projectnumber"] or "", customer_id=r["customer_id"],
                customer=r["customer"] or "", customernumber=r["customernumber"] or "", parts_id=r["parts_id"],
                partnumber=r["partnumber"] or "", unit=r["unit"] or "", description=r["description"] or "",
                transdate=r["transdate"], qty=qty, sellprice=price, amount=qty * price, employee=r["employee"] or "",
                itemnotes=r["notes"] or "", cards=[(r["id"], qty)]))
    for it in out:
        it.amount = _round(it.amount, precision)
    return out


def generate_sales_orders(conn: psycopg.Connection, config: UserConfig, items: List[OpenItem],
                          customers: Optional[Dict[int, int]] = None, today: Optional[date] = None) -> List[int]:
    """Verkooporders maken (pe.pl ``generate_sales_orders``): per klant één order met de gekozen regels (prijs van de
    tijdkaart, project op de regel, leverdatum = datum van de uren), daarna ``allocated`` bijwerken. ``customers``:
    klant per regel (index) voor projecten zonder klant. Alles in één transactie (UR9). Geeft de order-id's."""
    from .order import Order, OrderLedger, OrderLine
    customers = customers or {}
    per_customer: Dict[int, List[OpenItem]] = {}
    for n, it in enumerate(items):
        cust = it.customer_id or customers.get(n)
        if not cust:
            raise ValidationError("Customer missing!")
        per_customer.setdefault(int(cust), []).append(it)
    if not per_customer:
        raise ValidationError("Nothing to generate!")
    _ensure(conn)
    with _db.cursor(conn) as cur:
        cur.execute("""SELECT 1 FROM x_project WHERE kind = 'internal' AND project_id = ANY(%s)""",
                    ([it.project_id for it in items],))
        if cur.fetchone():                              # IN6: ook op de server, niet alleen het scherm
            raise ValidationError("Uren op een intern project worden niet gefactureerd.")
    day = today or date.today()
    ledger = OrderLedger(conn, config)
    ids: List[int] = []
    with conn.transaction(), _db.cursor(conn) as cur:
        for cust, rows in sorted(per_customer.items()):
            d = ledger.arap.vc_defaults("customer", cust, day)       # zoals AA->get_name in pe.pl
            o = Order(type="sales_order", vc_id=cust, transdate=day, reqdate=day, terms=int(d.get("terms") or 0),
                      taxincluded=bool(d.get("taxincluded")), currency=d.get("currency") or None,
                      language_code=d.get("language_code") or None, lines=[
                OrderLine(parts_id=it.parts_id, qty=it.qty, sellprice=it.sellprice, description=it.description,
                          unit=it.unit or None, reqdate=it.transdate, project_id=it.project_id,
                          itemnotes=it.itemnotes or None) for it in rows])
            ids.append(ledger.save_order(o))
            for it in rows:
                for card_id, qty in it.cards:
                    _db.update_balance(cur, "jcitems", "allocated", card_id, qty)
    return ids


# ------------------------------------------------------------------ toewijzing en maandstaat (U-3, UR5–UR7)

@dataclass
class Assignment:
    employee_id: int
    project_id: int
    parts_id: int
    validfrom: date
    validto: Optional[date] = None
    sellprice: Optional[Decimal] = None          # leeg = prijs van de dienst
    projectleader_id: Optional[int] = None
    notes: str = ""
    id: Optional[int] = None
    internalrate: Optional[Decimal] = None       # leeg = intern tarief van de medewerker (IN3)
    # alleen lezen
    employee: str = ""
    projectnumber: str = ""
    partnumber: str = ""
    description: str = ""
    projectleader: str = ""


class Assignments:
    """Wie op welk project met welke dienst uren schrijft (``x_assignment``)."""

    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        from .extensions import ensure_schema
        ensure_schema(conn)
        self.conn = conn
        self.config = config

    def list(self, employee_id: Optional[int] = None, active_on: Optional[date] = None) -> List[Assignment]:
        where, params = ["true"], []
        if employee_id:
            where.append("a.employee_id = %s")
            params.append(employee_id)
        if active_on:
            where.append("a.validfrom <= %s AND (a.validto IS NULL OR a.validto >= %s)")
            params += [active_on, active_on]
        with _db.cursor(self.conn) as cur:
            cur.execute(f"""SELECT a.*, e.name AS employee, pr.projectnumber, p.partnumber, p.description,
                                   l.name AS projectleader
                            FROM x_assignment a
                            JOIN employee e ON (e.id = a.employee_id)
                            JOIN project pr ON (pr.id = a.project_id)
                            JOIN parts p ON (p.id = a.parts_id)
                            LEFT JOIN employee l ON (l.id = a.projectleader_id)
                            WHERE {' AND '.join(where)}
                            ORDER BY e.name, pr.projectnumber, p.partnumber, a.validfrom""", params)
            rows = cur.fetchall()
        return [Assignment(employee_id=r["employee_id"], project_id=r["project_id"], parts_id=r["parts_id"],
                           validfrom=r["validfrom"], validto=r["validto"],
                           sellprice=to_decimal(r["sellprice"]) if r["sellprice"] is not None else None,
                           projectleader_id=r["projectleader_id"], notes=r["notes"] or "", id=r["id"],
                           internalrate=to_decimal(r["internalrate"]) if r["internalrate"] is not None else None,
                           employee=r["employee"] or "", projectnumber=r["projectnumber"] or "",
                           partnumber=r["partnumber"] or "", description=r["description"] or "",
                           projectleader=r["projectleader"] or "") for r in rows]

    def get(self, assignment_id: int) -> Assignment:
        for a in self.list():
            if a.id == assignment_id:
                return a
        raise NotFoundError(f"Toewijzing {assignment_id} niet gevonden.")

    def save(self, a: Assignment, login: str = "") -> int:
        """Toewijzing toevoegen of wijzigen; dezelfde medewerker/project/dienst mag niet in overlappende perioden."""
        if not (a.employee_id and a.project_id and a.parts_id and a.validfrom):
            raise ValidationError("Kies medewerker, project, dienst en begindatum.")
        if a.validto and a.validto < a.validfrom:
            raise ValidationError("De einddatum ligt vóór de begindatum.")
        if a.internalrate is not None and to_decimal(a.internalrate) < 0:
            raise ValidationError("Het interne tarief kan niet negatief zijn.")
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("SELECT parts_id FROM project WHERE id = %s", (a.project_id,))
            pr = cur.fetchone()
            if not pr or pr["parts_id"]:
                raise ValidationError("Kies een project (geen productie-opdracht).")
            cur.execute("SELECT inventory_accno_id, income_accno_id FROM parts WHERE id = %s", (a.parts_id,))
            p = cur.fetchone()
            if not p or p["inventory_accno_id"] or not p["income_accno_id"]:
                raise ValidationError("Kies een dienst (zonder voorraad, met opbrengstrekening).")
            cur.execute("""SELECT 1 FROM x_assignment
                           WHERE employee_id = %s AND project_id = %s AND parts_id = %s AND id IS DISTINCT FROM %s
                             AND validfrom <= coalesce(%s, 'infinity'::date)
                             AND coalesce(validto, 'infinity'::date) >= %s""",
                        (a.employee_id, a.project_id, a.parts_id, a.id, a.validto, a.validfrom))
            if cur.fetchone():
                raise ValidationError("Deze medewerker heeft dit project met deze dienst al in die periode.")
            values = (a.employee_id, a.project_id, a.parts_id, a.validfrom, a.validto, a.sellprice,
                      a.projectleader_id, a.notes or None, login or self.config.login, a.internalrate)
            if a.id:
                cur.execute("""UPDATE x_assignment SET employee_id = %s, project_id = %s, parts_id = %s,
                                      validfrom = %s, validto = %s, sellprice = %s, projectleader_id = %s, notes = %s,
                                      login = %s, internalrate = %s, updated = now() WHERE id = %s RETURNING id""",
                            values + (a.id,))
                if not cur.fetchone():
                    raise NotFoundError(f"Toewijzing {a.id} niet gevonden.")
                return a.id
            cur.execute("""INSERT INTO x_assignment (employee_id, project_id, parts_id, validfrom, validto, sellprice,
                                                     projectleader_id, notes, login, internalrate)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""", values)
            return cur.fetchone()["id"]


@dataclass
class SheetRow:
    project_id: int
    parts_id: int
    projectnumber: str
    partnumber: str
    description: str
    sellprice: Decimal
    hours: Dict[int, Decimal]                  # dag → uren (som van de tijdkaarten)
    locked: Dict[int, str]                     # dag → reden dat de dag niet in de maandstaat te wijzigen is
    assigned: bool = True                      # uit een toewijzing (anders: alleen omdat er al uren staan)
    internal: bool = False                     # intern project (IN6: label "intern")


def month_days(year: int, month: int) -> List[date]:
    from calendar import monthrange
    return [date(year, month, d) for d in range(1, monthrange(year, month)[1] + 1)]


def month_sheet(conn: psycopg.Connection, config: UserConfig, employee_id: int, year: int, month: int) -> List[SheetRow]:
    """Maandstaat (UR5–UR7): per toegewezen project/dienst (actief in de maand) een rij met de uren per dag; plus
    rijen voor uren die er al staan zonder toewijzing. Een dag is vergrendeld als er al uren op een order staan
    (UR6) of als er meer dan één tijdkaart op die dag staat (dan via Tijdkaarten wijzigen)."""
    days = month_days(year, month)
    first, last = days[0], days[-1]
    rows: Dict[Tuple[int, int], SheetRow] = {}
    with _db.cursor(conn) as cur:
        from .extensions import ensure_schema
        ensure_schema(conn)
        cur.execute("""SELECT a.project_id, a.parts_id, pr.projectnumber, p.partnumber, p.description,
                              coalesce(a.sellprice, p.sellprice) AS sellprice
                       FROM x_assignment a JOIN project pr ON (pr.id = a.project_id) JOIN parts p ON (p.id = a.parts_id)
                       WHERE a.employee_id = %s AND a.validfrom <= %s AND (a.validto IS NULL OR a.validto >= %s)
                       ORDER BY pr.projectnumber, p.partnumber""", (employee_id, last, first))
        for r in cur.fetchall():
            rows.setdefault((r["project_id"], r["parts_id"]), SheetRow(
                r["project_id"], r["parts_id"], r["projectnumber"], r["partnumber"], r["description"],
                to_decimal(r["sellprice"]), {}, {}))
        cur.execute("""SELECT j.project_id, j.parts_id, j.checkedin::date AS day, sum(j.qty) AS qty,
                              sum(j.allocated) AS allocated, count(*) AS n, pr.projectnumber, p.partnumber,
                              p.description, max(j.sellprice) AS sellprice
                       FROM jcitems j JOIN project pr ON (pr.id = j.project_id) JOIN parts p ON (p.id = j.parts_id)
                       WHERE j.employee_id = %s AND j.checkedin >= %s AND j.checkedin < %s AND pr.parts_id IS NULL
                       GROUP BY j.project_id, j.parts_id, j.checkedin::date, pr.projectnumber, p.partnumber,
                                p.description""", (employee_id, first, last + timedelta(days=1)))
        for r in cur.fetchall():
            key = (r["project_id"], r["parts_id"])
            if key not in rows:
                rows[key] = SheetRow(r["project_id"], r["parts_id"], r["projectnumber"], r["partnumber"],
                                     r["description"], to_decimal(r["sellprice"]), {}, {}, assigned=False)
            row = rows[key]
            row.hours[r["day"].day] = to_decimal(r["qty"])
            if to_decimal(r["allocated"]):
                row.locked[r["day"].day] = "order"
            elif r["n"] > 1:
                row.locked[r["day"].day] = "multiple"
        closedto = _closedto(_db.get_defaults(cur, "closedto"), config.dateformat)
    if closedto:
        for row in rows.values():
            for d in days:
                if _num(d) <= closedto:
                    row.locked.setdefault(d.day, "closed")
    if rows:
        with _db.cursor(conn) as cur:
            cur.execute("SELECT project_id FROM x_project WHERE kind = 'internal' AND project_id = ANY(%s)",
                        (list({k[0] for k in rows}),))
            internal = {r["project_id"] for r in cur.fetchall()}
        for row in rows.values():
            row.internal = row.project_id in internal
    return sorted(rows.values(), key=lambda r: (not r.assigned, r.projectnumber, r.partnumber))


def save_month(conn: psycopg.Connection, config: UserConfig, employee_id: int, year: int, month: int,
               cells: Dict[Tuple[int, int, int], Decimal]) -> int:
    """Maandstaat opslaan (UR6): alleen dagen die anders zijn dan wat er staat; vergrendelde dagen niet (fout);
    0 of leeg = de tijdkaart van die dag weg; één tijdkaart per dag per project/dienst, zonder tijden (UR7). Alleen
    toegewezen combinaties of combinaties waar al uren op staan. Alles in één transactie. Geeft het aantal wijzigingen."""
    sheet = {(r.project_id, r.parts_id): r for r in month_sheet(conn, config, employee_id, year, month)}
    lib = TimeCards(conn, config)
    changes = 0
    with conn.transaction(), _db.cursor(conn) as cur:
        for (project_id, parts_id, day), qty in sorted(cells.items()):
            row = sheet.get((project_id, parts_id))
            if row is None:
                raise ValidationError("Dit project met deze dienst is niet aan de medewerker toegewezen.")
            qty = to_decimal(qty)
            if qty == row.hours.get(day, Decimal(0)):
                continue
            if qty < 0:
                raise ValidationError("Het aantal uren kan niet negatief zijn.")
            if day in row.locked:
                raise ValidationError(f"De uren op {day}-{month}-{year} kunnen hier niet gewijzigd worden.")
            when = date(year, month, day)
            cur.execute("""SELECT id FROM jcitems WHERE employee_id = %s AND project_id = %s AND parts_id = %s
                             AND checkedin >= %s AND checkedin < %s""",
                        (employee_id, project_id, parts_id, when, when + timedelta(days=1)))
            existing = cur.fetchone()
            if existing and not qty:
                lib.delete(existing["id"])
            elif existing:
                tc = lib.get(existing["id"])
                tc.qty = qty                         # tijden laten staan (het verschil is niet te declareren)
                lib.save(tc)
            else:
                lib.save(TimeCard(transdate=when, project_id=project_id, parts_id=parts_id, employee_id=employee_id,
                                  qty=qty, sellprice=row.sellprice, description=row.description))
            changes += 1
    return changes


def timesheet_rows(conn: psycopg.Connection, employee_id: int, year: int, month: int,
                   project_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Uren van een medewerker in een maand (eventueel één project) voor de urenstaat (U-4): datum, project, dienst,
    omschrijving, uren, al op een order."""
    days = month_days(year, month)
    where, params = ["j.employee_id = %s", "j.checkedin >= %s", "j.checkedin < %s", "pr.parts_id IS NULL"], \
        [employee_id, days[0], days[-1] + timedelta(days=1)]
    if project_id:
        where.append("j.project_id = %s")
        params.append(project_id)
    with _db.cursor(conn) as cur:
        cur.execute(f"""SELECT j.checkedin::date AS transdate, pr.projectnumber, pr.description AS projectdescription,
                               p.partnumber, coalesce(j.description, p.description) AS description, j.qty, j.allocated,
                               j.notes
                        FROM jcitems j JOIN project pr ON (pr.id = j.project_id) JOIN parts p ON (p.id = j.parts_id)
                        WHERE {' AND '.join(where)}
                        ORDER BY j.checkedin::date, pr.projectnumber, p.partnumber, j.id""", params)
        out = cur.fetchall()
    for r in out:
        r["qty"], r["allocated"] = to_decimal(r["qty"]), to_decimal(r["allocated"])
    return out


# ------------------------------------------------------------------ v1.1 interne uren (UREN_ANALYSE.md IN3, IN5)

class InternalRates:
    """Intern uurtarief per medewerker met geldig-vanaf (``x_employee_rate``, IN3). Een tarief geldt vanaf zijn datum
    tot het volgende; dezelfde datum nog eens = overschrijven. Vastgelegde tijdkaarten veranderen niet mee."""

    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        _ensure(conn)
        self.conn = conn
        self.config = config

    def list(self, employee_id: Optional[int] = None) -> List[Dict[str, Any]]:
        where, params = "", []
        if employee_id:
            where, params = "WHERE r.employee_id = %s", [employee_id]
        with _db.cursor(self.conn) as cur:
            cur.execute(f"""SELECT r.id, r.employee_id, e.name AS employee, r.validfrom, r.rate, r.notes
                            FROM x_employee_rate r LEFT JOIN employee e ON (e.id = r.employee_id) {where}
                            ORDER BY e.name, r.employee_id, r.validfrom DESC""", params)
            rows = cur.fetchall()
        for r in rows:
            r["rate"] = to_decimal(r["rate"])
        return rows

    def rate_on(self, employee_id: int, day: date) -> Optional[Decimal]:
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT rate FROM x_employee_rate WHERE employee_id = %s AND validfrom <= %s
                           ORDER BY validfrom DESC LIMIT 1""", (employee_id, day))
            r = cur.fetchone()
        return to_decimal(r["rate"]) if r else None

    def save(self, employee_id: int, validfrom: date, rate: Any, notes: str = "", login: str = "") -> int:
        if not employee_id or not validfrom:
            raise ValidationError("Kies een medewerker en een datum.")
        rate = to_decimal(rate)
        if rate < 0:
            raise ValidationError("Het interne tarief kan niet negatief zijn.")
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("SELECT 1 FROM employee WHERE id = %s", (employee_id,))
            if not cur.fetchone():
                raise ValidationError("Onbekende medewerker.")
            cur.execute("""INSERT INTO x_employee_rate (employee_id, validfrom, rate, notes, login)
                           VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (employee_id, validfrom) DO UPDATE SET rate = excluded.rate,
                               notes = excluded.notes, login = excluded.login, updated = now()
                           RETURNING id""", (employee_id, validfrom, rate, notes or None, login or self.config.login))
            return cur.fetchone()["id"]

    def delete(self, rate_id: int) -> None:
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("DELETE FROM x_employee_rate WHERE id = %s RETURNING id", (rate_id,))
            if not cur.fetchone():
                raise NotFoundError(f"Tarief {rate_id} niet gevonden.")


@dataclass
class HoursSearch:
    """Filters van het rapport Uren per project (IN5)."""
    kind: str = ""                       # '' = alle, customer, internal, none (niet ingedeeld)
    project_id: Optional[int] = None
    department_id: Optional[int] = None  # kostenplaats (via het project)
    customer_id: Optional[int] = None
    employee_id: Optional[int] = None
    datefrom: Optional[date] = None
    dateto: Optional[date] = None
    per_employee: bool = False           # uitsplitsen per medewerker


@dataclass
class HoursRow:
    project_id: int
    projectnumber: str
    description: str
    kind: str                            # customer | internal | none
    customer: str
    department: str
    employee_id: Optional[int] = None
    employee: str = ""
    hours: Decimal = Decimal(0)
    cost: Decimal = Decimal(0)           # kostwaarde = uren × intern tarief
    sales: Optional[Decimal] = None      # verkoopwaarde = uren × prijs van de tijdkaart (niet bij intern)
    ordered: Optional[Decimal] = None    # uren al op een verkooporder (allocated)
    open: Optional[Decimal] = None       # uren nog niet op een order
    margin: Optional[Decimal] = None     # verkoopwaarde − kostwaarde (niet bij intern)

    @property
    def rate(self) -> Optional[Decimal]:
        return self.cost / self.hours if self.hours else None


def hours_by_project(conn: psycopg.Connection, s: Optional[HoursSearch] = None) -> Dict[str, Any]:
    """Rapport Uren per project (IN5): per project (eventueel per medewerker) uren, kostwaarde (vastgelegd intern
    tarief; zonder vastgelegd tarief berekend met ``internal_rate`` op de datum van de uren), verkoopwaarde, uren op een
    order en nog niet, marge. Geeft {"rows": [HoursRow], "computed": aantal berekende tarieven, "precision": n}."""
    s = s or HoursSearch()
    _ensure(conn)
    where, params = ["pr.parts_id IS NULL"], []
    for col, value in (("j.project_id", s.project_id), ("xp.department_id", s.department_id),
                       ("pr.customer_id", s.customer_id), ("j.employee_id", s.employee_id)):
        if value:
            where.append(f"{col} = %s")
            params.append(value)
    if s.kind in ("customer", "internal", "none"):
        where.append(f"({_KIND_SQL}) = %s")
        params.append(s.kind)
    if s.datefrom:
        where.append("j.checkedin >= %s")
        params.append(s.datefrom)
    if s.dateto:
        where.append("j.checkedin < %s")
        params.append(s.dateto + timedelta(days=1))
    rows: Dict[Tuple[int, Optional[int]], HoursRow] = {}
    computed = 0
    cache: Dict[Tuple, Decimal] = {}
    with _db.cursor(conn) as cur:
        precision = _db.get_precision(cur)
        cur.execute(f"""SELECT j.id, j.qty, j.allocated, j.sellprice, j.employee_id, j.project_id, j.parts_id,
                               j.checkedin::date AS day, pr.projectnumber, pr.description, {_KIND_SQL} AS kind,
                               c.name AS customer, d.description AS department, e.name AS employee, x.costprice
                        FROM jcitems j
                        JOIN project pr ON (pr.id = j.project_id)
                        LEFT JOIN x_project xp ON (xp.project_id = pr.id)
                        LEFT JOIN customer c ON (c.id = pr.customer_id)
                        LEFT JOIN department d ON (d.id = xp.department_id)
                        LEFT JOIN employee e ON (e.id = j.employee_id)
                        LEFT JOIN x_jcitem_cost x ON (x.jcitem_id = j.id)
                        WHERE {' AND '.join(where)}
                        ORDER BY pr.projectnumber, pr.id, e.name, j.employee_id, j.id""", params)
        data = cur.fetchall()
        for r in data:
            qty, allocated = to_decimal(r["qty"]), to_decimal(r["allocated"])
            if r["costprice"] is not None:
                rate = to_decimal(r["costprice"])
            else:
                key = (r["employee_id"], r["project_id"], r["parts_id"], r["day"])
                if key not in cache:
                    cache[key] = internal_rate(cur, *key)[0] if r["day"] else Decimal(0)
                rate = cache[key]
                computed += 1
            gkey = (r["project_id"], r["employee_id"] if s.per_employee else None)
            row = rows.get(gkey)
            if row is None:
                internal = r["kind"] == "internal"
                row = rows[gkey] = HoursRow(
                    project_id=r["project_id"], projectnumber=r["projectnumber"] or "",
                    description=r["description"] or "", kind=r["kind"], customer=r["customer"] or "",
                    department=r["department"] or "", employee_id=gkey[1],
                    employee=(r["employee"] or "") if s.per_employee else "",
                    sales=None if internal else Decimal(0), ordered=None if internal else Decimal(0),
                    open=None if internal else Decimal(0))
            row.hours += qty
            row.cost += qty * rate
            if row.sales is not None:
                row.sales += qty * to_decimal(r["sellprice"])
                row.ordered += allocated
                row.open += qty - allocated
    out = list(rows.values())
    for row in out:
        row.cost = _round(row.cost, precision)
        if row.sales is not None:
            row.sales = _round(row.sales, precision)
            row.margin = row.sales - row.cost
    return {"rows": out, "computed": computed, "precision": precision}
