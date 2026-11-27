"""Bankafschrift inlezen en bankregels verwerken in de webapp (fase 2, I1–I10)."""

import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from webhelpers import csrf, login, make_user, nohelp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bankfiles import Entry, camt, make_iban, nl_reference  # noqa: E402

D = Decimal
OWN = make_iban("NL", "INGB0001234567")
OTHER = make_iban("NL", "RABO0300000001")
KLANT_A = make_iban("NL", "ABNA0417164300")
CEES = make_iban("NL", "RABO0123456789")
KPN = make_iban("NL", "ABNA0600000001")
REF_3002 = nl_reference("000000000003002")
D1 = date(2026, 7, 1)


@pytest.fixture
def cdb(settings, company_db):
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True, row_factory=dict_row) as conn:
        conn.execute("UPDATE ar SET exchangerate = 1 WHERE exchangerate IS NULL")
        conn.execute("UPDATE ap SET exchangerate = 1 WHERE exchangerate IS NULL")
        conn.execute("UPDATE tax SET validto = '2012-09-30' WHERE rate IN (0.19, 0.06)")
        conn.execute("UPDATE chart SET link = 'AR_paid:AP_paid' WHERE accno = '1140'")
        conn.execute("INSERT INTO bank (id, iban) VALUES (10044, %s)", (OWN,))
        for vid, iban in ((900, KLANT_A), (902, CEES)):
            conn.execute("INSERT INTO bank (id, iban) VALUES (%s, %s)", (vid, iban))
        conn.execute("UPDATE ar SET dcn = %s WHERE id = 3002", (REF_3002,))
        yield conn


@pytest.fixture
def booker(client, db, company, cdb):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    return client


def ok(r, status=200):
    assert r.status_code == status, re.sub(r"\s+", " ", r.text)[:3000]
    return nohelp(r.text)


def upload(client, data, name="afschrift.xml", rekening="", status=200):
    page = client.get("/bank/inlezen").text
    r = client.post("/bank/inlezen", data={"csrf": csrf(client, page), "rekening": rekening},
                    files={"bestand": (name, data, "application/octet-stream")})
    return ok(r, status)


def post(client, url, data, page_url=None):
    page = client.get(page_url or url).text
    return client.post(url, data={"csrf": csrf(client, page), **data}, follow_redirects=False)


def statement(*entries, opening="0", **kw):
    return camt(OWN, list(entries), D(opening), **kw)


def one(cdb, sql, *params):
    return cdb.execute(sql, params).fetchone()


def lines_status(cdb):
    return {r["amount"]: r["status"] for r in cdb.execute("SELECT amount, status FROM x_bank_line")}


STANDARD = (Entry(D("222.50"), D1, "Bakkerij Cees", CEES, reference=REF_3002, bank_ref="B1"),
            Entry(D("263.00"), D1, "Klant A", KLANT_A, "Betaling F-3006", bank_ref="B2"),
            Entry(D("-121.00"), D1, "KPN BV", KPN, "Abonnement", bank_ref="B3"),
            Entry(D("-9.99"), D1, "Onbekend", None, "iets", bank_ref="B4"))


def test_menu_and_rights(booker, client, db, company):
    page = ok(booker.get("/bank/inlezen"))
    assert 'href="/bank/regels"' in page and 'href="/bank/vaste-regels"' in page and 'name="bestand"' in page
    assert ok(booker.get("/bank/regels"))
    db.execute("UPDATE company SET modules = array_remove(modules, 'cash') WHERE id = %s", (company["id"],))
    assert booker.get("/bank/inlezen").status_code == 403


def test_readonly_forbidden(client, db, company, cdb):
    make_user(db, "kijk", memberships=[(company["id"], "readonly", "jan")])
    login(client, "kijk")
    assert client.get("/bank/regels").status_code == 403
    assert 'href="/bank/inlezen"' not in client.get("/").text


