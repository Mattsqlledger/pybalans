"""Systeem > RGS-lijst en Beheer > RGS-koppeling (REKENINGSCHEMA_ANALYSE.md RS13 keuze A)."""

import io
import re

import psycopg
import pytest

from conftest import DSN
from rgsfile import make_xlsx
from webhelpers import csrf, login, make_user

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def choose(client, company):
    page = client.get("/administratie").text
    client.post("/administratie", data={"csrf": csrf(client, page), "company_id": company["id"]})


def cconn(company):
    return psycopg.connect(f"{DSN} dbname={company['dbname']}", autocommit=True)


def upload(client, data, name="RGS 3.8-def.xlsx"):
    page = client.get("/systeem/rgs").text
    return client.post("/systeem/rgs", data={"csrf": csrf(client, page)},
                       files={"bestand": (name, io.BytesIO(data), XLSX)}, follow_redirects=True)


@pytest.fixture
def anna(client, db, company):
    """Systeembeheerder én beheerder van de administratie."""
    secret = make_user(db, "anna", sysadmin=True, totp=True, memberships=[(company["id"], "admin", "anna")])
    login(client, "anna", secret)
    choose(client, company)
    return client


def test_upload_list(anna):
    page = anna.get("/systeem/rgs")
    assert page.status_code == 200 and "Er is nog geen RGS-lijst ingelezen." in page.text
    assert 'href="/systeem/rgs"' in page.text                        # in het systeemmenu
    r = upload(anna, b"geen excel", "x.xlsx")
    assert r.status_code == 400 and "Dit is geen leesbaar Excel-bestand (xlsx)." in r.text
    r = upload(anna, make_xlsx())
    assert r.status_code == 200 and "De RGS-lijst is ingelezen." in r.text
    assert "RGS 3.8" in r.text and "111 (MKB: 2" in r.text and "RGS 3.8-def.xlsx" in r.text
    log = anna.get("/systeem/logboek").text
    assert "RGS-lijst ingelezen" in log


def test_download_hint_and_prerelease_warning(anna):
    """PB13: link naar de Kennisbank met aanwijzing; een alfa/bèta wordt ingelezen met een waarschuwing."""
    page = anna.get("/systeem/rgs").text
    assert 'href="https://www.referentiegrootboekschema.nl/kbase"' in page and 'rel="noopener"' in page
    assert "Definitieve versie RGS 3.x" in page and "bij het uitbrengen van deze versie: RGS 3.8" in page
    r = upload(anna, make_xlsx("3.9", suffix="beta", recap="Bijgaand de bèta-versie van RGS 3.9"), "RGS 3.9-beta.xlsx")
    assert r.status_code == 200 and "De RGS-lijst is ingelezen." in r.text and "RGS 3.9" in r.text
    assert "Let op: deze lijst lijkt geen definitieve versie" in r.text
    assert "de bestandsnaam, de naam van het blad Totaal-RGS, de versietekst op het blad Recap" in r.text
    # de definitieve versie van dezelfde versie vervangt hem, zonder waarschuwing
    r = upload(anna, make_xlsx("3.9", recap="Bijgaand de definitieve versie van RGS 3.9"), "RGS 3.9-def.xlsx")
    assert "RGS 3.9" in r.text and "Let op: deze lijst lijkt geen definitieve versie" not in r.text
    log = anna.get("/systeem/logboek").text
    assert "RGS-lijst ingelezen" in log


def test_rights(client, db, company):
    secret = make_user(db, "boek", totp=True, memberships=[(company["id"], "bookkeeper", "boek")])
    login(client, "boek", secret)
    choose(client, company)
    assert client.get("/beheer/rgs").status_code == 403
    assert client.get("/systeem/rgs", follow_redirects=False).status_code != 200


def test_mapping_without_list(anna):
    page = anna.get("/beheer/rgs")
    assert page.status_code == 200 and "Er is nog geen RGS-lijst ingelezen." in page.text
    assert 'href="/systeem/rgs"' in page.text


def _save(client, page, **choices):
    data = {"csrf": csrf(client, page), "toon": re.search(r'name="toon" value="(\w+)"', page).group(1),
            "acc": re.findall(r'name="acc" value="(\d+)"', page)}
    data.update(choices)
    return client.post("/beheer/rgs", data=data, follow_redirects=True)


