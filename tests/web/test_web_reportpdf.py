"""Rapporten en lijsten als PDF (fase 4 stap 5, D14, RP1–RP10).

De PDF moet dezelfde bedragen bevatten als het scherm: de tekst van de PDF (pypdf) wordt vergeleken met de
bedragen in de schermtabel, die op hun beurt tegen Perl bewezen zijn (test_web_reports).
"""

import re
from collections import Counter
from datetime import datetime
from io import BytesIO

import pytest
from pypdf import PdfReader

from sqlledger_web import reportpdf, routes_reports
from sqlledger_web.reportdefs import REPORTS
from sqlledger_web.reportpdf import Layout, choose_layout, render_pdf
from sqlledger_web.tables import Col, Row, Table, cell_text
from test_web_reports import Y2026, table_rows, web_amounts
from webhelpers import login, make_user


@pytest.fixture
def reader(client, db, company):
    make_user(db, "lees", memberships=[(company["id"], "readonly")])
    login(client, "lees")
    assert client.get("/").status_code == 200
    return client


@pytest.fixture
def booker(client, db, company):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper")])
    login(client, "boek")
    return client


def pdf_text(content: bytes) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(BytesIO(content)).pages)


def pdf_pages(content: bytes):
    return PdfReader(BytesIO(content)).pages


def is_landscape(content: bytes) -> bool:
    box = pdf_pages(content)[0].mediabox
    return box.width > box.height


def assert_same_amounts(screen: str, content: bytes):
    """Elk bedrag van het scherm komt (minstens zo vaak) in de PDF voor."""
    want = Counter(a for row in web_amounts(screen) for a in row)
    have = Counter(pdf_text(content).split())
    missing = want - have
    assert not missing, missing


# --- alle rapporten (RP1) --------------------------------------------------------------

def test_every_report_as_pdf(reader, monkeypatch):
    monkeypatch.setattr(routes_reports, "PAGE_SIZE", 100000)       # scherm toont alles, PDF ook
    for key, rep in REPORTS.items():
        params = {**Y2026, "accno": "1100"}
        screen = reader.get(f"/rapporten/{key}", params=params).text
        r = reader.get(f"/rapporten/{key}", params={**params, "export": "pdf"})
        assert r.status_code == 200, key
        assert r.headers["content-type"] == "application/pdf", key
        assert f'filename="{key}.pdf"' in r.headers["content-disposition"], key
        text = pdf_text(r.content)
        assert rep.title in text, key
        assert "Afgedrukt op" in text and "Pagina 1 / " in text, key
        assert PdfReader(BytesIO(r.content)).metadata.creator.startswith("PyBalans "), key      # v1.0.1 NB1
        assert_same_amounts(screen, r.content)


@pytest.mark.parametrize("nf", ["1.000,00", "1,000.00", "1'000.00"])
def test_amounts_in_user_notation(reader, db, nf):
    db.execute("UPDATE app_user SET numberformat = %s WHERE login = 'lees'", (nf,))
    params = {**Y2026, "l_heading": "1", "l_subtotal": "1"}
    screen = reader.get("/rapporten/proefbalans", params=params).text
    pdf = reader.get("/rapporten/proefbalans", params={**params, "export": "pdf"}).content
    assert web_amounts(screen)
    assert_same_amounts(screen, pdf)


def test_all_rows_not_only_the_screen_page(reader, monkeypatch):
    """RP2: de PDF bevat alle regels, ook als het scherm in pagina's is verdeeld."""
    monkeypatch.setattr(routes_reports, "PAGE_SIZE", 3)
    params = {"run": "1", "period": "", "accno": "1200", "fromdate": "2026-03-05"}
    first = reader.get("/rapporten/rekeningmutaties", params=params).text
    assert "pagina 1 /" in first
    pdf = reader.get("/rapporten/rekeningmutaties", params={**params, "export": "pdf"}).content
    monkeypatch.setattr(routes_reports, "PAGE_SIZE", 100000)
    everything = reader.get("/rapporten/rekeningmutaties", params=params).text
    assert len(table_rows(everything)) > len(table_rows(first))
    assert_same_amounts(everything, pdf)


