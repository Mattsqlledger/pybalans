"""Bijlagen bij boekingen (fase 4 stap 4e, D17/IU6): opslag in reference/archive/archivedata
met dezelfde codering als SQL-Ledger (Form::save_reference: blokken van 512 bytes, pack 'u')."""

import os
import shutil
import subprocess
from datetime import date
from decimal import Decimal

import pytest
from psycopg.rows import dict_row

from sqlledger import ArapLedger, ArapLine, ArapTransaction, GeneralLedger, Reference, UserConfig
from sqlledger.attachments import (MAX_SIZE, MB, Attachments, check_file, decode, detect_type, encode,
                                   max_size, safe_filename, setting_mb)
from sqlledger.errors import NotFoundError, ValidationError
from sqlledger.models import GLTransaction, JournalLine

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
VEND = 901


@pytest.fixture
def cur(conn):
    with conn.cursor(row_factory=dict_row) as c:
        yield c


@pytest.fixture(autouse=True)
def old_rates(conn):
    conn.execute("UPDATE tax SET validto = '2025-12-31' WHERE taxnumber IS NULL")


def big_pdf(n):
    return (PDF + os.urandom(n))[:n]


# ------------------------------------------------------------------ codering

def test_encode_like_perl_pack_u():
    assert encode(b"\x00" * 6) == ["&" + "`" * 8 + "\n"]
    rows = encode(b"a" * 1300)
    assert len(rows) == 3                                   # 512 + 512 + 276
    assert rows[0].count("\n") == 12                        # 11 x 45 + 17 bytes
    assert decode(rows) == b"a" * 1300
    data = os.urandom(5000)
    assert decode(encode(data)) == data
    assert encode(b"") == []


@pytest.mark.skipif(not shutil.which("perl"), reason="geen perl")
def test_perl_reads_python_rows_and_back():
    data = os.urandom(2000)
    rows = encode(data)
    script = 'local $/; my @r = split /\\x01/, <STDIN>; binmode STDOUT; print map { unpack "u", $_ } @r'
    out = subprocess.run(["perl", "-e", script], input="\x01".join(rows).encode(), capture_output=True,
                         check=True).stdout
    assert out == data
    script = ('binmode STDIN; local $/; my $d = <STDIN>; my @r; '
              'while (length $d) { push @r, pack "u", substr($d, 0, 512, "") } print join "\\x01", @r')
    perl_rows = subprocess.run(["perl", "-e", script], input=data, capture_output=True,
                               check=True).stdout.decode().split("\x01")
    assert perl_rows == rows


# ------------------------------------------------------------------ bestandscontrole

def test_detect_type_on_content():
    assert detect_type(PDF) == "pdf"
    assert detect_type(PNG) == "png"
    assert detect_type(JPG) == "jpg"
    assert detect_type(b"<html><script>alert(1)</script>") is None
    assert detect_type(b"MZ\x90\x00") is None
    assert detect_type(b"") is None
    assert detect_type(b"  %PDF-1.4") is None              # alleen aan het begin


def test_check_file():
    assert check_file("factuur.pdf", PDF) == "pdf"
    assert check_file("bon.JPG", JPG) == "jpg"
    with pytest.raises(ValidationError, match="leeg"):
        check_file("leeg.pdf", b"")
    with pytest.raises(ValidationError, match="PDF, JPG of PNG"):
        check_file("factuur.pdf", b"<html>")                 # naam zegt pdf, inhoud niet
    with pytest.raises(ValidationError, match="10 MB"):
        check_file("groot.pdf", big_pdf(MAX_SIZE + 1))
    assert check_file("precies.pdf", big_pdf(MAX_SIZE)) == "pdf"


def test_check_file_own_limit():
    with pytest.raises(ValidationError, match="^Het bestand is groter dan 3 MB.$"):
        check_file("groot.pdf", big_pdf(3 * MB + 1), 3 * MB)
    assert check_file("groot.pdf", big_pdf(3 * MB), 3 * MB) == "pdf"


