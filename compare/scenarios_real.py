"""Scenario's op een echte SQL-Ledger-administratie (na upgrade naar 3.2.4).

Gebruik met run_compare.py --scenarios real --template <db>. Bevat geen
bedrijfsgegevens: alleen rekeningnummers en jaren worden gebruikt.
"""

CFG = {"login": "admin", "numberformat": "1.000,00", "dateformat": "yyyy-mm-dd", "countrycode": ""}

YEARS = range(2011, 2026)

# rekeningen met boekingen (uit de rekeningschema-inventarisatie)
ACCOUNTS = """0150 0200 0201 0250 0251 0300 0301 0400 0401 0600 0601 0700 0701 0800 0900 0950 0975
1000 1001 1003 1005 1010 1021 1299 1301 1305 1309 1310 1330 1340 1350 1390 1391 1395 1396 1501
1505 1601 1602 1603 1604 1605 1610 1629 1630 1631 1632 1633 1645 1650 1651 1652 1670 1672 1674
1676 1690 1699 1710 1715 1716 1717 1720 1730 1731 1732 1740 1745 1751 1760 1780 1830 1831 1832
1835 1836 1837 1840 4001 4002 4003 4009 4010 4012 4015 4020 4025 4030 4035 4050 4051 4052 4350
4385 4395 4396 4490 4498 4499 4630 4631 4640 4660 4670 4675 4685 4690 4692 4695 4700 4705 4710
4720 4810 4820 4830 4850 4860 4870 4955 4956 4960 4970 4973 4980 5000 5001 5005 5010 5900 8100
8200 8220 8240 8250 8299 8300 8350 9050""".split()


def line(accno, debit="", credit="", **kw):
    return {"accno": accno, "debit": debit, "credit": credit, **kw}


def year_ops(y):
    return [
        {"op": "trial_balance", "args": {"fromdate": f"{y}-01-01", "todate": f"{y}-12-31"}},
        {"op": "trial_balance", "args": {"todate": f"{y}-12-31"}},
        {"op": "trial_balance", "args": {"fromdate": f"{y}-01-01", "todate": f"{y}-12-31",
                                         "accounttype": "gifi"}},
        {"op": "income_statement", "args": {"fromdate": f"{y}-01-01", "todate": f"{y}-12-31",
                                            "decimalplaces": 2, "includeperiod": "month"}},
        {"op": "income_statement", "args": {"fromdate": f"{y}-01-01", "todate": f"{y}-12-31",
                                            "decimalplaces": 2, "includeperiod": "quarter",
                                            "previousyear": 1}},
        {"op": "income_statement", "args": {"fromdate": f"{y}-01-01", "todate": f"{y}-12-31",
                                            "decimalplaces": 2, "method": "cash"}},
        {"op": "balance_sheet", "args": {"todate": f"{y}-12-31", "decimalplaces": 2,
                                         "includeperiod": "quarter", "previousyear": 1}},
        {"op": "balance_sheet", "args": {"todate": f"{y}-06-30", "decimalplaces": 2,
                                         "accounttype": "gifi"}},
        {"op": "list", "search": {"datefrom": f"{y}-03-01", "dateto": f"{y}-03-31"}},
        {"op": "list", "search": {"accno": "1000", "datefrom": f"{y}-11-01", "dateto": f"{y}-12-31"}},
    ]


SCENARIOS = [{"name": f"ADM A {y}", "config": CFG, "ops": year_ops(y)} for y in YEARS]

