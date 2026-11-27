"""Inkoopfactuur uploaden (fase 4 stap 4e, IU3–IU5): voorstel kostenrekening, btw en overige
velden per leverancier, splitsen van het totaalbedrag en de vaste kostenrekening (x_vendor_default)."""

from datetime import date
from decimal import Decimal

import pytest

from sqlledger import ArapLedger, ArapLine, ArapTransaction, Contacts, UserConfig
from sqlledger.apupload import PurchaseUpload, split_total
from sqlledger.errors import ValidationError

D = Decimal
VEND = 901          # L001, btw 1800 gekoppeld, termijn 0
MOLEN = 905         # L002, btw 1800, termijn 30


@pytest.fixture(autouse=True)
def old_rates(conn):
    """Oude tarieven (19%/6%) met einddatum; geen inkoophistorie uit de template."""
    conn.execute("UPDATE tax SET validto = '2025-12-31' WHERE taxnumber IS NULL")
    conn.execute("DELETE FROM acc_trans WHERE trans_id IN (SELECT id FROM ap)")
    conn.execute("DELETE FROM ap")


@pytest.fixture
def pu(conn, config):
    return PurchaseUpload(conn, config)


def post(conn, config, vendor, transdate, lines, taxes=None, **kw):
    return ArapLedger(conn, config).post_transaction(ArapTransaction(
        vc="vendor", vc_id=vendor, transdate=transdate, arap_accno="1600",
        lines=[ArapLine(accno=a, amount=D(v)) for a, v in lines],
        taxes={a: D(v) for a, v in (taxes or {}).items()}, **kw))


# ------------------------------------------------------------------ totaal splitsen (IU4)

@pytest.mark.parametrize("total, rate, net, tax", [
    ("121.00", "0.21", "100.00", "21.00"),
    ("100.00", "0.21", "82.64", "17.36"),
    ("109.00", "0.09", "100.00", "9.00"),
    ("10.00", "0.09", "9.17", "0.83"),
    ("0.01", "0.21", "0.01", "0.00"),
    ("-121.00", "0.21", "-100.00", "-21.00"),
    ("50.00", "0", "50.00", "0.00"),
])
def test_split_total(total, rate, net, tax):
    assert split_total(D(total), D(rate)) == (D(net), D(tax))


def test_split_total_with_given_tax_and_precision():
    assert split_total(D("100.00"), D("0.21"), tax=D("17.35")) == (D("82.65"), D("17.35"))
    assert split_total(D("100.00"), None) == (D("100.00"), D("0"))
    assert split_total(D("100"), D("0.21"), precision=0) == (D("83"), D("17"))


# ------------------------------------------------------------------ voorstel (IU3–IU5)

def test_no_history(pu):
    s = pu.suggest(MOLEN, date(2026, 4, 1))
    assert s.expense_accno is None and s.expense_source == "" and s.last_id is None
    assert s.tax_accno == "1800" and s.tax_choice == ["1800"] and s.rates == {"1800": D("0.21")}
    assert (s.terms, s.duedate, s.arap_accno) == (30, date(2026, 5, 1), None)    # geen eigen crediteurenrek.
    assert s.currency and s.department_id is None


def test_last_booking_account_and_tax(pu, conn, config):
    post(conn, config, VEND, date(2026, 3, 1), [("8010", "50")], {"1800": "10.50"})
    last = post(conn, config, VEND, date(2026, 3, 5), [("8100", "100")], {"1800": "21"})
    s = pu.suggest(VEND, date(2026, 4, 1))
    assert (s.expense_accno, s.expense_source, s.several_lines, s.last_id) == ("8100", "laatste", False, last)
    assert (s.tax_accno, s.several_taxes) == ("1800", False)


def test_latest_by_date_not_by_entry(pu, conn, config):
    post(conn, config, VEND, date(2026, 3, 9), [("8100", "100")])
    post(conn, config, VEND, date(2026, 3, 1), [("8010", "50")])          # later ingevoerd, eerdere datum
    assert pu.suggest(VEND, date(2026, 4, 1)).expense_accno == "8100"


def test_several_lines_takes_largest(pu, conn, config):
    post(conn, config, VEND, date(2026, 3, 5), [("8010", "30"), ("8100", "-80"), ("8020", "70")])
    s = pu.suggest(VEND, date(2026, 4, 1))
    assert (s.expense_accno, s.several_lines) == ("8100", True)


def test_same_account_twice_is_not_several(pu, conn, config):
    post(conn, config, VEND, date(2026, 3, 5), [("8010", "30"), ("8010", "70")])
    assert pu.suggest(VEND, date(2026, 4, 1)).several_lines is False


