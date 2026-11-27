"""Documentinstellingen (fase 4 stap 3): opslag in x_doc_setting, standaardwaarden, controle, audittrail,
en wat de instellingen in de HTML van een document doen (DocumentRenderer, zonder database)."""

import pytest

from sqlledger import docsettings as ds
from sqlledger.errors import ValidationError
from sqlledger_web.documents import DocumentRenderer, doc_context, light_markup


def add_bank(conn, accno="1100", iban="NL91ABNA0417164300", dcn=None):
    conn.execute("""INSERT INTO bank (id, name, iban, bic, dcn) SELECT id, 'Interbank NV', %s, 'ABNANL2A', %s
                    FROM chart WHERE accno = %s""", (iban, dcn, accno))


def test_defaults_without_table(conn):
    assert conn.execute("SELECT to_regclass('x_doc_setting')").fetchone()[0] is None
    s = ds.load(conn)
    assert s.is_default() and s.payinfo and s.return_line and s.bank_mode == ds.BANK_BOOKING
    assert s.column("sales_invoice", "discount").mode == ds.AUTO
    assert s.column("sales_invoice", "deliverydate").mode == ds.NOTE
    assert s.column("packing_list", "package").mode == ds.AUTO
    assert s.column("pick_list", "bin").mode == ds.AUTO
    assert s.column("sales_invoice", "barcode").mode == ds.OFF


def test_save_load_only_changes(conn):
    add_bank(conn)
    s = ds.load(conn)
    s.taxnumber = "NL001234567B01"
    s.bank_mode, s.bank_accno, s.bank_name, s.payinfo = ds.BANK_FIXED, "1100", True, False
    s.return_line = False
    s.texts["nl"] = {"footer": "Voet **vet**", "closing:invoice": "Bedankt!", "note:invoice": "Korting over colli."}
    s.texts["de"] = {"closing:invoice": "Danke!"}
    s.columns["sales_invoice"] = {"barcode": ds.Column(ds.COLUMN, {"nl": "EAN-code"}),
                                  "discount": ds.Column(ds.AUTO),            # = standaard, niet opgeslagen
                                  "unit": ds.Column(ds.COLUMN, {"nl": "Eenh.Collo"})}
    s.weights["sales_invoice"] = True
    changed = ds.save(conn, s, login="admin")
    rows = dict(conn.execute("SELECT key, value FROM x_doc_setting").fetchall())
    assert set(changed) == set(rows)
    assert set(rows) == {"taxnumber", "bank", "return_line", "text:nl:footer", "text:nl:closing:invoice",
                         "text:nl:note:invoice", "text:de:closing:invoice", "columns:sales_invoice",
                         "weights:sales_invoice"}
    assert "discount" not in rows["columns:sales_invoice"]
    back = ds.load(conn)
    assert back == s or back.to_rows() == s.to_rows()
    assert back.column("sales_invoice", "unit").labels == {"nl": "Eenh.Collo"}
    assert back.text("de", "closing", "invoice") == "Danke!" and back.text("en", "closing", "invoice") == ""
    # niets gewijzigd: geen wijzigingen; terug naar standaard: tabel leeg
    assert ds.save(conn, back, login="admin") == []
    assert sorted(ds.save(conn, ds.DocSettings(), login="admin")) == sorted(rows)
    assert conn.execute("SELECT count(*) FROM x_doc_setting").fetchone()[0] == 0


def test_audittrail(conn):
    s = ds.DocSettings(taxnumber="NL1")
    ds.save(conn, s, login="admin")
    r = conn.execute("""SELECT tablename, reference, formname, action FROM audittrail
                        WHERE tablename = 'x_doc_setting'""").fetchall()
    assert r == [("x_doc_setting", "taxnumber", "docsettings", "saved")]


