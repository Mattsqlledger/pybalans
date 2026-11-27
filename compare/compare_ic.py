"""Artikelen (fase 3) in de Perl-vergelijking: Python-tegenhanger van ``ic_ui`` in
perl_driver.pl (blok "ic"). Perl speelt ic.pl na als browser; hier gebeurt hetzelfde
met de formuliercode van de webapp (``sqlledger_web.routes_ic``)."""

from __future__ import annotations

from typing import Any, Dict

from sqlledger.errors import LedgerError
from sqlledger.parts import Parts

#: tabellen die bij elk scenario mee vergeleken worden
IC_TABLES = {t: () for t in ("parts", "partstax", "partsgroup", "partsvendor", "partscustomer", "makemodel")}
#: stuklijstregels: Perl verwijdert en schrijft de stuklijst bij elk opslaan van een ongebruikt samengesteld
#: artikel opnieuw (nieuwe id's); Python alleen bij een wijziging (README, fase 3 SA8). Inhoud wordt vergeleken.
IC_TABLES["assembly"] = ("id",)


def ic_normalize(table: str, cols, rows):
    """Lijstprijs van een samengesteld artikel niet vergelijken: Perl haalt bij het toevoegen van een
    onderdeel de lijstprijs niet op (IC.pm assembly_item selecteert geen listprice) en slaat 0 op;
    Python rekent hem uit de onderdelen (README, fase 3 SA3). Getoetst in tests/test_assembly.py."""
    if table != "parts" or "assembly" not in cols:
        return rows
    ia, il = cols.index("assembly"), cols.index("listprice")
    return sorted((tuple(None if (i == il and r[ia] in ("1", "True", "true")) else v for i, v in enumerate(r))
                   for r in rows), key=repr)


def ic_ui_py(conn, cfg, a: Dict[str, Any]) -> Dict[str, Any]:
    from sqlledger_web import routes_ic as ri
    nf = cfg.numberformat
    lib = Parts(conn, cfg)
    with conn.cursor() as cur:
        cur.execute("SELECT fldvalue FROM defaults WHERE fldname = 'precision'")
        row = cur.fetchone()
        precision = int(row[0]) if row and row[0] else 2

    def fmt(v):     # zoals fmt() in perl_driver.pl
        v = "" if v in (None, "") else str(v)
        return v.replace(".", ",", 1) if nf in ("1.000,00", "1000,00") else v

    p = lib.get(a["id"]) if a.get("id") else lib.new(a.get("item") or "part")
    f = ri.form_from_part(p, nf, precision)
    mode = a.get("mode") or "save"
    if mode == "delete":
        lib.delete(f.id)
        return {}
    for k, x in (a.get("fields") or {}).items():
        if k == "taxes":
            f.taxaccounts = list(x)
        elif k in ("obsolete", "checkinventory"):
            setattr(f, k, bool(x))
        elif k in ("sellprice", "listprice", "lastcost", "weight", "rop"):
            setattr(f, k, fmt(x))
        else:
            setattr(f, k, "" if x is None else str(x))
    if a.get("components"):         # zoals in het scherm: nummer in een lege regel + Bijwerken
        for number, qty, bom in a["components"]:
            row = ri.comp_row(None, fmt(qty), bool(bom), number)
            f.components.append(row)
            err = ri.resolve_components(lib, f)
            if err:
                raise LedgerError(err)
            row["qty"] = fmt(qty)
    for i, flag in (a.get("bom") or {}).items():
        f.components[int(i) - 1]["bom"] = bool(flag)
    if mode == "new_number":
        return {"number": lib.next_number(peek=True)}
    try:
        return {"id": ri.do_save(lib, f, nf, as_new=mode == "save_as_new")}
    except ValueError as exc:
        raise LedgerError(str(exc))


def ic_stock_py(conn, cfg, a: Dict[str, Any]) -> Dict[str, Any]:
    """Assembleren (ic.pl Stock Assembly): Perl controleert de voorraad niet (zonder vinkje), dus
    hier de waarschuwing bevestigd (allow_short)."""
    from datetime import date
    from decimal import Decimal
    from sqlledger.assembly import Assemblies
    Assemblies(conn, cfg).build(a["id"], Decimal(str(a["qty"])), transdate=date.today(), allow_short=True)
    return {}
