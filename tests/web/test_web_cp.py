"""Ontvangsten en betalingen in de webapp (stap 6): één relatie, meerdere relaties, corrigeren."""

import re
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import psycopg
import pytest
from psycopg.rows import dict_row

from test_web_arap import page_fields
from webhelpers import csrf, login, make_user


@pytest.fixture
def cdb(settings, company_db):
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True, row_factory=dict_row) as conn:
        conn.execute("UPDATE ar SET exchangerate = 1 WHERE exchangerate IS NULL")
        conn.execute("UPDATE ap SET exchangerate = 1 WHERE exchangerate IS NULL")
        yield conn


@pytest.fixture
def booker(client, db, company, cdb):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    return client


def step(client, url, data, page):
    return client.post(url, data={**page_fields(page), "csrf": csrf(client, page), **data}, follow_redirects=False)


def ok(r):
    assert r.status_code == 200, re.sub(r"\s+", " ", r.text)[:3000]
    return r.text


def start(client, vc_text, prefix="ontvangst", **extra):
    page = client.get(f"/{prefix}/nieuw").text
    return ok(step(client, f"/{prefix}", {"vc_text": vc_text, "datepaid": "2026-07-01", "action": "update",
                                          **extra}, page))


def paid(cdb, table, tid):
    return Decimal(str(cdb.execute(f"SELECT paid FROM {table} WHERE id = %s", (tid,)).fetchone()["paid"]))


def balance(cdb, tid):
    return Decimal(str(cdb.execute("SELECT coalesce(round(sum(amount)::numeric, 6), 0) AS s FROM acc_trans "
                                   "WHERE trans_id = %s", (tid,)).fetchone()["s"]))


# --- één relatie --------------------------------------------------------------------

def test_menu_and_rights(booker, client, db, company):
    page = booker.get("/ontvangst/nieuw").text
    assert "<h2>Ontvangst</h2>" in page and 'name="vc_text" value=""' in page     # geen voorselectie (P3)
    assert 'href="/ontvangsten"' in page and 'href="/betalingen/corrigeren"' in page
    assert 'option value="1100" selected' in page
    assert "Betaling" in booker.get("/betaling/nieuw").text
    db.execute("UPDATE company SET modules = array_remove(modules, 'cash') WHERE id = %s", (company["id"],))
    assert booker.get("/ontvangst/nieuw").status_code == 403


def test_readonly_forbidden(client, db, company, cdb):
    make_user(db, "kijk", memberships=[(company["id"], "readonly", "jan")])
    login(client, "kijk")
    assert client.get("/ontvangsten").status_code == 403
    assert 'href="/ontvangst/nieuw"' not in client.get("/rapporten/ontvangsten").text


def test_choose_customer_and_pay_two_invoices(booker, cdb, db):
    page = start(booker, "K001")
    assert 'value="K001 Klant A"' in page and "F-3006" in page and "F-3010" not in page   # niet goedgekeurd weg
    r = step(booker, "/ontvangst", {"checked_3001": "1", "checked_3006": "1", "action": "calc"}, page)
    calc = ok(r)
    assert 'name="paid_3001" value="121,00"' in calc and 'name="paid_3006" value="263,00"' in calc
    assert 'name="amount" value="384,00"' in calc
    r = step(booker, "/ontvangst", {"checked_3001": "1", "checked_3006": "1", "source": "R-77",
                                    "memo": "bank", "action": "post"}, calc)
    assert r.status_code == 303, r.text[:3000]
    loc = urlparse(r.headers["location"])
    q = parse_qs(loc.query)
    assert loc.path == "/ontvangst/nieuw" and q["geboekt"] == ["R-77"] and q["rekening"] == ["1100"]
    assert paid(cdb, "ar", 3001) == 121 and paid(cdb, "ar", 3006) == 363
    assert balance(cdb, 3001) == 0 and balance(cdb, 3006) == 0
    after = booker.get(r.headers["location"]).text
    assert "Ontvangst R-77 geboekt" in after and 'href="/boekingen/3001"' in after
    assert 'name="datepaid" value="2026-07-01"' in after and 'name="source" value="R-78"' in after   # P11
    log = db.execute("SELECT detail FROM audit_log WHERE action = 'payment_posted'").fetchone()
    assert log["detail"]["source"] == "R-77" and sorted(log["detail"]["trans_ids"]) == [3001, 3006]


def test_partial_and_edited_amount_kept(booker, cdb):
    page = start(booker, "Klant A")
    calc = ok(step(booker, "/ontvangst", {"checked_3006": "1", "action": "calc"}, page))
    calc = ok(step(booker, "/ontvangst", {"paid_3006": "50,00", "action": "calc"}, calc))
    assert 'name="paid_3006" value="50,00"' in calc and 'name="amount" value="50,00"' in calc
    r = step(booker, "/ontvangst", {"action": "post"}, calc)
    assert r.status_code == 303
    assert paid(cdb, "ar", 3006) == 150