SCENARIOS += [
    {
        "name": "ADM A rekeningschema en mutaties vanaf 2024",
        "config": CFG,
        "ops": [{"op": "accounts"}] + [
            {"op": "account_tx", "args": {"accno": a, "fromdate": "2024-01-01"}} for a in ACCOUNTS
        ],
    },
    {
        "name": "ADM A overzicht met grootboekkaarten (split ledger)",
        "config": CFG,
        "ops": [
            {"op": "list", "search": {"splitledger": True, "datefrom": "2024-06-01", "dateto": "2024-06-30"}},
            {"op": "list", "search": {"category": "I", "datefrom": "2023-01-01", "dateto": "2023-12-31"}},
            {"op": "list", "search": {"accnofrom": "4000", "accnoto": "4999", "year": 2022, "month": "05"}},
            {"op": "list", "search": {"amountfrom": "1000", "datefrom": "2020-01-01", "dateto": "2020-12-31"}},
        ],
    },
    {
        "name": "ADM A boeken, wijzigen, verwijderen",
        "config": CFG,
        "ops": [
            {"op": "post", "save_as": "t1", "trans": {
                "transdate": "2025-09-15", "description": "Test kantoorkosten", "notes": "test",
                "lines": [line("4810", debit="123.45", source="TEST-1"), line("1831", debit="25.93"),
                          line("1000", credit="149.38", memo="bank")]}},
            {"op": "get", "id": "$t1"},
            {"op": "post", "save_as": "t1b", "trans": {
                "id": "$t1", "transdate": "2025-09-16", "description": "Test kantoorkosten (gewijzigd)",
                "lines": [line("4810", debit="100", cleared="2025-09-30"), line("1000", credit="100")]}},
            {"op": "get", "id": "$t1"},
            {"op": "list", "search": {"datefrom": "2025-09-01", "dateto": "2025-09-30"}},
            {"op": "account_tx", "args": {"accno": "4810", "fromdate": "2025-01-01"}},
            {"op": "trial_balance", "args": {"fromdate": "2025-01-01", "todate": "2025-12-31"}},
            {"op": "delete", "id": "$t1"},
            # closedto = '20173112' (geen geldige datum): Perl vergelijkt als getal
            {"op": "post", "trans": {"transdate": "2017-06-30",
                                     "lines": [line("4810", debit="1"), line("1000", credit="1")]}},
            {"op": "post", "save_as": "t2", "trans": {"transdate": "2018-01-02",
                                                      "lines": [line("4810", debit="1"), line("1000", credit="1")]}},
            {"op": "post", "trans": {"transdate": "2025-09-15",
                                     "lines": [line("4810", debit="10"), line("1000", credit="9.99")]}},
            {"op": "accounts"},
        ],
    },
]

TAX = ["1830", "1831", "1832", "1835", "1836", "1837"]
PAID = ["1000", "1003", "1299", "1340", "1390", "1395", "1650", "1652", "4499", "9020", "9021", "9050"]


def arap_ops(y):
    return [
        {"op": "tax_report", "args": {"db": "ar", "accounts": TAX, "fromdate": f"{y}-01-01",
                                      "todate": f"{y}-12-31", "summary": 1}},
        {"op": "tax_report", "args": {"db": "ap", "accounts": TAX, "fromdate": f"{y}-01-01",
                                      "todate": f"{y}-12-31", "summary": 1}},
        {"op": "tax_report", "args": {"db": "ar", "accounts": TAX, "fromdate": f"{y}-01-01",
                                      "todate": f"{y}-03-31", "summary": 0}},
        {"op": "tax_report", "args": {"db": "ar", "accounts": TAX, "fromdate": f"{y}-04-01",
                                      "todate": f"{y}-06-30", "summary": 1, "method": "cash"}},
        {"op": "tax_report", "args": {"db": "ar", "fromdate": f"{y}-01-01", "todate": f"{y}-12-31",
                                      "summary": 1, "nontaxable": 1}},
        {"op": "payments", "args": {"db": "ar", "accounts": PAID, "fromdate": f"{y}-01-01",
                                    "todate": f"{y}-12-31"}},
        {"op": "payments", "args": {"db": "ap", "accounts": PAID, "fromdate": f"{y}-01-01",
                                    "todate": f"{y}-12-31", "l_subtotal": 1, "sort": "transdate"}},
    ]


SCENARIOS += [{"name": f"ADM A btw en betalingen {y}", "config": CFG, "ops": arap_ops(y)}
              for y in range(2011, 2026)]

