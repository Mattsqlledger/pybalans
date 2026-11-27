"""Grafiek bij Winst en verlies (v1.1, GR1–GR5).

De bedragen in de grafiek komen uit hetzelfde bibliotheekrapport als de tabel (die tegen Perl bewezen is); deze
tests toetsen de SVG (staven, lijnen, tooltips met het bedrag in de notatie van de gebruiker), de keuze
Tabel / Grafiek / Beide (per gebruiker onthouden), de keuze van een rekening of groep, de PDF en de balans (geen
grafiek).
"""

import html
import re
from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from pypdf import PdfReader

import sqlledger
from sqlledger import Reports, UserConfig
from sqlledger.amounts import format_amount
from sqlledger_web import plchart
from sqlledger_web.reportdefs import Fmt
from webhelpers import login, make_user

URL = "/rapporten/winst-en-verlies"
Y2026 = {"run": "1", "period": "year", "year": "2026"}
RECT_RE = re.compile(r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)" fill="[^"]+" '
                     r'class="pl-([\w-]+)" data-tip="([^"]*)"')


@pytest.fixture
def reader(client, db, company):
    make_user(db, "lees", memberships=[(company["id"], "readonly")])
    login(client, "lees")
    assert client.get("/").status_code == 200
    return client


@pytest.fixture
def lib(settings, company_db):
    conn = sqlledger.connect(**settings.company_conninfo(company_db))
    yield Reports(conn, UserConfig(login="lees", numberformat="1.000,00", dateformat="dd-mm-yy", countrycode="nl"))
    conn.close()


def rects(page):
    return [(float(x), float(y), float(w), float(h), key, html.unescape(tip))
            for x, y, w, h, key, tip in RECT_RE.findall(page)]


def amount(v):
    return format_amount(v, "1.000,00", 2, "0").replace("&nbsp;", "").strip()


def svg(page):
    m = re.search(r'<svg xmlns="http://www.w3.org/2000/svg" class="pl-svg".*?</svg>', page, re.S)
    return m.group(0) if m else ""


def labels(page):
    return re.findall(r'<text x="[\d.]+" y="[\d.]+" text-anchor="middle" fill="#6b7684">([^<]*)</text>', svg(page))


# --- GR1: opbrengsten en kosten per periode, lijn resultaat ---------------------------------------

def test_months_bars_and_result_line(reader, lib):
    page = reader.get(URL, params={**Y2026, "includeperiod": "month", "weergave": "grafiek"}).text
    s = svg(page)
    assert 'role="img"' in s and '<title id="pl-title">Opbrengsten en kosten per maand</title>' in s
    assert "<desc" in s
    assert labels(page) == ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]
    rep = lib.income_statement(fromdate=date(2026, 1, 1), todate=date(2026, 12, 31), includeperiod="month")
    bars = rects(page)
    assert [k for *_, k, __ in bars] == ["inc", "exp"] * 12
    for m in range(1, 13):
        inc, exp = bars[2 * (m - 1)], bars[2 * (m - 1) + 1]
        assert inc[5].endswith(": " + amount(rep.totals["I"][("this", m)])), inc
        assert exp[5].endswith(": " + amount(rep.totals["E"][("this", m)])), exp
        assert inc[0] < exp[0]                                     # naast elkaar, opbrengsten links
    assert "Opbrengsten 01-01-2026 t/m 31-01-2026: " in bars[0][5]
    # resultaatlijn: één punt per maand, tooltip met het resultaat
    tips = re.findall(r'<circle [^>]*class="pl-res" data-tip="([^"]*)"', s)
    assert len(tips) == 12 and "<polyline" in s
    assert tips[1] == "Resultaat 01-02-2026 t/m 28-02-2026: " + amount(rep.result[("this", 2)])
    # alleen grafiek: geen tabel, wel de cijfers bij de grafiek (tabelalternatief)
    assert '<tr class="k-' not in page and "Cijfers bij de grafiek" in page


def test_quarters_previous_year(reader, lib):
    page = reader.get(URL, params={**Y2026, "includeperiod": "quarter", "previousyear": "1",
                                   "weergave": "grafiek"}).text
    assert labels(page) == ["Q1", "Q2", "Q3", "Q4"]
    bars = rects(page)
    assert [k for *_, k, __ in bars] == ["inc", "inc-prev", "exp", "exp-prev"] * 4      # GR4
    rep = lib.income_statement(fromdate=date(2026, 1, 1), todate=date(2026, 12, 31), includeperiod="quarter",
                               previousyear=True)
    assert bars[1][5] == "Opbrengsten vorig jaar 01-01-2025 t/m 31-03-2025: " + amount(rep.totals["I"][("previous", 1)])
    s = svg(page)
    assert s.count("<polyline") == 2 and s.count('stroke-dasharray="5 4"') == 1            # stippellijn vorig jaar
    assert "Lichtere staven en stippellijn: vorig jaar." in s
    assert "Opbrengsten vorig jaar" in page and "Resultaat vorig jaar" in page             # legenda in tekst