def test_prepayment_needs_confirmation(booker, cdb):
    page = start(booker, "K001")
    calc = ok(step(booker, "/ontvangst", {"checked_3009": "1", "action": "calc"}, page))
    calc = ok(step(booker, "/ontvangst", {"amount": "25,00", "action": "calc"}, calc))
    assert "Wordt vooruitontvangst: 15,00" in calc
    r = step(booker, "/ontvangst", {"action": "post"}, calc)
    assert r.status_code == 200 and "is niet aan een post toegewezen" in r.text            # P6
    r = step(booker, "/ontvangst", {"action": "post_confirmed"}, r.text)
    assert r.status_code == 303
    dep = cdb.execute("SELECT id, amount, paid FROM ar WHERE customer_id = 900 AND amount = 0").fetchone()
    assert dep and Decimal(str(dep["paid"])) == 15 and balance(cdb, dep["id"]) == 0


def test_total_lower_than_lines_refused(booker):
    page = start(booker, "K001")
    calc = ok(step(booker, "/ontvangst", {"checked_3001": "1", "action": "calc"}, page))
    r = step(booker, "/ontvangst", {"amount": "100,00", "action": "post"}, calc)
    assert r.status_code == 400 and "lager dan de som" in r.text


def test_more_than_due_refused(booker, cdb):
    page = start(booker, "K001")
    calc = ok(step(booker, "/ontvangst", {"checked_3009": "1", "action": "calc"}, page))
    r = step(booker, "/ontvangst", {"paid_3009": "12,00", "amount": "", "action": "post"}, calc)
    assert r.status_code == 400 and "hoger dan wat openstaat" in r.text                    # P7
    assert paid(cdb, "ar", 3009) == 0


def test_changed_meanwhile(booker, cdb):
    page = start(booker, "K001")
    calc = ok(step(booker, "/ontvangst", {"checked_3006": "1", "action": "calc"}, page))
    cdb.execute("UPDATE ar SET paid = 200 WHERE id = 3006")              # iemand anders boekte intussen
    r = step(booker, "/ontvangst", {"action": "post"}, calc)
    assert r.status_code == 400 and "intussen gewijzigd" in r.text                         # P8
    assert 'name="paid_3006" value="163,00"' in r.text
    assert cdb.execute("SELECT count(*) AS n FROM acc_trans WHERE trans_id = 3006 AND source IS NOT NULL "
                       "AND transdate = '2026-07-01'").fetchone()["n"] == 0


def test_locked_by_other_user_hidden(booker, cdb):
    cdb.execute("INSERT INTO semaphore (id, login, module, expires) VALUES (3001, 'ander', 'ar', '9999999999')")
    page = start(booker, "K001")
    assert "F-3006" in page and "F-3001" not in page


def test_all_vc_for_prepayment(booker, cdb):
    page = booker.get("/ontvangst/nieuw").text
    r = step(booker, "/ontvangst", {"vc_text": "Kassaklant", "action": "update"}, page)
    assert "Geen klant met openstaande posten" in r.text
    r = step(booker, "/ontvangst", {"vc_text": "Kassaklant", "all_vc": "1", "datepaid": "2026-07-01",
                                    "action": "update"}, page)
    page = ok(r)
    assert 'value="K004 Kassaklant"' in page and "Kies eerst een relatie" not in page
    r = step(booker, "/ontvangst", {"amount": "40,00", "action": "post"}, page)
    r = step(booker, "/ontvangst", {"action": "post_confirmed"}, r.text)
    assert r.status_code == 303
    assert cdb.execute("SELECT paid FROM ar WHERE customer_id = 904 AND amount = 0").fetchone()["paid"] == 40


def test_vc_options(booker):
    r = booker.get("/ontvangst/relaties", params={"vc_text": "k00"})
    assert "K001 Klant A" in r.text and "Kassaklant" not in r.text
    r = booker.get("/ontvangst/relaties", params={"vc_text": "kassa", "all_vc": "1"})
    assert "K004 Kassaklant" in r.text


def test_vendor_payment(booker, cdb):
    page = start(booker, "Molen", prefix="betaling")
    calc = ok(step(booker, "/betaling", {"checked_4002": "1", "action": "calc"}, page))
    assert 'name="paid_4002" value="305,00"' in calc
    r = step(booker, "/betaling", {"source": "1001", "action": "post"}, calc)
    assert r.status_code == 303 and paid(cdb, "ap", 4002) == 605


def test_filters_reset_selection(booker):
    page = start(booker, "K001")
    calc = ok(step(booker, "/ontvangst", {"checked_3001": "1", "action": "calc"}, page))
    r = ok(step(booker, "/ontvangst", {"duedatefrom": "2026-06-01", "action": "update"}, calc))
    assert 'name="checked_3001" value="1" checked' not in r and "F-3006" not in r


# --- meerdere relaties -------------------------------------------------------------

