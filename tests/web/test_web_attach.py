"""Blok Bijlagen op de boekingsschermen verkoop, inkoop en memoriaal (fase 4 stap 4e, IU6/D17):
bekijken, downloaden, verwijderen, toevoegen; bij een nieuwe boeking gaat het bestand mee met Opslaan."""

import re
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from sqlledger import UserConfig
from sqlledger.attachments import MB, Attachments, encode
from webhelpers import csrf, login, make_user
from test_web_arap import acc, page_fields, posted_id, start, step

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


@pytest.fixture
def cdb(settings, company_db):
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture(autouse=True)
def tax_setup(cdb):
    cdb.execute("UPDATE tax SET validto = '2025-12-31' WHERE taxnumber IS NULL")


@pytest.fixture
def booker(client, db, company):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    return client


def new_vendor_booking(client):
    page = start(client, "L002", prefix="inkoop")
    r = step(client, "/inkoop", {"invnumber": "B-1", "transdate": "2026-04-01", "amount_1": "100,00",
                                 "accno_1": "8100", "rowcount": "1", "action": "post"}, page)
    return posted_id(r, "inkoop")


def add(client, trans_id, url, formname, content=PDF, name="factuur.pdf", **extra):
    page = client.get(url).text
    return client.post("/bijlagen/toevoegen", data={"csrf": csrf(client, page), "trans_id": str(trans_id),
                                                    "formname": formname, **extra},
                       files={"bestand": (name, content, "application/pdf")}, follow_redirects=False)


def archive_ids(cdb, trans_id):
    return [r["archive_id"] for r in cdb.execute(
        "SELECT archive_id FROM reference WHERE trans_id = %s AND archive_id IS NOT NULL ORDER BY id",
        (trans_id,)).fetchall()]


# ------------------------------------------------------------------ bestaande boeking

