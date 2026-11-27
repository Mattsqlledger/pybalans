"""Orders en offertes (fase 3 stap 3) in de Perl-vergelijking: Python-tegenhanger van ``oe_ui`` in
perl_driver.pl (blok "oe"). Perl speelt oe.pl (en voor "factuur maken" is.pl/ir.pl) na als browser; hier
gebeurt hetzelfde met de formuliercode van het webscherm (``sqlledger_web.routes_oe``/``routes_is``)."""

from __future__ import annotations

from typing import Any, Dict

from sqlledger.errors import LedgerError
from sqlledger.order import TYPES, OrderLedger

#: orderregels: id's wisselen bij elk opslaan (Perl schrijft alles opnieuw); inhoud wordt vergeleken.
#: ``inventory``: magazijnbewegingen (Perl schrijft ze bij verzonden aantallen, Python niet: O3)
OE_TABLES = {"oe": (), "orderitems": ("id",), "cargo": ("id",), "inventory": ("id", "orderitems_id")}


def oe_normalize(table: str, cols, rows):
    """Orderregels: lege kostprijs/leverancier-id = 0 (OE.pm schrijft parse_amount('') en costvendorid*1 als 0;
    Python laat een NULL uit oude gegevens staan, O6)."""
    if table != "orderitems":
        return rows
    ix = [cols.index(c) for c in ("cost", "vendor_id")]
    return sorted((tuple("0" if (i in ix and v is None) else v for i, v in enumerate(r)) for r in rows), key=repr)


def oe_ui_py(conn, cfg, a: Dict[str, Any]) -> Dict[str, Any]:
    """Orderscherm van de webapp nagespeeld zoals oe_ui in perl_driver.pl oe.pl naspeelt: openen (met lock),
    relatie kiezen, artikelen toevoegen, doorrekenen, opslaan; omzetten; factuur maken en boeken."""
    from sqlledger_web import routes_is as ri
    from sqlledger_web import routes_oe as ro
    from sqlledger_web.reportdefs import Lib
    from sqlledger._db import cursor as dbcursor, default_currency, get_precision
    lib = Lib(conn, cfg)
    nf = cfg.numberformat
    otype = a["type"]
    with dbcursor(conn) as cur:
        prec = get_precision(cur)
        defcur = default_currency(cur)

    def fmt(v):
        v = "" if v in (None, "") else str(v)
        return v.replace(".", ",", 1) if nf in ("1.000,00", "1000,00") else v

    def recalc(form, module=ro):
        errors = module.recalc(lib, form, nf, prec, defcur)
        if errors:
            raise LedgerError(" ".join(errors))

    ol = OrderLedger(conn, cfg)
    if a.get("id"):
        f = ro.form_from_order(lib, ol.get_order(a["id"], lock=True), nf, prec)
    else:
        f = ro.blank_form(lib, otype, a.get("transdate") or "")
    recalc(f)
    mode = a.get("mode") or "save"
    if mode == "delete":
        ol.delete_order(f.id)
        return {}
    if a.get("transdate") is not None:
        f.transdate = a["transdate"]
    vc = TYPES[otype][0]
    if a.get("vc_id"):
        row = conn.execute(f"SELECT name, {vc}number FROM {vc} WHERE id = %s", (a["vc_id"],)).fetchone()
        f.vc_text = f"{row[1] or ''} {row[0] or ''}".strip()
        recalc(f)
    for k in ("ordnumber", "quonumber", "description", "notes", "intnotes", "ponumber", "shippingpoint", "shipvia",
              "waybill", "reqdate"):
        if a.get(k) is not None:
            setattr(f, k, a[k])
    if "taxincluded" in a:
        f.taxincluded = bool(a["taxincluded"])
    if a.get("currency"):
        f.currency = a["currency"]
        recalc(f)
    if a.get("exchangerate") is not None:
        f.exchangerate = fmt(a["exchangerate"])
    if a.get("department_id") is not None:
        f.department_id = str(a["department_id"] or "")

    def setline(ln, x):
        for k in ("qty", "sellprice", "discount"):
            if x.get(k) is not None:
                setattr(ln, k, fmt(x[k]))
        for k in ("description", "serialnumber", "itemnotes"):
            if x.get(k) is not None:
                setattr(ln, k, x[k])
        if x.get("reqdate") is not None:
            ln.deliverydate = x["reqdate"]
        if x.get("project_id") is not None:
            ln.project_id = str(x["project_id"] or "")

    if a.get("lines") is not None:
        f.lines = []
        for x in a["lines"]:
            ln = ro.OLine(item_text=x["partnumber"])
            f.lines.append(ln)
            recalc(f)
            setline(ln, x)
    for i, x in (a.get("set") or {}).items():
        setline(f.lines[int(i) - 1], x)
    recalc(f)
    try:
        if mode == "convert":
            # webscherm: "Opslaan en omzetten naar": eerst opslaan (als dat mag), dan het nieuwe formulier
            if f.id and ro.readonly_reason(_Ctx(), ol.get_order(f.id)) is None:
                ro.do_save(lib, f, nf)
            target = {"sales_order": "sales_order", "purchase_order": "purchase_order",
                      "quotation": "sales_quotation", "rfq": "request_quotation"}[a["to"]]
            g = ro.form_from_order(lib, ol.convert(f.id, target), nf, prec)
            g.id = None
            for ln in g.lines:
                ln.id = ""
                ln.ship = ""
            if a.get("new_transdate") is not None:
                g.transdate = a["new_transdate"]
            recalc(g)
            return {"id": ro.do_save(lib, g, nf)}
        if mode == "invoice":
            if f.id and ro.readonly_reason(_Ctx(), ol.get_order(f.id)) is None:
                ro.do_save(lib, f, nf)
            inv = ol.to_invoice(f.id)
            g = ri.form_from_invoice(lib, inv, nf, prec, copy=True)
            g.order_id = str(f.id)
            g.oldterms = "x"
            g.oldtransdate = g.transdate
            ship = a.get("ship") or {}
            if ship:                              # deellevering: aantallen in het factuurscherm verlagen (ON2)
                keep = []
                for i, ln in enumerate(g.lines, 1):
                    q = ship.get(str(i), ship.get(i, "0"))
                    if str(q) not in ("0", ""):
                        ln.qty = fmt(q)
                        keep.append(ln)
                g.lines = keep
            if a.get("invnumber") is not None:
                g.invnumber = a["invnumber"]
            recalc(g, ri)
            return {"id": ri.do_post(lib, g, nf)}
        if mode == "save_as_new":
            ro.for_new(lib, f)
        # force: alleen de echte-datatoets (Perl slaat ook gesloten/gefactureerde orders op; het scherm weigert, O6)
        return {"id": ro.do_save(lib, f, nf, _force=bool(a.get("force")))}
    except ValueError as exc:
        raise LedgerError(str(exc))


