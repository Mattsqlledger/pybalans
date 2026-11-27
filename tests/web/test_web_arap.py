"""Verkoop- en inkoopboekingen zonder artikelen in de webapp (stap 5)."""

import re
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import dict_row

from webhelpers import csrf, login, make_user


@pytest.fixture
def cdb(settings, company_db):
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture(autouse=True)
def tax_setup(cdb):
    """Testdata: de oude tarieven (19%/6%) krijgen een einddatum, Klant A alleen btw hoog."""
    cdb.execute("UPDATE tax SET validto = '2025-12-31' WHERE taxnumber IS NULL")
    cdb.execute("DELETE FROM customertax WHERE customer_id = 900 AND chart_id = (SELECT id FROM chart WHERE accno = '1811')")


@pytest.fixture
def booker(client, db, company):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    return client


def lines(*rows):
    """Regelvelden: (bedrag, rekening[, omschrijving])."""
    data = {"rowcount": str(len(rows))}
    for i, r in enumerate(rows, 1):
        data.update({f"amount_{i}": r[0], f"accno_{i}": r[1]})
        if len(r) > 2:
            data[f"description_{i}"] = r[2]
    return data


def pays(*rows):
    """Betaalregels: (datum, bedrag, rekening[, kenmerk])."""
    data = {"paidcount": str(len(rows))}
    for i, r in enumerate(rows, 1):
        data.update({f"datepaid_{i}": r[0], f"paid_{i}": r[1], f"paidaccno_{i}": r[2]})
        if len(r) > 3:
            data[f"source_{i}"] = r[3]
    return data


def page_fields(html):
    """Velden zoals de browser ze zou versturen (hidden + ingevulde invoer + gekozen opties)."""
    data = {}
    for m in re.finditer(r"<input\b[^>]*>", html):
        tag = m.group(0)
        name = re.search(r'name="([^"]+)"', tag)
        if not name or "disabled" in tag:
            continue
        value = re.search(r'value="([^"]*)"', tag)
        if 'type="checkbox"' in tag and " checked" not in tag:
            continue
        key = name.group(1)
        v = value.group(1) if value else ""
        if key == "taxaccount":
            data.setdefault("taxaccount", []).append(v)
        else:
            data[key] = v
    for m in re.finditer(r'<select name="([^"]+)"[^>]*>(.*?)</select>', html, re.S):
        sel = re.search(r'<option value="([^"]*)"[^>]*selected', m.group(2))
        first = re.search(r'<option value="([^"]*)"', m.group(2))
        data[m.group(1)] = sel.group(1) if sel else (first.group(1) if first else "")
    for m in re.finditer(r'<textarea name="([^"]+)"[^>]*>(.*?)</textarea>', html, re.S):
        data[m.group(1)] = m.group(2)
    data.pop("csrf", None)
    return data


def step(client, url, data, page):
    """Formulier op ``page`` versturen met ``data`` eroverheen (zoals een browser)."""
    return client.post(url, data={**page_fields(page), "csrf": csrf(client, page), **data}, follow_redirects=False)


def start(client, vc_text, prefix="verkoop", query=""):
    """Nieuwe boeking openen en de relatie kiezen (verversen zoals het scherm doet)."""
    page = client.get(f"/{prefix}/nieuw{query}").text
    r = step(client, f"/{prefix}", {"vc_text": vc_text, "action": "update"}, page)
    assert r.status_code == 200, r.text[:2000]
    return r.text


def posted_id(r, prefix="verkoop"):
    assert r.status_code == 303, re.sub(r"\s+", " ", r.text)[:3000]
    m = re.fullmatch(rf"/{prefix}/(\d+)\?ok=arap_posted", r.headers["location"])
    assert m, r.headers["location"]
    return int(m.group(1))


def acc(cdb, trans_id):
    return [(r["accno"], r["amount"]) for r in cdb.execute(
        """SELECT c.accno, ac.amount::numeric AS amount FROM acc_trans ac JOIN chart c ON c.id = ac.chart_id
           WHERE trans_id = %s ORDER BY c.accno, ac.amount""", (trans_id,)).fetchall()]


# --- invoeren ---------------------------------------------------------------------

def test_new_form_and_menu(booker):
    page = booker.get("/verkoop/nieuw").text
    assert "Verkoopboeking toevoegen" in page and 'name="vc_text"' in page
    assert '<option value="8520 Opbrengst Verkopen A" data-n="8520" data-name="Opbrengst Verkopen A">' in page            # alleen AR_amount-rekeningen (V5)
    assert "8010" not in page.split('id="accounts"')[1].split("</datalist>")[0]
    assert 'href="/verkoop/nieuw"' in page and 'href="/inkoop/nieuw?creditnota=1"' in page   # menu (V14)
    assert "Creditnota verkoop toevoegen" in booker.get("/verkoop/nieuw?creditnota=1").text
    assert "Inkoopboeking toevoegen" in booker.get("/inkoop/nieuw").text


