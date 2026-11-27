"""Overstap uit SQL-Ledger (FASE6_ANALYSE.md 6d-2): lijst op het dashboard na inlezen, Beheer > Ingebruikname en het
controleblad (scherm en PDF)."""

import io
from html import unescape
import re
import uuid

import psycopg
import pytest

from sqlledger import switchover
from webhelpers import csrf, login, make_user, restore_backup
from test_web_restore import dump_template


def text(html):
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)))


@pytest.fixture
def newdb(pg_admin):
    name = f"ws_{uuid.uuid4().hex[:10]}"
    yield name
    pg_admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def restore(client, db, newdb, url="/systeem/back-up"):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    r = restore_backup(client, dump_template(), url, name="Overstap BV", dbname=newdb, add_me="1")
    assert r.status_code == 200 and "Back-up ingelezen" in r.text, r.text[-500:]
    company = db.execute("SELECT * FROM company WHERE dbname = %s", (newdb,)).fetchone()
    client.post("/administratie", data={"csrf": csrf(client, client.get("/administratie").text),
                                         "company_id": company["id"]})
    return company


def cdb(settings, name):
    return psycopg.connect(**settings.company_conninfo(name), autocommit=True)


def test_restore_turns_on_switch_list(client, db, settings, newdb):
    restore(client, db, newdb)
    with cdb(settings, newdb) as c:
        info = switchover.restore_info(c)
    assert info["kind"] == "pg_dump" and info["version_to"] == "3.2.4" and info["last_transdate"]
    start = client.get("/dashboard/blok/start").text
    assert "Overstap uit SQL-Ledger" in start and "Aan de slag" not in start
    t = text(start)
    assert "Back-up ingelezen klaar" in t and "Cijfers controleren met het controleblad" in t
    assert 'value="check"' in start and "gedaan" in t                       # zelf afvinken
    assert 'href="/systeem/back-up-maken?overstap=backup&amp;terug=%2F"' in start    # sysadmin: link naar back-up


