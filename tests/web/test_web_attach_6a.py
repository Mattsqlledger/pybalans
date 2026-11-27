"""Bijlagen op facturen met artikelen, orders/offertes, relaties en artikelen (fase 4 stap 6a, RB1, RB2, RB6,
RB7): toevoegen via het blok Bijlagen, bij een nieuwe factuur/order meteen, meenemen bij omzetten, niet bij
kopiëren of opslaan als nieuw, rechten en verwijderen."""

import re

import psycopg
import pytest
from psycopg.rows import dict_row

from test_web_arap import page_fields
from test_web_ct import form_fields
from test_web_is import add_item, posted_id, setup  # noqa: F401  (testdata: artikelen A-1/A-2)
from webhelpers import csrf, login, make_user

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


@pytest.fixture
def cdb(settings, company_db):
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True, row_factory=dict_row) as conn:
        conn.execute("SELECT setval('addressid', (SELECT max(id) FROM address))")
        conn.execute("SELECT setval('contactid', (SELECT max(id) FROM contact))")
        yield conn


@pytest.fixture
def booker(client, db, company):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    return client


def main(page):
    """Alleen het hoofdformulier (het blok Bijlagen heeft eigen formulieren met o.a. een veld description)."""
    return page.split('<section class="attachments" id="bijlagen">')[0]


def post(client, url, page, data, files=None):
    fields = form_fields if url in ("/klanten", "/leveranciers") else page_fields   # keuzerondjes
    return client.post(url, data={**fields(main(page)), "csrf": csrf(client, page), **data}, files=files,
                       follow_redirects=False)


def add(client, trans_id, url, formname, content=PDF, name="bijlage.pdf", **extra):
    page = client.get(url).text
    return client.post("/bijlagen/toevoegen", data={"csrf": csrf(client, page), "trans_id": str(trans_id),
                                                    "formname": formname, **extra},
                       files={"bestand": (name, content, "application/pdf")}, follow_redirects=False)


def refs(cdb, trans_id):
    return cdb.execute("SELECT archive_id, formname, login FROM reference WHERE trans_id = %s ORDER BY id",
                       (trans_id,)).fetchall()


def archive_ids(cdb, trans_id):
    return [r["archive_id"] for r in refs(cdb, trans_id) if r["archive_id"]]


def archive_exists(cdb, aid):
    return cdb.execute("SELECT count(*) AS n FROM archive WHERE id = %s", (aid,)).fetchone()["n"] == 1


def delete_first(client, url):
    page = client.get(url).text
    ref_id = re.search(r'action="/bijlagen/(\d+)/verwijderen"', page).group(1)
    return client.post(f"/bijlagen/{ref_id}/verwijderen", data={"csrf": csrf(client, page)}, follow_redirects=False)


def start(client, prefix, vc_text="Bakkerij"):
    page = client.get(f"/{prefix}/nieuw").text
    r = post(client, f"/{prefix}", page, {"vc_text": vc_text, "transdate": "2026-07-01", "action": "update"})
    assert r.status_code == 200, r.text[:2000]
    return r.text


def order_id(r, prefix):
    assert r.status_code == 303, re.sub(r"\s+", " ", r.text)[:3000]
    return int(re.fullmatch(rf"/{prefix}/(\d+)\?ok=order_saved", r.headers["location"]).group(1))


# ------------------------------------------------------------------ facturen met artikelen