def test_many_screen_allocates_oldest_first(booker, cdb):
    page = ok(booker.get("/ontvangsten?datum=2026-07-01"))
    assert "Klant A" in page and "Bakkerij Cees" in page
    calc = ok(step(booker, "/ontvangsten", {"vcchecked_902": "1", "action": "calc"}, page))
    assert 'name="vcpaid_902" value="384,70"' in calc
    calc = ok(step(booker, "/ontvangsten", {"vcpaid_902": "100,00", "vcsource_902": "B-1", "action": "calc"}, calc))
    r = step(booker, "/ontvangsten", {"action": "post"}, calc)
    assert r.status_code == 303, r.text[:3000]
    assert paid(cdb, "ar", 3007) == Decimal("65.4")          # oudste eerst
    assert paid(cdb, "ar", 3004) == Decimal("34.6")
    assert paid(cdb, "ar", 3002) == 50
    assert "Betalingen geboekt" in booker.get(r.headers["location"]).text


def test_many_screen_sources_increment(booker, cdb):
    cdb.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('receipt_1100', 'R-10')")
    page = ok(booker.get("/ontvangsten?datum=2026-07-01"))
    calc = ok(step(booker, "/ontvangsten", {"allbox": "1", "action": "calc"}, page))
    sources = re.findall(r'name="vcsource_\d+" value="([^"]*)"', calc)
    assert sources[:2] == ["R-10", "R-11"]
    r = step(booker, "/ontvangsten", {"action": "post"}, calc)
    assert r.status_code == 303, r.text[:3000]
    assert cdb.execute("SELECT count(*) AS n FROM ar WHERE amount <> paid AND approved").fetchone()["n"] == 1  # USD blijft
    assert cdb.execute("SELECT count(*) AS n FROM ar WHERE amount = 0").fetchone()["n"] == 0             # geen vooruitbet.


def test_many_screen_all_or_nothing(booker, cdb):
    page = ok(booker.get("/ontvangsten?datum=2026-07-01"))
    calc = ok(step(booker, "/ontvangsten", {"allbox": "1", "action": "calc"}, page))
    cdb.execute("UPDATE ar SET paid = 10 WHERE id = 3009")
    r = step(booker, "/ontvangsten", {"action": "post"}, calc)
    assert r.status_code == 400 and "intussen gewijzigd" in r.text
    assert paid(cdb, "ar", 3007) == 0


# --- corrigeren ------------------------------------------------------------------------

def _pay(booker, source="R-90"):
    page = start(booker, "K001")
    calc = ok(step(booker, "/ontvangst", {"checked_3001": "1", "action": "calc"}, page))
    r = step(booker, "/ontvangst", {"source": source, "action": "post"}, calc)
    assert r.status_code == 303


def test_register_void(booker, cdb, db):
    _pay(booker)
    page = ok(booker.get("/ontvangsten/corrigeren?weergave=exclude&rekening=1100"))
    key = re.search(r'name="sel" value="(1100\|R-90\|2026-07-01\|900)"', page).group(1)
    r = step(booker, "/ontvangsten/corrigeren", {"sel": key, "action": "void"}, page)
    assert ok(r) and "Ja, storneren" in r.text
    r = step(booker, "/ontvangsten/corrigeren", {"sel": key, "voiddate": "2026-07-02",
                                                 "action": "void_confirmed"}, r.text)
    assert r.status_code == 303, r.text[:3000]
    assert paid(cdb, "ar", 3001) == 0 and balance(cdb, 3001) == 0
    page = ok(booker.get("/ontvangsten/corrigeren?weergave=all&rekening=1100"))
    assert "gestorneerd" in page and 'value="1100|R-90|' not in page                      # niet nog eens
    assert db.execute("SELECT count(*) AS n FROM audit_log WHERE action = 'payment_voided'").fetchone()["n"] == 1


def test_register_reissue(booker, cdb):
    _pay(booker)
    page = ok(booker.get("/ontvangsten/corrigeren?weergave=exclude"))
    key = re.search(r'name="sel" value="([^"]*R-90[^"]*)"', page).group(1)
    r = step(booker, "/ontvangsten/corrigeren", {"sel": key, "action": "reissue"}, page)
    assert 'name="new_source" value="R-91"' in r.text
    r = step(booker, "/ontvangsten/corrigeren", {"sel": key, "new_source": "R-95", "new_date": "2026-07-03",
                                                 "action": "reissue_confirmed"}, r.text)
    assert r.status_code == 303, r.text[:3000]
    rows = cdb.execute("SELECT source, transdate FROM acc_trans WHERE trans_id = 3001 AND source IS NOT NULL").fetchall()
    assert {(x["source"], str(x["transdate"])) for x in rows} == {("R-95", "2026-07-03")}
    assert paid(cdb, "ar", 3001) == 121


def test_register_nothing_selected(booker):
    _pay(booker)
    page = ok(booker.get("/ontvangsten/corrigeren?weergave=exclude"))
    r = step(booker, "/ontvangsten/corrigeren", {"action": "void"}, page)
    assert r.status_code == 400 and "Selecteer eerst" in r.text
