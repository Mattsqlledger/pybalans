"""Scenario's artikelen (fase 3, blok "ic"): ic.pl als browser vs de formuliercode van de webapp.

Draaien: ``python compare/run_compare.py --sldir $SRC --scenarios ic --dbprefix ic``.
Ze zitten ook in de standaardset (scenarios.py voegt ze toe).
"""

ADMIN = {"login": "admin", "numberformat": "1,000.00", "dateformat": "yyyy-mm-dd", "countrycode": ""}

#: testdata: een opbrengstrekening voor diensten (IC_income) en een artikelgroep
IC_SETUP = ["UPDATE chart SET link = link || ':IC_income' WHERE accno = '8520'",
            "INSERT INTO partsgroup (id, partsgroup) VALUES (900, 'Gebak')",
            "INSERT INTO defaults (fldname, fldvalue) VALUES ('partnumber', 'A-100')"]


def ic(**args):
    return {"op": "ic_ui", "args": args}


def stock(part_id, qty):
    return {"op": "ic_stock", "args": {"id": part_id, "qty": qty}}


#: onderdelen voor de scenario's met samengestelde artikelen
PARTS_OPS = [
    {**ic(item="part", fields={"partnumber": "O-1", "description": "Deeg", "unit": "kg", "sellprice": "2.5",
                               "listprice": "3", "lastcost": "1.1", "weight": "1", "inventory_accno": "7001",
                               "income_accno": "8520", "expense_accno": "8020", "taxes": ["1810"],
                               "priceupdate": "2026-01-05"}), "save_as": "o1"},
    {**ic(item="part", fields={"partnumber": "O-2", "description": "Room", "unit": "l", "sellprice": "1.25",
                               "listprice": "1.5", "lastcost": "0.4", "weight": "0.25", "inventory_accno": "7001",
                               "income_accno": "8520", "expense_accno": "8020", "taxes": ["1810"],
                               "priceupdate": "2026-01-05"}), "save_as": "o2"},
    {**ic(item="service", fields={"partnumber": "D-1", "description": "Bezorgen", "sellprice": "10",
                                  "income_accno": "8520", "expense_accno": "8100", "taxes": ["1810"],
                                  "priceupdate": "2026-01-05"}), "save_as": "d1"},
]


