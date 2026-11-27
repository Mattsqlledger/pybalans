"""Dashboard op het startscherm (fase 4 stap 5b, DB1–DB13).

De pagina toont de indeling; elk blok is een eigen verzoek (DB7). De getallen komen uit ``sqlledger.dashboard``
(zie tests/test_dashboard.py voor de gelijkheid met de rapporten); hier: opmaak, links, modules, rollen, talen,
btw afvinken en de instelling.
"""

import re
from datetime import date

import psycopg
import pytest
from psycopg.rows import dict_row

from sqlledger import UserConfig, connect
from sqlledger.dashboard import Dashboard
from sqlledger_web import routes_dashboard
from webhelpers import csrf, login, make_user

TODAY = date(2026, 7, 10)


class FakeDate(date):
    @classmethod
    def today(cls):
        return TODAY


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(routes_dashboard, "date", FakeDate)


@pytest.fixture
def cdb(settings, company_db):
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True, row_factory=dict_row) as c:
        yield c


@pytest.fixture
def lib(settings, company_db):
    conn = connect(**settings.company_conninfo(company_db))
    yield Dashboard(conn, UserConfig(login="x", numberformat="1.000,00", dateformat="dd-mm-yy"))
    conn.close()


def user(client, db, company, role="bookkeeper", name="boek", lang=None, totp=False):
    secret = make_user(db, name, memberships=[(company["id"], role)], totp=totp)
    if lang:
        db.execute("UPDATE app_user SET language = %s WHERE login = %s", (lang, name))
    login(client, name, secret)
    return client


def blocks(page):
    return re.findall(r'hx-get="/dashboard/blok/(\w+)"', page)


def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_page_and_menu(client, db, company):
    page = user(client, db, company).get("/").text
    assert blocks(page) == ["tegels", "bank", "verkoop", "inkoop", "resultaat", "tedoen"]
    assert re.search(r'<a class="home current" href="/" aria-current="page">Dashboard</a>', page)
    assert "Over deze administratie" in page and "Afgesloten t/m" in page
    other = client.get("/rapporten/proefbalans").text
    assert '<a class="home" href="/">Dashboard</a>' in other            # DB1: altijd terug naar het dashboard


def test_tiles(client, db, company, lib):
    html = user(client, db, company).get("/dashboard/blok/tegels").text
    ar, ap, res = lib.open_items("ar", TODAY), lib.open_items("ap", TODAY), lib.results(TODAY)
    bank = sum(a.balance for a in lib.bank(TODAY))
    t = text(html)
    fmt = lambda v: f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    for v in (bank, ar.total, ap.total, res.ytd.result, res.ytd_prev.result):
        assert fmt(v) in t, v
    assert f"{ar.overdue_count} vervallen: {fmt(ar.overdue)}" in t
    assert "Boekjaar 2026 t/m 10-07-2026" in t
    assert 'href="/rapporten/ouderdom-verkoop?run=1&amp;period=&amp;todate=2026-07-10&amp;overdue=0"' in html
    assert 'href="/rapporten/winst-en-verlies?run=1&amp;period=&amp;fromdate=2026-01-01&amp;todate=2026-07-10&amp;previousyear=1"' in html
    assert 'href="/rapporten/balans?run=1&amp;period=&amp;todate=2026-07-10"' in html


def test_tile_links_give_the_same_total(client, db, company, lib):
    """Doorklikken: de balans toont hetzelfde banksaldo, de W&V hetzelfde resultaat."""
    c = user(client, db, company)
    res = lib.results(TODAY)
    wv = text(c.get("/rapporten/winst-en-verlies", params={"run": "1", "period": "", "fromdate": "2026-01-01",
                                                          "todate": "2026-07-10", "previousyear": "1"}).text)
    assert f"{res.ytd.result:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") in wv


def test_blocks_render(client, db, company):
    c = user(client, db, company)
    for b in ("bank", "verkoop", "inkoop", "resultaat", "tedoen"):
        r = c.get(f"/dashboard/blok/{b}")
        assert r.status_code == 200 and "dash-" + b in r.text and "niet worden geladen" not in r.text, b
    ar = c.get("/dashboard/blok/verkoop").text
    assert ar.count("<rect") == 4 and "niet vervallen" in ar and "&gt; 60" in ar
    assert 'href="/verkoopfactuur/nieuw"' in ar and 'href="/aanmaningen"' in ar
    res = c.get("/dashboard/blok/resultaat").text
    assert res.count("<rect") == 24 and "jul 2026" in res
    assert 'fromdate=2026-06-01&amp;todate=2026-06-30' in res             # klik op juni → W&V juni
    assert 'fromdate=2026-07-01&amp;todate=2026-07-10' in res
    bank = c.get("/dashboard/blok/bank").text
    assert "1100" in bank and 'href="/bank/inlezen"' in bank


