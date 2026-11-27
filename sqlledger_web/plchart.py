"""Grafiek bij Winst en verlies (v1.1, GR1–GR5).

Per periode (de kolommen van het rapport: maand, kwartaal of het geheel) twee staven, opbrengsten en kosten, met
een lijn voor het resultaat (GR1); met "Vorig jaar ernaast" een lichtere staaf vorig jaar naast elke staaf en een
stippellijn voor het resultaat vorig jaar (GR4). Een gekozen rekening, groep (kop) of rubriek toont alleen die
bedragen per periode. De bedragen komen uit hetzelfde bibliotheekrapport als de tabel (dus dezelfde filters:
afdeling, project, valuta, stelsel); hier wordt alleen getekend.

De server tekent de SVG (GR3): zichtbaar zonder JavaScript en ook in de PDF (WeasyPrint) en bij Afdrukken. De
kleuren staan als attribuut op de elementen (PDF, afdrukken) en worden op het scherm via CSS-variabelen
overschreven (licht/donker). Kleur is nooit de enige drager: legenda met tekst, stippellijn voor vorig jaar,
``<title>`` per staaf en een tabel met de cijfers onder de grafiek.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlledger.amounts import format_amount
from sqlledger.dates import fiscal_label_for, last_day_of_month

from .i18n import N_, gettext as _

#: weergave boven de tabel (GR2); per gebruiker onthouden
VIEWS = ("tabel", "grafiek", "beide")
VIEW_LABELS = {"tabel": N_("Tabel"), "grafiek": N_("Grafiek"), "beide": N_("Beide")}
DEFAULT_VIEW = "tabel"

MONTHS = (N_("jan"), N_("feb"), N_("mrt"), N_("apr"), N_("mei"), N_("jun"),
          N_("jul"), N_("aug"), N_("sep"), N_("okt"), N_("nov"), N_("dec"))

#: lichte kleuren (PDF, afdrukken, zonder CSS); het scherm zet ze via CSS-variabelen (ook donker)
COLORS = {
    "inc": "#3f78b5", "inc-prev": "#b4cbe4",
    "exp": "#c47a2c", "exp-prev": "#ecc9a2",
    "sel": "#3f78b5", "sel-prev": "#b4cbe4",
    "res": "#1f2933", "res-prev": "#6b7684",
    "text": "#6b7684", "grid": "#d9dee5", "zero": "#6b7684",
}

LEFT, RIGHT, TOP, BOTTOM, PLOT_H = 60, 12, 14, 34, 220


def nice_step(v: float) -> float:
    """Rond een stapgrootte af op 1, 2, 2,5 of 5 maal een macht van tien (ook voor het dashboard)."""
    if v <= 0:
        return 1.0
    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * exp:
            return m * exp
    return 10 * exp


def short_amount(v, nf: str) -> str:
    """Kort bedrag voor een as: 1,5K / 12K / 1,2M (ook voor het dashboard)."""
    v = float(v or 0)
    a = abs(v)
    if a >= 1_000_000:
        n, suf = v / 1_000_000, "M"
    elif a >= 1000:
        n, suf = v / 1000, "K"
    else:
        return format_amount(Decimal(str(round(v))), nf, 0, "0").replace("&nbsp;", "").strip()
    txt = f"{n:.1f}".rstrip("0").rstrip(".") if abs(n) < 10 else f"{n:.0f}"
    if nf in ("1.000,00", "1000,00"):
        txt = txt.replace(".", ",")
    return txt + suf


@dataclass
class Series:
    key: str                    # inc | exp | sel (+ "-prev") | res | res-prev
    label: str
    values: List[Optional[Decimal]]
    titles: List[str] = field(default_factory=list)    # per periode, voor de tooltip

    @property
    def color(self) -> str:
        return COLORS[self.key]

    @property
    def prev(self) -> bool:
        return self.key.endswith("-prev")


@dataclass
class Bar:
    x: float
    y: float
    w: float
    h: float
    key: str
    color: str
    title: str
    negative: bool


@dataclass
class Line:
    key: str
    color: str
    dashed: bool
    points: List[Tuple[float, float, str]]    # x, y, tooltip

    @property
    def path(self) -> str:
        return " ".join(f"{x},{y}" for x, y, __ in self.points)


@dataclass
class PlChart:
    title: str
    desc: str
    groups: List[Dict[str, str]]            # label (as), title (volledige periode)
    bars: List[Series]
    lines: List[Series]
    selected: Optional[str] = None           # gekozen rekening/kop/rubriek (sleutel)
    selected_label: str = ""
    options: List[Tuple[str, str]] = field(default_factory=list)
    nf: str = "1.000,00"
    dp: int = 2
    # geometrie (layout())
    width: int = 0
    height: int = 0
    left: int = LEFT
    zero: float = 0
    ticks: List[Dict[str, Any]] = field(default_factory=list)
    labels: List[Dict[str, Any]] = field(default_factory=list)
    rects: List[Bar] = field(default_factory=list)
    paths: List[Line] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not any(v for s in self.bars + self.lines for v in s.values)

    @property
    def legend(self) -> List[Series]:
        return self.bars + self.lines

    def amount(self, v) -> str:
        return "" if v is None else format_amount(v, self.nf, self.dp, "0").replace("&nbsp;", "").strip()

    def data_rows(self) -> List[Tuple[str, List[str]]]:
        """Tabelalternatief: per periode de bedragen van elke reeks (zelfde volgorde als de legenda)."""
        return [(g["title"], [self.amount(s.values[i]) for s in self.legend]) for i, g in enumerate(self.groups)]


def _period_label(f: Optional[date], t: Optional[date], kind: str, years: bool, fmt) -> str:
    if not f or not t:
        return fmt.period(f, t)
    if kind == "month":
        return _(MONTHS[f.month - 1]) + (f" {f:%y}" if years else "")
    if kind == "quarter":
        if f.day == 1 and f.month % 3 == 1 and t == last_day_of_month(date(f.year, f.month + 2, 1)):
            return f"Q{(f.month - 1) // 3 + 1}" + (f" {f.year}" if years else "")
        return f"{_(MONTHS[f.month - 1])}–{_(MONTHS[t.month - 1])}" + (f" {t:%y}" if years else "")
    return fiscal_label_for(f, t) or fmt.period(f, t)      # "2026" of boekjaar "2025/2026"


def build(rep, fmt, dp: int, includeperiod: Optional[str], selected: Optional[str] = None,
          labels: Optional[Dict[str, str]] = None) -> PlChart:
    """De grafiek bij een winst-en-verliesrapport van de bibliotheek (``Reports.income_statement``).

    ``selected``: rekeningnummer (ook van een kop) of ``I``/``E`` (de rubriek); onbekend = opbrengsten/kosten.
    ``labels``: de (vertaalde) omschrijving per rekeningnummer zoals in de tabel.
    """
    labels = labels or {}
    cols = set(rep.columns)
    idx = sorted(p for p in rep.periods if p != 0 and ("this", p) in cols)
    kind = includeperiod if idx else ""
    idx = idx or [0]
    prev = any(c[0] == "previous" for c in rep.columns)
    this_years = {rep.periods[i].this[0].year for i in idx if rep.periods[i].this[0]}
    groups = []
    for i in idx:
        f, t = rep.periods[i].this
        groups.append({"label": _period_label(f, t, kind, len(this_years) > 1, fmt), "title": fmt.period(f, t)})

    def ptitle(i: int, year: str) -> str:
        per = rep.periods[i]
        f, t = per.this if year == "this" else (per.previous or (None, None))
        return fmt.period(f, t)

    def series(key: str, label: str, get, year: str) -> Series:
        vals, titles = [], []
        for i in idx:
            c = (year, i)
            v = get(c) if c in cols else None
            vals.append(v)
            titles.append(f"{label} {ptitle(i, year)}: " + (format_amount(v, fmt.nf, dp, "0").replace("&nbsp;", "")
                                                              .strip() if v is not None else "–"))
        return Series(key, label, vals, titles)

    last = _("vorig jaar")
    options = [("", _("Opbrengsten en kosten")), ("I", _("Opbrengsten")), ("E", _("Kosten"))]
    known: Dict[str, Tuple[str, str]] = {"I": ("I", _("Opbrengsten")), "E": ("E", _("Kosten"))}
    for cat in ("I", "E"):
        for accno, a in sorted(rep.accounts.get(cat, {}).items()):
            name = f"{accno} {labels.get(accno) or a['description']}"
            known[accno] = (cat, name)
            options.append((accno, name if a["charttype"] == "H" else "  " + name))
    per_label = {"month": _("per maand"), "quarter": _("per kwartaal")}.get(kind, "")

    if selected in known:
        cat, name = known[selected]
        if selected in ("I", "E"):
            get = lambda c: rep.totals[cat].get(c)                                     # noqa: E731
        else:
            amounts = rep.accounts[cat][selected]["amounts"]
            get = lambda c: amounts.get(c)                                              # noqa: E731
        bars = [series("sel", name, get, "this")]
        if prev:
            bars.append(series("sel-prev", f"{name} {last}", get, "previous"))
        chart = PlChart(" ".join(x for x in (name, per_label) if x), "", groups, bars, [], selected, name, options)
        chart.desc = _("Staven: {name}.").format(name=name) + (" " + _("Lichtere staaf: vorig jaar.") if prev else "")
    else:
        inc, exp, res = _("Opbrengsten"), _("Kosten"), _("Resultaat")
        bars = [series("inc", inc, lambda c: rep.totals["I"].get(c), "this")]
        if prev:
            bars.append(series("inc-prev", f"{inc} {last}", lambda c: rep.totals["I"].get(c), "previous"))
        bars.append(series("exp", exp, lambda c: rep.totals["E"].get(c), "this"))
        if prev:
            bars.append(series("exp-prev", f"{exp} {last}", lambda c: rep.totals["E"].get(c), "previous"))
        lines = [series("res", res, lambda c: rep.result.get(c), "this")]
        if prev:
            lines.append(series("res-prev", f"{res} {last}", lambda c: rep.result.get(c), "previous"))
        title = " ".join(x for x in (_("Opbrengsten en kosten"), per_label) if x)
        chart = PlChart(title, "", groups, bars, lines, None, "", options)
        chart.desc = _("Staven: opbrengsten en kosten; lijn: resultaat.") + (
            " " + _("Lichtere staven en stippellijn: vorig jaar.") if prev else "")
    chart.nf, chart.dp = fmt.nf, dp
    return layout(chart)


def layout(c: PlChart) -> PlChart:
    """Vaste maten in pixels (niet meeschalen): veel perioden maken de grafiek breder, het scherm scrolt dan
    binnen de grafiek (375 px), niet de pagina."""
    nb = max(1, len(c.bars))
    bw = 18 if nb <= 2 else 14
    slot = max(nb * bw + 22, 64 if len(c.groups) == 1 else 50)
    c.width = max(320, LEFT + len(c.groups) * slot + RIGHT)
    c.height = TOP + PLOT_H + BOTTOM
    values = [float(v) for s in c.bars + c.lines for v in s.values if v is not None]
    hi, lo = max(values + [0.0]), min(values + [0.0])
    step = nice_step(((hi - lo) or 1) / 4)
    top_v = step * max(1, math.ceil(hi / step)) if hi > 0 else 0
    low_v = -step * max(1, math.ceil(-lo / step)) if lo < 0 else 0
    span = (top_v - low_v) or 1

    def y_of(v: float) -> float:
        return round(TOP + (top_v - v) / span * PLOT_H, 1)

    c.zero = y_of(0)
    c.ticks = []
    v = low_v
    while v <= top_v + step / 2:
        c.ticks.append({"y": y_of(v), "label": short_amount(v, c.nf)})
        v += step
    c.rects, c.paths, c.labels = [], [], []
    for gi, g in enumerate(c.groups):
        x0 = LEFT + gi * slot + (slot - nb * bw) / 2
        for j, s in enumerate(c.bars):
            val = s.values[gi]
            if val is None:
                continue
            y1, y2 = sorted((y_of(float(val)), c.zero))
            c.rects.append(Bar(round(x0 + j * bw, 1), y1, bw - 2, round(max(y2 - y1, 0), 1), s.key, s.color,
                               s.titles[gi], val < 0))
        c.labels.append({"x": round(LEFT + gi * slot + slot / 2, 1), "label": g["label"]})
    for li, s in enumerate(c.lines):
        # twee lijnen (dit jaar en vorig jaar): punten iets uit elkaar, anders vallen ze bij "Geheel" samen
        dx = (li - (len(c.lines) - 1) / 2) * bw
        pts = [(round(c.labels[gi]["x"] + dx, 1), y_of(float(v)), s.titles[gi])
               for gi, v in enumerate(s.values) if v is not None]
        if pts:
            c.paths.append(Line(s.key, s.color, s.prev, pts))
    return c
