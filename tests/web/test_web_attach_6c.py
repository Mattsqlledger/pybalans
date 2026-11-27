"""Rapporten > Bijlagen (fase 4 stap 6c, RB3): overzicht van alle bijlagen die je mag zien, zoeken, link naar het
record, losse archiefstukken uit SQL-Ledger (bekijken; weggooien alleen door de beheerder)."""

import re
from datetime import date

import psycopg
import pytest
from psycopg.rows import dict_row

from sqlledger.attachments import encode
from test_web_attach import PDF, add, archive_ids, new_vendor_booking
from webhelpers import csrf, login, make_user


@pytest.fixture
def cdb(settings, company_db):
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True, row_factory=dict_row) as conn:
        conn.execute("UPDATE tax SET validto = '2025-12-31' WHERE taxnumber IS NULL")
        yield conn


@pytest.fixture
def booker(client, db, company):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    return client


def loose(cdb, name="los.pdf", data=PDF + b"los"):
    aid = cdb.execute("INSERT INTO archive (filename) VALUES (%s) RETURNING id", (name,)).fetchone()["id"]
    for rn, bt in enumerate(encode(data), 1):
        cdb.execute("INSERT INTO archivedata (rn, archive_id, bt) VALUES (%s, %s, %s)", (rn, aid, bt))
    return aid


def rows(html):
    body = html.split('<table class="list attoverview">', 1)[1].split("<tbody>", 1)[1].split("</tbody>", 1)[0] \
        if "attoverview" in html else ""
    return re.findall(r"<tr>(.*?)</tr>", body, re.S)


def test_overview_lists_links_and_filters(booker, cdb):
    tid = new_vendor_booking(booker)
    add(booker, tid, f"/inkoop/{tid}", "ap_transaction", name="factuur.pdf", description="Factuur B-1")
    add(booker, 902, "/klanten/902", "customer", content=PDF + b"c", name="contract.pdf")
    aid = loose(cdb)
    page = booker.get("/bijlagen").text
    assert 'href="/bijlagen"' in page and 'aria-current="page">Bijlagen<' in page      # menu Rapporten
    assert len(rows(page)) == 3 and "3 gevonden" in page
    assert f'href="/inkoop/{tid}"' in page and "Inkoopboeking B-1" in page and "Factuur B-1" in page
    assert 'href="/klanten/902"' in page and "contract.pdf" in page
    assert "Los document (zonder koppeling)" in page and f'href="/bijlage/{aid}"' in page
    assert date.today().strftime("%Y") in rows(page)[0]                                  # toegevoegd: datum
    # zoeken
    assert len(rows(booker.get("/bijlagen", params={"text": "contract"}).text)) == 1
    assert len(rows(booker.get("/bijlagen", params={"text": "Factuur B"}).text)) == 1       # omschrijving
    assert len(rows(booker.get("/bijlagen", params={"kind": "inkoop"}).text)) == 1
    assert len(rows(booker.get("/bijlagen", params={"kind": "los"}).text)) == 1
    assert len(rows(booker.get("/bijlagen", params={"linked": "B-1"}).text)) == 1
    p = booker.get("/bijlagen", params={"datefrom": "2000-01-01", "dateto": "2000-12-31"}).text
    assert "Geen bijlagen." in p
    # F43: het losse stuk heeft geen datum; dat wordt gemeld, met "Tonen"
    assert "Bijlagen zonder datum (uit SQL-Ledger) niet getoond: 1." in p
    link = '/bijlagen?datefrom=2000-01-01&amp;dateto=2000-12-31&amp;zonderdatum=1'
    assert f'href="{link}"' in p
    p = booker.get(link.replace("&amp;", "&")).text
    assert len(rows(p)) == 1 and f'href="/bijlage/{aid}"' in p and "niet getoond" not in p
    assert 'name="zonderdatum" value="1"' in p and 'href="/bijlagen?datefrom=2000-01-01&amp;dateto=2000-12-31">Verbergen' in p
    assert "niet getoond" not in booker.get("/bijlagen").text                          # zonder datumfilter niets
    # los document openen kan
    assert booker.get(f"/bijlage/{aid}").content == PDF + b"los"
    # alleen de beheerder gooit een los document weg
    r = booker.post(f"/bijlagen/los/{aid}/verwijderen", data={"csrf": csrf(booker, page)}, follow_redirects=False)
    assert r.status_code == 403
    assert "/verwijderen" not in page


def test_admin_deletes_loose_only(booker, client, db, company, cdb):
    tid = new_vendor_booking(booker)
    add(booker, tid, f"/inkoop/{tid}", "ap_transaction")
    (linked,) = archive_ids(cdb, tid)
    aid = loose(cdb)
    booker.cookies.clear()
    secret = make_user(db, "baas", memberships=[(company["id"], "admin", "baas")], totp=True)
    login(client, "baas", secret)
    page = client.get("/bijlagen").text
    assert f'action="/bijlagen/los/{aid}/verwijderen"' in page
    assert f'action="/bijlagen/los/{linked}/verwijderen"' not in page
    r = client.post(f"/bijlagen/los/{linked}/verwijderen", data={"csrf": csrf(client, page)}, follow_redirects=False)
    assert r.status_code == 404                                       # hangt nog ergens aan
    r = client.post(f"/bijlagen/los/{aid}/verwijderen", data={"csrf": csrf(client, page)}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/bijlagen?ok=attach_deleted"
    assert cdb.execute("SELECT count(*) AS n FROM archive WHERE id = %s", (aid,)).fetchone()["n"] == 0


def test_confidential_modules_and_reader(booker, client, db, company, cdb):
    tid = new_vendor_booking(booker)
    add(booker, tid, f"/inkoop/{tid}", "ap_transaction", name="geheim.pdf", confidential="1")
    add(booker, 902, "/klanten/902", "customer", content=PDF + b"k", name="klant.pdf")
    page = booker.get("/bijlagen").text
    assert "geheim.pdf" in page and "vertrouwelijk" in page
    booker.cookies.clear()
    make_user(db, "kijk", memberships=[(company["id"], "readonly", "kijk")])
    login(client, "kijk")
    page = client.get("/bijlagen").text
    assert "geheim.pdf" not in page and "klant.pdf" in page and "/verwijderen" not in page
    # module Inkoop uit: geen inkoopbijlagen, soort niet in de keuzelijst; Verkoop uit: geen klantbijlagen
    db.execute("UPDATE company SET modules = array_remove(modules, 'ar') WHERE id = %s", (company["id"],))
    page = client.get("/bijlagen").text
    assert "klant.pdf" not in page and 'value="klanten"' not in page
    db.execute("UPDATE company SET modules = array_remove(modules, 'reports') WHERE id = %s", (company["id"],))
    assert client.get("/bijlagen").status_code == 403


def test_sl_archive_without_added_date(booker, cdb):
    """Een bijlage uit SQL-Ledger (geen x_archive_added): datum leeg, verder gewoon in de lijst."""
    tid = new_vendor_booking(booker)
    aid = loose(cdb, "uit-sl.pdf")
    cdb.execute("""INSERT INTO reference (trans_id, description, archive_id, login, formname)
                   VALUES (%s, 'van SL', %s, '', 'ap_transaction')""", (tid, aid))
    page = booker.get("/bijlagen").text
    (row,) = rows(page)
    assert "uit-sl.pdf" in row and "van SL" in row and re.search(r'<td class="nowrap">\s*</td>', row)
