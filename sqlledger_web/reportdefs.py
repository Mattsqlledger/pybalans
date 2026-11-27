"""Rapporten in de webapp (stap 3): zoekvelden per rapport en de omzetting van
het bibliotheekresultaat naar tabellen.

Rekenen gebeurt in de bibliotheek; hier worden alleen velden ingelezen en de
regels uit ``display``/``rows`` naast elkaar gezet (R8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

import psycopg

from sqlledger import (ArapLedger, ArapSearch, ChartOfAccounts, GeneralLedger, Reports, TransactionSearch,
                       UserConfig)
from sqlledger._db import currencies, cursor
from sqlledger.payments import Payments
from sqlledger.dates import fiscal_dates, fiscal_label, fiscal_start_year, fiscal_year, format_date, from_to

from . import plchart
from .i18n import N_, gettext as _
from .tables import Col, Row, Table

# ------------------------------------------------------------------ velden

Options = Sequence[Tuple[str, str]]


@dataclass
class Field:
    name: str
    label: str
    type: str                     # text | int | date | select | radio | check | checks | period | asof
    options: Any = None           # lijst (waarde, label) of functie(Lib) -> lijst
    default: Any = None
    more: bool = False            # achter "Meer opties" (R5)
    group: str = ""               # losse vinkjes bovenaan: kader met deze kop (v1.0.1 PB12; leeg = "Opties")

    def choices(self, lib: "Lib") -> Options:
        return self.options(lib) if callable(self.options) else (self.options or [])


@dataclass
class Lib:
    """De bibliotheek op de administratie van dit verzoek."""
    conn: psycopg.Connection
    cfg: UserConfig

    @property
    def reports(self) -> Reports:
        return Reports(self.conn, self.cfg)

    @property
    def ca(self) -> ChartOfAccounts:
        return ChartOfAccounts(self.conn, self.cfg)

    @property
    def gl(self) -> GeneralLedger:
        return GeneralLedger(self.conn, self.cfg)

    @property
    def arap(self) -> ArapLedger:
        return ArapLedger(self.conn, self.cfg)

    @property
    def payments(self) -> Payments:
        return Payments(self.conn, self.cfg)

    def precision(self) -> int:
        with cursor(self.conn) as cur:
            cur.execute("SELECT fldvalue FROM defaults WHERE fldname = 'precision'")
            r = cur.fetchone()
        try:
            return int(r["fldvalue"]) if r and r["fldvalue"] else 2
        except ValueError:
            return 2


PERIODS = [("", N_("Vrije datums")), ("month", N_("Maand")), ("quarter", N_("Kwartaal")), ("year", N_("Jaar")),
           ("fiscal", N_("Boekjaar"))]
#: sleutel in de zoekwaarden met de laatste maand van het boekjaar (JA3); geen voorkeur, komt uit ``defaults``
YEAREND = "_yearend"
QUARTERS = [(str(i), f"Q{i}") for i in range(1, 5)]


def _departments(lib: Lib) -> Options:
    return [("", _("alle"))] + [(str(d["id"]), d["description"]) for d in lib.gl.departments()]


def _projects(lib: Lib) -> Options:
    return [("", _("alle"))] + [(str(p["id"]), f"{p['projectnumber']} {p['description'] or ''}".strip())
                             for p in lib.gl.projects()]


def _currencies(lib: Lib) -> Options:
    with cursor(lib.conn) as cur:
        return [("", _("standaardvaluta"))] + [(c, c) for c in currencies(cur) if c]


def _accounts(lib: Lib) -> Options:
    return [("", "")] + [(a["accno"], f"{a['accno']} {a['description']}")
                         for a in lib.ca.all_accounts() if a["charttype"] == "A"]


def _tax_accounts(db: str):
    def f(lib: Lib) -> Options:
        return [(a["accno"], f"{a['accno']} {a['translation'] or a['description']}")
                for a in lib.reports.tax_accounts(db)["taxaccounts"]]
    return f


def _payment_accounts(db: str):
    def f(lib: Lib) -> Options:
        return [(a["accno"], f"{a['accno']} {a['translation'] or a['description']}")
                for a in lib.reports.payment_accounts(db)]
    return f


def period_field(more=False, open_from=False) -> Field:
    """``open_from``: "Van" standaard leeg (openstaande posten: ook die van eerdere jaren, golf 2 F2)."""
    return Field("period", N_("Periode"), "period", default="open_from" if open_from else None, more=more)


DEPARTMENT = Field("department_id", N_("Afdeling"), "select", _departments, "", more=True)
PROJECT = Field("project_id", N_("Project"), "select", _projects, "", more=True)
ACCOUNTTYPE = Field("accounttype", N_("Rekeningen"), "radio", [("standard", N_("Standaard")), ("gifi", N_("GIFI"))],
                    "standard", more=True)
L_HEADING = Field("l_heading", N_("Koppen tonen"), "check", default=True, more=True)
L_SUBTOTAL = Field("l_subtotal", N_("Subtotalen"), "check", default=True, more=True)
METHOD = Field("method", N_("Stelsel"), "radio", [("accrual", N_("Factuurstelsel")), ("cash", N_("Kasstelsel"))],
               "accrual", more=True)
INCLUDEPERIOD = Field("includeperiod", N_("Kolommen"), "radio",
                      [("", N_("Geheel")), ("quarter", N_("Per kwartaal")), ("month", N_("Per maand"))], "")
PREVIOUSYEAR = Field("previousyear", N_("Vorig jaar ernaast"), "check", default=False, group=N_("Vergelijken"))
#: gekozen rekening, kop of rubriek in de grafiek (v1.1, GR1); verborgen, geen voorkeur
CHART_SELECT = Field("grafiek", N_("Grafiek"), "hidden", default="")
STATEMENT_MORE = [
    Field("currency", N_("Valuta"), "select", _currencies, "", more=True),
    Field("decimalplaces", N_("Decimalen"), "int", default=None, more=True),
    METHOD, ACCOUNTTYPE, DEPARTMENT, PROJECT,
    Field("reversedisplay", N_("Omgekeerde weergave"), "check", default=False, more=True),
    L_HEADING, L_SUBTOTAL,
    Field("l_accno", N_("Rekeningnummer tonen"), "check", default=True, more=True),
]


def default_values(fields: List[Field], today: date, yearend: int = 12) -> Dict[str, Any]:
    """Beginwaarden; ``yearend`` = laatste maand van het boekjaar (JA3: "Van" = begin van het lopende boekjaar)."""
    v: Dict[str, Any] = {YEAREND: yearend}
    fyear = str(fiscal_start_year(today, yearend))
    for f in fields:
        if f.type == "period":
            v.update(fromdate="" if f.default == "open_from" else fiscal_year(today, yearend)[0].isoformat(),
                     todate=today.isoformat(),
                     period="", year=str(today.year), month=str(today.month),
                     quarter=str((today.month - 1) // 3 + 1), fyear=fyear)
        elif f.type == "asof":
            v.update(todate=today.isoformat(), period="", year=str(today.year),
                     month=str(today.month), quarter=str((today.month - 1) // 3 + 1), fyear=fyear)
        elif f.type == "checks":
            v[f.name] = list(f.default) if f.default is not None else None   # None = alles
        else:
            v[f.name] = f.default
    return v


def _iso(value: str) -> Optional[str]:
    try:
        return date.fromisoformat(value).isoformat() if value else None
    except ValueError:
        return None


def parse_values(fields: List[Field], q, base: Dict[str, Any], full: bool) -> Tuple[Dict[str, Any], List[str]]:
    """Zoekvelden uit de url. ``full``: het formulier is verstuurd (een ontbrekend
    vinkje = uit); anders alleen aanwezige velden over ``base`` heen leggen."""
    v = dict(base)
    errors: List[str] = []

    def has(name):
        return name in q

    for f in fields:
        if f.type == "period" and (has("fromdate") or has("todate")) and not has("period"):
            v["period"] = ""        # doorklikken met datums: vrije datums
        if f.type in ("period", "asof"):
            names = ["todate", "period", "year", "month", "quarter", "fyear"] + \
                (["fromdate"] if f.type == "period" else [])
            for n in names:
                if has(n):
                    raw = q.get(n, "").strip()
                    if n in ("fromdate", "todate"):
                        iso = _iso(raw)
                        if raw and not iso:
                            errors.append(_("Ongeldige datum: {value}").format(value=raw))
                        v[n] = iso
                    elif n == "period":
                        v[n] = raw if raw in dict(PERIODS) else ""
                    else:
                        v[n] = raw if raw.isdigit() else v.get(n)
        elif f.type == "check":
            if full or has(f.name):
                v[f.name] = q.get(f.name) in ("1", "on", "Y", "true")
        elif f.type == "checks":
            if full or has(f.name):
                v[f.name] = [x for x in q.getlist(f.name) if x]
        elif has(f.name):
            raw = q.get(f.name, "").strip()
            if f.type == "int":
                v[f.name] = int(raw) if raw.isdigit() else None
            elif f.type == "date":
                v[f.name] = _iso(raw)
                if raw and not v[f.name]:
                    errors.append(_("Ongeldige datum: {value}").format(value=raw))
            elif f.type in ("select", "radio") and isinstance(f.options, list):
                v[f.name] = raw if raw in {o[0] for o in f.options} else f.default
            else:
                v[f.name] = raw[:200]
    return v, errors


def period_dates(v: Dict[str, Any]) -> Tuple[Optional[date], Optional[date]]:
    """Van/tot uit de snelkeuze (maand, kwartaal, jaar, boekjaar) of de vrije datums (R4).

    Boekjaar (JA3 B): ``fyear`` = beginjaar, de eindmaand staat onder ``YEAREND`` (anders december)."""
    p = v.get("period") or ""
    year = int(v.get("year") or date.today().year)
    if p == "month":
        return from_to(year, int(v.get("month") or 1), 1)
    if p == "quarter":
        return from_to(year, 3 * int(v.get("quarter") or 1) - 2, 3)
    if p == "year":
        return from_to(year, 1, 12)
    if p == "fiscal":
        return fiscal_dates(int(v.get("fyear") or year), int(v.get(YEAREND) or 12))
    f, t = v.get("fromdate"), v.get("todate")
    return (date.fromisoformat(f) if f else None), (date.fromisoformat(t) if t else None)


def asof_date(v: Dict[str, Any]) -> Optional[date]:
    p = v.get("period") or ""
    if p:
        return period_dates(v)[1]
    t = v.get("todate")
    return date.fromisoformat(t) if t else None


def _int(v) -> Optional[int]:
    try:
        return int(v) if v not in (None, "") else None
    except ValueError:
        return None


# ------------------------------------------------------------------ resultaat

@dataclass
class Result:
    tables: List[Table]
    info: List[str] = field(default_factory=list)
    subtitle: str = ""
    paginate: bool = False
    chart: Any = None            # grafiek bij winst en verlies (v1.1, plchart.PlChart)


@dataclass
class Report:
    key: str
    title: str
    module: str
    fields: List[Field]
    run: Callable[[Lib, Dict[str, Any], "Fmt"], Result]
    autorun: bool = False        # zonder formulier meteen tonen (rekeningschema)
    chart: bool = False          # keuze Tabel / Grafiek / Beide (v1.1, alleen winst en verlies: GR5)
    help: str = ""               # helpblok templates/help/<help>.html (HELP_ANALYSE golf 2)


@dataclass
class Fmt:
    """Opmaak van de gebruiker (voor kolomkoppen en informatieregels)."""
    nf: str
    df: str

    def date(self, d: Optional[date]) -> str:
        return format_date(d, self.df) if d else ""

    def period(self, f: Optional[date], t: Optional[date]) -> str:
        if f and t:
            return _("{start} t/m {end}").format(start=self.date(f), end=self.date(t))
        if t:
            return _("t/m {date}").format(date=self.date(t))
        if f:
            return _("vanaf {date}").format(date=self.date(f))
        return _("alle datums")


LABELS = {
    "Income": N_("Opbrengsten"), "Expenses": N_("Kosten"), "Assets": N_("Activa"), "Liabilities": N_("Passiva"),
    "Equity": N_("Eigen vermogen"), "Total Income": N_("Totaal opbrengsten"), "Total Expenses": N_("Totaal kosten"),
    "Income / (Loss)": N_("Resultaat"), "Total Assets": N_("Totaal activa"),
    "Total Liabilities": N_("Totaal passiva"), "Total Equity": N_("Totaal eigen vermogen"),
    "Current Earnings": N_("Resultaat lopend boekjaar"), "Total": N_("Totaal"),
}


def link(path: str, **params) -> str:
    return path + "?" + urlencode({k: v for k, v in params.items() if v not in (None, "", False)})


def booking_link(trans_id) -> Optional[str]:
    return f"/boekingen/{int(trans_id)}" if trans_id else None


def mutations_link(accno: str, gifi: bool, fromdate: Optional[date], todate: Optional[date],
                   department_id=None, project_id=None) -> str:
    return link("/rapporten/rekeningmutaties", **{"gifi_accno" if gifi else "accno": accno},
                fromdate=fromdate.isoformat() if fromdate else "",
                todate=todate.isoformat() if todate else "",
                department_id=department_id or "", project_id=project_id or "", run=1)


# ------------------------------------------------------------------ grootboekrapporten

def run_trial_balance(lib: Lib, v, fmt: Fmt) -> Result:
    fdate, tdate = period_dates(v)
    gifi = v.get("accounttype") == "gifi"
    rep = lib.reports.trial_balance(
        fromdate=fdate, todate=tdate, department_id=_int(v.get("department_id")),
        project_id=_int(v.get("project_id")), accounttype="gifi" if gifi else "standard",
        all_accounts=bool(v.get("all_accounts")), l_heading=bool(v.get("l_heading")),
        l_subtotal=bool(v.get("l_subtotal")))
    p = int(rep.info.get("precision") or 2)
    cols = [Col("accno", _("Rekening")), Col("description", _("Omschrijving"))] + [
        Col(k, _(label), "amount", p) for k, label in zip(
            rep.columns, (N_("Beginsaldo"), N_("Debet"), N_("Credit"), N_("Eindsaldo")))]
    rows = []
    for d in rep.display:
        vals = dict(zip(rep.columns, d.values))
        if d.kind == "heading":
            rows.append(Row("heading", {"accno": d.accno, "description": d.description}))
        elif d.kind == "account":
            url = mutations_link(d.accno, gifi, rep.fromdate, rep.todate,
                                 v.get("department_id"), v.get("project_id"))
            rows.append(Row("row", {"accno": d.accno, "description": d.description, **vals},
                            {"accno": url, "description": url}))
        elif d.kind == "subtotal":
            rows.append(Row("subtotal", {"description": d.description, **vals}))
        else:
            rows.append(Row("total", {"description": _("Totaal"), "debit": vals["debit"],
                                      "credit": vals["credit"]}))
    return Result([Table(cols, rows)], [fmt.period(rep.fromdate, rep.todate)])


def statement_columns(columns: List[Tuple[str, int]], kind: str, reverse: bool = False) -> List[Tuple[str, int]]:
    """Kolomvolgorde van winst en verlies en balans (PB22): de periodes van oud naar nieuw (januari → december,
    Q1 → Q4), het geheel als laatste kolom; per periode dit jaar en dan vorig jaar. SQL-Ledger (en de bibliotheek)
    zet het geheel vooraan en de periodes van nieuw naar oud. Bij de winst-en-verliesrekening is periode 1 de eerste
    maand, bij de balans periode 0 de peildatum en periode i i maanden (kwartalen) eerder. "Omgekeerde weergave"
    draait de hele rij om (zoals in SQL-Ledger: ook vorig jaar vóór dit jaar)."""
    years = [y for y in ("this", "previous") if any(c[0] == y for c in columns)]
    idx = sorted({c[1] for c in columns if c[1] != 0}, reverse=kind != "income")
    order = [(y, p) for p in idx + [0] for y in years if (y, p) in columns]
    return list(reversed(order)) if reverse else order


def _statement(lib: Lib, v, fmt: Fmt, kind: str) -> Result:
    gifi = v.get("accounttype") == "gifi"
    common = dict(includeperiod=v.get("includeperiod") or None, previousyear=bool(v.get("previousyear")),
                  currency=v.get("currency") or None, decimalplaces=_int(v.get("decimalplaces")),
                  department_id=_int(v.get("department_id")), project_id=_int(v.get("project_id")),
                  accounttype="gifi" if gifi else "standard", method=v.get("method") or "accrual",
                  l_heading=bool(v.get("l_heading")),
                  l_subtotal=bool(v.get("l_subtotal")))
    if kind == "income":
        fdate, tdate = period_dates(v)
        rep = lib.reports.income_statement(fromdate=fdate, todate=tdate, **common)
    else:
        fdate, tdate = None, asof_date(v)
        rep = lib.reports.balance_sheet(todate=tdate, **common)
    dp = int(rep.info.get("decimalplaces") or 0)
    columns = statement_columns(rep.columns, kind, bool(v.get("reversedisplay")))
    keys = [f"v{i}" for i in range(len(columns))]
    pos = {c: i for i, c in enumerate(rep.columns)}
    cols = ([Col("accno", _("Rekening"))] if v.get("l_accno") else []) + [Col("description", "")]
    for key, (year, idx) in zip(keys, columns):
        per = rep.periods[idx]
        f, t = per.this if year == "this" else per.previous
        cols.append(Col(key, fmt.period(f, t) if kind == "income" else fmt.date(t), "amount", dp))
    f0, t0 = rep.periods[0].this if 0 in rep.periods else (fdate, tdate)
    rows = []
    income = kind == "income"
    for d in rep.display:
        label = _(LABELS[d.description]) if d.description in LABELS else d.description
        vals = {key: d.values[pos[c]] for key, c in zip(keys, columns)} if d.values else {}
        if d.kind == "section":
            rows.append(Row("section", {"description": label}, chart=d.category if income else None))
        elif d.kind == "heading":
            rows.append(Row("heading", {"accno": d.accno, "description": label}, chart=d.accno if income else None))
        elif d.kind == "account":
            url = mutations_link(d.accno, gifi, f0, t0, v.get("department_id"), v.get("project_id"))
            rows.append(Row("row", {"accno": d.accno, "description": label, **vals},
                            {"accno": url, "description": url}, dash="-", chart=d.accno if income else None))
        elif d.kind == "current_earnings":
            rows.append(Row("row", {"description": label, **vals}, dash="-"))
        else:
            rows.append(Row(d.kind if d.kind in ("subtotal", "total", "result") else "subtotal",
                            {"accno": d.accno if d.kind == "subtotal" else None,
                             "description": label, **vals}))
    info = [fmt.period(fdate or rep.periods[0].this[0], tdate) if kind == "income"
            else _("Per {date}").format(date=fmt.date(tdate))]
    if rep.currency:
        info.append(_("Valuta {currency}").format(currency=rep.currency))
    chart = None
    if income:
        chart = plchart.build(rep, fmt, dp, v.get("includeperiod") or None, v.get("grafiek") or None,
                              {d.accno: d.description for d in rep.display if d.accno})
    return Result([Table(cols, rows)], info, chart=chart)


def run_income_statement(lib, v, fmt):
    return _statement(lib, v, fmt, "income")


def run_balance_sheet(lib, v, fmt):
    return _statement(lib, v, fmt, "balance")


def run_chart(lib: Lib, v, fmt: Fmt) -> Result:
    accounts = lib.ca.all_accounts()
    p = lib.precision()
    cols = [Col("accno", _("Rekening")), Col("gifi_accno", _("GIFI")), Col("description", _("Omschrijving")),
            Col("debit", _("Debet"), "amount", p), Col("credit", _("Credit"), "amount", p), Col("closed", _("Gesloten"))]
    rows = []
    debit = credit = Decimal(0)
    for a in accounts:
        cells = {k: a[k] for k in ("accno", "gifi_accno", "description", "debit", "credit")}
        if a["charttype"] == "H":
            rows.append(Row("heading", cells))
        else:
            cells["closed"] = "✓" if a["closed"] else ""
            rows.append(Row("row", cells, {
                "accno": link("/rapporten/rekeningmutaties", accno=a["accno"]),
                "gifi_accno": link("/rapporten/rekeningmutaties", gifi_accno=a["gifi_accno"])
                if a["gifi_accno"] else None}))
        # ca.pl chart_of_accounts telt debet en credit van alle regels op
        debit += a["debit"] or 0
        credit += a["credit"] or 0
    rows.append(Row("total", {"debit": debit, "credit": credit}, dash="0"))
    return Result([Table(cols, rows)])


SORT_TX = [("transdate", N_("Datum")), ("reference", N_("Nummer")), ("description", N_("Omschrijving"))]
DIRECTION = Field("direction", N_("Volgorde"), "radio", [("ASC", N_("Oplopend")), ("DESC", N_("Aflopend"))], "ASC", more=True)


def run_account_transactions(lib: Lib, v, fmt: Fmt) -> Result:
    accno = v.get("accno") or None
    gifi = v.get("gifi_accno") or None
    if not accno and not gifi:
        raise ValueError(_("Kies een rekening."))
    fdate, tdate = period_dates(v)
    sort = v.get("sort") or None
    out = lib.ca.account_transactions(
        accno, gifi_accno=None if accno else gifi, fromdate=fdate, todate=tdate,
        department_id=_int(v.get("department_id")), project_id=_int(v.get("project_id")),
        sort=sort, direction=v.get("direction") or "ASC", l_subtotal=bool(v.get("l_subtotal")))
    p = int(out.info.get("precision") or 2)
    paid = "_paid" in (out.info.get("link") or "")
    cols = [Col("transdate", _("Datum"), "date"), Col("reference", _("Nummer")), Col("description", _("Omschrijving"))]
    if paid:
        cols += [Col("source", _("Kenmerk")), Col("cleared", _("Afgeletterd"), "date")]
    if v.get("l_accno"):
        cols.append(Col("contra", _("Debiteuren/crediteuren")))
    cols += [Col("debit", _("Debet"), "amount", p), Col("credit", _("Credit"), "amount", p),
             Col("balance", _("Saldo"), "amount", p, "0")]
    rows: List[Row] = []
    data = iter(out.rows)
    for d in out.display:
        dr, cr, bal = d.values
        if d.kind == "balance":
            rows.append(Row("balance", {"description": _("Beginsaldo"), "balance": bal}))
        elif d.kind == "row":
            r = next(data)
            cells = {k: r.get(k) for k in ("transdate", "reference", "description", "source", "cleared")}
            cells.update(contra=" ".join(a.strip() for a in (r.get("accno") or [])),
                         debit=dr, credit=cr, balance=bal)
            rows.append(Row("row", cells, {"reference": booking_link(r["id"])}))
        elif d.kind == "subtotal":
            rows.append(Row("subtotal", {"debit": dr, "credit": cr}))
        else:
            rows.append(Row("total", {"description": _("Totaal"), "debit": dr, "credit": cr, "balance": bal}))
    name = f"{'GIFI ' if not accno else ''}{accno or gifi} {out.info.get('description') or ''}".strip()
    return Result([Table(cols, rows)], [fmt.period(out.fromdate, out.todate)], subtitle=name, paginate=True)


# ------------------------------------------------------------------ journaal (gl.pl search/transactions)

JOURNAL_COLUMNS = [("id", N_("Id")), ("transdate", N_("Datum")), ("reference", N_("Nummer")), ("description", N_("Omschrijving")),
                   ("name", N_("Relatie")), ("vcnumber", N_("Relatienummer")), ("address", N_("Adres")),
                   ("department", N_("Afdeling")), ("notes", N_("Opmerkingen")), ("debit", N_("Debet")), ("credit", N_("Credit")),
                   ("source", N_("Kenmerk")), ("memo", N_("Memo")), ("lineitem", N_("Artikelregel")), ("accno", N_("Rekening")),
                   ("gifi_accno", N_("GIFI")), ("contra", N_("Tegenrekening"))]
JOURNAL_SORT = [(k, label) for k, label in JOURNAL_COLUMNS
                if k not in ("address", "notes", "debit", "credit", "contra")]
JOURNAL_DEFAULT = ["transdate", "reference", "description", "debit", "credit"]   # gl.pl search
CATEGORIES = [("X", N_("Alle")), ("A", N_("Activa")), ("L", N_("Passiva")), ("Q", N_("Eigen vermogen")),
              ("I", N_("Opbrengsten")), ("E", N_("Kosten"))]


def _amount(raw) -> Optional[Decimal]:
    try:
        return Decimal(str(raw).replace(",", ".")) if raw not in (None, "") else None
    except ArithmeticError:
        raise ValueError(_("Ongeldig bedrag: {value}").format(value=raw))


def edit_link(module: str, trans_id) -> Optional[str]:
    """Bewerkscherm voor memoriaal; andere boekingen (nog) de leespagina."""
    if not trans_id:
        return None
    return f"/memoriaal/{int(trans_id)}" if module == "gl" else booking_link(trans_id)


def run_journal(lib: Lib, v, fmt: Fmt) -> Result:
    fdate, tdate = period_dates(v)
    show = set(v.get("columns") or JOURNAL_DEFAULT)
    accno = v.get("accno") or None
    gifi = v.get("gifi_accno") or None
    s = TransactionSearch(
        reference=v.get("reference") or None, description=v.get("description") or None,
        name=v.get("name") or None, vcnumber=v.get("vcnumber") or None,
        department_id=_int(v.get("department_id")), lineitem=v.get("lineitem") or None,
        include_lineitem="lineitem" in show, source=v.get("source") or None, memo=v.get("memo") or None,
        notes=v.get("notes") or None, accno=accno, gifi_accno=gifi,
        accnofrom=v.get("accnofrom") or None, accnoto=v.get("accnoto") or None,
        category=v.get("category") or "X", datefrom=fdate, dateto=tdate,
        amountfrom=_amount(v.get("amountfrom")), amountto=_amount(v.get("amountto")),
        splitledger=bool(v.get("splitledger")), sort=v.get("sort") or "transdate",
        direction=v.get("direction") or "ASC", l_subtotal=bool(v.get("l_subtotal")))
    out = lib.gl.list_transactions(s)
    p = int(out.info.get("precision") or 2)
    single = bool(accno or gifi)
    if single:
        show -= {"accno", "gifi_accno", "contra"}
    if s.accnofrom or s.accnoto:
        show.discard("contra")       # gl.pl: geen tegenrekeningen bij een rekeningbereik
    if s.department_id:
        show.discard("department")
    cols = [Col(k, _(label), "amount" if k in ("debit", "credit") else ("date" if k == "transdate" else "text"), p)
            for k, label in JOURNAL_COLUMNS if k in show]
    if single and "_paid" in (out.info.get("link") or ""):
        cols.append(Col("cleared", _("Afgeletterd")))
    if single or s.splitledger:
        cols.append(Col("balance", _("Saldo"), "amount", p, "0"))
    period = dict(fromdate=fdate.isoformat() if fdate else "", todate=tdate.isoformat() if tdate else "")

    def acc_link(a):
        return link("/rapporten/journaal", run=1, accno=a, **period)

    rows: List[Row] = []
    data = iter(out.rows)
    for d in out.display:
        dr, cr, bal = d.values
        if d.kind == "balance":
            rows.append(Row("balance", {"description": _("Beginsaldo"), "balance": bal}))
        elif d.kind == "account":
            first = cols[0].key
            rows.append(Row("heading", {first: d.description.replace("--", " "), "balance": bal},
                            {first: acc_link(d.accno)}, dash="0"))
        elif d.kind == "row":
            r = next(data)
            cells = {k: r.get(k) for k in ("id", "transdate", "reference", "description", "name", "vcnumber",
                                           "address", "department", "notes", "source", "memo", "lineitem",
                                           "accno", "gifi_accno")}
            contra = r.get("contra") or ""
            cells.update(contra=contra if isinstance(contra, str) else " ".join(contra),
                         cleared="*" if r.get("cleared") else "", debit=dr, credit=cr, balance=bal)
            links = {"reference": edit_link(r.get("module"), r.get("id")), "accno": acc_link(r["accno"])}
            rows.append(Row("row", cells, links))
        elif d.kind in ("subtotal", "account_subtotal"):
            rows.append(Row("subtotal", {"debit": dr, "credit": cr, "balance": bal}))
        else:
            rows.append(Row("total", {"description": _("Totaal"), "debit": dr, "credit": cr, "balance": bal}))
    info = [fmt.period(out.datefrom, out.dateto)]
    subtitle = ""
    if accno:
        subtitle = f"{accno} {out.info.get('account_description') or ''}".strip()
    elif gifi:
        subtitle = f"GIFI {gifi} {out.info.get('gifi_account_description') or ''}".strip()
    if s.category != "X":
        info.append(_(dict(CATEGORIES)[s.category]))
    return Result([Table(cols, rows)], info, subtitle=subtitle, paginate=True)


# ------------------------------------------------------------------ debiteuren / crediteuren

BUCKET_LABELS = {"c0": N_("Lopend"), "c15": "15", "c30": "30", "c45": "45", "c60": "60", "c75": "75", "c90": "90"}


def _vc_label(arap: str) -> str:
    """Gemarkeerd, niet vertaald: bij het tonen nog door ``_()`` halen."""
    return N_("Klant") if arap == "ar" else N_("Leverancier")


def _vc_number_label(arap: str) -> str:
    return N_("Klantnummer") if arap == "ar" else N_("Leveranciernummer")


def run_aging(arap: str):
    def run(lib: Lib, v, fmt: Fmt) -> Result:
        tdate = asof_date(v)
        buckets = v.get("buckets") or list(BUCKET_LABELS)
        summary = v.get("summary") != "0"
        rep = lib.reports.aging(arap, todate=tdate, buckets=buckets, overdue=v.get("overdue") == "1",
                                name=v.get("name") or None, vcnumber=v.get("vcnumber") or None,
                                department_id=_int(v.get("department_id")), summary=summary)
        p = int(rep.info.get("precision") or 2)
        cols = [Col("name", _(_vc_label(arap)))]
        if not summary:
            cols += [Col("invnumber", _("Factuur")), Col("transdate", _("Datum"), "date"),
                     Col("duedate", _("Vervaldatum"), "date")]
        cols += [Col(b, _(BUCKET_LABELS[b]), "amount", p) for b in rep.buckets]
        cols.append(Col("total", _("Totaal"), "amount", p))
        rows: List[Row] = []
        data = iter(rep.subtotals if summary else rep.rows)
        for d in rep.display:
            vals = dict(zip(rep.buckets + ["total"], d.values))
            if d.kind == "row":
                r = next(data)
                cells = {"name": r["name"], **vals}
                links = {}
                if not summary:
                    cells.update(invnumber=r.get("invnumber"), transdate=r.get("transdate"),
                                 duedate=r.get("duedate"))
                    links["invnumber"] = booking_link(r.get("id"))
                rows.append(Row("row", cells, links))
            elif d.kind == "subtotal":
                rows.append(Row("subtotal", {"name": d.description, **vals}))
            else:
                rows.append(Row("total", {"name": _("Totaal {name}").format(name=d.description or "").strip(), **vals}))
        if v.get("overdue") == "1":
            line = _("Per {date}, dagen vanaf vervaldatum").format(date=fmt.date(rep.todate))
        else:
            line = _("Per {date}, dagen vanaf factuurdatum").format(date=fmt.date(rep.todate))
        return Result([Table(cols, rows)], [line])
    return run


def run_reminders(lib: Lib, v, fmt: Fmt) -> Result:
    asof = date.fromisoformat(v["asof"]) if v.get("asof") else None
    rep = lib.reports.reminders(name=v.get("name") or None, customernumber=v.get("vcnumber") or None,
                                department_id=_int(v.get("department_id")), asof=asof)
    p = int(rep.info.get("precision") or 2)
    cols = [Col("name", _("Klant")), Col("invnumber", _("Factuur")), Col("transdate", _("Datum"), "date"),
            Col("duedate", _("Vervaldatum"), "date"), Col("curr", _("Valuta")),
            Col("due", _("Openstaand"), "amount", p), Col("level", _("Voorgesteld niveau"))]
    rows = []
    for r in rep.rows:
        rate = r.get("exchangerate") or 1
        # rp.pl reminder toont het openstaande bedrag in de valuta van de factuur
        rows.append(Row("row", {"name": r["name"], "invnumber": r["invnumber"], "transdate": r["transdate"],
                                "duedate": r["duedate"], "curr": r["curr"], "due": r["due"] / rate,
                                "level": r["level"]},
                        {"invnumber": booking_link(r["id"])}))
    return Result([Table(cols, rows)], [_("Peildatum {date}").format(date=fmt.date(rep.info.get('asof') or asof or date.today()))])


def run_tax(db: str, nontaxable: bool):
    def run(lib: Lib, v, fmt: Fmt) -> Result:
        fdate, tdate = period_dates(v)
        accounts = None
        if not nontaxable:
            accounts = v.get("accounts") or [a for a, _ in _tax_accounts(db)(lib)]   # niets gekozen = alle
        rep = lib.reports.tax_report(db, accounts=accounts, fromdate=fdate, todate=tdate,
                                     department_id=_int(v.get("department_id")),
                                     method=v.get("method") or "accrual", summary=v.get("summary") != "0",
                                     nontaxable=nontaxable, sort=v.get("sort") or None,
                                     l_subtotal=bool(v.get("l_subtotal")))
        p = int(rep.info.get("precision") or 2)
        cols = [Col("transdate", _("Datum"), "date"), Col("invnumber", _("Factuur")),
                Col("name", _(_vc_label(db))), Col("taxnumber", _("Btw-nummer")), Col("description", _("Omschrijving"))]
        if not nontaxable:
            cols.append(Col("accno", _("Btw-rekening")))
        cols += [Col("netamount", _("Netto"), "amount", p), Col("tax", _("Btw"), "amount", p),
                 Col("total", _("Totaal"), "amount", p)]
        keys = ("netamount", "tax", "total")
        rows: List[Row] = []
        data = iter(rep.rows)
        for d in rep.display:
            vals = dict(zip(keys, d.values))
            if d.kind == "row":
                r = next(data)
                rows.append(Row("row", {**{k: r.get(k) for k in ("transdate", "invnumber", "name",
                                                                 "taxnumber", "description", "accno")},
                                        **vals}, {"invnumber": booking_link(r.get("id"))}))
            elif d.kind == "subtotal" and d.description == "acctotal":
                if nontaxable or not rep.rows:
                    continue
                rows.append(Row("subtotal", {"description": _("Totaal {account}").format(account=d.accno or "").strip(), **vals}))
            elif d.kind == "subtotal":
                rows.append(Row("subtotal", vals))
            else:
                rows.append(Row("total", {"description": _("Totaal"), **vals}))
        info = [fmt.period(rep.fromdate or fdate, rep.todate or tdate)]
        if v.get("method") == "cash":
            info.append(_("Kasstelsel"))
        return Result([Table(cols, rows)], info)
    return run


def run_payments(db: str):
    def run(lib: Lib, v, fmt: Fmt) -> Result:
        fdate, tdate = period_dates(v)
        accounts = v.get("accounts") or [a for a, _ in _payment_accounts(db)(lib)]   # niets gekozen = alle
        rep = lib.reports.payments(db, accounts=accounts, fromdate=fdate, todate=tdate,
                                   department_id=_int(v.get("department_id")),
                                   description=v.get("description") or None, source=v.get("source") or None,
                                   memo=v.get("memo") or None, name=v.get("name") or None,
                                   vcnumber=v.get("vcnumber") or None,
                                   fx_transaction=bool(v.get("fx_transaction")), sort=v.get("sort") or None,
                                   l_subtotal=bool(v.get("l_subtotal")))
        p = int(rep.info.get("precision") or 2)
        cols = [Col("transdate", _("Datum"), "date"), Col("reference", _("Nummer")), Col("description", _("Omschrijving")),
                Col("name", _(_vc_label(db))), Col("source", _("Kenmerk")), Col("memo", _("Memo")),
                Col("curr", _("Valuta")), Col("paid", _("Bedrag"), "amount", p)]
        names = {a["accno"]: a.get("translation") or a.get("description") or "" for a in rep.accounts}
        flat = [pr for acc in sorted(rep.accounts, key=lambda a: a["accno"]) for pr in acc["payments"]]
        data = iter(flat)
        rows: List[Row] = []
        current = None
        for d in rep.display:
            if d.kind == "row":
                if d.accno != current:
                    current = d.accno
                    rows.append(Row("heading", {"transdate": None, "description": f"{d.accno} {names.get(d.accno, '')}"}))
                pr = next(data)
                rows.append(Row("row", {**{k: pr.get(k) for k in ("transdate", "reference", "description",
                                                                  "name", "source", "memo", "curr")},
                                        "paid": d.values[0]}, {"reference": booking_link(pr.get("trans_id"))}))
            elif d.kind == "subtotal":
                rows.append(Row("subtotal", {"paid": d.values[0]}))
            elif d.accno:
                rows.append(Row("subtotal", {"description": _("Totaal {account}").format(account=d.accno), "paid": d.values[0]}))
            else:
                rows.append(Row("total", {"description": _("Totaal"), "paid": d.values[0]}))
        return Result([Table(cols, rows)], [fmt.period(fdate, tdate)])
    return run


AMOUNT_LABELS = {"netamount": N_("Netto"), "tax": N_("Btw"), "amount": N_("Totaal"), "paid": N_("Betaald"), "due": N_("Openstaand"),
                 "debit": N_("Debet"), "credit": N_("Credit")}


def run_arap_list(vc: str, outstanding: bool):
    def run(lib: Lib, v, fmt: Fmt) -> Result:
        fdate, tdate = period_dates(v)
        dept = None
        if v.get("department_id"):
            dept = dict(_departments(lib)).get(v["department_id"])
        vc_id = int(v["vc_id"]) if str(v.get("vc_id") or "").isdigit() else None
        s = ArapSearch(name=v.get("name") or None, vcnumber=v.get("vcnumber") or None, vc_id=vc_id,
                       invnumber=v.get("invnumber") or None, description=v.get("description") or None,
                       ordnumber=v.get("ordnumber") or None, ponumber=v.get("ponumber") or None,
                       notes=v.get("notes") or None, memo=v.get("memo") or None, source=v.get("source") or None,
                       department=dept, transdatefrom=fdate, transdateto=tdate,
                       open=v.get("status") == "open", closed=v.get("status") == "closed",
                       onhold=bool(v.get("onhold")), paidlate=bool(v.get("paidlate")),
                       paidearly=bool(v.get("paidearly")), outstanding=outstanding,
                       summary=v.get("summary") != "0", sort=v.get("sort") or None,
                       direction=v.get("direction") or "ASC", l_subtotal=bool(v.get("l_subtotal")),
                       l_curr=bool(v.get("l_curr")))
        rep = lib.arap.list_transactions(vc, s)
        p = rep.precision
        cols = [Col("transdate", _("Datum"), "date"), Col("invnumber", _("Factuur")),
                Col("name", _(_vc_label("ar" if vc == "customer" else "ap"))), Col("description", _("Omschrijving")),
                Col("duedate", _("Vervaldatum"), "date"), Col("curr", _("Valuta"))]
        if rep.display and not (rep.search.summary or rep.search.outstanding):
            cols.append(Col("accno", _("Rekening")))
        for c in rep.columns:
            if c in AMOUNT_LABELS:
                cols.append(Col(c, _(AMOUNT_LABELS[c]), "amount", p))
            else:
                cur, k = c.split("_", 1)
                cols.append(Col(c, f"{_(AMOUNT_LABELS[k])} {cur}", "amount", p))
        rows: List[Row] = []
        data = iter(rep.rows)
        for d in rep.display:
            vals = dict(zip(rep.columns, d.values))
            if d.kind == "row":
                r = next(data)
                rows.append(Row("row", {**{k: r.get(k) for k in ("transdate", "invnumber", "name", "description",
                                                                 "duedate", "curr", "accno")}, **vals},
                                {"invnumber": booking_link(r.get("id"))}))
            elif d.kind == "subtotal":
                rows.append(Row("subtotal", vals))
            else:
                rows.append(Row("total", {"description": _("Totaal"), **vals}))
        info = [fmt.period(fdate, tdate)]
        if vc_id:
            with cursor(lib.conn) as cur:
                cur.execute(f"SELECT name, {vc}number AS number FROM {vc} WHERE id = %s", (vc_id,))
                r = cur.fetchone()
            if r:
                info.append(f"{_(_vc_label('ar' if vc == 'customer' else 'ap'))}: {r['number'] or ''} {r['name']}".strip())
        if outstanding:
            info.append(_("Openstaand per einddatum") if tdate else _("Openstaand"))
        return Result([Table(cols, rows)], info, paginate=True)
    return run


# ------------------------------------------------------------------ overzicht

def _aging_fields(arap):
    return [Field("asof", N_("Per datum"), "asof"),
            Field("name", _vc_label(arap), "text", default=""),
            Field("vcnumber", _vc_number_label(arap), "text", default=""),
            Field("summary", N_("Weergave"), "radio", [("1", N_("Per relatie")), ("0", N_("Per factuur"))], "1"),
            Field("overdue", N_("Dagen tellen"), "radio", [("0", N_("vanaf factuurdatum")), ("1", N_("vanaf vervaldatum"))],
                  "0", more=True),
            Field("buckets", N_("Kolommen"), "checks", list(BUCKET_LABELS.items()), ["c0", "c30", "c60", "c90"],
                  more=True),
            DEPARTMENT]


def _tax_fields(db, nontaxable):
    f = [period_field()]
    if not nontaxable:
        f.append(Field("accounts", N_("Btw-rekeningen"), "checks", _tax_accounts(db), None))
    f += [Field("summary", N_("Weergave"), "radio", [("1", N_("Per factuur")), ("0", N_("Per regel"))], "1"),
          METHOD,
          Field("sort", N_("Sorteren op"), "select", [("transdate", N_("Datum")), ("invnumber", N_("Factuur")), ("name", N_("Relatie"))],
                "transdate", more=True),
          Field("l_subtotal", N_("Subtotaal per sorteerwaarde"), "check", default=False, more=True),
          DEPARTMENT]
    return f


def _payment_fields(db):
    return [period_field(),
            Field("accounts", N_("Betaalrekeningen"), "checks", _payment_accounts(db), None),
            Field("name", _vc_label(db), "text", default=""),
            Field("vcnumber", _vc_number_label(db), "text", default="", more=True),
            Field("description", N_("Omschrijving"), "text", default="", more=True),
            Field("source", N_("Kenmerk"), "text", default="", more=True),
            Field("memo", N_("Memo"), "text", default="", more=True),
            Field("fx_transaction", N_("Inclusief koersverschillen"), "check", default=False, more=True),
            Field("sort", N_("Sorteren op"), "select", [("transdate", N_("Datum")), ("name", N_("Relatie")), ("employee", N_("Medewerker"))],
                  "transdate", more=True),
            Field("l_subtotal", N_("Subtotaal per sorteerwaarde"), "check", default=False, more=True),
            DEPARTMENT]


def _list_fields(arap, outstanding):
    vcl = _vc_label(arap)
    f = [period_field(open_from=outstanding),
         Field("vc_id", vcl, "hidden", default=""),       # doorklik vanaf het relatiescherm (fase 2)
         Field("name", vcl, "text", default=""),
         Field("vcnumber", _vc_number_label(arap), "text", default="", more=True),
         Field("invnumber", N_("Factuurnummer"), "text", default=""),
         Field("description", N_("Omschrijving"), "text", default="", more=True)]
    if not outstanding:
        f.append(Field("status", N_("Status"), "radio", [("", N_("Alle")), ("open", N_("Open")), ("closed", N_("Betaald"))], ""))
        f.append(Field("summary", N_("Weergave"), "radio", [("1", N_("Per boeking")), ("0", N_("Per journaalregel"))], "1",
                       more=True))
    f += [Field("ordnumber", N_("Ordernummer"), "text", default="", more=True),
          Field("ponumber", N_("Inkoopordernummer"), "text", default="", more=True),
          Field("notes", N_("Opmerkingen"), "text", default="", more=True),
          Field("source", N_("Betaalkenmerk"), "text", default="", more=True),
          Field("onhold", N_("Alleen geblokkeerd"), "check", default=False, more=True),
          Field("sort", N_("Sorteren op"), "select", [("transdate", N_("Datum")), ("invnumber", N_("Factuur")), ("name", N_("Relatie")),
                                                  ("duedate", N_("Vervaldatum"))], "transdate", more=True),
          DIRECTION,
          Field("l_subtotal", N_("Subtotaal per sorteerwaarde"), "check", default=False, more=True),
          Field("l_curr", N_("Bedragen per valuta"), "check", default=False, more=True),
          DEPARTMENT]
    return f


# ------------------------------------------------------------------ v1.1 interne uren: Uren per project (IN5)

HOURS_KINDS = [("", N_("Alle")), ("customer", N_("Klantprojecten")), ("internal", N_("Interne projecten")),
               ("none", N_("Niet ingedeeld"))]
KIND_SHORT = {"customer": N_("klant"), "internal": N_("intern"), "none": N_("niet ingedeeld")}


def _cost_centres(lib: Lib) -> Options:
    with cursor(lib.conn) as cur:
        cur.execute("SELECT id, description FROM department WHERE role = 'C' ORDER BY rn NULLS LAST, id")
        return [("", _("alle"))] + [(str(r["id"]), r["description"] or "") for r in cur.fetchall()]


def _project_customers(lib: Lib) -> Options:
    with cursor(lib.conn) as cur:
        cur.execute("""SELECT DISTINCT c.id, c.name FROM customer c JOIN project pr ON (pr.customer_id = c.id)
                       ORDER BY c.name, c.id""")
        return [("", _("alle"))] + [(str(r["id"]), r["name"] or "") for r in cur.fetchall()]


def _employees(lib: Lib) -> Options:
    with cursor(lib.conn) as cur:
        cur.execute("SELECT id, name FROM employee ORDER BY name, id")
        return [("", _("alle"))] + [(str(r["id"]), r["name"] or "") for r in cur.fetchall()]


def run_hours_by_project(lib: Lib, v, fmt: Fmt) -> Result:
    from sqlledger.timecards import HoursSearch, hours_by_project
    fdate, tdate = period_dates(v)
    per_employee = v.get("grouping") == "employee"
    rep = hours_by_project(lib.conn, HoursSearch(
        kind=v.get("kind") or "", project_id=_int(v.get("project_id")), department_id=_int(v.get("department_id")),
        customer_id=_int(v.get("customer_id")), employee_id=_int(v.get("employee_id")), datefrom=fdate,
        dateto=tdate, per_employee=per_employee))
    p = rep["precision"]
    cols = [Col("projectnumber", _("Project")), Col("description", _("Omschrijving")), Col("kind", _("Soort")),
            Col("relation", _("Klant / kostenplaats"))]
    if per_employee:
        cols.append(Col("employee", _("Medewerker")))
    cols += [Col("hours", _("Uren"), "amount", p), Col("rate", _("Gem. intern tarief"), "amount", p),
             Col("cost", _("Kostwaarde"), "amount", p), Col("sales", _("Verkoopwaarde"), "amount", p),
             Col("ordered", _("Uren op order"), "amount", p), Col("open", _("Uren nog niet op order"), "amount", p),
             Col("margin", _("Marge"), "amount", p, "-")]
    keys = ("hours", "cost", "sales", "ordered", "open", "margin")

    def add(total: Dict[str, Any], r) -> None:
        for k in keys:
            value = getattr(r, k)
            if value is not None:
                total[k] = total.get(k, Decimal(0)) + value

    def cells(r) -> Dict[str, Any]:
        return {"projectnumber": r.projectnumber, "description": r.description, "kind": _(KIND_SHORT[r.kind]),
                "relation": r.department if r.kind == "internal" else r.customer, "employee": r.employee,
                "rate": r.rate, **{k: getattr(r, k) for k in keys}}

    def tc_link(r) -> str:
        return link("/uren/tijdkaarten", project=r.project_id, employee=r.employee_id or "",
                    van=fdate.isoformat() if fdate else "", tot=tdate.isoformat() if tdate else "")

    rows: List[Row] = []
    grand: Dict[str, Any] = {}
    sub: Dict[str, Any] = {}
    for n, r in enumerate(rep["rows"]):
        rows.append(Row("row", cells(r), {"projectnumber": tc_link(r)}))
        add(grand, r)
        if per_employee:
            add(sub, r)
            nxt = rep["rows"][n + 1] if n + 1 < len(rep["rows"]) else None
            if nxt is None or nxt.project_id != r.project_id:
                rate = sub["cost"] / sub["hours"] if sub.get("hours") else None
                rows.append(Row("subtotal", {"projectnumber": r.projectnumber, "description": _("Totaal project"),
                                             "rate": rate, **sub}))
                sub = {}
    if rows:
        rate = grand["cost"] / grand["hours"] if grand.get("hours") else None
        rows.append(Row("total", {"projectnumber": _("Totaal"), "rate": rate, **grand}))
    info = [fmt.period(fdate, tdate),
            _("Kostwaarde = uren × intern tarief; verkoopwaarde = uren × prijs op de tijdkaart; marge = verkoopwaarde − "
              "kostwaarde. Interne projecten worden niet gefactureerd.")]
    if any(r.kind == "none" for r in rep["rows"]):
        info.append(_("Niet ingedeelde projecten (uit SQL-Ledger of van vóór deze versie) tellen mee zoals klantprojecten."))
    if rep["computed"]:
        info.append(_("{n} tijdkaarten zonder vastgelegd intern tarief (van vóór deze versie of uit SQL-Ledger): "
                      "berekend met het tarief op de datum van de uren.").format(n=rep["computed"]))
    return Result([Table(cols, rows)], info)


REPORTS: Dict[str, Report] = {r.key: r for r in [
    Report("rekeningschema", N_("Rekeningschema"), "reports", [], run_chart, autorun=True),
    Report("journaal", N_("Boekingen zoeken"), "gl", [
        period_field(),
        Field("reference", N_("Nummer"), "text", default=""),
        Field("description", N_("Omschrijving"), "text", default=""),
        Field("name", N_("Relatie"), "text", default=""),
        Field("accno", N_("Rekening"), "select", _accounts, ""),
        Field("splitledger", N_("Per rekening"), "check", default=True),
        Field("vcnumber", N_("Relatienummer"), "text", default="", more=True),
        Field("source", N_("Kenmerk"), "text", default="", more=True),
        Field("memo", N_("Memo"), "text", default="", more=True),
        Field("notes", N_("Opmerkingen"), "text", default="", more=True),
        Field("lineitem", N_("Artikelregel"), "text", default="", more=True),
        Field("accnofrom", N_("Rekening vanaf"), "text", default="", more=True),
        Field("accnoto", N_("Rekening t/m"), "text", default="", more=True),
        Field("amountfrom", N_("Bedrag vanaf"), "text", default="", more=True),
        Field("amountto", N_("Bedrag t/m"), "text", default="", more=True),
        Field("category", N_("Soort rekening"), "radio", CATEGORIES, "X", more=True),
        Field("gifi_accno", N_("GIFI-rekening"), "text", default="", more=True),
        DEPARTMENT,
        Field("columns", N_("Kolommen"), "checks", JOURNAL_COLUMNS, JOURNAL_DEFAULT, more=True),
        Field("sort", N_("Sorteren op"), "select", JOURNAL_SORT, "transdate", more=True),
        DIRECTION,
        Field("l_subtotal", N_("Subtotaal per sorteerwaarde"), "check", default=False, more=True),
    ], run_journal),
    Report("rekeningmutaties", N_("Rekeningmutaties"), "reports", [
        Field("accno", N_("Rekening"), "select", _accounts, ""),
        period_field(),
        Field("sort", N_("Sorteren op"), "select", SORT_TX, "transdate", more=True),
        DIRECTION,
        Field("l_subtotal", N_("Subtotaal per sorteerwaarde"), "check", default=False, more=True),
        Field("l_accno", N_("Debiteuren-/crediteurenrekening tonen"), "check", default=False, more=True),
        Field("gifi_accno", N_("GIFI-rekening"), "text", default="", more=True),
        DEPARTMENT, PROJECT], run_account_transactions),
    Report("proefbalans", N_("Proefbalans"), "reports", [
        period_field(), Field("all_accounts", N_("Ook rekeningen zonder mutaties"), "check", default=False),
        ACCOUNTTYPE, DEPARTMENT, PROJECT, L_HEADING, L_SUBTOTAL], run_trial_balance),
    Report("winst-en-verlies", N_("Winst en verlies"), "reports",
           [period_field(), INCLUDEPERIOD, PREVIOUSYEAR, CHART_SELECT] + STATEMENT_MORE, run_income_statement,
           chart=True),
    Report("balans", N_("Balans"), "reports",
           [Field("asof", N_("Per datum"), "asof"), INCLUDEPERIOD, PREVIOUSYEAR] + STATEMENT_MORE,
           run_balance_sheet),
    Report("overzicht-verkoop", N_("Overzicht verkoop"), "ar", _list_fields("ar", False),
           run_arap_list("customer", False)),
    Report("openstaand-verkoop", N_("Openstaande verkoopfacturen"), "ar", _list_fields("ar", True),
           run_arap_list("customer", True)),
    Report("ouderdom-verkoop", N_("Ouderdomsanalyse debiteuren"), "ar", _aging_fields("ar"), run_aging("ar")),
    Report("aanmaningen", N_("Aanmaningen"), "ar", [
        Field("asof", N_("Peildatum"), "date", default=None),
        Field("name", N_("Klant"), "text", default=""),
        Field("vcnumber", N_("Klantnummer"), "text", default="", more=True), DEPARTMENT], run_reminders),
    Report("btw-verkoop", N_("Btw verkoop (af te dragen)"), "ar", _tax_fields("ar", False), run_tax("ar", False)),
    Report("onbelast-verkoop", N_("Onbelaste omzet"), "ar", _tax_fields("ar", True), run_tax("ar", True)),
    Report("overzicht-inkoop", N_("Overzicht inkoop"), "ap", _list_fields("ap", False),
           run_arap_list("vendor", False)),
    Report("openstaand-inkoop", N_("Openstaande inkoopfacturen"), "ap", _list_fields("ap", True),
           run_arap_list("vendor", True)),
    Report("ouderdom-inkoop", N_("Ouderdomsanalyse crediteuren"), "ap", _aging_fields("ap"), run_aging("ap")),
    Report("btw-inkoop", N_("Btw inkoop (voorbelasting)"), "ap", _tax_fields("ap", False), run_tax("ap", False)),
    Report("onbelast-inkoop", N_("Onbelaste inkoop"), "ap", _tax_fields("ap", True), run_tax("ap", True)),
    Report("ontvangsten", N_("Ontvangsten"), "cash", _payment_fields("ar"), run_payments("ar")),
    Report("betalingen", N_("Betalingen"), "cash", _payment_fields("ap"), run_payments("ap")),
    # v1.1 interne uren (IN5)
    Report("uren-per-project", N_("Uren per project"), "projects", [
        period_field(),
        Field("kind", N_("Soort project"), "radio", HOURS_KINDS, ""),
        Field("grouping", N_("Indeling"), "radio", [("project", N_("Per project")),
                                                    ("employee", N_("Per project en medewerker"))], "project"),
        Field("project_id", N_("Project"), "select", _projects, ""),
        Field("department_id", N_("Kostenplaats"), "select", _cost_centres, "", more=True),
        Field("customer_id", N_("Klant"), "select", _project_customers, "", more=True),
        Field("employee_id", N_("Medewerker"), "select", _employees, "", more=True),
    ], run_hours_by_project),
]}

#: helpblok per rapport (HELP_ANALYSE golf 2): templates/help/<naam>.html
REPORT_HELP = {
    "overzicht-verkoop": "rep_arap_list", "overzicht-inkoop": "rep_arap_list",
    "openstaand-verkoop": "rep_open", "openstaand-inkoop": "rep_open",
    "ouderdom-verkoop": "rep_aging", "ouderdom-inkoop": "rep_aging",
    "btw-verkoop": "rep_tax", "btw-inkoop": "rep_tax", "onbelast-verkoop": "rep_tax", "onbelast-inkoop": "rep_tax",
    "ontvangsten": "rep_payments", "betalingen": "rep_payments",
    "rekeningschema": "rep_chart", "journaal": "rep_journal", "rekeningmutaties": "rep_account",
    "proefbalans": "rep_trial", "winst-en-verlies": "rep_income", "balans": "rep_balance",
    "uren-per-project": "rep_hours",
}
for _key, _help in REPORT_HELP.items():
    REPORTS[_key].help = _help