def test_too_many_rows(reader, db, monkeypatch):
    """RP2: boven de grens een melding met een weg terug, geen PDF en geen logregel."""
    monkeypatch.setattr(reportpdf, "MAX_ROWS", 3)
    r = reader.get("/rapporten/journaal", params={**Y2026, "export": "pdf"})
    assert r.status_code == 413 and "text/html" in r.headers["content-type"]
    assert "een PDF kan er hoogstens 3" in r.text and "gebruik Excel" in r.text
    back = re.search(r'<a class="button secondary" href="([^"]+)">Terug', r.text).group(1)
    assert back.startswith("/rapporten/journaal?") and "export" not in back and "run=1" in back
    assert not db.execute("SELECT 1 FROM audit_log WHERE action = 'report_exported'").fetchall()


def test_logged_like_csv(reader, db):
    """RP7: alleen een regel in het logboek, zoals CSV/Excel."""
    reader.get("/rapporten/balans", params={"run": "1", "export": "pdf"})
    rows = db.execute("SELECT action, detail FROM audit_log WHERE action = 'report_exported'").fetchall()
    assert len(rows) == 1 and rows[0]["detail"]["format"] == "pdf" and rows[0]["detail"]["report"] == "balans"


def test_header_on_every_page_and_footer(reader, monkeypatch):
    """RP3: administratie, titel en periode op elke pagina; kolomkoppen herhaald; paginanummer."""
    monkeypatch.setattr(reportpdf, "MAX_ROWS", 100000)
    cols = [Col("d", "Datum", "date"), Col("t", "Omschrijving"), Col("a", "Bedrag", "amount")]
    rows = [Row("row", {"d": datetime(2026, 1, 1).date(), "t": f"regel {i}", "a": i}) for i in range(400)]
    rows.append(Row("total", {"t": "Totaal", "a": sum(range(400))}))
    pdf = render_pdf([Table(cols, rows)], "Proef", ["1-1-2026 t/m 31-12-2026"], "Bakkerij BV", "1.000,00",
                     "dd-mm-yyyy", now=datetime(2026, 10, 11, 3, 4))
    pages = pdf_pages(pdf)
    assert len(pages) > 3
    for n, p in enumerate(pages, start=1):
        t = p.extract_text()
        assert "Proef" in t and "Bakkerij BV" in t and "1-1-2026 t/m 31-12-2026" in t
        assert "Datum" in t and "Omschrijving" in t and "Bedrag" in t
        assert f"Pagina {n} / {len(pages)}" in t
        assert "Afgedrukt op 11-10-2026 03:04" in t
    assert "79.800,00" in pages[-1].extract_text()


def test_no_links_and_escaped(reader, db, settings, company_db):
    """Namen worden niet als HTML/CSS uitgevoerd; geen links in de PDF."""
    import psycopg
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True) as c:
        c.execute("UPDATE customer SET name = '<b>Bakkerij</b> \"Cees\" & Co' WHERE id = 900")
    r = reader.get("/rapporten/overzicht-verkoop", params={**Y2026, "export": "pdf"})
    assert '<b>Bakkerij</b> "Cees" & Co' in pdf_text(r.content)
    for p in pdf_pages(r.content):
        assert not p.get("/Annots")


# --- staand/liggend en lettergrootte (RP4) --------------------------------------------------

def test_layout_follows_width(reader):
    pdf = reader.get("/rapporten/proefbalans", params={**Y2026, "export": "pdf"}).content
    assert not is_landscape(pdf)
    params = {"run": "1", "period": "year", "year": "2026", "includeperiod": "month", "previousyear": "1"}
    screen = reader.get("/rapporten/winst-en-verlies", params=params).text
    pdf = reader.get("/rapporten/winst-en-verlies", params={**params, "export": "pdf"}).content
    assert is_landscape(pdf)
    assert_same_amounts(screen, pdf)


def _layout(widths, kinds=None):
    kinds = kinds or ["text"] * len(widths)
    cols = [Col(f"c{i}", "K", k) for i, k in enumerate(kinds)]
    texts = [["x" * w for w in widths]]
    return choose_layout([Table(cols, [Row("row", {})])], [texts])


def test_choose_layout():
    assert _layout([10, 30, 12, 12, 12, 12]) == Layout(False, 8.0)
    assert _layout([12] * 10, ["amount"] * 10) == Layout(True, 8.0)
    wide = _layout([12] * 26, ["amount"] * 26)
    assert wide.landscape and 6.5 <= wide.font_pt < 8
    # v1.0.1 PB28: past het liggend op 6,5 pt niet, dan kolomblokken in plaats van kleiner/afgekapt
    many = _layout([14] * 60, ["amount"] * 60)
    assert many.landscape and 6.5 <= many.font_pt <= 8 and len(many.parts[0]) == 5
    assert [i for p in many.parts[0] for i in p] == list(range(60))
    # lange omschrijvingen tellen afgetopt mee: geen liggend alleen door één lange tekst
    assert _layout([10, 300, 12, 12]) == Layout(False, 8.0)


