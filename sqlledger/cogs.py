"""Kostprijs van verkochte artikelen (FIFO) bij facturen (fase 3, beslispunten F3–F5 en SA7).

Zoals SQL-Ledger (IS.pm/IR.pm ``cogs``, ``cogs_returns``, ``reverse_invoice``) staat de voorraad
per factuurregel in ``invoice``: een **aanvoerregel** heeft ``qty < 0`` (inkoop; IR slaat aantallen
negatief op, en een retour van een klant), een **afnemende regel** ``qty > 0`` (verkoop, onderdeel
van een verkocht samengesteld artikel, retour aan een leverancier). ``invoice.allocated`` telt hoeveel
er van een regel is toegewezen (aanvoer: positief, afnemer: negatief). Een afnemer neemt van de
oudste open aanvoer (op volgorde van boeking, zoals Perl); is er geen voorraad, dan krijgt een
verkoop later bij de volgende aanvoer alsnog zijn kostprijs (F4), geboekt in de verkoop op de datum
van de aanvoer.

Verschillen met Perl (zie README "Bewuste afwijkingen"):

* Elke toewijzing wordt vastgelegd in ``x_cogs_allocation`` (afnemer, aanvoer, aantal, bedrag) en
  bij wijzigen of verwijderen precies teruggedraaid (F3). Perl draaide terug op LIFO-volgorde terwijl
  het op FIFO-volgorde toewees, waardoor opnieuw opslaan de kostprijs veranderde.
* De kostprijs van een aanvoerregel staat in volle precisie in ``x_invoice_cost`` en wordt over de
  afnemers verdeeld zonder restcenten: 3 stuks voor 1,00 gaan als 0,33 + 0,34 + 0,33 de voorraad uit
  (Perl: inkoopprijs afgerond op 2 decimalen, 0,01 bleef voor altijd in de voorraad).
* Een retour van een klant komt terug tegen de oorspronkelijke kostprijs (de toewijzingen van de
  laatste verkopen aan die klant; anders de laatste inkoopprijs), niet tegen de verkoopprijs (F5). De
  creditnota boekt de omzet gewoon terug en daarnaast voorraad aan kostprijs.
* Een retour aan een leverancier haalt de voorraad weg tegen de FIFO-kostprijs; het verschil met de
  retourprijs gaat naar de kostprijsrekening (F5). Wat niet op voorraad is gaat tegen de retourprijs.
* De kostprijsregels in ``acc_trans`` krijgen als ``id`` de afnemende factuurregel (Perl: de
  inkoopregel, of bij een latere inkoop het artikelnummer); afrondingsverschillen van de factuur komen
  nooit op een kostprijsregel.
* Ongewijzigd opnieuw opslaan verandert niets aan de toewijzingen; een regel die wijzigt houdt wat
  kan (alleen het verschil wordt bij- of teruggeboekt).
* Gegevens uit Perl zonder vastgelegde toewijzing ("oud") blijven ongemoeid zolang de regels van de
  factuur niet wijzigen. Wijzigt er een, dan worden de oude toewijzingen van die factuur op de manier
  van Perl teruggedraaid en daarna opnieuw (vastgelegd) toegewezen.
* Lock op de artikelen tijdens het boeken (Perl: geen lock, twee gelijktijdige verkopen konden
  dezelfde inkoop nemen).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .amounts import round_amount, to_decimal
from .errors import ClosedPeriodError, LedgerError

ZERO = Decimal(0)

Row = Tuple[int, Decimal, date, Optional[int], Optional[int]]   # chart_id, amount, transdate, project_id, id


@dataclass
class Allocation:
    id: int
    parts_id: int
    consumer_id: int
    supply_id: Optional[int]
    qty: Decimal
    amount: Decimal
    kind: str
    project_id: Optional[int]


def _d(v: Any) -> Decimal:
    return to_decimal(v)


class CogsEngine:
    """Toewijzingen binnen één boekactie (één databasetransactie, artikelen gelockt).

    ``trans_id``: de factuur die geboekt wordt; haar eigen kostprijsregels schrijft de aanroeper
    zelf (``derived_rows``). Kostprijsregels in andere boekingen worden aan het eind bijgewerkt met
    ``rebook_others`` (alleen het verschil)."""

    def __init__(self, cur, precision: int, closedto: Optional[int], trans_id: Optional[int] = None):
        self.cur = cur
        self.prec = precision
        self.closedto = closedto
        self.trans_id = trans_id
        self.snap: Dict[int, Counter] = {}

    # ------------------------------------------------------------------ basis

    def lock_parts(self, ids: Iterable[int]) -> None:
        ids = sorted({int(i) for i in ids if i})
        if ids:
            self.cur.execute("SELECT id FROM parts WHERE id = ANY(%s) ORDER BY id FOR UPDATE", (ids,))

    def line(self, line_id: int) -> Dict[str, Any]:
        self.cur.execute("""SELECT i.id, i.trans_id, i.parts_id, i.qty::numeric AS qty,
                                   COALESCE(i.allocated, 0)::numeric AS allocated, i.sellprice::numeric AS sellprice,
                                   i.project_id, i.assemblyitem
                            FROM invoice i WHERE i.id = %s""", (line_id,))
        row = self.cur.fetchone()
        if not row:
            raise LedgerError(f"invoice line {line_id} not found")
        for k in ("qty", "allocated", "sellprice"):
            row[k] = _d(row[k])
        return row

    def records(self, *, consumer: Optional[int] = None, supply: Optional[int] = None) -> List[Allocation]:
        col, val = ("consumer_id", consumer) if consumer is not None else ("supply_id", supply)
        self.cur.execute(f"""SELECT id, parts_id, consumer_id, supply_id, qty, amount, kind, project_id
                             FROM x_cogs_allocation WHERE {col} = %s ORDER BY id""", (val,))
        return [Allocation(**{**r, "qty": _d(r["qty"]), "amount": _d(r["amount"])}) for r in self.cur.fetchall()]

    def covered(self, line_id: int, as_consumer: bool) -> Decimal:
        recs = self.records(consumer=line_id) if as_consumer else self.records(supply=line_id)
        return sum((r.qty for r in recs), ZERO)

    def cost(self, line_id: int) -> Optional[Tuple[Decimal, Decimal]]:
        self.cur.execute("SELECT qty, amount FROM x_invoice_cost WHERE invoice_id = %s", (line_id,))
        r = self.cur.fetchone()
        return (_d(r["qty"]), _d(r["amount"])) if r else None

    def set_cost(self, line_id: int, qty: Decimal, amount: Decimal) -> None:
        self.cur.execute("""INSERT INTO x_invoice_cost (invoice_id, qty, amount) VALUES (%s, %s, %s)
                            ON CONFLICT (invoice_id) DO UPDATE SET qty = excluded.qty, amount = excluded.amount""",
                         (line_id, qty, amount))

    def drop_cost(self, line_id: int) -> None:
        self.cur.execute("DELETE FROM x_invoice_cost WHERE invoice_id = %s", (line_id,))

    def _add_allocated(self, line_id: int, delta: Decimal) -> None:
        if delta:
            self.cur.execute("""UPDATE invoice SET allocated = (COALESCE(allocated, 0)::numeric + %s)::float8
                                WHERE id = %s""", (delta, line_id))

    def _trans_of(self, line_id: int) -> Optional[int]:
        self.cur.execute("SELECT trans_id FROM invoice WHERE id = %s", (line_id,))
        r = self.cur.fetchone()
        return r["trans_id"] if r else None

    def touch(self, consumer_id: int) -> None:
        """Leg de kostprijsregels van de boeking van deze afnemer vast vóór de eerste wijziging."""
        tid = self._trans_of(consumer_id)
        if tid and tid != self.trans_id and tid not in self.snap:
            self.snap[tid] = Counter(self.derived_rows(tid))

    # ----------------------------------------------------------- toewijzen

    def supply_amount(self, supply: Dict[str, Any], qty: Decimal) -> Decimal:
        """Kostprijs van ``qty`` uit een aanvoerregel: uit ``x_invoice_cost`` zonder restcenten
        (cumulatief afgerond), anders zoals Perl: verkoopprijs van de regel × aantal."""
        c = self.cost(supply["id"])
        if c is None:
            return round_amount(supply["sellprice"] * qty, self.prec)
        total_qty, total = c
        recs = self.records(supply=supply["id"])
        a = sum((r.qty for r in recs), ZERO)
        b = sum((r.amount for r in recs), ZERO)
        if not total_qty or a + qty >= total_qty:
            return total - b
        return round_amount(total * (a + qty) / total_qty, self.prec) - b

    def create(self, consumer: Dict[str, Any], supply: Optional[Dict[str, Any]], qty: Decimal,
               amount: Decimal, kind: str, project_id: Optional[int]) -> None:
        self.touch(consumer["id"])
        self.cur.execute("""INSERT INTO x_cogs_allocation (parts_id, consumer_id, supply_id, qty, amount, kind,
                                                           project_id)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                         (consumer["parts_id"], consumer["id"], supply["id"] if supply else None, qty, amount,
                          kind, project_id))
        self._add_allocated(consumer["id"], -qty)
        if supply:
            self._add_allocated(supply["id"], qty)

    def reverse(self, rec: Allocation) -> None:
        self.touch(rec.consumer_id)
        self.cur.execute("DELETE FROM x_cogs_allocation WHERE id = %s", (rec.id,))
        self._add_allocated(rec.consumer_id, rec.qty)
        if rec.supply_id:
            self._add_allocated(rec.supply_id, -rec.qty)

    def allocate_consumer(self, consumer_id: int, need: Decimal, project_id: Optional[int],
                          kind: str = "c") -> Decimal:
        """FIFO: neem ``need`` van de oudste open aanvoer (IS.pm cogs; volgorde van boeking zoals
        Perl, binnen een boeking op regel). Geeft het toegewezen aantal terug."""
        consumer = self.line(consumer_id)
        done = ZERO
        if need <= 0:
            return done
        self.cur.execute("""SELECT i.id FROM invoice i
                            WHERE i.parts_id = %s AND i.qty::numeric + COALESCE(i.allocated, 0)::numeric < 0
                            ORDER BY i.trans_id, i.id""", (consumer["parts_id"],))
        for sid in [r["id"] for r in self.cur.fetchall()]:
            supply = self.line(sid)
            avail = -supply["qty"] - supply["allocated"]
            take = min(avail, need - done)
            if take <= 0:
                continue
            self.create(consumer, supply, take, self.supply_amount(supply, take), kind, project_id)
            done += take
            if done >= need:
                break
        return done

    def allocate_uncovered(self, consumer_id: int, qty: Decimal, unit: Decimal, project_id: Optional[int]) -> None:
        """Retour aan leverancier zonder voorraad: tegen de retourprijs (geen aanvoerregel)."""
        if qty > 0:
            consumer = self.line(consumer_id)
            self.create(consumer, None, qty, round_amount(unit * qty, self.prec), "c", project_id)

    def allocate_supply(self, supply_id: int) -> Decimal:
        """Nieuwe of gewijzigde aanvoer: verkopen zonder (volledige) kostprijs krijgen die alsnog,
        oudste eerst (IR.pm cogs, IS.pm cogs_returns). Alleen verkoopregels (Perl nam ook retouren
        aan leveranciers mee)."""
        supply = self.line(supply_id)
        avail = -supply["qty"] - supply["allocated"]
        done = ZERO
        if avail <= 0:
            return done
        self.cur.execute("""SELECT i.id FROM invoice i JOIN ar a ON (a.id = i.trans_id)
                            WHERE i.parts_id = %s AND i.qty > 0
                              AND i.qty::numeric + COALESCE(i.allocated, 0)::numeric > 0
                            ORDER BY i.trans_id, i.id""", (supply["parts_id"],))
        for cid in [r["id"] for r in self.cur.fetchall()]:
            consumer = self.line(cid)
            open_qty = consumer["qty"] + consumer["allocated"]
            take = min(open_qty, avail - done)
            if take <= 0:
                continue
            self.create(consumer, supply, take, self.supply_amount(supply, take), "d", self.line_project(cid))
            supply = self.line(supply_id)
            done += take
            if done >= avail:
                break
        return done

    def line_project(self, line_id: int) -> Optional[int]:
        """Project van een factuurregel; een onderdeel van een samengesteld artikel heeft het project
        van het artikel (de eerstvolgende gewone regel van de factuur, zoals Perl ze wegschrijft)."""
        row = self.line(line_id)
        if not row["assemblyitem"]:
            return row["project_id"]
        self.cur.execute("""SELECT project_id FROM invoice WHERE trans_id = %s AND id > %s
                              AND NOT COALESCE(assemblyitem, false) ORDER BY id LIMIT 1""",
                         (row["trans_id"], line_id))
        r = self.cur.fetchone()
        return r["project_id"] if r else None

    # ---------------------------------------------------------- terugdraaien

    def trim_consumer(self, consumer_id: int, keep: Decimal) -> None:
        """Laat van de vastgelegde toewijzingen van een afnemer ten hoogste ``keep`` staan (LIFO)."""
        recs = self.records(consumer=consumer_id)
        total = sum((r.qty for r in recs), ZERO)
        for rec in reversed(recs):
            if total <= keep:
                break
            excess = total - keep
            self.reverse(rec)
            total -= rec.qty
            rest = rec.qty - excess
            if rest > 0:
                self._recreate(rec, rest)
                total += rest

    def trim_supply(self, supply_id: int, keep: Decimal) -> None:
        recs = self.records(supply=supply_id)
        total = sum((r.qty for r in recs), ZERO)
        for rec in reversed(recs):
            if total <= keep:
                break
            excess = total - keep
            self.reverse(rec)
            total -= rec.qty
            rest = rec.qty - excess
            if rest > 0:
                self._recreate(rec, rest)
                total += rest

    def _recreate(self, rec: Allocation, qty: Decimal) -> None:
        consumer = self.line(rec.consumer_id)
        if rec.supply_id:
            supply = self.line(rec.supply_id)
            amount = self.supply_amount(supply, qty)
        else:
            supply = None
            amount = round_amount(rec.amount * qty / rec.qty, self.prec) if rec.qty else ZERO
        self.create(consumer, supply, qty, amount, rec.kind, rec.project_id)

    def reverse_all(self, line_id: int) -> None:
        for rec in self.records(consumer=line_id) + self.records(supply=line_id):
            self.reverse(rec)

    def reprice_supply(self, supply_id: int) -> None:
        """Kostprijs van een aanvoerregel gewijzigd: bedragen van de toewijzingen opnieuw verdelen
        (cumulatief, zonder restcenten)."""
        c = self.cost(supply_id)
        if c is None:
            return
        total_qty, total = c
        a = b = ZERO
        for rec in self.records(supply=supply_id):
            a += rec.qty
            new = (total - b) if (not total_qty or a >= total_qty) else round_amount(total * a / total_qty, self.prec) - b
            b += new
            if new != rec.amount:
                self.touch(rec.consumer_id)
                self.cur.execute("UPDATE x_cogs_allocation SET amount = %s WHERE id = %s", (new, rec.id))

    # ------------------------------------------------------ oude toewijzingen (Perl)

    def legacy_qty(self, line: Dict[str, Any]) -> Decimal:
        """Deel van ``allocated`` zonder vastgelegde toewijzing (uit Perl)."""
        if line["qty"] > 0:
            return -line["allocated"] - self.covered(line["id"], True)
        return line["allocated"] - self.covered(line["id"], False)

    def legacy_reverse(self, line: Dict[str, Any], transdate: date) -> None:
        """Draai het oude deel terug zoals Perl (IS.pm/IR.pm reverse_invoice): afnemer → aanvoer
        LIFO vrijgeven; aanvoer → verkopen LIFO vrijgeven met een tegenboeking in die verkoop op
        ``transdate``. Alleen oude delen van de andere regels worden geraakt."""
        qty = self.legacy_qty(line)
        if qty <= 0:
            return
        pid = line["parts_id"]
        if line["qty"] > 0:
            self.cur.execute("""SELECT i.id FROM invoice i WHERE i.parts_id = %s AND COALESCE(i.allocated, 0) > 0
                                ORDER BY i.trans_id DESC, i.id DESC""", (pid,))
            todo = qty
            for sid in [r["id"] for r in self.cur.fetchall()]:
                s = self.line(sid)
                take = min(todo, self.legacy_qty(s))
                if take <= 0:
                    continue
                self._add_allocated(sid, -take)
                todo -= take
                if todo <= 0:
                    break
            self._add_allocated(line["id"], qty)
            return
        self.cur.execute("SELECT inventory_accno_id, expense_accno_id FROM parts WHERE id = %s", (pid,))
        acc = self.cur.fetchone()
        self.cur.execute("""SELECT i.id FROM invoice i WHERE i.parts_id = %s AND COALESCE(i.allocated, 0) < 0
                            ORDER BY i.trans_id DESC, i.id DESC""", (pid,))
        todo = qty
        for cid in [r["id"] for r in self.cur.fetchall()]:
            c = self.line(cid)
            take = min(todo, self.legacy_qty(c))
            if take <= 0:
                continue
            self._add_allocated(cid, take)
            amount = round_amount(line["sellprice"] * take, self.prec)
            if amount and c["trans_id"] != self.trans_id:
                # Perl boekte de tegenboeking op de datum van de inkoop, ook als die vóór de verkoop lag
                self.cur.execute("SELECT transdate FROM ar WHERE id = %s UNION ALL SELECT transdate FROM ap WHERE id = %s",
                                 (c["trans_id"], c["trans_id"]))
                cdate = self.cur.fetchone()["transdate"]
                d = max(cdate, transdate)
                self._check_open(d)
                for chart, amt in ((acc["expense_accno_id"], amount), (acc["inventory_accno_id"], -amount)):
                    self.cur.execute("""INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, project_id)
                                        VALUES (%s, %s, %s, %s, %s)""",
                                     (c["trans_id"], chart, amt, d, line["project_id"]))
            todo -= take
            if todo <= 0:
                break
        self._add_allocated(line["id"], -qty)

    # ------------------------------------------------------------ kostprijsregels

    def derived_rows(self, trans_id: int) -> List[Row]:
        """Kostprijsregels van een boeking volgens de vastgelegde toewijzingen van haar afnemende
        regels: kosten debet / voorraad credit, op de boekdatum (of bij een latere aanvoer op de
        datum daarvan), met het project van de regel; ``id`` = de afnemende regel."""
        self.cur.execute("""
            SELECT r.amount, r.kind, r.project_id, r.consumer_id, p.inventory_accno_id, p.expense_accno_id,
                   COALESCE(ca.transdate, cp.transdate) AS cdate, COALESCE(sa.transdate, sp.transdate) AS sdate
            FROM x_cogs_allocation r
            JOIN invoice i ON (i.id = r.consumer_id)
            JOIN parts p ON (p.id = r.parts_id)
            LEFT JOIN ar ca ON (ca.id = i.trans_id) LEFT JOIN ap cp ON (cp.id = i.trans_id)
            LEFT JOIN invoice si ON (si.id = r.supply_id)
            LEFT JOIN ar sa ON (sa.id = si.trans_id) LEFT JOIN ap sp ON (sp.id = si.trans_id)
            WHERE i.trans_id = %s ORDER BY r.id""", (trans_id,))
        total: Dict[Tuple, Decimal] = {}
        for r in self.cur.fetchall():
            d = r["cdate"]
            if r["kind"] == "d" and r["sdate"] and r["sdate"] > d:
                d = r["sdate"]
            key = (r["expense_accno_id"], r["inventory_accno_id"], d, r["project_id"], r["consumer_id"])
            total[key] = total.get(key, ZERO) + _d(r["amount"])
        out: List[Row] = []
        for (expense, inventory, d, project, consumer), amount in total.items():
            amount = round_amount(amount, self.prec)
            if amount:      # één regel per afnemende regel en datum
                out.append((expense, -amount, d, project, consumer))
                out.append((inventory, amount, d, project, consumer))
        return out

    def _check_open(self, d: date) -> None:
        if self.closedto and int(d.strftime("%Y%m%d")) <= self.closedto:
            raise ClosedPeriodError("Kostprijs zou wijzigen in een afgesloten periode")

    def rebook_others(self) -> List[int]:
        """Werk de kostprijsregels bij in de andere boekingen waarvan toewijzingen gewijzigd zijn:
        alleen verschillen worden verwijderd en toegevoegd. Geeft de geraakte boekingen terug."""
        changed = []
        for tid, before in self.snap.items():
            after = Counter(self.derived_rows(tid))
            gone, new = before - after, after - before
            if not gone and not new:
                continue
            changed.append(tid)
            for (chart, amount, d, project, line_id), n in gone.items():
                self._check_open(d)
                for _ in range(n):
                    self.cur.execute("""DELETE FROM acc_trans WHERE ctid = (
                                            SELECT ctid FROM acc_trans WHERE trans_id = %s AND chart_id = %s
                                              AND round(amount::numeric, %s) = %s AND transdate = %s
                                              AND project_id IS NOT DISTINCT FROM %s AND id IS NOT DISTINCT FROM %s
                                            LIMIT 1)""", (tid, chart, self.prec, amount, d, project, line_id))
                    if self.cur.rowcount != 1:
                        raise LedgerError(f"Kostprijsregel van boeking {tid} niet gevonden (rekening {chart}, "
                                          f"{amount}, {d}); de boeking is buiten deze toepassing gewijzigd")
            for (chart, amount, d, project, line_id), n in new.items():
                self._check_open(d)
                for _ in range(n):
                    self.cur.execute("""INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, project_id, id)
                                        VALUES (%s, %s, %s, %s, %s, %s)""", (tid, chart, amount, d, project, line_id))
        return changed

    # ------------------------------------------------------------ retour van klant

    def return_unit_cost(self, parts_id: int, customer_id: int, qty: Decimal, exclude_trans: Optional[int],
                         fallback: Decimal) -> Decimal:
        """Kostprijs van ``qty`` stuks retour van een klant (F5): de vastgelegde toewijzingen van de
        laatste verkopen van dit artikel aan die klant; de rest tegen de laatste inkoopprijs (anders
        gemiddelde inkoopprijs, anders ``fallback``)."""
        self.cur.execute("""
            SELECT r.qty, r.amount FROM x_cogs_allocation r
            JOIN invoice i ON (i.id = r.consumer_id) JOIN ar a ON (a.id = i.trans_id)
            WHERE r.parts_id = %s AND a.customer_id = %s AND i.qty > 0 AND a.id <> %s
            ORDER BY a.transdate DESC, a.id DESC, r.id DESC""", (parts_id, customer_id, int(exclude_trans or 0)))
        todo, total = qty, ZERO
        for r in self.cur.fetchall():
            take = min(todo, _d(r["qty"]))
            if take <= 0:
                break
            total += _d(r["amount"]) * take / _d(r["qty"])
            todo -= take
        if todo > 0:
            self.cur.execute("SELECT lastcost::numeric AS lastcost, avgcost::numeric AS avgcost FROM parts WHERE id = %s",
                             (parts_id,))
            p = self.cur.fetchone() or {}
            unit = _d(p.get("lastcost")) or _d(p.get("avgcost")) or fallback
            total += unit * todo
        return round_amount(total, self.prec)