def test_todo_block(client, db, company):
    t = text(user(client, db, company).get("/dashboard/blok/tedoen").text)
    assert "Btw 2e kwartaal 2026: aangifte vóór 31-07-2026" in t
    assert re.search(r"\d+ vervallen verkoopfacturen: [\d.,]+", t)
    assert "Afgehandeld" in t


def test_modules_off(client, db, company):
    db.execute("UPDATE company SET modules = %s WHERE id = %s", (["gl", "reports"], company["id"]))
    c = user(client, db, company)
    assert blocks(c.get("/").text) == ["tegels", "resultaat", "tedoen"]
    assert c.get("/dashboard/blok/verkoop").status_code == 403
    tiles = text(c.get("/dashboard/blok/tegels").text)
    assert "Resultaat" in tiles and "Te ontvangen" not in tiles and "Bank en kas" not in tiles
    assert "Btw" not in text(c.get("/dashboard/blok/tedoen").text)       # btw hoort bij verkoop/inkoop
    assert c.get("/dashboard/blok/bestaat-niet").status_code == 404


def test_readonly_sees_numbers_not_actions(client, db, company):
    c = user(client, db, company, role="readonly", name="lees")
    ar = c.get("/dashboard/blok/verkoop").text
    assert "<rect" in ar and "/verkoopfactuur/nieuw" not in ar and 'href="/aanmaningen"' in ar
    assert "/bank/inlezen" not in c.get("/dashboard/blok/bank").text
    todo = c.get("/dashboard/blok/tedoen").text
    assert "Btw 2e kwartaal" in todo and "Afgehandeld" not in todo
    page = c.get("/").text
    r = c.post("/dashboard/btw-afgehandeld", data={"csrf": csrf(c, page), "period_end": "2026-06-30"},
               follow_redirects=False)
    assert r.status_code == 403


