"""Samengestelde artikelen (stuklijst/BOM): prijzen, controles, assembleren en lijsten.

Port van de assembly-delen van SL/IC.pm en bin/mozilla/ic.pl (fase 3, beslispunten SA1–SA12).
Een samengesteld artikel is een rij in ``parts`` met ``assembly = true`` en alleen een
opbrengstrekening; de stuklijst staat in ``assembly`` (``aid`` = product, ``parts_id`` = onderdeel).
Opslaan van het artikel met zijn stuklijst gebeurt in ``sqlledger.parts``; hier staat de rest.

Verschillen met Perl (zie README "Bewuste afwijkingen"):

* Lijstprijs, kostprijs en gewicht van het product worden altijd uit de onderdelen berekend
  (per regel afgerond op de standaarddecimalen, zoals ic.pl)
  (ook over sub-stuklijsten), de verkoopprijs ook tenzij "verkoopprijs zelf bepalen" aan staat
  (``x_assembly_price``) (SA3). De opgeslagen waarden in ``parts`` worden bij elke wijziging van
  een onderdeel opnieuw berekend; Perl gaf wijzigingen door als verschil en deed dat door een
  omgedraaide voorwaarde (IC.pm 281) nooit.
* Cycli (A in B in A) en meer dan 5 niveaus worden geweigerd, elk onderdeel één keer per
  stuklijst (SA2); Perl liep bij een cyclus oneindig door.
* Assembleren en demonteren worden vastgelegd in ``x_assembly_build`` (+ regels) (SA4/SA5);
  demonteren kan niet verder dan de voorraad van het product. Onvoldoende onderdelen geeft een
  waarschuwing, met "voorraad controleren" op het onderdeel een weigering (SA6); Perl keek
  alleen naar een verborgen veld in het formulier (ic.pl 4494).
* "Hoeveel kan ik maken" rekent per product met de voorraad van de directe onderdelen; Perl
  verdeelde de voorraad over de producten in de lijst en rekende daarbij fout (IC.pm 860).
* Alle SQL geparametriseerd (Perl: SQL-injectie in assembly_item/include_assembly).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import psycopg

from . import _db, support
from .amounts import to_decimal
from .config import UserConfig
from .errors import ClosedPeriodError, NotFoundError, ValidationError
from .extensions import ensure_schema
from .gl import _closedto, _num, like

#: maximaal aantal niveaus in een stuklijst (SA2)
MAX_LEVELS = 5
#: decimalen voor hoeveelheden en berekende prijzen
DECIMALS = 6
_Q = Decimal(1).scaleb(-DECIMALS)
PRICE_FIELDS = ("sellprice", "listprice", "lastcost", "weight")
#: melding bij een datum t/m de afsluitdatum (F41; zelfde tekst als tijdkaarten in de webapp)
CLOSED = "Deze datum valt in een afgesloten periode."


class InsufficientStock(ValidationError):
    """Te weinig voorraad van een of meer onderdelen; ``shortages`` per onderdeel.
    ``blocking``: een van de onderdelen heeft "voorraad controleren" aan (dan kan het niet)."""

    def __init__(self, message: str, shortages: List[Dict[str, Any]], blocking: bool):
        super().__init__(message)
        self.shortages = shortages
        self.blocking = blocking


@dataclass
class Component:
    """Een regel van de stuklijst."""
    parts_id: int
    qty: Decimal
    bom: bool = False               # "op documenten" (SA10; effect in fase 4)
    adj: bool = True                # Perl: wijzigingen doorgeven; bewaard, niet meer gebruikt (SA3)
    # alleen lezen
    partnumber: str = ""
    description: str = ""
    unit: str = ""
    kind: str = ""
    onhand: Decimal = Decimal(0)
    obsolete: bool = False
    sellprice: Decimal = Decimal(0)
    listprice: Decimal = Decimal(0)
    lastcost: Decimal = Decimal(0)
    weight: Decimal = Decimal(0)


def _d(v: Any) -> Decimal:
    return to_decimal(v)


def _has_table(cur, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (name,))
    return bool(cur.fetchone()["ok"])


def stocked(row: Dict[str, Any]) -> bool:
    """Onderdeel met voorraad (artikel, loon/overhead, samengesteld); diensten niet (IC.pm adjust_inventory)."""
    return bool(row.get("inventory_accno_id") or row.get("assembly"))


class Bom:
    """Alle stuklijsten van de administratie in het geheugen (de tabel is klein), met de prijzen
    en voorraad van de betrokken artikelen. ``override`` vervangt de stuklijst van één product
    (voor controle en prijsberekening vóór het opslaan)."""

    def __init__(self, cur, override: Optional[Tuple[int, List[Tuple[int, Decimal]]]] = None,
                 extra_ids: Iterable[int] = ()):
        self.cur = cur
        cur.execute("SELECT aid, parts_id, qty::numeric AS qty FROM assembly WHERE aid IS NOT NULL ORDER BY id")
        self.children: Dict[int, List[Tuple[int, Decimal]]] = {}
        for r in cur.fetchall():
            self.children.setdefault(r["aid"], []).append((r["parts_id"], _d(r["qty"])))
        if override is not None:
            pid, rows = override
            if pid:
                self.children[pid] = list(rows)
            else:
                self.children[0] = list(rows)
        self.parents: Dict[int, Set[int]] = {}
        for aid, rows in self.children.items():
            for cid, _qty in rows:
                self.parents.setdefault(cid, set()).add(aid)
        ids = set(self.children) | {c for rows in self.children.values() for c, _ in rows} | set(extra_ids)
        ids.discard(0)
        cur.execute("""SELECT id, partnumber, description, unit, onhand::numeric AS onhand,
                              sellprice::numeric AS sellprice, listprice::numeric AS listprice,
                              lastcost::numeric AS lastcost, weight::numeric AS weight, assembly, obsolete,
                              checkinventory, inventory_accno_id, income_accno_id, expense_accno_id
                       FROM parts WHERE id = ANY(%s)""", (list(ids),))
        self.parts: Dict[int, Dict[str, Any]] = {r["id"]: r for r in cur.fetchall()}
        self.q = Decimal(1).scaleb(-_db.get_precision(cur))
        self.own: Set[int] = set()
        if _has_table(cur, "x_assembly_price"):
            cur.execute("SELECT parts_id FROM x_assembly_price WHERE own_sellprice")
            self.own = {r["parts_id"] for r in cur.fetchall()}

    def is_assembly(self, pid: int) -> bool:
        return bool(self.parts.get(pid, {}).get("assembly")) or pid == 0

    # ---------------------------------------------------------------- structuur

    def cycle(self, pid: int) -> Optional[List[int]]:
        """Pad van een cyclus die bij ``pid`` terugkomt, of None."""
        def walk(node: int, path: List[int]) -> Optional[List[int]]:
            for cid, _ in self.children.get(node, []):
                if cid == pid:
                    return path + [cid]
                if cid in path:
                    continue        # cyclus elders: die wordt bij dat product gemeld
                found = walk(cid, path + [cid])
                if found:
                    return found
            return None
        return walk(pid, [pid])

    def height(self, pid: int, _seen: Tuple[int, ...] = ()) -> int:
        """Aantal niveaus onder het product (alleen onderdelen = 1)."""
        rows = self.children.get(pid, [])
        if not rows:
            return 0
        if pid in _seen:
            raise ValidationError("De stuklijst bevat een cyclus.")
        return 1 + max(self.height(c, _seen + (pid,)) for c, _ in rows)

    def depth_above(self, pid: int, _seen: Tuple[int, ...] = ()) -> int:
        """Aantal niveaus boven het product (niet gebruikt in een ander product = 0)."""
        parents = self.parents.get(pid, set())
        if not parents:
            return 0
        if pid in _seen:
            raise ValidationError("De stuklijst bevat een cyclus.")
        return 1 + max(self.depth_above(a, _seen + (pid,)) for a in parents)

    def ancestors(self, pid: int) -> List[int]:
        """Alle producten waarin ``pid`` (direct of indirect) zit, dichtstbijzijnde eerst."""
        out: List[int] = []
        todo = sorted(self.parents.get(pid, ()))
        while todo:
            a = todo.pop(0)
            if a in out or a == pid:
                continue
            out.append(a)
            todo.extend(sorted(self.parents.get(a, ())))
        return out

    # ---------------------------------------------------------------- prijzen (SA3)

    def line(self, qty: Decimal, unit: Decimal, key: str) -> Decimal:
        """Regelbedrag van een onderdeel: prijzen per regel afgerond op de standaarddecimalen
        (ic.pl assembly_row: round_amount per regel), gewicht niet."""
        value = qty * unit
        return value if key == "weight" else value.quantize(self.q, ROUND_HALF_UP)

    def prices(self, pid: int, _path: Tuple[int, ...] = ()) -> Dict[str, Decimal]:
        """Berekende verkoop-, lijst-, kostprijs en gewicht van een product: som van de regels
        (``line``); een sub-product telt mee met zijn eigen berekende prijzen."""
        if pid in _path:
            raise ValidationError("De stuklijst bevat een cyclus.")
        total = {k: Decimal(0) for k in PRICE_FIELDS}
        for cid, qty in self.children.get(pid, []):
            c = self.parts.get(cid)
            if c is None:
                continue
            if c["assembly"]:
                sub = self.prices(cid, _path + (pid,))
                if cid in self.own:
                    sub["sellprice"] = _d(c["sellprice"])
            else:
                sub = {k: _d(c[k]) for k in PRICE_FIELDS}
            for k in PRICE_FIELDS:
                total[k] += self.line(qty, sub[k], k)
        return total

    def stored_prices(self, pid: int) -> Dict[str, Decimal]:
        """Prijzen zoals ze in ``parts`` horen te staan (afgerond op 6 decimalen); verkoopprijs
        ongemoeid als het product een eigen verkoopprijs heeft."""
        out = {k: v.quantize(_Q, ROUND_HALF_UP) for k, v in self.prices(pid).items()}
        if pid in self.own:
            out["sellprice"] = _d(self.parts[pid]["sellprice"])
        return out

    # ---------------------------------------------------------------- uitklappen (SA9)

    def explode(self, pid: int, qty: Decimal = Decimal(1)) -> List[Dict[str, Any]]:
        """Stuklijst over alle niveaus: niveau, aantal per stuk (op dat niveau) en totaal."""
        out: List[Dict[str, Any]] = []

        def walk(node: int, mult: Decimal, level: int, path: Tuple[int, ...]) -> None:
            for cid, per in self.children.get(node, []):
                c = self.parts.get(cid)
                if c is None or cid in path or cid == node:
                    continue
                total = mult * per
                unit = self.prices(cid, path + (node,)) if c["assembly"] else {k: _d(c[k]) for k in PRICE_FIELDS}
                if c["assembly"] and cid in self.own:
                    unit["sellprice"] = _d(c["sellprice"])
                out.append({"level": level, "id": cid, "parent_id": node, "partnumber": c["partnumber"],
                            "description": c["description"], "unit": c["unit"], "assembly": bool(c["assembly"]),
                            "stocked": stocked(c), "perassembly": per, "qty": total,
                            "lastcost": unit["lastcost"], "linecost": total * unit["lastcost"],
                            "sellprice": unit["sellprice"], "onhand": _d(c["onhand"]),
                            "obsolete": bool(c["obsolete"])})
                if c["assembly"]:
                    walk(cid, total, level + 1, path + (node,))
        walk(pid, qty, 1, ())
        return out


def check_components(cur, pid: Optional[int], rows: List[Tuple[int, Decimal]],
                     old_ids: Iterable[int] = ()) -> Bom:
    """Controleer een nieuwe stuklijst voor product ``pid`` (None = nieuw product) (SA2).
    ``old_ids``: onderdelen die er al in stonden (die mogen vervallen zijn)."""
    if not rows:
        raise ValidationError("Voeg minstens één onderdeel toe.")
    seen: Set[int] = set()
    for cid, qty in rows:
        if cid in seen:
            raise ValidationError("Een onderdeel staat twee keer in de stuklijst; tel de aantallen op.")
        seen.add(cid)
        if qty <= 0:
            raise ValidationError("Het aantal van een onderdeel moet groter dan 0 zijn.")
        if qty != qty.quantize(_Q):
            raise ValidationError(f"Aantal heeft meer dan {DECIMALS} decimalen.")
        if pid and cid == pid:
            raise ValidationError("Een artikel kan geen onderdeel van zichzelf zijn.")
    bom = Bom(cur, override=(pid or 0, rows))
    old = set(old_ids)
    for cid, _qty in rows:
        c = bom.parts.get(cid)
        if c is None:
            raise NotFoundError(f"Artikel {cid} niet gevonden.")
        if not (c["inventory_accno_id"] or c["income_accno_id"] or c["expense_accno_id"]):
            raise ValidationError(f"Artikel {c['partnumber']} is een kit en kan geen onderdeel zijn.")
        if c["obsolete"] and cid not in old:
            raise ValidationError(f"Artikel {c['partnumber']} is vervallen.")
    key = pid or 0
    path = bom.cycle(key)
    if path:
        names = " → ".join(bom.parts[i]["partnumber"] or str(i) for i in path if i in bom.parts)
        raise ValidationError(f"De stuklijst maakt een kring: {names}.")
    if bom.depth_above(key) + bom.height(key) > MAX_LEVELS:
        raise ValidationError(f"Een stuklijst kan hoogstens {MAX_LEVELS} niveaus diep zijn.")
    return bom


def preview(cur, pid: Optional[int], rows: List[Tuple[int, Decimal]]) -> Dict[str, Any]:
    """Prijzen van een (nog niet opgeslagen) stuklijst: per onderdeel de stukprijzen en het
    totaal, zonder controles (voor het scherm)."""
    key = pid or 0
    bom = Bom(cur, override=(key, rows), extra_ids=[c for c, _ in rows])
    units: Dict[int, Dict[str, Any]] = {}
    for cid, _qty in rows:
        c = bom.parts.get(cid)
        if c is None:
            continue
        try:
            u = bom.prices(cid) if c["assembly"] else {k: _d(c[k]) for k in PRICE_FIELDS}
        except ValidationError:
            u = {k: Decimal(0) for k in PRICE_FIELDS}
        if c["assembly"] and cid in bom.own:
            u["sellprice"] = _d(c["sellprice"])
        units[cid] = {**u, "lines": {k: bom.line(dict(rows)[cid], u[k], k) for k in PRICE_FIELDS},
                      "partnumber": c["partnumber"], "description": c["description"], "unit": c["unit"],
                      "onhand": _d(c["onhand"]), "assembly": bool(c["assembly"]), "stocked": stocked(c),
                      "obsolete": bool(c["obsolete"])}
    try:
        total = bom.prices(key)
    except ValidationError:
        total = {k: Decimal(0) for k in PRICE_FIELDS}
    return {"units": units, "total": total}


def refresh_ancestors(cur, pid: int) -> List[int]:
    """Herbereken de opgeslagen prijzen van alle producten waar ``pid`` in zit (SA3). Geeft de
    gewijzigde product-id's."""
    bom = Bom(cur)
    changed = []
    for aid in bom.ancestors(pid):
        row = bom.parts.get(aid)
        if not row or not row["assembly"]:
            continue
        new = bom.stored_prices(aid)
        diff = {k: v for k, v in new.items() if _d(row[k]) != v}
        if diff:
            cur.execute(f"UPDATE parts SET {', '.join(f'{k} = %s' for k in diff)} WHERE id = %s",
                        [*diff.values(), aid])
            changed.append(aid)
    return changed


