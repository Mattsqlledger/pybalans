"""Facturen met artikelen (fase 3 stap 2) in de Perl-vergelijking: Python-tegenhanger van ``is_ui`` in
perl_driver.pl (blok "is"). Perl speelt is.pl/ir.pl na als browser; hier gebeurt hetzelfde met de
bibliotheek (``sqlledger.invoice``)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Dict

from sqlledger.arap import ArapPayment
from sqlledger.dates import parse_date
from sqlledger.errors import LedgerError
from sqlledger.invoice import Invoice, InvoiceLedger, InvoiceLine

#: factuurregels: id's wisselen bij elk opslaan (Perl schrijft alles opnieuw); inhoud wordt vergeleken
IS_TABLES = {"invoice": ("id",)}


def is_normalize(conn, table: str, cols, rows):
    """Kostprijsregels vergelijkbaar maken (README, fase 3 F3): regels op voorraad- en kostprijsrekeningen
    (koppeling IC*) van facturen per boeking, rekening, datum en project opgeteld, zonder ``id`` (Perl: id
    van de inkoopregel of het artikel en één regel per inkoop; Python: id van de verkoopregel, één regel per
    verkoopregel en datum). Perl draait bij opnieuw opslaan terug en boekt opnieuw; netto per datum moet het
    gelijk zijn. Regels die netto 0 zijn vallen weg."""
    if table == "ap" and "bank_id" in cols:     # ir.pl verliest de standaard betaalrekening (README fase 3)
        ii, ib = cols.index("invoice"), cols.index("bank_id")
        return sorted((tuple(None if (i == ib and r[ii] in ("1", "True", "true")) else v for i, v in enumerate(r))
                       for r in rows), key=repr)
    if table != "acc_trans":
        return rows
    ic = {str(r[0]) for r in conn.execute("SELECT id FROM chart WHERE link LIKE '%IC%' AND link NOT LIKE '%AR%' "
                                          "AND link NOT LIKE '%AP_tax%'").fetchall()}
    inv = {str(r[0]) for r in conn.execute("SELECT id FROM ar WHERE invoice UNION SELECT id FROM ap WHERE invoice")}
    it, ic_, ia, idt, ip, iid = (cols.index(c) for c in ("trans_id", "chart_id", "amount", "transdate", "project_id",
                                                           "id"))
    keep, sums = [], {}
    for r in rows:
        if r[it] in inv and r[ic_] in ic and r[cols.index("fx_transaction")] in ("0", "False", "false", None):
            k = (r[it], r[ic_], r[idt], r[ip])
            sums[k] = sums.get(k, Decimal(0)) + Decimal(r[ia])
        else:
            keep.append(r)
    for (t, c, d, p), v in sums.items():
        if v:
            row = [None] * len(cols)
            row[it], row[ic_], row[ia], row[idt], row[ip] = t, c, format(v.normalize(), "f"), d, p
            row[cols.index("approved")] = "1"
            row[cols.index("fx_transaction")] = "0"
            row[iid] = "cogs"
            keep.append(tuple(row))
    return sorted(keep, key=repr)


def _d(v) -> Decimal:
    return Decimal(str(v)) if v not in (None, "") else Decimal(0)


def is_ui_py(conn, cfg, a: Dict[str, Any]) -> Dict[str, Any]:
    """Factuurscherm van de webapp (sqlledger_web.routes_is) nagespeeld, zoals is_ui in perl_driver.pl
    is.pl/ir.pl naspeelt: openen (met lock), relatie kiezen, artikelen toevoegen (tekst in de lege regel +
    verversen), aantallen/prijzen, doorrekenen, opslaan."""
    from sqlledger_web import routes_is as ri
    from sqlledger_web.reportdefs import Lib
    from sqlledger._db import cursor as dbcursor, default_currency, get_precision
    lib = Lib(conn, cfg)
    nf = cfg.numberformat
    vc = a["vc"]
    with dbcursor(conn) as cur:
        prec = get_precision(cur)
        defcur = default_currency(cur)

    def fmt(v):     # zoals fmt() in perl_driver.pl
        v = "" if v in (None, "") else str(v)
        return v.replace(".", ",", 1) if nf in ("1.000,00", "1000,00") else v

    def recalc():
        errors = ri.recalc(lib, f, nf, prec, defcur)
        if errors:
            raise LedgerError(" ".join(errors))

    il = InvoiceLedger(conn, cfg)
    if a.get("id"):
        f = ri.form_from_invoice(lib, il.get_invoice(a["id"], vc, lock=True), nf, prec)
    else:
        f = ri.blank_form(lib, vc, a.get("type") or "invoice", a.get("transdate") or "")
    recalc()
    mode = a.get("mode") or "post"
    if mode == "delete":
        il.delete_invoice(f.id, vc)
        return {}
    if a.get("transdate") is not None:
        f.transdate = a["transdate"]
    if a.get("vc_id"):
        row = conn.execute(f"SELECT name, {vc}number FROM {vc} WHERE id = %s", (a["vc_id"],)).fetchone()
        f.vc_text = f"{row[1] or ''} {row[0] or ''}".strip()
        recalc()
    for k in ("invnumber", "description", "notes", "intnotes", "ordnumber", "ponumber", "shippingpoint", "shipvia",
              "waybill"):
        if a.get(k) is not None:
            setattr(f, k, a[k])
    if "taxincluded" in a:
        f.taxincluded = bool(a["taxincluded"])
    if a.get("currency"):
        f.currency = a["currency"]
        recalc()
    if a.get("exchangerate") is not None:
        f.exchangerate = fmt(a["exchangerate"])
    if a.get("arap_accno"):
        f.arap_accno = a["arap_accno"]
    if a.get("department_id") is not None:
        f.department_id = str(a["department_id"] or "")

    def setline(ln, x):
        for k in ("qty", "sellprice", "discount"):
            if x.get(k) is not None:
                setattr(ln, k, fmt(x[k]))
        for k in ("description", "serialnumber", "deliverydate", "itemnotes"):
            if x.get(k) is not None:
                setattr(ln, k, x[k])
        if x.get("project_id") is not None:
            ln.project_id = str(x["project_id"] or "")

    if a.get("lines") is not None:
        f.lines = []
        for x in a["lines"]:
            ln = ri.ILine(item_text=x["partnumber"])
            f.lines.append(ln)
            recalc()
            setline(ln, x)
    for i, x in (a.get("set") or {}).items():
        setline(f.lines[int(i) - 1], x)
    if a.get("payments") is not None:
        from sqlledger_web.routes_arap import PayRow
        f.pays = [PayRow(datepaid=p["datepaid"], source=p.get("source") or "", memo=p.get("memo") or "",
                         amount=fmt(p["amount"]), exchangerate=fmt(p.get("exchangerate")), accno=p["accno"])
                  for p in a["payments"]]
    recalc()
    if mode == "post_as_new":
        ri.for_new(lib, f)
    try:
        return {"id": ri.do_post(lib, f, nf)}
    except ValueError as exc:
        raise LedgerError(str(exc))


def is_ui_lib(conn, cfg, a: Dict[str, Any]) -> Dict[str, Any]:
    """Zelfde als is_ui_py maar rechtstreeks met de bibliotheek (eerste vergelijking, vóór het scherm)."""
    vc = a["vc"]
    lib = InvoiceLedger(conn, cfg)
    if a.get("id"):
        inv = lib.get_invoice(a["id"], vc, lock=True)
    else:
        last = lib.arap.last_used(vc) or {}       # zoals het scherm: afdeling van de vorige boeking
        inv = Invoice(vc=vc, vc_id=0, transdate=parse_date(a.get("transdate")) if a.get("transdate") else date.today(),
                      arap_accno="", lines=[], type=a.get("type") or "invoice",
                      department_id=last.get("department_id") or None)
    mode = a.get("mode") or "post"
    if mode == "delete":
        lib.delete_invoice(inv.id, vc)
        return {}
    if a.get("transdate"):
        inv.transdate = parse_date(a["transdate"])
    if a.get("vc_id"):
        inv.vc_id = a["vc_id"]
        d = lib.arap.vc_defaults(vc, inv.vc_id, inv.transdate)
        inv.arap_accno = d["arap_accno"] or inv.arap_accno
        inv.currency = d["currency"]
        inv.terms = d["terms"] or 0
        inv.duedate = d["duedate"]
        inv.taxincluded = bool(d["taxincluded"])
        inv.employee_id = d["employee_id"] or inv.employee_id
        if not a.get("id"):
            paid = lib.arap.accounts(vc)["AR_paid" if vc == "customer" else "AP_paid"]
            inv.bank_accno = d["payment_accno"] or (paid[0]["accno"] if paid else None)
    if not inv.arap_accno:
        acc = lib.arap.accounts(vc)
        inv.arap_accno = acc["AR" if vc == "customer" else "AP"][0]["accno"]
    for k in ("invnumber", "description", "notes", "intnotes", "ordnumber", "ponumber", "shippingpoint", "shipvia",
              "waybill"):
        if a.get(k) is not None:
            setattr(inv, k, a[k])
    if "taxincluded" in a:
        inv.taxincluded = bool(a["taxincluded"])
    if a.get("currency"):
        inv.currency = a["currency"]
    if a.get("exchangerate") is not None:
        inv.exchangerate = _d(a["exchangerate"])
    if a.get("arap_accno"):
        inv.arap_accno = a["arap_accno"]
    if a.get("department_id") is not None:
        inv.department_id = a["department_id"] or None

    def setline(l: InvoiceLine, x: Dict[str, Any]) -> None:
        for k in ("qty", "sellprice", "discount"):
            if x.get(k) is not None:
                setattr(l, k, _d(x[k]))
        for k in ("description", "serialnumber", "itemnotes"):
            if x.get(k) is not None:
                setattr(l, k, x[k])
        if x.get("deliverydate") is not None:
            l.deliverydate = parse_date(x["deliverydate"]) if x["deliverydate"] else None
        if x.get("project_id") is not None:
            l.project_id = x["project_id"] or None

    if a.get("lines") is not None:
        inv.lines = []
        for x in a["lines"]:
            items = lib.find_items(vc, partnumber=x["partnumber"])
            items = [i for i in items if i["partnumber"].lower() == x["partnumber"].lower()] or items
            if len(items) != 1:
                raise LedgerError(f"artikel {x['partnumber']} niet (eenduidig) gevonden")
            p = lib.item_defaults(vc, items[0]["id"], inv.exchangerate)
            l = InvoiceLine(parts_id=p["id"], qty=Decimal(1), sellprice=p["price"], description=p["description"],
                            unit=p["unit"] or "", cost=p["cost"] if vc == "customer" else None,
                            vendor_id=0 if vc == "customer" else None)
            setline(l, x)
            inv.lines.append(l)
    for i, x in (a.get("set") or {}).items():
        setline(inv.lines[int(i) - 1], x)
    if a.get("payments") is not None:
        inv.payments = [ArapPayment(accno=p["accno"], amount=_d(p["amount"]), datepaid=parse_date(p["datepaid"]),
                                    source=p.get("source") or "", memo=p.get("memo") or "",
                                    exchangerate=_d(p.get("exchangerate")) or None) for p in a["payments"]]
    if mode == "post_as_new":            # arap.pl post_as_new: nummer blijft, tenzij lock_sinumber
        inv.id = None
        lock = conn.execute("SELECT fldvalue FROM defaults WHERE fldname = 'lock_sinumber'").fetchone()
        if vc == "customer" and lock and lock[0] not in (None, "", "0"):
            inv.invnumber = None
        inv.printed, inv.emailed, inv.queued = set(), set(), {}
        for l in inv.lines:
            l.id = None
        for p in inv.payments:
            p.cleared = None
    return {"id": lib.post_invoice(inv)}


def explain_cogs(a: Dict[str, Any], b: Dict[str, Any]) -> int:
    """Kostprijsregels die alleen in datum verschillen (Perl draait bij opnieuw opslaan of een latere inkoop
    terug en boekt op een andere datum opnieuw, README fase 3 F3): als de opgetelde kostprijsregels (na
    ``is_normalize``) per boeking en rekening gelijk zijn, tellen ze als verklaard en worden ze uit beide
    dumps gehaald. Geeft het aantal verklaarde boekingen terug."""
    cols, ra = a["acc_trans"]
    _, rb = b["acc_trans"]
    it, ic, ia, iid = (cols.index(c) for c in ("trans_id", "chart_id", "amount", "id"))
    da = [r for r in ra if r[iid] == "cogs" and r not in rb]
    db = [r for r in rb if r[iid] == "cogs" and r not in ra]
    if not da and not db:
        return 0

    def sums(rows):
        out: Dict[tuple, Decimal] = {}
        for r in rows:
            out[(r[it], r[ic])] = out.get((r[it], r[ic]), Decimal(0)) + Decimal(r[ia])
        return {k: v for k, v in out.items() if v}
    trans = {r[it] for r in da + db}
    ca = sums([r for r in ra if r[iid] == "cogs" and r[it] in trans])
    cb = sums([r for r in rb if r[iid] == "cogs" and r[it] in trans])
    ok = {t for t in trans if {k: v for k, v in ca.items() if k[0] == t} == {k: v for k, v in cb.items() if k[0] == t}}
    a["acc_trans"] = (cols, [r for r in ra if not (r[iid] == "cogs" and r[it] in ok)])
    b["acc_trans"] = (cols, [r for r in rb if not (r[iid] == "cogs" and r[it] in ok)])
    return len(ok)