def test_invoice_new_with_file_and_block(booker, cdb):
    page = start(booker, "verkoopfactuur")
    page = add_item(booker, page, "A-2", qty="1")
    assert 'enctype="multipart/form-data"' in page and 'name="bijlage"' in page
    r = post(booker, "/verkoopfactuur", page, {"action": "update"}, files={"bijlage": ("bon.png", PNG, "image/png")})
    assert r.status_code == 200 and "bon.png" in r.text and "wordt bewaard bij Opslaan" in r.text
    tid = posted_id(post(booker, "/verkoopfactuur", r.text, {"action": "post"}))
    assert [x["formname"] for x in refs(cdb, tid)] == ["ar_invoice"]
    page = booker.get(f"/verkoopfactuur/{tid}").text
    assert "bon.png" in page and 'action="/bijlagen/toevoegen"' in page and 'value="ar_invoice"' in page
    r = add(booker, tid, f"/verkoopfactuur/{tid}", "ar_invoice", name="tweede.pdf")
    assert r.status_code == 303 and r.headers["location"] == f"/verkoopfactuur/{tid}?ok=attach_added"
    first, second = archive_ids(cdb, tid)
    # opnieuw opslaan laat beide staan
    page = booker.get(f"/verkoopfactuur/{tid}").text
    assert posted_id(post(booker, "/verkoopfactuur", page, {"action": "post_confirmed"})) == tid
    assert archive_ids(cdb, tid) == [first, second]
    # opslaan als nieuw en kopiëren nemen geen bijlage mee (RB2)
    page = booker.get(f"/verkoopfactuur/{tid}").text
    new = posted_id(post(booker, "/verkoopfactuur", page, {"action": "post_as_new"}))
    assert new != tid and archive_ids(cdb, new) == []
    copy =booker.get(f"/verkoopfactuur/nieuw?kopie={tid}").text
    assert "bon.png" not in main(copy) and f'name="ref_archive_1" value="{first}"' not in copy
    # verwijderen via het blok
    r = delete_first(booker, f"/verkoopfactuur/{tid}")
    assert r.headers["location"] == f"/verkoopfactuur/{tid}?ok=attach_deleted"
    assert archive_ids(cdb, tid) == [second] and not archive_exists(cdb, first)


def test_vendor_credit_invoice_formname(booker, cdb):
    page = start(booker, "inkoopfactuur", vc_text="Graanhandel")
    page = add_item(booker, page, "A-1", prefix="inkoopfactuur", qty="2", sellprice="2")
    r = post(booker, "/inkoopfactuur", page, {"invnumber": "IF-1", "action": "post"})
    tid = posted_id(r, "inkoopfactuur")
    assert add(booker, tid, f"/inkoopfactuur/{tid}", "ap_invoice").status_code == 303
    assert add(booker, tid, f"/inkoopfactuur/{tid}", "sales_order").status_code == 404      # soort past niet
    assert archive_ids(cdb, tid) and booker.get(f"/inkoopfactuur/{tid}").text.count("bijlage.pdf") >= 1


def test_credit_note_attachment_survives_repost(booker, cdb):
    """4e: een bijlage op een creditnota kreeg na opnieuw opslaan formname ar_credit_note (zoals AA.pm) en was
    daarna niet meer te verwijderen."""
    page = booker.get("/verkoop/nieuw?creditnota=1").text
    r = post(booker, "/verkoop", page, {"vc_text": "Bakkerij", "action": "update"})
    r = post(booker, "/verkoop", r.text, {"transdate": "2026-04-01", "amount_1": "10,00", "accno_1": "8520",
                                          "rowcount": "1", "action": "post"})
    assert r.status_code == 303, r.text[:2000]
    tid = int(re.search(r"/verkoop/(\d+)", r.headers["location"]).group(1))
    assert add(booker, tid, f"/verkoop/{tid}", "credit_note").status_code == 303
    page = booker.get(f"/verkoop/{tid}").text
    assert post(booker, "/verkoop", page, {"action": "post_confirmed"}).status_code == 303
    assert [x["formname"] for x in refs(cdb, tid)] == ["ar_credit_note"]
    assert delete_first(booker, f"/verkoop/{tid}").headers["location"] == f"/verkoop/{tid}?ok=attach_deleted"


# ------------------------------------------------------------------ orders en offertes, omzetten (RB2)