SCENARIOS += [
    {
        "name": "ADM A ouderdomsanalyse en aanmaningen",
        "config": CFG,
        "ops": [
            {"op": "aging", "args": {"arap": "ap", "todate": "2025-09-12"}},
            {"op": "aging", "args": {"arap": "ap", "todate": "2025-09-12", "overdue": 1, "summary": 1}},
            {"op": "aging", "args": {"arap": "ap", "todate": "2025-06-30", "buckets": ["c0", "c30", "c60", "c90"]}},
            {"op": "aging", "args": {"arap": "ar", "todate": "2025-09-12"}},
            {"op": "reminder", "args": {}},
        ],
    },
    {
        "name": "ADM A jaarafsluiting 2024 en opnieuw 2023",
        "config": CFG,
        "ops": [
            {"op": "yearend", "args": {"todate": "2024-12-31", "accno": "1505", "description": "Jaarafsluiting 2024"}},
            {"op": "income_statement", "args": {"fromdate": "2024-01-01", "todate": "2024-12-31", "decimalplaces": 2}},
            {"op": "balance_sheet", "args": {"todate": "2024-12-31", "decimalplaces": 2}},
            {"op": "yearend", "args": {"todate": "2023-12-31", "accno": "1505", "reference": "JA-2023"}},
            {"op": "trial_balance", "args": {"fromdate": "2023-01-01", "todate": "2025-12-31"}},
        ],
    },
]

# ---------------------------------------------------------------- fase 1a
# Alle bestaande verkoop-/inkoopboekingen zonder artikelen openen en ongewijzigd
# opnieuw boeken (Perl via het scherm, Python via de bibliotheek). Getalnotatie
# 1,000.00 omdat de Perl-driver de HTML-opmaak overslaat. Afsluitdatum uit in de
# testkopie zodat alle jaren meedoen.
CFG_UI = dict(CFG, numberformat="1,000.00")
SCENARIOS += [
    {"name": "ADM A opnieuw boeken verkoop (zonder artikelen)", "config": CFG_UI,
     "setup": ["UPDATE defaults SET fldvalue = '' WHERE fldname = 'closedto'"],
     "ops": [{"op": "arap_repost_all", "args": {"vc": "customer",
              "sql": "SELECT id FROM ar WHERE NOT invoice ORDER BY id"}}]},
    {"name": "ADM A opnieuw boeken inkoop (zonder artikelen)", "config": CFG_UI,
     "setup": ["UPDATE defaults SET fldvalue = '' WHERE fldname = 'closedto'"],
     "ops": [{"op": "arap_repost_all", "args": {"vc": "vendor",
              "sql": "SELECT id FROM ap WHERE NOT invoice ORDER BY id"}}]},
]

# alle openstaande posten afbetalen via het betaalscherm (fase 1b)
SCENARIOS += [
    {"name": "ADM A alle openstaande posten afbetalen", "config": CFG_UI,
     "ops": [
         {"op": "cp_pay_all", "args": {"vc": "vendor", "datepaid": "2025-09-30", "payment_accno": "1000",
                                        "discount_accno": "4490", "sql": "SELECT a.vendor_id, trim(a.curr), string_agg(a.id::text, ',' ORDER BY a.id) FROM ap a WHERE a.amount <> a.paid AND a.approved AND NOT a.onhold GROUP BY 1, 2 ORDER BY 1, 2"}},
         {"op": "cp_pay_all", "args": {"vc": "customer", "datepaid": "2025-09-30", "payment_accno": "1000",
                                        "discount_accno": "4490", "sql": "SELECT a.customer_id, trim(a.curr), string_agg(a.id::text, ',' ORDER BY a.id) FROM ar a WHERE a.amount <> a.paid AND a.approved AND NOT a.onhold GROUP BY 1, 2 ORDER BY 1, 2"}},
         {"op": "aging", "args": {"arap": "ap", "todate": "2025-09-30"}},
     ]},
]

# overzicht verkoop/inkoop (AA::transactions + aa.pl), per jaar
def list_ops(y):
    f, t = f"{y}-01-01", f"{y}-12-31"
    return [
        {"op": "arap_list", "args": {"vc": "customer", "summary": 1, "transdatefrom": f, "transdateto": t}},
        {"op": "arap_list", "args": {"vc": "vendor", "summary": 1, "transdatefrom": f, "transdateto": t,
                                     "l_subtotal": 1, "sort": "name"}},
        {"op": "arap_list", "args": {"vc": "customer", "outstanding": 1, "transdateto": t}},
        {"op": "arap_list", "args": {"vc": "vendor", "outstanding": 1, "transdateto": t}},
    ]


