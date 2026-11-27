"""Beheer (fase 2) in de Perl-vergelijking: Python-tegenhanger van ``am_ui`` in
perl_driver.pl (blok "am").

Perl speelt am.pl na als browser (scherm openen → velden aanpassen → knop); hier
gebeurt hetzelfde met de formuliercode van de webapp (``sqlledger_web.routes_am``:
``*_fields`` = wat het scherm toont, ``*_from_form`` = wat terugkomt) en de
bibliotheek (``sqlledger.admin``, ``sqlledger.masterdata``).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict

from sqlledger.admin import LINK_ORDER, Admin
from sqlledger.dates import parse_date
from sqlledger.masterdata import MasterData, Project

#: extra tabellen die bij beheer-scenario's vergeleken worden (naast run_compare.TABLES)
AM_TABLES = {t: () for t in ("chart", "tax", "curr", "department", "project", "language", "business",
                             "paymentmethod", "translation", "partstax", "customertax", "vendortax",
                             "bank", "customer", "vendor")}


@contextmanager
def extra_tables(sc: Dict[str, Any], tables: Dict[str, tuple]):
    """Vergelijk tijdelijk ook de beheertabellen als het scenario am_ui-operaties heeft."""
    add = {k: v for k, v in AM_TABLES.items() if k not in tables} \
        if any(o.get("op") == "am_ui" for o in sc.get("ops", [])) else {}
    tables.update(add)
    try:
        yield
    finally:
        for k in add:
            tables.pop(k, None)


def _tax_add_row(f, cid):
    """Extra lege tariefregel onder de laatste regel van rekening ``cid``, zoals een gepost formulier met een
    regel erbij (am.pl update_taxes). Het webscherm heeft daar sinds BT1 geen knop meer voor ("Nieuw tarief
    per datum"), maar het opslaan (``taxes_from_form`` + ``save_taxes``) neemt zulke regels nog steeds aan;
    dat pad wordt hier met Perl vergeleken."""
    n = int(f.get("rowcount") or 0)
    last = max([i for i in range(1, n + 1) if f.get(f"chart_id_{i}") == str(cid)] or [n])
    rows = [{k: f.get(f"{k}_{i}") or "" for k in ("chart_id", "rate", "taxnumber", "validto")}
            for i in range(1, n + 1)]
    rows.insert(last, {"chart_id": str(cid), "rate": "", "taxnumber": "", "validto": ""})
    g = {k: v for k, v in f.items() if k.startswith("closed_")}
    g["rowcount"] = str(len(rows))
    for i, r in enumerate(rows, 1):
        for k, v in r.items():
            g[f"{k}_{i}"] = v
    return g


def am_ui_py(conn, cfg, a: Dict[str, Any]) -> Dict[str, Any]:
    from sqlledger_web import routes_am as ra
    nf = cfg.numberformat
    adm, md = Admin(conn, cfg), MasterData(conn, cfg)

    def fmt(v):     # zoals fmt() in perl_driver.pl
        v = "" if v in (None, "") else str(v)
        return v.replace(".", ",", 1) if nf in ("1.000,00", "1000,00") else v

    do = a["do"]
    mode = a.get("mode") or "save"
    if isinstance(a.get("id"), str) and ":" in a["id"]:          # id opzoeken (zoals perl_driver.pl)
        k, v = a["id"].split(":", 1)
        sql = {"accno": "SELECT id FROM chart WHERE accno = %s",
               "number": "SELECT id FROM project WHERE projectnumber = %s"}.get(
            k, f"SELECT id FROM {do} WHERE description = %s")
        with conn.cursor() as cur:
            cur.execute(sql, (v,))
            row = cur.fetchone()
        a["id"] = row[0] if row else None
    if do == "account":
        view = adm.get_account(a["id"]) if a.get("id") else None
        f = ra.account_fields(view)
        for k in ("accno", "description", "category", "charttype", "gifi_accno"):
            if k in a:
                f[k] = a[k]
        for k in ("contra", "closed"):
            if k in a:
                f[k] = "1" if a[k] else ""
        if "link" in a:
            for k in LINK_ORDER:
                f[f"link_{k}"] = "1" if k in a["link"] else ""
        acc = ra.account_from_form(f)
        if mode == "delete":
            adm.delete_account(acc.id)
        else:
            if mode == "save_as_new":
                acc.id = None
            adm.save_account(acc)
    elif do == "taxes":
        rows = adm.taxes()
        f = ra.tax_fields(rows, nf)
        with conn.cursor() as cur:
            cur.execute("SELECT id, accno FROM chart")
            accno_of = {cid: accno for cid, accno in cur.fetchall()}
        id_of = {v: k for k, v in accno_of.items()}

        def apply(f, sets):
            for s in sets or []:
                n = 0
                for i in range(1, int(f["rowcount"]) + 1):
                    if accno_of[int(f[f"chart_id_{i}"])] != s["accno"]:
                        continue
                    n += 1
                    if n != s["n"]:
                        continue
                    if "rate" in s:
                        f[f"rate_{i}"] = fmt(s["rate"])
                    if "taxnumber" in s:
                        f[f"taxnumber_{i}"] = s["taxnumber"]
                    if "validto" in s:
                        f[f"validto_{i}"] = s["validto"]
        apply(f, a.get("set"))
        for accno, flag in (a.get("closed") or {}).items():
            f[f"closed_{id_of[accno]}"] = "1" if flag else ""
        for accno in dict.fromkeys(s["accno"] for s in a.get("add") or []):
            f = _tax_add_row(f, str(id_of[accno]))
        apply(f, a.get("add"))
        rows, closed = ra.taxes_from_form(f, nf)
        adm.save_taxes(rows, closed)
    elif do == "defaults":
        f = ra.settings_fields(adm.settings())
        for k, v in (a.get("set") or {}).items():
            if k == "fxgainloss":
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM chart WHERE accno = %s", (v,))
                    f["fxgainloss_accno_id"] = str(cur.fetchone()[0])
            else:
                f[k] = v or ""
        values, shown = ra.settings_from_form(f)
        adm.save_settings(values, shown)
    elif do == "closing":
        c = adm.closing()
        if "closedto" in a:
            closedto = parse_date(a["closedto"]) if a["closedto"] else None
        else:
            closedto = c["closedto"]           # veld ongewijzigd: opgeslagen waarde blijft
        def flag(k):
            return a[k] not in ("", "0", 0, False, None) if k in a else c[k]
        adm.save_closing(closedto, flag("revtrans"), flag("audittrail"))
    elif do == "currency":
        if mode == "add":
            md.add_currency(a["curr"], a.get("prec"))
        elif mode == "move":
            md.move_currency(a["curr"], a["move"])
        elif mode == "delete":
            md.delete_currency(a["curr"])
        else:
            md.set_precision(a["curr"], a["prec"])
    elif do == "exchangerate":
        d = parse_date(a.get("date")) if a.get("date") else None
        rates = md.rates_on(d) if d else {c["curr"]: None for c in md.currencies()[1:]}
        f = ra.rate_fields(d, rates, nf)
        if "transdate" in a:
            f["transdate"] = a["transdate"]
        for c, v in (a.get("rates") or {}).items():
            f[f"rate_{c}"] = fmt(v)
        transdate, new, olddate = ra.rates_from_form(f, nf)
        md.save_exchangerates(transdate, new, olddate)
    elif do in ("department", "business", "paymentmethod"):
        slug = {"department": "afdelingen", "business": "branches", "paymentmethod": "betaalmethoden"}[do]
        kind = ra._kinds()[slug]
        if mode == "move":
            kind.move(md, str(a["id"]), a["move"])
            return {}
        row = next((r for r in kind.rows(md) if r["id"] == a["id"]), None) if a.get("id") else None
        f = ra.simple_fields(kind, row, nf)
        for k, v in (a.get("set") or {}).items():
            f[k] = "" if (k == "roundchange" and v in ("0", 0)) else str(v)
        if mode == "delete":
            kind.delete(md, f["key"])
        else:
            kind.save(md, ra.simple_values(kind, f, nf), f["key"])
    elif do == "language":
        if mode == "delete":
            md.delete_language(a["code"])
        else:
            md.save_language(a["code"], a["description"], a.get("oldcode"))
    elif do == "project":
        if mode == "delete":
            md.delete_project(a["id"])
        else:
            md.save_project(Project(id=a.get("id"), projectnumber=a.get("projectnumber") or "",
                                    description=a.get("description") or "",
                                    startdate=parse_date(a.get("startdate")) if a.get("startdate") else None,
                                    enddate=parse_date(a.get("enddate")) if a.get("enddate") else None,
                                    customer_id=a.get("customer_id")))
    else:
        raise ValueError(f"onbekend am_ui: {do}")
    return {}