def test_upload_result_and_duplicates(booker, cdb):
    page = upload(booker, statement(*STANDARD))
    assert "nieuw 4" in page and "saldocontrole klopt" in page and 'href="/bank/regels?rekening=1100"' in page
    page = upload(booker, statement(*STANDARD))
    assert "al helemaal ingelezen" in page and "overgeslagen 4" in page
    assert one(cdb, "SELECT count(*) AS n FROM x_bank_line")["n"] == 4
    st = ok(booker.get("/bank/afschriften"))
    assert "afschrift.xml" in st
    sid = one(cdb, "SELECT id FROM x_bank_statement")["id"]
    csv = booker.get(f"/bank/afschriften/{sid}?export=csv")
    assert csv.status_code == 200 and "Bakkerij Cees" in csv.text and "222,50" in csv.text
    assert booker.get(f"/bank/afschriften/{sid}?export=xlsx").status_code == 200
    pdf = booker.get(f"/bank/afschriften/{sid}?export=pdf")              # stap 5 (RP1)
    from test_web_reportpdf import pdf_text
    assert pdf.headers["content-type"] == "application/pdf"
    assert "Bakkerij Cees" in pdf_text(pdf.content) and "222,50" in pdf_text(pdf.content)
    assert "export=pdf" in booker.get(f"/bank/afschriften/{sid}").text


def test_upload_errors(booker, cdb, monkeypatch):
    page = upload(booker, statement(Entry(D("5"), D1, description="x"), closing=D("6")), status=400)
    assert "Saldocontrole" in page
    page = upload(booker, b"geen afschrift", status=400)
    assert "Onbekend formaat" in page
    page = upload(booker, statement(Entry(D("5"), D1, description="x"), ccy="USD"), status=400)
    assert "is in USD" in page
    from sqlledger_web import routes_bank
    monkeypatch.setattr(routes_bank, "MAX_UPLOAD", 100)
    page = upload(booker, statement(Entry(D("5"), D1, description="x")), status=413)
    assert "te groot" in page


def test_unknown_iban_choose_account(booker, cdb):
    data = camt(OTHER, [Entry(D("1.00"), D1, description="rente")], D("0"))
    page = upload(booker, data, status=400)
    assert "kies de rekening" in page.lower() and 'name="rekening" required' in page
    page = upload(booker, data, rekening="1140")
    assert "rekeningnummer opgeslagen" in page
    assert one(cdb, "SELECT iban FROM bank WHERE id = 10051")["iban"] == OTHER


def test_lines_accept_and_cleared(booker, cdb):
    upload(booker, statement(*STANDARD))
    page = ok(booker.get("/bank/regels?rekening=1100"))
    assert "Alle zekere voorstellen verwerken (2)" in page
    lid = one(cdb, "SELECT id FROM x_bank_line WHERE amount = 222.50")["id"]
    sig = re.search(rf'name="sig_{lid}" value="([^"]+)"', page).group(1)
    # gewijzigde handtekening → niet geboekt
    r = post(booker, "/bank/regels", {"rekening": "1100", "action": f"accept:{lid}", f"sig_{lid}": "x"},
             "/bank/regels?rekening=1100")
    assert r.status_code == 409 and lines_status(cdb)[D("222.50")] == "open"
    r = post(booker, "/bank/regels", {"rekening": "1100", "action": f"accept:{lid}", f"sig_{lid}": sig},
             "/bank/regels?rekening=1100")
    assert r.status_code == 303 and "ok=line" in r.headers["location"]
    assert D(str(one(cdb, "SELECT paid FROM ar WHERE id = 3002")["paid"])) == D("272.50")
    row = one(cdb, """SELECT cleared, source FROM acc_trans WHERE trans_id = 3002 AND chart_id = 10044
                      AND transdate = %s""", D1)
    assert row["cleared"] == D1 and row["source"] == "B1"
    assert "Bankregel verwerkt." in ok(booker.get(r.headers["location"]))


def test_bulk_sure(booker, cdb):
    upload(booker, statement(*STANDARD))
    r = post(booker, "/bank/regels", {"rekening": "1100", "action": "bulk"}, "/bank/regels?rekening=1100")
    page = ok(r)
    assert "Ja, verwerken" in page
    bulk = re.findall(r'name="bulk" value="([^"]+)"', page)
    assert len(bulk) == 2
    r = booker.post("/bank/regels", data={"csrf": csrf(booker, page), "rekening": "1100",
                                          "action": "bulk_confirmed", "bulk": bulk}, follow_redirects=False)
    assert r.status_code == 303 and "aantal=2" in r.headers["location"]
    st = lines_status(cdb)
    assert st[D("222.50")] == st[D("263.00")] == "booked" and st[D("-121.00")] == "open"
    assert "2 zekere voorstel(len) verwerkt." in ok(booker.get(r.headers["location"]))
    # nog een keer bevestigen (dubbele klik): niets dubbel
    r = booker.post("/bank/regels", data={"csrf": csrf(booker, page), "rekening": "1100",
                                          "action": "bulk_confirmed", "bulk": bulk}, follow_redirects=False)
    assert r.status_code == 303 and "aantal=0" in r.headers["location"]
    assert one(cdb, "SELECT count(*) AS n FROM acc_trans WHERE trans_id = 3006 AND source = 'B2'")["n"] == 1