def test_choose_customer_loads_defaults(booker):
    page = start(booker, "Bakkerij")
    assert 'name="vc_text" value="K002 Bakkerij Cees"' in page
    assert 'name="terms" value="30"' in page                               # termijn relatie
    assert 'name="taxaccount" value="1810"' in page and 'name="taxaccount" value="1811"' in page
    assert "Kredietlimiet" in page
    r = booker.get("/verkoop/relaties", params={"vc_text": "K00", "transdate": "2026-07-01"})
    assert 'value="K001 Klant A"' in r.text and 'value="K002 Bakkerij Cees"' in r.text


def test_unknown_or_ambiguous_customer(booker):
    page = booker.get("/verkoop/nieuw").text
    r = step(booker, "/verkoop", {"vc_text": "Bestaat niet", "action": "update"}, page)
    assert "Geen klant gevonden" in r.text
    r = step(booker, "/verkoop", {"vc_text": "a", "action": "update"}, page)
    assert "Meer dan één klant gevonden" in r.text


def test_post_with_tax(booker, cdb, db):
    page = start(booker, "K001")
    r = step(booker, "/verkoop", {"transdate": "2026-07-01", "action": "calc",
                                  **lines(("100,00", "8520 Opbrengst Verkopen A", "advies"))}, page)
    assert 'id="arap-calc"' in r.text and 'value="21,00"' in r.text            # btw door de server (V4)
    tid = posted_id(step(booker, "/verkoop", {"transdate": "2026-07-01", "action": "post",
                                              **lines(("100,00", "8520 Opbrengst Verkopen A", "advies"))}, page))
    ar = cdb.execute("SELECT * FROM ar WHERE id = %s", (tid,)).fetchone()
    assert ar["amount"] == 121 and ar["netamount"] == 100 and ar["customer_id"] == 900 and not ar["invoice"]
    assert acc(cdb, tid) == [("1200", Decimal(-121)), ("1810", Decimal(21)), ("8520", Decimal(100))]
    page = booker.get(f"/verkoop/{tid}?ok=arap_posted").text
    assert "Boeking opgeslagen." in page and "Verkoopboeking wijzigen" in page
    assert db.execute("SELECT detail FROM audit_log WHERE action = 'arap_posted'").fetchone()["detail"]["id"] == tid
    assert booker.get(f"/boekingen/{tid}", follow_redirects=False).headers["location"] == f"/verkoop/{tid}"


def test_tax_included_and_manual_tax(booker, cdb):
    page = start(booker, "K001")
    tid = posted_id(step(booker, "/verkoop", {"transdate": "2026-07-01", "taxincluded": "1", "action": "post",
                                              **lines(("121,00", "8520"))}, page))
    assert acc(cdb, tid) == [("1200", Decimal(-121)), ("1810", Decimal(21)), ("8520", Decimal(100))]
    page = start(booker, "K001")
    data = {"transdate": "2026-07-01", "action": "post", "tax_1810": "20,00", **lines(("100,00", "8520"))}
    data.pop("calctax_1810", None)
    fields = {**page_fields(page), **data}
    fields.pop("calctax_1810")                                              # vinkje "berekenen" uit
    r = booker.post("/verkoop", data={**fields, "csrf": csrf(booker, page)}, follow_redirects=False)
    tid = posted_id(r)
    assert acc(cdb, tid) == [("1200", Decimal(-120)), ("1810", Decimal(20)), ("8520", Decimal(100))]


def test_errors_keep_input(booker, cdb):
    before = cdb.execute("SELECT count(*) AS n FROM ar").fetchone()["n"]
    page = booker.get("/verkoop/nieuw").text
    assert 'name="vc_text" value="K' in page                 # laatste relatie voorgevuld (lastname_used)
    r = step(booker, "/verkoop", {"vc_text": "", "action": "post", **lines(("10", "8520"))}, page)
    assert r.status_code == 400 and "Kies een klant." in r.text
    page = start(booker, "K001")
    r = step(booker, "/verkoop", {"transdate": "2025-12-31", "action": "post", **lines(("10", "8520"))}, page)
    assert "afgesloten periode" in r.text
    r = step(booker, "/verkoop", {"action": "post", **lines(("10,004", "8520"))}, page)
    assert "meer dan 2 decimalen" in r.text                                 # V16
    r = step(booker, "/verkoop", {"action": "post", **lines(("tien", "8520"))}, page)
    assert "Ongeldig bedrag: tien" in r.text and 'value="tien"' in r.text
    r = step(booker, "/verkoop", {"action": "post", **lines(("10", ""))}, page)
    assert "Regel 1: kies een rekening." in r.text
    r = step(booker, "/verkoop", {"action": "post", **lines(("10", "8520")), **pays(("", "5", "1100"))}, page)
    assert "Vul de datum van de betaling in." in r.text
    assert cdb.execute("SELECT count(*) AS n FROM ar").fetchone()["n"] == before