# ------------------------------------------------------------------ maximale uploadgrootte (stap 4f, MU1–MU5)

@pytest.mark.parametrize("raw, mb", [
    (None, None), ("", None), ("  ", None), ("0", None), ("-3", None), ("x", None), ("nan", None), ("inf", None),
    ("1", 1), ("12", 12), (" 25 ", 25), ("2.5", 2), ("3,7", 3), ("0.5", 1), ("40", 25), (12, 12),
])
def test_setting_mb(raw, mb):
    assert setting_mb(raw) == mb


def test_max_size_from_defaults(conn, cur):
    conn.execute("DELETE FROM defaults WHERE fldname = 'max_upload_size'")
    assert max_size(cur) == MAX_SIZE == 10 * MB               # SL 3.2.12 kent de sleutel niet: standaard
    conn.execute("INSERT INTO defaults VALUES ('max_upload_size', '0')")   # SQL-Ledger 4.x: leeg veld
    assert max_size(cur) == 10 * MB
    conn.execute("UPDATE defaults SET fldvalue = '12' WHERE fldname = 'max_upload_size'")
    assert max_size(cur) == 12 * MB
    conn.execute("UPDATE defaults SET fldvalue = '100' WHERE fldname = 'max_upload_size'")
    assert max_size(cur) == 25 * MB


def test_limit_applies_to_add_and_booking(conn, config):
    al = ArapLedger(conn, config)
    tid = al.post_transaction(ArapTransaction(vc="vendor", vc_id=VEND, transdate=date(2026, 3, 2), arap_accno="1600",
                                              lines=[ArapLine(accno="8100", amount=Decimal("100"))]))
    att = Attachments(conn, config)
    conn.execute("DELETE FROM defaults WHERE fldname = 'max_upload_size'")
    conn.execute("INSERT INTO defaults VALUES ('max_upload_size', '1')")
    with pytest.raises(ValidationError, match="groter dan 1 MB"):
        att.add(tid, "ap_transaction", "groot.pdf", big_pdf(MB + 1))
    assert att.list(tid) == []
    t = al.get_transaction(tid, "vendor")
    t.references = [Reference(description="x", filename="x.pdf", data=big_pdf(MB + 1))]
    with pytest.raises(ValidationError, match="groter dan 1 MB"):
        al.post_transaction(t)
    # groter dan de standaard van 10 MB mag als de administratie dat instelt
    conn.execute("UPDATE defaults SET fldvalue = '12' WHERE fldname = 'max_upload_size'")
    data = big_pdf(11 * MB)
    a = att.add(tid, "ap_transaction", "groot.pdf", data)
    assert att.get(a.archive_id)[1] == data
    t = al.get_transaction(tid, "vendor")
    t.references.append(Reference(description="ook groot", filename="y.pdf", data=data))
    al.post_transaction(t)
    assert len(att.list(tid)) == 2


def test_safe_filename():
    assert safe_filename("factuur.pdf", "pdf") == "factuur.pdf"
    assert safe_filename("C:\\Users\\x\\Factuur 12.PDF", "pdf") == "Factuur 12.PDF"
    assert safe_filename("../../etc/passwd", "pdf") == "passwd.pdf"
    assert safe_filename("scan", "jpg") == "scan.jpg"
    assert safe_filename("foto.jpeg", "jpg") == "foto.jpeg"
    assert safe_filename("bon.png", "jpg") == "bon.png.jpg"          # extensie volgt de inhoud (SL: contenttype)
    assert safe_filename("", "pdf") == "bijlage.pdf"
    assert safe_filename("a\x00b\nc.pdf", "pdf") == "abc.pdf"
    assert len(safe_filename("x" * 500 + ".pdf", "pdf")) <= 200


# ------------------------------------------------------------------ opslaan en lezen