def test_gl_from_list_with_rule(booker, cdb):
    upload(booker, statement(*STANDARD))
    lid = one(cdb, "SELECT id FROM x_bank_line WHERE amount = -121")["id"]
    s = f"_{lid}"
    r = post(booker, "/bank/regels", {"rekening": "1100", "action": f"gl:{lid}", "gl_accno" + s: "4900 Telefoon",
                                      "gl_tax" + s: "1800", "gl_desc" + s: "Telefoon", "gl_rule" + s: "1"},
             "/bank/regels?rekening=1100")
    assert r.status_code == 303
    gl_id = one(cdb, "SELECT trans_id FROM x_bank_line WHERE id = %s", lid)["trans_id"]
    rows = cdb.execute("""SELECT c.accno, ac.amount, ac.cleared FROM acc_trans ac JOIN chart c ON c.id = ac.chart_id
                          WHERE ac.trans_id = %s ORDER BY c.accno""", (gl_id,)).fetchall()
    assert [(r["accno"], r["amount"], r["cleared"]) for r in rows] == \
        [("1100", 121, D1), ("1800", -21, None), ("4900", -100, None)]
    rule = one(cdb, "SELECT * FROM x_bank_rule")
    assert rule["iban"] == KPN and rule["accno"] == "4900" and rule["direction"] == "-"
    assert one(cdb, "SELECT rule_id FROM x_bank_line WHERE id = %s", lid)["rule_id"] == rule["id"]
    # fout: geen tegenrekening
    lid2 = one(cdb, "SELECT id FROM x_bank_line WHERE amount = -9.99")["id"]
    r = post(booker, "/bank/regels", {"rekening": "1100", "action": f"gl:{lid2}"}, "/bank/regels?rekening=1100")
    assert r.status_code == 400 and "Kies de tegenrekening" in r.text


def test_line_page_pay_partial_prepayment(booker, cdb):
    upload(booker, statement(Entry(D("100.00"), D1, "Cees", CEES, "diverse", bank_ref="P1")))
    lid = one(cdb, "SELECT id FROM x_bank_line")["id"]
    page = ok(booker.get(f"/bank/regel/{lid}"))
    assert "rekeningnummer relatie" in page and 'name="vc_text" value="K002 Bakkerij Cees"' in page
    # handmatig: alleen F-3007 (65,40) en de rest als vooruitbetaling
    form = {"vc": "customer", "vc_old": "customer", "vc_text": "K002 Bakkerij Cees", "vc_shown": "K002 Bakkerij Cees",
            "vc_id": "902", "due_3007": "65,40", "inv_3007": "1", "paid_3007": "65,40", "due_3004": "96,80",
            "due_3002": "222,50"}
    r = post(booker, f"/bank/regel/{lid}", dict(form, action="pay"))
    assert r.status_code == 400 and "sluit niet" in r.text
    r = post(booker, f"/bank/regel/{lid}", dict(form, action="pay", prepay="1"))
    assert r.status_code == 303
    assert D(str(one(cdb, "SELECT paid FROM ar WHERE id = 3007")["paid"])) == D("65.40")
    ids = one(cdb, "SELECT trans_ids FROM x_bank_line WHERE id = %s", lid)["trans_ids"]
    assert ids[0] == 3007 and len(ids) == 2
    assert one(cdb, "SELECT count(*) AS n FROM acc_trans WHERE chart_id = 10044 AND source = 'P1' "
                    "AND cleared = %s", D1)["n"] == 2
    page = ok(booker.get(f"/bank/regel/{lid}"))
    assert "geboekt" in page and 'href="/boekingen/3007"' in page and 'value="pay"' not in page


def test_line_page_search_relation(booker, cdb):
    upload(booker, statement(Entry(D("10.00"), D1, "X", None, "iets", bank_ref="S1")))
    lid = one(cdb, "SELECT id FROM x_bank_line")["id"]
    r = post(booker, f"/bank/regel/{lid}", {"vc": "customer", "vc_old": "customer", "vc_text": "Klant A",
                                            "action": "update"})
    page = ok(r)
    assert 'name="vc_id" value="900"' in page and "F-3009" in page