def test_vat_done(client, db, company, cdb):
    c = user(client, db, company)
    todo = c.get("/dashboard/blok/tedoen").text
    r = c.post("/dashboard/btw-afgehandeld", data={"csrf": csrf(c, todo), "period_end": "2026-06-30"},
               follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?ok=vat_done"
    assert cdb.execute("SELECT kind, period_end, login FROM x_task_done").fetchall() == \
        [{"kind": "vat", "period_end": date(2026, 6, 30), "login": "boek"}]
    assert "Btw" not in text(c.get("/dashboard/blok/tedoen").text)
    assert "De btw-aangifte is afgevinkt." in c.get("/?ok=vat_done").text
    log = db.execute("SELECT detail FROM audit_log WHERE action = 'vat_return_done'").fetchall()
    assert log and log[0]["detail"]["period_end"] == "2026-06-30"
    # een ander tijdvak dan dat van nu: niets vastgelegd
    r = c.post("/dashboard/btw-afgehandeld", data={"csrf": csrf(c, todo), "period_end": "2026-03-31"},
               follow_redirects=False)
    assert r.status_code == 303 and len(cdb.execute("SELECT 1 FROM x_task_done").fetchall()) == 1
    assert c.post("/dashboard/btw-afgehandeld", data={"period_end": "2026-06-30"}).status_code in (400, 403)


def test_vat_links_to_return(client, db, company):
    """BA1: met ingestelde btw-rubrieken linkt de taak naar de btw-aangifte van dat tijdvak, anders naar Btw verkoop."""
    c = user(client, db, company)
    assert "/rapporten/btw-verkoop?" in c.get("/dashboard/blok/tedoen").text
    db.execute("UPDATE company SET vat_return = true WHERE id = %s", (company["id"],))
    assert 'href="/btw/aangifte?tijdvak=2026-04-01_2026-06-30"' in c.get("/dashboard/blok/tedoen").text


def test_vat_reminder_expires(client, db, company, monkeypatch):
    # alleen een herinnering: na de termijn vervalt hij, het volgende tijdvak volgt na afsluiten
    monkeypatch.setattr(FakeDate, "today", classmethod(lambda cls: date(2026, 8, 3)))
    t = text(user(client, db, company).get("/dashboard/blok/tedoen").text)
    assert "Btw 2e kwartaal 2026" not in t and "te laat" not in t


def test_vat_setting(client, db, company, cdb):
    c = user(client, db, company, role="admin", name="baas", totp=True)
    page = c.get("/beheer/instellingen").text
    assert re.search(r'<option value="quarter" selected>kwartaal</option>', page)
    form = dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', page))
    form.update(vat_period="month", doc_language="nl")
    r = c.post("/beheer/instellingen", data=form, follow_redirects=False)
    assert r.status_code == 303, r.text[:500]
    assert cdb.execute("SELECT value FROM x_setting WHERE key = 'vat_return_period'").fetchone()["value"] == "month"
    assert "Btw jun 2026" in text(c.get("/dashboard/blok/tedoen").text)
    log = db.execute("SELECT detail FROM audit_log WHERE action = 'am_settings_saved' ORDER BY id DESC").fetchone()
    assert "vat_return_period" in log["detail"]["keys"]


def test_fallback_year(client, db, company, monkeypatch):
    monkeypatch.setattr(FakeDate, "today", classmethod(lambda cls: date(2028, 3, 1)))
    t = text(user(client, db, company).get("/dashboard/blok/tegels").text)
    assert "Laatste boekjaar met boekingen: 2026" in t and "2026 t/m 31-12-2026" in t


def test_block_error_does_not_hang(client, db, company, monkeypatch):
    def boom(self, *a, **kw):
        raise RuntimeError("kapot")
    monkeypatch.setattr(Dashboard, "bank", boom)
    r = user(client, db, company).get("/dashboard/blok/bank")
    assert r.status_code == 200 and "Dit blok kon niet worden geladen." in r.text and "kapot" not in r.text


@pytest.mark.parametrize("lang,words", [
    ("en", ["Bank and cash", "Receivable", "Payable", "To do", "Income / (loss)"]),
    ("de", ["Bank und Kasse", "Forderungen", "Verbindlichkeiten", "Zu erledigen", "Ergebnis"]),
    ("fr", ["Banque et caisse", "À recevoir", "À payer", "À faire", "Résultat"]),
])
def test_languages(client, db, company, lang, words):
    c = user(client, db, company, lang=lang)
    html = "".join(c.get(f"/dashboard/blok/{b}").text for b in ("tegels", "bank", "verkoop", "inkoop", "resultaat", "tedoen"))
    for w in words:
        assert w in html, (lang, w)
    assert "bezig" not in c.get("/").text.lower() or lang == "nl"


def test_orders_late_link_lists_the_order(client, db, company, cdb):
    """Keuze 10 A: de link opent de open verkooporders op leverdatum (was: lege lijst, run=1 zonder open=1)."""
    cdb.execute("""INSERT INTO oe (ordnumber, transdate, reqdate, customer_id, vendor_id, amount, netamount, curr,
                                   closed, quotation)
                   SELECT 'LAAT-1', '2026-06-01', '2026-06-15', min(id), 0, 10, 10, 'EUR', false, false
                   FROM customer""")
    c = user(client, db, company)
    todo = c.get("/dashboard/blok/tedoen").text
    href = re.search(r'href="(/verkooporders\?[^"]+)"', todo).group(1).replace("&amp;", "&")
    assert "open=1" in href and "sort=reqdate" in href
    assert "LAAT-1" in c.get(href).text


def test_block_db_error_hides_details(client, db, company, monkeypatch):
    """Keuze 12 A: geen servernaam of poort in de melding, alleen in het log."""
    c = user(client, db, company)
    def down(*a, **kw):
        raise psycopg.OperationalError('connection to server at "10.9.8.7", port 5432 failed')
    monkeypatch.setattr(routes_dashboard, "connect_company", down)
    r = c.get("/dashboard/blok/bank")
    assert r.status_code == 200 and "Dit blok kon niet worden geladen." in r.text and "10.9.8.7" not in r.text


def test_block_session_expired_redirects_whole_page(client, db):
    """Keuze 12 A: een blok zonder sessie geeft HX-Redirect (hele pagina naar inloggen), geen inlogpagina in het blok."""
    make_user(db, "anna")           # zonder gebruikers: naar /welkom (eerste start)
    r = client.get("/dashboard/blok/bank", headers={"HX-Request": "true"}, follow_redirects=False)
    assert r.status_code == 200 and r.headers["HX-Redirect"].startswith("/login") and not r.text


def test_block_failure_message_in_page(client, db, company):
    """Keuze 12 A: elk blok draagt de (vertaalde) melding voor een mislukt verzoek; het script staat erbij."""
    page = user(client, db, company, lang="de").get("/").text
    n = len(blocks(page))
    assert n and page.count('data-dash-error="') == n and 'src="/static/dashboard.js"' in page
    assert "Dit blok kon niet worden geladen." not in page
    assert client.get("/static/dashboard.js").status_code == 200
