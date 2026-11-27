"""Een back-up van Run my Accounts (RMA) of een verwante ledger123-installatie inlezen (RMA_IMPORT_ANALYSE.md §9–§10,
RI1–RI7, BK1–BK8).

RMA is afgetakt van SQL-Ledger 2.8.10 en heeft daarna een eigen schemareeks (2.8.11 … 2.8.52) plus ledger123-tabellen.
Route: alles wat niet in het originele 2.8.10-schema staat (``slschema/ref-2.8.10.txt``, RI1) is **extra** en gaat
ongewijzigd naar schema ``rma`` (RI2); daarna draait de bestaande keten 2.8.10 → 3.2.4 (RI4) en worden de gegevens
met een PyBalans-plek teruggezet (RI5). Vóór en na wordt gecontroleerd (RI6).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from importlib import resources
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

import psycopg
from psycopg import sql as psql

from .errors import LedgerError

#: versie waarop RMA is afgetakt (de keten begint hier)
BASE_VERSION = "2.8.10"
SCHEMA = "rma"


@dataclass(frozen=True)
class Reference:
    """Origineel SQL-Ledger-2.8.10-schema."""
    tables: Dict[str, FrozenSet[str]]
    sequences: FrozenSet[str]
    functions: FrozenSet[str]          # namen; een trigger met dezelfde naam is ook origineel


@lru_cache(maxsize=1)
def reference() -> Reference:
    text = resources.files("sqlledger.slschema").joinpath("ref-2.8.10.txt").read_text(encoding="utf-8")
    tables: Dict[str, FrozenSet[str]] = {}
    seqs: Set[str] = set()
    funcs: Set[str] = set()
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0].startswith("#"):
            continue
        if parts[0] == "table":
            tables[parts[1]] = frozenset(parts[2:])
        elif parts[0] == "sequence":
            seqs.add(parts[1])
        elif parts[0] == "function":
            funcs.add(parts[1])
    return Reference(tables, frozenset(seqs), frozenset(funcs))


@dataclass
class Extras:
    """Wat een database méér heeft dan het originele 2.8.10-schema (alleen schema ``public``)."""
    tables: List[str] = field(default_factory=list)
    columns: Dict[str, List[str]] = field(default_factory=dict)       # gewone tabel → extra kolommen
    sequences: List[str] = field(default_factory=list)                # los of bij een extra kolom (niet bij een
                                                                      # extra tabel: die gaat met de tabel mee)
    functions: List[str] = field(default_factory=list)                # naam(argumenten)
    triggers: List[Tuple[str, str]] = field(default_factory=list)     # (tabel, trigger)
    views: List[str] = field(default_factory=list)
    types: List[str] = field(default_factory=list)                    # ENUM, domein, los samengesteld type
    missing: Dict[str, List[str]] = field(default_factory=dict)       # originele tabel → ontbrekende kolommen

    def __bool__(self) -> bool:
        return bool(self.tables or self.columns or self.sequences or self.functions or self.triggers
                    or self.views or self.types)


def find_extras(conn: psycopg.Connection) -> Extras:
    """Vergelijk schema ``public`` met de referentie (RI1). Objecten van een PostgreSQL-extensie tellen niet mee."""
    ref = reference()
    ex = Extras()
    cols: Dict[str, Set[str]] = {}
    for table, column in conn.execute(
            "SELECT c.table_name, c.column_name FROM information_schema.columns c "
            "JOIN information_schema.tables t USING (table_schema, table_name) "
            "WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'"):
        cols.setdefault(table, set()).add(column)
    for table in sorted(cols):
        if table not in ref.tables:
            ex.tables.append(table)
            continue
        extra = sorted(cols[table] - ref.tables[table])
        if extra:
            ex.columns[table] = extra
        gone = sorted(ref.tables[table] - cols[table])
        if gone:
            ex.missing[table] = gone
    extra_tables = set(ex.tables)
    for seq, owner in conn.execute(
            "SELECT s.relname, o.relname FROM pg_class s "
            "LEFT JOIN pg_depend d ON d.objid = s.oid AND d.classid = 'pg_class'::regclass AND d.deptype IN ('a', 'i') "
            "LEFT JOIN pg_class o ON o.oid = d.refobjid "
            "WHERE s.relkind = 'S' AND s.relnamespace = 'public'::regnamespace ORDER BY 1"):
        if seq not in ref.sequences and owner not in extra_tables:
            ex.sequences.append(seq)
    ex.views = [r[0] for r in conn.execute(
        "SELECT c.relname FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('v', 'm') "
        "ORDER BY 1")]
    ex.functions = [r[0] for r in conn.execute(
        "SELECT p.oid::regprocedure::text FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace "
        "AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e') "
        "AND NOT (p.proname = ANY(%s)) ORDER BY 1", (sorted(ref.functions),))]
    ex.triggers = [(r[0], r[1]) for r in conn.execute(
        "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
        "WHERE NOT t.tgisinternal AND c.relnamespace = 'public'::regnamespace "
        "AND NOT (t.tgname = ANY(%s)) ORDER BY 1, 2", (sorted(ref.functions),))]
    ex.types = [r[0] for r in conn.execute(
        "SELECT t.typname FROM pg_type t WHERE t.typnamespace = 'public'::regnamespace "
        "AND (t.typtype IN ('e', 'd') OR (t.typtype = 'c' AND (SELECT relkind FROM pg_class WHERE oid = t.typrelid) = 'c')) "
        "AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = t.oid AND d.deptype = 'e') ORDER BY 1")]
    return ex


class RmaImportError(LedgerError):
    pass


#: tabellen die de keten 2.8.10 → 3.2.4 weggooit en opnieuw opbouwt (Pg-upgrade-2.9.x) of waarvan ze rijen omzet
#: (``defaults``: fxgain/fxloss → één fxgainloss in 3.2.0→3.2.1, findings ADM-17); hun bronrijen gaan altijd als
#: kopie mee naar ``rma`` (BK3), net als ``inventory`` (RM11)
REBUILT = ("curr", "customer", "defaults", "employee", "exchangerate", "pay_trans", "reference", "vendor")
ALWAYS_COPY = REBUILT + ("inventory",)


@lru_cache(maxsize=1)
def target_tables() -> FrozenSet[str]:
    """Tabelnamen van het 3.2.4-schema (``slschema/Pg-tables.sql``)."""
    text = resources.files("sqlledger.slschema").joinpath("Pg-tables.sql").read_text(encoding="utf-8")
    return frozenset(m.lower() for m in re.findall(r"(?im)^create\s+table\s+(\w+)", text))


@dataclass
class Snapshot:
    """RI6: proefbalans per rekeningnummer, openstaand per factuur (2 decimalen) en rijen per tabel."""
    balance: Dict[str, Decimal]
    open_items: Dict[Tuple[str, int], Decimal]
    counts: Dict[str, int]


def snapshot(conn: psycopg.Connection, schema: str = "public") -> Snapshot:
    balance = {r[0]: r[1] for r in conn.execute(
        "SELECT c.accno, round(sum(a.amount::numeric), 2) FROM acc_trans a JOIN chart c ON c.id = a.chart_id "
        "GROUP BY c.accno")}
    open_items: Dict[Tuple[str, int], Decimal] = {}
    for table in ("ar", "ap"):
        for i, amount in conn.execute(psql.SQL("SELECT id, round(coalesce(amount, 0)::numeric - coalesce(paid, 0)::numeric, 2) "
                                               "FROM {}").format(psql.Identifier(table))):
            open_items[(table, i)] = amount
    return Snapshot(balance, open_items, table_counts(conn, schema))


def table_counts(conn: psycopg.Connection, schema: str) -> Dict[str, int]:
    out = {}
    for (table,) in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s "
                                 "AND table_type = 'BASE TABLE' ORDER BY 1", (schema,)).fetchall():
        out[table] = conn.execute(psql.SQL("SELECT count(*) FROM {}.{}").format(
            psql.Identifier(schema), psql.Identifier(table))).fetchone()[0]
    return out


@dataclass
class Prepared:
    source_version: str
    extras: Extras
    before: Snapshot
    copies: List[str]                         # gewone tabellen met een volledige kopie in rma (BK3)
    objects: List[Tuple[str, str, str]]       # (soort, naam, tabel) bewaard als tekst in rma.object


def _ident(name: str) -> psql.Composable:
    return psql.Identifier(name)


def prepare(conn: psycopg.Connection, source_version: str) -> Prepared:
    """RI2/RI4: alles buiten het originele 2.8.10-schema naar schema ``rma``; versie op 2.8.10. In één transactie."""
    with conn.transaction():
        ex = find_extras(conn)
        clash = sorted(t for t in ex.tables if t in target_tables() or t.startswith("x_"))
        if clash:                                                                      # BK4
            tables = ", ".join(clash)
            raise RmaImportError(f"Extra tabel met een naam die PyBalans zelf gebruikt: {tables}.")
        if conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (SCHEMA,)).fetchone():
            raise RmaImportError("De back-up heeft al een schema rma.")
        before = snapshot(conn)
        conn.execute("CREATE SCHEMA rma")
        conn.execute("CREATE TABLE rma.object (kind text NOT NULL, name text NOT NULL, tabel text, "
                     "definition text NOT NULL)")
        objects: List[Tuple[str, str, str]] = []

        def keep(kind: str, name: str, table: Optional[str], definition: str) -> None:
            conn.execute("INSERT INTO rma.object (kind, name, tabel, definition) VALUES (%s, %s, %s, %s)",
                         (kind, name, table, definition))
            objects.append((kind, name, table or ""))

        extra_tables = set(ex.tables)
        # views (2.8.10 heeft er geen): tekst bewaren, weg
        for (view,) in conn.execute("SELECT c.relname FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace "
                                    "AND c.relkind IN ('v', 'm') ORDER BY 1").fetchall():
            definition = conn.execute("SELECT pg_get_viewdef(%s::regclass, true)", (f'public."{view}"',)).fetchone()[0]
            keep("view", view, None, definition)
            conn.execute(psql.SQL("DROP VIEW IF EXISTS public.{} CASCADE").format(_ident(view)))
        # triggers die niet origineel zijn (BK2): tekst bewaren, weg (ook op extra tabellen: rma is een archief)
        for table, trig, definition in conn.execute(
                "SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "WHERE NOT t.tgisinternal AND c.relnamespace = 'public'::regnamespace "
                "AND (c.relname = ANY(%s) OR NOT (t.tgname = ANY(%s))) ORDER BY 1, 2",
                (sorted(extra_tables), sorted(reference().functions))).fetchall():
            keep("trigger", trig, table, definition)
            conn.execute(psql.SQL("DROP TRIGGER {} ON public.{}").format(_ident(trig), _ident(table)))
        # foreign keys tussen een extra en een origineel deel (BK2): tekst bewaren, weg; binnen rma blijven ze
        for table, con, definition, own_extra, ref_extra, cols in conn.execute(
                "SELECT c.relname, k.conname, pg_get_constraintdef(k.oid), c.relname = ANY(%(t)s), "
                "f.relname = ANY(%(t)s), (SELECT array_agg(attname::text) FROM pg_attribute "
                "  WHERE attrelid = k.conrelid AND attnum = ANY(k.conkey)) "
                "FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid JOIN pg_class f ON f.oid = k.confrelid "
                "WHERE k.contype = 'f' AND c.relnamespace = 'public'::regnamespace ORDER BY 1, 2",
                {"t": sorted(extra_tables)}).fetchall():
            on_extra_column = bool(set(cols or []) & set(ex.columns.get(table, [])))
            if own_extra != ref_extra or on_extra_column:
                keep("constraint", con, table, definition)
                conn.execute(psql.SQL("ALTER TABLE public.{} DROP CONSTRAINT {}").format(_ident(table), _ident(con)))
        # losse extra sequences (ook die bij een extra kolom horen) naar rma, met hun stand
        for seq in ex.sequences:
            conn.execute(psql.SQL("ALTER SEQUENCE public.{} OWNED BY NONE").format(_ident(seq)))
            conn.execute(psql.SQL("ALTER SEQUENCE public.{} SET SCHEMA rma").format(_ident(seq)))
        # extra tabellen met inhoud, indexen en eigen sequences naar rma
        for table in ex.tables:
            conn.execute(psql.SQL("ALTER TABLE public.{} SET SCHEMA rma").format(_ident(table)))
        # volledige kopie van gewone tabellen met extra kolommen en van wat de keten herbouwt (BK3, RM11)
        copies = sorted((set(ex.columns) | set(ALWAYS_COPY)) & set(reference().tables)
                        & set(before.counts))
        for table in copies:
            conn.execute(psql.SQL("CREATE TABLE rma.{} AS TABLE public.{}").format(_ident(table), _ident(table)))
        for table, cols in ex.columns.items():
            for col in cols:
                conn.execute(psql.SQL("ALTER TABLE public.{} DROP COLUMN {}").format(_ident(table), _ident(col)))
        if "inventory" in copies:
            conn.execute("DELETE FROM public.inventory")                                  # RM11: tot RM4 leeg
        # functies (BK2): tekst bewaren, weg; ENUM- en andere typen naar rma
        for func in ex.functions:
            definition = conn.execute("SELECT pg_get_functiondef(%s::regprocedure)", (f"public.{func}",)).fetchone()[0]
            keep("function", func, None, definition)
            conn.execute(psql.SQL("DROP FUNCTION public.{}").format(psql.SQL(_qualify_args(func))))
        for typ in ex.types:
            conn.execute(psql.SQL("ALTER TYPE public.{} SET SCHEMA rma").format(_ident(typ)))
        conn.execute("UPDATE defaults SET fldvalue = %s WHERE fldname = 'version'", (BASE_VERSION,))
    return Prepared(source_version, ex, before, copies, objects)


def _qualify_args(func: str) -> str:
    """``naam(argumenten)`` uit ``regprocedure``: naam als identifier, argumenten ongewijzigd (PostgreSQL-typen)."""
    name, args = func.split("(", 1)
    return '"' + name.strip('"').replace('"', '""') + '"(' + args


# ------------------------------------------------------------------ terugzetten (RI5, RM3)

def _has(conn: psycopg.Connection, table: str, column: str, schema: str = SCHEMA) -> bool:
    return conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
                        "AND column_name = %s", (schema, table, column)).fetchone() is not None


def _count(conn: psycopg.Connection, query: str, *args: Any) -> int:
    return int(conn.execute(query, args).fetchone()[0] or 0)


def restore_values(conn: psycopg.Connection, prep: Prepared) -> Dict[str, Any]:
    """RI5: RMA-gegevens met een PyBalans-plek terugzetten uit de kopieën in ``rma``; wat geen plek heeft, blijft in
    ``rma`` en wordt geteld. Geeft ``{"done": [...], "kept": [...], "warnings": [...]}`` met (sleutel, aantal)."""
    done: List[Dict[str, Any]] = []
    kept: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    def item(lst: List[Dict[str, Any]], key: str, n: int, **extra: Any) -> None:
        if n:
            lst.append({"key": key, "count": n, **extra})

    with conn.transaction():
        # vaste kostenrekening per leverancier → x_vendor_default (IU3), alleen als de rekening er nog is
        if _has(conn, "vendor", "expense_accno_id"):
            n = conn.execute(
                "INSERT INTO x_vendor_default (vendor_id, expense_chart_id, login) "
                "SELECT v.id, v.expense_accno_id, 'rma-import' FROM rma.vendor v "
                "JOIN public.vendor p ON p.id = v.id JOIN chart c ON c.id = v.expense_accno_id "
                "WHERE v.expense_accno_id IS NOT NULL ON CONFLICT (vendor_id) DO NOTHING").rowcount
            item(done, "vendor_expense_account", n)
            item(kept, "vendor_expense_account_missing",
                 _count(conn, "SELECT count(*) FROM rma.vendor v WHERE v.expense_accno_id IS NOT NULL "
                              "AND NOT EXISTS (SELECT 1 FROM chart c WHERE c.id = v.expense_accno_id)"))
        # boekjaar (JA1): eindmaand uit het lopende (anders laatste) boekjaar van RMA, als PyBalans er nog geen heeft
        if conn.execute("SELECT 1 FROM information_schema.tables WHERE table_schema = 'rma' "
                        "AND table_name = 'financial_year'").fetchone():
            r = conn.execute("SELECT end_date FROM rma.financial_year ORDER BY (status::text = 'CURRENT_FINANCIAL_YEAR') "
                             "DESC, end_date DESC LIMIT 1").fetchone()
            current = conn.execute("SELECT fldvalue FROM defaults WHERE fldname = 'yearend'").fetchone()
            if r and r[0] and not (current and (current[0] or "").strip()):
                if current:
                    conn.execute("UPDATE defaults SET fldvalue = %s WHERE fldname = 'yearend'", (str(r[0].month),))
                else:
                    conn.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('yearend', %s)", (str(r[0].month),))
                item(done, "fiscal_year_end", 1, month=r[0].month)
            item(kept, "financial_years", _count(conn, "SELECT count(*) FROM rma.financial_year"))
        # door de keten zelf al omgezet: tellen ter controle (IBAN/BIC → bank, medewerkeradres → address, precisie)
        for vc in ("customer", "vendor", "employee"):
            if _has(conn, vc, "iban"):
                src = _count(conn, f"SELECT count(*) FROM rma.{vc} WHERE coalesce(iban, '') <> '' OR coalesce(bic, '') <> ''")
                now = _count(conn, f"SELECT count(*) FROM rma.{vc} v JOIN bank b ON b.id = v.id "
                                   "WHERE coalesce(b.iban, '') = coalesce(v.iban, '') AND coalesce(b.bic, '') = coalesce(v.bic, '') "
                                   "AND (coalesce(v.iban, '') <> '' OR coalesce(v.bic, '') <> '')")
                item(done, f"{vc}_iban", now)
                if now != src:
                    warnings.append({"key": f"{vc}_iban_lost", "count": src - now})
        if _has(conn, "employee", "address1"):
            src = _count(conn, "SELECT count(*) FROM rma.employee WHERE coalesce(address1, '') || coalesce(city, '') <> ''")
            now = _count(conn, "SELECT count(*) FROM rma.employee e JOIN address a ON a.trans_id = e.id "
                               "WHERE coalesce(e.address1, '') || coalesce(e.city, '') <> '' "
                               "AND coalesce(a.address1, '') = coalesce(e.address1, '') AND coalesce(a.city, '') = coalesce(e.city, '')")
            item(done, "employee_address", now)
            if now != src:
                warnings.append({"key": "employee_address_lost", "count": src - now})
        if _has(conn, "employee", "role"):
            item(kept, "employee_role", _count(conn, "SELECT count(*) FROM rma.employee WHERE coalesce(role, '') <> ''"))
        if _has(conn, "curr", "precision"):
            item(done, "currency_precision",
                 _count(conn, "SELECT count(*) FROM rma.curr r JOIN curr c ON c.curr = r.curr AND c.prec = r.precision"))
        # wisselkoersen: 3.2.4 kent één koers per dag (buy, anders sell; ADM-15); waar buy ≠ sell blijft sell in rma
        if _has(conn, "exchangerate", "sell"):
            item(kept, "exchangerate_sell_differs",
                 _count(conn, "SELECT count(*) FROM rma.exchangerate WHERE coalesce(buy, 0) <> 0 "
                              "AND coalesce(sell, 0) <> 0 AND buy <> sell"))
        # koersverschillen: één rekening in 3.2.x (findings ADM-17)
        fx = dict(conn.execute("SELECT fldname, fldvalue FROM rma.defaults WHERE fldname IN "
                               "('fxgain_accno_id', 'fxloss_accno_id')").fetchall())
        if fx.get("fxloss_accno_id") and fx.get("fxloss_accno_id") != fx.get("fxgain_accno_id"):
            accnos = dict(conn.execute("SELECT id::text, accno || coalesce(' ' || description, '') FROM chart "
                                       "WHERE id::text = ANY(%s)",
                                       ([fx.get("fxgain_accno_id") or "", fx["fxloss_accno_id"]],)).fetchall())
            warnings.append({"key": "fx_loss_account_merged", "count": 1,
                             "gain": accnos.get(fx.get("fxgain_accno_id") or "", ""),
                             "loss": accnos.get(fx["fxloss_accno_id"], "")})
        # zonder PyBalans-plek (blijven in rma, RM2)
        for table, column, key in (("customer", "income_accno_id", "customer_income_account"),
                                   ("customer", "payment_clearing_accno_id", "customer_clearing_account"),
                                   ("vendor", "payment_clearing_accno_id", "vendor_clearing_account"),
                                   ("customer", "reminderstop", "customer_reminderstop"),
                                   ("vendor", "reminderstop", "vendor_reminderstop"),
                                   ("vendor", "early_payment_discount", "vendor_early_payment_discount"),
                                   ("bank", "qriban", "bank_qriban")):
            if _has(conn, table, column):
                cond = f"{column}" if column == "reminderstop" else f"coalesce({column}::text, '') NOT IN ('', '0')"
                item(kept, key, _count(conn, f"SELECT count(*) FROM rma.{table} WHERE {cond}"))
        if "inventory" in prep.copies:
            item(kept, "inventory_rows", prep.before.counts.get("inventory", 0))
    return {"done": done, "kept": kept, "warnings": warnings}


class RmaCheckError(RmaImportError):
    """RI6: proefbalans of openstaand verschilt na het inlezen; het rapport staat in ``report``."""

    def __init__(self, message: str, report: Dict[str, Any]):
        super().__init__(message)
        self.report = report


def _diff(before: Dict[Any, Decimal], after: Dict[Any, Decimal]) -> List[Tuple[Any, Optional[Decimal], Optional[Decimal]]]:
    out = []
    for key in sorted(set(before) | set(after), key=str):
        b, a = before.get(key), after.get(key)
        if (b or Decimal(0)) != (a or Decimal(0)):
            out.append((key, b, a))
    return out


def check_counts(prep: Prepared, conn: psycopg.Connection) -> List[Dict[str, Any]]:
    """RI6/BK6: rijen per brontabel terug te vinden: extra tabel in rma, kopie in rma, anders in public. Herbouwde
    tabellen alleen tonen (``rebuilt``)."""
    public, rma = table_counts(conn, "public"), table_counts(conn, SCHEMA)
    out = []
    for table, n in sorted(prep.before.counts.items()):
        if table in prep.extras.tables:
            where, now = "rma", rma.get(table)
        elif table in prep.copies:
            where, now = "rma (kopie)", rma.get(table)
        else:
            where, now = "public", public.get(table)
        row = {"table": table, "source": n, "where": where, "now": now, "ok": now == n}
        if table in prep.copies and table not in ("inventory",):
            row["public"] = public.get(table)
        if table in REBUILT:
            row["rebuilt"] = True
        out.append(row)
    return out


def finish(conn: psycopg.Connection, prep: Prepared, chain_errors: List[Tuple[str, int, str]],
           restored: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """RI6: na de keten (en het terugzetten, RI5) vergelijken met vóór; verschil in proefbalans of openstaand →
    ``RmaCheckError`` (de aanroeper verwijdert de database). Schrijft het rapport in ``rma.import_report``."""
    if restored is None:
        restored = restore_values(conn, prep)
    after = snapshot(conn)
    balance = _diff(prep.before.balance, after.balance)
    open_items = _diff(prep.before.open_items, after.open_items)
    counts = check_counts(prep, conn)
    report: Dict[str, Any] = {
        "family": "rma",
        "source_version": prep.source_version,
        "extra_tables": [{"table": t, "rows": prep.before.counts.get(t, 0)} for t in prep.extras.tables],
        "extra_columns": prep.extras.columns,
        "copies": prep.copies,
        "objects": [{"kind": k, "name": n, "table": t} for k, n, t in prep.objects],
        "sequences": prep.extras.sequences,
        "types": prep.extras.types,
        "skipped_extension": "fuzzystrmatch" if prep.extras.types or prep.extras.functions else "",
        "chain_errors": [{"script": s, "line": ln, "message": m} for s, ln, m in chain_errors],
        "balance": {"accounts": len(prep.before.balance), "differences": [[str(k), str(b), str(a)] for k, b, a in balance]},
        "open_items": {"documents": len(prep.before.open_items),
                       "differences": [[f"{k[0]} {k[1]}", str(b), str(a)] for k, b, a in open_items]},
        "counts": counts,
        "restored": restored or {},
        "warehouse_converted": False,                     # RM11: voorraad per magazijn nog niet omgezet (RM4)
    }
    if balance or open_items:
        raise RmaCheckError("Na het inlezen klopt de proefbalans of het openstaande bedrag niet meer met de back-up.",
                            report)
    with conn.transaction():
        conn.execute("CREATE TABLE IF NOT EXISTS rma.import_report (id serial PRIMARY KEY, "
                     "created timestamptz NOT NULL DEFAULT now(), report jsonb NOT NULL)")
        conn.execute("INSERT INTO rma.import_report (report) VALUES (%s)", (json.dumps(report, default=str),))
    return report


def load_report(conn: psycopg.Connection) -> Optional[Dict[str, Any]]:
    """Laatste inleesrapport, of None als dit geen ingelezen RMA-administratie is."""
    if not conn.execute("SELECT 1 FROM information_schema.tables WHERE table_schema = 'rma' "
                        "AND table_name = 'import_report'").fetchone():
        return None
    r = conn.execute("SELECT report FROM rma.import_report ORDER BY id DESC LIMIT 1").fetchone()
    return r[0] if r else None
