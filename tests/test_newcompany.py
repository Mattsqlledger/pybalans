"""Standaardrekeningschema's en nieuwe administratie (REKENINGSCHEMA_ANALYSE.md, RS1, RS9, RS10)."""

import os
import uuid
from datetime import date
from decimal import Decimal

import psycopg
import pytest

from sqlledger import ChartOfAccounts, GeneralLedger, GLTransaction, JournalLine, UserConfig, chartfiles, newcompany
from sqlledger.chartfiles import ChartFileError
from sqlledger.errors import ValidationError
from sqlledger.vatreturn import VatReturns

DSN = os.environ.get("SQLLEDGER_TEST_DSN", "host=127.0.0.1 port=55432 user=postgres")

CHARTS = chartfiles.available()


@pytest.fixture
def admin():
    try:
        conn = psycopg.connect(f"{DSN} dbname=postgres", autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("geen test-PostgreSQL bereikbaar")
    made = []
    yield conn, made
    for name in made:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    conn.close()


def _connect(dbname):
    return psycopg.connect(f"{DSN} dbname={dbname}", autocommit=True)


def _create(admin, chart_id, **kw):
    conn, made = admin
    name = f"nc_{uuid.uuid4().hex[:10]}"
    made.append(name)
    info = newcompany.create(conn, _connect, newcompany.NewCompany(
        dbname=name, company="Bakkerij Jansen", chart=chartfiles.get(chart_id), **kw))
    return name, info


def test_shipped_charts_pass_the_checks():
    assert {c.id for c in CHARTS} >= {"leeg-nl", "leeg-be", "leeg-uk", "leeg-fr", "leeg-de", "leeg-ch"}
    assert list(dict.fromkeys(c.country for c in CHARTS)) == ["NL", "BE", "GB", "FR", "DE", "CH"]
    assert [c.id for c in CHARTS if c.country == "NL"] == ["nl-eenmanszaak", "nl-bv", "nl-basis", "leeg-nl"]
    for chart in CHARTS:
        chartfiles.check(chart)
        assert chart.license and chart.source and chart.name("nl")


SAMPLE = """# id: proef
# land: NL
# talen: nl en
# valuta: EUR
# naam.nl: Proef
# bron: toets
# licentie: GPL-2.0-or-later
# voorraad: 3001
# omzet: 8001
# kosten: 7001
# koersverschil: 4910
# btw-afdracht: 1590
# btw-afronding: 4900
rekening;soort;categorie;koppeling;contra;tarief;rubriek;rgs;nl;en
"""


def _with_rows(text):
    base = chartfiles.get("leeg-nl")
    lines = [";".join([r.accno, r.charttype, r.category, ":".join(r.link), "1" if r.contra else "",
                       str(r.rate) if r.rate is not None else "", r.box, r.rgs, r.descriptions["nl"],
                       r.descriptions["en"]]) for r in base.rows]
    return SAMPLE + "\n".join(lines) + "\n" + text


@pytest.mark.parametrize("extra, message", [
    ("1001;A;A;;;;;;Dubbel;Double", "dubbel"),
    ("0050;A;E;;;;;;Voor de kop;Before heading", "geen kop van categorie Kosten"),
    ("1515;A;L;AR_tax;;;;;Btw zonder tarief;VAT without rate", "tarief"),
    ("1516;A;L;AR_tax;;21;1A;;Tarief als procent;Percentage", "fractie"),
    ("1517;A;L;AR_tax;;0.21;;;Zonder rubriek;No box", "rubriek"),
    ("1518;A;L;AR_tax;;0.21;9Z;;Onbekende rubriek;Unknown box", "rubriek 9Z"),
    ("1519;A;L;AR:AP;;;;;Twee hoofdrekeningen;Two control accounts", "hoofdrekening"),
    ("1520;A;L;;;;;;Te weinig;Too few;extra", "kolommen"),
    ("4002;A;E;;;;;;;Missing Dutch", "omschrijving"),
    ("1550;A;L;AP_tax;;-0.21;1A;;Negatief op 1A;Negative on 1A", "negatief tarief"),
    ("1550;A;L;AP_tax;;-0.06;4B;;Zonder 5B-paar;No 5B partner", "te vorderen \\(5B\\) met tarief 0.06"),
    ("1550;A;L;AP_tax;;-1.5;4B;;Te groot;Too large", "fractie"),
    ("4002;A;E;;;;;Bxyz;Foute code;Bad code", "ongeldige RGS-code"),
    ("4002;A;E;;;;;WBedAlkOal;Code zonder versie;Code without version", "kopregel 'rgs'"),
    ("0;H;Q;;;;;;Nul;Zero", "rekeningnummer 0"),
])
def test_check_rejects_broken_files(extra, message):
    with pytest.raises(ChartFileError, match=message):
        chartfiles.check(chartfiles.parse(_with_rows(extra), "proef.csv"))


def test_check_requires_default_accounts_with_the_right_links():
    text = _with_rows("").replace("# voorraad: 3001", "# voorraad: 1001")
    with pytest.raises(ChartFileError, match="voorraad = 1001 mist de koppeling IC"):
        chartfiles.check(chartfiles.parse(text, "proef.csv"))
    text = _with_rows("").replace("# omzet: 8001\n", "")
    with pytest.raises(ChartFileError, match="standaardrekening ontbreekt: omzet"):
        chartfiles.check(chartfiles.parse(text, "proef.csv"))


def test_dutch_charts():
    """RS3: eenmanszaak/vof, bv en basis met RGS 3.8-codes en verlegde btw (2A/4A/4B als paar met 5B)."""
    charts = {c.id: c for c in CHARTS}
    for cid in ("nl-eenmanszaak", "nl-bv", "nl-basis"):
        chart = charts[cid]
        assert chart.country == "NL" and chart.rgs_version == "3.8"
        assert all(r.rgs for r in chart.rows), cid
        accounts = {r.accno: r for r in chart.accounts}
        # kernnummers gelijk aan het lege schema
        for accno, link in (("1001", "AR_paid"), ("1100", "AR_paid"), ("1300", "AR"), ("1600", "AP"),
                            ("3001", "IC"), ("8001", "IC_sale"), ("7001", "IC_cogs")):
            assert link in accounts[accno].link, (cid, accno)
    eenmanszaak, bv, basis = (charts[c] for c in ("nl-eenmanszaak", "nl-bv", "nl-basis"))
    reverse = {r.box: r.rate for r in eenmanszaak.accounts if r.rate is not None and r.rate < 0}
    assert set(reverse) == {"2A", "4A", "4B"}
    assert {r.accno for r in basis.accounts} <= {r.accno for r in eenmanszaak.accounts}
    assert "0520" in {r.accno for r in eenmanszaak.accounts if r.rgs == "BEivKapPro"}
    assert {r.rgs for r in bv.accounts} >= {"BEivGokGea", "BVorOvrRcb", "BSchBepVpb", "WBelBgrBgr"}
    assert not any(r.rgs.startswith("BEivKap") for r in bv.accounts)
    assert 150 <= len(eenmanszaak.accounts) <= 170 and 55 <= len(basis.accounts) <= 70


def test_unknown_chart():
    with pytest.raises(ValidationError):
        chartfiles.get("bestaat-niet")


def test_create_dutch_company(admin):
    name, info = _create(admin, "leeg-nl", employee_login="anna", employee_name="Anna", yearend=6)
    assert info == {"accounts": 17, "tax_accounts": 4, "currency": "EUR", "language": "nl", "chart": "leeg-nl",
                    "country": "NL"}
    with _connect(name) as conn:
        d = dict(conn.execute("SELECT fldname, fldvalue FROM defaults").fetchall())
        assert d["version"] == "3.2.4" and d["company"] == "Bakkerij Jansen" and d["yearend"] == "6"
        ids = dict(conn.execute("SELECT accno, id::text FROM chart").fetchall())
        assert d["inventory_accno_id"] == ids["3001"] and d["income_accno_id"] == ids["8001"]
        assert d["expense_accno_id"] == ids["7001"] and d["fxgainloss_accno_id"] == ids["4910"]
        assert conn.execute("SELECT curr FROM curr").fetchall() == [("EUR",)]
        assert conn.execute("SELECT login, name FROM employee").fetchall() == [("anna", "Anna")]
        assert conn.execute("SELECT count(*) FROM mimetype").fetchone()[0] > 100
        # btw: tarieven, rubrieken en de twee rekeningen voor de aangifte (BA3, RS9)
        vat = VatReturns(conn, UserConfig(login="anna"))
        assert vat.configured() and vat.accounts_setting() == ("1590", "4900")
        assert {a["accno"]: (a["box"], a["rate"]) for a in vat.tax_accounts()} == {
            "1510": ("1A", Decimal("0.21")), "1520": ("1B", Decimal("0.09")),
            "1530": ("5B", Decimal("0.21")), "1540": ("5B", Decimal("0.09"))}
        assert newcompany.setting(conn, "country") == "NL" and newcompany.setting(conn, "chart") == "leeg-nl"
        # boeken en lezen werkt meteen
        gl = GeneralLedger(conn, UserConfig(login="anna"))
        gl.post_transaction(GLTransaction(transdate=date(2026, 1, 2), description="Inbreng",
                                          lines=[JournalLine("1100", debit=Decimal("500")),
                                                 JournalLine("0100", credit=Decimal("500"))]))
        balances = {a["accno"]: a for a in ChartOfAccounts(conn, UserConfig(login="anna")).all_accounts()}
        assert balances["1100"]["debit"] == Decimal("500")


@pytest.mark.parametrize("chart_id, lang, currency, accno, description", [
    ("leeg-uk", "", "", "1200", "Bank current account"),
    ("leeg-ch", "fr", "", "1100", "Créances résultant de ventes et prestations de services"),
    ("leeg-be", "fr", "", "400000", "Clients"),
    ("leeg-fr", "", "usd", "411000", "Clients"),
])
def test_language_and_currency(admin, chart_id, lang, currency, accno, description):
    name, info = _create(admin, chart_id, language=lang, currency=currency)
    with _connect(name) as conn:
        assert conn.execute("SELECT description FROM chart WHERE accno = %s", (accno,)).fetchone()[0] == description
        curr = conn.execute("SELECT curr FROM curr").fetchone()[0]
        assert curr == (currency.upper() or chartfiles.get(chart_id).currency) == info["currency"]
        # VAT100-boxen / MWST-Ziffern met de landcode (UK5); de aangifte volgt het land van het schema (BL2): het
        # Britse en Belgische formulier bestaan, de andere landen nog niet (dan de NL-aangifte, die hun koppelingen niet leest)
        countries = {r[0] for r in conn.execute("SELECT DISTINCT country FROM x_vat_box")}
        assert countries == {{"leeg-uk": "GB", "leeg-ch": "CH", "leeg-fr": "FR", "leeg-be": "BE"}[chart_id]}
        vr = VatReturns(conn)
        assert vr.country == {"leeg-uk": "GB", "leeg-be": "BE"}.get(chart_id, "NL")
        assert vr.configured() == (chart_id in ("leeg-uk", "leeg-be"))


def test_existing_database_is_never_touched(admin):
    conn, made = admin
    name, _ = _create(admin, "leeg-nl")
    with pytest.raises(ValidationError, match="bestaat al een database"):
        newcompany.create(conn, _connect, newcompany.NewCompany(dbname=name, company="X",
                                                                chart=chartfiles.get("leeg-uk")))
    with _connect(name) as c:
        assert c.execute("SELECT fldvalue FROM defaults WHERE fldname = 'company'").fetchone()[0] == "Bakkerij Jansen"


@pytest.mark.parametrize("kw, message", [
    ({"language": "de", "chart": "leeg-nl"}, None),
    ({"language": "it", "chart": "leeg-nl"}, "taal"),
    ({"currency": "EURO", "chart": "leeg-nl"}, "drie letters"),
    ({"yearend": 13, "chart": "leeg-nl"}, "boekjaar"),
])
def test_validation_before_anything_is_made(admin, kw, message):
    conn, made = admin
    name = f"nc_{uuid.uuid4().hex[:10]}"
    made.append(name)
    new = newcompany.NewCompany(dbname=name, company="X", chart=chartfiles.get(kw.pop("chart")), **kw)
    if message is None:
        newcompany.create(conn, _connect, new)
        return
    with pytest.raises(ValidationError, match=message):
        newcompany.create(conn, _connect, new)
    assert not newcompany.database_exists(conn, name)


def test_failure_after_create_drops_the_database(admin, monkeypatch):
    conn, made = admin
    name = f"nc_{uuid.uuid4().hex[:10]}"
    made.append(name)

    def boom(c, new):
        raise RuntimeError("stuk")
    monkeypatch.setattr(newcompany, "fill", boom)
    with pytest.raises(RuntimeError):
        newcompany.create(conn, _connect, newcompany.NewCompany(dbname=name, company="X",
                                                                chart=chartfiles.get("leeg-nl")))
    assert not newcompany.database_exists(conn, name)


def test_dutch_company_with_rgs_and_reverse_charge(admin):
    """RGS-codes in x_account_rgs; een EU-inkoop met het btw-paar komt in 4B én 5B van de aangifte."""
    from sqlledger import ArapLedger
    from sqlledger.arap import ArapLine, ArapTransaction
    from sqlledger.vatreturn import period_of
    name, info = _create(admin, "nl-eenmanszaak", employee_login="anna")
    assert info["accounts"] == 160
    with _connect(name) as conn:
        rgs = dict(conn.execute("""SELECT c.accno, r.code FROM x_account_rgs r JOIN chart c ON c.id = r.chart_id
                                   WHERE r.version = '3.8'""").fetchall())
        assert len(rgs) == len(chartfiles.get("nl-eenmanszaak").rows)
        assert rgs["1001"] == "BLimKasKas" and rgs["1570"] == "BSchBepBlu" and rgs["4000"] == "WPer"
        rates = dict(conn.execute("SELECT c.accno, t.rate::numeric FROM tax t JOIN chart c ON c.id = t.chart_id")
                     .fetchall())
        assert rates["1570"] == Decimal("-0.21") and rates["1571"] == Decimal("0.21")
        conn.execute("INSERT INTO vendor (id, name, vendornumber, taxnumber) VALUES (9001, 'Lieferant', 'L1', 'DE1')")
        conn.execute("INSERT INTO vendortax (vendor_id, chart_id) SELECT 9001, id FROM chart "
                     "WHERE accno IN ('1570', '1571')")
        cfg = UserConfig(login="anna")
        al = ArapLedger(conn, cfg)
        d = date(2026, 2, 5)
        t = ArapTransaction(vc="vendor", vc_id=9001, transdate=d, arap_accno="1600",
                            lines=[ArapLine(accno="7001", amount=Decimal("200"))],
                            taxrates=al.vc_defaults("vendor", 9001, d)["taxrates"])
        t.taxes = al.calculate_taxes(t)            # zoals het scherm: -21 % en +21 %, samen 0
        assert t.taxes == {"1570": Decimal("-42"), "1571": Decimal("42")}
        al.post_transaction(t)
        booked = dict(conn.execute("""SELECT c.accno, sum(a.amount) FROM acc_trans a JOIN chart c ON c.id = a.chart_id
                                      GROUP BY c.accno""").fetchall())
        assert booked == {"1570": Decimal("42"), "1571": Decimal("-42"), "1600": Decimal("200"),
                          "7001": Decimal("-200")}
        rep = VatReturns(conn, cfg).report(period_of("quarter", d))
        got = {c: (b.base_exact, b.tax_exact) for c, b in rep.boxes.items() if b.base_exact or b.tax_exact}
        assert got == {"4B": (Decimal("200"), Decimal("42")), "5B": (Decimal("200"), Decimal("42"))}
        assert rep.totals["5A"] == 42 and rep.totals["5B"] == 42 and not rep.split_warnings


def test_swiss_chart():
    """Deelstap 3 (CH1/CH2/CH3 A): het KMU-schema van 2012 hernummerd naar de huidige Kontenrahmen KMU, btw 2024, vijf talen."""
    chart = chartfiles.get("ch-kmu")
    assert chart.country == "CH" and chart.languages == ("de", "fr", "it", "en", "nl") and chart.currency == "CHF"
    assert (chart.name("de"), chart.name("fr"), chart.name("it"), chart.name("nl")) == ("KMU", "PME", "PMI", "MKB")
    acc = {r.accno: r for r in chart.accounts}
    taxes = {a: (r.rate, r.box) for a, r in acc.items() if r.rate is not None}
    assert taxes == {"1170": (Decimal("0.081"), "400"), "1171": (Decimal("0.026"), "400"),
                     "1172": (Decimal("0.038"), "400"), "2200": (Decimal("0.081"), "303"),
                     "2201": (Decimal("0.026"), "313"), "2202": (Decimal("0.038"), "343")}
    assert not acc["1173"].link and not acc["1173"].box          # Vorsteuer Investitionen: gewone rekening (keuze 3)
    # standaardrekeningen gelijk aan leeg-ch (keuze 5, CH3)
    assert chart.defaults == chartfiles.get("leeg-ch").defaults
    assert acc["6940"].descriptions["de"] == "Kursdifferenzen" and "3950" not in acc and "3955" not in acc
    # hernummerd zoals de huidige KMU; eigen vermogen als Q (bron: L)
    assert acc["6800"].descriptions["de"] == "Abschreibungen" and acc["6900"].descriptions["de"] == "Zinsaufwand"
    assert acc["2261"].descriptions["de"] == "Dividenden, Tantiemen" and acc["1480"].descriptions["de"] == "Beteiligungen"
    assert all(acc[a].category == "Q" for a in ("2800", "2900", "2970", "2979"))
    assert all(r.contra for a, r in acc.items() if a.startswith("1") and a.endswith("9") and a >= "1500")
    heads = {r.accno: r for r in chart.rows if r.charttype == "H"}
    assert heads["260"].descriptions["de"] == "Langfristige Rückstellungen"        # bron: "Lanfristige"
    assert acc["1000"].descriptions == {"de": "Kasse", "fr": "Caisse", "it": "Cassa", "en": "Cash", "nl": "Kas"}


def test_create_swiss_company_in_italian(admin):
    """Italiaans als taal van de rekeningnamen; MWST-Ziffern met landcode CH, de NL-aangifte blijft uit (RS9)."""
    name, info = _create(admin, "ch-kmu", language="it")
    assert info["accounts"] == 109 and info["tax_accounts"] == 6 and info["currency"] == "CHF"
    with _connect(name) as conn:
        assert conn.execute("SELECT description FROM chart WHERE accno = '2200'").fetchone()[0] == "IVA dovuta 8,1 %"
        boxes = dict(conn.execute("""SELECT c.accno, b.box FROM x_vat_box b JOIN chart c ON c.id = b.chart_id
                                     WHERE b.country = 'CH'""").fetchall())
        assert boxes == {"1170": "400", "1171": "400", "1172": "400", "2200": "303", "2201": "313", "2202": "343"}
        vat = VatReturns(conn, UserConfig(login="anna"))
        assert not vat.configured() and vat.boxes() == {}
        assert all(a["box"] is None for a in vat.tax_accounts())
        # een NL-rubriek naast de CH-Ziffer op dezelfde rekening kan (sleutel rekening + land)
        cid = conn.execute("SELECT id FROM chart WHERE accno = '2200'").fetchone()[0]
        vat.save_settings({cid: "1A"}, "2209", "6990", login="anna")
        assert vat.boxes() == {cid: "1A"}
        assert conn.execute("SELECT box FROM x_vat_box WHERE chart_id = %s AND country = 'CH'", (cid,)).fetchone()[0] \
            == "303"


def test_uk_chart():
    """Deelstap 4 (UK1–UK5 A): UK_General met kopjes, x001-nummers zoals leeg-uk, btw 20/5/0 % met VAT100-box 1/4."""
    chart = chartfiles.get("uk-ltd")
    assert chart.country == "GB" and chart.currency == "GBP" and chart.languages == ("en", "nl", "de", "fr")
    assert chart.defaults == chartfiles.get("leeg-uk").defaults
    acc = {r.accno: r for r in chart.accounts}
    assert {a: (r.rate, r.box) for a, r in acc.items() if r.rate is not None} == {
        "2200": (Decimal("0.20"), "1"), "2201": (Decimal("0.05"), "1"), "2203": (Decimal("0"), "1"),
        "2210": (Decimal("0.20"), "4"), "2211": (Decimal("0.05"), "4"), "2212": (Decimal("0"), "4"),
        # UK5 (BTW_LANDEN_ANALYSE.md): verlegde btw als paar (te betalen negatief, aftrek in box 4)
        "2204": (Decimal("-0.20"), "1"), "2205": (Decimal("-0.20"), "1R"), "2206": (Decimal("-0.20"), "1R"),
        "2214": (Decimal("0.20"), "4"), "2215": (Decimal("0.20"), "4"), "2216": (Decimal("0.20"), "4")}
    # elke rekening van het lege schema zit erin met dezelfde koppeling
    for r in chartfiles.get("leeg-uk").accounts:
        assert r.accno in acc and set(r.link) <= set(acc[r.accno].link), r.accno
    # UK4: creditcard is een schuld, dividenden eigen vermogen, één koersverschilrekening, afschrijving goodwill contra
    assert acc["2120"].category == "L" and acc["3300"].category == "Q"
    assert "4920" not in acc and "5220" not in acc and acc["0012"].contra
    assert 106 <= len(chart.accounts) <= 116


def test_create_uk_company(admin):
    name, info = _create(admin, "uk-ltd")
    assert info["accounts"] == 113 and info["tax_accounts"] == 12 and info["currency"] == "GBP"
    with _connect(name) as conn:
        boxes = dict(conn.execute("""SELECT c.accno, b.box FROM x_vat_box b JOIN chart c ON c.id = b.chart_id
                                     WHERE b.country = 'GB'""").fetchall())
        assert boxes == {"2200": "1", "2201": "1", "2203": "1", "2210": "4", "2211": "4", "2212": "4",
                         "2204": "1", "2205": "1R", "2206": "1R", "2214": "4", "2215": "4", "2216": "4"}
        assert conn.execute("SELECT rate::numeric FROM tax t JOIN chart c ON c.id = t.chart_id "
                            "WHERE c.accno = '2203'").fetchone()[0] == 0


def test_french_chart():
    """Deelstap 5 (FR1–FR5 A): PCG abrégé, nomenclatuur 2025, nummers van leeg-fr, CA3-regels per btw-rekening."""
    chart = chartfiles.get("fr-pcg")
    assert chart.country == "FR" and chart.languages == ("fr", "en", "nl", "de")
    assert chart.defaults == chartfiles.get("leeg-fr").defaults
    acc = {r.accno: r for r in chart.accounts}
    for r in chartfiles.get("leeg-fr").accounts:
        assert r.accno in acc and set(r.link) <= set(acc[r.accno].link) and r.rate == acc[r.accno].rate, r.accno
    assert {a: r.box for a, r in acc.items() if r.rate is not None} == {
        "445660": "20", "445661": "20", "445662": "20", "445663": "20",
        "445710": "08", "445711": "9B", "445712": "09", "445713": "T6"}
    assert not acc["445620"].link                                   # FR4: zonder btw-koppeling
    assert "766000" not in acc and not any(a.startswith("79") for a in acc)      # FR2; 79 vervalt (ANC 2022-06)
    assert "657000" in acc and "757000" in acc and "675000" not in acc and "775000" not in acc
    # B1–B7 tegen de ANC-lijst 1-1-2025: koersverschil commercieel 656, subsidiequote 747, geen 514/671/771/777/158
    assert chart.defaults["koersverschil"] == "656000" and "747000" in acc and "152000" in acc
    assert not {"514000", "666000", "671000", "771000", "777000", "158000"} & set(acc)
    assert all(r.contra for a, r in acc.items() if a[:2] in ("28", "29", "39", "49", "59"))
    assert 160 <= len(chart.accounts) <= 200


def test_create_french_company(admin):
    name, info = _create(admin, "fr-pcg", language="nl")
    assert info["accounts"] == 175 and info["tax_accounts"] == 8
    with _connect(name) as conn:
        assert conn.execute("SELECT description FROM chart WHERE accno = '411000'").fetchone()[0] == "Debiteuren"
        assert conn.execute("SELECT count(*) FROM x_vat_box WHERE country = 'FR'").fetchone()[0] == 8


def test_belgian_chart():
    """Deelstap 6 (BE1–BE4 A): MKB-selectie uit het MAR, nummers van leeg-be; btw per land BE3: verkoop-btw op het
    rooster van de grondslag (01/02/03, btw in 54), aftrek 59, verlegde paren 86/88/87/57 + 411100."""
    chart = chartfiles.get("be-mar")
    assert chart.country == "BE" and chart.languages == ("nl", "fr", "en", "de")
    assert chart.defaults == chartfiles.get("leeg-be").defaults
    acc = {r.accno: r for r in chart.accounts}
    for r in chartfiles.get("leeg-be").accounts:
        assert r.accno in acc and set(r.link) <= set(acc[r.accno].link) and r.rate == acc[r.accno].rate, r.accno
    assert {a: r.box for a, r in acc.items() if r.rate is not None} == {
        "411006": "59", "411012": "59", "411021": "59", "411100": "59", "451006": "01", "451012": "02",
        "451021": "03", "451157": "57", "451186": "86", "451187": "87", "451188": "88"}
    # BL3/BE2: inkoop per kosten-/activarekening
    assert acc["604000"].box == acc["340000"].box == "81" and acc["612000"].box == "82" and acc["230000"].box == "83"
    assert not any(a[:2] in ("69", "79") for a in acc) and "754000" not in acc            # BE3, BE4
    assert all(acc[a].contra for a in ("209000", "219000", "229000", "239000", "249000", "349000", "409000"))
    assert acc["430000"].category == "L" and 110 <= len(chart.accounts) <= 140
    # B1–B6 tegen de MAR-tekst van de CBN: 164/638/642 i.p.v. 163/637/641, kopjes 42 en 43, 433 rekening-courant
    assert {"164000", "638000", "642000", "420000", "433000"} <= set(acc)
    assert not {"163000", "637000", "641000"} & set(acc)


def test_create_belgian_company_in_french(admin):
    name, info = _create(admin, "be-mar", language="fr")
    assert info["accounts"] == 133 and info["tax_accounts"] == 11
    with _connect(name) as conn:
        assert conn.execute("SELECT description FROM chart WHERE accno = '451021'").fetchone()[0] == "TVA à payer 21 %"
        assert conn.execute("SELECT count(*) FROM x_vat_box WHERE country = 'BE'").fetchone()[0] == 11
        assert conn.execute("SELECT box FROM x_vat_account_box b JOIN chart c ON (c.id = b.chart_id) "
                            "WHERE c.accno = '613000' AND b.country = 'BE'").fetchone()[0] == "82"