def test_line_page_match_ignore_reopen(booker, cdb):
    cdb.execute("""INSERT INTO gl (id, reference, transdate, description, approved) VALUES (77001, 'M-1', '2026-07-03',
                   'Storting', true)""")
    cdb.execute("""INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, approved) VALUES
                   (77001, 10044, -80, '2026-07-03', true), (77001, 10042, 80, '2026-07-03', true)""")
    upload(booker, statement(Entry(D("80.00"), D1, None, None, "storting", bank_ref="M1"),
                             Entry(D("1.00"), D1, None, None, "rente", bank_ref="M2")))
    l80 = one(cdb, "SELECT id FROM x_bank_line WHERE amount = 80")["id"]
    page = ok(booker.get(f"/bank/regel/{l80}"))
    assert "bestaande boeking" in page and 'value="77001|2026-07-03|"' in page
    r = post(booker, f"/bank/regel/{l80}", {"action": "match", "match": "77001|2026-07-03|"})
    assert r.status_code == 303
    assert one(cdb, "SELECT cleared FROM acc_trans WHERE trans_id = 77001 AND chart_id = 10044")["cleared"] == D1
    assert lines_status(cdb)[D("80")] == "matched"
    l1 = one(cdb, "SELECT id FROM x_bank_line WHERE amount = 1")["id"]
    r = post(booker, f"/bank/regel/{l1}", {"action": "ignore", "note": "rente later"})
    assert r.status_code == 303 and lines_status(cdb)[D("1")] == "ignored"
    r = post(booker, f"/bank/regel/{l1}", {"action": "reopen"})
    assert r.status_code == 303 and lines_status(cdb)[D("1")] == "open"


def test_rules_page(booker, cdb):
    r = post(booker, "/bank/vaste-regels", {"action": "add", "keyword": "bankkosten", "direction": "-",
                                            "accno": "4980 Bankkosten"})
    assert r.status_code == 303
    page = ok(booker.get("/bank/vaste-regels"))
    assert "bankkosten" in page and "4980" in page
    rid = one(cdb, "SELECT id FROM x_bank_rule")["id"]
    r = post(booker, "/bank/vaste-regels", {"action": "add", "accno": "4980"})
    assert r.status_code == 400 and "rekeningnummer of een trefwoord" in r.text
    upload(booker, statement(Entry(D("-2.50"), D1, "ING", None, "Bankkosten juli", bank_ref="K1")))
    page = ok(booker.get("/bank/regels?rekening=1100"))
    assert "vaste regel" in page and "4980" in page
    r = post(booker, "/bank/vaste-regels", {"action": f"delete:{rid}"})
    assert r.status_code == 303 and one(cdb, "SELECT count(*) AS n FROM x_bank_rule")["n"] == 0


# --- v1.0.1 PB26 tegenrekening "Eerder gebruikt", PB27 "Voortaan zo boeken?" ---------------------------

def _contra(page):
    dl = page.split('<datalist id="bank-accounts">', 1)[1].split("</datalist>", 1)[0]
    return re.findall(r'data-n="([^"]+)"[^>]*data-group="([^"]+)"', dl)


def test_contra_recent_first_and_rule_hint(booker, cdb):
    upload(booker, statement(*STANDARD))
    lid = one(cdb, "SELECT id FROM x_bank_line WHERE amount = -121")["id"]
    s = f"_{lid}"
    r = post(booker, "/bank/regels", {"rekening": "1100", "action": f"gl:{lid}", "gl_accno" + s: "4900 Telefoon",
                                      "gl_tax" + s: "1800"}, "/bank/regels?rekening=1100")
    assert r.status_code == 303 and f"regel={lid}" in r.headers["location"]
    page = ok(booker.get(r.headers["location"]))
    # PB26: 4900 staat nu bij "Eerder gebruikt", vóór alle rekeningen; geen btw-rekening en niet de bank zelf
    contra = _contra(page)
    recent = [a for a, g in contra if g == "Eerder gebruikt"]
    assert contra[0] == ("4900", "Eerder gebruikt") or "4900" in recent
    assert contra.index(("4900", "Eerder gebruikt")) < next(i for i, (a, g) in enumerate(contra) if g == "Alle rekeningen")
    assert "1800" not in recent and "1100" not in [a for a, g in contra]
    # PB27: voorstel voor een vaste regel, vooringevuld
    assert "Voortaan zo boeken?" in page
    assert re.search(r'name="by" value="iban" checked', page) and KPN in page
    assert re.search(r'name="accno" value="4900 [^"]+"', page) and re.search(r'<option value="1800" selected>', page)
    assert f'value="from_line:{lid}"' in page
    r = post(booker, "/bank/vaste-regels", {"action": f"from_line:{lid}", "rekening": "1100", "by": "iban",
                                            "keyword": "abonnement", "accno": "4900", "tax_accno": "1800"},
             "/bank/regels?rekening=1100")
    assert r.status_code == 303 and "ok=rule" in r.headers["location"] and "aantal=0" in r.headers["location"]
    rule = one(cdb, "SELECT r.*, c.accno AS bank FROM x_bank_rule r JOIN chart c ON c.id = r.chart_id")
    assert (rule["iban"], rule["keyword"], rule["accno"], rule["tax_accno"], rule["direction"], rule["bank"]) == \
        (KPN, None, "4900", "1800", "-", "1100")
    page = ok(booker.get(r.headers["location"]))
    assert "Vaste regel opgeslagen. Geen andere open regel krijgt dit voorstel." in page
    # dezelfde regel nog eens: er is al een regel die hem vindt, dus geen vraag meer
    assert "Voortaan zo boeken?" not in ok(booker.get(f"/bank/regels?rekening=1100&ok=line&regel={lid}"))