SCENARIOS = [
    {
        "name": "artikelscherm (ic.pl) als browser",
        "config": ADMIN,
        "setup": IC_SETUP,
        "ops": [
            {**ic(item="part", fields={
                "partnumber": "T-1", "description": "Appeltaart", "unit": "st", "sellprice": "12.5",
                "listprice": "14", "lastcost": "6.125", "priceupdate": "2026-01-05", "inventory_accno": "7001",
                "income_accno": "8520", "expense_accno": "8020", "taxes": ["1810"], "rop": "4", "bin": "A3",
                "weight": "1.25", "barcode": "8712345678901", "notes": "vers", "partsgroup_id": 900}),
             "save_as": "p1"},
            ic(id="$p1", fields={"sellprice": "13", "taxes": ["1810", "1811"], "notes": "met room",
                                 "obsolete": 1}),
            {**ic(item="service", fields={"partnumber": "D-1", "description": "Advies", "sellprice": "95",
                                          "income_accno": "8520", "expense_accno": "8100", "taxes": ["1810"],
                                          "priceupdate": "2026-02-01"}), "save_as": "s1"},
            {**ic(item="part", fields={"description": "Zonder nummer", "inventory_accno": "7002",
                                       "income_accno": "8530", "expense_accno": "8030", "taxes": [],
                                       "priceupdate": "2026-03-01"}), "save_as": "p2"},
            {**ic(id="$p1", mode="save_as_new", fields={"partnumber": "T-2", "description": "Appeltaart groot"}),
             "save_as": "p3"},
            ic(id="$s1", mode="delete"),
        ],
    },
    {
        "name": "artikelscherm: ongewijzigd opslaan schrijft niets",
        "config": ADMIN,
        "setup": IC_SETUP,
        "expect_diff": {"tabel parts": "Perl schrijft alle kolommen opnieuw (leeg wordt 0) en een audittrail-regel; "
                                       "Python schrijft bij ongewijzigd opslaan niets (README, fase 3 AT2)",
                        "tabel audittrail": "zie tabel parts"},
        "ops": [ic(id=1000)],          # testartikel: dienst zonder kostenrekening
    },
    {
        "name": "samengesteld artikel: stuklijst (ic.pl) als browser",
        "config": ADMIN,
        "setup": IC_SETUP,
        "ops": PARTS_OPS + [
            {**ic(item="assembly", fields={"partnumber": "P-1", "description": "Taartpakket", "unit": "doos",
                                           "income_accno": "8520", "taxes": ["1810"], "priceupdate": "2026-02-01",
                                           "rop": "2", "bin": "B1"},
                  components=[["O-1", "2", 0], ["O-2", "1.5", 1], ["D-1", "1", 0]]), "save_as": "a1"},
            {**ic(item="assembly", fields={"partnumber": "S-1", "description": "Bodem", "income_accno": "8520",
                                           "taxes": ["1810"], "priceupdate": "2026-02-01"},
                  components=[["O-1", "3", 0]]), "save_as": "a2"},
            {**ic(item="assembly", fields={"partnumber": "T-1", "description": "Taart compleet",
                                           "income_accno": "8520", "taxes": [], "priceupdate": "2026-02-01"},
                  components=[["S-1", "2", 0], ["O-2", "1", 0]]), "save_as": "a3"},
            ic(id="$a1", bom={"1": 1, "2": 0}, fields={"notes": "vinkjes"}),
        ],
    },
    {
        "name": "samengesteld artikel: assembleren en demonteren (Stock Assembly)",
        "config": ADMIN,
        "setup": IC_SETUP,
        "expect_diff": {"tabel audittrail": "Perl schrijft bij demonteren (negatief aantal) ook action 'stock'; "
                                            "Python 'unstock' (README, fase 3 SA5)"},
        "ops": PARTS_OPS + [
            {**ic(item="assembly", fields={"partnumber": "P-1", "description": "Taartpakket", "income_accno": "8520",
                                           "taxes": ["1810"], "priceupdate": "2026-02-01"},
                  components=[["O-1", "2", 0], ["O-2", "1.5", 0], ["D-1", "1", 0]]), "save_as": "a1"},
            {**ic(item="assembly", fields={"partnumber": "S-1", "description": "Bodem", "income_accno": "8520",
                                           "taxes": ["1810"], "priceupdate": "2026-02-01"},
                  components=[["O-1", "3", 0]]), "save_as": "a2"},
            {**ic(item="assembly", fields={"partnumber": "T-1", "description": "Taart compleet",
                                           "income_accno": "8520", "taxes": [], "priceupdate": "2026-02-01"},
                  components=[["S-1", "2", 0], ["O-2", "1", 0]]), "save_as": "a3"},
            stock("$a1", "2"),
            stock("$a2", "3"),
            stock("$a3", "1"),
            stock("$a1", "-1"),
        ],
    },
    {
        "name": "samengesteld artikel: prijswijziging onderdeel",
        "config": ADMIN,
        "setup": IC_SETUP,
        "expect_diff": {"tabel parts": "Perl geeft een prijswijziging van een onderdeel niet door aan het samengestelde "
                                       "artikel (omgedraaide voorwaarde IC.pm 281); Python rekent de prijzen opnieuw "
                                       "uit de onderdelen (README, fase 3 SA3)",
                        "tabel audittrail": "zie tabel parts"},
        "ops": PARTS_OPS + [
            {**ic(item="assembly", fields={"partnumber": "S-1", "description": "Bodem", "income_accno": "8520",
                                           "taxes": ["1810"], "priceupdate": "2026-02-01"},
                  components=[["O-1", "3", 0]]), "save_as": "a2"},
            ic(id="$o1", fields={"sellprice": "2.75", "lastcost": "1.2"}),
        ],
    },
]
