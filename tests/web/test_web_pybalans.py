"""v1.0.1 naam PyBalans (NB1–NB5) en de kleine fixes: titel, ondertitel, Over PyBalans met merkregels, TOTP-issuer,
PDF-maker, opdrachten in pyproject.toml, bestandsgrootte op het controlescherm en de vertaalde bestandsknop."""

import io
import re
import tomllib
from fnmatch import fnmatch
from pathlib import Path

import pytest

import sqlledger
from sqlledger import dbbackup, slbackup
from sqlledger_web import reportpdf
from sqlledger_web.routes_restore import file_size
from sqlledger_web.settings import Settings
from webhelpers import csrf, login, make_user

ROOT = Path(__file__).resolve().parents[2]
TRADEMARKS = ("PostgreSQL is een merk van de PostgreSQL Community Association of Canada; PyBalans is daar niet "
              "aan verbonden.",
              "SQL-Ledger is een merk van DWS Systems Inc.; PyBalans is daar niet aan verbonden en leest "
              "SQL-Ledger-administraties alleen in.")
TAGLINE = "boekhouding in Python op PostgreSQL · leest en verrijkt SQL-Ledger-administraties"


def test_welcome_and_login_title_brand_and_tagline(client, db):
    page = client.get("/login").text                      # lege server: de wizard (Engels)
    assert "<title>Welcome · PyBalans</title>" in page and "Welcome to PyBalans" in page
    assert "accounting in Python on PostgreSQL · reads and enhances SQL-Ledger companies" in page
    make_user(db, "anna")
    page = client.get("/login").text
    assert re.search(r"<title>Inloggen · PyBalans</title>", page)
    assert '<h1 class="brand">PyBalans</h1>' in page and TAGLINE in page
    assert "SQL-Ledger</title>" not in page and ">SQL-Ledger<" not in page


def test_totp_issuer_is_pybalans():
    assert Settings().totp_issuer == "PyBalans"


def test_about_page_shows_version_licence_and_trademarks(client, db):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    page = client.get("/over").text.replace("&#39;", "'")
    assert "<title>Over PyBalans · PyBalans</title>" in page and sqlledger.__version__ in page
    assert "GPL-2.0-or-later" in page and all(t in page for t in TRADEMARKS)
    assert '<a class="brand" href="/">PyBalans</a>' in page and 'href="/over"' in page     # kopregel en menu
    client.cookies.set("lang", "en")
    assert client.get("/over").status_code == 200


def test_pdf_creator_is_pybalans():
    pypdf = pytest.importorskip("pypdf")
    from weasyprint import HTML
    data = reportpdf.write_pdf(HTML(string="<p>x</p>").render())
    meta = pypdf.PdfReader(io.BytesIO(data)).metadata
    assert meta.creator == f"PyBalans {sqlledger.__version__}"


def test_backup_header_new_and_old():
    assert dbbackup.HEADER == "-- PyBalans back-up"
    for head in (dbbackup.HEADER, "-- SQL-Ledger (Python) back-up"):
        b = slbackup.info(f"{head}\n-- App-Dataset: adm_a\n-- App-Version: 3.2.4\n--\n")
        assert (b.kind, b.dataset) == ("app", "adm_a")


def test_pyproject_name_scripts_and_package_data():
    """NB3: naam pybalans, beide opdrachten, en elk bestand (geen .py) in de pakketten valt onder package-data."""
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert meta["project"]["name"] == "pybalans" and meta["project"]["version"] == sqlledger.__version__
    assert meta["project"]["scripts"] == {"pybalans": "sqlledger_web.cli:main", "sqlledger-web": "sqlledger_web.cli:main"}
    data = meta["tool"]["setuptools"]["package-data"]
    missing = []
    for package in meta["tool"]["setuptools"]["packages"]:
        base = ROOT / package.replace(".", "/")
        subpackages = {p.split(".", 1)[1].split(".")[0] for p in meta["tool"]["setuptools"]["packages"]
                       if p.startswith(package + ".")}
        for f in base.rglob("*"):
            rel = f.relative_to(base)
            if (f.is_dir() or f.suffix in (".py", ".pyc") or "__pycache__" in rel.parts
                    or rel.parts[0] in subpackages or f.name == ".DS_Store"):
                continue
            if not any(fnmatch(str(rel), pat) and str(rel).count("/") == pat.count("/") for pat in data.get(package, [])):
                missing.append(f"{package}: {rel}")
    assert not missing


@pytest.mark.parametrize("size, text", [(1, "1 kB"), (40_000, "40 kB"), (999 * 1024, "999 kB"), (999 * 1024 + 1, "1,0 MB"),
                                        (5 * 1048576, "5,0 MB"), (int(1.25 * 1048576), "1,3 MB")])
def test_file_size(size, text):
    assert file_size(size, "1.000,00") == text


def test_import_and_rgs_use_translated_file_button(client, db, company):
    secret = make_user(db, "baas", sysadmin=True, totp=True, memberships=[(company["id"], "admin", "baas")])
    login(client, "baas", secret)
    page = client.get("/administratie").text
    client.post("/administratie", data={"csrf": csrf(client, page), "company_id": company["id"]})
    for url in ("/beheer/importeren", "/systeem/rgs"):
        page = client.get(url).text
        assert '<span class="filepick"><input type="file" name="bestand"' in page and "Bestand kiezen" in page, url
        assert len(re.findall(r'type="file"', page)) == len(re.findall(r'class="filepick"', page)), url