def test_fixed_default_wins(pu, conn, config):
    post(conn, config, VEND, date(2026, 3, 5), [("8100", "100")])
    pu.set_default_expense(VEND, "8020")
    assert pu.default_expense(VEND) == "8020"
    s = pu.suggest(VEND, date(2026, 4, 1))
    assert (s.expense_accno, s.expense_source, s.several_lines) == ("8020", "vast", False)
    pu.set_default_expense(VEND, "")
    assert pu.default_expense(VEND) is None
    assert pu.suggest(VEND, date(2026, 4, 1)).expense_accno == "8100"


def test_default_must_be_an_account(pu):
    with pytest.raises(ValidationError):
        pu.set_default_expense(VEND, "9999")
    with pytest.raises(ValidationError):
        pu.set_default_expense(VEND, "1600")                  # crediteuren, geen kostenrekening
    pu.set_default_expense(VEND, "8100")
    with pytest.raises(ValidationError):
        pu.set_default_expense(999999, "8100")


def test_default_removed_with_vendor(conn, config, pu):
    c = Contacts(conn, config)
    new = c.new("vendor")
    new.name = "Weg BV"
    vid = c.save(new)
    pu.set_default_expense(vid, "8100")
    c.delete("vendor", vid)
    assert pu.default_expense(vid) is None


def test_last_booking_without_tax_means_no_tax(pu, conn, config):
    post(conn, config, VEND, date(2026, 3, 5), [("8100", "100")])
    s = pu.suggest(VEND, date(2026, 4, 1))
    assert s.tax_accno == "" and s.tax_choice == ["1800"]


def test_tax_no_longer_linked_falls_back_to_linked(pu, conn, config):
    post(conn, config, VEND, date(2026, 3, 5), [("8100", "100")], {"1801": "6"})
    s = pu.suggest(VEND, date(2026, 4, 1))
    assert s.tax_accno == "1800"


def test_two_linked_taxes_without_history_must_choose(pu, conn):
    conn.execute("INSERT INTO vendortax VALUES (%s, (SELECT id FROM chart WHERE accno = '1801'))", (MOLEN,))
    conn.execute("UPDATE tax SET validto = NULL WHERE chart_id = (SELECT id FROM chart WHERE accno = '1801')")
    s = pu.suggest(MOLEN, date(2026, 4, 1))
    assert s.tax_accno is None and s.tax_choice == ["1800", "1801"]


def test_two_taxes_on_last_booking_takes_largest(pu, conn, config):
    conn.execute("INSERT INTO vendortax VALUES (%s, (SELECT id FROM chart WHERE accno = '1801'))", (VEND,))
    conn.execute("UPDATE tax SET validto = NULL WHERE chart_id = (SELECT id FROM chart WHERE accno = '1801')")
    post(conn, config, VEND, date(2026, 3, 5), [("8100", "100"), ("8010", "200")], {"1800": "21", "1801": "12"})
    s = pu.suggest(VEND, date(2026, 4, 1))
    assert (s.tax_accno, s.several_taxes) == ("1800", True)


def test_department_from_last_booking(pu, conn, config):
    conn.execute("INSERT INTO department (id, description, role) VALUES (77, 'Winkel', 'P')")
    post(conn, config, VEND, date(2026, 3, 5), [("8100", "100")], department_id=77)
    assert pu.suggest(VEND, date(2026, 4, 1)).department_id == 77


def test_before_ignores_later_bookings(pu, conn, config):
    first = post(conn, config, VEND, date(2026, 3, 1), [("8010", "50")])
    second = post(conn, config, VEND, date(2026, 3, 5), [("8100", "100")])
    assert pu.suggest(VEND, date(2026, 3, 5), before=(date(2026, 3, 5), second)).last_id == first
    assert pu.suggest(VEND, date(2026, 3, 1), before=(date(2026, 3, 1), first)).last_id is None


def test_invoices_with_items_are_ignored(pu, conn, config):
    post(conn, config, VEND, date(2026, 3, 1), [("8010", "50")])
    tid = post(conn, config, VEND, date(2026, 3, 5), [("8100", "100")])
    conn.execute("UPDATE ap SET invoice = true WHERE id = %s", (tid,))
    assert pu.suggest(VEND, date(2026, 4, 1)).expense_accno == "8010"


def test_other_user_config_is_fine(conn):
    assert PurchaseUpload(conn, UserConfig(login="x")).suggest(VEND, date(2026, 4, 1)).tax_accno == "1800"
