"""Afletteren in de webapp (fase 2, rc.pl): scherm, live verschil, opslaan, rapport."""

import re
from datetime import date

import psycopg
import pytest
from psycopg.rows import dict_row

from webhelpers import csrf, login, make_user

PERIOD = "rekening=1100&van=2026-03-01&tot=2026-06-30"
# 1100 in de testdata: 950 (-121, geen kenmerk), 3006 BANK-306 (-100), 3005 BANK-305 (-48,40),
# 4002 BANK-402 (+300), 3002 BANK-302 (-50), 3008 KASSA (-24,20), 5001 RENTE (-2,50)


@pytest.fixture
def cdb(settings, company_db):
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture
def booker(client, db, company, cdb):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    return client


def post_form(html):
    """Velden van het afletterformulier (POST) zoals een browser ze verstuurt; ``sel`` als lijst."""
    m = re.search(r'<form method="post" action="/afletteren".*?</form>', html, re.S)
    assert m, "geen afletterformulier"
    data = {"sel": []}
    for tag in re.findall(r"<input\b[^>]*>", m.group(0)):
        name = re.search(r'name="([^"]+)"', tag)
        if not name or " disabled" in tag:
            continue
        value = re.search(r'value="([^"]*)"', tag)
        v = value.group(1) if value else ""
        if 'type="checkbox"' in tag:
            if " checked" in tag:
                data.setdefault(name.group(1), []).append(v)
            continue
        data[name.group(1)] = v
    return data


def tokens(html, *sources):
    """Waarde van het vinkje per regel, op kenmerk (leeg kenmerk: '–')."""
    out = {}
    for row in re.findall(r"<tr[^>]*>.*?</tr>", html, re.S):
        v = re.search(r'name="sel" value="([^"]+)"', row)
        src = re.search(r'target="_blank">([^<]*)</a>', row)
        if v and src:
            out[src.group(1)] = v.group(1)
    return [out[s] for s in sources]


def send(client, html, **data):
    fields = post_form(html)
    fields.update(data)
    return client.post("/afletteren", data={**fields, "csrf": csrf(client, html)}, follow_redirects=False)


def ok(r):
    assert r.status_code == 200, re.sub(r"\s+", " ", r.text)[:3000]
    return r.text


def diff_text(html):
    return re.search(r'id="rc-diff">([^<]*)<', html).group(1)


def cleared(cdb, *tids):
    rows = cdb.execute("SELECT trans_id, cleared FROM acc_trans WHERE chart_id = 10044 AND trans_id = ANY(%s)",
                       (list(tids),)).fetchall()
    return {r["trans_id"]: r["cleared"] for r in rows}


def test_menu_and_rights(booker, client, db, company, cdb):
    home = booker.get("/afletteren").text
    assert '<h2>Afletteren</h2>' in home and 'href="/afletteren/rapport"' in home
    assert 'option value="1100"' in home and 'option value="1140"' not in home
    assert 'option value="1140"' in booker.get("/afletteren?alle=1").text
    db.execute("UPDATE company SET modules = array_remove(modules, 'cash') WHERE id = %s", (company["id"],))
    assert booker.get("/afletteren").status_code == 403
    assert booker.get("/afletteren/rapport").status_code == 403


def test_readonly_sees_report_only(client, db, company, cdb):
    make_user(db, "kijk", memberships=[(company["id"], "readonly", "jan")])
    login(client, "kijk")
    assert client.get("/afletteren").status_code == 403
    page = client.get("/afletteren/rapport").text
    assert "Afgeletterde posten" in page and 'href="/afletteren"' not in page


def test_reconcile_round(booker, cdb, db):
    page = ok(booker.get(f"/afletteren?{PERIOD}"))
    assert "Beginsaldo" in page and "BANK-306" in page and 'name="statementbalance" value="0,00"' in page
    assert diff_text(page) == "0,00"
    t306, t402 = tokens(page, "BANK-306", "BANK-402")
    # live: de server rekent aangevinkt en verschil (activa: debet telt op)
    calc = ok(send(booker, page, sel=[t306, t402], action="calc"))
    assert diff_text(calc) == "-200,00"
    calc = ok(send(booker, page, sel=[t306, t402], statementbalance="-200,00", action="calc"))
    assert diff_text(calc) == "0,00"
    # verschil niet 0: geweigerd, niets opgeslagen
    r = send(booker, page, sel=[t306], statementbalance="99,50", action="save")
    assert r.status_code == 400 and "verschil is niet 0" in r.text
    assert set(cleared(cdb, 3006, 4002).values()) == {None}
    # Opslaan zonder eerst "Bijwerken" (Perl: vinkjes gingen verloren)
    r = send(booker, page, sel=[t306, t402], statementbalance="-200,00", action="save")
    assert r.status_code == 303 and "ok=rc_saved" in r.headers["location"]
    assert cleared(cdb, 3006, 4002, 3005) == {3006: date(2026, 6, 30), 4002: date(2026, 6, 30), 3005: None}
    rec = cdb.execute("SELECT * FROM x_reconciliation").fetchall()
    assert len(rec) == 1 and rec[0]["statementbalance"] == -200 and rec[0]["login"] == "jan"
    assert db.execute("SELECT count(*) AS n FROM audit_log WHERE action = 'reconciled'").fetchone()["n"] == 1
    page = ok(booker.get(r.headers["location"]))
    assert "Afletterronde opgeslagen." in page and 'name="statementbalance" value="-200,00"' in page
    assert "Vorige afletterronde" not in page          # de ronde zelf is niet "vorig" voor deze periode
    nxt = ok(booker.get("/afletteren?rekening=1100"))   # standaardperiode: na de vorige ronde
    assert 'name="van" value="2026-07-01"' in nxt and "Vorige afletterronde" in nxt


