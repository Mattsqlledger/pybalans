"""Huisstijl (HUISSTIJL_ANALYSE.md HS1–HS12): opslag in x_doc_setting (style.*), contrast, en wat de instellingen
in de HTML van een document doen. Zonder huisstijl is elk document gelijk aan de standaardset."""

import re
import subprocess
from pathlib import Path

import pytest

from sqlledger import docsettings as ds, docstyle
from sqlledger.docstyle import DocStyle
from sqlledger.errors import ValidationError
from sqlledger_web import docfonts
from sqlledger_web.documents import DocumentRenderer

from test_docsettings import sample_vars

ROOT = Path(__file__).resolve().parents[1]


def test_contrast_and_colors():
    assert docstyle.normalize_color("1F4E79") == "#1f4e79"
    assert docstyle.normalize_color("#abc") == "#aabbcc"
    assert docstyle.normalize_color("blauw") is None
    assert round(docstyle.contrast_with_white("#000000"), 1) == 21.0
    assert round(docstyle.contrast_with_white("#ffffff"), 1) == 1.0
    assert docstyle.contrast_with_white("#767676") >= 4.5 > docstyle.contrast_with_white("#777777")   # WCAG-grens
    for color, _ in docstyle.SWATCHES:                           # alle snelkeuzes zijn donker genoeg
        assert docstyle.contrast_with_white(color) >= docstyle.MIN_CONTRAST
    assert docstyle.validate(DocStyle(color="#ffff66")) == [
        "Deze kleur is te licht voor tekst op wit papier; kies een donkerdere tint."]
    assert docstyle.validate(DocStyle(color="#ffff66"), contrast=False) == []
    assert docstyle.validate(DocStyle(color="geel", font="comic", logo_pos="boven", address_pos="midden")) == [
        "Ongeldige keuze voor het logo.", "Onbekend lettertype.", "Ongeldige ligging van het adresblok.",
        "Geef de kleur als hexcode, bijvoorbeeld #1F4E79."]


def test_rows_only_changes():
    assert DocStyle().to_rows() == {}
    assert DocStyle(saved=True).to_rows() == {"style.saved": "1"}
    s = DocStyle(logo_size="large", color="#1f3a5f", font="inter", address_pos="right", show_footer=False, saved=True)
    rows = s.to_rows()
    assert rows == {"style.logo_size": "large", "style.color": "#1f3a5f", "style.font": "inter",
                    "style.address_pos": "right", "style.show_footer": "0", "style.saved": "1"}
    back = DocStyle.from_rows(rows)
    assert back == s
    # kapotte waarden = standaard; een andere style.*-sleutel betekent ook "opgeslagen"
    assert DocStyle.from_rows({"style.font": "comic", "style.color": "x"}) == DocStyle(saved=True)


def test_fonts_match_library():
    assert set(docstyle.FONTS) == {""} | set(docfonts.FONTS)
    for key, f in docfonts.FONTS.items():
        for name, _ in f.files:
            assert docfonts.font_file(key, name) is not None
        assert "SIL Open Font License" in docfonts.license_text(key)
    assert (docfonts.FONT_DIR / "NOTICE.txt").is_file()
    assert docfonts.font_file("inter", "../NOTICE.txt") is None and docfonts.font_file("x", "OFL.txt") is None
    assert docfonts.family("") == docfonts.STANDARD_FAMILY
    assert docfonts.face_css("") == ""
    assert "url(/lettertypen/lora/Lora-Variable.ttf)" in docfonts.face_css("lora", "/lettertypen/")
    assert docfonts.face_css("lora").count("data:font/ttf;base64,") == 1


def test_save_load_audit(conn):
    conn.execute("UPDATE defaults SET fldvalue = '1' WHERE fldname = 'audittrail'")
    assert docstyle.load(conn) == DocStyle()                      # nog geen tabel
    changed = docstyle.save(conn, DocStyle(color="#1F3A5F", font="lora"), login="admin")
    assert changed == ["style.color", "style.font", "style.saved"]
    assert docstyle.load(conn) == DocStyle(color="#1f3a5f", font="lora", saved=True)
    assert ds.load(conn).style == docstyle.load(conn)             # ook via de documentinstellingen
    # ongewijzigd opslaan blijft "opgeslagen" (HS10) en schrijft wel het logboek
    assert docstyle.save(conn, docstyle.load(conn)) == []
    docstyle.save(conn, DocStyle(), source="Holding B.V.")
    assert docstyle.load_rows(conn) == {"style.saved": "1"}
    log = conn.execute("""SELECT reference, formname, action FROM audittrail WHERE tablename = 'x_doc_setting'
                          ORDER BY transdate, reference""").fetchall()
    assert log == [("style", "docstyle", "saved"), ("style", "docstyle", "saved"),
                   ("Holding B.V.", "docstyle", "copied")]
    with pytest.raises(ValidationError):
        docstyle.save(conn, DocStyle(color="#eeeeee"))


def test_docsettings_and_style_leave_each_other_alone(conn):
    docstyle.save(conn, DocStyle(font="inter"))
    s = ds.load(conn)
    s.taxnumber = "NL001234567B01"
    ds.save(conn, s)                                             # mag style.* niet weggooien
    assert docstyle.load(conn).font == "inter"
    docstyle.save(conn, DocStyle(font="lora"))                   # mag de documentinstellingen niet weggooien
    assert ds.load(conn).taxnumber == "NL001234567B01"
    assert ds.load(conn).to_rows() == {"taxnumber": "NL001234567B01"}