def test_add_view_download_delete(booker, cdb):
    tid = new_vendor_booking(booker)
    page = booker.get(f"/inkoop/{tid}").text
    assert "Bijlagen" in page and 'action="/bijlagen/toevoegen"' in page and "data-dropzone" in page
    r = add(booker, tid, f"/inkoop/{tid}", "ap_transaction")
    assert r.status_code == 303 and r.headers["location"] == f"/inkoop/{tid}?ok=attach_added"
    (aid,) = archive_ids(cdb, tid)
    page = booker.get(f"/inkoop/{tid}?ok=attach_added").text
    assert f'href="/bijlage/{aid}"' in page and f'href="/bijlage/{aid}?download=1"' in page
    assert "factuur.pdf" in page and "Bijlage toegevoegd." in page
    r = booker.get(f"/bijlage/{aid}")
    assert r.status_code == 200 and r.content == PDF and r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("inline")
    r = booker.get(f"/bijlage/{aid}?download=1")
    assert r.headers["content-disposition"].startswith("attachment") and "factuur.pdf" in r.headers["content-disposition"]
    # opnieuw opslaan van de boeking laat de bijlage staan
    page = booker.get(f"/inkoop/{tid}").text
    r = step(booker, "/inkoop", {"action": "post_confirmed"}, page)
    assert posted_id(r, "inkoop") == tid
    assert archive_ids(cdb, tid) == [aid]
    # verwijderen
    page = booker.get(f"/inkoop/{tid}").text
    ref_id = re.search(r'action="/bijlagen/(\d+)/verwijderen"', page).group(1)
    r = booker.post(f"/bijlagen/{ref_id}/verwijderen", data={"csrf": csrf(booker, page)}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/inkoop/{tid}?ok=attach_deleted"
    assert archive_ids(cdb, tid) == []
    assert cdb.execute("SELECT count(*) AS n FROM archive WHERE id = %s", (aid,)).fetchone()["n"] == 0
    assert booker.get(f"/bijlage/{aid}").status_code == 404


def test_add_refuses_bad_file(booker, cdb):
    tid = new_vendor_booking(booker)
    r = add(booker, tid, f"/inkoop/{tid}", "ap_transaction", content=b"<html>", name="x.pdf")
    assert r.status_code == 400 and "Alleen PDF, JPG of PNG" in r.text
    assert archive_ids(cdb, tid) == []


def test_add_in_closed_period_and_description(booker, cdb):
    tid = new_vendor_booking(booker)
    cdb.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('closedto', '2026-12-31')")
    page = booker.get(f"/inkoop/{tid}").text
    assert "afgesloten periode" in page and 'action="/bijlagen/toevoegen"' in page
    r = add(booker, tid, f"/inkoop/{tid}", "ap_transaction", content=PNG, name="bon.png", description="Bon")
    assert r.status_code == 303
    ref = cdb.execute("SELECT description FROM reference WHERE trans_id = %s", (tid,)).fetchone()
    assert ref["description"] == "Bon"
    assert acc(cdb, tid)                                      # boeking zelf ongewijzigd


def test_unsafe_sl_file_is_only_downloaded(booker, cdb):
    """Een bestand dat SQL-Ledger wel toeliet (bv. html) nooit in de browser tonen."""
    tid = new_vendor_booking(booker)
    aid = cdb.execute("INSERT INTO archive (filename) VALUES ('x.html') RETURNING id").fetchone()["id"]
    for rn, bt in enumerate(encode(b"<script>alert(1)</script>"), 1):
        cdb.execute("INSERT INTO archivedata VALUES (%s, %s, %s)", (aid, bt, rn))
    cdb.execute("""INSERT INTO reference (trans_id, description, archive_id, login, formname)
                   VALUES (%s, 'van SL', %s, '', 'ap_transaction')""", (tid, aid))
    r = booker.get(f"/bijlage/{aid}")
    assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment")
    assert "van SL" in booker.get(f"/inkoop/{tid}").text


def test_roles_and_confidential(booker, client, db, company, cdb, settings, company_db):
    tid = new_vendor_booking(booker)
    add(booker, tid, f"/inkoop/{tid}", "ap_transaction")
    add(booker, tid, f"/inkoop/{tid}", "ap_transaction", content=PDF + b"geheim", name="geheim.pdf",
        confidential="1")
    open_aid, secret_aid = archive_ids(cdb, tid)
    booker.cookies.clear()
    make_user(db, "kijk", memberships=[(company["id"], "readonly", "kijk")])
    login(client, "kijk")
    page = client.get(f"/inkoop/{tid}").text
    assert f'href="/bijlage/{open_aid}"' in page and "geheim.pdf" not in page
    assert 'action="/bijlagen/toevoegen"' not in page and "/verwijderen" not in page
    assert client.get(f"/bijlage/{open_aid}").status_code == 200
    assert client.get(f"/bijlage/{secret_aid}").status_code == 404
    r = client.post("/bijlagen/toevoegen", data={"csrf": csrf(client, page), "trans_id": str(tid),
                                                 "formname": "ap_transaction"},
                    files={"bestand": ("x.pdf", PDF, "application/pdf")}, follow_redirects=False)
    assert r.status_code == 403


def test_module_off_hides_attachment(booker, db, company, cdb):
    tid = new_vendor_booking(booker)
    add(booker, tid, f"/inkoop/{tid}", "ap_transaction")
    (aid,) = archive_ids(cdb, tid)
    db.execute("UPDATE company SET modules = array_remove(modules, 'ap') WHERE id = %s", (company["id"],))
    assert booker.get(f"/bijlage/{aid}").status_code == 403


# ------------------------------------------------------------------ nieuwe boeking

def test_new_booking_takes_file_on_save(booker, cdb, settings):
    page = start(booker, "L002", prefix="inkoop")
    assert 'enctype="multipart/form-data"' in page and 'name="bijlage"' in page and "data-dropzone" in page
    data = {**page_fields(page), "csrf": csrf(booker, page), "invnumber": "N-1", "transdate": "2026-04-01",
            "amount_1": "100,00", "accno_1": "8100", "rowcount": "1", "action": "update"}
    r = booker.post("/inkoop", data=data, files={"bijlage": ("scan.pdf", PDF, "application/pdf")})
    assert r.status_code == 200 and "scan.pdf" in r.text
    token = re.search(r'name="ref_token_\d+" value="([0-9a-f]{32})"', r.text).group(1)
    assert f'href="/bijlage/tijdelijk/{token}"' in r.text
    r = step(booker, "/inkoop", {"action": "post"}, r.text)
    tid = posted_id(r, "inkoop")
    (aid,) = archive_ids(cdb, tid)
    assert Attachments(cdb, UserConfig(login="jan")).get(aid)[1] == PDF
    assert not list((Path(settings.data_dir) / "uploads").iterdir())


def test_new_booking_remove_pending(booker):
    page = start(booker, "L002", prefix="inkoop")
    data = {**page_fields(page), "csrf": csrf(booker, page), "action": "update"}
    r = booker.post("/inkoop", data=data, files={"bijlage": ("scan.pdf", PDF, "application/pdf")})
    token = re.search(r'name="ref_token_\d+" value="([0-9a-f]{32})"', r.text).group(1)
    r = step(booker, "/inkoop", {"detach": token}, r.text)
    assert r.status_code == 200 and "scan.pdf" not in r.text


def test_new_booking_bad_file(booker):
    page = start(booker, "L002", prefix="inkoop")
    data = {**page_fields(page), "csrf": csrf(booker, page), "action": "update"}
    r = booker.post("/inkoop", data=data, files={"bijlage": ("x.pdf", b"MZ", "application/pdf")})
    assert r.status_code == 400 and "Alleen PDF, JPG of PNG" in r.text


def test_copy_and_save_as_new_leave_file_behind(booker, cdb):
    tid = new_vendor_booking(booker)
    add(booker, tid, f"/inkoop/{tid}", "ap_transaction")
    page = booker.get(f"/inkoop/nieuw?kopie={tid}").text
    assert "factuur.pdf" not in page and 'name="ref_archive_' not in page.replace('name="ref_archive_1" value=""', "")
    page = booker.get(f"/inkoop/{tid}").text
    r = step(booker, "/inkoop", {"action": "post_as_new", "invnumber": "B-2"}, page)
    new = posted_id(r, "inkoop")
    assert new != tid and archive_ids(cdb, new) == [] and len(archive_ids(cdb, tid)) == 1


def test_sales_booking_has_block(booker, cdb):
    page = start(booker, "Bakkerij")
    r = step(booker, "/verkoop", {"transdate": "2026-04-01", "amount_1": "10,00", "accno_1": "8520",
                                  "rowcount": "1", "action": "post"}, page)
    tid = posted_id(r)
    r = add(booker, tid, f"/verkoop/{tid}", "ar_transaction")
    assert r.headers["location"] == f"/verkoop/{tid}?ok=attach_added"


def test_wrong_formname_for_booking(booker, cdb):
    tid = new_vendor_booking(booker)
    r = add(booker, tid, f"/inkoop/{tid}", "ar_transaction")
    assert r.status_code == 404
    r = add(booker, tid, f"/inkoop/{tid}", "nonsense")
    assert r.status_code == 400


# ------------------------------------------------------------------ memoriaal

def test_gl_new_and_existing(booker, cdb):
    page = booker.get("/memoriaal/nieuw").text
    assert 'enctype="multipart/form-data"' in page and 'name="bijlage"' in page
    data = {**page_fields(page), "csrf": csrf(booker, page), "transdate": "2026-04-02", "reference": "M-1",
            "accno_1": "8100", "debit_1": "5,00", "accno_2": "1100", "credit_2": "5,00", "rowcount": "2",
            "action": "update"}
    r = booker.post("/memoriaal", data=data, files={"bijlage": ("bon.png", PNG, "image/png")})
    assert r.status_code == 200 and "bon.png" in r.text
    r = step(booker, "/memoriaal", {"action": "post"}, r.text)
    assert r.status_code == 303, r.text[:3000]
    tid = int(re.search(r"/memoriaal/(\d+)", r.headers["location"]).group(1))
    assert len(archive_ids(cdb, tid)) == 1
    r = add(booker, tid, f"/memoriaal/{tid}", "gl", name="tweede.pdf")
    assert r.headers["location"] == f"/memoriaal/{tid}?ok=attach_added"
    assert len(archive_ids(cdb, tid)) == 2
    page = booker.get(f"/memoriaal/{tid}").text
    assert "bon.png" in page and "tweede.pdf" in page


# ------------------------------------------------------------------ maximale uploadgrootte (stap 4f, MU1–MU5)

def set_limit(cdb, mb):
    cdb.execute("DELETE FROM defaults WHERE fldname = 'max_upload_size'")
    if mb is not None:
        cdb.execute("INSERT INTO defaults VALUES ('max_upload_size', %s)", (str(mb),))


def test_limit_in_dropzone(booker, cdb):
    tid = new_vendor_booking(booker)
    set_limit(cdb, None)
    page = booker.get(f"/inkoop/{tid}").text
    assert f'data-max-bytes="{10 * MB}"' in page and "(hoogstens 10 MB)" in page
    assert 'data-too-large="Het bestand is groter dan 10 MB."' in page
    set_limit(cdb, 3)
    page = booker.get(f"/inkoop/{tid}").text
    assert f'data-max-bytes="{3 * MB}"' in page and "(hoogstens 3 MB)" in page
    assert f'data-max-bytes="{3 * MB}"' in start(booker, "L002", prefix="inkoop")
    assert f'data-max-bytes="{3 * MB}"' in booker.get("/inkoop/uploaden").text


def test_limit_on_server(booker, cdb):
    tid = new_vendor_booking(booker)
    set_limit(cdb, 1)
    big = (PDF + b"0" * MB)[:MB + 1]
    r = add(booker, tid, f"/inkoop/{tid}", "ap_transaction", content=big)
    assert r.status_code == 400 and "Het bestand is groter dan 1 MB." in r.text
    assert archive_ids(cdb, tid) == []
    # nieuwe boeking: bestand wordt niet tijdelijk bewaard
    page = start(booker, "L002", prefix="inkoop")
    data = {**page_fields(page), "csrf": csrf(booker, page), "action": "update"}
    r = booker.post("/inkoop", data=data, files={"bijlage": ("groot.pdf", big, "application/pdf")})
    assert r.status_code == 400 and "groter dan 1 MB" in r.text
    assert not re.search(r'name="ref_token_\d+" value="[0-9a-f]{32}"', r.text)
    # boven de standaard van 10 MB als de administratie dat toestaat
    set_limit(cdb, 12)
    data11 = (PDF + b"0" * (11 * MB))[:11 * MB]
    r = add(booker, tid, f"/inkoop/{tid}", "ap_transaction", content=data11)
    assert r.status_code == 303
    (aid,) = archive_ids(cdb, tid)
    assert booker.get(f"/bijlage/{aid}").content == data11


def test_request_above_highest_limit_refused(booker, cdb):
    tid = new_vendor_booking(booker)
    set_limit(cdb, 25)
    huge = PDF + b"0" * (26 * MB)
    r = add(booker, tid, f"/inkoop/{tid}", "ap_transaction", content=huge)
    assert r.status_code == 413 and "groter dan 25 MB" in r.text
    assert archive_ids(cdb, tid) == []


def test_limit_message_in_english(client, db, company, cdb):
    make_user(db, "boek_en", memberships=[(company["id"], "bookkeeper", "jan")])
    db.execute("UPDATE app_user SET language = 'en' WHERE login = 'boek_en'")
    login(client, "boek_en")
    tid = new_vendor_booking(client)
    set_limit(cdb, 1)
    page = client.get(f"/inkoop/{tid}").text
    assert "(at most 1 MB)" in page and 'data-too-large="The file is larger than 1 MB."' in page
    r = add(client, tid, f"/inkoop/{tid}", "ap_transaction", content=(PDF + b"0" * MB)[:MB + 1])
    assert r.status_code == 400 and "The file is larger than 1 MB." in r.text


# ------------------------------------------------------------------ dubbele bestanden (stap 6b, RB5)

def test_duplicate_on_saved_booking_asks_and_confirms(booker, cdb, settings):
    one = new_vendor_booking(booker)
    add(booker, one, f"/inkoop/{one}", "ap_transaction")
    page = booker.get("/inkoop/nieuw").text
    r = step(booker, "/inkoop", {"vc_text": "L002", "invnumber": "B-2", "transdate": "2026-04-02",
                                 "amount_1": "50,00", "accno_1": "8100", "rowcount": "1", "action": "post"},
             start(booker, "L002", prefix="inkoop"))
    two = posted_id(r, "inkoop")
    r = add(booker, two, f"/inkoop/{two}", "ap_transaction")
    assert r.status_code == 200 and "Dit bestand hangt al bij:" in r.text and f'href="/inkoop/{one}"' in r.text
    assert "B-1" in r.text and archive_ids(cdb, two) == []
    token = re.search(r'name="token" value="([0-9a-f]{32})"', r.text).group(1)
    # F44: Annuleren is een POST die het tijdelijke bestand weggooit en terug gaat naar de boeking
    assert 'formaction="/bijlagen/annuleren"' in r.text
    uploads = Path(settings.data_dir) / "uploads"
    assert {p.stem for p in uploads.iterdir()} == {token}
    c = booker.post("/bijlagen/annuleren", data={"csrf": csrf(booker, page), "trans_id": str(two),
                                                 "formname": "ap_transaction", "token": token}, follow_redirects=False)
    assert c.status_code == 303 and c.headers["location"] == f"/inkoop/{two}"
    assert not list(uploads.iterdir()) and archive_ids(cdb, two) == []
    r = add(booker, two, f"/inkoop/{two}", "ap_transaction")
    token = re.search(r'name="token" value="([0-9a-f]{32})"', r.text).group(1)
    r = booker.post("/bijlagen/toevoegen", data={"csrf": csrf(booker, page), "trans_id": str(two),
                                                 "formname": "ap_transaction", "token": token, "dup_ok": "1"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/inkoop/{two}?ok=attach_added"
    (a2,) = archive_ids(cdb, two)
    assert a2 not in archive_ids(cdb, one)                                  # eigen archiefstuk
    assert not list(uploads.iterdir())
    # hetzelfde bestand nog eens bij dezelfde boeking: geweigerd
    r = add(booker, two, f"/inkoop/{two}", "ap_transaction", name="weer.pdf")
    assert r.status_code == 400 and "hangt hier al" in r.text


def test_duplicate_pending_needs_checkbox(booker, cdb):
    one = new_vendor_booking(booker)
    add(booker, one, f"/inkoop/{one}", "ap_transaction")
    page = start(booker, "L002", prefix="inkoop")
    data = {**page_fields(page), "csrf": csrf(booker, page), "invnumber": "B-3", "transdate": "2026-04-03",
            "amount_1": "10,00", "accno_1": "8100", "rowcount": "1", "action": "update"}
    r = booker.post("/inkoop", data=data, files={"bijlage": ("f.pdf", PDF, "application/pdf")})
    assert r.status_code == 200 and "Dit bestand hangt al bij:" in r.text and "toch toevoegen" in r.text
    token = re.search(r'name="dup_ok_([0-9a-f]{32})"', r.text).group(1)
    r2 = step(booker, "/inkoop", {"action": "post"}, r.text)
    assert r2.status_code == 400 and "toch toevoegen" in r2.text
    # zelfde bestand nog eens slepen bij dezelfde nieuwe boeking: geweigerd
    data = {**page_fields(r.text), "csrf": csrf(booker, r.text), "action": "update"}
    r3 = booker.post("/inkoop", data=data, files={"bijlage": ("f2.pdf", PDF, "application/pdf")})
    assert r3.status_code == 400 and "staat al bij de bijlagen" in r3.text
    r = step(booker, "/inkoop", {"action": "post", f"dup_ok_{token}": "1"}, r.text)
    tid = posted_id(r, "inkoop")
    assert len(archive_ids(cdb, tid)) == 1


def test_old_pending_files_removed_at_start(settings, db):
    """F44: bij het starten van de server gaan tijdelijke bijlagen van meer dan een dag oud weg; nieuwere blijven."""
    import os
    import time
    from fastapi.testclient import TestClient
    from sqlledger_web.main import create_app
    uploads = Path(settings.data_dir) / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    old, new = uploads / f"{'a' * 32}.bin", uploads / f"{'b' * 32}.bin"
    for p in (old, new):
        p.write_bytes(PDF)
    os.utime(old, (time.time() - 2 * 86400,) * 2)
    with TestClient(create_app(settings), base_url="https://testserver"):
        pass
    assert not old.exists() and new.exists()


def test_confidential_label_names_employee(booker, cdb):
    """F42 A: het vinkje noemt de medewerkerlogin waar de bijlage onder komt."""
    tid = new_vendor_booking(booker)
    assert "Vertrouwelijk (alleen voor medewerker jan)" in booker.get(f"/inkoop/{tid}").text
