"""Tests zonder database: bedragen, datums, nummering, tegenrekeningen."""

from datetime import date
from decimal import Decimal as D

import pytest

from sqlledger.amounts import format_amount, parse_amount, round_amount, to_decimal
from sqlledger.dates import format_date, from_to, parse_date
from sqlledger.gl import _assign_contra, like, sort_order
from sqlledger.numbering import expand, increment


@pytest.mark.parametrize("text,nf,expected", [
    ("1,234.56", "1,000.00", D("1234.56")),
    ("1.234,56", "1.000,00", D("1234.56")),
    ("1234,5", "1000,00", D("1234.5")),
    ("1'234.56", "1'000.00", D("1234.56")),
    ("", "1,000.00", D(0)),
    ("abc", "1,000.00", D(0)),
    ("12abc", "1,000.00", D(12)),
])
def test_parse_amount(text, nf, expected):
    assert parse_amount(text, nf) == expected


@pytest.mark.parametrize("value,places,expected", [
    ("2.675", 2, D("2.68")),
    ("1.005", 2, D("1.01")),
    ("-1.005", 2, D("-1.01")),
    ("1.00499", 2, D("1.00")),   # Perl: 1.01
    ("0.125", 2, D("0.13")),
])
def test_round_amount_half_up(value, places, expected):
    assert round_amount(D(value), places) == expected


@pytest.mark.parametrize("value,nf,places,dash,expected", [
    ("1234.5", "1,000.00", 2, "", "1,234.50"),
    ("-1234567.891", "1.000,00", 2, "-", "(1.234.567,89)"),
    ("-5", "1,000.00", 2, "DRCR", "5.00 DR"),
    ("0", "1,000.00", 2, "0", "0.00"),
    ("0", "1,000.00", 2, "", ""),
    ("12.3", "1,000.00", None, "", "12.3"),
    ("1234567.5", "1'000.00", 2, "", "1'234'567.50"),
])
def test_format_amount(value, nf, places, dash, expected):
    assert format_amount(D(value), nf, places, dash) == expected


def test_to_decimal_float_is_exact_repr():
    assert to_decimal(10.1) == D("10.1")


def test_parse_date_formats():
    assert parse_date("2026-03-02") == date(2026, 3, 2)
    assert parse_date("20260302") == date(2026, 3, 2)
    assert parse_date("02-03-26", "dd-mm-yy") == date(2026, 3, 2)
    assert parse_date("03/02/2026", "mm/dd/yy") == date(2026, 3, 2)
    assert format_date(date(2026, 3, 2), "dd-mm-yy") == "02-03-2026"


def test_from_to():
    assert from_to(2026, 2) == (date(2026, 2, 1), date(2026, 2, 28))
    assert from_to(2026, 1, 3) == (date(2026, 1, 1), date(2026, 3, 31))
    assert from_to(2026, 11, 3) == (date(2026, 11, 1), date(2027, 1, 31))
    assert from_to(2026, 1, 12) == (date(2026, 1, 1), date(2026, 12, 31))
    assert from_to(2026, 1, 0, today=date(2026, 5, 5)) == (date(2026, 1, 1), date(2026, 5, 5))


@pytest.mark.parametrize("current,expected", [
    ("GL-0099", "GL-0100"),
    ("0", "1"),
    ("A9", "A10"),
    ("GL<%YYYY%>-0009", "GL<%YYYY%>-0010"),
    ("2026-0001", "2027-0001"),  # Perl hoogt het eerste getal op
])
def test_increment(current, expected):
    assert increment(current) == expected


def test_expand_templates():
    d = date(2026, 3, 2)
    assert expand("GL<%YYYY%>-1", dateformat="yyyy-mm-dd", transdate=d) == "GL2026-1"
    # Perl zet de delen altijd in volgorde dd, mm, yy
    assert expand("<%YYMMDD%>", dateformat="yyyy-mm-dd", transdate=d) == "020326"
    assert expand("<%CURR%>-1", dateformat="yyyy-mm-dd", transdate=d, currency="USD") == "USD-1"
    assert expand("<%LDM%>", dateformat="dd-mm-yy", transdate=d) == "31-03-2026"
    assert expand("<%DESCRIPTION 1 3%>", dateformat="yyyy-mm-dd", transdate=d,
                  fields={"description": "kas opname"}) == "KOPN"


def test_like():
    assert like("huur") == "%huur%"
    assert like('"MEM-B"') == "MEM-B"
    assert like("h_ur%") == "h_ur%"


def test_sort_order():
    ordinal = {"id": 1, "transdate": 6, "reference": 4, "accno": 9}
    assert sort_order(["id", "transdate", "reference"], None, "ASC", ordinal) == "1 ASC,6,4"
    assert sort_order(["id", "transdate", "reference"], "transdate", "DESC", ordinal) == "6 DESC,1,4"
    # onbekende kolom wordt genegeerd (geen SQL-injectie via sort)
    assert sort_order(["id"], "1; DROP TABLE gl", "ASC", ordinal) == "1 ASC"


def _row(i, typ, accno, amount, link="", transdate="2026-03-01"):
    amount = D(amount)
    return {"id": i, "type": typ, "accno": accno, "gifi_accno": None, "link": link,
            "transdate": transdate, "amount": amount,
            "debit": -amount if amount < 0 else D(0), "credit": amount if amount > 0 else D(0),
            "contra": None, "gifi_contra": None}


def test_contra_simple_gl():
    rows = [_row(1, "gl", "4100", "-100"), _row(1, "gl", "1100", "100")]
    _assign_contra(rows)
    assert rows[0]["contra"] == "1100"
    assert rows[1]["contra"] == "4100"


def test_contra_split_gl():
    rows = [_row(1, "gl", "4100", "-30"), _row(1, "gl", "4110", "-70"), _row(1, "gl", "1100", "100")]
    _assign_contra(rows)
    assert rows[2]["contra"] == "4100 4110"
    assert rows[0]["contra"] == "1100"


def test_contra_ar_invoice_and_payment_tie():
    rows = [
        _row(9, "ar", "1200", "-121", "AR"),
        _row(9, "ar", "8520", "100", "AR_amount"),
        _row(9, "ar", "1810", "21", "AR_tax"),
        _row(9, "ar", "1200", "121", "AR", "2026-03-10"),
        _row(9, "ar", "1100", "-121", "AR_paid", "2026-03-10"),
    ]
    _assign_contra(rows)
    assert rows[0]["contra"].split() == ["8520", "1810"]   # factuurregel
    assert rows[3]["contra"] == "1100"                      # betaalregel
    assert rows[4]["contra"] == "1200"