@pytest.mark.parametrize("change, message", [
    (lambda s: setattr(s, "taxnumber", "X" * 41), "Het btw-nummer is te lang."),
    (lambda s: (setattr(s, "bank_mode", ds.BANK_FIXED), setattr(s, "bank_accno", "1100")),
     "Kies een bankrekening met bankgegevens."),
    (lambda s: s.texts.setdefault("nl", {}).update(footer="x" * 2001), "Een tekst is te lang (maximaal 2000 tekens)."),
    (lambda s: s.texts.setdefault("nl", {}).update({"closing:nonsense": "x"}), "Onbekende tekst."),
    (lambda s: s.texts.update(xx={"footer": "x"}), "Onbekende documenttaal."),
    (lambda s: s.columns.update(sales_invoice={"number": ds.Column(ds.NOTE)}), "Ongeldige kolominstelling."),
    (lambda s: s.columns.update(packing_list={"sellprice": ds.Column(ds.COLUMN)}), "Ongeldige kolominstelling."),
    (lambda s: s.columns.update(order={"barcode": ds.Column(ds.COLUMN, {"nl": "a\nb"})}),
     "Een kolomnaam is te lang (maximaal 60 tekens, één regel)."),
])
def test_validate(conn, change, message):
    s = ds.DocSettings()
    change(s)
    assert message in ds.validate(conn, s)
    with pytest.raises(ValidationError):
        ds.save(conn, s)


def test_fixed_bank_needs_iban(conn):
    add_bank(conn, iban="")
    s = ds.DocSettings(bank_mode=ds.BANK_FIXED, bank_accno="1100")
    assert ds.validate(conn, s) == ["Deze bankrekening heeft geen IBAN."]


def test_from_rows_skips_garbage():
    s = ds.DocSettings.from_rows({"bank": "{kapot", "columns:sales_invoice": "[]", "columns:onbekend": "{}",
                                  "text:xx:footer": "a", "text:nl:closing:onbekend": "b", "weights:order": "1",
                                  "columns:order": '{"barcode": {"mode": "raar"}, "hscode": {"mode": "column"}}'})
    assert s.bank_mode == ds.BANK_BOOKING and s.texts == {} and s.weights == {"order": True}
    assert s.column("order", "barcode").mode == ds.OFF and s.column("order", "hscode").mode == ds.COLUMN


def test_groups_cover_kinds():
    from sqlledger_web import doctypes
    assert set(ds.KINDS) == set(doctypes.TEMPLATE_KINDS)
    assert set(ds.LETTER_KINDS) == set(doctypes.LETTERS) | set(doctypes.PAYMENT_DOCS)
    assert {k for kinds in ds.GROUPS.values() for k in kinds} == {k for k, v in doctypes.KINDS.items()
                                                                if v.source in ("invoice", "order")}


# ---------------------------------------------------------------- wat het doet in het document

def test_light_markup_escapes():
    html = str(light_markup("<b>x</b> **vet** *schuin* [site](https://example.org) [x](javascript:alert(1))\nregel 2"))
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<strong>vet</strong>" in html and "<em>schuin</em>" in html
    assert '<a href="https://example.org">site</a>' in html and "javascript" in html and "<a href=\"javascript" not in html
    assert "<br>regel 2" in html
    assert str(light_markup('**"<script>"**')) == "<strong>&#34;&lt;script&gt;&#34;</strong>"


def sample_vars(**extra):
    lines = {"runningnumber": ["1", "2"], "number": ["A-1", "B-2"], "description": ["Appels", "Peren"],
             "qty": ["2", "1"], "unit": ["kist", "doos"], "sellprice": ["1,00", "2,00"], "linetotal": ["2,00", "2,00"],
             "discountrate": ["", ""], "barcode": ["8712345678906", ""], "countryorigin": ["0,0125 kg", ""],
             "serialnumber": ["THT 01-2027", ""], "deliverydate": ["02-07-26", "01-07-26"], "package": ["3", ""],
             "grossweight": ["4,2", ""], "netweight": ["8,4", ""], "bin": ["", ""], "itemnotes": ["", ""], "bom": ["", ""]}
    v = {"company": "Koek BV", "address": "Straat 1\n1234 AB Gouda", "businessnumber": "123", "invnumber": "F1",
         "invdate": "01-07-26", "duedate": "31-07-26", "name": "Klant", "address1": "Weg 2", "zipcode": "1000 AA",
         "city": "Utrecht", "currency": "EUR", "subtotal": "4,00", "invtotal": "4,84", "total": "4,84", "paid": "",
         "iban": "NL02RABO0123456789", "bic": "RABONL2U", "dcn": "", "totalgrossweight": "4,2",
         "totalnetweight": "8,4", "weightunit": "kg", "language_code": "", "vc": "customer", **lines}
    v.update(extra)
    return v