def test_add_list_get_delete(conn, config, cur):
    al = ArapLedger(conn, config)
    tid = al.post_transaction(ArapTransaction(vc="vendor", vc_id=VEND, transdate=date(2026, 3, 2), arap_accno="1600",
                                              lines=[ArapLine(accno="8100", amount=Decimal("100"))]))
    att = Attachments(conn, config)
    a = att.add(tid, "ap_transaction", "factuur.pdf", PDF)
    assert a.filename == "factuur.pdf" and a.description == "factuur.pdf" and not a.confidential
    cur.execute("SELECT * FROM reference WHERE id = %s", (a.id,))
    ref = cur.fetchone()
    assert (ref["trans_id"], ref["formname"], ref["login"], ref["archive_id"]) == (tid, "ap_transaction", "",
                                                                                   a.archive_id)
    cur.execute("SELECT rn, bt FROM archivedata WHERE archive_id = %s ORDER BY rn", (a.archive_id,))
    rows = cur.fetchall()
    assert [r["rn"] for r in rows] == [1] and decode(r["bt"] for r in rows) == PDF
    assert [x.archive_id for x in att.list(tid)] == [a.archive_id]
    meta, data = att.get(a.archive_id)
    assert data == PDF and meta.trans_id == tid and meta.formname == "ap_transaction"

    b = att.add(tid, "ap_transaction", "bon", JPG, description="Kassabon")
    assert b.filename == "bon.jpg" and b.description == "Kassabon"
    assert len(att.list(tid)) == 2
    att.delete(a.id)
    assert [x.id for x in att.list(tid)] == [b.id]
    cur.execute("SELECT count(*) AS n FROM archive WHERE id = %s", (a.archive_id,))
    assert cur.fetchone()["n"] == 0                          # archief en data weg (cascade)
    cur.execute("SELECT count(*) AS n FROM archivedata WHERE archive_id = %s", (a.archive_id,))
    assert cur.fetchone()["n"] == 0
    with pytest.raises(NotFoundError):
        att.get(a.archive_id)
    with pytest.raises(NotFoundError):
        att.delete(a.id)


def test_add_refuses_bad_file_and_unknown_booking(conn, config):
    att = Attachments(conn, config)
    with pytest.raises(ValidationError):
        att.add(1, "gl", "x.pdf", b"<html>")
    with pytest.raises(NotFoundError):
        att.add(999999, "ap_transaction", "x.pdf", PDF)
    with pytest.raises(ValueError):
        att.add(1, "nonsense", "x.pdf", PDF)


def test_confidential_only_for_owner(conn, config):
    al = ArapLedger(conn, config)
    tid = al.post_transaction(ArapTransaction(vc="vendor", vc_id=VEND, transdate=date(2026, 3, 2), arap_accno="1600",
                                              lines=[ArapLine(accno="8100", amount=Decimal("10"))]))
    mine = Attachments(conn, config)
    a = mine.add(tid, "ap_transaction", "geheim.pdf", PDF, confidential=True)
    assert a.confidential
    other = Attachments(conn, UserConfig(login="piet@bedrijf"))
    assert other.list(tid) == []
    with pytest.raises(NotFoundError):
        other.get(a.archive_id)
    with pytest.raises(NotFoundError):
        other.delete(a.id)
    assert mine.get(a.archive_id)[1] == PDF


def test_shared_archive_kept_until_last_reference(conn, config, cur):
    """Zoals RD::delete_document: het archief verdwijnt pas met de laatste verwijzing."""
    al = ArapLedger(conn, config)
    tid = al.post_transaction(ArapTransaction(vc="vendor", vc_id=VEND, transdate=date(2026, 3, 2), arap_accno="1600",
                                              lines=[ArapLine(accno="8100", amount=Decimal("10"))]))
    att = Attachments(conn, config)
    a = att.add(tid, "ap_transaction", "f.pdf", PDF)
    cur.execute("""INSERT INTO reference (trans_id, description, archive_id, login, formname)
                   VALUES (%s, 'ook hier', %s, '', 'ap_transaction') RETURNING id""", (tid, a.archive_id))
    att.delete(a.id)
    assert att.get(a.archive_id)[1] == PDF