def test_mark_done_skip_and_hide(client, db, settings, newdb):
    restore(client, db, newdb)
    page = client.get("/beheer/ingebruikname").text
    assert "Leg het controleblad naast" in page
    token = csrf(client, page)
    r = client.post("/dashboard/overstaplijst", data={"csrf": token, "item": "check", "done": "1",
                                                       "back": "/beheer/ingebruikname"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/beheer/ingebruikname"
    client.post("/dashboard/overstaplijst", data={"csrf": token, "item": "style"})
    assert client.post("/dashboard/overstaplijst",
                       data={"csrf": token, "item": "company", "done": "1"}).status_code == 400
    t = text(client.get("/dashboard/blok/start").text)
    assert "Cijfers controleren met het controleblad klaar" in t and "Logo en huisstijl niet nodig" in t
    client.post("/dashboard/overstaplijst", data={"csrf": token, "item": "hidden"})
    assert "Overstap uit SQL-Ledger" not in client.get("/dashboard/blok/start").text
    page = client.get("/beheer/ingebruikname").text
    assert "Lijst weer tonen" in page
    client.post("/beheer/ingebruikname/aanzetten", data={"csrf": csrf(client, page)})
    assert "Overstap uit SQL-Ledger" in client.get("/dashboard/blok/start").text


def test_enable_for_linked_company_and_roles(client, db, company, settings):
    secret = make_user(db, "baas", memberships=[(company["id"], "admin")], totp=True)
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper")])
    login(client, "baas", secret)
    page = client.get("/beheer/ingebruikname").text
    assert "Overstaplijst aanzetten" in page and 'href="/beheer/ingebruikname"' in page     # menu Beheer
    client.post("/beheer/ingebruikname/aanzetten", data={"csrf": csrf(client, page)})
    with cdb(settings, company["dbname"]) as c:
        assert switchover.restore_info(c)["kind"] == "linked"
    t = text(client.get("/beheer/ingebruikname").text)
    assert "gekoppelde database" in t
    assert "Iedereen een eigen login met tweestapsverificatie" in t and "tweestapsverificatie klaar" not in t
    assert db.execute("SELECT 1 FROM audit_log WHERE action = 'switch_list_enabled'").fetchone()
    client.cookies.clear()
    login(client, "boek")
    assert client.get("/beheer/ingebruikname").status_code == 403
    assert client.get("/beheer/ingebruikname/controleblad").status_code == 403


def test_control_sheet_and_pdf(client, db, settings, newdb):
    restore(client, db, newdb)
    with cdb(settings, newdb) as c:
        last = switchover.restore_info(c)["last_transdate"]
        c.execute("UPDATE ar SET paid = amount + 10 WHERE id = (SELECT min(id) FROM ar WHERE amount > 0)")
    t = text(client.get("/beheer/ingebruikname/controleblad").text)
    y, m, d = last.split("-")
    assert f"Peildatum {d}-{m}-{y}" in t
    for title in ("Controles", "Aantal boekingen per jaar", "Kas en bank per", "Proefbalans", "Winst en verlies",
                  "Balans", "Openstaande verkoopfacturen per klant per", "Openstaande inkoopfacturen per leverancier per",
                  "Facturen met meer betaald dan het bedrag"):
        assert title in t, title
    assert "Facturen met meer betaald dan het bedrag 1 nakijken" in t
    assert "Boekingen niet in evenwicht (debet ≠ credit) 0 in orde" in t
    t2 = text(client.get("/beheer/ingebruikname/controleblad?datum=2026-01-31").text)
    assert "Peildatum 31-01-2026" in t2 and "01-01-2026 t/m 31-01-2026" in t2
    r = client.get("/beheer/ingebruikname/controleblad?datum=2026-01-31&export=pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content[:5] == b"%PDF-"


def test_substeps_page_and_dashboard_counter(client, db, settings, newdb):
    """Deelstappen (6d-2, beslispunten 1–4): alleen op Beheer > Ingebruikname; op het dashboard een teller."""
    company = restore(client, db, newdb)
    for n in range(6):                                                  # 6 gebruikers zonder 2FA
        make_user(db, f"zonder{n}", memberships=[(company["id"], "bookkeeper")])
    with cdb(settings, newdb) as c:
        c.execute("UPDATE bank SET iban = ''")
        c.execute("UPDATE defaults SET fldvalue = '' WHERE fldname = 'address'")
        n_tax = c.execute("SELECT count(*) FROM chart WHERE id IN (SELECT chart_id FROM tax) "
                          "AND NOT coalesce(closed, false)").fetchone()[0]
        pay = c.execute("SELECT id, accno FROM chart WHERE (link LIKE '%AR_paid%' OR link LIKE '%AP_paid%') "
                        "AND NOT coalesce(closed, false) ORDER BY accno").fetchall()
    page = client.get("/beheer/ingebruikname").text
    t = text(page)
    assert 'class="substeps"' in page
    assert "Bedrijfsnaam ingevuld klaar" in t and "Adres ingevuld · Beheer > Instellingen" in t
    assert 'href="/beheer/instellingen?overstap=company&amp;terug=%2Fbeheer%2Fingebruikname#bedrijf"' in page and 'id="bedrijf"' in client.get("/beheer/instellingen").text
    assert "Aangiftetijdvak gekozen" in t and 'href="/beheer/instellingen?overstap=vat&amp;terug=%2Fbeheer%2Fingebruikname#btw-tijdvak"' in page
    assert f"{n_tax} zonder rubriek:" in t and 'href="/btw/rubrieken?overstap=vat&amp;terug=%2Fbeheer%2Fingebruikname"' in page
    assert "IBAN bij een betaalrekening" in t and "nog bij geen enkele betaalrekening" in t
    assert "#bank" not in page                                    # geen lijst van alle betaalrekeningen
    assert "Eerste bankafschrift ingelezen · Kas en bank > Bankafschrift inlezen" in t
    # sysadmin: namen, eerste 5 + "en nog 1" met link naar de volledige lijst
    assert "zonder tweestapsverificatie:" in t and "Zonder0" in t and "Zonder4" in t and "Zonder5" not in t
    assert '<a href="/systeem/gebruikers?overstap=users&amp;terug=%2Fbeheer%2Fingebruikname">en nog 1</a>' in page
    assert "laatste back-up: nooit" in t
    assert "nu: 31-12-2025; moet later zijn dan bij het inlezen: 31-12-2025" in t        # afsluitdatum
    # dashboard: geen deelstappen, wel de teller achter een open punt
    start = client.get("/dashboard/blok/start").text
    st = text(start)
    assert 'class="substeps"' not in start and "geen enkele betaalrekening" not in st
    assert "Bedrijfsgegevens controleren (naam en adres) (1 van 2)" in st
    assert "IBAN invullen en het eerste bankafschrift inlezen (0 van 3)" in st
    # beheerder van de administratie (geen systeembeheerder): alleen het aantal
    secret = make_user(db, "baas", memberships=[(company["id"], "admin")], totp=True)
    client.cookies.clear()
    login(client, "baas", secret)
    client.post("/administratie", data={"csrf": csrf(client, client.get("/administratie").text),
                                         "company_id": company["id"]})
    page = client.get("/beheer/ingebruikname").text
    t = text(page)
    assert "6 gebruikers zonder tweestapsverificatie" in t and "Zonder0" not in t
    assert "Systeem > Gebruikers" not in t and "Systeem > Back-up maken" not in t
    assert 'href="/systeem/gebruikers/' not in page and 'href="/systeem/back-up-maken' not in page


def test_substeps_long_account_list(client, db, settings, newdb):
    restore(client, db, newdb)
    with cdb(settings, newdb) as c:
        for n in range(7):
            cid = c.execute("INSERT INTO chart (accno, description, charttype, category, link) "
                            "VALUES (%s, %s, 'A', 'L', 'AR_tax:AP_tax') RETURNING id",
                            (f"1979{n}", f"Btw {n} met een heel lange " "omschrijving_zonder_spaties_erin_" * 2)
                            ).fetchone()[0]
            c.execute("INSERT INTO tax (chart_id, rate) VALUES (%s, 0.21)", (cid,))
        total = c.execute("SELECT count(*) FROM chart c WHERE c.id IN (SELECT chart_id FROM tax) "
                          "AND NOT coalesce(c.closed, false) AND NOT EXISTS (SELECT 1 FROM x_vat_box b "
                          "WHERE b.chart_id = c.id AND b.country = 'NL')").fetchone()[0]
    page = client.get("/beheer/ingebruikname").text
    assert f"{total} zonder rubriek:" in page.replace("&nbsp;", " ")
    assert f'<a href="/btw/rubrieken?overstap=vat&amp;terug=%2Fbeheer%2Fingebruikname">en nog {total - 5}</a>' in page


def test_control_sheet_verdict(client, db, settings, newdb):
    """v1.0.1 PB8: uitleg in 3 stappen, "Terug naar Ingebruikname" als knop, onderaan akkoord / er klopt iets niet
    (niet in PDF of afdruk); de notitie staat bij het punt op de overstaplijst."""
    restore(client, db, newdb)
    page = client.get("/beheer/ingebruikname/controleblad").text
    t = text(page)
    assert '<ol class="steps3">' in page and "Klopt alles? Klik onderaan op \"Cijfers kloppen – akkoord\"" in t
    assert '<a class="button secondary" href="/beheer/ingebruikname">Terug naar Ingebruikname</a>' in page
    assert '<div class="head"><h2>Controleblad overstap</h2></div>' in page            # geen losse link rechtsboven
    assert '<section class="sheet-verdict noprint" id="oordeel"' in page
    # er klopt iets niet: notitie verplicht, daarna open met notitie
    form = {"csrf": csrf(client, page), "verdict": "issue", "datum": "2026-01-31"}
    r = client.post("/beheer/ingebruikname/controleblad/oordeel", data={**form, "note": ""})
    assert "Schrijf op wat er niet klopt." in r.text
    r = client.post("/beheer/ingebruikname/controleblad/oordeel", data={**form, "note": "Bank wijkt 10 euro af"})
    assert "Het punt blijft open" in r.text and "Bank wijkt 10 euro af" in r.text and "Peildatum 31-01-2026" in text(r.text)
    lst = text(client.get("/beheer/ingebruikname").text)
    assert "Er klopt iets niet: Bank wijkt 10 euro af (Anna," in lst
    with cdb(settings, newdb) as c:
        assert not c.execute("SELECT 1 FROM x_task_done WHERE kind = 'switchdone_check'").fetchone()
    # akkoord: afgevinkt met datum en naam, notitie weg
    r = client.post("/beheer/ingebruikname/controleblad/oordeel",
                    data={"csrf": csrf(client, page), "verdict": "ok", "datum": ""})
    assert r.url.path == "/beheer/ingebruikname" and "Akkoord vastgelegd" in r.text
    t = text(r.text)
    assert "Cijfers controleren met het controleblad klaar · akkoord op" in t and "door Anna" in t
    assert "Bank wijkt" not in t
    assert db.execute("SELECT 1 FROM audit_log WHERE action = 'switch_check_ok'").fetchone()
    assert db.execute("SELECT detail FROM audit_log WHERE action = 'switch_check_issue'").fetchone()["detail"][
        "note"] == "Bank wijkt 10 euro af"


def test_recurring_item_on_switch_list(client, db, settings, newdb):
    """v1.0.1 PB16: overstappunt "Terugkerende boekingen nakijken" als er een schema is met een volgende datum vóór
    de laatste boeking bij het inlezen; per schema Stoppen en Volgende datum aanpassen."""
    restore(client, db, newdb)
    assert "Terugkerende boekingen nakijken" not in client.get("/beheer/ingebruikname").text   # geen schema's
    with cdb(settings, newdb) as c:
        last = switchover.restore_info(c)["last_transdate"]
        gid = c.execute("SELECT min(id) FROM gl").fetchone()[0]
        c.execute("INSERT INTO recurring (id, reference, description, startdate, nextdate, enddate, repeat, unit, "
                  "howmany, payment) VALUES (%s, '', 'huur opslag', '2013-12-01', '2015-11-01', '2015-11-01', 1, "
                  "'months', 24, false)", (gid,))
    page = client.get("/beheer/ingebruikname").text
    t = text(page)
    assert "Terugkerende boekingen nakijken" in t and "1 schema; stop het of pas de volgende datum aan:" in t
    assert "huur opslag – volgende 01-11-2015" in t and f'<a href="/herhalen/{gid}?overstap=recurring&amp;terug=%2Fbeheer%2Fingebruikname">Volgende datum aanpassen</a>' in page
    r = client.post(f"/terugkerend/{gid}/stoppen", data={"csrf": csrf(client, page), "back": "/beheer/ingebruikname"})
    assert r.url.path == "/beheer/ingebruikname" and "Herhaalschema gestopt." in r.text
    t = text(r.text)
    assert "Terugkerende boekingen nakijken klaar" in t
    assert last


def test_switch_bar_back_to_list(client, db, settings, newdb):
    """v1.0.1 PB10: vanuit de lijst geopend = balkje "Overstap: … – Terug naar de overstaplijst", ook na Opslaan;
    btw toont beide pagina's; alleen bekende punten en terugadressen (geen open redirect)."""
    restore(client, db, newdb)
    page = client.get("/beheer/ingebruikname").text
    url = "/btw/rubrieken?overstap=vat&terug=%2Fbeheer%2Fingebruikname"
    assert f'href="{url.replace("&", "&amp;")}"' in page
    r = client.get(url)
    t = text(r.text)
    assert '<nav class="switchbar noprint"' in r.text
    assert "Overstap: Btw-rubrieken en aangiftetijdvak instellen" in t and "Terug naar de overstaplijst" in t
    assert '<a href="/btw/rubrieken">Btw-rubrieken</a>' in r.text
    assert '<a href="/beheer/instellingen#btw-tijdvak">Aangiftetijdvak</a>' in r.text
    assert '<a href="/beheer/ingebruikname">Terug naar de overstaplijst</a>' in r.text
    # opslaan (POST + doorverwijzing): het balkje blijft
    form = {k: v for k, v in re.findall(r'<select name="(box_\d+)"[^>]*>.*?<option value="([^"]*)" selected',
                                        r.text, re.S)}
    r = client.post("/btw/rubrieken", data={"csrf": csrf(client, r.text), **form})
    assert r.url.path == "/btw/rubrieken" and "overstap=" not in str(r.url) and "Btw-rubrieken opgeslagen." in r.text
    assert "Overstap: Btw-rubrieken en aangiftetijdvak instellen" in text(r.text)
    # de andere pagina van hetzelfde punt ook; een pagina van een ander punt niet
    assert "Terug naar de overstaplijst" in client.get("/beheer/instellingen").text
    assert "Terug naar de overstaplijst" not in client.get("/beheer/huisstijl").text
    # terug op de lijst: vergeten
    client.get("/beheer/ingebruikname")
    assert "Terug naar de overstaplijst" not in client.get("/btw/rubrieken").text
    # bank: twee links; onbekend punt of vreemd terugadres
    r = client.get("/bank/inlezen?overstap=bank&terug=/")
    assert '<a href="/beheer/rekeningschema">IBAN bij de bankrekening</a>' in r.text
    assert '<a href="/">Terug naar de overstaplijst</a>' in r.text
    r = client.get("/bank/inlezen?overstap=bank&terug=https://example.com")
    assert '<a href="/beheer/ingebruikname">Terug naar de overstaplijst</a>' in r.text and "example.com" not in r.text
    client.get("/")
    assert "switchbar" not in client.get("/bank/inlezen?overstap=onzin&terug=/").text


def test_control_sheet_from_start_of_fiscal_year(client, db, settings, newdb):
    """F22/JA7: proefbalans en winst en verlies lopen vanaf het begin van het boekjaar van de peildatum."""
    restore(client, db, newdb)
    with cdb(settings, newdb) as c:
        c.execute("DELETE FROM defaults WHERE fldname = 'yearend'")
        c.execute("INSERT INTO defaults VALUES ('yearend', '6')")
    t = text(client.get("/beheer/ingebruikname/controleblad?datum=2026-01-31").text)
    assert "01-07-2025 t/m 31-01-2026" in t and "01-01-2026 t/m 31-01-2026" not in t


def test_numbering_shows_next_number(client, db, settings, newdb):
    """K2: "Nummering controleren" toont het volgende nummer ingevuld, het sjabloon erachter; er wordt niets
    opgeslagen."""
    from datetime import date
    restore(client, db, newdb)
    with cdb(settings, newdb) as c:
        for k, v in (("sinumber", "20<%yy%>-14337"), ("glnumber", "0042")):
            c.execute("UPDATE defaults SET fldvalue = %s WHERE fldname = %s", (v, k))
            if not c.execute("SELECT 1 FROM defaults WHERE fldname = %s", (k,)).fetchone():
                c.execute("INSERT INTO defaults (fldname, fldvalue) VALUES (%s, %s)", (k, v))
    t = text(client.get("/beheer/ingebruikname").text)
    yy = date.today().strftime("%y")
    assert f"verkoopfactuur 20{yy}-14338 (Instellingen: 20<%yy%>-14337)" in t and "memoriaal 0043" in t
    assert "volgende:" in t and "laatst gebruikt" not in t
    with cdb(settings, newdb) as c:
        assert c.execute("SELECT fldvalue FROM defaults WHERE fldname = 'sinumber'").fetchone()[0] == "20<%yy%>-14337"