def render(tmp_path, s, kind="invoice", lang="nl", banks=(), **extra):
    return DocumentRenderer(tmp_path, None, lang, settings=s, banks=banks).render_html(kind, sample_vars(**extra))


def test_default_document(tmp_path):
    html = render(tmp_path, ds.DocSettings())
    assert html == render(tmp_path, None)
    assert '<div class="return">Koek BV · Straat 1 · 1234 AB Gouda</div>' in html
    assert "Dank voor uw bestelling." in html and "Graag betalen vóór" in html
    assert "<th>EAN</th>" not in html and '<div class="note">Leverdatum 02-07-26</div>' in html
    assert '<div class="note">THT 01-2027</div>' in html and "Leverdatum 01-07-26" not in html   # = factuurdatum


def test_company_bank_texts(tmp_path):
    s = ds.DocSettings(taxnumber="NL001234567B01", bank_name=True, return_line=True)
    s.texts["nl"] = {"return_line": "Postbus 5, Gouda", "closing:invoice": "Tot **ziens**",
                     "note:invoice": "Kortingspercentage wordt over colli berekend."}
    banks = [{"accno": "1100", "iban": "NL02RABO0123456789", "bic": "RABONL2U", "name": "Rabobank", "membernumber": "",
              "dcn": "", "rvc": ""}]
    html = render(tmp_path, s, banks=banks)
    assert "Btw-nummer NL001234567B01" in html and "Rabobank IBAN NL02RABO0123456789" in html
    assert '<div class="return">Postbus 5, Gouda</div>' in html and "Tot <strong>ziens</strong>" in html
    assert "Dank voor uw bestelling." not in html and "bij Rabobank" in html
    assert '<div class="docnote">Kortingspercentage wordt over colli berekend.</div>' in html
    # andere taal: eigen teksten gelden niet, standaard wel
    en = render(tmp_path, s, lang="en", banks=banks)
    assert "Thank you for your order." in en and "VAT number NL001234567B01" in en
    # uit: retourregel, betaalzin; eigen voet vervangt de standaardvoet
    s2 = ds.DocSettings(return_line=False, payinfo=False)
    s2.texts["nl"] = {"footer": "Alleen dit"}
    html = render(tmp_path, s2)
    assert 'class="return"' not in html and "Graag betalen vóór" not in html
    assert '<div id="pagefooter">Alleen dit</div>' in html


def test_fixed_bank_and_dcn(tmp_path):
    s = ds.DocSettings(bank_mode=ds.BANK_FIXED, bank_accno="1200")
    banks = [{"accno": "1200", "iban": "NL91ABNA0417164300", "bic": "ABNANL2A", "name": "ABN", "membernumber": "",
              "dcn": "<%invnumber%>X", "rvc": ""}]
    html = render(tmp_path, s, banks=banks)
    assert "NL91ABNA0417164300" in html and "NL02RABO0123456789" not in html
    assert "onder vermelding van <strong>11X</strong>" in html    # kenmerk volgens het sjabloon van de vaste rekening


def test_closing_on_credit_invoice(tmp_path):
    s = ds.DocSettings()
    assert '<div class="closing">' not in render(tmp_path, s, "credit_invoice")
    s.texts["nl"] = {"closing:credit_invoice": "Excuses voor het ongemak."}
    assert '<div class="closing">Excuses voor het ongemak.</div>' in render(tmp_path, s, "credit_invoice")
    assert "Excuses" not in render(tmp_path, s, "invoice")