# ------------------------------------------------------------------ via de boeking (Form::save_reference)

def test_post_transaction_stores_new_file_and_keeps_it_on_repost(conn, config, cur):
    al = ArapLedger(conn, config)
    t = ArapTransaction(vc="vendor", vc_id=VEND, transdate=date(2026, 3, 2), arap_accno="1600", invnumber="F1",
                        lines=[ArapLine(accno="8100", amount=Decimal("100"))], taxes={"1800": Decimal("21")},
                        references=[Reference(description="Factuur F1", filename="F1.pdf", data=PDF),
                                    Reference(description="Alleen tekst", code="X")])
    tid = al.post_transaction(t)
    t2 = al.get_transaction(tid, "vendor")
    files = [r for r in t2.references if r.archive_id]
    assert [(r.description, r.filename) for r in files] == [("Factuur F1", "F1.pdf")]
    assert Attachments(conn, config).get(files[0].archive_id)[1] == PDF
    al.post_transaction(t2)                                    # opnieuw boeken: bijlage blijft
    t3 = al.get_transaction(tid, "vendor")
    assert [r.archive_id for r in t3.references if r.archive_id] == [files[0].archive_id]
    assert {r.description for r in t3.references} == {"Factuur F1", "Alleen tekst"}
    al.delete_transaction(tid, "vendor")                       # boeking weg: archief weg
    cur.execute("SELECT count(*) AS n FROM archive WHERE id = %s", (files[0].archive_id,))
    assert cur.fetchone()["n"] == 0


def test_post_refuses_bad_file_data(conn, config):
    al = ArapLedger(conn, config)
    t = ArapTransaction(vc="vendor", vc_id=VEND, transdate=date(2026, 3, 2), arap_accno="1600",
                        lines=[ArapLine(accno="8100", amount=Decimal("100"))],
                        references=[Reference(description="x", filename="x.pdf", data=b"<html>")])
    with pytest.raises(ValidationError):
        al.post_transaction(t)


def test_gl_with_file(conn, config):
    gl = GeneralLedger(conn, config)
    tid = gl.post_transaction(GLTransaction(
        transdate=date(2026, 3, 3), reference="M1",
        lines=[JournalLine("8100", debit=Decimal("5")), JournalLine("1100", credit=Decimal("5"))],
        references=[Reference(description="Bon", filename="bon.png", data=PNG)]))
    att = Attachments(conn, config)
    (a,) = att.list(tid)
    assert (a.formname, a.filename) == ("gl", "bon.png")
    assert att.get(a.archive_id)[1] == PNG


def test_shared_archive_survives_delete_and_repost_of_other_booking(conn, config, cur):
    """rd.pl "Koppelen aan" (RD::attach_document) laat twee boekingen hetzelfde archiefstuk delen;
    Form::delete_references/save_reference wisten het dan ook voor de andere (Perl-fout 28)."""
    al = ArapLedger(conn, config)
    one = al.post_transaction(ArapTransaction(vc="vendor", vc_id=VEND, transdate=date(2026, 3, 2), arap_accno="1600",
                                              lines=[ArapLine(accno="8100", amount=Decimal("10"))]))
    two = al.post_transaction(ArapTransaction(vc="vendor", vc_id=VEND, transdate=date(2026, 3, 3), arap_accno="1600",
                                              lines=[ArapLine(accno="8100", amount=Decimal("20"))]))
    att = Attachments(conn, config)
    a = att.add(one, "ap_transaction", "gedeeld.pdf", PDF)
    cur.execute("""INSERT INTO reference (trans_id, description, archive_id, login, formname)
                   VALUES (%s, 'gekoppeld', %s, '', 'ap_transaction')""", (two, a.archive_id))
    t = al.get_transaction(one, "vendor")
    t.references = []
    al.post_transaction(t)                                 # bijlage weg bij boeking 1
    assert [x.archive_id for x in att.list(two)] == [a.archive_id]
    cur.execute("""INSERT INTO reference (trans_id, description, archive_id, login, formname)
                   VALUES (%s, 'terug', %s, '', 'ap_transaction')""", (one, a.archive_id))
    al.delete_transaction(one, "vendor")                   # boeking 1 weg
    assert att.get(a.archive_id)[1] == PDF
    al.delete_transaction(two, "vendor")                   # laatste verwijzing weg: archief weg
    cur.execute("SELECT count(*) AS n FROM archive WHERE id = %s", (a.archive_id,))
    assert cur.fetchone()["n"] == 0