def test_without_javascript_select_all(booker, cdb):
    page = ok(booker.get(f"/afletteren?{PERIOD}&weergave=detail&koers=0"))
    allp = ok(send(booker, page, action="select_all"))
    assert len(post_form(allp)["sel"]) == 7
    # som 1100 in de periode: -121 -100 -48,40 +300 -50 -24,20 -2,50 = -46,10 → activa 46,10
    assert diff_text(allp) == "46,10"
    none = ok(send(booker, allp, action="deselect_all"))
    assert post_form(none)["sel"] == [] and diff_text(none) == "0,00"
    r = send(booker, allp, statementbalance="46,10", action="save")
    assert r.status_code == 303
    assert cdb.execute("SELECT count(*) AS n FROM acc_trans WHERE chart_id = 10044 AND cleared = '2026-06-30'"
                       ).fetchone()["n"] == 7


def test_changed_lines_and_bad_input(booker, cdb):
    page = ok(booker.get(f"/afletteren?{PERIOD}"))
    (t306,) = tokens(page, "BANK-306")
    ghost = "999999.20260601."
    r = send(booker, page, sel=[t306, ghost], statementbalance="100,00", action="save")
    assert r.status_code == 409 and "intussen gewijzigd" in r.text
    r = send(booker, page, sel=["kapot"], action="calc")
    assert r.status_code == 400
    assert "Ongeldig afschriftsaldo" in ok(send(booker, page, statementbalance="abc", action="calc"))
    assert booker.get("/afletteren?rekening=1100&van=2026-07-01&tot=2026-06-01").status_code == 400
    assert booker.get("/afletteren?rekening=9999").status_code == 404


def test_many_lines_more_than_1000_fields(booker, cdb):
    cdb.execute("""INSERT INTO gl (id, reference, description, transdate)
                   SELECT 900000 + g, 'M' || g, 'massa', DATE '2026-05-01' + (g % 20) FROM generate_series(1, 1100) g""")
    cdb.execute("""INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, source)
                   SELECT 900000 + g, 10044, -1, DATE '2026-05-01' + (g % 20), 'M' || g FROM generate_series(1, 1100) g""")
    page = ok(booker.get(f"/afletteren?{PERIOD}"))
    allp = ok(send(booker, page, action="select_all"))
    assert len(post_form(allp)["sel"]) == 1107
    r = send(booker, allp, statementbalance="1.146,10", action="save")
    assert r.status_code == 303, r.text[:500]
    assert cdb.execute("SELECT count(*) AS n FROM acc_trans WHERE cleared = '2026-06-30'").fetchone()["n"] == 1107


def test_report_and_export(booker, client, cdb):
    page = ok(booker.get(f"/afletteren?{PERIOD}"))
    t306, t402 = tokens(page, "BANK-306", "BANK-402")
    assert send(booker, page, sel=[t306, t402], statementbalance="-200,00", action="save").status_code == 303
    rep = ok(booker.get(f"/afletteren/rapport?{PERIOD}"))
    assert "BANK-306" in rep and "BANK-402" in rep and "BANK-305" not in rep
    assert "300,00" in rep and "100,00" in rep
    csv = booker.get(f"/afletteren/rapport?{PERIOD}&export=csv")
    assert csv.status_code == 200 and "BANK-402" in csv.text and csv.headers["content-type"].startswith("text/csv")
    xlsx = booker.get(f"/afletteren/rapport?{PERIOD}&export=xlsx")
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"
    pdf = booker.get(f"/afletteren/rapport?{PERIOD}&export=pdf")          # stap 5 (RP1)
    assert pdf.headers["content-type"] == "application/pdf"
    from test_web_reportpdf import pdf_text
    text = pdf_text(pdf.content)
    assert "Afgeletterde posten" in text and "BANK-402" in text and "300,00" in text and "t/m" in text
    assert "export=pdf" in rep