def render(tmp_path, style=None, kind="invoice", **extra):
    s = ds.DocSettings(style=style or DocStyle())
    return DocumentRenderer(tmp_path, None, "nl", settings=s).render_html(kind, sample_vars(**extra))


def test_without_style_same_as_before(tmp_path):
    """Zonder opgeslagen huisstijl is de HTML byte-gelijk aan die van het basissjabloon van vóór de huisstijl."""
    old = subprocess.run(["git", "show", "fase4:sqlledger_web/doctemplates/standard/_base.html"], cwd=ROOT,
                         capture_output=True, text=True)
    if old.returncode:
        pytest.skip("branch fase4 niet beschikbaar")
    r = DocumentRenderer(tmp_path, None, "nl")
    for kind in ("invoice", "credit_invoice", "sales_order", "packing_list", "pick_list", "statement", "receipt",
                 "reminder1", "ar_transaction"):
        assert r.render_html(kind, sample_vars(), overrides={"_base.html": old.stdout}) == \
            r.render_html(kind, sample_vars()), kind
    # niet-opgeslagen instellingen (saved=False) tellen niet
    assert render(tmp_path, DocStyle(color="#7a1f2b", font="lora")) == render(tmp_path)


def test_style_in_document(tmp_path):
    html = render(tmp_path, DocStyle(saved=True, color="#1f3a5f", font="inter", logo_pos="center",
                                     logo_size="large", address_pos="right"))
    assert "@font-face" in html and '"sldoc-inter", sans-serif' in html and "&#34;" not in html
    assert ".titlebar h1 { color: #1f3a5f; }" in html
    assert "max-height: 24mm; max-width: 95mm;" in html
    assert ".parties { flex-direction: row-reverse; }" in html
    assert '<div class="head pos-center">' in html
    plain = render(tmp_path, DocStyle(saved=True))
    assert "@font-face" not in plain and '"Helvetica Neue", Helvetica' in plain
    assert "row-reverse" not in plain and '<div class="head pos-left">' in plain


def test_logo_positions(tmp_path):
    folder = tmp_path / "templates" / "1"
    folder.mkdir(parents=True)
    (folder / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 20)

    def order(pos):
        s = ds.DocSettings(style=DocStyle(saved=True, logo_pos=pos))
        html = DocumentRenderer(tmp_path, 1, "nl", settings=s).render_html("invoice", sample_vars(tel="1"))
        head = html[html.index('<div class="head'):html.index("</table>", html.index('<div class="head'))]
        return [m for m in re.findall(r'class="(logo|company)"|<(table)>', head) for m in m if m]

    assert order("left") == ["logo", "company", "table"]
    assert order("center") == ["company", "logo", "table"]
    s = ds.DocSettings(style=DocStyle(saved=True, logo_pos="right"))
    html = DocumentRenderer(tmp_path, 1, "nl", settings=s).render_html("invoice", sample_vars(tel="1"))
    assert html.index("</table>") < html.index('<div class="logo">')


def test_head_and_footer_off(tmp_path):
    html = render(tmp_path, DocStyle(saved=True, show_head=False, show_footer=False))
    assert '<div class="head pos-left blank">' in html and "<strong>Koek BV</strong>" not in html
    assert '<div id="pagefooter"></div>' in html
    assert "Koek BV · Straat 1" in html                          # retourregel blijft (Documentinstellingen)


def test_custom_template_gets_style_variables(tmp_path):
    folder = tmp_path / "templates" / "1"
    folder.mkdir(parents=True)
    (folder / "invoice.html").write_text("<p>{{ style.set }} {{ style.color }} {{ style.font }} {{ style.logo_height }}"
                                         " {{ style.logo_pos }} {{ style.address_pos }} {{ style.show_head }}</p>")
    s = ds.DocSettings(style=DocStyle(saved=True, color="#0f5560", font="lora", logo_size="small"))
    html = DocumentRenderer(tmp_path, 1, "nl", settings=s).render_html("invoice", sample_vars())
    assert html == '<p>True #0f5560 "sldoc-lora", serif 14 left left True</p>'


@pytest.mark.parametrize("pos,size", [("left", "large"), ("center", "medium"), ("right", "small")])
def test_address_in_window(tmp_path, pos, size):
    """HS12 A: met huisstijl staat het adresblok (incl. retourregel) op 45 mm van de bovenkant, ook met logo."""
    from weasyprint import HTML

    from sqlledger_web.documents import _fetcher
    folder = tmp_path / "templates" / "1"
    folder.mkdir(parents=True)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="100"><rect width="300" height="100"/></svg>'
    (folder / "logo.svg").write_bytes(svg)
    s = ds.DocSettings(style=DocStyle(saved=True, logo_pos=pos, logo_size=size, font="source-serif-4"))
    v = sample_vars(address="Straat 1\nPostbus 12\n1234 AB Gouda\nNederland", tel="0182-123456",
                    companyemail="info@koek.nl")
    doc = HTML(string=DocumentRenderer(tmp_path, 1, "nl", settings=s).render_html("invoice", v),
               url_fetcher=_fetcher()).render()

    def find(box, cls):
        el = getattr(box, "element", None)
        if el is not None and cls in (el.get("class") or "").split():
            return box
        for c in getattr(box, "children", None) or []:
            hit = find(c, cls)
            if hit is not None:
                return hit
        return None

    addr = find(doc.pages[0]._page_box, "addr")
    assert abs((addr.position_y + addr.margin_top) * 25.4 / 96 - 45.0) < 0.2