# ------------------------------------------------------------------ stap 6a: andere soorten (RB1, RB2, RB7)

def test_record_exists_checks_kind(conn, config, cur):
    from sqlledger.attachments import record_exists
    cur.execute("""INSERT INTO oe (ordnumber, transdate, customer_id, quotation) VALUES ('O-1', '2026-03-01', 902, false)
                   RETURNING id""")
    oid = cur.fetchone()["id"]
    cur.execute("""INSERT INTO parts (partnumber, description, income_accno_id, expense_accno_id)
                   VALUES ('D-1', 'Dienst', (SELECT id FROM chart WHERE accno = '8520'),
                           (SELECT id FROM chart WHERE accno = '8020')) RETURNING id""")
    sid = cur.fetchone()["id"]
    assert record_exists(cur, "sales_order", oid)
    assert not record_exists(cur, "sales_quotation", oid) and not record_exists(cur, "purchase_order", oid)
    assert record_exists(cur, "service", sid) and not record_exists(cur, "part", sid)
    assert record_exists(cur, "customer", 902) and not record_exists(cur, "vendor", 902)
    assert record_exists(cur, "vendor", VEND)
    assert not record_exists(cur, "timecard", oid)
    att = Attachments(conn, config)
    a = att.add(oid, "sales_order", "o.pdf", PDF)
    with pytest.raises(NotFoundError):
        att.add(oid, "sales_quotation", "o.pdf", PDF)
    b = att.add(902, "customer", "c.pdf", PDF)
    assert [x.formname for x in att.list(oid)] == ["sales_order"] and att.list(902)[0].id == b.id
    assert a.module == "oe"


def test_elsewhere_shows_other_holders(conn, config, cur):
    al = ArapLedger(conn, config)
    tid = al.post_transaction(ArapTransaction(vc="vendor", vc_id=VEND, transdate=date(2026, 3, 2), arap_accno="1600",
                                              invnumber="F-77", lines=[ArapLine(accno="8100", amount=Decimal("10"))]))
    att = Attachments(conn, config)
    a = att.add(VEND, "vendor", "contract.pdf", PDF)
    cur.execute("""INSERT INTO reference (trans_id, description, archive_id, login, formname)
                   VALUES (%s, 'x', %s, '', 'ap_transaction')""", (tid, a.archive_id))
    (on_vendor,) = att.list(VEND)
    assert [(h.formname, h.trans_id, h.number) for h in on_vendor.elsewhere] == [("ap_transaction", tid, "F-77")]
    (on_booking,) = att.list(tid)
    assert on_booking.elsewhere[0].formname == "vendor" and on_booking.elsewhere[0].name
    # vertrouwelijke verwijzing van een ander telt niet mee
    cur.execute("UPDATE reference SET login = 'piet' WHERE trans_id = %s", (tid,))
    assert att.list(VEND)[0].elsewhere == []


