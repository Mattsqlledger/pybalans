"""Documenten als PDF in de webapp (fase 4, D7/D8)."""

import pytest

from test_web_arap import step
from test_web_is import add_item, cdb, posted_id, setup, start  # noqa: F401 (fixtures)
from webhelpers import login, make_user


@pytest.fixture
def booker(client, db, company, settings, tmp_path):
    settings.data_dir = str(tmp_path)
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    return client


def make_invoice(client) -> int:
    page = start(client, "K001")
    page = add_item(client, page, "A-1", qty="2")
    return posted_id(step(client, "/verkoopfactuur", {"action": "post"}, page))


def test_pdf_from_invoice_screen(booker, cdb, company, tmp_path):
    sid = make_invoice(booker)
    page = booker.get(f"/verkoopfactuur/{sid}").text
    assert f'href="/document/invoice/{sid}.pdf"' in page
    r = booker.get(f"/document/invoice/{sid}.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content[:5] == b"%PDF-" and r.headers["content-disposition"].startswith("inline")
    from io import BytesIO
    from pypdf import PdfReader
    assert PdfReader(BytesIO(r.content)).metadata.creator.startswith("PyBalans ")         # v1.0.1 NB1
    assert cdb.execute("SELECT printed, emailed FROM status WHERE trans_id = %s AND formname = 'invoice'",
                       (sid,)).fetchone() == {"printed": True, "emailed": False}
    assert booker.get(f"/document/invoice/{sid}.pdf?download=1").headers["content-disposition"].startswith("attachment")
    # eigen sjabloon van de administratie gaat voor de standaardset
    d = tmp_path / "templates" / str(company["id"])
    d.mkdir(parents=True)
    (d / "invoice.html").write_text("<p>Eigen {{ invnumber }}</p>")
    assert booker.get(f"/document/invoice/{sid}.pdf").status_code == 200


def test_pdf_errors(booker, cdb):
    sid = make_invoice(booker)
    assert booker.get(f"/document/onzin/{sid}.pdf").status_code == 404
    assert booker.get(f"/document/vendor_invoice/{sid}.pdf").status_code == 404      # is een verkoopfactuur
    assert booker.get("/document/invoice/999999.pdf").status_code == 404
    assert booker.get(f"/document/invoice/{sid}.pdf?variant=../x").status_code == 400


def test_readonly_can_view_without_status(client, db, company, cdb, settings, tmp_path):
    settings.data_dir = str(tmp_path)
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    make_user(db, "lees", memberships=[(company["id"], "readonly", "lees")])
    login(client, "boek")
    sid = make_invoice(client)
    client.cookies.clear()
    login(client, "lees")
    assert client.get(f"/document/invoice/{sid}.pdf").status_code == 200
    assert cdb.execute("SELECT count(*) AS n FROM status WHERE trans_id = %s", (sid,)).fetchone()["n"] == 0
    assert f'href="/document/invoice/{sid}.pdf"' in client.get(f"/verkoopfactuur/{sid}").text
