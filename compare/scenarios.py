"""Testscenario's die zowel door de Perl-originele code als door de Python-port
worden uitgevoerd. Bedragen zijn canonieke strings ('1234.56'); de Perl-driver
zet ze om naar het numberformat van het scenario.

'setup' bevat SQL die vóór de operaties in beide databases wordt uitgevoerd.
'expect_diff' beschrijft bewuste, gedocumenteerde afwijkingen.
"""

ADMIN = {"login": "admin", "numberformat": "1,000.00", "dateformat": "yyyy-mm-dd", "countrycode": ""}


def line(accno, debit="", credit="", **kw):
    return {"accno": accno, "debit": debit, "credit": credit, **kw}


SIMPLE = {
    "transdate": "2026-03-02", "description": "Kasopname",
    "lines": [line("1001", debit="50.00"), line("1100", credit="50.00")],
}

# in echte data heeft elke boeking een koers; in de seed ontbreekt die (Perl rekent dan met 0)
# (contactpersoon 11 weg: de ouderdomsanalyse van Perl verdubbelt anders de posten, README 17)
PAY_SETUP = ["DELETE FROM contact WHERE id = 11",
             "UPDATE ar SET exchangerate = 1 WHERE exchangerate IS NULL",
             "UPDATE ap SET exchangerate = 1 WHERE exchangerate IS NULL"]

# kortingsrekeningen; de oude tarieven (19%/6%) krijgen een einddatum zoals in echte data
DISCOUNT_SETUP = ["INSERT INTO chart (accno, description, charttype, category, link) VALUES "
                  "('8590', 'Kortingen verleend', 'A', 'E', 'AR_discount'), "
                  "('8595', 'Kortingen ontvangen', 'A', 'I', 'AP_discount')",
                  "UPDATE tax SET validto = '2026-06-30' WHERE rate IN (0.19, 0.06)"]