class _Ctx:
    """Minimale context voor ``routes_oe.readonly_reason`` (boekhouder)."""

    def has_role(self, role: str) -> bool:
        return True


def oe_remap(a: Dict[str, Any], b: Dict[str, Any]) -> int:
    """Volgorde van aanmaken: Perl maakt bij factureren eerst de nabestelling en boekt dan de factuur; Python
    boekt de factuur en maakt de nabestelling pas daarna (O4: pas bij boeken). Daardoor zijn hun id's (uit
    dezelfde reeks) verwisseld. Als orders en facturen op inhoud (nummer, datum, soort, nabestelling) één op
    één overeenkomen, krijgen de Python-id's die van Perl; geeft het aantal omgenummerde id's terug."""
    def sigs(d):
        out: Dict[str, Any] = {}
        cols, rows = d["oe"]
        ix = {c: cols.index(c) for c in ("id", "ordnumber", "quonumber", "transdate", "quotation", "backorder",
                                         "customer_id", "vendor_id")}
        for r in rows:
            out[r[ix["id"]]] = ("oe",) + tuple(r[ix[c]] for c in ix if c != "id")
        for t in ("ar", "ap"):
            cols, rows = d[t]
            i, n = cols.index("id"), cols.index("invnumber")
            for r in rows:
                out[r[i]] = (t, r[n])
        return out
    sa, sb = sigs(a), sigs(b)
    if set(sa) != set(sb) or len(set(sa.values())) != len(sa) or set(sa.values()) != set(sb.values()):
        return 0
    by_sig = {v: k for k, v in sa.items()}
    mapping = {k: by_sig[v] for k, v in sb.items() if by_sig[v] != k}
    if not mapping:
        return 0
    for table, (cols, rows) in list(b.items()):
        idx = [i for i, c in enumerate(cols) if c == "trans_id" or c == "aa_id"
               or (c == "id" and table in ("oe", "ar", "ap", "semaphore"))]
        if not idx:
            continue
        new = [tuple(mapping.get(v, v) if i in idx else v for i, v in enumerate(r)) for r in rows]
        b[table] = (cols, sorted(new, key=repr))
    return len(mapping)
