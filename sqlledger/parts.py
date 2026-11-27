"""Artikelen en diensten (port van SL/IC.pm + regels uit bin/mozilla/ic.pl).

Een artikel is een rij in ``parts`` met btw-rekeningen in ``partstax``. De soort
staat niet in een kolom maar volgt uit de rekeningen (IC.pm ``get_part``):

* artikel (part): voorraad-, opbrengst- en kostprijsrekening;
* dienst (service): geen voorraadrekening;
* loon/overhead (labor): geen opbrengstrekening;
* samengesteld (assembly): ``assembly``; kit: geen enkele rekening.

Artikelen, diensten en samengestelde artikelen (stuklijst, SA1) kunnen hier
aangemaakt en gewijzigd worden (AT1); loon/overhead en kits zijn alleen te lezen.
Prijzen, controles en assembleren van samengestelde artikelen staan in
``sqlledger.assembly``.

Verschillen met Perl (zie README "Bewuste afwijkingen"):

* Opslaan schrijft alleen gewijzigde kolommen; velden die het scherm niet kent
  (afbeelding, tekening, merk/model, leveranciers- en klantprijzen, douane,
  lot/houdbaarheid, vertalingen) blijven staan (AT2). Perl schreef alle kolommen
  opnieuw en gooide leveranciers, klantprijzen, merk/model en btw weg.
* De voorraad (``onhand``) verandert nooit via het artikel, alleen via facturen
  en de voorraadcorrectie, die in ``x_stock_adjustment`` wordt vastgelegd (AT4).
* Rekeningen en soort van een gebruikt artikel kunnen niet meer wijzigen (AT5);
  Perl blokkeerde dat alleen in het scherm.
* Verwijderen alleen als het artikel nergens gebruikt is (AT6); Perl controleerde
  dat niet op de server.
* Omschrijving verplicht; dubbel artikelnummer mag (AT3), ``duplicates`` geeft
  ze voor een waarschuwing.
* Zoeken: het open/gesloten-filter werkt ook voor offertes (Perl schreef het in
  de verkeerde variabele, IC.pm 1543).
* Samengesteld artikel: de stuklijst kan wijzigen zolang er geen voorraad van het
  product is (SA8; Perl: alleen zolang het artikel nergens gebruikt is, daarna
  "Changeup"); prijzen uit de onderdelen (SA3); de soort kan niet van of naar
  samengesteld wijzigen.
* Alle SQL geparametriseerd.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Tuple

import psycopg

from . import _db, support
from . import assembly as asm
from .amounts import to_decimal
from .config import UserConfig
from .errors import ClosedPeriodError, NotFoundError, ValidationError
from .extensions import ensure_schema
from .gl import _closedto, _num, like
from .numbering import next_number

KINDS = ("part", "service", "labor", "assembly", "kit")
EDITABLE_KINDS = ("part", "service", "assembly")
#: maximaal aantal decimalen voor prijzen en hoeveelheden (Perl: zoveel als ingevoerd)
DECIMALS = 6

#: rekeningkoppelingen per soort (ic.pl 115-186): (voorraad, opbrengst, kosten, btw)
LINKS = {
    "part": ("IC", "IC_sale", "IC_cogs", "IC_taxpart"),
    "service": (None, "IC_income", "IC_expense", "IC_taxservice"),
    "labor": ("IC", None, "IC_cogs", "IC_taxpart"),
    "assembly": (None, "IC_sale", None, "IC_taxpart"),     # ic.pl: alleen opbrengst (IC_sale) en btw
}

#: velden van het artikel die het scherm beheert (veld -> kolom)
TEXT_COLUMNS = ("partnumber", "description", "unit", "notes", "barcode", "bin")
NUMBER_COLUMNS = ("sellprice", "listprice", "lastcost", "weight", "rop")
FLAG_COLUMNS = ("obsolete", "checkinventory")

STATUSES = ("all", "active", "obsolete", "orphaned", "onhand", "short")
TRANSACTIONS = ("bought", "sold", "onorder", "ordered", "rfq", "quoted")


def kind_of(row: Dict[str, Any]) -> str:
    """Soort artikel uit de rekeningen (IC.pm 56-60)."""
    kind = "part" if row.get("inventory_accno_id") else "service"
    if not row.get("income_accno_id"):
        kind = "labor"
    if row.get("assembly"):
        kind = "assembly"
    if not (row.get("inventory_accno_id") or row.get("income_accno_id") or row.get("expense_accno_id")):
        kind = "kit"
    return kind


@dataclass
class Part:
    kind: str = "part"
    partnumber: str = ""
    description: str = ""
    unit: str = ""
    partsgroup_id: Optional[int] = None
    sellprice: Decimal = Decimal(0)
    listprice: Decimal = Decimal(0)
    lastcost: Decimal = Decimal(0)
    priceupdate: Optional[date] = None
    weight: Decimal = Decimal(0)
    rop: Decimal = Decimal(0)
    bin: str = ""
    barcode: str = ""
    notes: str = ""
    obsolete: bool = False
    checkinventory: bool = False
    inventory_accno: str = ""
    income_accno: str = ""
    expense_accno: str = ""
    taxaccounts: List[str] = field(default_factory=list)
    id: Optional[int] = None
    # alleen lezen
    onhand: Decimal = Decimal(0)
    avgcost: Decimal = Decimal(0)
    partsgroup: str = ""
    project_id: Optional[int] = None
    in_use: bool = False
    # samengesteld artikel (SA1–SA3)
    components: List["asm.Component"] = field(default_factory=list)
    own_sellprice: bool = False             # verkoopprijs zelf bepalen i.p.v. som van de onderdelen
    computed: Dict[str, Decimal] = field(default_factory=dict)   # berekende prijzen (alleen lezen)
    used_in: int = 0                        # aantal producten waarin dit artikel een onderdeel is

    @property
    def editable(self) -> bool:
        return self.kind in EDITABLE_KINDS and not self.project_id


@dataclass
class PartSearch:
    """Filters van ic.pl search / IC::all_parts."""
    kind: str = "all"                   # all | part | service | labor | assembly | kit
    partnumber: str = ""
    description: str = ""
    barcode: str = ""
    partsgroup_id: Optional[int] = None
    status: str = "active"              # zie STATUSES
    # transacties: alleen regels uit facturen/orders/offertes van deze soorten
    transactions: Tuple[str, ...] = ()
    open: bool = True
    closed: bool = True
    datefrom: Optional[date] = None
    dateto: Optional[date] = None
    sort: str = "partnumber"
    desc: bool = False


def _dec(value: Any) -> Decimal:
    return to_decimal(value)


class Parts:
    """Artikelen: lezen, opslaan, verwijderen, zoeken, prijshistorie, groepen, voorraadcorrectie."""

    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        self.conn = conn
        self.config = config

    # ------------------------------------------------------------ keuzelijsten

    def links(self, kind: str) -> Dict[str, List[Dict[str, str]]]:
        """Rekeningen per veld voor deze soort: inventory, income, expense, tax."""
        inv, inc, exp, tax = LINKS.get(kind, LINKS["part"])
        out: Dict[str, List[Dict[str, str]]] = {"inventory": [], "income": [], "expense": [], "tax": []}
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT c.accno, coalesce(l.description, c.description) AS description, c.link
                           FROM chart c
                           LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %s)
                           WHERE c.link LIKE '%%IC%%' ORDER BY c.accno""", (self.config.countrycode,))
            for r in cur.fetchall():
                keys = (r["link"] or "").split(":")
                for name, key in (("inventory", inv), ("income", inc), ("expense", exp), ("tax", tax)):
                    if key and key in keys:
                        out[name].append({"accno": r["accno"], "description": r["description"]})
        return out

    def groups(self) -> List[Dict[str, Any]]:
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT g.id, g.partsgroup, g.code,
                                  (SELECT count(*) FROM parts p WHERE p.partsgroup_id = g.id) AS used
                           FROM partsgroup g ORDER BY g.partsgroup""")
            return cur.fetchall()

    def save_group(self, name: str, code: str = "", group_id: Optional[int] = None) -> int:
        name = (name or "").strip()
        if not name:
            raise ValidationError("Vul een naam in.")
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("SELECT id FROM partsgroup WHERE lower(partsgroup) = lower(%s) AND id IS DISTINCT FROM %s",
                        (name, group_id))
            if cur.fetchone():
                raise ValidationError(f"Groep {name} bestaat al.")
            if group_id:
                cur.execute("UPDATE partsgroup SET partsgroup = %s, code = %s WHERE id = %s RETURNING id",
                            (name, code or None, group_id))
                if not cur.fetchone():
                    raise NotFoundError(f"Groep {group_id} niet gevonden.")
                return group_id
            cur.execute("INSERT INTO partsgroup (partsgroup, code) VALUES (%s, %s) RETURNING id", (name, code or None))
            return cur.fetchone()["id"]

    def delete_group(self, group_id: int) -> None:
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("SELECT count(*) AS n FROM parts WHERE partsgroup_id = %s", (group_id,))
            n = cur.fetchone()["n"]
            if n:
                raise ValidationError(f"Deze groep is in gebruik: {n} artikel(en).")
            cur.execute("DELETE FROM translation WHERE trans_id = %s", (group_id,))
            cur.execute("DELETE FROM partsgroup WHERE id = %s", (group_id,))

    # ------------------------------------------------------------ lezen

    def _usage(self, cur, part_id: int) -> Dict[str, int]:
        """Waar het artikel gebruikt wordt (IC.pm ``orphaned``)."""
        cur.execute("""SELECT (SELECT count(*) FROM invoice WHERE parts_id = %(id)s) AS invoice,
                              (SELECT count(*) FROM orderitems WHERE parts_id = %(id)s) AS orderitems,
                              (SELECT count(*) FROM assembly WHERE parts_id = %(id)s) AS assembly,
                              (SELECT count(*) FROM jcitems WHERE parts_id = %(id)s) AS jcitems""",
                    {"id": part_id})
        return dict(cur.fetchone())

    def _load(self, cur, part_id: int, lock: bool = False) -> Tuple[Part, Dict[str, Any]]:
        cur.execute(f"""SELECT p.*, c1.accno AS inventory_accno, c2.accno AS income_accno,
                               c3.accno AS expense_accno, pg.partsgroup AS groupname
                        FROM parts p
                        LEFT JOIN chart c1 ON (c1.id = p.inventory_accno_id)
                        LEFT JOIN chart c2 ON (c2.id = p.income_accno_id)
                        LEFT JOIN chart c3 ON (c3.id = p.expense_accno_id)
                        LEFT JOIN partsgroup pg ON (pg.id = p.partsgroup_id)
                        WHERE p.id = %s{' FOR UPDATE OF p' if lock else ''}""", (part_id,))
        row = cur.fetchone()
        if not row:
            raise NotFoundError(f"Artikel {part_id} niet gevonden.")
        cur.execute("""SELECT c.accno FROM partstax pt JOIN chart c ON (c.id = pt.chart_id)
                       WHERE pt.parts_id = %s ORDER BY c.accno""", (part_id,))
        taxes = [r["accno"] for r in cur.fetchall()]
        usage = self._usage(cur, part_id)
        p = Part(
            id=row["id"], kind=kind_of(row), partnumber=row["partnumber"] or "",
            description=row["description"] or "", unit=row["unit"] or "",
            partsgroup_id=row["partsgroup_id"] or None, partsgroup=row["groupname"] or "",
            sellprice=_dec(row["sellprice"]), listprice=_dec(row["listprice"]), lastcost=_dec(row["lastcost"]),
            priceupdate=row["priceupdate"], weight=_dec(row["weight"]), rop=_dec(row["rop"]),
            bin=row["bin"] or "", barcode=row["barcode"] or "", notes=row["notes"] or "",
            obsolete=bool(row["obsolete"]), checkinventory=bool(row["checkinventory"]),
            inventory_accno=row["inventory_accno"] or "", income_accno=row["income_accno"] or "",
            expense_accno=row["expense_accno"] or "", taxaccounts=taxes,
            onhand=_dec(row["onhand"]), avgcost=_dec(row["avgcost"]), project_id=row["project_id"],
            in_use=any(usage.values()), used_in=usage["assembly"],
        )
        if p.kind in ("assembly", "kit"):
            p.components = self._components(cur, part_id)
        if p.kind == "assembly":
            p.own_sellprice = self._own_sellprice(cur, part_id)
            bom = asm.Bom(cur, extra_ids=[part_id])
            p.computed = bom.prices(part_id)
        return p, dict(row)

    def _components(self, cur, part_id: int) -> List["asm.Component"]:
        cur.execute("""SELECT a.parts_id, a.qty::numeric AS qty, a.bom, a.adj, p.partnumber, p.description, p.unit,
                              p.onhand::numeric AS onhand, p.obsolete, p.sellprice::numeric AS sellprice,
                              p.listprice::numeric AS listprice, p.lastcost::numeric AS lastcost,
                              p.weight::numeric AS weight, p.inventory_accno_id, p.income_accno_id,
                              p.expense_accno_id, p.assembly
                       FROM assembly a JOIN parts p ON (p.id = a.parts_id)
                       WHERE a.aid = %s ORDER BY a.id""", (part_id,))
        return [asm.Component(parts_id=r["parts_id"], qty=_dec(r["qty"]), bom=bool(r["bom"]), adj=bool(r["adj"]),
                              partnumber=r["partnumber"] or "", description=r["description"] or "",
                              unit=r["unit"] or "", kind=kind_of(r), onhand=_dec(r["onhand"]),
                              obsolete=bool(r["obsolete"]), sellprice=_dec(r["sellprice"]),
                              listprice=_dec(r["listprice"]), lastcost=_dec(r["lastcost"]),
                              weight=_dec(r["weight"]))
                for r in cur.fetchall()]

    def _own_sellprice(self, cur, part_id: int) -> bool:
        if not asm._has_table(cur, "x_assembly_price"):
            return False
        cur.execute("SELECT own_sellprice FROM x_assembly_price WHERE parts_id = %s", (part_id,))
        row = cur.fetchone()
        return bool(row and row["own_sellprice"])

    def bom_preview(self, part_id: Optional[int], rows: List[Tuple[int, Any]]) -> Dict[str, Any]:
        """Stukprijzen per onderdeel en berekende totalen van een stuklijst (``assembly.preview``)."""
        with _db.cursor(self.conn) as cur:
            return asm.preview(cur, part_id, [(int(c), _dec(q)) for c, q in rows])

    def component_choices(self, text: str, exclude_id: Optional[int] = None,
                          limit: int = 50) -> List[Dict[str, Any]]:
        """Artikelen die onderdeel kunnen worden: nummer exact (hoofdletterongevoelig) of nummer/
        omschrijving bevat de tekst; niet vervallen, geen kit, niet het product zelf (IC.pm
        assembly_item). Exacte nummers eerst."""
        text = (text or "").strip()
        if not text:
            return []
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT p.id, p.partnumber, p.description, p.unit, p.onhand::numeric AS onhand,
                                  p.inventory_accno_id, p.income_accno_id, p.expense_accno_id, p.assembly
                           FROM parts p
                           WHERE NOT p.obsolete AND p.id IS DISTINCT FROM %(ex)s
                             AND (p.inventory_accno_id IS NOT NULL OR p.income_accno_id IS NOT NULL
                                  OR p.expense_accno_id IS NOT NULL)
                             AND (lower(p.partnumber) LIKE %(like)s OR lower(p.description) LIKE %(like)s)
                           ORDER BY lower(p.partnumber) <> lower(%(text)s), lower(p.partnumber), p.id
                           LIMIT %(limit)s""",
                        {"ex": exclude_id, "like": like(text.lower()), "text": text, "limit": limit})
            out = cur.fetchall()
        for r in out:
            r["kind"] = kind_of(r)
        return out

    def get(self, part_id: int) -> Part:
        with _db.cursor(self.conn) as cur:
            return self._load(cur, part_id)[0]

    def new(self, kind: str = "part") -> Part:
        """Leeg artikel: alle btw-rekeningen aangevinkt en prijsdatum vandaag, zoals ic.pl add."""
        if kind not in EDITABLE_KINDS:
            raise ValidationError("Alleen artikelen en diensten kunnen aangemaakt worden.")
        links = self.links(kind)
        return Part(kind=kind, priceupdate=date.today(), taxaccounts=[t["accno"] for t in links["tax"]])

    def in_use(self, part_id: int) -> bool:
        with _db.cursor(self.conn) as cur:
            return any(self._usage(cur, part_id).values())

    def duplicates(self, partnumber: str, exclude_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Andere artikelen met hetzelfde nummer (hoofdletterongevoelig), voor een waarschuwing (AT3)."""
        if not (partnumber or "").strip():
            return []
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT id, partnumber, description FROM parts
                           WHERE lower(partnumber) = lower(%s) AND id IS DISTINCT FROM %s ORDER BY id""",
                        (partnumber.strip(), exclude_id))
            return cur.fetchall()

    def next_number(self, peek: bool = False) -> str:
        """Volgend artikelnummer (defaults.partnumber). peek: teller niet ophogen."""
        with self.conn.transaction(force_rollback=peek), _db.cursor(self.conn) as cur:
            return next_number(cur, "partnumber", dateformat=self.config.dateformat)

    # ------------------------------------------------------------ opslaan

    def _chart_ids(self, cur, p: Part, old: Optional[Part] = None) -> Dict[str, Optional[int]]:
        inv, inc, exp, _tax = LINKS[p.kind]
        out: Dict[str, Optional[int]] = {}
        for name, key, accno in (("inventory", inv, p.inventory_accno), ("income", inc, p.income_accno),
                                 ("expense", exp, p.expense_accno)):
            if not key:
                if accno:
                    raise ValidationError(f"Een {SOORT[p.kind]} heeft geen {LABEL[name]}.")
                out[name] = None
                continue
            if not accno:
                if old is not None and not getattr(old, f"{name}_accno"):
                    out[name] = None        # bestaand artikel zonder deze rekening: mag zo blijven
                    continue
                raise ValidationError(f"Kies een {LABEL[name]}.")
            cur.execute("SELECT id, link FROM chart WHERE accno = %s", (accno,))
            row = cur.fetchone()
            unchanged = old is not None and getattr(old, f"{name}_accno") == accno
            if not row or (key not in (row["link"] or "").split(":") and not unchanged):
                raise ValidationError(f"Rekening {accno} is niet gekoppeld als {LABEL[name]} ({key}).")
            out[name] = row["id"]
        return out

    def _tax_ids(self, cur, p: Part) -> List[int]:
        key = LINKS[p.kind][3]
        ids = []
        for accno in sorted(set(a for a in p.taxaccounts if a)):
            cur.execute("SELECT id, link FROM chart WHERE accno = %s", (accno,))
            row = cur.fetchone()
            if not row or key not in (row["link"] or "").split(":"):
                raise ValidationError(f"Rekening {accno} is geen btw-rekening voor dit soort artikel.")
            ids.append(row["id"])
        return ids

    def _validate(self, cur, p: Part, old: Optional[Part] = None) -> None:
        if p.kind not in EDITABLE_KINDS:
            raise ValidationError("Alleen artikelen en diensten kunnen gewijzigd worden.")
        if not (p.description or "").strip() and (old is None or (old.description or "").strip()):
            raise ValidationError("Vul een omschrijving in.")   # bestaand artikel zonder omschrijving mag blijven
        if len(p.unit or "") > 5:
            raise ValidationError("De eenheid is te lang (maximaal 5 tekens).")
        computed = ("listprice", "lastcost", "weight") if p.kind == "assembly" else ()
        for name in NUMBER_COLUMNS:
            if name in computed or (name == "sellprice" and p.kind == "assembly" and not p.own_sellprice):
                continue                # berekend uit de onderdelen (SA3)
            value = _dec(getattr(p, name))
            if old is not None and value == getattr(old, name):
                continue                # ongewijzigd (oude float-waarde mag meer decimalen hebben)
            if value != value.quantize(Decimal(1).scaleb(-DECIMALS)):
                raise ValidationError(f"{LABEL[name]} heeft meer dan {DECIMALS} decimalen.")
            if name != "weight" and value < 0:
                raise ValidationError(f"{LABEL[name]} kan niet negatief zijn.")
        if p.partsgroup_id:
            cur.execute("SELECT 1 FROM partsgroup WHERE id = %s", (p.partsgroup_id,))
            if not cur.fetchone():
                raise ValidationError("Onbekende groep.")

    def save(self, p: Part) -> int:
        """Nieuw artikel toevoegen of een bestaand wijzigen (IC::save). Geeft het id."""
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            old_raw: Optional[Dict[str, Any]] = None
            old: Optional[Part] = None
            if p.id:
                old, old_raw = self._load(cur, p.id, lock=True)
            if old is not None and old.kind != p.kind and "assembly" in (old.kind, p.kind):
                raise ValidationError("De soort kan niet van of naar samengesteld artikel wijzigen; "
                                      "maak een nieuw artikel.")
            bom_write = self._assembly_values(cur, p, old, old_raw) if p.kind == "assembly" else None
            self._validate(cur, p, old)
            charts = self._chart_ids(cur, p, old)
            taxes = self._tax_ids(cur, p)
            if old is not None:
                if not old.editable:
                    raise ValidationError("Dit artikel kan hier niet gewijzigd worden (soort of project).")
                if old.in_use:
                    if old.kind != p.kind or (old_raw["inventory_accno_id"], old_raw["income_accno_id"],
                                              old_raw["expense_accno_id"]) != (
                            charts["inventory"], charts["income"], charts["expense"]):
                        raise ValidationError("Soort en rekeningen van een gebruikt artikel kunnen niet "
                                              "wijzigen; maak een nieuw artikel en zet dit op vervallen.")
            elif p.kind not in EDITABLE_KINDS:
                raise ValidationError("Alleen artikelen en diensten kunnen aangemaakt worden.")
            partnumber = (p.partnumber or "").strip()
            if not partnumber:
                partnumber = next_number(cur, "partnumber", dateformat=self.config.dateformat)
            values = {
                "partnumber": partnumber, "description": p.description, "unit": p.unit or None,
                "notes": p.notes or None, "barcode": p.barcode or None, "bin": p.bin or None,
                "sellprice": _dec(p.sellprice), "listprice": _dec(p.listprice), "lastcost": _dec(p.lastcost),
                "weight": _dec(p.weight), "rop": _dec(p.rop), "priceupdate": p.priceupdate,
                "obsolete": bool(p.obsolete), "checkinventory": bool(p.checkinventory),
                "partsgroup_id": p.partsgroup_id or 0,
                "inventory_accno_id": charts["inventory"], "income_accno_id": charts["income"],
                "expense_accno_id": charts["expense"], "assembly": p.kind == "assembly",
            }
            if old_raw is None:
                cols = list(values)
                cur.execute(f"""INSERT INTO parts ({', '.join(cols)}, onhand)
                                VALUES ({', '.join(['%s'] * len(cols))}, 0) RETURNING id""",
                            [values[c] for c in cols])
                part_id = cur.fetchone()["id"]
                for tid in taxes:
                    cur.execute("INSERT INTO partstax (parts_id, chart_id) VALUES (%s, %s)", (part_id, tid))
                if bom_write is not None:
                    self._write_bom(cur, part_id, p, None, bom_write)
            else:
                part_id = p.id
                changed = {c: v for c, v in values.items() if not _same(c, old_raw.get(c), v)}
                if changed:
                    cur.execute(f"UPDATE parts SET {', '.join(f'{c} = %s' for c in changed)} WHERE id = %s",
                                [*changed.values(), part_id])
                taxes_changed = sorted(old.taxaccounts) != sorted(set(a for a in p.taxaccounts if a))
                if taxes_changed:
                    cur.execute("DELETE FROM partstax WHERE parts_id = %s", (part_id,))
                    for tid in taxes:
                        cur.execute("INSERT INTO partstax (parts_id, chart_id) VALUES (%s, %s)", (part_id, tid))
                bom_changed = bom_write is not None and self._write_bom(cur, part_id, p, old, bom_write)
                if not (changed or taxes_changed or bom_changed):
                    return part_id          # niets gewijzigd: niets schrijven (ook geen audittrail)
                if old.used_in and (bom_changed or set(changed) & {"sellprice", "listprice", "lastcost", "weight"}):
                    asm.refresh_ancestors(cur, part_id)     # prijzen van producten met dit onderdeel (SA3)
            support.audittrail(cur, self.config, trans_id=part_id, tablename="ic", reference=partnumber,
                               formname=p.kind, action="saved")
            return part_id

    def _assembly_values(self, cur, p: Part, old: Optional[Part],
                         old_raw: Optional[Dict[str, Any]]) -> List[Tuple[int, Decimal]]:
        """Stuklijst controleren (SA2/SA8) en de berekende prijzen in ``p`` zetten (SA3)."""
        rows = [(int(c.parts_id), _dec(c.qty)) for c in p.components]
        old_rows = [(c.parts_id, c.qty) for c in old.components] if old is not None else None
        if rows != old_rows:
            if old is not None and old_raw is not None and _dec(old_raw["onhand"]):
                raise ValidationError("De stuklijst kan niet wijzigen zolang er voorraad van dit artikel is; "
                                      "demonteer of verkoop eerst.")
            bom = asm.check_components(cur, p.id, rows, [c.parts_id for c in (old.components if old else [])])
        else:
            bom = asm.Bom(cur, override=(p.id or 0, rows))
        prices = {k: v.quantize(asm._Q, ROUND_HALF_UP) for k, v in bom.prices(p.id or 0).items()}
        p.listprice, p.lastcost, p.weight = prices["listprice"], prices["lastcost"], prices["weight"]
        if not p.own_sellprice:
            p.sellprice = prices["sellprice"]
        p.computed = prices
        return rows

    def _write_bom(self, cur, part_id: int, p: Part, old: Optional[Part], rows: List[Tuple[int, Decimal]]) -> bool:
        """Stuklijst en "eigen verkoopprijs" wegschrijven; geeft True als er iets geschreven is."""
        wrote = False
        old_comp = {c.parts_id: c for c in old.components} if old is not None else {}
        flags = {int(c.parts_id): bool(c.bom) for c in p.components}
        if old is None or rows != [(c.parts_id, c.qty) for c in old.components]:
            cur.execute("DELETE FROM assembly WHERE aid = %s", (part_id,))
            for cid, qty in rows:
                adj = old_comp[cid].adj if cid in old_comp else True     # Perl zet adj aan bij een nieuwe regel
                cur.execute("INSERT INTO assembly (aid, parts_id, qty, bom, adj) VALUES (%s, %s, %s, %s, %s)",
                            (part_id, cid, qty, flags[cid], adj))
            wrote = True
        else:
            for cid, c in old_comp.items():
                if flags.get(cid, c.bom) != c.bom:
                    cur.execute("UPDATE assembly SET bom = %s WHERE aid = %s AND parts_id = %s",
                                (flags[cid], part_id, cid))
                    wrote = True
        if bool(p.own_sellprice) != bool(old.own_sellprice if old is not None else False):
            ensure_schema(self.conn)
            cur.execute("""INSERT INTO x_assembly_price (parts_id, own_sellprice) VALUES (%s, %s)
                           ON CONFLICT (parts_id) DO UPDATE SET own_sellprice = excluded.own_sellprice""",
                        (part_id, bool(p.own_sellprice)))
            wrote = True
        return wrote

    # ------------------------------------------------------------ verwijderen

    def delete(self, part_id: int) -> None:
        """Ongebruikt artikel verwijderen (IC::delete), met servercontrole (AT6)."""
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            p, _raw = self._load(cur, part_id, lock=True)
            if p.used_in:
                raise ValidationError(f"Artikel {p.partnumber} is een onderdeel van een samengesteld artikel en "
                                      "kan niet verwijderd worden; zet het op vervallen.")
            if p.in_use:
                raise ValidationError(f"Artikel {p.partnumber} wordt gebruikt in facturen of orders en kan niet "
                                      "verwijderd worden; zet het op vervallen.")
            if p.kind not in EDITABLE_KINDS:
                raise ValidationError("Alleen artikelen, diensten en samengestelde artikelen kunnen hier "
                                      "verwijderd worden.")
            if p.kind == "assembly":
                if p.onhand:
                    raise ValidationError("Er is nog voorraad van dit artikel; demonteer eerst.")
                if asm._has_table(cur, "x_assembly_build"):
                    cur.execute("SELECT 1 FROM x_assembly_build WHERE parts_id = %s LIMIT 1", (part_id,))
                    if cur.fetchone():
                        raise ValidationError("Dit artikel is geassembleerd geweest (productiehistorie); "
                                              "zet het op vervallen.")
                    cur.execute("DELETE FROM x_assembly_price WHERE parts_id = %s", (part_id,))
                cur.execute("DELETE FROM assembly WHERE aid = %s", (part_id,))
            for table, col in (("partstax", "parts_id"), ("partsvendor", "parts_id"), ("makemodel", "parts_id"),
                               ("inventory", "parts_id"), ("partscustomer", "parts_id"),
                               ("translation", "trans_id")):
                cur.execute(f"DELETE FROM {table} WHERE {col} = %s", (part_id,))
            support.delete_references(cur, self.config, part_id)
            cur.execute("DELETE FROM parts WHERE id = %s", (part_id,))
            support.audittrail(cur, self.config, trans_id=part_id, tablename="ic", reference=p.partnumber,
                               formname=p.kind, action="deleted")

    # ------------------------------------------------------------ voorraadcorrectie (AT4)

    def adjust_stock(self, part_id: int, *, transdate: date, reason: str,
                     newonhand: Optional[Any] = None, qty: Optional[Any] = None) -> Dict[str, Any]:
        """Voorraad corrigeren: nieuw aantal (``newonhand``) of verschil (``qty``), met reden.

        Geen grootboekboeking (zoals Perl), wel vastgelegd in ``x_stock_adjustment``."""
        if (newonhand is None) == (qty is None):
            raise ValidationError("Geef een nieuw aantal of een verschil.")
        if not (reason or "").strip():
            raise ValidationError("Vul een reden in.")
        if not transdate:
            raise ValidationError("Vul een datum in.")
        ensure_schema(self.conn)
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            p, raw = self._load(cur, part_id, lock=True)
            closedto = _closedto(_db.get_defaults(cur, "closedto"), self.config.dateformat)
            if closedto and _num(transdate) <= closedto:          # F41: zoals tijdkaarten (timecards._check)
                raise ClosedPeriodError(asm.CLOSED)
            if p.kind not in ("part", "labor", "assembly"):
                raise ValidationError("Alleen artikelen met voorraad kunnen gecorrigeerd worden.")
            old = _dec(raw["onhand"])
            if newonhand is not None:
                new = _dec(newonhand)
                diff = new - old
            else:
                diff = _dec(qty)
                new = old + diff
            for v in (new, diff):
                if v != v.quantize(Decimal(1).scaleb(-DECIMALS)):
                    raise ValidationError(f"Aantal heeft meer dan {DECIMALS} decimalen.")
            if not diff:
                raise ValidationError("Het aantal is niet gewijzigd.")
            _name, employee_id = _db.get_employee(cur, self.config.short_login)
            cur.execute("UPDATE parts SET onhand = %s WHERE id = %s", (new, part_id))
            cur.execute("""INSERT INTO x_stock_adjustment (parts_id, transdate, oldonhand, qty, newonhand, reason,
                                                           employee_id, login)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (part_id, transdate, old, diff, new, reason.strip(), employee_id or None,
                         self.config.login))
            adj_id = cur.fetchone()["id"]
            support.audittrail(cur, self.config, trans_id=part_id, tablename="ic", reference=p.partnumber,
                               formname="stock", action="adjusted")
            return {"id": adj_id, "oldonhand": old, "qty": diff, "newonhand": new}

    def stock_adjustments(self, part_id: Optional[int] = None, datefrom: Optional[date] = None,
                          dateto: Optional[date] = None) -> List[Dict[str, Any]]:
        ensure_schema(self.conn)
        where, params = ["1 = 1"], []
        if part_id:
            where.append("a.parts_id = %s")
            params.append(part_id)
        if datefrom:
            where.append("a.transdate >= %s")
            params.append(datefrom)
        if dateto:
            where.append("a.transdate <= %s")
            params.append(dateto)
        with _db.cursor(self.conn) as cur:
            cur.execute(f"""SELECT a.*, p.partnumber, p.description, e.name AS employee
                            FROM x_stock_adjustment a JOIN parts p ON (p.id = a.parts_id)
                            LEFT JOIN employee e ON (e.id = a.employee_id)
                            WHERE {' AND '.join(where)} ORDER BY a.transdate, a.id""", params)
            return cur.fetchall()

    # ------------------------------------------------------------ prijshistorie (IC::history)

    def history(self, part_id: int, side: str = "sales") -> List[Dict[str, Any]]:
        """Prijzen van een artikel in facturen, orders en offertes (sales/purchases), nieuwste eerst.

        Eén basis (F39): de ingevoerde prijs vóór korting, omgerekend naar de standaardvaluta (``fxsellprice`` ×
        koers; een oude SL-factuurregel zonder ``fxsellprice``: ``sellprice``, die al in de standaardvaluta staat).
        Perl mengde netto factuurprijzen met bruto orderprijzen (INV-22)."""
        if side not in ("sales", "purchases"):
            raise ValueError("side moet 'sales' of 'purchases' zijn")
        vc, arap, sign = ("customer", "ar", 1) if side == "sales" else ("vendor", "ap", -1)
        order_types = ("sales_order", "sales_quotation") if side == "sales" else ("purchase_order",
                                                                               "request_quotation")
        with _db.cursor(self.conn) as cur:
            precision = _db.get_precision(cur)
            cur.execute(f"""SELECT '{arap}' AS module, 'invoice' AS type, a.id, a.invnumber AS number,
                                   a.transdate, c.name, i.description, (i.qty * {sign})::numeric AS qty,
                                   {_INVOICE_PRICE} AS sellprice, i.fxsellprice::numeric AS fxprice,
                                   i.discount::numeric AS discount, a.curr
                            FROM invoice i JOIN {arap} a ON (a.id = i.trans_id)
                            JOIN {vc} c ON (c.id = a.{vc}_id)
                            WHERE i.parts_id = %(id)s
                            UNION ALL
                            SELECT 'oe', CASE WHEN o.quotation THEN %(q)s ELSE %(o)s END, o.id,
                                   CASE WHEN o.quotation THEN o.quonumber ELSE o.ordnumber END,
                                   o.transdate, c.name, i.description, i.qty::numeric,
                                   {_ORDER_PRICE.replace("a.", "o.")}, i.sellprice::numeric,
                                   i.discount::numeric, o.curr
                            FROM orderitems i JOIN oe o ON (o.id = i.trans_id)
                            JOIN {vc} c ON (c.id = o.{vc}_id)
                            WHERE i.parts_id = %(id)s
                            ORDER BY 5 DESC, 3 DESC""",
                        {"id": part_id, "o": order_types[0], "q": order_types[1]})
            return [_round_price(r, precision) for r in cur.fetchall()]

    # ------------------------------------------------------------ zoeken (IC::all_parts)

    def search(self, s: Optional[PartSearch] = None) -> List[Dict[str, Any]]:
        s = s or PartSearch()
        where, params = self._part_where(s)
        with _db.cursor(self.conn) as cur:
            if not s.transactions:
                cur.execute(f"""SELECT p.id, p.partnumber, p.description, p.onhand::numeric AS onhand, p.unit,
                                       p.bin, p.sellprice::numeric AS sellprice, p.listprice::numeric AS listprice,
                                       p.lastcost::numeric AS lastcost, p.avgcost::numeric AS avgcost,
                                       p.rop::numeric AS rop, p.weight::numeric AS weight, p.priceupdate,
                                       p.barcode, p.notes, p.obsolete, p.assembly, p.inventory_accno_id,
                                       p.income_accno_id, p.expense_accno_id, pg.partsgroup,
                                       c1.accno AS inventory, c2.accno AS income, c3.accno AS expense,
                                       (SELECT string_agg(c.accno, ' ' ORDER BY c.accno) FROM partstax pt
                                        JOIN chart c ON (c.id = pt.chart_id) WHERE pt.parts_id = p.id) AS tax
                                FROM parts p
                                LEFT JOIN partsgroup pg ON (pg.id = p.partsgroup_id)
                                LEFT JOIN chart c1 ON (c1.id = p.inventory_accno_id)
                                LEFT JOIN chart c2 ON (c2.id = p.income_accno_id)
                                LEFT JOIN chart c3 ON (c3.id = p.expense_accno_id)
                                WHERE {' AND '.join(where)}
                                ORDER BY {self._order(s, False)}""", params)
                out = cur.fetchall()
            else:
                out = self._transactions(cur, s, where, params)
        for r in out:
            r["kind"] = kind_of(r)
        return out

    SORTS = {"partnumber": "lower(p.partnumber)", "description": "lower(p.description)", "onhand": "p.onhand",
             "sellprice": "p.sellprice", "partsgroup": "lower(pg.partsgroup)", "bin": "p.bin",
             "priceupdate": "p.priceupdate", "lastcost": "p.lastcost", "unit": "p.unit"}
    TSORTS = {"partnumber": "lower(partnumber)", "description": "lower(description)", "transdate": "transdate",
              "name": "lower(name)", "number": "lower(number)", "qty": "qty", "sellprice": "sellprice"}

    def _order(self, s: PartSearch, transactions: bool) -> str:
        sorts = self.TSORTS if transactions else self.SORTS
        key = sorts.get(s.sort) or sorts["partnumber"]
        direction = " DESC" if s.desc else ""
        tie = "transdate, trans_id, id" if transactions else "lower(p.partnumber), p.id"
        return f"{key}{direction} NULLS LAST, {tie}"

    def _part_where(self, s: PartSearch) -> Tuple[List[str], List[Any]]:
        where: List[str] = ["1 = 1"]
        params: List[Any] = []
        for name in ("partnumber", "barcode"):
            v = getattr(s, name)
            if v:
                where.append(f"lower(p.{name}) LIKE %s")
                params.append(like(v.lower()))
        if s.description and not s.transactions:
            where.append("lower(p.description) LIKE %s")
            params.append(like(s.description.lower()))
        kinds = {
            "part": "p.inventory_accno_id > 0 AND p.income_accno_id > 0 AND NOT p.assembly",
            "service": "NOT p.assembly AND p.inventory_accno_id IS NULL AND p.income_accno_id > 0",
            "labor": "p.inventory_accno_id > 0 AND p.income_accno_id IS NULL",
            "assembly": "p.assembly",
            "kit": "p.inventory_accno_id IS NULL AND p.income_accno_id IS NULL AND p.expense_accno_id IS NULL",
        }
        if s.kind in kinds:
            where.append(kinds[s.kind])
        status = {
            "active": "NOT p.obsolete",
            "obsolete": "p.obsolete",
            "onhand": "p.onhand > 0",
            "short": "p.onhand < p.rop",
            "orphaned": """p.onhand = 0
                           AND NOT EXISTS (SELECT 1 FROM invoice i WHERE i.parts_id = p.id)
                           AND NOT EXISTS (SELECT 1 FROM orderitems o WHERE o.parts_id = p.id)
                           AND NOT EXISTS (SELECT 1 FROM assembly a WHERE a.parts_id = p.id)
                           AND NOT EXISTS (SELECT 1 FROM jcitems j WHERE j.parts_id = p.id)""",
        }
        if s.status in status:
            where.append(status[s.status])
        if s.partsgroup_id:
            where.append("p.partsgroup_id = %s")
            params.append(s.partsgroup_id)
        return where, params

    def _transactions(self, cur, s: PartSearch, where: List[str], params: List[Any]) -> List[Dict[str, Any]]:
        """Regels uit facturen (bought/sold), orders (onorder/ordered) en offertes (rfq/quoted)."""
        parts: List[str] = []
        allp: List[Any] = []

        def common(alias: str, datecol: str) -> Tuple[List[str], List[Any]]:
            w, prm = list(where), list(params)
            if s.datefrom:
                w.append(f"{alias}.{datecol} >= %s")
                prm.append(s.datefrom)
            if s.dateto:
                w.append(f"{alias}.{datecol} <= %s")
                prm.append(s.dateto)
            if s.description:
                w.append("lower(i.description) LIKE %s")
                prm.append(like(s.description.lower()))
            return w, prm

        cols = """p.id, p.partnumber, i.description, i.serialnumber, i.unit, {price} AS sellprice,
                  {fxprice} AS fxprice, i.discount::numeric AS discount, pg.partsgroup, p.inventory_accno_id, p.income_accno_id,
                  p.expense_accno_id, p.assembly, p.obsolete, i.trans_id, ct.name, ct.id AS vc_id,
                  e.name AS employee, a.curr, a.transdate"""
        for kind, table, vc, sign in (("bought", "ap", "vendor", -1), ("sold", "ar", "customer", 1)):
            if kind not in s.transactions:
                continue
            w, prm = common("a", "transdate")
            w.append("NOT coalesce(i.assemblyitem, false)")
            if s.open and not s.closed:
                w.append("a.amount <> a.paid")
            elif s.closed and not s.open:
                w.append("a.amount = a.paid")
            elif not s.open and not s.closed:
                w.append("false")
            parts.append(f"""SELECT {cols.format(price=_INVOICE_PRICE, fxprice="i.fxsellprice::numeric")},
                                    (i.qty * {sign})::numeric AS qty, a.invnumber AS number,
                                    '{table}' AS module, 'invoice' AS type, '{vc}' AS vc
                             FROM invoice i JOIN parts p ON (p.id = i.parts_id)
                             JOIN {table} a ON (a.id = i.trans_id)
                             JOIN {vc} ct ON (ct.id = a.{vc}_id)
                             LEFT JOIN partsgroup pg ON (pg.id = p.partsgroup_id)
                             LEFT JOIN employee e ON (e.id = a.employee_id)
                             WHERE {' AND '.join(w)}""")
            allp += prm
        for kind, quotation, vc, typ, numcol in (
                ("ordered", False, "customer", "sales_order", "ordnumber"),
                ("onorder", False, "vendor", "purchase_order", "ordnumber"),
                ("quoted", True, "customer", "sales_quotation", "quonumber"),
                ("rfq", True, "vendor", "request_quotation", "quonumber")):
            if kind not in s.transactions:
                continue
            w, prm = common("a", "transdate")
            w.append("a.quotation" if quotation else "NOT a.quotation")
            w.append(f"a.{vc}_id > 0")
            if s.open and not s.closed:
                w.append("NOT a.closed")
            elif s.closed and not s.open:
                w.append("a.closed")
            elif not s.open and not s.closed:
                w.append("false")
            parts.append(f"""SELECT {cols.format(price=_ORDER_PRICE, fxprice="i.sellprice::numeric")},
                                    i.qty::numeric AS qty, a.{numcol} AS number,
                                    'oe' AS module, '{typ}' AS type, '{vc}' AS vc
                             FROM orderitems i JOIN parts p ON (p.id = i.parts_id)
                             JOIN oe a ON (a.id = i.trans_id)
                             JOIN {vc} ct ON (ct.id = a.{vc}_id)
                             LEFT JOIN partsgroup pg ON (pg.id = p.partsgroup_id)
                             LEFT JOIN employee e ON (e.id = a.employee_id)
                             WHERE {' AND '.join(w)}""")
            allp += prm
        if not parts:
            return []
        cur.execute(f"SELECT * FROM ({' UNION ALL '.join(parts)}) x ORDER BY {self._order(s, True)}", allp)
        rows = cur.fetchall()
        precision = _db.get_precision(cur)
        return [_round_price(r, precision) for r in rows]


#: prijs van een regel in de standaardvaluta, vóór korting (F39): factuur ``fxsellprice`` × koers (oude SL-regel
#: zonder ``fxsellprice``: ``sellprice``, al in de standaardvaluta), order ``sellprice`` × koers; alias ``a``
_RATE = "coalesce(nullif(a.exchangerate, 0), 1)"
_INVOICE_PRICE = (f"(CASE WHEN i.fxsellprice IS NULL THEN i.sellprice ELSE i.fxsellprice * {_RATE} END)::numeric")
_ORDER_PRICE = f"(i.sellprice * {_RATE})::numeric"


def _round_price(r: Dict[str, Any], precision: int) -> Dict[str, Any]:
    """Omgerekende prijs afronden zoals IS.pm (op de decimalen van de ingevoerde prijs, minstens ``precision``)."""
    fx, price = r.pop("fxprice", None), r.get("sellprice")
    if price is not None:
        places = max(precision, -_dec(fx).normalize().as_tuple().exponent if fx is not None else 0)
        r["sellprice"] = _dec(price).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return r


#: Nederlandse namen van soorten en velden in meldingen
SOORT = {"part": "artikel", "service": "dienst", "labor": "loon/overhead", "assembly": "samengesteld artikel",
         "kit": "kit"}
LABEL = {"inventory": "voorraadrekening", "income": "opbrengstrekening", "expense": "kostenrekening",
         "sellprice": "Verkoopprijs", "listprice": "Lijstprijs", "lastcost": "Laatste inkoopprijs",
         "weight": "Gewicht", "rop": "Bestelniveau"}


def _same(col: str, old: Any, new: Any) -> bool:
    """Zelfde waarde in de database als in het formulier (float/NULL/0/'' gelijk behandeld)."""
    if col in ("sellprice", "listprice", "lastcost", "weight", "rop"):
        return to_decimal(old) == to_decimal(new)
    if col == "partsgroup_id":
        return (old or 0) == (new or 0)
    if col in ("obsolete", "checkinventory", "assembly"):
        return bool(old) == bool(new)
    if col in ("inventory_accno_id", "income_accno_id", "expense_accno_id"):
        return (old or None) == (new or None)
    if col == "priceupdate":
        return old == new
    return _text(old) == _text(new)


def _text(v: Any) -> str:
    """Tekst zoals het scherm hem terugstuurt: regeleinden als \\n, zonder witruimte eromheen
    (een browser stuurt CRLF; het nummer wordt bij opslaan bijgesneden, zoals Perl)."""
    return (v or "").replace("\r\n", "\n").strip()