SCENARIOS = [
    {
        "name": "eenvoudige boeking + lezen",
        "config": ADMIN,
        "ops": [
            {"op": "post", "save_as": "t1", "trans": SIMPLE},
            {"op": "get", "id": "$t1"},
            {"op": "list", "search": {}},
            {"op": "accounts"},
            {"op": "account_tx", "args": {"accno": "1001"}},
        ],
    },
    {
        "name": "meerdere regels, afdeling, project, bron/memo, notities, eigen referentie",
        "config": ADMIN,
        "ops": [
            {"op": "post", "save_as": "t1", "trans": {
                "transdate": "2026-04-10", "reference": "MEM-1", "description": "Verdeling kosten",
                "notes": "Q2 verdeling", "department_id": 600,
                "lines": [
                    line("4100", debit="100.10", source="BON-1", memo="huur", project_id=700),
                    line("4110", debit="20.20", memo="schoonmaak", project_id=701),
                    line("1100", credit="120.30", source="BANK-7"),
                    line("4120", source="alleen bron"),
                ]}},
            {"op": "get", "id": "$t1"},
            {"op": "list", "search": {"department_id": 600}},
            {"op": "account_tx", "args": {"accno": "4100", "project_id": 700}},
            {"op": "account_tx", "args": {"accno": "4100", "department_id": 600}},
        ],
    },
    {
        "name": "automatische nummering glnumber",
        "config": ADMIN,
        "ops": [
            {"op": "post", "save_as": "a", "trans": SIMPLE},
            {"op": "post", "save_as": "b", "trans": SIMPLE},
            {"op": "get", "id": "$b"},
        ],
    },
    {
        "name": "nummersjablonen <%YYYY%>, <%DD%>, <%CURR%>",
        "config": ADMIN,
        "setup": ["UPDATE defaults SET fldvalue = 'GL<%YYYY%>/<%MM-DD%>-<%CURR%>-0009' WHERE fldname = 'glnumber'"],
        "ops": [
            {"op": "post", "save_as": "a", "trans": SIMPLE},
            {"op": "get", "id": "$a"},
        ],
    },
    {
        "name": "nummerveld bestaat nog niet",
        "config": ADMIN,
        "setup": ["DELETE FROM defaults WHERE fldname = 'glnumber'"],
        "ops": [
            {"op": "post", "save_as": "a", "trans": SIMPLE},
            {"op": "post", "save_as": "b", "trans": SIMPLE},
        ],
    },
    {
        "name": "vreemde valuta USD met koers",
        "config": ADMIN,
        "ops": [
            {"op": "post", "save_as": "t1", "trans": {
                "transdate": "2026-05-01", "description": "USD betaling", "currency": "USD",
                "exchangerate": "1.0975",
                "lines": [line("4100", debit="333.33"), line("1100", credit="333.33")]}},
            {"op": "get", "id": "$t1"},
            {"op": "list", "search": {}},
            {"op": "accounts"},
            {"op": "post", "save_as": "t2", "trans": {
                "transdate": "2026-05-01", "description": "tweede koers zelfde dag", "currency": "USD",
                "exchangerate": "1.10",
                "lines": [line("4100", debit="10"), line("1100", credit="10")]}},
        ],
    },
    {
        "name": "bestaande boeking wijzigen (cleared blijft)",
        "config": ADMIN,
        "ops": [
            {"op": "post", "save_as": "t1", "trans": SIMPLE},
            {"op": "post", "save_as": "t1b", "trans": {
                "id": "$t1", "transdate": "2026-03-03", "reference": "GL-0100",
                "description": "Kasopname gecorrigeerd",
                "lines": [line("1001", debit="75", cleared="2026-03-31"),
                          line("1100", credit="75")]}},
            {"op": "get", "id": "$t1"},
            {"op": "list", "search": {}},
        ],
    },
    {
        "name": "verwijderen",
        "config": ADMIN,
        "ops": [
            {"op": "post", "save_as": "t1", "trans": {**SIMPLE, "references": [{"description": "bon"}],
                                                       "recurring": {"startdate": "2026-03-02", "repeat": 1,
                                                                     "unit": "months", "howmany": 3}}},
            {"op": "delete", "id": "$t1"},
            {"op": "list", "search": {}},
        ],
    },
    {
        "name": "validaties",
        "config": ADMIN,
        "setup": [
            "INSERT INTO gl (id, reference, transdate, approved, curr) VALUES (5000, 'OUD-1', '2025-06-30', 't', 'EUR')",
            "INSERT INTO acc_trans (trans_id, chart_id, amount, transdate) VALUES (5000, (SELECT id FROM chart WHERE accno='1001'), -5, '2025-06-30'), (5000, (SELECT id FROM chart WHERE accno='1100'), 5, '2025-06-30')",
        ],
        "ops": [
            {"op": "post", "trans": {"transdate": "2026-03-02",
                                     "lines": [line("1001", debit="50"), line("1100", credit="49.99")]}},
            {"op": "post", "trans": {"transdate": "2026-03-02",
                                     "lines": [line("1001", debit="50", credit="50"), line("1100", credit="0")]}},
            {"op": "post", "trans": {"transdate": "2025-12-31",
                                     "lines": [line("1001", debit="50"), line("1100", credit="50")]}},
            {"op": "post", "trans": {"transdate": "",
                                     "lines": [line("1001", debit="50"), line("1100", credit="50")]}},
            {"op": "post", "trans": {"id": 5000, "transdate": "2026-01-15",
                                     "lines": [line("1001", debit="5"), line("1100", credit="5")]}},
            {"op": "delete", "id": 5000},
            {"op": "post", "trans": {"transdate": "2026-03-02",
                                     "lines": [line("1001", debit="0.004"), line("1100", credit="0.001")]}},
        ],
        "expect_diff": {
            "op 7": "README 40: bedragen met meer decimalen dan de precisie (0.004/0.001) weigert Python; "
                    "Perl slaat ze op en de boeking is een fractie uit balans.",
            "tabel gl": "idem: Perl schrijft de boeking met 0.004/0.001",
            "tabel acc_trans": "idem",
            "tabel defaults": "idem: Perl hoogt het boekingsnummer op",
            "tabel audittrail": "idem",
        },
    },
    {
        "name": "revtrans: boekingen niet meer wijzigbaar",
        "config": ADMIN,
        "ops": [
            {"op": "post", "save_as": "t1", "trans": SIMPLE},
            {"op": "sql", "sql": "UPDATE defaults SET fldvalue = '1' WHERE fldname = 'revtrans'"},
            {"op": "post", "trans": {**SIMPLE, "id": "$t1"}},
            {"op": "delete", "id": "$t1"},
        ],
    },
    {
        "name": "pending (niet goedgekeurd)",
        "config": ADMIN,
        "ops": [
            {"op": "post", "save_as": "t1", "trans": {**SIMPLE, "pending": True}},
            {"op": "list", "search": {}},
            {"op": "accounts"},
            {"op": "account_tx", "args": {"accno": "1001"}},
        ],
    },
    {
        "name": "batch/voucher",
        "config": ADMIN,
        "ops": [
            {"op": "post", "save_as": "t1", "trans": {**SIMPLE, "batch_id": 800, "pending": True}},
            {"op": "post", "save_as": "t2", "trans": {"transdate": "2026-02-01", "batch_id": 800, "pending": True,
                                                      "lines": [line("4100", debit="12.50"), line("1001", credit="12.50")]}},
            {"op": "post", "save_as": "t1b", "trans": {**SIMPLE, "id": "$t1", "batch_id": 800, "pending": True,
                                                       "lines": [line("1001", debit="60"), line("1100", credit="60")]}},
            {"op": "get", "id": "$t1"},
            {"op": "delete", "id": "$t2", "batch_id": 800},
        ],
        "expect_diff": {"tabel br": "Perl werkt het batchtotaal niet bij bij verwijderen (typefout $mount)."},
    },
    {
        "name": "herhaalschema",
        "config": ADMIN,
        "ops": [
            {"op": "post", "save_as": "t1", "trans": {**SIMPLE, "recurring": {
                "reference": "HUUR", "description": "maandhuur", "startdate": "2026-01-15",
                "repeat": 1, "unit": "months", "howmany": 12, "payment": False,
                "email": [["transaction", "pdf"]], "print": [["transaction", "postscript", "lpr"]],
                "message": "zie bijlage"}}},
            {"op": "post", "save_as": "t2", "trans": {**SIMPLE, "recurring": {
                "startdate": "2027-01-01", "repeat": 2, "unit": "weeks", "howmany": 4}}},
            {"op": "post", "save_as": "t3", "trans": {**SIMPLE, "recurring": {
                "startdate": "2026-01-01", "repeat": 1, "unit": "days", "howmany": 5,
                "nextdate": "2026-02-01"}}},
            {"op": "get", "id": "$t1"},
        ],
    },
    {
        "name": "referentiedocumenten",
        "config": ADMIN,
        "setup": ["INSERT INTO archive (id, filename) VALUES (1, 'factuur.pdf'), (2, 'oud.pdf')",
                  "SELECT setval('archiveid', 10)"],
        "ops": [
            {"op": "post", "save_as": "t1", "trans": {**SIMPLE, "references": [
                {"description": "Factuur", "code": "F1", "archive_id": 1, "folder": "inkoop"},
                {"description": "Oud", "archive_id": 2},
                {"description": "Geheim", "confidential": True}]}},
            {"op": "post", "save_as": "t1b", "trans": {**SIMPLE, "id": "$t1", "references": [
                {"description": "Factuur", "code": "F1", "archive_id": 1, "folder": "inkoop"}]}},
            {"op": "get", "id": "$t1"},
        ],
    },
    {
        "name": "zoekfilters overzicht",
        "config": ADMIN,
        "ops": [
            {"op": "post", "save_as": "a", "trans": {"transdate": "2026-01-10", "reference": "MEM-A",
                                                     "description": "Huur januari", "notes": "contract 12",
                                                     "lines": [line("4100", debit="1000", source="S-1", memo="huur jan"),
                                                               line("1100", credit="1000", source="S-1")]}},
            {"op": "post", "save_as": "b", "trans": {"transdate": "2026-02-10", "reference": "MEM-B",
                                                     "description": "Huur februari", "department_id": 601,
                                                     "lines": [line("4100", debit="1000", memo="huur feb"),
                                                               line("1001", credit="400"), line("1100", credit="600")]}},
            {"op": "post", "save_as": "c", "trans": {"transdate": "2026-03-15", "reference": "MEM-C",
                                                     "description": "Verdeling",
                                                     "lines": [line("4100", debit="30"), line("4110", debit="70"),
                                                               line("1100", credit="100")]}},
            {"op": "list", "search": {"accno": "1100", "datefrom": "2026-02-01"}},
            {"op": "list", "search": {"description": "huur"}},
            {"op": "list", "search": {"reference": "\"MEM-B\""}},
            {"op": "list", "search": {"source": "s-1"}},
            {"op": "list", "search": {"memo": "feb"}},
            {"op": "list", "search": {"notes": "contract"}},
            {"op": "list", "search": {"amountfrom": "500", "amountto": "1001"}},
            {"op": "list", "search": {"splitledger": True, "datefrom": "2026-02-01"}},
            {"op": "list", "search": {"category": "E"}},
            {"op": "list", "search": {"year": 2026, "month": "02"}},
            {"op": "list", "search": {"year": 2026, "month": "01", "interval": 3}},
            {"op": "list", "search": {"accnofrom": "1000", "accnoto": "1100"}},
            {"op": "list", "search": {"name": "klant"}},
            {"op": "list", "search": {"vcnumber": "l001"}},
            {"op": "list", "search": {"department_id": 601}},
            {"op": "list", "search": {"include_lineitem": True}},
            {"op": "list", "search": {"sort": "transdate", "direction": "DESC"}},
            {"op": "list", "search": {"gifi_accno": "G1"}},
        ],
        "expect_diff": {
            "op 4": "Perl-bug: zonder AR/AP-regel in de selectie doet Perl pop() op een lege lijst en zet de tegenrekening op $form->{GL}[undef], d.w.z. op de eerste regel van het overzicht.",
            "op 20": "Perl geeft de bankrekening als tegenrekening aan de factuurregel i.p.v. de "
                     "betaalregel (keuze hangt af van rijvolgorde); Python kiest de betaalkant.",
        },
    },
    {
        "name": "rekeningmutaties scherm (ca.pl)",
        "config": dict(ADMIN, numberformat="1.000,00"),
        "ops": [
            {"op": "post", "trans": {"transdate": "2026-02-10", "department_id": 600,
                                     "lines": [line("1001", debit="10", project_id=700), line("1100", credit="10")]}},
            {"op": "account_screen", "args": {"accno": "1200"}},
            {"op": "account_screen", "args": {"accno": "1100", "fromdate": "2026-03-05"}},
            {"op": "account_screen", "args": {"accno": "1100", "fromdate": "2026-03-05", "todate": "2026-12-31",
                                              "sort": "reference", "direction": "DESC", "l_subtotal": 1}},
            {"op": "account_screen", "args": {"accno": "1100", "sort": "transdate", "l_subtotal": 1}},
            {"op": "account_screen", "args": {"accno": "1600", "fromdate": "2026-04-01", "l_subtotal": 1}},
            {"op": "account_screen", "args": {"accno": "8520", "fromdate": "2026-04-01"}},
            {"op": "account_screen", "args": {"accno": "0235", "fromdate": "2026-06-01"}},
            {"op": "account_screen", "args": {"gifi_accno": "G1"}},
            {"op": "account_screen", "args": {"accno": "1001", "year": 2026, "month": "02"}},
            {"op": "account_screen", "args": {"accno": "4100", "project_id": 700}},
        ],
    },
    {
        "name": "memoriaalscherm (gl.pl) als browser",
        "config": dict(ADMIN, numberformat="1.000,00"),
        "setup": [
            "UPDATE acc_trans SET cleared = '2026-06-30' WHERE trans_id = 5001 AND amount < 0",
            "INSERT INTO recurring (id, reference, startdate, nextdate, repeat, unit, howmany, payment) "
            "VALUES (5001, 'MEM-BANK', '2026-06-30', '2026-07-31', 1, 'months', 6, '0')",
        ],
        "ops": [
            {"op": "gl_ui", "save_as": "g1", "args": {
                "transdate": "2026-07-01", "description": "Kasstorting", "notes": "n", "department_id": 600,
                "lines": [{"accno": "1001", "debit": "1250.50", "source": "K1", "project_id": 700},
                          {"accno": "1100", "credit": "1250.50", "memo": "storting"}]}},
            {"op": "gl_ui", "args": {"id": "$g1", "lines": [
                {"accno": "1001", "debit": "60"}, {"accno": "1100", "credit": "50"},
                {"accno": "4100", "credit": "10", "memo": "extra"}]}},
            {"op": "gl_ui", "save_as": "g2", "args": {"id": "$g1", "mode": "post_as_new", "transdate": "2026-07-02",
                                                      "lines": [{"accno": "1001", "debit": "7"},
                                                                {"accno": "1100", "credit": "7"}]}},
            {"op": "gl_ui", "args": {"id": "$g2", "mode": "delete"}},
            {"op": "gl_ui", "args": {"transdate": "2026-07-03", "currency": "USD", "exchangerate": "0.9",
                                     "lines": [{"accno": "1001", "debit": "100"}, {"accno": "1100", "credit": "100"}]}},
            {"op": "gl_ui", "args": {"transdate": "2026-07-05", "mode": "new_number",
                                     "lines": [{"accno": "1001", "debit": "1"}]}},
            {"op": "gl_ui", "args": {"id": 5001, "description": "Rente juni", "lines": [
                {"accno": "1100", "debit": "2.50", "source": "RENTE", "memo": "gewijzigd"},
                {"accno": "8520", "credit": "2.50"}]}},
            {"op": "gl_ui", "args": {"transdate": "2026-07-06", "reference": "EIGEN-1", "lines": [
                {"accno": a, "debit": "1.11"} for a in ("1001", "1110", "4100", "4110", "8520", "8530")] + [
                {"accno": a, "credit": "1.11"} for a in ("1100", "1200", "0235", "4210", "4220", "8540")]}},
            {"op": "gl_ui", "args": {"transdate": "2026-07-07", "lines": [
                {"accno": "1001", "debit": "10"}, {"accno": "1100", "credit": "9.99"}]}},
            {"op": "gl_ui", "args": {"transdate": "2025-12-30", "lines": [
                {"accno": "1001", "debit": "10"}, {"accno": "1100", "credit": "10"}]}},
            {"op": "gl_ui", "args": {"transdate": "2026-07-08", "lines": [
                {"accno": "1001", "debit": "10", "credit": "10"}, {"accno": "1100", "credit": "0"}]}},
        ],
        "expect_diff": {
            "tabel recurring": "README 43: Perl geeft bij opnieuw opslaan de volgende herhaaldatum niet mee en "
                               "rekent die opnieuw uit vanaf vandaag (2026-07-31 -> later): achterstallige "
                               "herhalingen worden overgeslagen. Python behoudt de volgende datum.",
        },
    },
    {
        "name": "koersverschilboeking via het scherm (gl.pl fxadj)",
        "config": dict(ADMIN, numberformat="1.000,00"),
        "ops": [
            {"op": "gl_ui", "args": {"fxadj": True, "transdate": "2026-07-04",
                                     "lines": [{"accno": "1100", "debit": "5", "fx": True},
                                               {"accno": "8520", "credit": "5"}]}},
            {"op": "gl_ui", "args": {"fxadj": True, "transdate": "2026-07-04",
                                     "lines": [{"accno": "8520", "debit": "3"},
                                               {"accno": "1100", "credit": "3", "fx": True}]}},
        ],
        "expect_diff": {
            "tabel acc_trans": "README 10: Perl's $cleared lekt van de FX-regel naar de volgende regels "
                               "(8520 krijgt ook een afletterdatum); Python per regel.",
        },
    },
    {
        "name": "journaaloverzicht scherm (gl.pl)",
        "config": dict(ADMIN, numberformat="1.000,00"),
        "ops": [
            {"op": "gl_screen", "search": {}},
            {"op": "gl_screen", "search": {"l_subtotal": 1}},
            {"op": "gl_screen", "search": {"sort": "reference", "direction": "DESC", "l_subtotal": 1}},
            {"op": "gl_screen", "search": {"accno": "1100"}},
            {"op": "gl_screen", "search": {"accno": "1100", "datefrom": "2026-03-05", "l_subtotal": 1}},
            {"op": "gl_screen", "search": {"accno": "1200", "datefrom": "2026-02-01", "dateto": "2026-12-31"}},
            {"op": "gl_screen", "search": {"accno": "4100", "datefrom": "2026-01-01"}},
            {"op": "gl_screen", "search": {"gifi_accno": "G1"}},
            {"op": "gl_screen", "search": {"splitledger": True}},
            {"op": "gl_screen", "search": {"splitledger": True, "datefrom": "2026-03-01"}},
            {"op": "gl_screen", "search": {"splitledger": True, "accno": "1100", "datefrom": "2026-03-01"}},
            {"op": "gl_screen", "search": {"category": "E", "datefrom": "2026-01-01", "dateto": "2026-06-30"}},
            {"op": "gl_screen", "search": {"accnofrom": "1000", "accnoto": "1999", "l_subtotal": 1}},
            {"op": "gl_screen", "search": {"description": "huur", "sort": "description", "l_subtotal": 1}},
            {"op": "gl_screen", "search": {"year": 2026, "month": "03", "interval": 1}},
            {"op": "gl_screen", "search": {"amountfrom": "100", "amountto": "5000"}},
        ],
    },
    {
        "name": "journaaloverzicht scherm splitledger + subtotaal (gl.pl)",
        "config": dict(ADMIN, numberformat="1.000,00"),
        "ops": [
            {"op": "gl_screen", "search": {"splitledger": True, "l_subtotal": 1}},
        ],
        "expect_diff": {
            "op 1": "Perl-bug (README 39): bij splitledger loopt het subtotaal per sorteerwaarde door over "
                    "rekeningen heen; Python sluit de groep af bij een nieuwe rekening.",
        },
    },
    {
        "name": "rekeningmutaties (CA)",
        "config": ADMIN,
        "ops": [
            {"op": "post", "trans": {"transdate": "2026-02-10", "department_id": 600,
                                     "lines": [line("1001", debit="10", project_id=700), line("1100", credit="10")]}},
            {"op": "account_tx", "args": {"accno": "1200"}},
            {"op": "account_tx", "args": {"accno": "1100", "fromdate": "2026-03-05"}},
            {"op": "account_tx", "args": {"accno": "1100", "fromdate": "2026-03-05", "todate": "2026-12-31",
                                          "sort": "reference", "direction": "DESC"}},
            {"op": "account_tx", "args": {"gifi_accno": "G1"}},
            {"op": "account_tx", "args": {"accno": "1001", "department_id": 600}},
            {"op": "account_tx", "args": {"accno": "1001", "fromdate": "2026-03-01", "department_id": 600}},
            {"op": "account_tx", "args": {"accno": "1001", "year": 2026, "month": "02"}},
            {"op": "account_tx", "args": {"accno": "9998"}},
            {"op": "account_tx", "args": {"accno": "1200", "fromdate": "2026-03-05", "department_id": 600}},
        ],
        "setup": ["UPDATE ar SET department_id = 600 WHERE id = 950"],
        "expect_diff": {"op 9": "Onbekende rekening: Perl crasht op een lege SQL-query; Python geeft een lege lijst.",
                        "op 10": "Perl telt met afdeling alleen het gl-deel van het beginsaldo "
                                      "(de lus over ar/ap/gl overschrijft de query); Python telt alle drie."},
    },
    {
        "name": "vertaling (countrycode en)",
        "config": {**ADMIN, "countrycode": "en"},
        "ops": [
            {"op": "post", "save_as": "t1", "trans": SIMPLE},
            {"op": "get", "id": "$t1"},
            {"op": "accounts"},
            {"op": "list", "search": {"accno": "1100"}},
        ],
        "expect_diff": {"op 4": "Perl-bug: zonder AR/AP-regel in de selectie doet Perl pop() op een lege lijst en zet de tegenrekening op $form->{GL}[undef], d.w.z. op de eerste regel van het overzicht."},
    },
    {
        "name": "numberformat 1.000,00",
        "config": {**ADMIN, "numberformat": "1.000,00"},
        "ops": [
            {"op": "post", "save_as": "t1", "trans": {"transdate": "2026-03-02",
                                                      "lines": [line("1001", debit="1234.56"), line("1100", credit="1234.56")]}},
            {"op": "get", "id": "$t1"},
            {"op": "amounts",
             "parse": ["1.234,56", "1234,5", "-12,30", "", "abc", "1.234.567,891"],
             "format": [["1234.5", 2, ""], ["-1234567.891", 2, "-"], ["0", 2, "0"], ["0", 2, ""],
                        ["1001", 0, ""], ["-5", 2, "DRCR"], ["12.3", None, ""]],
             "round": [["2.675", 2], ["1.005", 2], ["-1.005", 2], ["1.00499", 2], ["0.125", 2], ["123.456", 0]]},
        ],
        "expect_diff": {"op 3": "1.00499 -> Perl 1.01, Python 1.00 (bewust: correcte afronding)."},
    },
    {
        "name": "numberformat 1'000.00 en 1000,00",
        "config": {**ADMIN, "numberformat": "1'000.00"},
        "ops": [
            {"op": "amounts", "parse": ["1'234.56", "-1'000'000.5"],
             "format": [["1234567.5", 2, ""], ["-0.5", 2, "-"]], "round": []},
        ],
    },
    {
        "name": "boeken op kop- of onbekende rekening",
        "config": ADMIN,
        "ops": [
            {"op": "post", "trans": {"transdate": "2026-03-02",
                                     "lines": [line("1000", debit="5"), line("1100", credit="5")]}},
            {"op": "post", "trans": {"transdate": "2026-03-02",
                                     "lines": [line("9998", debit="5"), line("1100", credit="5")]}},
        ],
        "expect_diff": {"*": "Perl boekt op kop-rekeningen en maakt bij een onbekende "
                                     "rekening een regel zonder rekening (chart_id NULL); Python weigert."},
    },
    {
        "name": "debiteuren/crediteuren tegenrekeningen",
        "config": ADMIN,
        "ops": [
            {"op": "list", "search": {}},
            {"op": "list", "search": {"accno": "1200"}},
        ],
    },

    # ------------------------------------------------------------- rapporten
    {
        "name": "proefbalans",
        "config": ADMIN,
        "ops": [
            {"op": "trial_balance", "args": {"fromdate": "2026-01-01", "todate": "2026-06-30"}},
            {"op": "trial_balance", "args": {"todate": "2025-12-31"}},
            {"op": "trial_balance", "args": {"fromdate": "2026-01-01", "todate": "2026-06-30", "all_accounts": 1}},
            {"op": "trial_balance", "args": {"year": 2026, "month": "02", "interval": 3}},
            {"op": "trial_balance", "args": {"fromdate": "2026-01-01", "department_id": 600}},
            {"op": "trial_balance", "args": {"fromdate": "2026-01-01", "project_id": 700}},
            {"op": "trial_balance", "args": {"fromdate": "2026-01-01", "accounttype": "gifi"}},
            {"op": "trial_balance", "args": {"fromdate": "2026-01-01", "l_heading": 0}},
            {"op": "trial_balance", "args": {"fromdate": "2026-01-01", "l_heading": 0, "l_subtotal": 0}},
        ],
    },
    {
        "name": "winst- en verliesrekening",
        "config": ADMIN,
        "ops": [
            {"op": "income_statement", "args": {"fromdate": "2026-01-01", "todate": "2026-06-30", "decimalplaces": 2}},
            {"op": "income_statement", "args": {"fromdate": "2025-01-01", "todate": "2025-12-31", "decimalplaces": 2}},
            {"op": "income_statement", "args": {"fromdate": "2026-01-01", "todate": "2026-06-30", "decimalplaces": 2,
                                                "previousyear": 1}},
            {"op": "income_statement", "args": {"fromdate": "2026-01-01", "todate": "2026-06-30", "decimalplaces": 2,
                                                "includeperiod": "month", "interval": 6}},
            {"op": "income_statement", "args": {"fromdate": "2026-01-01", "todate": "2026-12-31", "decimalplaces": 2,
                                                "includeperiod": "quarter", "previousyear": 1}},
            {"op": "income_statement", "args": {"fromdate": "2026-01-15", "todate": "2026-05-31", "decimalplaces": 0,
                                                "includeperiod": "month"}},
            {"op": "income_statement", "args": {"fromyear": 2026, "frommonth": "01", "interval": 3, "decimalplaces": 2}},
            {"op": "income_statement", "args": {"fromdate": "2026-01-01", "todate": "2026-06-30", "decimalplaces": 2,
                                                "department_id": 600}},
            {"op": "income_statement", "args": {"fromdate": "2026-01-01", "todate": "2026-06-30", "decimalplaces": 2,
                                                "project_id": 701}},
            {"op": "income_statement", "args": {"fromdate": "2026-01-01", "todate": "2026-06-30", "decimalplaces": 2,
                                                "method": "cash"}},
            {"op": "income_statement", "args": {"fromdate": "2026-01-01", "todate": "2026-06-30", "decimalplaces": 2,
                                                "accounttype": "gifi"}},
            {"op": "income_statement", "args": {"fromdate": "2026-01-01", "todate": "2026-06-30", "decimalplaces": 2,
                                                "currency": "USD"}},
            {"op": "income_statement", "args": {"fromdate": "2026-01-01", "todate": "2026-06-30", "decimalplaces": 2,
                                                "previousyear": 1, "reversedisplay": 1, "includeperiod": "quarter"}},
            {"op": "income_statement", "args": {"fromdate": "2026-01-01", "todate": "2026-06-30", "decimalplaces": 2,
                                                "l_subtotal": 0}},
        ],
    },
    {
        "name": "balans",
        "config": ADMIN,
        "ops": [
            {"op": "balance_sheet", "args": {"todate": "2026-06-30", "decimalplaces": 2}},
            {"op": "balance_sheet", "args": {"todate": "2025-12-31", "decimalplaces": 2}},
            {"op": "balance_sheet", "args": {"todate": "2026-06-30", "decimalplaces": 2, "previousyear": 1}},
            {"op": "balance_sheet", "args": {"todate": "2026-06-30", "decimalplaces": 2, "includeperiod": "quarter"}},
            {"op": "balance_sheet", "args": {"todate": "2026-06-30", "decimalplaces": 2, "includeperiod": "month",
                                             "previousyear": 1}},
            {"op": "balance_sheet", "args": {"toyear": 2026, "tomonth": "03", "decimalplaces": 2}},
            {"op": "balance_sheet", "args": {"todate": "2026-06-30", "decimalplaces": 2, "department_id": 600}},
            {"op": "balance_sheet", "args": {"todate": "2026-06-30", "decimalplaces": 2, "accounttype": "gifi"}},
            {"op": "balance_sheet", "args": {"todate": "2026-06-30", "decimalplaces": 2, "currency": "USD"}},
            {"op": "balance_sheet", "args": {"todate": "2026-06-30", "decimalplaces": 0}},
        ],
    },
    {
        "name": "rapporten in andere getalnotatie en taal",
        "config": {**ADMIN, "numberformat": "1.000,00", "countrycode": "en"},
        "expect_diff": {"op 1": "Perl-proefbalans vertaalt koppen en beginsaldi via language_code, maar de "
                                "rekeningen in de periode via $form->{countrycode} (leeg); Python vertaalt alles "
                                "met dezelfde taal."},
        "ops": [
            {"op": "trial_balance", "args": {"fromdate": "2026-01-01", "todate": "2026-06-30"}},
            {"op": "income_statement", "args": {"fromdate": "2026-01-01", "todate": "2026-06-30", "decimalplaces": 2}},
            {"op": "balance_sheet", "args": {"todate": "2026-06-30", "decimalplaces": 2}},
        ],
    },

    # --------------------------------------------- debiteuren/crediteuren en btw
    {
        "name": "ouderdomsanalyse met twee contactpersonen bij één klant",
        "config": ADMIN,
        "ops": [
            {"op": "aging", "args": {"arap": "ar", "todate": "2026-06-30"}},
            {"op": "reminder", "args": {}},
        ],
        "expect_diff": {"*": "Perl joint op contact: een klant met twee contactpersonen krijgt elke "
                             "openstaande factuur twee keer (ouderdomsanalyse en aanmaningen tellen dubbel). "
                             "Het scenario hieronder (zelfde data, één contactpersoon) is wél exact gelijk."},
    },
    {
        "name": "ouderdomsanalyse",
        "config": ADMIN,
        "setup": ["DELETE FROM contact WHERE id = 11"],
        "ops": [
            {"op": "aging", "args": {"arap": "ar", "todate": "2026-06-30"}},
            {"op": "aging", "args": {"arap": "ar", "todate": "2026-06-30", "summary": 1}},
            {"op": "aging", "args": {"arap": "ar", "todate": "2026-06-30", "buckets": ["c0", "c30", "c60"]}},
            {"op": "aging", "args": {"arap": "ar", "todate": "2026-06-30", "buckets": ["c0"]}},
            {"op": "aging", "args": {"arap": "ar", "todate": "2026-06-30", "overdue": 1}},
            {"op": "aging", "args": {"arap": "ar", "todate": "2026-05-31", "name": "klant"}},
            {"op": "aging", "args": {"arap": "ar", "year": 2026, "month": "06", "department_id": 600}},
            {"op": "aging", "args": {"arap": "ap", "todate": "2026-06-30"}},
            {"op": "aging", "args": {"arap": "ap", "todate": "2026-06-30", "vcnumber": "l002"}},
        ],
    },
    {
        "name": "aanmaningen",
        "config": ADMIN,
        "setup": ["DELETE FROM contact WHERE id = 11"],
        "ops": [
            {"op": "reminder", "args": {}},
            {"op": "reminder", "args": {"customer_id": 900}},
            {"op": "save_level", "levels": {"3006": 3, "3002": 1, "3007": 0}},
            {"op": "reminder", "args": {}},
        ],
    },
    {
        "name": "btw-rapport",
        "config": ADMIN,
        "ops": [
            {"op": "tax_report", "args": {"db": "ar", "accounts": ["1810", "1811"], "fromdate": "2026-01-01",
                                          "todate": "2026-06-30", "summary": 1}},
            {"op": "tax_report", "args": {"db": "ar", "accounts": ["1810", "1811"], "fromdate": "2026-01-01",
                                          "todate": "2026-06-30", "summary": 0}},
            {"op": "tax_report", "args": {"db": "ar", "accounts": ["1810"], "year": 2026, "month": "04",
                                          "interval": 3, "summary": 1, "l_subtotal": 1, "sort": "transdate"}},
            {"op": "tax_report", "args": {"db": "ar", "accounts": ["1810", "1811"], "fromdate": "2026-01-01",
                                          "todate": "2026-06-30", "summary": 1, "method": "cash"}},
            {"op": "tax_report", "args": {"db": "ar", "accounts": ["1810", "1811"], "fromdate": "2026-05-01",
                                          "todate": "2026-06-30", "summary": 0, "method": "cash"}},
            {"op": "tax_report", "args": {"db": "ar", "fromdate": "2026-01-01", "todate": "2026-06-30",
                                          "summary": 1, "nontaxable": 1}},
            {"op": "tax_report", "args": {"db": "ar", "fromdate": "2026-01-01", "todate": "2026-06-30",
                                          "summary": 0, "nontaxable": 1}},
            {"op": "tax_report", "args": {"db": "ap", "accounts": ["1800"], "fromdate": "2026-01-01",
                                          "todate": "2026-06-30", "summary": 1}},
            {"op": "tax_report", "args": {"db": "ar", "accounts": ["1810"], "fromdate": "2026-01-01",
                                          "todate": "2026-06-30", "department_id": 600, "summary": 1}},
            {"op": "tax_report", "args": {"db": "ar", "fromdate": "2026-01-01", "todate": "2026-06-30",
                                          "summary": 1}},
        ],
    },
    {
        "name": "betalingen",
        "config": ADMIN,
        "ops": [
            {"op": "payments", "args": {"db": "ar", "accounts": ["1100"], "fromdate": "2026-01-01",
                                        "todate": "2026-06-30"}},
            {"op": "payments", "args": {"db": "ap", "accounts": ["1100"], "fromdate": "2026-01-01",
                                        "todate": "2026-06-30"}},
            {"op": "payments", "args": {"db": "ar", "accounts": ["1100"], "fromdate": "2026-01-01",
                                        "todate": "2026-06-30", "l_subtotal": 1, "sort": "transdate"}},
            {"op": "payments", "args": {"db": "ar", "accounts": ["1100"], "year": 2026, "month": "04",
                                        "interval": 3, "name": "klant"}},
            {"op": "payments", "args": {"db": "ar", "accounts": ["1100"], "source": "bank", "memo": "termijn"}},
            {"op": "payments", "args": {"db": "ar", "accounts": ["1100"], "till": "1"}},
            {"op": "payments", "args": {"db": "ar", "accounts": ["1100"], "department_id": 600}},
        ],
    },
    {
        "name": "jaarafsluiting",
        "config": ADMIN,
        # zonder afsluitdatum: de afsluiting per 2025-12-31 (op 5) mag dan ook in Python (README 41)
        "setup": ["UPDATE defaults SET fldvalue = '' WHERE fldname = 'closedto'"],
        "ops": [
            {"op": "yearend", "args": {"todate": "2026-06-30", "accno": "0550", "description": "Afsluiting H1"}},
            {"op": "income_statement", "args": {"fromdate": "2026-01-01", "todate": "2026-12-31", "decimalplaces": 2}},
            {"op": "balance_sheet", "args": {"todate": "2026-06-30", "decimalplaces": 2}},
            {"op": "yearend", "args": {"todate": "2026-06-30", "accno": "0550", "reference": "AFS-2"}},
            {"op": "yearend", "args": {"todate": "2025-12-31", "accno": "0550"}},
            {"op": "trial_balance", "args": {"fromdate": "2025-01-01", "todate": "2026-12-31"}},
        ],
    },
    {
        "name": "jaarafsluiting in afgesloten periode",
        "config": ADMIN,
        "ops": [
            {"op": "yearend", "args": {"todate": "2026-06-30", "accno": "0550", "description": "Afsluiting H1"}},
            {"op": "yearend", "args": {"todate": "2025-12-31", "accno": "0550"}},
        ],
        "expect_diff": {
            "op 2": "README 41: per 2025-12-31 valt in de afgesloten periode (closedto 20251231); Python weigert, "
                    "Perl boekt en verwijdert daarbij ook de latere afsluiting en die van 2025.",
            "tabel gl": "idem", "tabel acc_trans": "idem", "tabel defaults": "idem: nummer opgehoogd in Perl",
            "tabel audittrail": "idem", "tabel yearend": "idem",
        },
    },

    # ------------------------------------------------- fase 1a: AR/AP-boekingen
    {
        "name": "verkoop- en inkoopboekingen",
        "config": ADMIN,
        "setup": ["DELETE FROM contact WHERE id = 11"],
        "expect_diff": {
            "tabel ap": "p1 heeft een regel op 4220 (niet als AP_amount gekoppeld): Perl laat die bij openen "
                        "en opnieuw opslaan vallen (factuur 992,19 -> 972,20); Python behoudt hem (README 25b)",
            "tabel acc_trans": "idem: regel op 4220 blijft bij Python bestaan",
            "op 15": "idem: Perl laadt de regel op 4220 niet",
        },
        "ops": [
            {"op": "arap_post", "save_as": "a1", "args": {
                "vc": "customer", "vc_id": 900, "transdate": "2026-07-01", "arap_accno": "1200",
                "description": "Advies juli", "notes": "n1", "ponumber": "PO-9", "ordnumber": "O-9",
                "lines": [{"accno": "8520", "amount": "100.00", "description": "uren"},
                          {"accno": "8530", "amount": "33.33", "project_id": 700}]}},
            {"op": "arap_post", "save_as": "a2", "args": {
                "vc": "customer", "vc_id": 902, "transdate": "2026-07-02", "arap_accno": "1200",
                "lines": [{"accno": "8520", "amount": "250.00"}],
                "payments": [{"accno": "1100", "amount": "100.00", "datepaid": "2026-07-03", "source": "B-1",
                              "memo": "deel"},
                             {"accno": "1100", "amount": "52.50", "datepaid": "2026-07-04", "source": "B-2"}]}},
            {"op": "arap_post", "save_as": "a3", "args": {
                "vc": "customer", "vc_id": 900, "transdate": "2026-07-05", "arap_accno": "1200",
                "taxincluded": 1, "lines": [{"accno": "8520", "amount": "121.00"}, {"accno": "8530", "amount": "10.90"}]}},
            {"op": "arap_post", "save_as": "a4", "args": {
                "vc": "customer", "vc_id": 900, "transdate": "2026-07-06", "arap_accno": "1200",
                "type": "credit_note", "lines": [{"accno": "8520", "amount": "40.00"}]}},
            {"op": "arap_post", "save_as": "a5", "args": {
                "vc": "customer", "vc_id": 900, "transdate": "2026-05-20", "arap_accno": "1200",
                "currency": "USD", "lines": [{"accno": "8540", "amount": "1000.00"}],
                "payments": [{"accno": "1100", "amount": "400.00", "datepaid": "2026-06-30", "source": "USD-1"},
                             {"accno": "1100", "amount": "100.00", "datepaid": "2026-05-20", "source": "USD-0"}]}},
            {"op": "arap_post", "save_as": "a6", "args": {
                "vc": "customer", "vc_id": 902, "transdate": "2026-07-10", "arap_accno": "1200",
                "cashdiscount": 2, "discountterms": 14, "department_id": 600,
                "lines": [{"accno": "8520", "amount": "500.00"}],
                "payments": [{"accno": "1100", "amount": "590.00", "datepaid": "2026-07-15"}],
                "discount": {"accno": "8520", "amount": "5.00", "datepaid": "2026-07-15"}}},
            {"op": "arap_post", "save_as": "p1", "args": {
                "vc": "vendor", "vc_id": 905, "transdate": "2026-07-03", "arap_accno": "1600",
                "lines": [{"accno": "8010", "amount": "800.00", "description": "meel"},
                          {"accno": "4220", "amount": "19.99"}],
                "payments": [{"accno": "1100", "amount": "300.00", "datepaid": "2026-07-20", "source": "UIT-1"}]}},
            {"op": "arap_post", "save_as": "p2", "args": {
                "vc": "vendor", "vc_id": 905, "transdate": "2026-07-04", "arap_accno": "1600", "pending": 1,
                "type": "debit_note", "lines": [{"accno": "8010", "amount": "50.00"}]}},
            {"op": "arap_get", "args": {"vc": "customer", "id": "$a1"}},
            {"op": "arap_get", "args": {"vc": "customer", "id": "$a2"}},
            {"op": "arap_get", "args": {"vc": "customer", "id": "$a3"}},
            {"op": "arap_get", "args": {"vc": "customer", "id": "$a4"}},
            {"op": "arap_get", "args": {"vc": "customer", "id": "$a5"}},
            {"op": "arap_get", "args": {"vc": "customer", "id": "$a6"}},
            {"op": "arap_get", "args": {"vc": "vendor", "id": "$p1"}},
            {"op": "arap_repost", "args": {"vc": "customer", "id": "$a1"}},
            {"op": "arap_repost", "args": {"vc": "customer", "id": "$a2"}},
            {"op": "arap_repost", "args": {"vc": "customer", "id": "$a3"}},
            {"op": "arap_repost", "args": {"vc": "customer", "id": "$a4"}},
            {"op": "arap_repost", "args": {"vc": "customer", "id": "$a5"}},
            {"op": "arap_repost", "args": {"vc": "customer", "id": "$a6"}},
            {"op": "arap_repost", "args": {"vc": "vendor", "id": "$p1"}},
            {"op": "arap_repost", "args": {"vc": "vendor", "id": "$p2"}},
            {"op": "arap_delete", "vc": "customer", "id": "$a4"},
            {"op": "aging", "args": {"arap": "ar", "todate": "2026-07-31"}},
            {"op": "tax_report", "args": {"db": "ar", "accounts": ["1810", "1811"], "fromdate": "2026-07-01",
                                          "todate": "2026-07-31", "summary": 1}},
        ],
    },
    {
        "name": "verkoopboekingen foutgevallen",
        "config": ADMIN,
        "ops": [
            {"op": "arap_post", "args": {"vc": "customer", "vc_id": 900, "transdate": "2025-06-30",
                                         "arap_accno": "1200", "lines": [{"accno": "8520", "amount": "1"}]}},
            {"op": "arap_post", "args": {"vc": "customer", "vc_id": 900, "transdate": "2026-07-01", "arap_accno": "1200",
                                         "lines": [{"accno": "8520", "amount": "10"}],
                                         "payments": [{"accno": "1100", "amount": "5", "datepaid": "2025-12-01"}]}},
            {"op": "arap_post", "args": {"vc": "customer", "vc_id": 900, "transdate": "2026-07-01", "arap_accno": "1200",
                                         "currency": "USD", "lines": [{"accno": "8520", "amount": "10"}]}},
            {"op": "arap_post", "args": {"vc": "customer", "vc_id": 902, "transdate": "2026-07-01", "arap_accno": "1200",
                                         "cashdiscount": 2, "discountterms": 5, "lines": [{"accno": "8520", "amount": "100"}],
                                         "discount": {"accno": "8520", "amount": "2.00", "datepaid": "2026-07-20"}}},
        ],
    },

    {
        "name": "verkoop-/inkoopscherm (aa.pl) als browser",
        "config": dict(ADMIN, numberformat="1.000,00"),
        "setup": PAY_SETUP + DISCOUNT_SETUP,
        "ops": [
            {"op": "aa_ui", "save_as": "s1", "args": {
                "vc": "customer", "vc_id": 900, "transdate": "2026-07-01", "invnumber": "W-1",
                "description": "Advies juli", "notes": "n1", "ordnumber": "O-1", "ponumber": "PO-1",
                "lines": [{"accno": "8520", "amount": "100.00", "description": "uren"},
                          {"accno": "8530", "amount": "33.33", "project_id": 700}],
                "payments": [{"accno": "1100", "amount": "50.00", "datepaid": "2026-07-03", "source": "B-1",
                              "memo": "deel"}]}},
            {"op": "aa_ui", "save_as": "s2", "args": {
                "vc": "customer", "vc_id": 902, "transdate": "2026-07-10", "invnumber": "W-2",
                "cashdiscount": "2", "discountterms": 14,
                "lines": [{"accno": "8520", "amount": "500.00"}],
                "payments": [{"accno": "1100", "amount": "590.00", "datepaid": "2026-07-15"}],
                "discount": {"accno": "8590", "amount": "5.00", "datepaid": "2026-07-15"}}},
            {"op": "aa_ui", "save_as": "s3", "args": {
                "vc": "customer", "vc_id": 900, "type": "credit_note", "transdate": "2026-07-11", "invnumber": "C-1",
                "lines": [{"accno": "8520", "amount": "40.00"}]}},
            {"op": "aa_ui", "save_as": "s4", "args": {
                "vc": "vendor", "vc_id": 905, "transdate": "2026-07-03", "invnumber": "INK-1", "dcn": "1234 5678",
                "lines": [{"accno": "8010", "amount": "800.00", "description": "meel"}],
                "payments": [{"accno": "1100", "amount": "300.00", "datepaid": "2026-07-20", "source": "UIT-1"}]}},
            {"op": "aa_ui", "save_as": "s5", "args": {
                "vc": "vendor", "vc_id": 905, "type": "debit_note", "transdate": "2026-07-04", "invnumber": "INK-C1",
                "lines": [{"accno": "8010", "amount": "50.00"}]}},
            {"op": "aa_ui", "save_as": "s6", "args": {
                "vc": "customer", "vc_id": 903, "transdate": "2026-05-20", "invnumber": "USD-1", "currency": "USD",
                "lines": [{"accno": "8540", "amount": "1000.00"}],
                "payments": [{"accno": "1100", "amount": "100.00", "datepaid": "2026-05-20", "source": "USD-0"},
                             {"accno": "1100", "amount": "400.00", "datepaid": "2026-06-30", "source": "USD-1",
                              "exchangerate": "0.95"}]}},
            {"op": "aa_ui", "save_as": "s7", "args": {
                "vc": "customer", "vc_id": 900, "transdate": "2026-07-05", "invnumber": "W-7", "taxincluded": 1,
                "lines": [{"accno": "8520", "amount": "121.00"}, {"accno": "8530", "amount": "10.90"}]}},
            {"op": "aa_ui", "save_as": "s8", "args": {
                "vc": "customer", "vc_id": 902, "transdate": "2026-07-06", "invnumber": "W-8",
                "lines": [{"accno": "8520", "amount": "100.00"}], "taxes": {"1810": "20.00"}}},
            # wijzigen (bevestigen -> doorgaan), opslaan als nieuw, relatie wisselen, verwijderen, nieuw nummer
            {"op": "aa_ui", "args": {"vc": "customer", "id": "$s1", "description": "Advies juli (gewijzigd)",
                                     "lines": [{"accno": "8520", "amount": "110.00", "description": "uren"},
                                               {"accno": "8530", "amount": "33.33", "project_id": 700}]}},
            {"op": "aa_ui", "save_as": "s9", "args": {"vc": "customer", "id": "$s1", "mode": "post_as_new",
                                                      "invnumber": "W-9", "transdate": "2026-07-20"}},
            {"op": "aa_ui", "args": {"vc": "customer", "id": "$s7", "vc_id": 902}},
            {"op": "aa_ui", "args": {"vc": "vendor", "id": "$s4", "notes": "gecontroleerd"}},
            {"op": "aa_ui", "args": {"vc": "customer", "id": "$s3", "mode": "delete"}},
            {"op": "aa_ui", "args": {"vc": "customer", "vc_id": 900, "transdate": "2026-07-21", "mode": "new_number",
                                     "lines": [{"accno": "8520", "amount": "1.00"}]}},
            {"op": "aging", "args": {"arap": "ar", "todate": "2026-07-31"}},
        ],
    },
    {
        "name": "aa.pl-scherm: vervaldatum en geblokkeerd bij wijzigen",
        # geblokkeerd (onhold) blijft in beide staan: aa.pl heeft er een vinkje voor
        "config": ADMIN,
        "setup": PAY_SETUP + DISCOUNT_SETUP,
        "expect_diff": {
            "tabel ar": "README 45: Perl rekent bij openen en opslaan de vervaldatum opnieuw uit "
                        "(oldterms niet geladen); Python behoudt de opgeslagen vervaldatum",
        },
        "ops": [
            {"op": "aa_ui", "save_as": "d1", "args": {
                "vc": "customer", "vc_id": 902, "transdate": "2026-07-01", "invnumber": "VD-1",
                "lines": [{"accno": "8520", "amount": "100.00"}]}},
            {"op": "aa_ui", "save_as": "d2", "args": {
                "vc": "vendor", "vc_id": 905, "transdate": "2026-07-01", "invnumber": "VD-2",
                "lines": [{"accno": "8010", "amount": "100.00"}]}},
            {"op": "sql", "sql": "UPDATE ar SET duedate = '2026-09-01' WHERE invnumber = 'VD-1'"},
            {"op": "sql", "sql": "UPDATE ap SET onhold = TRUE WHERE invnumber = 'VD-2'"},
            {"op": "aa_ui", "args": {"vc": "customer", "id": "$d1", "description": "na wijziging"}},
            {"op": "aa_ui", "args": {"vc": "vendor", "id": "$d2", "description": "na wijziging"}},
        ],
    },

    # ------------------------------------------------- fase 1b: betalingen
    {
        "name": "betalingen: ontvangsten op meerdere facturen",
        "config": ADMIN,
        "setup": PAY_SETUP,
        "ops": [
            {"op": "cp_post", "args": {"vc": "customer", "vc_id": 900, "datepaid": "2026-07-01",
                                       "payment_accno": "1100", "source": "R-100", "memo": "juli",
                                       "rows": [{"invnumber": "F-3001"}, {"invnumber": "F-3006", "paid": "100.00"}]}},
            {"op": "cp_post", "args": {"vc": "customer", "vc_id": 900, "datepaid": "2026-07-02",
                                       "payment_accno": "1100", "rows": [{"invnumber": "F-3006"}]}},
            {"op": "cp_post", "args": {"vc": "customer", "vc_id": 902, "datepaid": "2026-07-03",
                                       "payment_accno": "1100", "source": "",
                                       "rows": [{"invnumber": "F-3007"}, {"invnumber": "F-3004", "paid": "50.00"}]}},
            {"op": "aging", "args": {"arap": "ar", "todate": "2026-07-31"}},
            {"op": "payments", "args": {"db": "ar", "accounts": ["1100"], "fromdate": "2026-07-01",
                                        "todate": "2026-07-31"}},
        ],
    },
    {
        "name": "betalingen: leverancier met vooruitbetaling",
        "config": ADMIN,
        "setup": PAY_SETUP,
        "ops": [
            {"op": "cp_post", "args": {"vc": "vendor", "vc_id": 905, "datepaid": "2026-07-01",
                                       "payment_accno": "1100", "source": "1001", "amount": "1565.00", "arap_accno": "1600",
                                       "rows": [{"invnumber": "I-4001"}, {"invnumber": "I-4002"}]}},
            {"op": "cp_post", "args": {"vc": "vendor", "vc_id": 901, "datepaid": "2026-07-02",
                                       "payment_accno": "1100", "rows": [{"invnumber": "I-4003", "paid": "21.00"}]}},
            {"op": "aging", "args": {"arap": "ap", "todate": "2026-07-31"}},
        ],
    },
    {
        "name": "betalingen: vooruitbetaling zonder posten en zonder kenmerk",
        "config": ADMIN,
        "setup": PAY_SETUP,
        "expect_diff": {
            "tabel acc_trans": "cp.pl laat het kenmerk weg als er geen post is aangevinkt, ook een ingevuld "
                               "kenmerk; de webapp boekt het ingevulde of voorgestelde kenmerk (README)",
            "tabel defaults": "gevolg: het volgende kenmerk is opgehoogd",
        },
        "ops": [
            {"op": "sql", "sql": "INSERT INTO defaults (fldname, fldvalue) VALUES ('check_1100', '2001')"},
            {"op": "cp_post", "args": {"vc": "vendor", "vc_id": 901, "datepaid": "2026-07-02",
                                       "payment_accno": "1100", "amount": "75.00", "arap_accno": "1600", "rows": []}},
            {"op": "cp_post", "args": {"vc": "vendor", "vc_id": 901, "datepaid": "2026-07-03", "source": "X-7",
                                       "payment_accno": "1100", "amount": "25.00", "arap_accno": "1600", "rows": []}},
        ],
    },
    {
        "name": "betalingen: scherm meerdere relaties",
        "config": ADMIN,
        "setup": PAY_SETUP,
        "expect_diff": {
            "tabel ar": "Perl zet bij een deelbetaling ook datepaid/bank_id op de post die niets krijgt (README)",
            "tabel payment": "Perl schrijft een betaling van 0 op de post die niets krijgt (README)",
            "tabel acc_trans": "Perl boekt nulregels (bank en debiteuren) op de post die niets krijgt (README)",
            "tabel audittrail": "gevolg: Perl logt die nulbetaling",
        },
        "ops": [
            {"op": "sql", "sql": "INSERT INTO defaults (fldname, fldvalue) VALUES ('receipt_1100', 'M-1')"},
            # Bakkerij Cees deels (oudste eerst), Klant A helemaal
            {"op": "cp_payments", "args": {"vc": "customer", "datepaid": "2026-07-01", "payment_accno": "1100",
                                           "rows": [{"vc_id": 902, "paid": "100.00", "memo": "deel"},
                                                    {"vc_id": 900}]}},
            {"op": "cp_payments", "args": {"vc": "vendor", "datepaid": "2026-07-02", "payment_accno": "1100",
                                           "rows": [{"vc_id": 905}]}},
            {"op": "aging", "args": {"arap": "ar", "todate": "2026-07-31"}},
            {"op": "aging", "args": {"arap": "ap", "todate": "2026-07-31"}},
        ],
    },
    {
        "name": "betalingen: scherm meerdere relaties met contante korting",
        "config": ADMIN,
        "setup": PAY_SETUP + DISCOUNT_SETUP,
        "expect_diff": {
            "op 2": "Perl stelt het openstaande bedrag zonder korting voor; de webapp het bedrag min de "
                    "korting (P5)",
            "op 3": "Perl geeft de korting ook bij een deelbetaling, trekt hem af van het betaalde bedrag "
                    "en boekt het verschil als vooruitbetaling bij de eerste relatie in de lijst (een andere "
                    "klant), zonder debiteurenrekening. De webapp: korting alleen bij volledige betaling (README)",
            "*": "gevolg van op 3",
        },
        "ops": [
            {"op": "arap_post", "args": {
                "vc": "customer", "vc_id": 904, "transdate": "2026-07-01", "arap_accno": "1200", "invnumber": "KM-1",
                "cashdiscount": 2, "discountterms": 10, "lines": [{"accno": "8520", "amount": "500.00"}]}},
            {"op": "cp_payments", "args": {"vc": "customer", "datepaid": "2026-07-05", "payment_accno": "1100",
                                           "discount_accno": "8590", "nopost": 1, "rows": [{"vc_id": 904}]}},
            {"op": "cp_payments", "args": {"vc": "customer", "datepaid": "2026-07-05", "payment_accno": "1100",
                                           "discount_accno": "8590", "rows": [{"vc_id": 904, "paid": "605.00"}]}},
            {"op": "aging", "args": {"arap": "ar", "todate": "2026-07-31"}},
        ],
    },
    {
        "name": "betalingen: contante korting zonder en met btw-correctie",
        "config": ADMIN,
        "setup": PAY_SETUP + DISCOUNT_SETUP,
        "ops": [
            {"op": "arap_post", "save_as": "k1", "args": {
                "vc": "customer", "vc_id": 902, "transdate": "2026-07-01", "arap_accno": "1200", "invnumber": "KD-1",
                "cashdiscount": 2, "discountterms": 10,
                "lines": [{"accno": "8520", "amount": "1000.00"}, {"accno": "8530", "amount": "333.33"}]}},
            {"op": "arap_post", "save_as": "k2", "args": {
                "vc": "customer", "vc_id": 902, "transdate": "2026-07-01", "arap_accno": "1200", "invnumber": "KD-2",
                "cashdiscount": 3, "discountterms": 10, "lines": [{"accno": "8520", "amount": "250.00"}]}},
            {"op": "arap_post", "save_as": "k3", "args": {
                "vc": "vendor", "vc_id": 905, "transdate": "2026-07-01", "arap_accno": "1600", "invnumber": "KI-3",
                "cashdiscount": 2, "discountterms": 10, "lines": [{"accno": "8010", "amount": "400.00"}]}},
            {"op": "arap_post", "save_as": "k4", "args": {
                "vc": "customer", "vc_id": 902, "transdate": "2026-07-01", "arap_accno": "1200", "invnumber": "KD-4",
                "cashdiscount": 2, "discountterms": 10, "taxincluded": 1,
                "lines": [{"accno": "8520", "amount": "121.00"}]}},
            # zonder cdt: korting zonder btw-correctie; KD-2 buiten de kortingstermijn
            {"op": "cp_post", "args": {"vc": "customer", "vc_id": 902, "datepaid": "2026-07-08",
                                       "payment_accno": "1100", "discount_accno": "8590", "source": "K-1",
                                       "rows": [{"invnumber": "KD-1"}]}},
            {"op": "cp_post", "args": {"vc": "customer", "vc_id": 902, "datepaid": "2026-07-20",
                                       "payment_accno": "1100", "discount_accno": "8590", "source": "K-2",
                                       "rows": [{"invnumber": "KD-2"}]}},
            # met cdt
            {"op": "sql", "sql": "INSERT INTO defaults (fldname, fldvalue) VALUES ('cdt', '1')"},
            {"op": "cp_post", "args": {"vc": "vendor", "vc_id": 905, "datepaid": "2026-07-05",
                                       "payment_accno": "1100", "discount_accno": "8595", "source": "K-3",
                                       "rows": [{"invnumber": "KI-3"}]}},
            {"op": "cp_post", "args": {"vc": "customer", "vc_id": 902, "datepaid": "2026-07-05",
                                       "payment_accno": "1100", "discount_accno": "8590", "source": "K-4",
                                       "rows": [{"invnumber": "KD-4"}]}},
            {"op": "arap_post", "save_as": "k5", "args": {
                "vc": "customer", "vc_id": 900, "transdate": "2026-07-01", "arap_accno": "1200", "invnumber": "KD-5",
                "cashdiscount": 2, "discountterms": 10,
                "lines": [{"accno": "8520", "amount": "200.00"}, {"accno": "8530", "amount": "100.00"}]}},
            {"op": "cp_post", "args": {"vc": "customer", "vc_id": 900, "datepaid": "2026-07-06",
                                       "payment_accno": "1100", "discount_accno": "8590", "source": "K-5",
                                       "rows": [{"invnumber": "KD-5", "paid": "345.00", "discount": "6.00"}]}},
            {"op": "aging", "args": {"arap": "ar", "todate": "2026-07-31"}},
        ],
    },
    {
        "name": "betalingen: vreemde valuta",
        "config": ADMIN,
        "setup": PAY_SETUP,
        "expect_diff": {
            "tabel semaphore": "Perl laat na een mislukte betaling de locks op de getoonde posten staan; "
                               "de webapp lockt pas bij het boeken (P8, README)",
        },
        "ops": [
            {"op": "arap_post", "save_as": "u1", "args": {
                "vc": "customer", "vc_id": 903, "transdate": "2026-05-20", "arap_accno": "1200", "invnumber": "US-1",
                "currency": "USD", "lines": [{"accno": "8540", "amount": "1000.00"}]}},
            {"op": "arap_post", "save_as": "u2", "args": {
                "vc": "customer", "vc_id": 903, "transdate": "2026-05-20", "arap_accno": "1200", "invnumber": "US-2",
                "currency": "USD", "lines": [{"accno": "8540", "amount": "333.33"}]}},
            {"op": "cp_post", "args": {"vc": "customer", "vc_id": 903, "datepaid": "2026-06-30", "currency": "USD",
                                       "payment_accno": "1100", "source": "USD-9", "amount": "1600.00", "arap_accno": "1200",
                                       "rows": [{"invnumber": "US-1"}, {"invnumber": "US-2", "paid": "300.00"}]}},
            {"op": "cp_post", "args": {"vc": "customer", "vc_id": 903, "datepaid": "2026-07-15", "currency": "USD",
                                       "exchangerate": "1.3333", "payment_accno": "1100", "source": "USD-10",
                                       "rows": [{"invnumber": "US-2"}]}},
            {"op": "cp_post", "args": {"vc": "customer", "vc_id": 903, "datepaid": "2026-07-16", "currency": "USD",
                                       "payment_accno": "1100", "source": "USD-11",
                                       "rows": [{"invnumber": "F-3003", "paid": "10.00"}]}},
            {"op": "aging", "args": {"arap": "ar", "todate": "2026-07-31"}},
        ],
    },
    {
        "name": "betalingen foutgevallen",
        "config": ADMIN,
        "setup": PAY_SETUP,
        "ops": [
            {"op": "cp_post", "args": {"vc": "customer", "vc_id": 900, "datepaid": "2025-12-31",
                                       "payment_accno": "1100", "rows": [{"invnumber": "F-3001"}]}},
            {"op": "cp_post", "args": {"vc": "customer", "vc_id": 903, "datepaid": "2026-07-01", "currency": "USD",
                                       "payment_accno": "1100", "rows": [{"invnumber": "F-3003"}]}},
            {"op": "cp_post", "args": {"vc": "customer", "vc_id": 900, "datepaid": "2026-07-01",
                                       "payment_accno": "1100", "rows": [{"invnumber": "F-3009"}]}},
        ],
    },
    {
        "name": "betalingen: bedrag lager dan toegewezen",
        "config": ADMIN,
        "setup": PAY_SETUP,
        "expect_diff": {
            "op 1": "Python weigert; Perl boekt een negatieve vooruitbetaling (README)",
            "*": "gevolg van de geweigerde betaling",
        },
        "ops": [
            {"op": "cp_post", "args": {"vc": "customer", "vc_id": 900, "datepaid": "2026-07-01",
                                       "payment_accno": "1100", "source": "R-1", "amount": "100.00",
                                       "rows": [{"invnumber": "F-3001"}]}},
        ],
    },
    {
        "name": "betalingen: korting met btw-correctie bij tariefwijziging",
        "config": ADMIN,
        "setup": PAY_SETUP + DISCOUNT_SETUP + ["INSERT INTO defaults (fldname, fldvalue) VALUES ('cdt', '1')"],
        "expect_diff": {
            "tabel ar": "Perl telt voor de btw-correctie 19% + 21% op (alle tarieven die op of na de "
                  "factuurdatum aflopen); factuurbedrag daalt te veel (README)",
            "op 3": "gevolg: ander openstaand bedrag in de ouderdomsanalyse",
        },
        "ops": [
            {"op": "arap_post", "args": {
                "vc": "customer", "vc_id": 902, "transdate": "2026-06-25", "arap_accno": "1200", "invnumber": "KD-6",
                "cashdiscount": 2, "discountterms": 10, "lines": [{"accno": "8520", "amount": "1000.00"}]}},
            {"op": "cp_post", "args": {"vc": "customer", "vc_id": 902, "datepaid": "2026-07-01",
                                       "payment_accno": "1100", "discount_accno": "8590", "source": "K-6",
                                       "rows": [{"invnumber": "KD-6"}]}},
            {"op": "aging", "args": {"arap": "ar", "todate": "2026-07-31"}},
        ],
    },
    {
        "name": "betalingen: vooruitbetaling zonder gekozen crediteurenrekening",
        "config": ADMIN,
        "setup": PAY_SETUP,
        "expect_diff": {
            "tabel acc_trans": "Perl boekt de crediteurenregel met chart_id NULL (relatie zonder standaard "
                         "crediteurenrekening); Python neemt de eerste AP-rekening (README)",
        },
        "ops": [
            {"op": "cp_post", "args": {"vc": "vendor", "vc_id": 901, "datepaid": "2026-07-02",
                                       "payment_accno": "1100", "amount": "75.00", "rows": []}},
        ],
    },
    {
        "name": "betalingen: storneren en opnieuw uitgeven",
        "config": ADMIN,
        "setup": PAY_SETUP,
        "expect_diff": {
            "tabel ar": "storno: Perl verhoogt bij klanten het betaalde bedrag; heruitgave: Python werkt "
                        "datepaid bij op dezelfde manier, maar paid/amount verschillen door de storno (README 33)",
            "tabel ap": "storno: Perl laat datepaid staan; Python zet de laatste overgebleven betaaldatum",
            "tabel acc_trans": "storno: Perl draait alleen de bankregel terug (boeking uit balans), Python de "
                               "hele betaling; heruitgave: Perl verplaatst alleen de bankregel (README 33-34)",
            "tabel payment": "Python legt bij de stornoregel de koers van de betaling vast",
            "tabel audittrail": "Python schrijft 'voided'/'reissued' in de audittrail",
            "op 6": "ander openstaand bedrag na de storno",
        },
        "ops": [
            {"op": "cp_post", "args": {"vc": "customer", "vc_id": 900, "datepaid": "2026-07-01",
                                       "payment_accno": "1100", "source": "R-1",
                                       "rows": [{"invnumber": "F-3001"}, {"invnumber": "F-3006", "paid": "50.00"}]}},
            {"op": "cp_post", "args": {"vc": "vendor", "vc_id": 905, "datepaid": "2026-07-01",
                                       "payment_accno": "1100", "source": "5001", "rows": [{"invnumber": "I-4002"}]}},
            {"op": "cp_void", "args": {"vc": "customer", "accno": "1100", "source": "R-1"}},
            {"op": "cp_void", "args": {"vc": "vendor", "accno": "1100", "source": "5001"}},
            {"op": "cp_post", "args": {"vc": "vendor", "vc_id": 905, "datepaid": "2026-07-02",
                                       "payment_accno": "1100", "source": "5002", "rows": [{"invnumber": "I-4001"}]}},
            {"op": "aging", "args": {"arap": "ar", "todate": "2026-07-31"}},
            {"op": "cp_reissue", "args": {"vc": "vendor", "accno": "1100", "source": "5002", "new_source": "5003"}},
        ],
    },
    {
        "name": "overzicht verkoop- en inkoopboekingen",
        "config": ADMIN,
        "setup": PAY_SETUP,
        "expect_diff": {
            "op 22": "Perl: zoeken op memo in de samenvatting geeft altijd een lege lijst ('a.id = 0'); "
                     "Python vindt de boeking (README 36)",
            "op 24": "Perl telt in de detailweergave factuurbedrag/betaald per journaalregel op; "
                     "Python één keer per boeking (README 36)",
        },
        "ops": [
            {"op": "arap_list", "args": {"vc": "customer", "summary": 1}},
            {"op": "arap_list", "args": {"vc": "vendor", "summary": 1}},
            {"op": "arap_list", "args": {"vc": "customer", "summary": 1, "open": 1}},
            {"op": "arap_list", "args": {"vc": "customer", "summary": 1, "closed": 1}},
            {"op": "arap_list", "args": {"vc": "customer", "outstanding": 1, "transdateto": "2026-05-31"}},
            {"op": "arap_list", "args": {"vc": "customer", "outstanding": 1}},
            {"op": "arap_list", "args": {"vc": "customer", "summary": 1, "name": "bakkerij"}},
            {"op": "arap_list", "args": {"vc": "customer", "summary": 1, "vcnumber": "k00"}},
            {"op": "arap_list", "args": {"vc": "customer", "summary": 1, "vc_id": 900}},
            {"op": "arap_list", "args": {"vc": "customer", "summary": 1, "transdatefrom": "2026-04-01",
                                         "transdateto": "2026-06-15"}},
            {"op": "arap_list", "args": {"vc": "customer", "summary": 1, "year": 2026, "month": "04", "interval": 3}},
            {"op": "arap_list", "args": {"vc": "customer", "summary": 1, "invnumber": "f-300"}},
            {"op": "arap_list", "args": {"vc": "customer", "summary": 1, "description": "koek"}},
            {"op": "arap_list", "args": {"vc": "customer", "summary": 1, "department": "Ver"}},
            {"op": "arap_list", "args": {"vc": "customer", "summary": 1, "paidlate": 1}},
            {"op": "arap_list", "args": {"vc": "customer", "summary": 1, "paidearly": 1}},
            {"op": "arap_list", "args": {"vc": "customer", "summary": 1, "arap_accno": "1200"}},
            {"op": "arap_list", "args": {"vc": "customer", "summary": 1, "l_curr": 1}},
            {"op": "arap_list", "args": {"vc": "customer", "summary": 1, "l_subtotal": 1, "sort": "name"}},
            {"op": "arap_list", "args": {"vc": "customer", "summary": 1, "sort": "amount", "direction": "DESC"}},
            {"op": "arap_list", "args": {"vc": "customer", "summary": 1, "l_memo": 1}},
            {"op": "arap_list", "args": {"vc": "customer", "summary": 1, "memo": "termijn"}},
            {"op": "arap_list", "args": {"vc": "customer", "source": "bank"}},
            {"op": "arap_list", "args": {"vc": "customer"}},
            {"op": "arap_list", "args": {"vc": "vendor", "outstanding": 1}},
        ],
    },
    {
        "name": "relaties zoeken en keuzelijst",
        "config": ADMIN,
        "setup": ["UPDATE customer SET enddate = '2026-05-31' WHERE id = 904",
                  "UPDATE customer SET startdate = '2026-07-01' WHERE id = 903"],
        "ops": [
            {"op": "names", "args": {"vc": "customer", "name": "klant"}},
            {"op": "names", "args": {"vc": "customer", "name": ""}},
            {"op": "names", "args": {"vc": "customer", "name": "", "transdate": "2026-06-15"}},
            {"op": "names", "args": {"vc": "customer", "name": "", "transdate": "2026-07-15"}},
            {"op": "names", "args": {"vc": "customer", "number": "k00"}},
            {"op": "names", "args": {"vc": "customer", "name": "\"Klant A\""}},
            {"op": "names", "args": {"vc": "vendor", "name": "mo"}},
            {"op": "names", "args": {"vc": "customer", "list": 1}},
            {"op": "names", "args": {"vc": "customer", "list": 1, "transdate": "2026-06-15", "include_id": 904}},
            {"op": "names", "args": {"vc": "customer", "list": 1, "open_invoices": 1}},
            {"op": "names", "args": {"vc": "vendor", "list": 1, "open_invoices": 1}},
            {"op": "sql", "sql": "INSERT INTO defaults (fldname, fldvalue) VALUES ('namesbynumber', '1')"},
            {"op": "names", "args": {"vc": "customer", "name": ""}},
        ],
    },
]