def test_whole_period_one_group(reader, lib):
    page = reader.get(URL, params={**Y2026, "weergave": "grafiek"}).text
    assert labels(page) == ["2026"]
    rep = lib.income_statement(fromdate=date(2026, 1, 1), todate=date(2026, 12, 31))
    bars = rects(page)
    assert [b[4] for b in bars] == ["inc", "exp"]
    assert bars[0][5].endswith(amount(rep.totals["I"][("this", 0)]))
    assert re.search(r'<circle [^>]*r="5"[^>]*class="pl-res"', svg(page))               # één punt, geen lijn
    assert "<polyline" not in svg(page)


def test_number_format_of_user(reader, db):
    db.execute("UPDATE app_user SET numberformat = '1,000.00' WHERE login = 'lees'")
    page = reader.get(URL, params={**Y2026, "includeperiod": "month", "weergave": "grafiek"}).text
    assert "Opbrengsten 01-02-2026 t/m 28-02-2026: 5,160.50" in page
    assert ">2.5K</text>" in svg(page)


def test_filters_follow_report(reader, lib):
    """Afdeling (en project) van het rapport gelden ook voor de grafiek."""
    params = {**Y2026, "includeperiod": "quarter", "weergave": "grafiek", "department_id": "600"}
    page = reader.get(URL, params=params).text
    rep = lib.income_statement(fromdate=date(2026, 1, 1), todate=date(2026, 12, 31), includeperiod="quarter",
                               department_id=600)
    tips = [b[5] for b in rects(page)]
    assert tips == [f"{lbl} {per}: {amount(rep.totals[c][('this', q)])}"
                    for q, per in enumerate(["01-01-2026 t/m 31-03-2026", "01-04-2026 t/m 30-06-2026",
                                             "01-07-2026 t/m 30-09-2026", "01-10-2026 t/m 31-12-2026"], start=1)
                    for lbl, c in (("Opbrengsten", "I"), ("Kosten", "E"))]


# --- GR1: rekening of groep kiezen ---------------------------------------------------------------

def test_pick_account_from_table(reader, lib):
    params = {**Y2026, "includeperiod": "month", "weergave": "beide", "l_heading": "1", "l_subtotal": "1"}
    page = reader.get(URL, params=params).text
    # grafiekteken bij rekeningen, koppen en rubrieken (niet bij totalen); de tabel blijft gelijk
    picks = re.findall(r'<a class="pl-row noprint" href="([^"]*)"', page)
    assert any("grafiek=8520" in html.unescape(h) for h in picks)
    assert any("grafiek=8000" in html.unescape(h) for h in picks)          # kop (groep)
    assert any(html.unescape(h).endswith("grafiek=I#grafiek") for h in picks)
    assert 'aria-label="Toon in grafiek: 8520 Opbrengst Verkopen A"' in page
    href = html.unescape(next(h for h in picks if "grafiek=8520" in html.unescape(h)))
    page = reader.get(href.split("#")[0]).text
    rep = lib.income_statement(fromdate=date(2026, 1, 1), todate=date(2026, 12, 31), includeperiod="month")
    bars = rects(page)
    assert [b[4] for b in bars] == ["sel"] * 12
    want = rep.accounts["I"]["8520"]["amounts"]
    assert [b[5].rsplit(": ", 1)[1] for b in bars] == [amount(want[("this", m)]) for m in range(1, 13)]
    assert "<title id=\"pl-title\">8520 Opbrengst Verkopen A per maand</title>" in page
    assert "terug naar opbrengsten en kosten" in page
    back = html.unescape(re.search(r'<a class="pl-back noprint" href="([^"]*)"', page).group(1))
    assert "grafiek=" not in back and "weergave=beide" in back
    assert '<option value="8520" selected>' in page
    assert 'aria-current="true"><svg' in page                       # gekozen rij gemarkeerd