def test_delete_references_also_confidential_of_others(conn, config, cur):
    """RB7: Form::delete_references liet vertrouwelijke verwijzingen van anderen als wees achter."""
    from sqlledger import support
    al = ArapLedger(conn, config)
    tid = al.post_transaction(ArapTransaction(vc="vendor", vc_id=VEND, transdate=date(2026, 3, 2), arap_accno="1600",
                                              lines=[ArapLine(accno="8100", amount=Decimal("10"))]))
    other = Attachments(conn, UserConfig(login="piet@bedrijf"))
    a = other.add(tid, "ap_transaction", "geheim.pdf", PDF, confidential=True)
    al.delete_transaction(tid, "vendor")
    cur.execute("SELECT count(*) AS n FROM reference WHERE trans_id = %s", (tid,))
    assert cur.fetchone()["n"] == 0
    cur.execute("SELECT count(*) AS n FROM archive WHERE id = %s", (a.archive_id,))
    assert cur.fetchone()["n"] == 0
    support.delete_references(cur, config, tid)              # niets meer: geen fout


def test_save_references_links_only_visible_archives(conn, config, cur):
    al = ArapLedger(conn, config)
    one = al.post_transaction(ArapTransaction(vc="vendor", vc_id=VEND, transdate=date(2026, 3, 2), arap_accno="1600",
                                              lines=[ArapLine(accno="8100", amount=Decimal("10"))]))
    secret = Attachments(conn, UserConfig(login="piet@bedrijf")).add(one, "ap_transaction", "g.pdf", PDF,
                                                                    confidential=True)
    public = Attachments(conn, config).add(one, "ap_transaction", "p.pdf", PDF)
    two = al.post_transaction(ArapTransaction(
        vc="vendor", vc_id=VEND, transdate=date(2026, 3, 3), arap_accno="1600",
        lines=[ArapLine(accno="8100", amount=Decimal("20"))],
        references=[Reference(description="buit", archive_id=secret.archive_id),
                    Reference(description="mag", archive_id=public.archive_id)]))
    cur.execute("SELECT description, archive_id FROM reference WHERE trans_id = %s ORDER BY id", (two,))
    assert [(r["description"], r["archive_id"]) for r in cur.fetchall()] == [("buit", None),
                                                                             ("mag", public.archive_id)]
    # los archiefstuk zonder verwijzing (rd.pl "Document toevoegen") mag iedereen koppelen
    cur.execute("INSERT INTO archive (filename) VALUES ('los.pdf') RETURNING id")
    loose = cur.fetchone()["id"]
    t = al.get_transaction(two, "vendor")
    t.references = [Reference(description="los", archive_id=loose)]
    al.post_transaction(t)
    cur.execute("SELECT archive_id FROM reference WHERE trans_id = %s", (two,))
    assert [r["archive_id"] for r in cur.fetchall()] == [loose]


# ------------------------------------------------------------------ stap 6b: sha256 en dubbele bestanden (RB5)

def _vendor_booking(conn, config, n=1):
    return ArapLedger(conn, config).post_transaction(ArapTransaction(
        vc="vendor", vc_id=VEND, transdate=date(2026, 3, n), arap_accno="1600", invnumber=f"D-{n}",
        lines=[ArapLine(accno="8100", amount=Decimal("121"))]))


def test_hash_stored_in_own_table(conn, config, cur):
    from sqlledger.attachments import sha256
    cur.execute("ALTER TABLE archive DROP COLUMN IF EXISTS hash")     # ook op een 4.x-sjabloon: zonder kolom
    tid = _vendor_booking(conn, config)
    a = Attachments(conn, config).add(tid, "ap_transaction", "f.pdf", PDF)
    cur.execute("SELECT hash FROM x_archive_hash WHERE archive_id = %s", (a.archive_id,))
    assert cur.fetchone()["hash"] == sha256(PDF)