def test_terms_and_duedate(booker):
    page = start(booker, "K002")                                            # termijn 30
    r = step(booker, "/verkoop", {"transdate": "2026-07-01", "action": "update"}, page)
    assert 'name="duedate" value="2026-07-31"' in r.text
    r = step(booker, "/verkoop", {"terms": "10", "action": "update"}, r.text)
    assert 'name="duedate" value="2026-07-11"' in r.text
    r = step(booker, "/verkoop", {"duedate": "2026-07-21", "action": "update"}, r.text)
    assert 'name="terms" value="20"' in r.text


def test_payment_on_transaction(booker, cdb):
    page = start(booker, "K001")
    tid = posted_id(step(booker, "/verkoop", {"transdate": "2026-07-01", "action": "post",
                                              **lines(("100,00", "8520")),
                                              **pays(("2026-07-05", "50,00", "1100", "BANK-1"))}, page))
    ar = cdb.execute("SELECT amount, paid, datepaid FROM ar WHERE id = %s", (tid,)).fetchone()
    assert ar["amount"] == 121 and ar["paid"] == 50 and str(ar["datepaid"]) == "2026-07-05"
    page = booker.get(f"/verkoop/{tid}").text
    assert 'value="BANK-1"' in page and "Openstaand" in page and "71,00" in page


def test_edit_confirm_and_keep_duedate(booker, cdb):
    page = start(booker, "K002")
    tid = posted_id(step(booker, "/verkoop", {"transdate": "2026-07-01", "action": "post",
                                              **lines(("100,00", "8520"))}, page))
    cdb.execute("UPDATE ar SET duedate = '2026-09-01', onhold = TRUE WHERE id = %s", (tid,))
    page = booker.get(f"/verkoop/{tid}").text
    assert 'name="duedate" value="2026-09-01"' in page and 'name="onhold" value="1" checked' in page
    r = step(booker, "/verkoop", {"description": "gewijzigd", "action": "post"}, page)
    assert r.status_code == 200 and "U wijzigt een bestaande boeking." in r.text
    posted_id(step(booker, "/verkoop", {"action": "post_confirmed"}, r.text))
    ar = cdb.execute("SELECT description, duedate, onhold FROM ar WHERE id = %s", (tid,)).fetchone()
    assert ar["description"] == "gewijzigd" and str(ar["duedate"]) == "2026-09-01" and ar["onhold"]   # V9, V11


def test_post_as_new_and_delete(booker, cdb):
    page = start(booker, "K001")
    tid = posted_id(step(booker, "/verkoop", {"transdate": "2026-07-01", "invnumber": "W-1", "action": "post",
                                              **lines(("10,00", "8520"))}, page))
    page = booker.get(f"/verkoop/{tid}").text
    new = posted_id(step(booker, "/verkoop", {"invnumber": "W-2", "action": "post_as_new"}, page))
    assert new != tid and cdb.execute("SELECT count(*) AS n FROM ar WHERE invnumber IN ('W-1', 'W-2')").fetchone()["n"] == 2
    page = booker.get(f"/verkoop/{new}").text
    r = step(booker, "/verkoop", {"action": "delete"}, page)
    assert "verwijderen?" in r.text
    r = step(booker, "/verkoop", {"action": "delete_confirmed"}, r.text)
    assert r.status_code == 303 and r.headers["location"] == "/verkoop/nieuw?ok=arap_deleted"
    assert cdb.execute("SELECT 1 FROM ar WHERE id = %s", (new,)).fetchone() is None


def test_new_number(booker, cdb):
    page = start(booker, "K001")
    r = step(booker, "/verkoop", {"action": "new_number"}, page)
    m = re.search(r'name="invnumber" value="([^"]+)"', r.text)
    assert m and cdb.execute("SELECT fldvalue FROM defaults WHERE fldname = 'sinumber'").fetchone()["fldvalue"] == m.group(1)


