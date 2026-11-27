"""Tijdkaarten (fase Uren U-1) in de Perl-vergelijking: Python-tegenhanger van ``jc_ui`` in perl_driver.pl (blok "jc").
Perl speelt jc.pl na als browser (add/edit → update → save); hier hetzelfde met ``sqlledger.timecards``
(``form_update`` = de regels van jc.pl ``update`` voor aantal en prijs)."""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlledger.amounts import to_decimal
from sqlledger.dates import parse_date
from sqlledger.timecards import TimeCard, TimeCards, form_update


def _t(h, m) -> Optional[time]:
    if h in (None, "") and m in (None, ""):
        return None
    return time(int(h or 0), int(m or 0))


def jc_ui_py(conn, cfg, a: Dict[str, Any]) -> Dict[str, Any]:
    lib = TimeCards(conn, cfg)
    if a.get("mode") == "delete":
        lib.delete(int(a["id"]))
        return {}
    v = a.get("fields") or {}
    tc = lib.get(int(a["id"])) if a.get("id") else TimeCard()
    old_in, old_out, old_qty = tc.checkedin, tc.checkedout, tc.qty
    with conn.cursor() as cur:
        if "projectnumber" in v:
            cur.execute("SELECT id FROM project WHERE projectnumber = %s", (v["projectnumber"],))
            tc.project_id = cur.fetchone()[0]
        if "employee" in v:
            cur.execute("SELECT id FROM employee WHERE name = %s", (v["employee"],))
            tc.employee_id = cur.fetchone()[0]
        part_description = None
        if "partnumber" in v and v["partnumber"] != tc.partnumber:
            cur.execute("SELECT id, sellprice, description FROM parts WHERE partnumber = %s", (v["partnumber"],))
            pid, price, part_description = cur.fetchone()
            tc.parts_id, tc.sellprice = pid, to_decimal(price)
        cur.execute("SELECT fldvalue FROM defaults WHERE fldname = 'precision'")
        r = cur.fetchone()
        places = int(to_decimal(r[0])) if r and r[0] else 2
    if "transdate" in v:
        tc.transdate = parse_date(v["transdate"], cfg.dateformat)
    for k in ("description", "notes"):
        if k in v:
            setattr(tc, k, v[k])
    if part_description is not None:      # jc.pl update: andere dienst = omschrijving van de dienst
        tc.description = part_description
    if any(k in v for k in ("inhour", "inmin")):
        tc.checkedin = _t(v.get("inhour"), v.get("inmin"))
    if any(k in v for k in ("outhour", "outmin")):
        tc.checkedout = _t(v.get("outhour"), v.get("outmin"))
    form_update(tc, old_in=old_in, old_out=old_out, old_qty=old_qty,
                qty_given=to_decimal(v["qty"]) if "qty" in v else None,
                noncharge=to_decimal(v.get("noncharge") or 0), places=places)
    if "sellprice" in v:
        tc.sellprice = to_decimal(v["sellprice"])
    return {"id": lib.save(tc)}


def pe_so_py(conn, cfg, a: Dict[str, Any]) -> Dict[str, Any]:
    """Tegenhanger van ``pe_so``: open uren (eventueel samengevat), gekozen rijen → verkooporders."""
    from datetime import date as _date
    from sqlledger.timecards import generate_sales_orders, open_items
    d = lambda k: _date.fromisoformat(a[k]) if a.get(k) else None  # noqa: E731
    items = open_items(conn, datefrom=d("datefrom"), dateto=d("dateto"), summary=bool(a.get("summary")))
    rows = a.get("rows")
    chosen = [it for n, it in enumerate(items, 1) if not rows or n in rows]
    return {"orders": generate_sales_orders(conn, cfg, chosen)}