def test_quotation_order_invoice_share_archive(booker, cdb):
    page = start(booker, "offerte")
    page = add_item(booker, page, "A-1", prefix="offerte", qty="2")
    qid = order_id(post(booker, "/offerte", page, {"action": "save"}), "offerte")
    r = add(booker, qid, f"/offerte/{qid}", "sales_quotation", name="tekening.pdf")
    assert r.headers["location"] == f"/offerte/{qid}?ok=attach_added"
    (aid,) = archive_ids(cdb, qid)
    # omzetten naar order: de bijlage staat in de lijst "gaat mee bij Opslaan"
    page = booker.get(f"/offerte/{qid}").text
    r = post(booker, "/offerte", page, {"action": "to_sales_order"})
    page = booker.get(r.headers["location"]).text
    assert "gaat mee bij Opslaan" in page and f'href="/bijlage/{aid}"' in page
    oid = order_id(post(booker, "/verkooporder", page, {"action": "save"}), "verkooporder")
    assert archive_ids(cdb, oid) == [aid] and [x["formname"] for x in refs(cdb, oid)] == ["sales_order"]
    page = booker.get(f"/verkooporder/{oid}").text
    assert "ook bij" in page and f'href="/offerte/{qid}"' in page
    # gesloten offerte: bijlage toevoegen mag nog (RB6)
    assert "alleen lezen" in booker.get(f"/offerte/{qid}").text
    assert add(booker, qid, f"/offerte/{qid}", "sales_quotation", name="nog een.pdf", content=PDF + b"2").status_code == 303
    # factuur uit de order
    page = booker.get(f"/verkooporder/{oid}").text
    r = post(booker, "/verkooporder", page, {"action": "to_invoice"})
    page = booker.get(r.headers["location"]).text
    assert "gaat mee bij Opslaan" in page
    tid = posted_id(post(booker, "/verkoopfactuur", page, {"action": "post"}))
    assert archive_ids(cdb, tid) == [aid]
    page = booker.get(f"/verkoopfactuur/{tid}").text
    assert f'href="/verkooporder/{oid}"' in page and f'href="/offerte/{qid}"' in page
    # bijlage bij de factuur weghalen: het bestand blijft voor order en offerte
    delete_first(booker, f"/verkoopfactuur/{tid}")
    assert archive_ids(cdb, tid) == [] and archive_exists(cdb, aid)
    assert booker.get(f"/bijlage/{aid}").status_code == 200


def test_drop_carried_attachment_before_save(booker, cdb):
    page = start(booker, "verkooporder")
    page = add_item(booker, page, "A-1", prefix="verkooporder", qty="1")
    oid = order_id(post(booker, "/verkooporder", page, {"action": "save"}), "verkooporder")
    add(booker, oid, f"/verkooporder/{oid}", "sales_order")
    (aid,) = archive_ids(cdb, oid)
    page = booker.get(f"/verkooporder/{oid}").text
    r = post(booker, "/verkooporder", page, {"action": "to_invoice"})
    page = booker.get(r.headers["location"]).text
    r = post(booker, "/verkoopfactuur", page, {"detach": f"a{aid}"})
    assert r.status_code == 200 and "gaat mee bij Opslaan" not in r.text
    tid = posted_id(post(booker, "/verkoopfactuur", r.text, {"action": "post"}))
    assert archive_ids(cdb, tid) == [] and archive_ids(cdb, oid) == [aid]


def test_order_new_with_file_save_as_new_and_delete(booker, cdb):
    page = start(booker, "inkooporder", vc_text="Graanhandel")
    page = add_item(booker, page, "A-1", prefix="inkooporder", qty="1")
    r = post(booker, "/inkooporder", page, {"action": "update"}, files={"bijlage": ("o.pdf", PDF, "application/pdf")})
    oid = order_id(post(booker, "/inkooporder", r.text, {"action": "save"}), "inkooporder")
    (aid,) = archive_ids(cdb, oid)
    page = booker.get(f"/inkooporder/{oid}").text
    new = order_id(post(booker, "/inkooporder", page, {"action": "save_as_new"}), "inkooporder")
    assert new != oid and archive_ids(cdb, new) == []
    page = booker.get(f"/inkooporder/{oid}").text
    r = post(booker, "/inkooporder", page, {"action": "delete_confirmed"})
    assert r.status_code == 303
    assert refs(cdb, oid) == [] and not archive_exists(cdb, aid)