def test_pick_group_and_section(reader, lib):
    rep = lib.income_statement(fromdate=date(2026, 1, 1), todate=date(2026, 12, 31), includeperiod="quarter",
                               previousyear=True)
    page = reader.get(URL, params={**Y2026, "includeperiod": "quarter", "previousyear": "1",
                                   "weergave": "grafiek", "grafiek": "4000"}).text
    bars = rects(page)
    assert [b[4] for b in bars] == ["sel", "sel-prev"] * 4
    head = rep.accounts["E"]["4000"]["amounts"]
    assert bars[2][5].endswith(amount(head[("this", 2)])) and bars[3][5].endswith(amount(head[("previous", 2)]))
    page = reader.get(URL, params={**Y2026, "weergave": "grafiek", "grafiek": "E"}).text
    assert [b[4] for b in rects(page)] == ["sel"]
    assert "<polyline" not in page
    # onbekende keuze: gewoon opbrengsten en kosten
    page = reader.get(URL, params={**Y2026, "weergave": "grafiek", "grafiek": "9999x"}).text
    assert [b[4] for b in rects(page)] == ["inc", "exp"]


def test_pick_not_kept_by_form(reader):
    """Het zoekformulier neemt de gekozen rekening niet mee (Toon = opbrengsten en kosten); de keuzelijst
    bij de grafiek neemt alle zoekvelden mee."""
    page = reader.get(URL, params={**Y2026, "weergave": "beide", "grafiek": "8520", "department_id": "600"}).text
    form = page[page.index('class="report-form'):page.index("</form>")]
    assert 'name="grafiek"' not in form
    pick = page[page.index('class="pl-pick'):]
    pick = pick[:pick.index("</form>")]
    assert '<input type="hidden" name="department_id" value="600">' in pick
    assert '<input type="hidden" name="grafiek"' not in pick


# --- GR2: Tabel / Grafiek / Beide, per gebruiker onthouden ------------------------------------------

def test_view_choice_remembered(reader, client, db, company):
    page = reader.get(URL, params=Y2026).text
    assert 'class="pl-views' in page and not svg(page)                     # standaard: tabel
    assert re.search(r'weergave=tabel"[^>]*aria-current', page)
    assert '<a class="pl-row' not in page
    page = reader.get(URL, params={**Y2026, "weergave": "beide"}).text
    assert svg(page) and '<tr class="k-row">' in page
    page = reader.get(URL, params=Y2026).text                              # onthouden
    assert svg(page) and '<tr class="k-row">' in page
    assert re.search(r'weergave=beide"[^>]*aria-current', page)
    page = reader.get(URL).text                                            # formulier: geen resultaat
    assert not svg(page)
    reader.get(URL, params={**Y2026, "weergave": "grafiek"})
    page = reader.get(URL, params=Y2026).text
    assert svg(page) and '<tr class="k-' not in page
    reader.get(URL, params={**Y2026, "weergave": "onzin"})                 # ongeldig: niets veranderd
    assert '<tr class="k-' not in reader.get(URL, params=Y2026).text
    # een andere gebruiker heeft zijn eigen keuze
    client.cookies.clear()
    make_user(db, "ander", memberships=[(company["id"], "readonly")])
    login(client, "ander")
    page = client.get(URL, params=Y2026).text
    assert not svg(page) and '<tr class="k-row">' in page
    rows = db.execute("SELECT u.login, p.params FROM report_pref p JOIN app_user u ON u.id = p.user_id "
                      "WHERE p.report = 'winst-en-verlies:weergave'").fetchall()
    assert [(r["login"], r["params"]) for r in rows] == [("lees", {"weergave": "grafiek"})]


def test_no_chart_on_balance_sheet(reader):
    """GR5: geen grafiek bij de balans."""
    page = reader.get("/rapporten/balans", params={"run": "1", "period": "", "todate": "2026-12-31",
                                                    "includeperiod": "quarter", "weergave": "grafiek"}).text
    assert "pl-views" not in page and not svg(page) and "pl-row" not in page
    assert '<tr class="k-row">' in page


# --- GR3: PDF en afdrukken ----------------------------------------------------------------------

def pdf_text(content):
    return "\n".join(p.extract_text() for p in PdfReader(BytesIO(content)).pages)


def test_pdf_contains_chart(reader):
    params = {**Y2026, "includeperiod": "month", "export": "pdf"}
    plain = pdf_text(reader.get(URL, params={**params, "weergave": "tabel"}).content)
    assert "Opbrengsten en kosten" not in plain and "Totaal opbrengsten" in plain
    both = reader.get(URL, params={**params, "weergave": "beide"})
    assert both.headers["content-type"] == "application/pdf"
    text = pdf_text(both.content)
    assert "Opbrengsten en kosten per maand" in text and "Totaal opbrengsten" in text
    assert "jan" in text and "dec" in text and "2,5K" in text                  # aslabels uit de SVG
    assert "Resultaat" in text and "Kosten" in text                            # legenda
    only = pdf_text(reader.get(URL, params={**params, "weergave": "grafiek"}).content)
    assert "Opbrengsten en kosten per maand" in only and "Totaal opbrengsten" not in only
    # gekozen rekening ook in de PDF
    acc = pdf_text(reader.get(URL, params={**params, "weergave": "grafiek", "grafiek": "8520"}).content)
    assert "8520 Opbrengst Verkopen A per maand" in acc