def test_wide_table_in_column_blocks():
    """PB28: rekening en omschrijving in elk blok, de bedragkolommen in volgorde verdeeld (paren bij elkaar),
    het totaal (laatste kolom) in het laatste blok."""
    lay = _layout([6, 30] + [10] * 26, ["text", "text"] + ["amount"] * 26)
    assert lay.landscape and lay.parts and 6.5 <= lay.font_pt <= 8
    blocks = lay.parts[0]
    assert len(blocks) == 2 and all(b[:2] == [0, 1] for b in blocks)
    assert [i for b in blocks for i in b[2:]] == list(range(2, 28)) and blocks[-1][-1] == 27
    assert all(len(b[2:]) % 2 == 0 for b in blocks)
    # past het wel (liggend, eventueel kleiner): niets gesplitst
    assert _layout([12] * 10, ["amount"] * 10).parts is None


def test_wide_pdf_repeats_text_columns(reader):
    """PB28 in de PDF zelf: W&V per maand met vorig jaar (26 bedragkolommen) in blokken; elk blok heeft de
    kolom Rekening en de koppen; het totaal staat in het laatste blok."""
    params = {"run": "1", "period": "year", "year": "2026", "includeperiod": "month", "previousyear": "1",
              "export": "pdf"}
    text = pdf_text(reader.get("/rapporten/winst-en-verlies", params=params).content)
    assert "deel 1 van" in text and "deel 2 van" in text


# --- taal (RP5) en rechten (RP9) -------------------------------------------------------------

@pytest.mark.parametrize("lang,title,page,printed", [
    ("en", "Balance sheet", "Page 1 / ", "Printed on"),
    ("de", "Bilanz", "Seite 1 / ", "Gedruckt am"),
    ("fr", "Bilan", "Page 1 / ", "Imprimé le"),
])
def test_language_of_the_user(reader, db, lang, title, page, printed):
    db.execute("UPDATE app_user SET language = %s WHERE login = 'lees'", (lang,))
    text = pdf_text(reader.get("/rapporten/balans", params={"run": "1", "export": "pdf"}).content)
    assert title in text and page in text and printed in text


def test_rights_follow_the_screen(reader, db, company, client):
    db.execute("UPDATE company SET modules = %s WHERE id = %s", (["gl", "reports"], company["id"]))
    assert reader.get("/rapporten/ouderdom-verkoop", params={**Y2026, "export": "pdf"}).status_code == 403
    assert reader.get("/rapporten/proefbalans", params={**Y2026, "export": "pdf"}).status_code == 200
    client.cookies.clear()
    r = client.get("/rapporten/proefbalans", params={**Y2026, "export": "pdf"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_buttons_on_screens(reader):
    page = reader.get("/rapporten/proefbalans", params=Y2026).text
    assert re.search(r'href="/rapporten/proefbalans\?[^"]*export=pdf"[^>]*target="_blank"', page)
    assert "data-print" in page                    # RP8: Afdrukken blijft


# --- overige lijsten (RP1) ---------------------------------------------------------------------

LISTS = [
    "/klanten?run=1&status=all",
    "/leveranciers?run=1",
    "/artikelen?run=1&status=all",
    "/verkooporders?run=1",
    "/artikelen-stuklijsten?run=1",
]


@pytest.mark.parametrize("url", LISTS)
def test_lists_as_pdf(booker, url):
    screen = booker.get(url)
    assert screen.status_code == 200
    assert "export=pdf" in screen.text
    r = booker.get(url + "&export=pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert "Afgedrukt op" in pdf_text(r.content)


def test_booking_page_as_pdf(reader, db, company):
    db.execute("UPDATE company SET modules = %s WHERE id = %s", (["gl", "reports"], company["id"]))
    page = reader.get("/boekingen/3007").text          # verkoop uit: leespagina
    assert "export=pdf" in page
    r = reader.get("/boekingen/3007?export=pdf")
    assert r.headers["content-type"] == "application/pdf"
    text = pdf_text(r.content)
    assert "F-3007" in text and "Bakkerij Cees" in text and "Totaal" in text
