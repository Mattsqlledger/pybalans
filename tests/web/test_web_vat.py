"""Btw-aangifte in de webapp (routes_vat, BTW_ANALYSE.md BA1–BA9): rubrieken, overzicht, doorklikken, Excel/PDF,
aangifteformulier, btw-boeking via het memoriaal, rubriek bij de klant, rechten en menu."""

import html as htmlmod
import re
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import dict_row

from sqlledger import ArapLedger, UserConfig, connect
from sqlledger.arap import ArapLine, ArapTransaction
from webhelpers import csrf, login, make_user, nohelp, post

D = Decimal
KEY = "2031-01-01_2031-03-31"


@pytest.fixture
def cdb(settings, company_db):
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True, row_factory=dict_row) as c:
        # sl_template: per rekening twee tarieven zonder einddatum; de oude weg (zoals tests/test_vatreturn.py)
        c.execute("UPDATE tax SET rate = 0.09, taxnumber = 'X' WHERE chart_id = (SELECT id FROM chart WHERE accno = '1801')")
        c.execute("DELETE FROM tax WHERE taxnumber IS NULL")
        yield c


@pytest.fixture
def data(settings, company_db, cdb):
    conn = connect(**settings.company_conninfo(company_db))
    al = ArapLedger(conn, UserConfig(login="x"))
    for vc, vc_id, arap, acc, net, taxes in (
            ("customer", 902, "1200", "8520", "100.60", {"1810": "21.13"}),
            ("customer", 903, "1200", "8520", "200", {}),
            ("vendor", 905, "1600", "8100", "40.40", {"1800": "8.48"})):
        al.post_transaction(ArapTransaction(vc=vc, vc_id=vc_id, transdate=date(2031, 2, 10), arap_accno=arap,
                                            lines=[ArapLine(accno=acc, amount=D(net))],
                                            taxes={k: D(v) for k, v in taxes.items()}))
    conn.close()


def user(client, db, company, role, name=None, lang=None):
    name = name or role
    secret = make_user(db, name, totp=role == "admin", memberships=[(company["id"], role)])
    if lang:
        db.execute("UPDATE app_user SET language = %s WHERE login = %s", (lang, name))
    login(client, name, secret)
    return client


def text(h):
    return re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", nohelp(h))))