def test_rule_hint_keyword_applies_to_open_lines(booker, cdb):
    upload(booker, statement(Entry(D("-5.00"), D1, "Bank", None, "Coupon 4% Staatslening 2037", bank_ref="C1"),
                             Entry(D("-7.00"), D1, "Bank", None, "Coupon 2,5% Lening 2030", bank_ref="C2")))
    lid = one(cdb, "SELECT id FROM x_bank_line WHERE amount = -5")["id"]
    r = post(booker, "/bank/regels", {"rekening": "1100", "action": f"gl:{lid}", f"gl_accno_{lid}": "4900"},
             "/bank/regels?rekening=1100")
    page = ok(booker.get(r.headers["location"]))
    assert re.search(r'name="by" value="keyword" checked', page) and 'name="keyword" value="coupon"' in page
    r = post(booker, "/bank/vaste-regels", {"action": f"from_line:{lid}", "rekening": "1100", "by": "keyword",
                                            "keyword": "coupon", "accno": "4900"}, "/bank/regels?rekening=1100")
    assert "aantal=1" in r.headers["location"]
    page = ok(booker.get(r.headers["location"]))
    assert "1 open regel heeft nu dit voorstel" in page and "Vaste regel" in page
    # alleen een voorstel: de andere regel is nog open
    assert one(cdb, "SELECT status FROM x_bank_line WHERE amount = -7")["status"] == "open"


def test_contra_without_history_results_first(booker, cdb, monkeypatch):
    from sqlledger.bankimport import BankImport
    monkeypatch.setattr(BankImport, "recent_contra_accounts", lambda self, *a, **k: [])
    upload(booker, statement(*STANDARD))
    contra = _contra(ok(booker.get("/bank/regels?rekening=1100")))
    assert contra and all(g == "Alle rekeningen" for a, g in contra)
    cats = {r["accno"]: r["category"] for r in cdb.execute("SELECT accno, category FROM chart")}
    kinds = [cats[a] in ("I", "E") for a, g in contra]
    assert kinds[0] and kinds.index(False) == sum(kinds)          # eerst alle I/E, dan de balansrekeningen


def test_iban_offer_after_payment(booker, cdb):
    cdb.execute("DELETE FROM bank WHERE id = 902")
    upload(booker, statement(Entry(D("65.40"), D1, "Cees", CEES, "diverse", bank_ref="P9")))
    lid = one(cdb, "SELECT id FROM x_bank_line")["id"]
    form = {"vc": "customer", "vc_old": "customer", "vc_text": "K002 Bakkerij Cees", "vc_shown": "K002 Bakkerij Cees",
            "vc_id": "902", "due_3007": "65,40", "inv_3007": "1", "paid_3007": "65,40"}
    r = post(booker, f"/bank/regel/{lid}", dict(form, action="pay"))
    assert r.status_code == 303 and f"regel={lid}" in r.headers["location"]
    page = ok(booker.get(r.headers["location"]))
    assert "Rekeningnummer opslaan?" in page
    assert f"Sla rekeningnummer {CEES} op als standaardrekeningnummer van Bakkerij Cees" in page
    r = post(booker, "/bank/vaste-regels", {"action": f"iban_from_line:{lid}", "rekening": "1100"},
             "/bank/regels?rekening=1100")
    assert r.status_code == 303 and "ok=iban" in r.headers["location"]
    assert one(cdb, "SELECT iban FROM bank WHERE id = 902")["iban"] == CEES
    assert "Rekeningnummer opslaan?" not in ok(booker.get(f"/bank/regels?rekening=1100&ok=line&regel={lid}"))