# ======================================================================= recurring
from scenarios_recurring import SCENARIOS as _RECURRING  # noqa: E402
SCENARIOS += _RECURRING

# ================================================================ rc (afletteren, fase 2) — begin
# rc.pl als browser (perl_driver rc_screen/rc_ui) tegen de webcode (routes_rc.process).
# 1100 in de testdata: 950 (-121), 3006 BANK-306 (-100), 3005 BANK-305 (-48.40), 4002 BANK-402 (+300),
# 3002 BANK-302 (-50), 3008 KASSA (-24.20), 5001 RENTE (-2.50). Audittrail uit: RC.pm schrijft er niets.
RC_SETUP = ["DELETE FROM defaults WHERE fldname = 'audittrail'"]
RC_FX = ["INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, source, fx_transaction) VALUES "
         "(4002, 10044, 1.5, '2026-05-15', 'BANK-402', true), (4002, 10195, -1.5, '2026-05-15', 'BANK-402', true)"]
NL = dict(ADMIN, numberformat="1.000,00")
P1 = {"accno": "1100", "fromdate": "2026-03-01", "todate": "2026-06-30"}


def _rc_gl(tid, day, amount, source, name, n=1):
    rows = [f"INSERT INTO gl (id, reference, description, transdate) VALUES ({tid}, 'R{tid}', '{name}', '{day}')"]
    for _ in range(n):
        rows.append("INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, source) VALUES "
                    f"({tid}, 10044, {-amount}, '{day}', '{source}'), ({tid}, 10090, {amount}, '{day}', '{source}')")
    return rows


