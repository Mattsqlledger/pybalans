"""Jaarlijkse klantenlisting België (sqlledger.vatlisting, BTW_LANDEN_ANALYSE.md BE5)."""

from datetime import date
from decimal import Decimal

from sqlledger.vatlisting import ClientListing, be_vat_number

from test_vatreturn import CUST, CUST2, VEND, al, one_rate  # noqa: F401  (fixtures)
from test_vatreturn_be import sale, purchase

D = Decimal


def test_be_vat_number():
    assert be_vat_number("BE 0417.497.106") == ("BE0417497106", "")
    assert be_vat_number("0417497106") == ("BE0417497106", "")
    assert be_vat_number("be0417497107") == ("BE0417497107", "check")
    assert be_vat_number("BE417497106") == ("BE417497106", "format")
    assert be_vat_number("NL123456789B01") == ("", "")
    assert be_vat_number(None) == ("", "")


def test_listing_threshold_credit_notes_and_nil(conn, al):
    conn.execute("UPDATE customer SET taxnumber = 'BE0417497106' WHERE id = %s", (CUST,))
    conn.execute("UPDATE customer SET taxnumber = 'BE0403170701' WHERE id = %s", (CUST2,))
    sale(al, "300", {"1810": "63"})
    sale(al, "-49.99", {"1810": "-10.50"})                      # creditnota: 250,01 > 250
    sale(al, "250", {"1810": "52.50"}, vc_id=CUST2)              # precies 250: niet op de lijst
    sale(al, "999", {"1810": "1"}, d=date(2030, 12, 31))         # ander jaar
    purchase(al, {"8100": "500"}, {})                            # inkoop telt niet
    rep = ClientListing(conn).report(2031)
    assert [(x.vatnumber, x.base_exact, x.tax_exact, x.base, x.tax) for x in rep.lines] == [
        ("BE0417497106", D("250.01"), D("52.50"), 250, 52)]
    assert [x.vatnumber for x in rep.below] == ["BE0403170701"] and not rep.nil and rep.totals == (250, 52)
    assert ClientListing(conn).report(2032).nil
