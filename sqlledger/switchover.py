"""Overstap uit SQL-Ledger (FASE6_ANALYSE.md 6d-2, I4–I6, I8): gegevens van het inlezen, integriteitscontroles en
de cijfers voor het controleblad.

Bij het inlezen van een back-up (``slbackup.restore``) legt de webapp vast waar de administratie vandaan komt
(``x_setting`` ``switch_list``: soort, dataset, versie, datum van de back-up, bijgewerkt van, overgeslagen
statements, afsluitdatum en laatste boekdatum op dat moment). Zolang die regel bestaat, toont het dashboard de lijst
"Overstap uit SQL-Ledger" (``dashboard.Dashboard.switch_list``).

De integriteitscontroles lezen alleen; ze vergelijken wat SQL-Ledger op twee plaatsen bewaart (boeking en
grootboekregels, factuur en debiteuren-/crediteurenrekening). Een bevinding is geen fout van het inlezen: het kan ook
een oude eigenaardigheid in de data zijn (bv. administratie C: afrondingen van 1 cent bij vreemde valuta uit 2014).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import psycopg

from . import _db
from .amounts import round_amount, to_decimal
from .extensions import ensure_schema

SETTING = "switch_list"
#: verschil per boeking dat bij vreemde valuta als afronding telt (SL rondt koersregels per regel af)
FX_ROUNDING = Decimal("0.01")
#: zoveel regels per bevinding tonen
MAX_ROWS = 50
#: controles in volgorde; niveau als er iets gevonden wordt
CHECKS = (("unbalanced", "warn"), ("fx_rounding", "info"), ("missing_account", "warn"), ("orphan", "warn"),
          ("overpaid", "warn"), ("arap_mismatch", "warn"), ("upgrade_skipped", "warn"))


@dataclass
class Finding:
    key: str
    level: str                      # ok | info | warn
    count: int
    rows: List[Dict[str, Any]] = field(default_factory=list)


# ------------------------------------------------------------------ herkomst

def record_restore(conn: psycopg.Connection, info: Dict[str, Any], login: Optional[str] = None) -> Dict[str, Any]:
    """Na het inlezen: herkomst vastleggen en daarmee de overstaplijst aanzetten. ``info``: kind, family ("rma" bij
    Run my Accounts / ledger123), dataset, version, date (uit de kop van de back-up), version_from/version_to
    (databaseversie vóór/na bijwerken), skipped. Afsluitdatum, bedrijfsnaam (VP1: een pg_dump heeft geen dataset) en
    laatste boekdatum komen uit de database."""
    ensure_schema(conn)
    with conn.transaction(), _db.cursor(conn) as cur:
        d = _db.get_defaults(cur, "closedto", "company")
        cur.execute("SELECT max(transdate) AS d FROM acc_trans")
        last = cur.fetchone()["d"]
        data = {k: info.get(k) or "" for k in ("kind", "family", "dataset", "version", "date", "version_from",
                                               "version_to")}
        data.update(company=(d.get("company") or "").strip(),
                    skipped=int(info.get("skipped") or 0), closedto=(d.get("closedto") or "").strip(),
                    last_transdate=last.isoformat() if last else "")
        cur.execute("""INSERT INTO x_setting (key, value, login) VALUES (%s, %s, %s)
                       ON CONFLICT (key) DO UPDATE SET value = excluded.value, login = excluded.login,
                                                       updated = now()""",
                    (SETTING, json.dumps(data, ensure_ascii=False), login))
    return data


def enable(conn: psycopg.Connection, login: Optional[str] = None) -> Dict[str, Any]:
    """Overstaplijst aanzetten voor een administratie die niet via Back-up inlezen kwam (gekoppeld)."""
    return record_restore(conn, {"kind": "linked"}, login)


def origin(conn: psycopg.Connection) -> Optional[Dict[str, Any]]:
    """Herkomst zoals ``restore_info``, maar alleen lezend (geen ``ensure_schema``): voor het nalopen van andere
    administraties bij het inlezen (K3). Geen ``x_setting`` of geen regel: None."""
    try:
        with conn.transaction(), _db.cursor(conn) as cur:
            cur.execute("SELECT value FROM x_setting WHERE key = %s", (SETTING,))
            r = cur.fetchone()
    except psycopg.errors.UndefinedTable:
        return None
    try:
        data = json.loads(r["value"]) if r else None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def restore_info(conn: psycopg.Connection) -> Optional[Dict[str, Any]]:
    ensure_schema(conn)
    with _db.cursor(conn) as cur:
        cur.execute("SELECT value FROM x_setting WHERE key = %s", (SETTING,))
        r = cur.fetchone()
    if not r:
        return None
    try:
        data = json.loads(r["value"])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# ------------------------------------------------------------------ oordeel over het controleblad (v1.0.1 PB8)

CHECK_SETTING = "switch_check"


def check_verdict(conn: psycopg.Connection, ok: bool, note: str = "", name: str = "",
                  login: Optional[str] = None) -> Dict[str, Any]:
    """Oordeel van de beheerder over het controleblad vastleggen: ``ok`` = "Cijfers kloppen – akkoord" (punt
    ``check`` van de overstaplijst afgevinkt), anders "Er klopt iets niet" met een notitie (punt blijft of gaat weer
    open). Het laatste oordeel staat in ``x_setting`` (datum, naam, notitie)."""
    ensure_schema(conn)
    data = {"verdict": "ok" if ok else "issue", "note": "" if ok else (note or "").strip()[:2000],
            "name": name or login or "", "date": date.today().isoformat()}
    from .dashboard import START_DAY
    with conn.transaction(), _db.cursor(conn) as cur:
        cur.execute("""INSERT INTO x_setting (key, value, login) VALUES (%s, %s, %s)
                       ON CONFLICT (key) DO UPDATE SET value = excluded.value, login = excluded.login,
                                                       updated = now()""",
                    (CHECK_SETTING, json.dumps(data, ensure_ascii=False), login))
        if ok:
            cur.execute("""INSERT INTO x_task_done (kind, period_end, login) VALUES ('switchdone_check', %s, %s)
                           ON CONFLICT DO NOTHING""", (START_DAY, login))
        else:
            cur.execute("DELETE FROM x_task_done WHERE kind = 'switchdone_check'")
    return data


def check_status(conn: psycopg.Connection) -> Optional[Dict[str, Any]]:
    """Laatste oordeel over het controleblad (``check_verdict``) of None."""
    ensure_schema(conn)
    with _db.cursor(conn) as cur:
        cur.execute("SELECT value FROM x_setting WHERE key = %s", (CHECK_SETTING,))
        r = cur.fetchone()
    try:
        data = json.loads(r["value"]) if r else None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# ------------------------------------------------------------------ integriteit

def integrity(conn: psycopg.Connection, info: Optional[Dict[str, Any]] = None) -> List[Finding]:
    """Alle controles, ook die zonder bevinding (niveau ``ok``)."""
    found: Dict[str, List[Dict[str, Any]]] = {}
    with _db.cursor(conn) as cur:
        cur.execute("""SELECT a.trans_id, sum(a.amount) AS diff, min(a.transdate) AS transdate,
                              bool_or(coalesce(a.fx_transaction, false)) AS fx,
                              coalesce(g.reference, ar.invnumber, ap.invnumber) AS reference,
                              CASE WHEN g.id IS NOT NULL THEN 'gl' WHEN ar.id IS NOT NULL THEN 'ar'
                                   WHEN ap.id IS NOT NULL THEN 'ap' END AS module
                       FROM acc_trans a
                       LEFT JOIN gl g ON (g.id = a.trans_id) LEFT JOIN ar ON (ar.id = a.trans_id)
                       LEFT JOIN ap ON (ap.id = a.trans_id)
                       GROUP BY a.trans_id, g.id, g.reference, ar.id, ar.invnumber, ap.id, ap.invnumber
                       HAVING abs(sum(a.amount)) >= 0.005
                       ORDER BY min(a.transdate), a.trans_id""")
        for r in cur.fetchall():
            r["diff"] = to_decimal(r["diff"]).quantize(Decimal("0.01"))
            key = "fx_rounding" if r["fx"] and abs(r["diff"]) <= FX_ROUNDING else "unbalanced"
            found.setdefault(key, []).append(r)
        cur.execute("""SELECT a.trans_id, a.chart_id, a.transdate, a.amount FROM acc_trans a
                       LEFT JOIN chart c ON (c.id = a.chart_id) WHERE c.id IS NULL
                       ORDER BY a.transdate, a.trans_id""")
        found["missing_account"] = cur.fetchall()
        cur.execute("""SELECT a.trans_id, min(a.transdate) AS transdate, count(*) AS lines FROM acc_trans a
                       WHERE NOT EXISTS (SELECT 1 FROM gl WHERE gl.id = a.trans_id)
                         AND NOT EXISTS (SELECT 1 FROM ar WHERE ar.id = a.trans_id)
                         AND NOT EXISTS (SELECT 1 FROM ap WHERE ap.id = a.trans_id)
                       GROUP BY a.trans_id ORDER BY 2, 1""")
        found["orphan"] = cur.fetchall()
        rows: List[Dict[str, Any]] = []
        mismatch: List[Dict[str, Any]] = []
        for module, link, sign in (("ar", "%AR", 1), ("ap", "%AP", -1)):
            cur.execute(f"""SELECT x.id AS trans_id, x.invnumber AS reference, x.transdate, x.curr, x.amount,
                                   x.paid, coalesce(t.account, 0) AS account
                            FROM {module} x
                            LEFT JOIN (SELECT a.trans_id, sum(a.amount) AS account FROM acc_trans a
                                       JOIN chart c ON (c.id = a.chart_id) WHERE c.link LIKE %s
                                       GROUP BY a.trans_id) t ON (t.trans_id = x.id)
                            ORDER BY x.transdate, x.id""", (link,))
            for r in cur.fetchall():
                r["module"] = module
                amount, paid = to_decimal(r["amount"]), to_decimal(r["paid"])
                account = to_decimal(r["account"]) * sign * -1          # AR: debet = negatief in acc_trans
                if abs(paid) > abs(amount) + Decimal("0.005"):
                    rows.append(dict(r, amount=amount, paid=paid))
                if abs((amount - paid) - account) >= Decimal("0.005"):
                    mismatch.append(dict(r, amount=amount, paid=paid, account=account.quantize(Decimal("0.01")),
                                         diff=(amount - paid - account).quantize(Decimal("0.01"))))
        found["overpaid"] = rows
        found["arap_mismatch"] = mismatch
    skipped = int((info or {}).get("skipped") or 0)
    out: List[Finding] = []
    for key, level in CHECKS:
        if key == "upgrade_skipped":
            out.append(Finding(key, level if skipped else "ok", skipped))
            continue
        items = found.get(key) or []
        out.append(Finding(key, level if items else "ok", len(items), items[:MAX_ROWS]))
    return out


# ------------------------------------------------------------------ cijfers

def counts_per_year(conn: psycopg.Connection) -> List[Dict[str, Any]]:
    """Per jaar: memoriaalboekingen, verkoop- en inkoopboekingen en grootboekregels."""
    with _db.cursor(conn) as cur:
        cur.execute("""SELECT y, sum(gl)::int AS gl, sum(ar)::int AS ar, sum(ap)::int AS ap, sum(lines)::int AS lines
                       FROM (SELECT extract(year FROM transdate)::int AS y, 1 AS gl, 0 AS ar, 0 AS ap, 0 AS lines FROM gl
                             UNION ALL SELECT extract(year FROM transdate)::int, 0, 1, 0, 0 FROM ar
                             UNION ALL SELECT extract(year FROM transdate)::int, 0, 0, 1, 0 FROM ap
                             UNION ALL SELECT extract(year FROM transdate)::int, 0, 0, 0, 1 FROM acc_trans) t
                       WHERE y IS NOT NULL GROUP BY y ORDER BY y""")
        return [{"year": r["y"], "gl": r["gl"], "ar": r["ar"], "ap": r["ap"], "lines": r["lines"]}
                for r in cur.fetchall()]


def payment_balances(conn: psycopg.Connection, asof: date) -> List[Dict[str, Any]]:
    """Saldo op ``asof`` van elke betaalrekening (kas/bank: activa met link AR_paid of AP_paid, zoals het dashboard),
    debet positief."""
    with _db.cursor(conn) as cur:
        cur.execute("""SELECT c.accno, c.description, coalesce(b.iban, '') AS iban,
                              -coalesce(sum(a.amount) FILTER (WHERE a.transdate <= %s), 0) AS balance
                       FROM chart c LEFT JOIN acc_trans a ON (a.chart_id = c.id) LEFT JOIN bank b ON (b.id = c.id)
                       WHERE c.charttype = 'A' AND c.category = 'A'
                         AND (c.link LIKE '%%AR_paid%%' OR c.link LIKE '%%AP_paid%%')
                       GROUP BY c.id, c.accno, c.description, b.iban ORDER BY c.accno""", (asof,))
        rows = cur.fetchall()
        p = _db.get_precision(cur)
    return [dict(r, balance=round_amount(to_decimal(r["balance"]), p) + 0) for r in rows]


def last_transdate(conn: psycopg.Connection) -> Optional[date]:
    with _db.cursor(conn) as cur:
        cur.execute("SELECT max(transdate) AS d FROM acc_trans")
        return cur.fetchone()["d"]


def rgs_complete(conn: psycopg.Connection) -> bool:
    """Elke open rekening (geen kopregel) heeft een RGS-code (``x_account_rgs``, welke versie ook)."""
    ensure_schema(conn)
    with _db.cursor(conn) as cur:
        cur.execute("""SELECT NOT EXISTS (SELECT 1 FROM chart c WHERE c.charttype = 'A'
                                          AND NOT coalesce(c.closed, false)
                                          AND NOT EXISTS (SELECT 1 FROM x_account_rgs r WHERE r.chart_id = c.id)) AS x""")
        return cur.fetchone()["x"]


def rgs_missing(conn: psycopg.Connection) -> Tuple[int, int]:
    """(open rekeningen zonder RGS-code, open rekeningen); zelfde selectie als ``rgs_complete`` (deelstap 6d-2)."""
    ensure_schema(conn)
    with _db.cursor(conn) as cur:
        cur.execute("""SELECT count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM x_account_rgs r
                                                                 WHERE r.chart_id = c.id)) AS missing,
                              count(*) AS total
                       FROM chart c WHERE c.charttype = 'A' AND NOT coalesce(c.closed, false)""")
        r = cur.fetchone()
        return r["missing"], r["total"]


def open_per_relation(conn: psycopg.Connection, arap: str, asof: date) -> List[Dict[str, Any]]:
    """Openstaand per klant/leverancier op ``asof`` in de standaardvaluta: aantal boekingen, totaal en vervallen
    deel. Zelfde selectie en bedrag per boeking als het dashboard (``Dashboard.open_items``, ``open_asof``; per
    factuur gelijk aan de ouderdomsanalyse), maar in één query: de ouderdomsanalyse zoekt per relatie en valuta
    (administratie A: 15 s tegen 0,1 s)."""
    from .reports_arap import open_asof
    if arap not in ("ar", "ap"):
        raise ValueError("arap moet 'ar' of 'ap' zijn")
    vc = "customer" if arap == "ar" else "vendor"
    with _db.cursor(conn) as cur:
        p = _db.get_precision(cur)
        cur.execute(f"""SELECT a.{vc}_id AS vc_id, coalesce(a.duedate, a.transdate) AS duedate,
                               {open_asof(arap)} AS open
                        FROM {arap} a
                        WHERE a.approved = '1' AND a.transdate <= %(todate)s
                          AND (a.amount::numeric != a.paid::numeric
                               OR EXISTS (SELECT 1 FROM acc_trans x WHERE x.trans_id = a.id
                                          AND x.transdate > %(todate)s::date))""", {"todate": asof})
        found = [r for r in cur.fetchall() if to_decimal(r["open"]) != 0]
        cur.execute(f"SELECT id, name, {vc}number AS number FROM {vc} WHERE id = ANY(%s)",
                    ([r["vc_id"] for r in found],))
        names = {r["id"]: r for r in cur.fetchall()}
    out: Dict[Any, Dict[str, Any]] = {}
    zero = Decimal(0)
    for r in found:
        amount = round_amount(to_decimal(r["open"]), p)
        v = names.get(r["vc_id"]) or {"name": "", "number": ""}
        row = out.setdefault(r["vc_id"], {"name": v["name"], "number": v["number"], "count": 0, "open": zero,
                                          "overdue": zero})
        row["count"] += 1
        row["open"] += amount
        if r["duedate"] < asof:
            row["overdue"] += amount
    return sorted(out.values(), key=lambda x: ((x["name"] or "").lower(), x["number"] or ""))