SCENARIOS += [
    {
        "name": "afletteren: scherm en rondes (rc.pl als browser)",
        "config": ADMIN,
        "setup": RC_SETUP + RC_FX,
        "ops": [
            {"op": "rc_screen", "args": P1},
            {"op": "rc_screen", "args": dict(P1, summary=0)},
            {"op": "rc_screen", "args": dict(P1, fx=0)},
            {"op": "rc_screen", "args": dict(P1, summary=0, fx=0)},
            {"op": "rc_ui", "args": dict(P1, check=["BANK-306", "BANK-402"], statementbalance="auto")},
            {"op": "rc_screen", "args": {"accno": "1100", "fromdate": "2026-07-01", "todate": "2026-07-31"}},
            {"op": "rc_ui", "args": dict(P1, uncheck=["BANK-306"], check=["2026-06-29 KASSA"],
                                         statementbalance="auto", summary=0)},
            {"op": "rc_screen", "args": dict(P1, report=1)},
            {"op": "rc_ui", "args": {"accno": "1100", "fromdate": "2026-07-01", "todate": "2026-09-30",
                                     "select_all": 1, "statementbalance": "auto"}},
            {"op": "rc_screen", "args": {"accno": "1100", "report": 1}},
            {"op": "rc_ui", "args": dict(P1, uncheck=["BANK-302"], statementbalance="12.34")},
        ],
    },
    {
        "name": "afletteren: Klaar zonder Bijwerken",
        "config": ADMIN,
        "setup": RC_SETUP,
        "expect_diff": {
            "op 1": "Perl toont het scherm zonder de vinkjes (er is niet bijgewerkt); de webapp rekent altijd met "
                    "de vinkjes (L2)",
            "tabel acc_trans": "Perl slaat bij Klaar zonder Bijwerken niets op (datecleared = oldcleared); de "
                               "webapp slaat de vinkjes op (README 96)",
        },
        "ops": [{"op": "rc_ui", "args": dict(P1, check=["BANK-306"], statementbalance="100", skip_update=1)}],
    },
    {
        "name": "afletteren: verschil 0,40 met notatie 1.000,00",
        "config": NL,
        "setup": RC_SETUP,
        "expect_diff": {
            "op 1": "Perl controleert het verborgen veld difference ('0,40' * 1 = 0) en slaat op; de webapp "
                    "rekent het verschil en weigert (README 97)",
            "tabel acc_trans": "gevolg van op 1: Perl lettert af ondanks verschil",
        },
        "ops": [{"op": "rc_ui", "args": dict(P1, check=["BANK-305"], statementbalance="48")}],
    },
    {
        "name": "afletteren: alles selecteren overschrijft oude afletterdatum",
        "config": ADMIN,
        "setup": RC_SETUP + ["UPDATE acc_trans SET cleared = '2026-05-31' WHERE trans_id = 3006 AND chart_id = 10044"],
        "expect_diff": {
            "tabel acc_trans": "Perl select_all (rc.pl r505, ongezette $i) geeft ook de al afgeletterde regel "
                               "de nieuwe datum; de webapp houdt 2026-05-31 (README 98)",
        },
        "ops": [{"op": "rc_ui", "args": dict(P1, select_all=1, statementbalance="auto")}],
    },
    {
        "name": "afletteren: samenvoegen per kenmerk, id als deelstring (12 in 123)",
        "config": ADMIN,
        "setup": RC_SETUP + _rc_gl(123, "2026-06-10", 10, "BATCH", "samen") + _rc_gl(12, "2026-06-10", 15, "BATCH", "samen"),
        "expect_diff": {
            "tabel acc_trans": "Perl RC.pm r267 (id !~ /$ref->{id}/): boeking 12 komt niet in id_$i omdat '123' "
                               "er al staat en wordt niet afgeletterd; de webapp lettert beide af (README 99)",
        },
        "ops": [
            {"op": "rc_screen", "args": P1},
            {"op": "rc_ui", "args": dict(P1, check=["BATCH"], statementbalance="auto")},
        ],
    },
    {
        "name": "afletteren: identieke regels (UNION i.p.v. UNION ALL)",
        "config": ADMIN,
        "setup": RC_SETUP + _rc_gl(777, "2026-06-12", 10, "DUP", "dubbel", n=2),
        "expect_diff": {
            "op 1": "Perl's UNION voegt de twee identieke regels samen (10,00 i.p.v. 20,00, RC.pm r192/204); "
                    "de webapp toont beide (README 100)",
            "op 2": "zelfde oorzaak: Perl rekent met 10,00 (het afschriftsaldo wijkt daardoor af)",
        },
        "ops": [
            {"op": "rc_screen", "args": dict(P1, summary=0)},
            {"op": "rc_ui", "args": dict(P1, check=["DUP"], statementbalance="auto", summary=0)},
            {"op": "rc_screen", "args": {"accno": "1100", "fromdate": "2026-07-01", "todate": "2026-07-31"}},
        ],
    },
    {
        "name": "afletteren: zonder koersverschilrekening",
        "config": ADMIN,
        "setup": RC_SETUP + ["DELETE FROM defaults WHERE fldname LIKE 'fx%'"],
        "expect_diff": {"op 1": "Perl RC.pm r138: 'AND NOT ac.chart_id = ' zonder rekening → SQL-fout; de webapp "
                                "toont het scherm (README 101)"},
        "ops": [{"op": "rc_screen", "args": P1}, {"op": "rc_screen", "args": dict(P1, fx=0)}],
    },
    {
        "name": "afletteren: zonder datums",
        "config": ADMIN,
        "setup": RC_SETUP + ["UPDATE acc_trans SET cleared = '2026-04-30' WHERE trans_id = 3006 AND chart_id = 10044"],
        "expect_diff": {"op 1": "Perl: zonder tot-datum is het voorgestelde afschriftsaldo de som van álle regels "
                                "(ook niet afgeletterd, RC.pm r100-116); de webapp telt alleen afgeletterde "
                                "(README 102)"},
        "ops": [{"op": "rc_screen", "args": {"accno": "1100"}}, {"op": "rc_screen", "args": {"accno": "1100", "todate": "2026-06-30"}}],
    },
]
# ================================================================ rc (afletteren, fase 2) — einde
# ===== ct (relaties, fase 2): ct.pl als browser vs webformuliercode (routes_ct) ==============
# testdata zet adressen/contactpersonen met vaste id's neer: reeksen bijzetten (Perl en Python gelijk)
CT_SETUP = ["SELECT setval('addressid', (SELECT max(id) FROM address))",
            "SELECT setval('contactid', (SELECT max(id) FROM contact))",
            "UPDATE customer SET contact = 'Piet Klant' WHERE id = 900"]   # zoals echte data: contact = voornaam achternaam

