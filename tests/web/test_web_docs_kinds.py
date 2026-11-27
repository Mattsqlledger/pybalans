"""Overige documentsoorten in de webapp (fase 4 stap 2, DS1/DS2/DS8): pakbon/picklijst, orders en offertes,
verkoop/inkoop zonder artikelen; knoppen met keuzelijst, Opslaan en PDF, status per soort."""

import pytest

from test_web_arap import lines, posted_id as arap_id, start as arap_start, step, tax_setup  # noqa: F401
from test_web_is import add_item, cdb, posted_id, setup, start  # noqa: F401 (fixtures)
from test_web_oe import saved_id, start as oe_start
from webhelpers import login, make_user


@pytest.fixture
def booker(client, db, company, settings, tmp_path):
    settings.data_dir = str(tmp_path)
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    return client


def pdf_ok(client, url):
    r = client.get(url)
    assert r.status_code == 200, r.text[:500]
    assert r.headers["content-type"] == "application/pdf" and r.content[:5] == b"%PDF-"
    return r


def status(cdb, tid, formname):
    return cdb.execute("SELECT printed, emailed FROM status WHERE trans_id = %s AND formname = %s",
                       (tid, formname)).fetchone()


def test_invoice_packing_and_pick_list(booker, cdb):
    page = start(booker, "K001")
    page = add_item(booker, page, "A-1", qty="2")
    sid = posted_id(step(booker, "/verkoopfactuur", {"action": "post"}, page))
    page = booker.get(f"/verkoopfactuur/{sid}").text
    # keuzelijst: factuur, pakbon, picklijst (DS1/DS2)
    for key in ("invoice", "packing_list", "pick_list"):
        assert f'href="/document/{key}/{sid}.pdf"' in page and f'href="/document/{key}/{sid}/mail"' in page
    assert 'class="docmenu"' in page
    pdf_ok(booker, f"/document/packing_list/{sid}.pdf")
    pdf_ok(booker, f"/document/pick_list/{sid}.pdf")
    assert status(cdb, sid, "packing_list") == {"printed": True, "emailed": False}
    assert status(cdb, sid, "invoice") is None                   # vinkje per soort
    page = booker.get(f"/verkoopfactuur/{sid}").text
    assert "Pakbon — Geprint: ja" in page and "Picklijst — Geprint: ja" in page
    # soort die niet bij dit document hoort
    assert booker.get(f"/document/sales_order/{sid}.pdf").status_code == 404
    assert booker.get(f"/document/ar_transaction/{sid}.pdf").status_code == 404


def test_orders_and_quotations(booker, cdb):
    page = oe_start(booker, "Bakkerij")
    page = add_item(booker, page, "A-1", prefix="verkooporder", qty="4")
    oid = saved_id(step(booker, "/verkooporder", {"action": "save"}, page))
    page = booker.get(f"/verkooporder/{oid}").text
    for key in ("sales_order", "packing_list", "pick_list"):
        assert f'href="/document/{key}/{oid}.pdf"' in page
    for key in ("sales_order", "packing_list", "pick_list"):
        pdf_ok(booker, f"/document/{key}/{oid}.pdf")
    assert status(cdb, oid, "sales_order") == {"printed": True, "emailed": False}
    assert booker.get(f"/document/invoice/{oid}.pdf").status_code == 404
    assert booker.get(f"/document/purchase_order/{oid}.pdf").status_code == 404
    # offerte: alleen de offerte
    page = oe_start(booker, "Bakkerij", prefix="offerte")
    page = add_item(booker, page, "A-1", prefix="offerte", qty="1")
    qid = saved_id(step(booker, "/offerte", {"action": "save"}, page), prefix="offerte")
    page = booker.get(f"/offerte/{qid}").text
    assert f'href="/document/sales_quotation/{qid}.pdf"' in page and "docmenu" not in page
    pdf_ok(booker, f"/document/sales_quotation/{qid}.pdf")
    assert booker.get(f"/document/packing_list/{qid}.pdf").status_code == 404