def test_columns_pcc_like(tmp_path):
    """Zoals de oude factuur van administratie A: EAN, eigen artikelcode, houdbaarheid als kolom, pallet, eenheid als colli,
    'Netto per CE:' onder de omschrijving, geen artikelnummer, gewichtsoverzicht."""
    s = ds.DocSettings()
    s.columns["sales_invoice"] = {
        "number": ds.Column(ds.OFF), "barcode": ds.Column(ds.COLUMN),
        "countryorigin": ds.Column(ds.NOTE, {"nl": "Netto per CE:"}),
        "deliverydate": ds.Column(ds.COLUMN, {"nl": "Levering"}), "serialnumber": ds.Column(ds.COLUMN, {"nl": "Houdbaar"}),
        "package": ds.Column(ds.COLUMN, {"nl": "Pallet", "de": "Palette"}), "unit": ds.Column(ds.COLUMN, {"nl": "Eenh.Collo"}),
        "discount": ds.Column(ds.COLUMN)}
    s.weights["sales_invoice"] = True
    html = render(tmp_path, s)
    heads = [h for h in ("EAN", "Omschrijving", "Levering", "Houdbaar", "Pallet", "Aantal", "Eenh.Collo", "Prijs",
                         "Korting", "Bedrag") if f">{h}</th>" in html]
    assert len(heads) == 10 and html.index(">EAN</th>") < html.index(">Omschrijving</th>") < html.index(">Pallet</th>")
    assert ">Artikelnr.</th>" not in html and "<td class=\"num\">A-1</td>" not in html
    assert '<div class="note">Netto per CE: 0,0125 kg</div>' in html
    assert "<td>01-07-26</td>" in html             # als kolom ook als die gelijk is aan de factuurdatum
    assert 'class="lines weights"' in html and "8,4 kg" in html
    de = render(tmp_path, s, lang="de")
    assert ">Palette</th>" in de and ">Eenh.Collo</th>" not in de and ">Einheit</th>" in de
    # automatisch: alleen als een regel het veld heeft
    s.columns["sales_invoice"]["hscode"] = ds.Column(ds.AUTO)
    assert "Goederencode" not in render(tmp_path, s)


def test_packing_and_pick_list(tmp_path):
    s = ds.DocSettings()
    html = render(tmp_path, s, "packing_list")
    assert ">Verpakking</th>" in html and ">Gewicht</th>" in html and "Totaal gewicht" in html
    s.columns["packing_list"] = {"grossweight": ds.Column(ds.OFF), "barcode": ds.Column(ds.COLUMN)}
    html = render(tmp_path, s, "packing_list")
    assert ">Gewicht</th>" not in html and "Totaal gewicht" not in html and ">EAN</th>" in html
    s.columns["pick_list"] = {"bin": ds.Column(ds.COLUMN, {"nl": "Vak"})}
    assert ">Vak</th>" in render(tmp_path, s, "pick_list")


def test_doc_in_own_template(tmp_path):
    """Eigen geüpload sjabloon: dezelfde instellingen als doc.* (DI uitwerking 8)."""
    s = ds.DocSettings(taxnumber="NL9")
    s.texts["nl"] = {"closing:invoice": "Slot"}
    r = DocumentRenderer(tmp_path, None, "nl", settings=s)
    tpl = "{{ doc.taxnumber }}|{{ doc.closing }}|{% for c in doc.columns %}{{ c.name }},{% endfor %}"
    html = r.render_html("invoice", sample_vars(), overrides={"invoice.html": tpl})
    assert html == "NL9|Slot|number,description,qty,unit,sellprice,linetotal,"


def test_doc_context_for_transaction():
    doc = doc_context(ds.DocSettings(), "ar_transaction", "nl", lambda x: x, [])
    assert doc["group"] == "" and doc["columns"] == [] and doc["payinfo"]