def setup_boxes(client):
    page = client.get("/btw/rubrieken").text
    fields = dict(re.findall(r'<select name="(box_\d+)"[^>]*>.*?<option value="(\w+)" selected', page, re.S))
    r = post(client, "/btw/rubrieken", {**fields, "settlement": "1850", "rounding": "8100"},
             follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/btw/rubrieken?ok=vat_boxes_saved"


def form_fields(page):
    """Velden zoals de browser ze zou versturen (radio/vinkje alleen aangevinkt, keuzelijst de gekozen optie)."""
    out = {}
    for tag in re.findall(r"<input[^>]*>", page):
        m = re.search(r'name="([^"]+)"', tag)
        v = re.search(r'value="([^"]*)"', tag)
        if not m or (re.search(r'type="(radio|checkbox)"', tag) and " checked" not in tag):
            continue
        out[m.group(1)] = v.group(1) if v else ""
    for name, body in re.findall(r'<select name="([^"]+)"[^>]*>(.*?)</select>', page, re.S):
        sel = re.search(r'<option value="([^"]*)" selected', body)
        out[name] = sel.group(1) if sel else ""
    out.update(re.findall(r'<textarea[^>]*name="([^"]+)"[^>]*>([^<]*)</textarea>', page))
    return {k: htmlmod.unescape(v) for k, v in out.items()}


def test_boxes_screen_suggests_and_saves(client, db, company, cdb):
    user(client, db, company, "admin")
    page = client.get("/btw/rubrieken").text
    assert "Nog niet opgeslagen" in page and "voorstel" in page
    assert re.search(r'<option value="1850" selected>1850 Saldo BTW', page)
    assert "Btw-aangifte" not in text(client.get("/rapporten/proefbalans").text.split("<main")[0])
    setup_boxes(client)
    assert db.execute("SELECT vat_return FROM company WHERE id = %s", (company["id"],)).fetchone()["vat_return"]
    assert 'href="/btw/aangifte"' in client.get("/btw/rubrieken").text
    boxes = {r["accno"]: r["box"] for r in cdb.execute(
        "SELECT c.accno, b.box FROM x_vat_box b JOIN chart c ON (c.id = b.chart_id)")}
    assert boxes == {"1800": "5B", "1801": "5B", "1810": "1A", "1811": "1B"}
    r = post(client, "/btw/rubrieken", {"settlement": "1810"})
    assert r.status_code == 400 and "geen btw-rekening" in r.text
    # tijdvak "geen" verbergt het menu-item (BA2)
    page = client.get("/beheer/instellingen").text
    fields = form_fields(page)
    fields.update({"vat_period": "none"})
    fields.pop("csrf", None)
    post(client, "/beheer/instellingen", fields)
    assert not db.execute("SELECT vat_return FROM company WHERE id = %s", (company["id"],)).fetchone()["vat_return"]


def test_overview_detail_exports(client, db, company, data):
    user(client, db, company, "admin")
    setup_boxes(client)
    page = client.get(f"/btw/aangifte?tijdvak={KEY}").text
    t = text(page)
    assert "Btw-aangifte (factuurstelsel): 2031 1e kwartaal" in t
    assert re.search(r"1A Leveringen/diensten belast met hoog tarief 100 21", t)
    assert re.search(r"1E Leveringen/diensten belast met 0% of niet bij u belast 200", t)
    assert re.search(r"5B Voorbelasting 9", t) and re.search(r"5G Totaal te betalen / terug te vragen 12", t)
    assert "Btw-boeking maken" in t and "Omzet zonder btw: 1 boekingen in 1E" in t
    detail = text(client.get(f"/btw/aangifte?tijdvak={KEY}&rubriek=1A").text)
    assert "100,60" in detail or "100.60" in detail
    x = client.get(f"/btw/aangifte?tijdvak={KEY}&export=xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    p = client.get(f"/btw/aangifte?tijdvak={KEY}&export=pdf")
    assert p.status_code == 200 and p.content[:4] == b"%PDF"
    # vrije periode
    assert "Btw-aangifte (factuurstelsel)" in client.get("/btw/aangifte?van=2031-02-01&tot=2031-02-20").text


def test_form_draft_and_pdf(client, db, company, data):
    user(client, db, company, "admin")
    setup_boxes(client)
    page = client.get(f"/btw/aangifte/formulier?tijdvak={KEY}").text
    t = text(page)
    assert "Proefafdruk" in t and "Gegevens omzet en omzetbelasting" in t
    assert re.search(r"1a Leveringen/diensten belast met hoog tarief 100 21 1b", t)       # € via CSS
    assert re.search(r"5e Schatting vorige aangifte\(n\)", t) and "× te betalen" in t
    assert f'href="/btw/aangifte?tijdvak={KEY}&amp;rubriek=1A#detail"' in page
    p = client.get(f"/btw/aangifte/formulier?tijdvak={KEY}&export=pdf")
    assert p.status_code == 200 and p.content[:4] == b"%PDF"


def test_booking_via_memoriaal(client, db, company, data, cdb):
    user(client, db, company, "admin")
    setup_boxes(client)
    page = client.get(f"/memoriaal/nieuw?btw={KEY}").text
    assert "Btw-boeking volgens de btw-aangifte" in page
    f = form_fields(page)
    assert f["vat_period"] == KEY and f["transdate"] == "2031-03-31"
    assert f["description"] == "Btw-aangifte 2031 1e kwartaal"
    rows = {(f.get(f"accno_{i}"), f.get(f"debit_{i}"), f.get(f"credit_{i}")) for i in range(1, 8) if f.get(f"accno_{i}")}
    assert {("1810", "21,13", ""), ("1800", "", "8,48"), ("1850", "", "12,00"), ("8100", "", "0,65")} <= rows
    assert f["rowcount"].isdigit()
    f.pop("csrf")
    r = client.post("/memoriaal", data={"csrf": csrf(client, page), **f, "action": "post"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/btw/aangifte?tijdvak={KEY}&ok=vat_booked"
    ret = cdb.execute("SELECT * FROM x_vat_return").fetchone()
    assert ret["snapshot"]["totals"]["5G"] == "12"
    t = text(client.get(r.headers["location"]).text)
    assert "Btw-boeking bekijken" in t and "Btw-boeking maken" not in t and "Beheer > Afsluiten" in t
    form = text(client.get(f"/btw/aangifte/formulier?tijdvak={KEY}").text)
    assert "Proefafdruk" not in form and "Definitief: btw-boeking" in form
    assert cdb.execute("SELECT 1 FROM x_task_done WHERE kind = 'vat' AND period_end = '2031-03-31'").fetchone()


def test_customer_box(client, db, company, data, cdb):
    user(client, db, company, "admin")
    setup_boxes(client)
    page = client.get("/klanten/903").text
    assert 'name="vat_box"' in page and "3B Leveringen naar of diensten in landen binnen de EU" in page
    f = form_fields(page)
    f.pop("csrf")
    f["vat_box"] = "3B"
    r = client.post("/klanten", data={"csrf": csrf(client, page), **f, "action": "save"}, follow_redirects=False)
    assert r.status_code == 400 and "US99 is geen geldig EU-btw-nummer" in r.text     # EU-klant: nummer verplicht
    f["taxnumber"] = "DE123456789"
    r = client.post("/klanten", data={"csrf": csrf(client, page), **f, "action": "save"}, follow_redirects=False)
    assert r.status_code == 303, r.text[:500]
    assert cdb.execute("SELECT box FROM x_vat_customer_box WHERE customer_id = 903").fetchone()["box"] == "3B"
    t = text(client.get(f"/btw/aangifte?tijdvak={KEY}").text)
    assert re.search(r"3B Leveringen naar of diensten in landen binnen de EU 200", t)


def test_rights_and_language(client, db, company, data):
    user(client, db, company, "admin", "beheer")
    setup_boxes(client)
    client.cookies.clear()
    user(client, db, company, "readonly", "kijker", lang="en")
    assert client.get("/btw/rubrieken").status_code == 403
    page = client.get(f"/btw/aangifte?tijdvak={KEY}").text
    t = text(page)
    assert "VAT return (accrual basis): Q1 2031" in t and "Supplies/services taxed at the standard rate" in t
    assert "Make VAT entry" not in t
    assert client.get(f"/memoriaal/nieuw?btw={KEY}").status_code == 403


def test_not_configured_message(client, db, company):
    user(client, db, company, "bookkeeper")
    t = text(client.get("/btw/aangifte").text)
    assert "Er zijn nog geen btw-rekeningen aan rubrieken van de aangifte gekoppeld" in t
    assert "Vraag de beheerder" in t


def test_boxes_country_without_form_and_rates(client, db, company, cdb):
    """§12 VP3/VP4: Zwitserse administratie: melding, geen Nederlandse rubrieken als voorstel; lege landkeuze slaat
    niets op. Tarieven met decimalen."""
    from sqlledger_web.routes_vat import rate_text
    assert rate_text(Decimal("0.081"), "1.000,00") == "8,1%" and rate_text(Decimal("0.21"), "1,000.00") == "21%"
    assert rate_text(Decimal("0.025"), "1,000.00") == "2.5%" and rate_text(None, "1,000.00") == ""
    user(client, db, company, "admin")
    from sqlledger.extensions import ensure_schema
    ensure_schema(cdb)
    cdb.execute("INSERT INTO x_setting (key, value) VALUES ('country', 'CH')")
    page = htmlmod.unescape(client.get("/btw/rubrieken").text)
    assert "Voor Zwitserland heeft PyBalans nog geen btw-aangifte." in page
    assert "Voorstel: Nederland" not in page and "vatboxes" not in page and '<option value="" selected>' in page
    r = post(client, "/btw/rubrieken", {"switch": "1", "country": ""})
    assert r.status_code == 400 and "Kies het land van de btw-aangifte." in r.text
    assert not cdb.execute("SELECT 1 FROM x_setting WHERE key = 'vat_country'").fetchone()
    post(client, "/btw/rubrieken", {"switch": "1", "country": "NL"})
    assert "vatboxes" in client.get("/btw/rubrieken").text