def test_svg_without_css_has_colours():
    """PDF en afdrukken: kleuren als attribuut; het scherm (CSS) overschrijft ze."""
    from sqlledger_web.web import TEMPLATES
    c = _chart([Decimal("100"), Decimal("-40")], [Decimal("30"), Decimal("-5")])
    out = TEMPLATES.env.get_template("reports/_plchart.html").module.svg(c)
    assert f'fill="{plchart.COLORS["inc"]}"' in out and f'fill="{plchart.COLORS["exp"]}"' in out
    assert 'stroke="#d9dee5"' in out


# --- negatieve bedragen, lege perioden, breedte -----------------------------------------------------

def _chart(inc, exp, res=None):
    n = len(inc)
    groups = [{"label": f"p{i}", "title": f"periode {i}"} for i in range(n)]
    bars = [plchart.Series("inc", "Opbrengsten", inc, ["t"] * n), plchart.Series("exp", "Kosten", exp, ["t"] * n)]
    lines = [plchart.Series("res", "Resultaat", res or [a - b for a, b in zip(inc, exp)], ["t"] * n)]
    return plchart.layout(plchart.PlChart("titel", "", groups, bars, lines))


def test_negative_amounts_below_zero_line():
    c = _chart([Decimal("100"), Decimal("-40")], [Decimal("30"), Decimal("-5")])
    neg = [b for b in c.rects if b.negative]
    assert len(neg) == 2 and all(b.y == c.zero and b.h > 0 for b in neg)
    assert all(b.y + b.h == c.zero for b in c.rects if not b.negative)
    assert any(t["label"].startswith("-") for t in c.ticks)
    # resultaat -35 in periode 2: punt onder de nullijn
    assert c.paths[0].points[1][1] > c.zero


def test_empty_periods_and_empty_chart():
    c = _chart([Decimal(0), Decimal("10"), Decimal(0)], [Decimal(0), Decimal(0), Decimal(0)])
    assert len(c.labels) == 3 and not c.empty
    assert [b.h for b in c.rects if b.key == "inc"][0] == 0
    assert _chart([Decimal(0)], [Decimal(0)]).empty


def test_many_periods_fixed_width():
    """Veel perioden: vaste breedte per periode, de grafiek scrolt binnen zijn kader (375 px)."""
    c = _chart([Decimal(i) for i in range(12)], [Decimal(i) for i in range(12)])
    assert c.width >= 12 * 50 and c.width == c.labels[-1]["x"] + (c.labels[1]["x"] - c.labels[0]["x"]) / 2 + 12


def test_empty_period_message(reader):
    page = reader.get(URL, params={"run": "1", "period": "year", "year": "2015", "weergave": "grafiek"}).text
    assert "Geen bedragen om te tonen in deze periode." in page and not svg(page)


def test_english(client, db, company):
    make_user(db, "lees", memberships=[(company["id"], "readonly")])
    db.execute("UPDATE app_user SET language = 'en' WHERE login = 'lees'")
    login(client, "lees")
    page = client.get(URL, params={**Y2026, "includeperiod": "quarter", "previousyear": "1",
                                   "weergave": "beide"}).text
    assert "Income and expenses per quarter" in page and "Income previous year" in page
    assert ">Both</a>" in page and "Show in chart" in page


def test_result_points_apart_with_previous_year():
    """Geheel met vorig jaar: de punten van beide resultaten vallen niet samen."""
    groups = [{"label": "2026", "title": "2026"}]
    lines = [plchart.Series("res", "Resultaat", [Decimal("10")], ["t"]),
             plchart.Series("res-prev", "Resultaat vorig jaar", [Decimal("10")], ["t"])]
    bars = [plchart.Series(k, k, [Decimal("20")], ["t"]) for k in ("inc", "inc-prev", "exp", "exp-prev")]
    c = plchart.layout(plchart.PlChart("titel", "", groups, bars, lines))
    (x1, y1, __), (x2, y2, __) = c.paths[0].points[0], c.paths[1].points[0]
    assert y1 == y2 and x1 < x2 and c.paths[1].dashed and not c.paths[0].dashed