def test_tampered_archive_id_not_linked(booker, client, db, company, cdb):
    """Een verborgen veld ref_archive met een vertrouwelijk stuk van een ander koppelt niets."""
    page = start(booker, "verkooporder")
    page = add_item(booker, page, "A-1", prefix="verkooporder", qty="1")
    oid = order_id(post(booker, "/verkooporder", page, {"action": "save"}), "verkooporder")
    add(booker, oid, f"/verkooporder/{oid}", "sales_order", name="geheim.pdf", confidential="1")
    (secret,) = archive_ids(cdb, oid)
    booker.cookies.clear()
    make_user(db, "ander", memberships=[(company["id"], "bookkeeper", "piet")])
    login(client, "ander")
    page = start(client, "verkooporder")
    page = add_item(client, page, "A-1", prefix="verkooporder", qty="1")
    r = post(client, "/verkooporder", page, {"refcount": "1", "ref_description_1": "buit",
                                             "ref_archive_1": str(secret), "action": "save"})
    other = order_id(r, "verkooporder")
    assert [x["archive_id"] for x in refs(cdb, other)] == [None]
    assert client.get(f"/bijlage/{secret}").status_code == 404


# ------------------------------------------------------------------ relaties en artikelen

def new_customer(client):
    page = client.get("/klanten/nieuw").text
    assert "Bijlagen kunt u toevoegen nadat u hebt opgeslagen." in page
    r = post(client, "/klanten", page, {"name": "Bijlagenklant", "action": "save"})
    assert r.status_code == 303, re.findall(r"class=\"error[^>]*>([^<]*)", r.text)
    return int(re.match(r"/klanten/(\d+)", r.headers["location"]).group(1))


def test_customer_attachments(booker, client, db, company, cdb):
    vid = new_customer(booker)
    r = add(booker, vid, f"/klanten/{vid}", "customer", name="contract.pdf")
    assert r.headers["location"] == f"/klanten/{vid}?ok=attach_added"
    assert add(booker, vid, f"/klanten/{vid}", "vendor").status_code == 404
    (aid,) = archive_ids(cdb, vid)
    page = booker.get(f"/klanten/{vid}").text
    assert "contract.pdf" in page
    # wijzigen en opslaan laat de bijlage staan; opslaan als nieuw neemt hem niet mee
    r = post(booker, "/klanten", page, {"city": "Delft", "action": "save"})
    assert r.status_code == 303 and archive_ids(cdb, vid) == [aid]
    page = booker.get(f"/klanten/{vid}").text
    r = post(booker, "/klanten", page, {"name": "Kopieklant", "action": "save_as_new"})
    new = int(re.match(r"/klanten/(\d+)", r.headers["location"]).group(1))
    assert new != vid and archive_ids(cdb, new) == []
    # vertrouwelijke bijlage van een andere gebruiker gaat bij verwijderen ook weg (RB7)
    booker.cookies.clear()
    make_user(db, "ander", memberships=[(company["id"], "bookkeeper", "piet")])
    login(client, "ander")
    add(client, vid, f"/klanten/{vid}", "customer", name="van piet.pdf", content=PDF + b"piet",
        confidential="1")
    secret = archive_ids(cdb, vid)[1]
    page = client.get(f"/klanten/{vid}").text
    assert "van piet.pdf" in page
    client.cookies.clear()
    login(booker, "boek")
    page = booker.get(f"/klanten/{vid}").text
    assert "van piet.pdf" not in page
    r = post(booker, "/klanten", page, {"action": "delete_confirmed"})
    assert r.status_code == 303 and r.headers["location"] == "/klanten?ok=ct_deleted"
    assert refs(cdb, vid) == [] and not archive_exists(cdb, aid) and not archive_exists(cdb, secret)


def test_vendor_block(booker, cdb):
    vid = cdb.execute("SELECT id FROM vendor ORDER BY id LIMIT 1").fetchone()["id"]
    page = booker.get(f"/leveranciers/{vid}").text
    assert 'action="/bijlagen/toevoegen"' in page and 'value="vendor"' in page
    assert add(booker, vid, f"/leveranciers/{vid}", "vendor").headers["location"] == \
        f"/leveranciers/{vid}?ok=attach_added"