SCENARIOS += [{"name": f"ADM A overzicht verkoop/inkoop {y}", "config": CFG_UI, "ops": list_ops(y)}
              for y in (2012, 2015, 2018, 2021, 2024, 2025)]

# relaties zoeken (Form::get_name) en keuzelijsten (Form::all_vc)
SCENARIOS += [{"name": "ADM A relaties zoeken", "config": CFG_UI, "ops": [
    {"op": "names", "args": {"vc": vc, **a}} for vc in ("customer", "vendor") for a in (
        {"name": ""}, {"name": "a"}, {"name": "en"}, {"number": "1"}, {"name": "", "transdate": "2015-06-30"},
        {"list": 1}, {"list": 1, "transdate": "2015-06-30"}, {"list": 1, "open_invoices": 1})]}]

# ca.pl list_transactions-scherm (lopend saldo, subtotalen, totaal) op de betaalrekeningen en een paar andere
SCENARIOS += [{
    "name": "ADM A mutatiescherm (ca.pl)",
    "config": CFG,
    "ops": [{"op": "account_screen", "args": {"accno": a, "fromdate": "2024-01-01"}} for a in PAID]
    + [{"op": "account_screen", "args": {"accno": a, "fromdate": "2025-01-01", "todate": "2025-06-30",
                                         "sort": "transdate", "l_subtotal": 1}} for a in ("1000", "4810", "8100")]
    + [{"op": "account_screen", "args": {"accno": "1000", "year": 2023, "month": "03"}}],
}]

# gl.pl transactions-scherm (journaal: lopend saldo, per rekening, subtotalen)
SCENARIOS += [{
    "name": "ADM A journaalscherm (gl.pl)",
    "config": CFG,
    "ops": [{"op": "gl_screen", "search": {"datefrom": "2025-01-01", "dateto": "2025-03-31"}},
            {"op": "gl_screen", "search": {"datefrom": "2025-01-01", "dateto": "2025-06-30", "l_subtotal": 1}},
            {"op": "gl_screen", "search": {"datefrom": "2024-01-01", "dateto": "2024-12-31", "splitledger": True}},
            {"op": "gl_screen", "search": {"datefrom": "2025-01-01", "dateto": "2025-12-31", "category": "E",
                                           "splitledger": True}}]
    + [{"op": "gl_screen", "search": {"accno": a, "datefrom": "2024-07-01"}} for a in PAID[:6]]
    + [{"op": "gl_screen", "search": {"accno": "1000", "datefrom": "2025-01-01", "sort": "reference",
                                      "l_subtotal": 1}}],
}]

# ================================================================ rc (afletteren, fase 2)
# administratie A heeft niets afgeletterd: twee rondes op de bankrekening via rc.pl en via de webcode (op kopieën).
_RC = {"accno": "1000"}
SCENARIOS += [{
    "name": "ADM A afletteren (rc.pl)",
    "config": CFG,
    "setup": ["DELETE FROM defaults WHERE fldname = 'audittrail'"],
    "ops": [
        {"op": "rc_screen", "args": dict(_RC, fromdate="2011-11-01", todate="2011-12-31")},
        {"op": "rc_screen", "args": dict(_RC, fromdate="2011-11-01", todate="2011-12-31", summary=0)},
        {"op": "rc_ui", "args": dict(_RC, fromdate="2011-11-01", todate="2011-12-31", select_all=1,
                                     statementbalance="auto")},
        {"op": "rc_screen", "args": dict(_RC, fromdate="2012-01-01", todate="2012-01-31")},
        {"op": "rc_ui", "args": dict(_RC, fromdate="2012-01-01", todate="2012-01-31", summary=0, select_all=1,
                                     statementbalance="auto")},
        {"op": "rc_screen", "args": dict(_RC, fromdate="2011-11-01", todate="2012-01-31", report=1)},
    ],
}]
