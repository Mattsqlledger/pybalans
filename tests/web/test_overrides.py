"""Eigen woorden per installatie (TL9): <data_dir>/vertalingen/<taal>.txt."""

import os
import re

import pytest

from sqlledger_web import cli, i18n, libmsg, overrides
from webhelpers import login, make_user


@pytest.fixture
def words(settings, tmp_path):
    settings.data_dir = str(tmp_path)
    folder = tmp_path / "vertalingen"
    folder.mkdir()
    yield folder
    i18n.OVERRIDES.clear()
    overrides._seen.clear()


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    st = path.stat()  # mtime zeker anders dan de vorige versie
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


def test_parse_rules():
    r = overrides.parse(
        "# commentaar\n\n"
        "Nieuwe datum (leeg = zelfde datum) = Neues Datum (leer = gleiches Datum)\n"
        "[menu] Valuta = Devisen\n"
        "Opslaan = Sichern\\nJetzt\n"
        "Bestaat niet = x\n"
        "Boeking {trans_id} niet gevonden. = Buchung fehlt\n"
        "[menu] Opslaan = x\n"
        "kapot\n"
        "Opslaan = Sichern 2\n")
    assert r.texts == {"Nieuwe datum (leeg = zelfde datum)": "Neues Datum (leer = gleiches Datum)",
                       "Opslaan": "Sichern 2"}
    assert r.contexts == {"menu": {"Valuta": "Devisen"}}
    assert [p.split(":")[0] for p in r.problems] == ["regel 6", "regel 7", "regel 8", "regel 9", "regel 10"]
    assert "plaatshouders" in r.problems[1] and "al eerder" in r.problems[4]


def test_own_words_on_pages_and_reload(client, db, company, words):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    db.execute("UPDATE app_user SET language = 'de' WHERE login = 'boek'")
    login(client, "boek")
    page = client.get("/administratie").text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    client.post("/administratie", data={"csrf": csrf, "company_id": company["id"]})
    assert "Buchung anlegen" in client.get("/").text

    _write(words / "de.txt", "Boeking toevoegen = Buchung erfassen\n[menu] Overzicht = Liste\n")
    html = client.get("/").text
    assert "Buchung erfassen" in html and "Buchung anlegen" not in html
    assert ">Liste<" in html

    # gewijzigd bestand wordt opnieuw gelezen, verwijderd bestand = weer de standaard
    _write(words / "de.txt", "Boeking toevoegen = Buchung neu\n")
    assert "Buchung neu" in client.get("/").text
    (words / "de.txt").unlink()
    assert "Buchung anlegen" in client.get("/").text


def test_dutch_words_and_library_messages(words, settings):
    _write(words / "nl.txt", "Opslaan = Bewaren\nDeze boeking is in gebruik door {holder}. = Op slot bij {holder}.\n")
    overrides.refresh(settings.data_dir, "nl")
    token = i18n.current_language.set("nl")
    try:
        assert i18n.gettext("Opslaan") == "Bewaren"
        assert libmsg.lib_message("Transaction is locked by jan") == "Op slot bij jan."
    finally:
        i18n.current_language.reset(token)


def test_invalid_file_ignored(words, settings):
    (words / "fr.txt").write_bytes(b"Opslaan = \xff\xfe")
    overrides.refresh(settings.data_dir, "fr")
    assert "fr" not in i18n.OVERRIDES or not i18n.OVERRIDES["fr"][0]
    with pytest.raises(ValueError):
        overrides.path_for(settings.data_dir, "../etc")


def test_cli(words, settings, monkeypatch, capsys, tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'app_dsn = "{settings.app_dsn}"\ndata_dir = "{tmp_path}"\n')
    assert cli.main(["--config", str(cfg), "translations", "de"]) == 0
    assert "bestaat niet" in capsys.readouterr().out
    _write(words / "de.txt", "Opslaan = Sichern\nOnzin = x\n")
    assert cli.main(["--config", str(cfg), "translations", "de"]) == 1
    out = capsys.readouterr().out
    assert "regel 2: onbekende tekst" in out and "1 eigen tekst(en)" in out
    assert cli.main(["--config", str(cfg), "translations", "fr", "--starter"]) == 0
    out = capsys.readouterr().out
    assert "# Opslaan = Enregistrer" in out and "# [menu] Valuta = " in out
    # het startpunt zelf is een geldig bestand (zonder '# ')
    body = "\n".join(line[2:] for line in out.splitlines() if line.startswith("# ") and " = " in line[2:])
    assert not [p for p in overrides.parse(body).problems if "al eerder" not in p]