class Assemblies:
    """Assembleren/demonteren en de lijsten van samengestelde artikelen."""

    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        self.conn = conn
        self.config = config

    def _product(self, cur, pid: int, lock: bool = False) -> Dict[str, Any]:
        cur.execute(f"""SELECT id, partnumber, description, unit, onhand::numeric AS onhand, assembly, obsolete,
                               project_id
                        FROM parts WHERE id = %s{' FOR UPDATE' if lock else ''}""", (pid,))
        row = cur.fetchone()
        if not row:
            raise NotFoundError(f"Artikel {pid} niet gevonden.")
        if not row["assembly"]:
            raise ValidationError(f"Artikel {row['partnumber']} is geen samengesteld artikel.")
        return row

    # ---------------------------------------------------------------- assembleren (SA4–SA6)

    def requirements(self, pid: int, qty: Any, _cur=None) -> List[Dict[str, Any]]:
        """Per direct onderdeel: aantal per stuk, beweging, voorraad nu en erna, tekort."""
        qty = _d(qty)

        def run(cur):
            self._product(cur, pid)
            bom = Bom(cur)
            out = []
            for cid, per in bom.children.get(pid, []):
                c = bom.parts.get(cid)
                if c is None:
                    continue
                has_stock = stocked(c)
                move = -qty * per if has_stock else Decimal(0)
                onhand = _d(c["onhand"])
                out.append({"id": cid, "partnumber": c["partnumber"], "description": c["description"],
                            "unit": c["unit"], "perassembly": per, "need": qty * per, "qty": move,
                            "stocked": has_stock, "onhand": onhand, "after": onhand + move,
                            "short": has_stock and qty > 0 and onhand + move < 0,
                            "checkinventory": bool(c["checkinventory"])})
            return out
        if _cur is not None:
            return run(_cur)
        with _db.cursor(self.conn) as cur:
            return run(cur)

    def build(self, pid: int, qty: Any, *, transdate: date, reference: str = "", notes: str = "",
              allow_short: bool = False) -> Dict[str, Any]:
        """Assembleren (``qty`` > 0) of demonteren (``qty`` < 0): onderdelen −/+, product +/−.

        Geen grootboekboeking (zoals Perl); vastgelegd in ``x_assembly_build``. Bij te weinig
        onderdelen ``InsufficientStock`` (tenzij ``allow_short`` en geen onderdeel met
        "voorraad controleren")."""
        qty = _d(qty)
        if not qty:
            raise ValidationError("Vul een aantal in.")
        if qty != qty.quantize(_Q):
            raise ValidationError(f"Aantal heeft meer dan {DECIMALS} decimalen.")
        if not transdate:
            raise ValidationError("Vul een datum in.")
        ensure_schema(self.conn)
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            prod = self._product(cur, pid, lock=True)
            closedto = _closedto(_db.get_defaults(cur, "closedto"), self.config.dateformat)
            if closedto and _num(transdate) <= closedto:          # F41: zoals tijdkaarten (timecards._check)
                raise ClosedPeriodError(CLOSED)
            if qty > 0 and prod["obsolete"]:
                raise ValidationError(f"Artikel {prod['partnumber']} is vervallen; assembleren kan niet meer.")
            if prod["project_id"]:
                raise ValidationError("Dit artikel hoort bij een project en kan hier niet geassembleerd worden.")
            cur.execute("SELECT parts_id FROM assembly WHERE aid = %s ORDER BY parts_id", (pid,))
            ids = [r["parts_id"] for r in cur.fetchall()]
            if not ids:
                raise ValidationError("Dit artikel heeft geen stuklijst.")
            cur.execute("SELECT id FROM parts WHERE id = ANY(%s) ORDER BY id FOR UPDATE", (ids,))
            old = _d(prod["onhand"])
            if qty < 0 and old + qty < 0:
                raise ValidationError("Demonteren kan niet verder dan de voorraad van het product "
                                      f"({_plain(old)}).")
            reqs = self.requirements(pid, qty, _cur=cur)
            shortages = [r for r in reqs if r["short"]]
            if shortages:
                blocking = any(r["checkinventory"] for r in shortages)
                if blocking or not allow_short:
                    names = ", ".join(r["partnumber"] for r in shortages)
                    msg = (f"Te weinig voorraad van {names}; bij deze onderdelen wordt de voorraad "
                           "gecontroleerd." if blocking else f"Te weinig voorraad van {names}.")
                    raise InsufficientStock(msg, shortages, blocking)
            _name, employee_id = _db.get_employee(cur, self.config.short_login)
            cur.execute("""INSERT INTO x_assembly_build (parts_id, transdate, qty, oldonhand, reference, notes,
                                                         employee_id, login)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (pid, transdate, qty, old, (reference or "").strip() or None,
                         (notes or "").strip() or None, employee_id or None, self.config.login))
            build_id = cur.fetchone()["id"]
            for r in reqs:
                if not r["stocked"]:
                    continue
                cur.execute("UPDATE parts SET onhand = coalesce(onhand, 0) + %s WHERE id = %s", (r["qty"], r["id"]))
                cur.execute("""INSERT INTO x_assembly_build_line (build_id, parts_id, perassembly, qty, oldonhand)
                               VALUES (%s, %s, %s, %s, %s)""",
                            (build_id, r["id"], r["perassembly"], r["qty"], r["onhand"]))
            cur.execute("UPDATE parts SET onhand = coalesce(onhand, 0) + %s WHERE id = %s", (qty, pid))
            support.audittrail(cur, self.config, trans_id=pid, tablename="ic", reference=prod["partnumber"],
                               formname="assembly", action="stock" if qty > 0 else "unstock")
            return {"id": build_id, "oldonhand": old, "qty": qty, "newonhand": old + qty,
                    "shortages": shortages}

    # ---------------------------------------------------------------- lijsten (SA9)

    def _products(self, cur, partnumber: str = "", description: str = "", obsolete: bool = False,
                  ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
        where, params = ["p.assembly"], []
        if not obsolete:
            where.append("NOT p.obsolete")
        if partnumber:
            where.append("lower(p.partnumber) LIKE %s")
            params.append(like(partnumber.lower()))
        if description:
            where.append("lower(p.description) LIKE %s")
            params.append(like(description.lower()))
        if ids is not None:
            where.append("p.id = ANY(%s)")
            params.append(ids)
        cur.execute(f"""SELECT p.id, p.partnumber, p.description, p.unit, p.bin, p.onhand::numeric AS onhand,
                               p.rop::numeric AS rop, p.obsolete
                        FROM parts p WHERE {' AND '.join(where)}
                        ORDER BY lower(p.partnumber), p.id""", params)
        return cur.fetchall()

    def can_make(self, partnumber: str = "", description: str = "",
                 obsolete: bool = False) -> List[Dict[str, Any]]:
        """Per product: voorraad, bestelniveau en hoeveel er met de voorraad van de directe
        onderdelen te maken is (``can_make`` None = geen onderdelen met voorraad), met het
        beperkende onderdeel."""
        with _db.cursor(self.conn) as cur:
            products = self._products(cur, partnumber, description, obsolete)
            bom = Bom(cur)
        for p in products:
            best: Optional[Decimal] = None
            limit = None
            for cid, per in bom.children.get(p["id"], []):
                c = bom.parts.get(cid)
                if c is None or not stocked(c) or per <= 0:
                    continue
                n = (max(_d(c["onhand"]), Decimal(0)) / per).to_integral_value(ROUND_FLOOR)
                if best is None or n < best:
                    best, limit = n, c["partnumber"]
            p["can_make"] = best
            p["limit"] = limit if best is not None else None
            p["components"] = len(bom.children.get(p["id"], []))
        return products

    def explode(self, pid: int, qty: Any = 1) -> List[Dict[str, Any]]:
        with _db.cursor(self.conn) as cur:
            self._product(cur, pid)
            return Bom(cur).explode(pid, _d(qty) or Decimal(1))

    def bom_list(self, partnumber: str = "", description: str = "", obsolete: bool = False,
                 top_only: bool = False) -> List[Dict[str, Any]]:
        """Stuklijsten van alle (gevonden) producten, uitgeklapt: per product een kopregel
        (``level`` 0) en daaronder de onderdelen. ``top_only``: alleen producten die zelf geen
        onderdeel van een ander product zijn."""
        with _db.cursor(self.conn) as cur:
            products = self._products(cur, partnumber, description, obsolete)
            bom = Bom(cur)
        out: List[Dict[str, Any]] = []
        for p in products:
            if top_only and bom.parents.get(p["id"]):
                continue
            prices = bom.prices(p["id"])
            out.append({"level": 0, "id": p["id"], "product_id": p["id"], "partnumber": p["partnumber"],
                        "description": p["description"], "unit": p["unit"], "assembly": True,
                        "perassembly": None, "qty": Decimal(1), "lastcost": prices["lastcost"],
                        "linecost": prices["lastcost"], "onhand": p["onhand"], "obsolete": p["obsolete"]})
            for r in bom.explode(p["id"]):
                out.append({**r, "product_id": p["id"]})
        return out

    def where_used(self, pid: int) -> List[Dict[str, Any]]:
        """Producten waar dit artikel in zit, direct (niveau 1) en via sub-stuklijsten, met het
        totale aantal per stuk van dat product."""
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT 1 FROM parts WHERE id = %s", (pid,))
            if not cur.fetchone():
                raise NotFoundError(f"Artikel {pid} niet gevonden.")
            bom = Bom(cur, extra_ids=[pid])
        out: List[Dict[str, Any]] = []

        def walk(node: int, mult: Decimal, level: int, path: Tuple[int, ...]) -> None:
            for aid in sorted(bom.parents.get(node, ()),
                              key=lambda a: ((bom.parts.get(a) or {}).get("partnumber") or "").lower()):
                if aid in path:
                    continue
                per = sum((q for c, q in bom.children.get(aid, []) if c == node), Decimal(0))
                a = bom.parts.get(aid)
                if a is None:
                    continue
                out.append({"level": level, "id": aid, "partnumber": a["partnumber"],
                            "description": a["description"], "unit": a["unit"], "perassembly": per,
                            "qty": mult * per, "via": (bom.parts.get(node) or {}).get("partnumber")
                            if level > 1 else None, "onhand": _d(a["onhand"]), "obsolete": bool(a["obsolete"])})
                walk(aid, mult * per, level + 1, path + (node,))
        walk(pid, Decimal(1), 1, ())
        return out

    def builds(self, pid: Optional[int] = None, datefrom: Optional[date] = None,
               dateto: Optional[date] = None, partnumber: str = "") -> List[Dict[str, Any]]:
        """Productiehistorie: assembleren en demonteren, oudste eerst."""
        with _db.cursor(self.conn) as cur:
            if not _has_table(cur, "x_assembly_build"):
                return []
            where, params = ["1 = 1"], []
            if pid:
                where.append("b.parts_id = %s")
                params.append(pid)
            if datefrom:
                where.append("b.transdate >= %s")
                params.append(datefrom)
            if dateto:
                where.append("b.transdate <= %s")
                params.append(dateto)
            if partnumber:
                where.append("lower(p.partnumber) LIKE %s")
                params.append(like(partnumber.lower()))
            cur.execute(f"""SELECT b.*, p.partnumber, p.description, p.unit, e.name AS employee
                            FROM x_assembly_build b
                            LEFT JOIN parts p ON (p.id = b.parts_id)
                            LEFT JOIN employee e ON (e.id = b.employee_id)
                            WHERE {' AND '.join(where)} ORDER BY b.transdate, b.id""", params)
            return cur.fetchall()

    def build_detail(self, build_id: int) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        with _db.cursor(self.conn) as cur:
            rows = []
            if _has_table(cur, "x_assembly_build"):
                cur.execute("""SELECT b.*, p.partnumber, p.description, p.unit, e.name AS employee
                               FROM x_assembly_build b
                               LEFT JOIN parts p ON (p.id = b.parts_id)
                               LEFT JOIN employee e ON (e.id = b.employee_id)
                               WHERE b.id = %s""", (build_id,))
                rows = cur.fetchall()
            if not rows:
                raise NotFoundError(f"Productieregel {build_id} niet gevonden.")
            cur.execute("""SELECT l.*, p.partnumber, p.description, p.unit
                           FROM x_assembly_build_line l LEFT JOIN parts p ON (p.id = l.parts_id)
                           WHERE l.build_id = %s ORDER BY l.id""", (build_id,))
            return rows[0], cur.fetchall()


def _plain(v: Decimal) -> str:
    """Getal zonder overbodige nullen (voor meldingen)."""
    s = format(v.normalize(), "f")
    return s