def test_save_and_pdf_new_order(booker):
    page = oe_start(booker, "Bakkerij")
    assert 'value="save_pdf"' in page
    page = add_item(booker, page, "A-1", prefix="verkooporder", qty="1")
    r = step(booker, "/verkooporder", {"action": "save_pdf"}, page)
    assert r.status_code == 303 and r.headers["location"].endswith("&pdf=sales_order")
    oid = int(r.headers["location"].split("/")[2].split("?")[0])
    page = booker.get(r.headers["location"]).text
    assert "PDF openen" in page and f'href="/document/sales_order/{oid}.pdf"' in page
    assert 'value="save_pdf"' not in page                        # alleen bij een nieuwe order


def test_transaction_without_items(booker, cdb):
    page = arap_start(booker, "K001")
    tid = arap_id(step(booker, "/verkoop", {"transdate": "2026-07-01", "action": "post",
                                           **lines(("100,00", "8520 Opbrengst Verkopen A", "advies"))}, page))
    page = booker.get(f"/verkoop/{tid}").text
    assert f'href="/document/ar_transaction/{tid}.pdf"' in page and f'href="/document/ar_transaction/{tid}/mail"' in page
    r = pdf_ok(booker, f"/document/ar_transaction/{tid}.pdf")
    assert "Factuur-" in r.headers["content-disposition"]
    assert status(cdb, tid, "transaction") == {"printed": True, "emailed": False}   # formname zoals Perl
    assert booker.get(f"/document/invoice/{tid}.pdf").status_code == 404           # geen factuur met artikelen
    page = arap_start(booker, "K001")
    r = step(booker, "/verkoop", {"transdate": "2026-07-01", "action": "post_pdf",
                                  **lines(("50,00", "8520 Opbrengst Verkopen A", "x"))}, page)
    assert r.status_code == 303 and r.headers["location"].endswith("&pdf=ar_transaction")


def test_mail_order_fills_placeholders(booker, cdb):
    page = oe_start(booker, "Bakkerij")
    page = add_item(booker, page, "A-1", prefix="verkooporder", qty="2")
    oid = saved_id(step(booker, "/verkooporder", {"action": "save"}, page))
    number = cdb.execute("SELECT ordnumber FROM oe WHERE id = %s", (oid,)).fetchone()["ordnumber"]
    page = booker.get(f"/document/sales_order/{oid}/mail").text
    assert f"Orderbevestiging {number}" in page and "{nummer}" not in page
    assert f'href="/verkooporder/{oid}"' in page


def test_doc_language_setting_and_variant(client, db, company, cdb, settings, tmp_path):
    settings.data_dir = str(tmp_path)
    secret = make_user(db, "baas", totp=True, memberships=[(company["id"], "admin", "baas")])
    login(client, "baas", secret)
    page = client.get("/beheer/instellingen").text
    assert 'name="doc_language"' in page and 'value="de"' in page and 'value="fr"' in page
    r = step(client, "/beheer/instellingen", {"doc_language": "de"}, page)
    assert r.status_code == 303
    assert db.execute("SELECT doc_language FROM company WHERE id = %s", (company["id"],)).fetchone()["doc_language"] == "de"
    assert step(client, "/beheer/instellingen", {"doc_language": "xx"}, page).status_code == 400
    # factuur in het Duits: bedrag in woorden ook Duits
    page = start(client, "K001")
    page = add_item(client, page, "A-1", qty="2")
    assert '<select name="language_code">' in page and 'value="en"' in page        # Beheer > Talen: en
    cdb.execute("DELETE FROM language")
    assert 'type="hidden" name="language_code"' in client.get("/verkoopfactuur/nieuw").text   # geen talen/varianten
    sid = posted_id(step(client, "/verkoopfactuur", {"action": "post"}, page))
    from sqlledger_web.routes_docs import load
    # variant uit Beheer > Talen wordt een keuze op het scherm (DS9)
    cdb.execute("INSERT INTO language (code, description) VALUES ('fr', 'Français')")
    (tmp_path / "templates" / str(company["id"]) / "Factor").mkdir(parents=True)
    page = client.get(f"/verkoopfactuur/{sid}").text
    assert '<select name="language_code">' in page and 'value="fr"' in page and 'value="Factor"' in page
    r = step(client, "/verkoopfactuur", {"action": "post_confirmed", "language_code": "fr"}, page)
    assert r.status_code == 303
    assert cdb.execute("SELECT language_code FROM ar WHERE id = %s", (sid,)).fetchone()["language_code"] == "fr"
    assert client.get(f"/document/invoice/{sid}.pdf").status_code == 200
    assert load is not None