SCENARIOS += [
    {
        "name": "relatiescherm (ct.pl) als browser",
        "config": ADMIN,
        "setup": CT_SETUP,
        "ops": [
            {"op": "ct_ui", "save_as": "c1", "args": {"vc": "customer", "fields": {
                "name": "Nieuwe Klant BV", "address1": "Laan 1", "zipcode": "2611AA", "city": "Delft", "country": "NL",
                "salutation": "Mevr.", "firstname": "Ans", "lastname": "Smit", "phone": "015-123", "mobile": "06-1",
                "email": "ans@example.test", "cc": "boekhouding@example.test", "taxes": ["1810", "1811"],
                "terms": "14", "discount": "2.5", "creditlimit": "1000", "threshold": "50", "arap_accno": "1200",
                "payment_accno": "1100", "taxnumber": "NL001B02", "notes": "nieuwe klant",
                "iban": "NL91ABNA0417164300", "bic": "ABNANL2A", "bankname": "ABN AMRO", "membernumber": "M7",
                "shiptoname": "Magazijn", "shiptoaddress1": "Haven 3", "shiptocity": "Rotterdam",
                "startdate": "2026-01-01"}}},
            {"op": "ct_ui", "args": {"vc": "customer", "id": "$c1", "fields": {
                "city": "Den Haag", "phone": "070-9", "email": "smit@example.test", "taxes": ["1810"], "terms": "30",
                "taxincluded": 1, "remittancevoucher": 1, "enddate": "2026-12-31", "bic": "",
                "shiptocity": "Schiedam", "occupation": "inkoper"}}},
            {"op": "ct_ui", "save_as": "v1", "args": {"vc": "vendor", "fields": {
                "name": "Drukkerij Jansen", "address1": "Drukweg 2", "city": "Gouda", "taxes": ["1800"],
                "terms": "30", "arap_accno": "1600", "bankname": "Rabobank", "iban": "NL91ABNA0417164300",
                "bankcity": "Utrecht", "bankaddress1": "Croeselaan 18", "gifi_accno": "4000"}}},
            {"op": "ct_ui", "args": {"vc": "vendor", "id": "$v1", "fields": {"number": "L777", "bankcity": "Zeist",
                                                                            "clearingnumber": "C9"}}},
            {"op": "ct_ui", "save_as": "p1", "args": {"vc": "customer", "fields": {
                "typeofcontact": "person", "firstname": "Piet", "lastname": "Pietersen", "gender": "M",
                "address1": "Kerkstraat 9", "city": "Gouda"}}},
            {"op": "ct_ui", "args": {"vc": "customer", "id": "$p1", "fields": {"lastname": "Pieters", "gender": "M"}}},
            {"op": "ct_ui", "save_as": "c2", "args": {"vc": "customer", "id": "$c1", "mode": "save_as_new",
                                                      "fields": {"number": "K100", "name": "Nieuwe Klant Filiaal"}}},
            {"op": "ct_ui", "args": {"vc": "customer", "id": 900, "fields": {"notes": "vaste klant", "fax": "030-2"}}},
            {"op": "ct_ui", "args": {"vc": "vendor", "id": "$v1", "mode": "delete"}},
        ],
    },
    {
        "name": "relatiescherm: telefoon/e-mail van de relatie overschreven door contactpersoon",
        "config": ADMIN,
        "setup": CT_SETUP,
        "expect_diff": {"tabel customer": "Perl toont en bewaart telefoon/e-mail van de contactpersoon en "
                                          "overschrijft zo die van de relatie; ook het veld contact wordt opnieuw "
                                          "opgebouwd. Python laat ongewijzigde velden staan (README 57)"},
        "ops": [{"op": "ct_ui", "args": {"vc": "customer", "id": 902, "fields": {"notes": "gewijzigd"}}}],
    },
    {
        "name": "relatiescherm: nieuw nummer hoogt teller pas op bij opslaan",
        "config": ADMIN,
        "setup": CT_SETUP,
        "expect_diff": {"tabel defaults": "Perl hoogt customernumber op bij alleen tonen van 'Nieuw nummer' "
                                          "(README 59)"},
        "ops": [{"op": "ct_ui", "args": {"vc": "customer", "id": 900, "mode": "new_number"}}],
    },
    {
        "name": "relatiescherm: betaalkenmerk/rvc van de bank blijven staan",
        "config": ADMIN,
        "setup": CT_SETUP + ["INSERT INTO bank (id, name, iban, dcn, rvc) VALUES "
                             "(905, 'BANK', 'NL91ABNA0417164300', '<%invnumber%>', 'R1')"],
        "expect_diff": {"tabel bank": "Perl gooit bij opslaan dcn en rvc van de bankrij weg (README 61)",
                        "tabel contact": "Perl maakt bij een relatie zonder contactpersoon een lege aan (README 57)",
                        "tabel vendor": "idem: Perl zet het veld contact op ' '"},
        "ops": [{"op": "ct_ui", "args": {"vc": "vendor", "id": 905, "fields": {"notes": "x"}}}],
    },
    {
        "name": "relatiescherm: opslaan als nieuw geeft geen dubbel nummer",
        "config": ADMIN,
        "setup": CT_SETUP,
        "expect_diff": {"tabel customer": "Perl bewaart de kopie met hetzelfde klantnummer; Python geeft het "
                                          "volgende nummer (README 58)",
                        "tabel defaults": "idem: Python hoogt customernumber op"},
        "ops": [{"op": "ct_ui", "args": {"vc": "customer", "id": 900, "mode": "save_as_new",
                                         "fields": {"name": "Klant A kopie"}}}],
    },
    {
        "name": "relatiescherm: verwijderen van een relatie met boekingen",
        "config": ADMIN,
        "setup": CT_SETUP,
        "expect_diff": {"op 1": "Perl verwijdert zonder controle op de server (alleen de knop ontbreekt); "
                                "Python weigert (README 56)",
                        "tabel customer": "idem", "tabel address": "idem", "tabel customertax": "idem",
                        "tabel contact": "idem", "tabel audittrail": "idem"},
        "ops": [{"op": "ct_ui", "args": {"vc": "customer", "id": 904, "mode": "delete"}}],
    },
]
# ===== einde ct ===========================================================================
# blok "am": beheer (fase 2), zie scenarios_am.py
from scenarios_am import SCENARIOS as _AM_SCENARIOS  # noqa: E402
from scenarios_ic import SCENARIOS as _IC_SCENARIOS  # noqa: E402

SCENARIOS += _AM_SCENARIOS
SCENARIOS += _IC_SCENARIOS

from scenarios_is import SCENARIOS as _IS_SCENARIOS  # noqa: E402  (facturen met artikelen, fase 3)
SCENARIOS += _IS_SCENARIOS

from scenarios_oe import SCENARIOS as _OE_SCENARIOS  # noqa: E402  (orders en offertes, fase 3)
SCENARIOS += _OE_SCENARIOS

from scenarios_rh import SCENARIOS as _RH_SCENARIOS  # noqa: E402  (herhalen facturen/orders, fase 3)
SCENARIOS += _RH_SCENARIOS

from scenarios_im import SCENARIOS as _IM_SCENARIOS  # noqa: E402  (import, fase 4 stap 7)
SCENARIOS += _IM_SCENARIOS

from scenarios_jc import SCENARIOS as _JC_SCENARIOS  # noqa: E402  (tijdkaarten, fase Uren)
SCENARIOS += _JC_SCENARIOS
