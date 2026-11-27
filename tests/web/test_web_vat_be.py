"""Periodieke btw-aangifte België in de webapp (routes_vat, BTW_LANDEN_ANALYSE.md BL2/BL3/BL10, BE1–BE6): land
kiezen, roosters per btw-rekening en per kostenrekening, overzicht, formulier (nl/fr), btw-boeking, teruggaaf,
klantscherm en de jaarlijkse klantenlisting."""

import re
from datetime import date
from decimal import Decimal as D

from psycopg import connect

from sqlledger import ArapLedger, UserConfig
from sqlledger.arap import ArapLine, ArapTransaction

from test_web_vat import KEY, cdb, data, form_fields, text, user  # noqa: F401  (fixtures)
from webhelpers import csrf, post


def setup_be(client):
    r = post(client, "/btw/rubrieken", {"switch": "1", "country": "BE"}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/btw/rubrieken").text
    assert "Belgische periodieke aangifte" in page and "Inkoop per kostenrekening (81, 82, 83)" in page
    ids = dict(re.findall(r'<td>(\d+)</td>\s*<td>[^<]*</td>\s*<td class="num">[^<]*</td>\s*<td><select name="box_(\d+)"',
                          page))
    fields = dict(re.findall(r'<select name="((?:box|exp)_\d+)"[^>]*>.*?<option value="(\w+)" selected', page, re.S))
    fields.update({f"box_{ids['1810']}": "03", f"box_{ids['1811']}": "01", f"box_{ids['1800']}": "59",
                   f"box_{ids['1801']}": "59"})
    r = post(client, "/btw/rubrieken", {**fields, "country": "BE", "settlement": "1850", "rounding": "8100"},
             follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/btw/rubrieken?ok=vat_boxes_saved"
    return page


def test_country_and_rosters(client, db, company, cdb):  # noqa: F811
    user(client, db, company, "admin")
    page = setup_be(client)
    assert "grondslag 21 %, btw in 54" in page and "verlegd: intracommunautaire verwerving goederen" in page
    boxes = {r["accno"]: r["box"] for r in cdb.execute(
        "SELECT c.accno, b.box FROM x_vat_box b JOIN chart c ON (c.id = b.chart_id) WHERE b.country = 'BE'")}
    assert boxes == {"1800": "59", "1801": "59", "1810": "03", "1811": "01"}
    exp = {r["accno"]: r["box"] for r in cdb.execute(
        "SELECT c.accno, b.box FROM x_vat_account_box b JOIN chart c ON (c.id = b.chart_id) WHERE b.country = 'BE'")}
    assert exp["8100"] == "82" and set(exp.values()) <= {"81", "82", "83"}
    # een rooster per kostenrekening wijzigen
    page = client.get("/btw/rubrieken").text
    fields = dict(re.findall(r'<select name="((?:box|exp)_\d+)"[^>]*>.*?<option value="(\w+)" selected', page, re.S))
    eid = cdb.execute("SELECT id FROM chart WHERE accno = '8100'").fetchone()["id"]
    fields[f"exp_{eid}"] = "81"
    post(client, "/btw/rubrieken", {**fields, "country": "BE", "settlement": "1850", "rounding": "8100"})
    assert cdb.execute("SELECT box FROM x_vat_account_box WHERE chart_id = %s", (eid,)).fetchone()["box"] == "81"


def test_overview_form_booking(client, db, company, data, cdb):  # noqa: F811
    user(client, db, company, "admin")
    setup_be(client)
    page = client.get(f"/btw/aangifte?tijdvak={KEY}").text
    t = text(page)
    assert "Periodieke btw-aangifte (België): 2031 1e kwartaal" in t
    assert re.search(r"03 Handelingen waarvoor de btw verschuldigd is door de aangever – tarief 21 % 100", t)
    assert re.search(r"47 Andere vrijgestelde handelingen .* 200", t)              # omzet zonder btw: standaard 47
    assert re.search(r"82 Diensten en diverse goederen 40", t)
    assert re.search(r"54 Btw op de handelingen aangegeven in de roosters 01, 02 en 03 21", t)
    assert re.search(r"59 Aftrekbare btw 9", t) and re.search(r"71 Verschuldigd aan de Staat 12", t)
    assert "Klantenlisting" in t and "Rooster 72" not in t
    form = client.get(f"/btw/aangifte/formulier?tijdvak={KEY}").text
    assert 'class="vatform be"' in form and "Periodieke btw-aangifte" in form and "Intervat" in text(form)
    p = client.get(f"/btw/aangifte/formulier?tijdvak={KEY}&export=pdf")
    assert p.status_code == 200 and p.content[:4] == b"%PDF"
    x = client.get(f"/btw/aangifte?tijdvak={KEY}&export=xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    assert "100,60" in text(client.get(f"/btw/aangifte?tijdvak={KEY}&rubriek=03").text)
    # btw-boeking: saldo 71 op de afdrachtrekening, centen naar afrondingsverschillen
    page = client.get(f"/memoriaal/nieuw?btw={KEY}").text
    f = form_fields(page)
    assert f["description"] == "Btw-aangifte België 2031 1e kwartaal"
    rows = {(f.get(f"accno_{i}"), f.get(f"debit_{i}"), f.get(f"credit_{i}")) for i in range(1, 8) if f.get(f"accno_{i}")}
    assert rows == {("1810", "21,13", ""), ("1800", "", "8,48"), ("1850", "", "12,00"), ("8100", "", "0,65")}
    f.pop("csrf")
    r = client.post("/memoriaal", data={"csrf": csrf(client, page), **f, "action": "post"}, follow_redirects=False)
    assert r.status_code == 303
    ret = cdb.execute("SELECT * FROM x_vat_return").fetchone()
    assert ret["country"] == "BE" and ret["snapshot"]["totals"]["71"] == "12"


def test_form_in_french_and_refund(client, db, company, settings, company_db):  # noqa: F811
    conn = connect(**settings.company_conninfo(company_db))
    ArapLedger(conn, UserConfig(login="x")).post_transaction(ArapTransaction(
        vc="vendor", vc_id=905, transdate=date(2031, 2, 10), arap_accno="1600",
        lines=[ArapLine(accno="8100", amount=D("1000"))], taxes={"1800": D("210")}))
    conn.close()
    user(client, db, company, "admin", "beheer")
    setup_be(client)
    t = text(client.get(f"/btw/aangifte?tijdvak={KEY}").text)
    assert re.search(r"72 Sommen verschuldigd door de Staat 210", t)
    # BE6: 210 is minder dan het minimum voor een teruggaaf in het eerste kwartaal (400)
    assert "Rooster 72: 210 is minder dan het minimum voor een teruggaaf (400)" in t and "€ 400 per tijdvak" in t
    client.cookies.clear()
    user(client, db, company, "readonly", "lecteur", lang="fr")
    form = client.get(f"/btw/aangifte/formulier?tijdvak={KEY}").text
    ft = text(form)
    assert "Déclaration périodique à la TVA" in ft and "TVA déductible" in ft and 'lang="fr"' in form
    t = text(client.get(f"/btw/aangifte?tijdvak={KEY}").text)
    assert "Déclaration périodique à la TVA (Belgique)" in t


def test_customer_screen_be_choices(client, db, company, data, cdb):  # noqa: F811
    user(client, db, company, "admin")
    setup_be(client)
    page = client.get("/klanten/903").text
    assert "47 vrijgesteld, uitvoer of buiten België (standaard)" in page and "intracommunautaire leveringen en ABC" in page
    f = form_fields(page)
    f.pop("csrf")
    f["vat_box"] = "46"
    f["taxnumber"] = "DE123456789"
    r = client.post("/klanten", data={"csrf": csrf(client, page), **f, "action": "save"}, follow_redirects=False)
    assert r.status_code == 303, r.text[:500]
    assert cdb.execute("SELECT box, country FROM x_vat_customer_box WHERE customer_id = 903").fetchone() == {
        "box": "46", "country": "BE"}
    assert re.search(r"46 Vrijgestelde intracommunautaire leveringen .* 200",
                     text(client.get(f"/btw/aangifte?tijdvak={KEY}").text))


def test_client_listing(client, db, company, data, cdb):  # noqa: F811
    cdb.execute("UPDATE customer SET taxnumber = 'BE 0417.497.106' WHERE id = 902")      # geldig (mod 97)
    cdb.execute("UPDATE customer SET taxnumber = '0123456789' WHERE id = 903")           # controlegetal fout
    user(client, db, company, "admin")
    setup_be(client)
    page = client.get("/btw/klantenlisting?jaar=2031").text
    t = text(page)
    assert "Jaarlijkse klantenlisting 2031" in t and "BE0417497106" not in t      # 100,60: niet boven € 250
    assert "Geen klanten boven de drempel" in t and "controlegetal klopt niet" in t
    assert "1 Belgische klanten met een omzet van niet meer dan 250 euro" in t
    cdb.execute("UPDATE ar SET netamount = 300.60, amount = 321.73 WHERE customer_id = 902")
    t = text(client.get("/btw/klantenlisting?jaar=2031").text)
    assert re.search(r"BE0417497106 .* 300 21", t) and "Geen klanten boven de drempel" not in t
    assert client.get("/btw/klantenlisting?jaar=2031&export=pdf").content[:4] == b"%PDF"
    assert client.get("/btw/klantenlisting?jaar=2031&export=xlsx").content[:2] == b"PK"
    assert client.get("/btw/klantenlisting?jaar=x").status_code == 400