def test_hash_in_archive_column_when_sl4(conn, config, cur):
    """SQL-Ledger 4.x: archive.hash (char(64)); dan daar, zoals Form::save_reference van 4.x."""
    from sqlledger.attachments import sha256
    cur.execute("ALTER TABLE archive ADD COLUMN IF NOT EXISTS hash char(64)")
    tid = _vendor_booking(conn, config)
    a = Attachments(conn, config).add(tid, "ap_transaction", "f.pdf", PDF)
    cur.execute("SELECT hash FROM archive WHERE id = %s", (a.archive_id,))
    assert cur.fetchone()["hash"] == sha256(PDF)
    cur.execute("SELECT to_regclass('x_archive_hash') AS t")
    t = cur.fetchone()["t"]
    if t:
        cur.execute("SELECT count(*) AS n FROM x_archive_hash")
        assert cur.fetchone()["n"] == 0


def test_old_archives_get_hash(conn, config, cur):
    """Archiefstukken van SQL-Ledger (zonder hash) krijgen hem bij de eerste controle (zoals 4.x)."""
    from sqlledger.attachments import duplicates, sha256, store
    tid = _vendor_booking(conn, config)
    cur.execute("INSERT INTO archive (filename) VALUES ('sl.pdf') RETURNING id")
    aid = cur.fetchone()["id"]
    for rn, bt in enumerate(encode(PDF + b"sl"), 1):
        cur.execute("INSERT INTO archivedata (rn, archive_id, bt) VALUES (%s, %s, %s)", (rn, aid, bt))
    cur.execute("""INSERT INTO reference (trans_id, description, archive_id, login, formname)
                   VALUES (%s, 'uit SL', %s, '', 'ap_transaction')""", (tid, aid))
    d = duplicates(cur, config.short_login, sha256(PDF + b"sl"))
    assert [(h.formname, h.trans_id, h.number) for h in d.holders] == [("ap_transaction", tid, "D-1")]
    assert d.holders[0].amount == 121 and not d.here
    store(cur, "x.pdf", PDF)                                  # nieuw stuk: hash meteen


def test_duplicates_here_elsewhere_hidden_loose(conn, config, cur):
    from sqlledger.attachments import duplicates, sha256
    one, two = _vendor_booking(conn, config, 1), _vendor_booking(conn, config, 2)
    att = Attachments(conn, config)
    att.add(one, "ap_transaction", "f.pdf", PDF)
    with pytest.raises(ValidationError, match="hangt hier al"):
        att.add(one, "ap_transaction", "zelfde.pdf", PDF)      # zelfde bestand, zelfde boeking: geweigerd
    b = att.add(two, "ap_transaction", "f.pdf", PDF)          # andere boeking: mag, eigen archiefstuk
    cur.execute("SELECT count(DISTINCT archive_id) AS n FROM reference WHERE trans_id IN (%s, %s)", (one, two))
    assert cur.fetchone()["n"] == 2
    d = duplicates(cur, config.short_login, sha256(PDF), one)
    assert d.here and [h.trans_id for h in d.holders] == [two] and not d.hidden
    Attachments(conn, UserConfig(login="piet@bedrijf")).add(two, "ap_transaction", "g.pdf", PDF + b"g",
                                                            confidential=True)
    d = duplicates(cur, config.short_login, sha256(PDF + b"g"), one)
    assert not d.here and d.hidden == 1 and not d.holders and d
    cur.execute("DELETE FROM reference WHERE id = %s", (b.id,))   # los stuk (rd.pl zonder koppeling)
    d = duplicates(cur, config.short_login, sha256(PDF))
    assert d.loose == 1 and [h.trans_id for h in d.holders] == [one]
    assert not duplicates(cur, config.short_login, sha256(b"iets anders"))


def test_photo_is_normalized_when_stored(conn, config):
    import io
    from PIL import Image
    im = Image.effect_noise((3000, 2000), 40).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=95)
    tid = _vendor_booking(conn, config)
    att = Attachments(conn, config)
    a = att.add(tid, "ap_transaction", "foto.jpg", buf.getvalue())
    data = att.get(a.archive_id)[1]
    assert Image.open(io.BytesIO(data)).size == (2400, 1600) and len(data) < len(buf.getvalue())