def new_part(client, **kw):
    page = client.get("/artikelen/nieuw").text
    assert "Bijlagen kunt u toevoegen nadat u hebt opgeslagen." in page
    data = {"partnumber": "B-1", "description": "Bijlagenartikel", "unit": "st", "sellprice": "1",
            "inventory_accno": "7001", "income_accno": "8520", "expense_accno": "8020", "action": "save", **kw}
    r = post(client, "/artikelen", page, data)
    assert r.status_code == 303, r.text[:2000]
    return int(re.match(r"/artikelen/(\d+)", r.headers["location"]).group(1))


def test_part_attachments_and_delete(booker, cdb):
    pid = new_part(booker)
    assert add(booker, pid, f"/artikelen/{pid}", "service").status_code == 404      # het is een artikel
    r = add(booker, pid, f"/artikelen/{pid}", "part", content=PNG, name="foto.png")
    assert r.headers["location"] == f"/artikelen/{pid}?ok=attach_added"
    (aid,) = archive_ids(cdb, pid)
    page = booker.get(f"/artikelen/{pid}").text
    assert "foto.png" in page and 'value="part"' in page
    assert post(booker, "/artikelen", page, {"notes": "x", "action": "save"}).status_code == 303
    assert archive_ids(cdb, pid) == [aid]
    page = booker.get(f"/artikelen/{pid}").text
    r = post(booker, "/artikelen", page, {"partnumber": "B-2", "action": "save_as_new"})
    new = int(re.match(r"/artikelen/(\d+)", r.headers["location"]).group(1))
    assert archive_ids(cdb, new) == []
    page = booker.get(f"/artikelen/{pid}").text
    assert post(booker, "/artikelen", page, {"action": "delete_confirmed"}).status_code == 303
    assert refs(cdb, pid) == [] and not archive_exists(cdb, aid)


def test_service_and_assembly_formname(booker, cdb):
    sid = cdb.execute("""INSERT INTO parts (partnumber, description, income_accno_id, expense_accno_id)
                         VALUES ('D-1', 'Dienst', (SELECT id FROM chart WHERE accno = '8520'),
                                 (SELECT id FROM chart WHERE accno = '8020')) RETURNING id""").fetchone()["id"]
    page = booker.get(f"/artikelen/{sid}").text
    assert 'name="formname" value="service"' in page
    assert add(booker, sid, f"/artikelen/{sid}", "service").status_code == 303


# ------------------------------------------------------------------ rechten (RB6)

def test_reader_and_modules(booker, client, db, company, cdb):
    vid = new_customer(booker)
    add(booker, vid, f"/klanten/{vid}", "customer")
    (aid,) = archive_ids(cdb, vid)
    page = start(booker, "verkooporder")
    page = add_item(booker, page, "A-1", prefix="verkooporder", qty="1")
    oid = order_id(post(booker, "/verkooporder", page, {"action": "save"}), "verkooporder")
    booker.cookies.clear()
    make_user(db, "kijk", memberships=[(company["id"], "readonly", "kijk")])
    login(client, "kijk")
    page = client.get(f"/klanten/{vid}").text
    assert f'href="/bijlage/{aid}"' in page and 'action="/bijlagen/toevoegen"' not in page
    assert "/verwijderen" not in page and "Bijlagen kun je toevoegen" not in page
    assert client.get(f"/bijlage/{aid}").status_code == 200
    assert "Geen bijlagen." in client.get(f"/verkooporder/{oid}").text
    r = client.post("/bijlagen/toevoegen", data={"csrf": csrf(client, page), "trans_id": str(vid),
                                                 "formname": "customer"},
                    files={"bestand": ("x.pdf", PDF, "application/pdf")}, follow_redirects=False)
    assert r.status_code == 403
    client.cookies.clear()
    login(booker, "boek")
    db.execute("UPDATE company SET modules = array_remove(modules, 'oe') WHERE id = %s", (company["id"],))
    assert add(booker, oid, f"/klanten/{vid}", "sales_order").status_code == 403
    db.execute("UPDATE company SET modules = array_remove(modules, 'ar') WHERE id = %s", (company["id"],))
    assert booker.get(f"/bijlage/{aid}").status_code == 403
