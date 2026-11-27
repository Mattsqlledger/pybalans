"""Scenario's orders en offertes (fase 3 stap 3, blok "oe"): oe.pl (+ is.pl/ir.pl bij factuur maken) als
browser vs de formuliercode van het webscherm.

Draaien: ``python compare/run_compare.py --sldir $SRC --scenarios oe --dbprefix oe``.
"""

from scenarios_is import ADMIN, IS_SETUP, isu


def oeu(**args):
    return {"op": "oe_ui", "args": args}


SCENARIOS = [
    {
        "name": "orderscherm (oe.pl): verkooporder, korting, btw inbegrepen, opnieuw opslaan",
        "config": ADMIN,
        "setup": IS_SETUP,
        "ops": [
            {**oeu(type="sales_order", transdate="2026-03-01", vc_id=900, reqdate="2026-03-20", ponumber="K-77",
                   lines=[{"partnumber": "A-1", "qty": "4", "sellprice": "5"},
                          {"partnumber": "C-1", "qty": "3", "sellprice": "12.95", "discount": "10"},
                          {"partnumber": "D-1", "qty": "1", "sellprice": "10.123"}]), "save_as": "o1"},
            {**oeu(type="sales_order", transdate="2026-03-02", vc_id=902, taxincluded=True,
                   lines=[{"partnumber": "C-1", "qty": "2.5", "sellprice": "12.95", "discount": "5"},
                          {"partnumber": "A-1", "qty": "-1", "sellprice": "4.99"}]), "save_as": "o2"},
            oeu(type="sales_order", id="$o1"),
            oeu(type="sales_order", id="$o2", set={"1": {"qty": "3"}}),
        ],
        "expect_diff": {"tabel semaphore": "Perl laat na opslaan van een geopende order een lock op ar/ap staan "
                                           "(OE.pm retrieve zet locks op oe én ar/ap, save verwijdert alleen oe)"},
    },
    {
        "name": "orderscherm: inkooporder, offerte en aanvraag opslaan, opslaan als nieuw, verwijderen",
        "config": ADMIN,
        "setup": IS_SETUP,
        "ops": [
            {**oeu(type="purchase_order", transdate="2026-02-01", vc_id=905,
                   lines=[{"partnumber": "A-1", "qty": "10", "sellprice": "2.0533"},
                          {"partnumber": "B-1", "qty": "4", "sellprice": "1.25"}]), "save_as": "po"},
            {**oeu(type="sales_quotation", transdate="2026-02-02", vc_id=900, reqdate="2026-03-02",
                   lines=[{"partnumber": "P-1", "qty": "2", "sellprice": "25"}]), "save_as": "q1"},
            {**oeu(type="request_quotation", transdate="2026-02-03", vc_id=905,
                   lines=[{"partnumber": "C-1", "qty": "6", "sellprice": "4.333"}]), "save_as": "r1"},
            {**oeu(type="purchase_order", id="$po", mode="save_as_new"), "save_as": "po2"},
            oeu(type="sales_quotation", id="$q1", mode="delete"),
        ],
        "expect_diff": {"tabel semaphore": "Perl laat na opslaan van een geopende order een lock op ar/ap staan "
                                           "(OE.pm retrieve zet locks op oe én ar/ap, save verwijdert alleen oe)"},
    },
    {
        "name": "orderscherm: offerte -> order -> factuur (alles geleverd)",
        "config": ADMIN,
        "setup": IS_SETUP,
        "ops": [
            {**oeu(type="sales_quotation", transdate="2026-03-01", vc_id=900,
                   lines=[{"partnumber": "A-1", "qty": "4", "sellprice": "5"},
                          {"partnumber": "D-1", "qty": "1", "sellprice": "10"}]), "save_as": "q1"},
            {**oeu(type="sales_quotation", id="$q1", mode="convert", to="sales_order"), "save_as": "o1"},
            {**oeu(type="sales_order", id="$o1", mode="invoice"), "save_as": "i1"},
        ],
        "expect_diff": {"tabel inventory": "O3: Perl schrijft bij factureren magazijnbewegingen voor de geleverde "
                                           "aantallen (inventory); in Python beweegt alleen de factuur de voorraad"},
    },
    {
        "name": "orderscherm: inkooporder -> deellevering -> nabestelling",
        "config": ADMIN,
        "setup": IS_SETUP,
        "ops": [
            {**oeu(type="purchase_order", transdate="2026-02-01", vc_id=905, notes="levering in delen",
                   lines=[{"partnumber": "A-1", "qty": "10", "sellprice": "2"},
                          {"partnumber": "B-1", "qty": "4", "sellprice": "1.25"},
                          {"partnumber": "C-1", "qty": "3", "sellprice": "4.333"}]), "save_as": "po"},
            {**oeu(type="purchase_order", id="$po", mode="invoice", invnumber="LF-1",
                   ship={"1": "6", "2": "4", "3": "0"}), "save_as": "i1"},
        ],
        "expect_diff": {"tabel inventory": "O3: Perl schrijft bij factureren magazijnbewegingen voor de geleverde "
                                           "aantallen (inventory); in Python beweegt alleen de factuur de voorraad"},
    },
    {
        "name": "orderscherm: verkooporder deels gefactureerd, nabestelling gefactureerd",
        "config": ADMIN,
        "setup": IS_SETUP,
        "ops": [
            {**isu(vc="vendor", transdate="2026-02-01", vc_id=905,
                   lines=[{"partnumber": "A-1", "qty": "10", "sellprice": "2"}]), "save_as": "p1"},
            {**oeu(type="sales_order", transdate="2026-03-01", vc_id=900,
                   lines=[{"partnumber": "A-1", "qty": "8", "sellprice": "5"},
                          {"partnumber": "D-1", "qty": "2", "sellprice": "10"}]), "save_as": "o1"},
            {**oeu(type="sales_order", id="$o1", mode="invoice", ship={"1": "5", "2": "1"}), "save_as": "i1"},
        ],
        "expect_diff": {"tabel inventory": "O3: Perl schrijft bij factureren magazijnbewegingen voor de geleverde "
                                           "aantallen (inventory); in Python beweegt alleen de factuur de voorraad"},
    },
    {
        "name": "orderscherm: aanvraag -> inkooporder, order -> offerte (order blijft open)",
        "config": ADMIN,
        "setup": IS_SETUP,
        "ops": [
            {**oeu(type="request_quotation", transdate="2026-02-01", vc_id=905,
                   lines=[{"partnumber": "B-1", "qty": "20", "sellprice": "1.2"}]), "save_as": "r1"},
            {**oeu(type="request_quotation", id="$r1", mode="convert", to="purchase_order"), "save_as": "po"},
            {**oeu(type="sales_order", transdate="2026-03-01", vc_id=902,
                   lines=[{"partnumber": "C-1", "qty": "1", "sellprice": "12.95"}]), "save_as": "o1"},
            {**oeu(type="sales_order", id="$o1", mode="convert", to="quotation"), "save_as": "q1"},
        ],
        "expect_diff": {"tabel oe": "O2: \"Order -> offerte\" sluit de order niet meer (Perl sloot hem)"},
    },
    {
        "name": "orderscherm: vreemde valuta, project op de regel, afdeling, samengesteld artikel (SA11)",
        "config": ADMIN,
        "setup": IS_SETUP,                      # sjabloon: project 700 P-001, afdeling 600 Verkoop (P)
        "ops": [
            {**oeu(type="sales_order", transdate="2026-05-20", vc_id=903, currency="USD", exchangerate="0.9",
                   department_id=600,
                   lines=[{"partnumber": "B-1", "qty": "3", "sellprice": "3.33", "project_id": 700},
                          {"partnumber": "P-1", "qty": "1", "sellprice": "27.5"}]), "save_as": "o1"},
            oeu(type="sales_order", id="$o1", set={"2": {"qty": "2"}}),
        ],
        "expect_diff": {"tabel semaphore": "Perl laat na opslaan van een geopende order een lock op ar/ap staan "
                                           "(OE.pm retrieve zet locks op oe én ar/ap, save verwijdert alleen oe)"},
    },
    {
        "name": "orderscherm: factuur met nabestelling verwijderen (F6, ON3)",
        "config": ADMIN,
        "setup": IS_SETUP,
        "ops": [
            {**isu(vc="vendor", transdate="2026-02-01", vc_id=905,
                   lines=[{"partnumber": "A-1", "qty": "10", "sellprice": "2"}]), "save_as": "p1"},
            {**oeu(type="sales_order", transdate="2026-03-01", vc_id=900,
                   lines=[{"partnumber": "A-1", "qty": "8", "sellprice": "5"}]), "save_as": "o1"},
            {**oeu(type="sales_order", id="$o1", mode="invoice", ship={"1": "5"}), "save_as": "i1"},
            isu(vc="customer", id="$i1", mode="delete"),
        ],
        "expect_diff": {"tabel inventory": "O3: Perl schrijft bij factureren magazijnbewegingen voor de geleverde "
                                           "aantallen (inventory); in Python beweegt alleen de factuur de voorraad",
                        "tabel oe": "ON3: Python verwijdert de onaangeroerde nabestelling die bij de verwijderde factuur "
                                    "ontstond (Perl laat hem staan: restant dubbel open)",
                        "tabel orderitems": "ON3: nabestelling weg en verzonden aantallen van de heropende order op 0 "
                                            "(Perl laat ship staan)",
                        "tabel parts": "O3: na verwijderen van de factuur is er niets geleverd; Perl laat de voorraad "
                                       "toch verlaagd staan (order verlaagde hem, reset_shipped en de factuur heffen "
                                       "elkaar niet op), Python zet hem terug",
                        "tabel audittrail": "ON3: Python logt het verwijderen van de nabestelling; nabestelling en "
                                            "factuur in andere id-volgorde (O4)"},
    },
]