def test_mapping_flow(anna, company):
    upload(anna, make_xlsx())
    with cconn(company) as c:
        deb = c.execute("SELECT id FROM chart WHERE accno = '1200'").fetchone()[0]
        bank = c.execute("SELECT id FROM chart WHERE accno = '1100'").fetchone()[0]
        # F20: alleen open boekbare rekeningen, zoals de overstaplijst
        total = c.execute("SELECT count(*) FROM chart WHERE charttype = 'A' AND NOT coalesce(closed, false)").fetchone()[0]
    page = anna.get("/beheer/rgs").text
    assert f"0 van {total} rekeningen hebben een code." in page
    # voorstellen, niets aangevinkt (niets automatisch)
    row = re.search(rf'name="acc" value="{deb}".*?</tr>', page, re.S).group(0)
    assert 'value="BVorDebHad"' in row and "checked" not in row
    # één voorstel kiezen, één andere code, de rest leeg = ongewijzigd
    r = _save(anna, page, **{f"c_{deb}": "BVorDebHad", f"c_{bank}": "other", f"o_{bank}": "BLimBanRba"})
    assert r.status_code == 200 and "2 koppelingen opgeslagen." in r.text
    assert f"2 van {total} rekeningen hebben een code." in r.text
    assert f'name="acc" value="{deb}"' not in r.text                  # filter: alleen zonder code
    with cconn(company) as c:
        assert c.execute("SELECT DISTINCT version, login FROM x_account_rgs").fetchall() == [("3.8", "anna")]
        got = dict(c.execute("SELECT chart_id, code FROM x_account_rgs").fetchall())
    assert got == {deb: "BVorDebHad", bank: "BLimBanRba"}
    # alle rekeningen: de huidige code is aangevinkt
    page = anna.get("/beheer/rgs?toon=all").text
    row = re.search(rf'name="acc" value="{deb}".*?</tr>', page, re.S).group(0)
    assert 'value="BVorDebHad" checked' in row and 'value="none"' in row
    # onbekende code: niets opgeslagen, melding
    r = _save(anna, page, **{f"c_{deb}": "other", f"o_{deb}": "BBestaatNiet"})
    assert r.status_code == 400 and "RGS-code BBestaatNiet bestaat niet in RGS 3.8." in r.text
    # koppeling weghalen
    r = _save(anna, page, **{f"c_{deb}": "none"})
    assert "1 koppeling opgeslagen." in r.text
    with cconn(company) as c:
        assert dict(c.execute("SELECT chart_id, code FROM x_account_rgs").fetchall()) == {bank: "BLimBanRba"}
    # kolom RGS op het rekeningschema
    acc = anna.get("/beheer/rekeningschema").text
    assert "<th>RGS</th>" in acc and "BLimBanRba" in acc
    assert "RGS-koppeling opgeslagen" in anna.get("/systeem/logboek").text


def test_search(anna):
    upload(anna, make_xlsx())
    page = anna.get("/beheer/rgs?zoek=omzetbelasting").text
    assert "BSchBepBtw" in page and "Te betalen omzetbelasting" in page
    page = anna.get("/beheer/rgs?zoek=BLimKas").text
    assert "BLimKasKas" in page
    assert "Niets gevonden." in anna.get("/beheer/rgs?zoek=zzzz").text


def test_delete_version(anna, company):
    """Keuze 10 (v1.0.1): een ingelezen versie verwijderen; eerst bevestigen (met de koppelingen per administratie),
    daarna geldt de nieuwste overgebleven versie; koppelingen in x_account_rgs blijven staan."""
    upload(anna, make_xlsx())
    upload(anna, make_xlsx("3.9", suffix="beta", recap="Bijgaand de bèta-versie van RGS 3.9"), "RGS 3.9-beta.xlsx")
    with cconn(company) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS x_account_rgs (chart_id int, version text, code text, login text,
                     PRIMARY KEY (chart_id, version))""")
        c.execute("INSERT INTO x_account_rgs (chart_id, version, code) "
                  "SELECT id, '3.9', 'BLimKasKas' FROM chart WHERE charttype = 'A' LIMIT 2")
    page = anna.get("/systeem/rgs").text
    assert "Ingelezen versies" in page and page.count('action="/systeem/rgs/verwijderen"') == 2
    assert "in gebruik" in page and "RGS 3.9" in page
    r = anna.post("/systeem/rgs/verwijderen", data={"csrf": csrf(anna, page), "versie": "3.9"})
    assert r.status_code == 200 and "RGS 3.9 verwijderen?" in r.text
    assert "Daarna geldt de nieuwste overgebleven versie." in r.text and "Testbedrijf: 2 rekeningen" in r.text
    assert 'value="confirmed"' in r.text
    r = anna.post("/systeem/rgs/verwijderen", data={"csrf": csrf(anna, page), "versie": "3.9", "action": "confirmed"},
                  follow_redirects=True)
    assert "De RGS-lijst is verwijderd." in r.text and "RGS 3.8" in r.text and "RGS 3.9" not in r.text
    with cconn(company) as c:
        assert c.execute("SELECT count(*) FROM x_account_rgs WHERE version = '3.9'").fetchone()[0] == 2
    assert "RGS-lijst verwijderd" in anna.get("/systeem/logboek").text
    # onbekende versie; de laatste versie verwijderen meldt dat er dan geen lijst meer is
    r = anna.post("/systeem/rgs/verwijderen", data={"csrf": csrf(anna, page), "versie": "../x"})
    assert r.status_code == 404
    r = anna.post("/systeem/rgs/verwijderen", data={"csrf": csrf(anna, page), "versie": "3.8"})
    assert "Daarna is er geen RGS-lijst meer" in r.text and "Geen administratie heeft rekeningen" in r.text
    anna.post("/systeem/rgs/verwijderen", data={"csrf": csrf(anna, page), "versie": "3.8", "action": "confirmed"})
    assert "Er is nog geen RGS-lijst ingelezen." in anna.get("/systeem/rgs").text


def test_delete_version_rights(client, db, company):
    secret = make_user(db, "baas", totp=True, memberships=[(company["id"], "admin", "baas")])
    login(client, "baas", secret)
    page = client.get("/administratie").text
    r = client.post("/systeem/rgs/verwijderen", data={"csrf": csrf(client, page), "versie": "3.8"},
                    follow_redirects=False)
    assert r.status_code in (303, 403)