def test_credit_note(booker, cdb):
    page = start(booker, "K001", query="?creditnota=1")
    assert "Creditnota verkoop" in page and 'name="type" value="credit_note"' in page
    tid = posted_id(step(booker, "/verkoop", {"transdate": "2026-07-01", "action": "post",
                                              **lines(("100,00", "8520"))}, page))
    assert cdb.execute("SELECT amount FROM ar WHERE id = %s", (tid,)).fetchone()["amount"] == -121
    assert acc(cdb, tid) == [("1200", Decimal(121)), ("1810", Decimal(-21)), ("8520", Decimal(-100))]
    assert "Creditnota verkoop wijzigen" in booker.get(f"/verkoop/{tid}").text


def test_vendor_duplicate_invnumber(booker, cdb):
    page = start(booker, "L001", prefix="inkoop")
    data = {"transdate": "2026-07-01", "invnumber": "I-77", "action": "post", **lines(("50,00", "8010"))}
    r = step(booker, "/inkoop", data, page)
    assert r.status_code == 200 and "bestaat al bij deze leverancier" in r.text          # V8
    tid = posted_id(step(booker, "/inkoop", {"action": "post"}, r.text), "inkoop")
    ap = cdb.execute("SELECT invnumber, vendor_id, amount FROM ap WHERE id = %s", (tid,)).fetchone()
    assert ap["invnumber"] == "I-77" and ap["vendor_id"] == 901


def test_invoice_with_items_goes_to_invoice_screen(booker):
    r = booker.get("/verkoop/3001", follow_redirects=False)                 # fase 3 F9 (was V2: leespagina)
    assert r.status_code in (302, 303, 307) and r.headers["location"] == "/verkoopfactuur/3001"
    page = booker.get("/verkoopfactuur/3001").text
    assert "Verkoopfactuur wijzigen" in page and "Roomboterkoek" in page


def test_closed_period_readonly(booker, cdb):
    cdb.execute("UPDATE defaults SET fldvalue = '20260331' WHERE fldname = 'closedto'")
    page = booker.get("/verkoop/950").text          # boeking 2026-03-01, afgesloten t/m 2026-03-31
    assert "afgesloten periode" in page and 'name="action" value="post"' not in page


def test_lock_by_other_user(booker, client, db, company, cdb):
    page = start(booker, "K001")
    tid = posted_id(step(booker, "/verkoop", {"transdate": "2026-07-01", "action": "post",
                                              **lines(("10,00", "8520"))}, page))
    booker.get(f"/verkoop/{tid}")                     # lock voor jan
    cdb.execute("UPDATE semaphore SET login = 'admin' WHERE id = %s", (tid,))
    page = booker.get(f"/verkoop/{tid}").text
    assert "in gebruik door Beheerder" in page


def test_readonly_role(client, db, company):
    make_user(db, "lees", memberships=[(company["id"], "readonly", "jan")])
    login(client, "lees")
    assert client.get("/verkoop/nieuw").status_code == 403
    page = client.get("/verkoop/3003").text
    assert "alleen-lezen" in page and 'name="action" value="post"' not in page


# --- zoeklijst (v1.0.1 PB17) ---------------------------------------------------------------

def test_search_list_options_for_combobox(booker, monkeypatch):
    """Opties met nummer en naam apart (static/combo.js toont "K001 – Klant A"), meer dan het maximum als telling."""
    from sqlledger_web import routes_arap
    r = booker.get("/verkoop/relaties", params={"vc_text": "K00", "transdate": "2026-07-01"})
    assert 'value="K001 Klant A" data-n="K001" data-name="Klant A"' in r.text and "data-more" not in r.text
    monkeypatch.setattr(routes_arap, "OPTIONS_MAX", 1)
    r = booker.get("/verkoop/relaties", params={"vc_text": "K00", "transdate": "2026-07-01"})
    assert r.text.count("data-n=") == 1 and re.search(r'<option data-more="\d+" value="" disabled>', r.text)
    page = booker.get("/verkoop/nieuw").text
    assert '<datalist id="vclist" data-remote></datalist>' in page and 'list="vclist"' in page
    assert re.search(r'<option value="8\d+ [^"]+" data-n="8\d+" data-name="[^"]+"></option>', page)
    # zoeklijst, datumvelden en de teksten ervoor staan op elke pagina
    assert '<script src="/static/combo.js" defer></script>' in page and 'src="/static/dates.js"' in page
    assert 'data-msg-more="nog {n} – typ verder"' in page
    assert 'data-msg-date="Vul een volledige datum in (dag, maand en jaar), of maak het veld leeg."' in page
