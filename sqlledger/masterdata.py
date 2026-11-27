"""Stamgegevens (fase 2, beslispunten A5 en A6): valuta en koersen, afdelingen,
projecten, talen, branches en betaalmethoden.

Perl-origineel: ``SL/AM.pm`` (currencies, save_currency, delete_currency, move,
get_exchangerates, save_exchangerate, departments/save_department/delete_department,
business, paymentmethod, language) en ``SL/PE.pm`` (projects, save_project,
delete_project).

Verschillen met Perl (README 74–83): verwijderen alleen als iets nergens gebruikt
wordt (op de server gecontroleerd), de standaardvaluta verandert nooit ongemerkt
door de volgorde te wijzigen en kan alleen expliciet gekozen worden zolang er niets
geboekt is, en alle SQL is geparametriseerd.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Tuple

import psycopg

from . import _db, numbering, support
from .admin import _collapse, usage
from .amounts import to_decimal
from .config import UserConfig
from .errors import NotFoundError, ValidationError

#: waar een valuta gebruikt kan worden (acc_trans hangt aan ar/ap/gl)
CURRENCY_REFERENCES = (("ar", "curr"), ("ap", "curr"), ("gl", "curr"), ("oe", "curr"),
                       ("customer", "curr"), ("vendor", "curr"), ("exchangerate", "curr"),
                       ("partscustomer", "curr"), ("partsvendor", "curr"))
DEPARTMENT_REFERENCES = (("ar", "department_id"), ("ap", "department_id"), ("gl", "department_id"),
                         ("oe", "department_id"), ("dpt_trans", "department_id"))
PROJECT_REFERENCES = (("acc_trans", "project_id"), ("invoice", "project_id"), ("orderitems", "project_id"),
                      ("jcitems", "project_id"), ("parts", "project_id"))
LANGUAGE_REFERENCES = (("customer", "language_code"), ("vendor", "language_code"), ("ar", "language_code"),
                       ("ap", "language_code"), ("oe", "language_code"), ("translation", "language_code"))
BUSINESS_REFERENCES = (("customer", "business_id"), ("vendor", "business_id"))
PAYMENTMETHOD_REFERENCES = (("ar", "paymentmethod_id"), ("ap", "paymentmethod_id"),
                            ("customer", "paymentmethod_id"), ("vendor", "paymentmethod_id"),
                            ("employee", "paymentmethod_id"), ("payment", "paymentmethod_id"))
#: als hier iets in staat, kan de standaardvaluta niet meer gewisseld worden
BOOKING_TABLES = ("acc_trans", "ar", "ap", "gl", "oe")


def _in_use(used: Dict[str, int]) -> str:
    return ", ".join(f"{t} ({n})" for t, n in sorted(used.items()))


#: soort project (UREN_ANALYSE.md IN1, ``x_project``); zonder rij: met klant = klantproject, anders niet ingedeeld
PROJECT_KINDS = ("customer", "internal")


@dataclass
class Project:
    projectnumber: str = ""
    description: str = ""
    startdate: Optional[date] = None
    enddate: Optional[date] = None
    customer_id: Optional[int] = None
    id: Optional[int] = None
    # v1.1 interne uren (IN1/IN2): None = soort ongewijzigd laten (import, Perl-vergelijking)
    kind: Optional[str] = None
    department_id: Optional[int] = None         # kostenplaats van een intern project
    new_department: str = ""                    # nieuwe kostenplaats, in dezelfde transactie aangemaakt


def _ensure(conn: psycopg.Connection) -> None:
    from .extensions import ensure_schema
    ensure_schema(conn)


#: soort van een project in SQL (``pr`` = project, ``xp`` = x_project): customer, internal of none (niet ingedeeld)
KIND_SQL = ("CASE WHEN xp.kind IS NOT NULL THEN xp.kind WHEN pr.customer_id IS NOT NULL THEN 'customer' "
            "ELSE 'none' END")


class MasterData:
    """Valuta, koersen en eenvoudige stamtabellen van één administratie."""

    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        self.conn = conn
        self.config = config

    def _audit(self, cur, trans_id, table, reference, action, formname=""):
        support.audittrail(cur, self.config, trans_id=trans_id or 0, tablename=table,
                           reference=reference, formname=formname, action=action)

    # ================================================================ valuta (A5)

    def currencies(self) -> List[Dict[str, Any]]:
        """Valuta in volgorde (rn); de eerste is de standaardvaluta."""
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT rn, curr, prec FROM curr ORDER BY rn, curr")
            rows = cur.fetchall()
            for i, r in enumerate(rows):
                r["curr"] = (r["curr"] or "").strip()
                r["default"] = i == 0
                r["used"] = usage(cur, CURRENCY_REFERENCES, r["curr"])
        return rows

    def add_currency(self, curr: str, prec: Any = 2) -> None:
        curr = (curr or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", curr):
            raise ValidationError("Een valutacode bestaat uit drie letters (ISO 4217, bv. EUR).")
        prec = self._prec(prec)
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("LOCK TABLE curr IN SHARE ROW EXCLUSIVE MODE")
            cur.execute("SELECT 1 FROM curr WHERE curr = %s", (curr,))
            if cur.fetchone():
                raise ValidationError(f"Valuta {curr} bestaat al.")
            cur.execute("SELECT COALESCE(MAX(rn), 0) + 1 AS rn FROM curr")
            rn = cur.fetchone()["rn"]
            cur.execute("INSERT INTO curr (rn, curr, prec) VALUES (%s, %s, %s)", (rn, curr, prec))
            self._audit(cur, 1, "curr", curr, "saved")

    @staticmethod
    def _prec(prec: Any) -> int:
        text = str(prec if prec is not None else "").strip()
        if not text.isdigit() or int(text) > 10:
            raise ValidationError("Precisie: een heel getal van 0 tot en met 10.")
        return int(text)

    def set_precision(self, curr: str, prec: Any) -> None:
        prec = self._prec(prec)
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("UPDATE curr SET prec = %s WHERE curr = %s", (prec, curr))
            if not cur.rowcount:
                raise NotFoundError(f"Valuta {curr} niet gevonden.")
            self._audit(cur, 1, "curr", curr, "saved")

    def move_currency(self, curr: str, direction: str) -> None:
        """Volgorde wijzigen zonder dat de standaardvaluta (eerste) verandert (README 74)."""
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("LOCK TABLE curr IN SHARE ROW EXCLUSIVE MODE")
            cur.execute("SELECT curr, rn FROM curr ORDER BY rn, curr")
            order = [(r["curr"].strip(), r["rn"]) for r in cur.fetchall()]
            names = [c for c, _ in order]
            if curr not in names:
                raise NotFoundError(f"Valuta {curr} niet gevonden.")
            i = names.index(curr)
            j = i - 1 if direction == "up" else i + 1
            if i == 0 or j <= 0 or j >= len(names):
                raise ValidationError("De standaardvaluta blijft bovenaan; kies 'Standaardvaluta maken' "
                                      "om hem te wijzigen.")
            names[i], names[j] = names[j], names[i]
            self._renumber(cur, names)

    @staticmethod
    def _renumber(cur, names: List[str]) -> None:
        for rn, c in enumerate(names, 1):
            cur.execute("UPDATE curr SET rn = %s WHERE curr = %s AND rn IS DISTINCT FROM %s", (rn, c, rn))

    def default_change_check(self, curr: str) -> Tuple[List[str], Dict[str, int]]:
        """(bezwaren, relaties die meegaan) voor 'curr wordt standaardvaluta'.

        Regel (A5, README 75): de standaardvaluta kan alleen gewisseld worden
        zolang er niets geboekt is (geen journaalregels, verkoop/inkoop, memoriaal
        of orders) en er geen koersen zijn — bedragen en koersen staan in de
        standaardvaluta en zouden anders fout worden. Relaties en artikelprijzen
        in de oude standaardvaluta gaan mee naar de nieuwe.
        """
        with _db.cursor(self.conn) as cur:
            old = _db.default_currency(cur)
            objections = []
            cur.execute("SELECT 1 FROM curr WHERE curr = %s", (curr,))
            if not cur.fetchone():
                objections.append(f"Valuta {curr} bestaat niet.")
            if curr == old:
                objections.append(f"{curr} is al de standaardvaluta.")
            for table in BOOKING_TABLES:
                if _db.rows(cur, f"SELECT 1 FROM {table} LIMIT 1"):
                    objections.append("Er is al geboekt in deze administratie; de standaardvaluta kan dan "
                                      "niet meer gewisseld worden (bedragen staan in de standaardvaluta).")
                    break
            if _db.rows(cur, "SELECT 1 FROM exchangerate LIMIT 1"):
                objections.append("Er zijn koersen ingevoerd; die zijn ten opzichte van de huidige "
                                  "standaardvaluta. Verwijder ze eerst.")
            moving = {}
            if old:
                for table in ("customer", "vendor", "partscustomer", "partsvendor"):
                    n = usage(cur, ((table, "curr"),), old).get(table)
                    if n:
                        moving[table] = n
        return objections, moving

    def set_default_currency(self, curr: str) -> None:
        """Maak ``curr`` de standaardvaluta (eerste in de lijst); zie default_change_check."""
        curr = (curr or "").strip().upper()
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("LOCK TABLE curr IN SHARE ROW EXCLUSIVE MODE")
            objections, _ = self.default_change_check(curr)
            if objections:
                raise ValidationError(" ".join(objections))
            old = _db.default_currency(cur)
            cur.execute("SELECT curr FROM curr ORDER BY rn, curr")
            names = [r["curr"].strip() for r in cur.fetchall()]
            names.remove(curr)
            self._renumber(cur, [curr] + names)
            for table in ("customer", "vendor", "partscustomer", "partsvendor"):
                if _db.rows(cur, "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                                 "AND table_name = %s AND column_name = 'curr'", (table,)):
                    cur.execute(f"UPDATE {table} SET curr = %s WHERE curr = %s", (curr, old))
            self._audit(cur, 1, "curr", curr, "default")

    def delete_currency(self, curr: str) -> None:
        """Valuta verwijderen: nooit de standaardvaluta, alleen als nergens gebruikt (README 76)."""
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("LOCK TABLE curr IN SHARE ROW EXCLUSIVE MODE")
            cur.execute("SELECT curr FROM curr ORDER BY rn, curr")
            names = [r["curr"].strip() for r in cur.fetchall()]
            if curr not in names:
                raise NotFoundError(f"Valuta {curr} niet gevonden.")
            if names[0] == curr:
                raise ValidationError("De standaardvaluta kan niet verwijderd worden.")
            used = usage(cur, CURRENCY_REFERENCES, curr)
            if used:
                raise ValidationError(f"Valuta {curr} is in gebruik: {_in_use(used)}.")
            cur.execute("DELETE FROM curr WHERE curr = %s", (curr,))
            names.remove(curr)
            self._renumber(cur, names)
            self._audit(cur, 1, "curr", curr, "deleted")

    # ================================================================ koersen (A5)

    def exchangerates(self, curr: Optional[str] = None, datefrom: Optional[date] = None,
                      dateto: Optional[date] = None) -> List[Dict[str, Any]]:
        """Koersen (AM::get_exchangerates), per datum oplopend; rate als Decimal."""
        where, params = ["TRUE"], []
        if curr:
            where.append("curr = %s")
            params.append(curr)
        if datefrom:
            where.append("transdate >= %s")
            params.append(datefrom)
        if dateto:
            where.append("transdate <= %s")
            params.append(dateto)
        with _db.cursor(self.conn) as cur:
            cur.execute(f"SELECT curr, transdate, exchangerate FROM exchangerate WHERE {' AND '.join(where)} "
                        "ORDER BY transdate, curr", params)
            rows = cur.fetchall()
        for r in rows:
            r["curr"] = (r["curr"] or "").strip()
            r["exchangerate"] = to_decimal(r["exchangerate"]) if r["exchangerate"] is not None else None
        return rows

    def rates_on(self, transdate: date) -> Dict[str, Optional[Decimal]]:
        """Koers per vreemde valuta op één datum (leeg als er geen is)."""
        with _db.cursor(self.conn) as cur:
            foreign = [c for c in _db.currencies(cur)[1:]]
        out: Dict[str, Optional[Decimal]] = {c: None for c in foreign}
        for r in self.exchangerates(datefrom=transdate, dateto=transdate):
            out[r["curr"]] = r["exchangerate"]
        return out

    def save_exchangerates(self, transdate: date, rates: Mapping[str, Any],
                           olddate: Optional[date] = None) -> None:
        """Koersen op één datum opslaan (AM::save_exchangerate).

        Leeg = koers op die datum weg. Een gewijzigde datum verplaatst de koersen
        (Perl liet de oude datum staan; README 77).
        """
        if not transdate:
            raise ValidationError("Datum ontbreekt.")
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            names = _db.currencies(cur)
            default = names[0] if names else None
            for curr, rate in rates.items():
                if curr not in names or curr == default:
                    raise ValidationError(f"{curr} is geen vreemde valuta van deze administratie.")
                value = to_decimal(rate) if rate not in (None, "") else Decimal(0)
                if value < 0:
                    raise ValidationError("Een koers kan niet negatief zijn.")
                if olddate and olddate != transdate:
                    cur.execute("DELETE FROM exchangerate WHERE curr = %s AND transdate = %s", (curr, olddate))
                if value:
                    cur.execute("SELECT exchangerate FROM exchangerate WHERE curr = %s AND transdate = %s",
                                (curr, transdate))
                    existing = [to_decimal(r["exchangerate"]) for r in cur.fetchall()]
                    if existing != [value]:
                        if len(existing) > 1:
                            cur.execute("DELETE FROM exchangerate WHERE curr = %s AND transdate = %s",
                                        (curr, transdate))
                        support.update_exchangerate(cur, curr, transdate, value, default)
                else:
                    cur.execute("DELETE FROM exchangerate WHERE curr = %s AND transdate = %s", (curr, transdate))
                self._audit(cur, 1, "exchangerate", transdate.isoformat(), "saved", formname=curr)

    def delete_exchangerate(self, curr: str, transdate: date) -> None:
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("DELETE FROM exchangerate WHERE curr = %s AND transdate = %s", (curr, transdate))
            if not cur.rowcount:
                raise NotFoundError("Koers niet gevonden.")
            self._audit(cur, 1, "exchangerate", transdate.isoformat(), "deleted", formname=curr)

    # ================================================================ volgorde (rn)

    def _move(self, table: str, row_id: int, direction: str) -> None:
        """Een rij een plek omhoog/omlaag (AM::move), zonder rondgaan aan het eind."""
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute(f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE")
            cur.execute(f"SELECT id FROM {table} ORDER BY rn NULLS LAST, id")
            ids = [r["id"] for r in cur.fetchall()]
            if row_id not in ids:
                raise NotFoundError("Niet gevonden.")
            i = ids.index(row_id)
            j = i - 1 if direction == "up" else i + 1
            if 0 <= j < len(ids):
                ids[i], ids[j] = ids[j], ids[i]
            for rn, x in enumerate(ids, 1):
                cur.execute(f"UPDATE {table} SET rn = %s WHERE id = %s AND rn IS DISTINCT FROM %s", (rn, x, rn))

    @staticmethod
    def _close_gap(cur, table: str, row_id: int) -> None:
        """AM::reorder_rn: volgende rijen een plek op laten schuiven."""
        cur.execute(f"SELECT rn FROM {table} WHERE id = %s", (row_id,))
        r = cur.fetchone()
        if r and r["rn"] is not None:
            cur.execute(f"UPDATE {table} SET rn = rn - 1 WHERE rn > %s", (r["rn"],))

    @staticmethod
    def _next_rn(cur, table: str) -> int:
        cur.execute(f"SELECT COALESCE(MAX(rn), 0) + 1 AS rn FROM {table}")
        return cur.fetchone()["rn"]

    # ================================================================ afdelingen (A6)

    def departments(self) -> List[Dict[str, Any]]:
        _ensure(self.conn)
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT id, description, role, rn FROM department ORDER BY rn NULLS LAST, id")
            rows = cur.fetchall()
            for r in rows:
                r["role"] = (r["role"] or "P").strip()
                r["used"] = usage(cur, DEPARTMENT_REFERENCES, r["id"])
                n = self._cost_centre_projects(cur, r["id"])
                if n:
                    r["used"]["project"] = n          # kostenplaats van interne projecten (IN2)
        return rows

    @staticmethod
    def _cost_centre_projects(cur, dep_id: int) -> int:
        cur.execute("SELECT count(*) AS n FROM x_project WHERE department_id = %s", (dep_id,))
        return int(cur.fetchone()["n"])

    def cost_centres(self) -> List[Dict[str, Any]]:
        """Afdelingen van het soort kostenplaats (``role`` 'C'), voor een intern project (IN2)."""
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT id, description FROM department WHERE role = 'C' ORDER BY rn NULLS LAST, id")
            return cur.fetchall()

    def get_department(self, dep_id: int) -> Dict[str, Any]:
        for r in self.departments():
            if r["id"] == dep_id:
                return r
        raise NotFoundError("Afdeling niet gevonden.")

    def save_department(self, description: str, role: str = "P", dep_id: Optional[int] = None) -> int:
        """Afdeling (kostenplaats C / winstplaats P) opslaan (AM::save_department)."""
        description = re.sub(r" ( )+", " ", re.sub(r"-(-)+", "-", description or ""))
        if not description.strip():
            raise ValidationError("Omschrijving ontbreekt.")
        if role not in ("C", "P"):
            raise ValidationError("Kies kostenplaats of winstplaats.")
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            if dep_id:
                cur.execute("UPDATE department SET description = %s, role = %s WHERE id = %s",
                            (description, role, dep_id))
                if not cur.rowcount:
                    raise NotFoundError("Afdeling niet gevonden.")
                new_id = dep_id
            else:
                cur.execute("LOCK TABLE department IN SHARE ROW EXCLUSIVE MODE")
                cur.execute("INSERT INTO department (description, role, rn) VALUES (%s, %s, %s) RETURNING id",
                            (description, role, self._next_rn(cur, "department")))
                new_id = cur.fetchone()["id"]
            self._audit(cur, new_id, "department", description, "saved")   # ook nieuw (README 243)
        return new_id

    def delete_department(self, dep_id: int) -> None:
        """Alleen als de afdeling nergens gebruikt wordt (README 78; Perl keek alleen naar dpt_trans)."""
        _ensure(self.conn)
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("SELECT description FROM department WHERE id = %s FOR UPDATE", (dep_id,))
            row = cur.fetchone()
            if not row:
                raise NotFoundError("Afdeling niet gevonden.")
            used = usage(cur, DEPARTMENT_REFERENCES, dep_id)
            n = self._cost_centre_projects(cur, dep_id)
            if n:
                used["project"] = n
            if used:
                raise ValidationError(f"Deze afdeling is in gebruik: {_in_use(used)}.")
            self._close_gap(cur, "department", dep_id)
            cur.execute("DELETE FROM department WHERE id = %s", (dep_id,))
            self._audit(cur, dep_id, "department", row["description"], "deleted")

    def move_department(self, dep_id: int, direction: str) -> None:
        self._move("department", dep_id, direction)

    # ================================================================ projecten (A6)

    def projects(self, status: str = "all") -> List[Dict[str, Any]]:
        """Projecten zonder artikel (PE::projects; JC-/productieprojecten vallen erbuiten).

        status: all, active (geen of toekomstige einddatum), inactive, orphaned (ongebruikt).
        """
        where = ["(pr.parts_id = 0 OR pr.parts_id IS NULL)"]
        if status == "active":
            where.append("(pr.enddate IS NULL OR pr.enddate >= current_date)")
        elif status == "inactive":
            where.append("pr.enddate < current_date")
        _ensure(self.conn)
        with _db.cursor(self.conn) as cur:
            cur.execute(f"""SELECT pr.id, pr.projectnumber, pr.description, pr.startdate, pr.enddate,
                                   pr.customer_id, c.name AS customer, {KIND_SQL} AS kind,
                                   xp.department_id, d.description AS department
                            FROM project pr LEFT JOIN customer c ON c.id = pr.customer_id
                            LEFT JOIN x_project xp ON xp.project_id = pr.id
                            LEFT JOIN department d ON d.id = xp.department_id
                            WHERE {' AND '.join(where)} ORDER BY pr.projectnumber""")
            rows = cur.fetchall()
            for r in rows:
                r["used"] = usage(cur, PROJECT_REFERENCES, r["id"])
        if status == "orphaned":
            rows = [r for r in rows if not r["used"]]
        return rows

    def get_project(self, project_id: int) -> Dict[str, Any]:
        _ensure(self.conn)
        with _db.cursor(self.conn) as cur:
            cur.execute(f"""SELECT pr.*, c.name AS customer, {KIND_SQL} AS kind, xp.department_id,
                                   d.description AS department
                            FROM project pr LEFT JOIN customer c ON c.id = pr.customer_id
                            LEFT JOIN x_project xp ON xp.project_id = pr.id
                            LEFT JOIN department d ON d.id = xp.department_id
                            WHERE pr.id = %s""", (project_id,))
            row = cur.fetchone()
            if not row:
                raise NotFoundError("Project niet gevonden.")
            row["used"] = usage(cur, PROJECT_REFERENCES, project_id)
        return row

    def customers(self) -> List[Dict[str, Any]]:
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT id, name, customernumber FROM customer ORDER BY name, id")
            return cur.fetchall()

    def save_project(self, p: Project) -> int:
        """Project opslaan (PE::save_project). Leeg nummer = volgend nummer (``projectnumber``).

        Met ``kind`` (v1.1, IN1/IN2) ook de soort in ``x_project``: klantproject = klant verplicht; intern project =
        kostenplaats verplicht (bestaande of ``new_department``) en geen klant."""
        if p.startdate and p.enddate and p.enddate < p.startdate:
            raise ValidationError("De einddatum ligt voor de startdatum.")
        if p.kind is not None:
            self._check_kind(p)
            _ensure(self.conn)
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            old_customer = None
            if p.id:
                cur.execute("SELECT parts_id, customer_id FROM project WHERE id = %s FOR UPDATE", (p.id,))
                row = cur.fetchone()
                if not row:
                    raise NotFoundError("Project niet gevonden.")
                if row["parts_id"]:
                    raise ValidationError("Dit project hoort bij een artikel (productie); dat valt buiten deze versie.")
                old_customer = row["customer_id"]
            if p.customer_id and p.customer_id != old_customer:      # bestaande (ook verweesde) koppeling blijft
                cur.execute("SELECT 1 FROM customer WHERE id = %s", (p.customer_id,))
                if not cur.fetchone():
                    raise ValidationError("Onbekende klant.")
            number = (p.projectnumber or "").strip()
            if not number:
                number = numbering.next_number(cur, "projectnumber", dateformat=self.config.dateformat,
                                               transdate=date.today())
            cur.execute("SELECT 1 FROM project WHERE projectnumber = %s AND id IS DISTINCT FROM %s",
                        (number, p.id))
            if cur.fetchone():
                raise ValidationError(f"Projectnummer {number} bestaat al.")
            values = (number, p.description or None, p.startdate, p.enddate, p.customer_id)
            if p.id:
                cur.execute("""UPDATE project SET projectnumber = %s, description = %s, startdate = %s,
                                      enddate = %s, customer_id = %s WHERE id = %s""", values + (p.id,))
            else:
                cur.execute("""INSERT INTO project (projectnumber, description, startdate, enddate, customer_id)
                               VALUES (%s, %s, %s, %s, %s) RETURNING id""", values)
                p.id = cur.fetchone()["id"]
            p.projectnumber = number
            if p.kind is not None:
                self._save_kind(cur, p)
        return p.id

    @staticmethod
    def _check_kind(p: Project) -> None:
        if p.kind not in PROJECT_KINDS:
            raise ValidationError("Kies het soort project.")
        if p.kind == "customer" and not p.customer_id:
            raise ValidationError("Kies een klant bij een klantproject.")
        if p.kind == "internal":
            if p.customer_id:
                raise ValidationError("Een intern project heeft geen klant (de uren worden niet gefactureerd).")
            if not p.department_id and not (p.new_department or "").strip():
                raise ValidationError("Kies een kostenplaats bij een intern project.")

    def _save_kind(self, cur, p: Project) -> None:
        department_id = None
        if p.kind == "internal":
            if (p.new_department or "").strip():
                department_id = self.save_department(p.new_department.strip(), "C")
            else:
                cur.execute("SELECT role FROM department WHERE id = %s", (p.department_id,))
                d = cur.fetchone()
                if not d or (d["role"] or "P").strip() != "C":
                    raise ValidationError("Kies een afdeling van het soort kostenplaats.")
                department_id = p.department_id
        cur.execute("""INSERT INTO x_project (project_id, kind, department_id, login) VALUES (%s, %s, %s, %s)
                       ON CONFLICT (project_id) DO UPDATE SET kind = excluded.kind,
                           department_id = excluded.department_id, login = excluded.login, updated = now()""",
                    (p.id, p.kind, department_id, self.config.login))
        p.department_id = department_id

    def delete_project(self, project_id: int) -> None:
        """Alleen een ongebruikt project (PE::delete_project; Perl: alleen de knop verborgen)."""
        _ensure(self.conn)
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("SELECT parts_id FROM project WHERE id = %s FOR UPDATE", (project_id,))
            row = cur.fetchone()
            if not row:
                raise NotFoundError("Project niet gevonden.")
            used = usage(cur, PROJECT_REFERENCES, project_id)
            if used or row["parts_id"]:
                raise ValidationError(f"Dit project is in gebruik: {_in_use(used) or 'artikel'}.")
            cur.execute("DELETE FROM project WHERE id = %s", (project_id,))
            cur.execute("DELETE FROM translation WHERE trans_id = %s", (project_id,))
            cur.execute("DELETE FROM x_project WHERE project_id = %s", (project_id,))
            support.delete_references(cur, self.config, project_id)

    # ================================================================ talen (A6)

    def languages(self) -> List[Dict[str, Any]]:
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT code, description FROM language ORDER BY code")
            rows = cur.fetchall()
            for r in rows:
                r["used"] = usage(cur, LANGUAGE_REFERENCES, r["code"])
        return rows

    def save_language(self, code: str, description: str, oldcode: Optional[str] = None) -> str:
        """Taal opslaan (AM::save_language).

        Dubbele spaties in de omschrijving worden één spatie (Perl maakte er '-'
        van; README 79). Een code die in gebruik is, kan niet gewijzigd worden
        (Perl wijzigde hem zonder de verwijzingen bij te werken).
        """
        code = re.sub(r"(\.\.|\*| )", "", code or "")
        code = re.sub(r"-(-)+", "-", code)
        description = re.sub(r" ( )+", " ", re.sub(r"-(-)+", "-", description or ""))
        if not code:
            raise ValidationError("Code ontbreekt.")
        if len(code) > 6:
            raise ValidationError("Een taalcode is hoogstens 6 tekens.")
        if not description.strip():
            raise ValidationError("Omschrijving ontbreekt.")
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            if oldcode:
                cur.execute("SELECT 1 FROM language WHERE code = %s FOR UPDATE", (oldcode,))
                if not cur.fetchone():
                    raise NotFoundError("Taal niet gevonden.")
                if code != oldcode:
                    used = usage(cur, LANGUAGE_REFERENCES, oldcode)
                    if used:
                        raise ValidationError(f"Taalcode {oldcode} is in gebruik ({_in_use(used)}) en kan "
                                              "niet gewijzigd worden.")
            if code != oldcode:
                cur.execute("SELECT 1 FROM language WHERE code = %s", (code,))
                if cur.fetchone():
                    raise ValidationError(f"Taal {code} bestaat al.")
            if oldcode:
                cur.execute("UPDATE language SET code = %s, description = %s WHERE code = %s",
                            (code, description, oldcode))
            else:
                cur.execute("INSERT INTO language (code, description) VALUES (%s, %s)", (code, description))
            self._audit(cur, 1, "language", code, "saved")
        return code

    def delete_language(self, code: str) -> None:
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("SELECT 1 FROM language WHERE code = %s FOR UPDATE", (code,))
            if not cur.fetchone():
                raise NotFoundError("Taal niet gevonden.")
            used = usage(cur, LANGUAGE_REFERENCES, code)
            if used:
                raise ValidationError(f"Taal {code} is in gebruik: {_in_use(used)}.")
            cur.execute("DELETE FROM language WHERE code = %s", (code,))
            self._audit(cur, 1, "language", code, "deleted")

    # ================================================================ branches (A6)

    def business(self) -> List[Dict[str, Any]]:
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT id, description, discount, rn FROM business ORDER BY rn NULLS LAST, id")
            rows = cur.fetchall()
            for r in rows:
                r["discount"] = to_decimal(r["discount"]) * 100 if r["discount"] is not None else None
                r["used"] = usage(cur, BUSINESS_REFERENCES, r["id"])
        return rows

    def save_business(self, description: str, discount: Any = None, business_id: Optional[int] = None) -> int:
        """Branche met korting in procenten (AM::save_business slaat een fractie op).
        ``discount=None``: bij een bestaande branche de korting laten staan (het scherm toont hem niet, VW5)."""
        description = re.sub(r" ( )+", " ", re.sub(r"-(-)+", "-", description or ""))
        if not description.strip():
            raise ValidationError("Omschrijving ontbreekt.")
        pct = to_decimal(discount) if discount not in (None, "") else Decimal(0)
        if pct < 0 or pct > 100:
            raise ValidationError("Korting: een percentage van 0 tot en met 100.")
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            if business_id:
                cur.execute("SELECT discount FROM business WHERE id = %s FOR UPDATE", (business_id,))
                old = cur.fetchone()
                if not old:
                    raise NotFoundError("Branche niet gevonden.")
                same = discount is None or (old["discount"] is not None and to_decimal(old["discount"]) * 100 == pct)
                cur.execute("UPDATE business SET description = %s"
                            + ("" if same else ", discount = %s") + " WHERE id = %s",
                            (description, business_id) if same else (description, pct / 100, business_id))
                new_id = business_id
            else:
                cur.execute("LOCK TABLE business IN SHARE ROW EXCLUSIVE MODE")
                cur.execute("INSERT INTO business (description, discount, rn) VALUES (%s, %s, %s) RETURNING id",
                            (description, pct / 100, self._next_rn(cur, "business")))
                new_id = cur.fetchone()["id"]
            self._audit(cur, new_id, "business", description, "saved")
        return new_id

    def delete_business(self, business_id: int) -> None:
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("SELECT description FROM business WHERE id = %s FOR UPDATE", (business_id,))
            row = cur.fetchone()
            if not row:
                raise NotFoundError("Branche niet gevonden.")
            used = usage(cur, BUSINESS_REFERENCES, business_id)
            if used:
                raise ValidationError(f"Deze branche is in gebruik: {_in_use(used)}.")
            self._close_gap(cur, "business", business_id)
            cur.execute("DELETE FROM business WHERE id = %s", (business_id,))
            self._audit(cur, business_id, "business", row["description"], "deleted")

    def move_business(self, business_id: int, direction: str) -> None:
        self._move("business", business_id, direction)

    # ================================================================ betaalmethoden (A6)

    def paymentmethods(self) -> List[Dict[str, Any]]:
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT id, description, fee, roundchange, rn FROM paymentmethod ORDER BY rn NULLS LAST, id")
            rows = cur.fetchall()
            for r in rows:
                r["fee"] = to_decimal(r["fee"]) if r["fee"] is not None else None
                r["roundchange"] = to_decimal(r["roundchange"]) if r["roundchange"] is not None else None
                r["used"] = usage(cur, PAYMENTMETHOD_REFERENCES, r["id"])
        return rows

    def save_paymentmethod(self, description: str, fee: Any = None, roundchange: Any = None,
                           pm_id: Optional[int] = None) -> int:
        """Betaalmethode (AM::save_paymentmethod): omschrijving, kosten, afronding.
        ``fee=None``: bij een bestaande betaalmethode de kosten laten staan (het scherm toont ze niet, VW6)."""
        description = re.sub(r" ( )+", " ", re.sub(r"-(-)+", "-", description or ""))
        if not description.strip():
            raise ValidationError("Omschrijving ontbreekt.")
        fee_v = to_decimal(fee) if fee not in (None, "") else Decimal(0)
        round_v = to_decimal(roundchange) if roundchange not in (None, "") else Decimal(0)
        if fee_v < 0 or round_v < 0:
            raise ValidationError("Kosten en afronding kunnen niet negatief zijn.")
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            if pm_id:
                cur.execute("UPDATE paymentmethod SET description = %s, roundchange = %s"
                            + ("" if fee is None else ", fee = %s") + " WHERE id = %s",
                            (description, round_v, pm_id) if fee is None else (description, round_v, fee_v, pm_id))
                if not cur.rowcount:
                    raise NotFoundError("Betaalmethode niet gevonden.")
                new_id = pm_id
            else:
                cur.execute("LOCK TABLE paymentmethod IN SHARE ROW EXCLUSIVE MODE")
                cur.execute("""INSERT INTO paymentmethod (rn, description, fee, roundchange)
                               VALUES (%s, %s, %s, %s) RETURNING id""",
                            (self._next_rn(cur, "paymentmethod"), description, fee_v, round_v))
                new_id = cur.fetchone()["id"]
            self._audit(cur, new_id, "paymentmethod", description, "saved")
        return new_id

    def delete_paymentmethod(self, pm_id: int) -> None:
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("SELECT description FROM paymentmethod WHERE id = %s FOR UPDATE", (pm_id,))
            row = cur.fetchone()
            if not row:
                raise NotFoundError("Betaalmethode niet gevonden.")
            used = usage(cur, PAYMENTMETHOD_REFERENCES, pm_id)
            if used:
                raise ValidationError(f"Deze betaalmethode is in gebruik: {_in_use(used)}.")
            self._close_gap(cur, "paymentmethod", pm_id)
            cur.execute("DELETE FROM paymentmethod WHERE id = %s", (pm_id,))
            self._audit(cur, pm_id, "paymentmethod", row["description"], "deleted")

    def move_paymentmethod(self, pm_id: int, direction: str) -> None:
        self._move("paymentmethod", pm_id, direction)