# --- bankproef 2026-11: voorgestelde vaste regels (BT3), IBAN vervangen (VZ4), samen afletteren (BT4) ----------

def test_suggested_rules_one_click(booker, cdb):
    upload(booker, statement(Entry(D("-60.50"), D1, "KPN BV", KPN, "juli"),
                             Entry(D("-60.50"), date(2026, 7, 2), "KPN BV", KPN, "aug"),
                             Entry(D("-60.50"), date(2026, 7, 3), "KPN BV", KPN, "sep")))
    ids = [r["id"] for r in cdb.execute("SELECT id FROM x_bank_line ORDER BY bookdate")]
    for lid in ids[:2]:
        s = f"_{lid}"
        r = post(booker, "/bank/regels", {"rekening": "1100", "action": f"gl:{lid}", "gl_accno" + s: "4900 Telefoon"},
                 "/bank/regels?rekening=1100")
        assert r.status_code == 303
    page = ok(booker.get("/bank/regels?rekening=1100"))
    assert "Voorgestelde vaste regels (1)" in page and f'value="suggested:{ids[1]}"' in page
    r = post(booker, "/bank/vaste-regels", {"action": f"suggested:{ids[1]}", "rekening": "1100", "iban": KPN,
                                            "direction": "-", "accno": "4900", "tax_accno": ""},
             "/bank/regels?rekening=1100")
    assert r.status_code == 303 and "ok=rule" in r.headers["location"] and "aantal=1" in r.headers["location"]
    page = ok(booker.get(r.headers["location"]))
    assert "Voorgestelde vaste regels" not in page and "e · vaste regel" in page


def test_iban_replace_offer(booker, cdb):
    other = make_iban("NL", "BUNQ2000000002")
    upload(booker, statement(Entry(D("65.40"), D1, "Cees", other, "F-3007")))
    lid = one(cdb, "SELECT id FROM x_bank_line")["id"]
    form = {"vc": "customer", "vc_old": "customer", "vc_text": "K002 Bakkerij Cees", "vc_shown": "K002 Bakkerij Cees",
            "vc_id": "902", "due_3007": "65,40", "inv_3007": "1", "paid_3007": "65,40"}
    r = post(booker, f"/bank/regel/{lid}", dict(form, action="pay"))
    page = ok(booker.get(r.headers["location"]))
    assert "Rekeningnummer vervangen?" in page and CEES in page and other in page
    r = post(booker, "/bank/vaste-regels", {"action": f"iban_from_line:{lid}", "rekening": "1100", "replace": "1"},
             "/bank/regels?rekening=1100")
    assert r.status_code == 303 and one(cdb, "SELECT iban FROM bank WHERE id = 902")["iban"] == other


def test_line_match_multi(booker, cdb):
    for amount, ref in ((-40, "X-1"), (10, "X-2")):
        cdb.execute("""INSERT INTO gl (id, reference, description, transdate, approved) VALUES
                       (nextval('id'), %s, 'Leverancier X', %s, true)""", (ref, D1))
        tid = one(cdb, "SELECT currval('id') AS i")["i"]
        cdb.execute("""INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, approved) VALUES
                       (%s, 10044, %s, %s, true), (%s, 10046, %s, %s, true)""", (tid, -amount, D1, tid, amount, D1))
    upload(booker, statement(Entry(D("-30.00"), D1, "Leverancier X", None, "netto")))
    lid = one(cdb, "SELECT id FROM x_bank_line")["id"]
    page = ok(booker.get(f"/bank/regel/{lid}"))
    assert "Meerdere boekingen samen afletteren" in page and "d · bestaande boeking" in page
    keys = re.findall(r'name="multi" value="([^"]+)"', page)[:2]      # de twee van vandaag staan vooraan
    assert all(k.endswith(f"|{D1}|") for k in keys)
    r = booker.post(f"/bank/regel/{lid}", data={"csrf": csrf(booker, page), "action": "match_multi", "multi": keys},
                    follow_redirects=False)
    assert r.status_code == 303 and lines_status(cdb)[D("-30.00")] == "matched"
    assert one(cdb, "SELECT count(*) AS n FROM acc_trans WHERE chart_id = 10044 AND cleared = %s", D1)["n"] == 2
