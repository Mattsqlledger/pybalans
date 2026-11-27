"""Afletteren (sqlledger.reconcile, port van RC.pm/rc.pl, fase 2 L1–L5)."""

from datetime import date
from decimal import Decimal

import pytest

from sqlledger import GLTransaction, JournalLine, OutOfBalanceError, UserConfig, ValidationError
from sqlledger.amounts import parse_amount
from sqlledger.reconcile import Reconciliation, key_token, parse_token, parse_tokens

D = Decimal
BANK = "1140"          # Rabobank: in de testdata zonder mutaties


@pytest.fixture
def rc(conn, config):
    return Reconciliation(conn, config)


def book(gl, day, amount, source=None, accno=BANK, contra="2400", cleared=None, description="bank"):
    """Bij (amount > 0) of af (amount < 0) op de bankrekening."""
    amount = D(amount)
    bank = JournalLine(accno, debit=amount if amount > 0 else D(0), credit=-amount if amount < 0 else D(0),
                       source=source)
    other = JournalLine(contra, debit=-amount if amount < 0 else D(0), credit=amount if amount > 0 else D(0))
    tid = gl.post_transaction(GLTransaction(transdate=day, description=description, lines=[bank, other]))
    if cleared:      # een nieuwe boeking krijgt geen afletterdatum mee (alleen bij vervangen)
        gl.conn.execute("UPDATE acc_trans SET cleared = %s WHERE trans_id = %s AND chart_id = "
                        "(SELECT id FROM chart WHERE accno = %s)", (cleared, tid, accno))
    return tid


def cleared(conn, tid, accno=BANK):
    return [r[0] for r in conn.execute(
        "SELECT ac.cleared FROM acc_trans ac JOIN chart c ON c.id = ac.chart_id "
        "WHERE ac.trans_id = %s AND c.accno = %s ORDER BY ac.amount", (tid, accno)).fetchall()]


def keys_of(st, *pred):
    return [k for ln in st.lines if all(p(ln) for p in pred) for k in ln.keys]


def test_tokens_roundtrip():
    k = (123, date(2026, 7, 1), "B 1/ü;x")
    assert parse_token(key_token(k)) == k
    assert parse_tokens(f"{key_token(k)} {key_token((1, date(2026, 1, 2), ''))}")[1] == (1, date(2026, 1, 2), "")
    with pytest.raises(ValidationError):
        parse_token("1.2026.zz")


def test_accounts(rc):
    assert [a["accno"] for a in rc.accounts()] == ["1100"]
    alle = [a["accno"] for a in rc.accounts(alle_balans=True)]
    assert "1140" in alle and "1600" in alle and "8000" not in alle


def test_statement_and_reconcile_round(conn, gl, rc):
    a = book(gl, date(2026, 6, 25), "100.00", "B-1")            # vóór de periode, nog niet afgeletterd
    b = book(gl, date(2026, 7, 3), "-40.00", "B-2")
    c = book(gl, date(2026, 7, 10), "250.00", "B-3")
    d = book(gl, date(2026, 8, 2), "5.00", "B-4")                # na de periode
    st = rc.statement(BANK, date(2026, 7, 1), date(2026, 7, 31))
    assert st.beginbalance == 0 and st.statementbalance == 0 and st.statementdate == date(2026, 7, 31)
    assert [(ln.source, ln.debit, ln.credit, ln.checked) for ln in st.lines] == [
        ("B-1", D("100.00"), D(0), False),
        ("B-2", D(0), D("40.00"), False), ("B-3", D("250.00"), D(0), False)]
    assert [ln.balance for ln in st.lines] == [D("100.00"), D("60.00"), D("310.00")]   # activa: debet +
    assert st.difference() == 0
    sel = keys_of(st, lambda ln: ln.source in ("B-1", "B-3"))
    assert st.difference(D("350.00"), st.selected_groups(sel)) == 0
    res = rc.reconcile(BANK, date(2026, 7, 31), D("350.00"), sel, date(2026, 7, 1))
    assert (res.cleared, res.uncleared, res.lines) == (2, 0, 2)
    assert cleared(conn, a) == [date(2026, 7, 31)] and cleared(conn, b) == [None] and cleared(conn, d) == [None]
    row = conn.execute("SELECT * FROM x_reconciliation WHERE id = %s", (res.id,)).fetchone()
    assert row[2] == date(2026, 7, 31) and D(str(row[5])) == D("350.00")
    assert conn.execute("SELECT count(*) FROM audittrail WHERE tablename = 'x_reconciliation' "
                        "AND formname = 'reconciliation'").fetchone()[0] == 1
    # volgende periode: beginsaldo = afgeletterd vóór 1-8; B-2 (juli, niet afgeletterd) komt terug
    st2 = rc.statement(BANK, date(2026, 8, 1), date(2026, 8, 31))
    assert st2.beginbalance == D("350.00") and not st2.warnings
    assert st2.previous["statementdate"] == date(2026, 7, 31)
    assert [ln.source for ln in st2.lines] == ["B-2", "B-4"]
    assert st2.statementbalance == D("350.00")
    # B-1 en B-3 staan in juli aangevinkt, ook in een herhaald scherm
    st = rc.statement(BANK, date(2026, 7, 1), date(2026, 7, 31))
    assert [ln.checked for ln in st.lines] == [True, False, True]
    assert st.statementbalance == D("350.00") and st.difference() == 0


def test_out_of_balance_refused_with_comma_format(conn, gl, rc):
    """Perl: verschil "0,50" in een verborgen veld * 1 = 0 → werd opgeslagen. Nu rekent de server."""
    book(gl, date(2026, 7, 3), "10.50", "B-1")
    st = rc.statement(BANK, date(2026, 7, 1), date(2026, 7, 31))
    nl = Reconciliation(conn, UserConfig(login="admin", numberformat="1.000,00"))
    balance = parse_amount("10,00", "1.000,00")
    with pytest.raises(OutOfBalanceError):
        nl.reconcile(BANK, date(2026, 7, 31), balance, keys_of(st), date(2026, 7, 1))
    assert conn.execute("SELECT count(*) FROM acc_trans WHERE cleared IS NOT NULL").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM x_reconciliation").fetchone()[0] == 0
    nl.reconcile(BANK, date(2026, 7, 31), parse_amount("10,50", "1.000,00"), keys_of(st), date(2026, 7, 1))
    with pytest.raises(ValidationError):           # meer decimalen dan precisie
        nl.reconcile(BANK, date(2026, 7, 31), D("10.501"), keys_of(st), date(2026, 7, 1))


def test_select_all_keeps_old_cleared_dates(conn, gl, rc):
    """Perl select_all (r505, ongezette $i) gaf álle regels de nieuwe datum, ook die al afgeletterd waren."""
    old = book(gl, date(2026, 7, 2), "20.00", "B-1", cleared=date(2026, 7, 15))
    new = book(gl, date(2026, 7, 20), "30.00", "B-2")
    st = rc.statement(BANK, date(2026, 7, 1), date(2026, 7, 31))
    assert [ln.checked for ln in st.lines] == [True, False]
    rc.reconcile(BANK, date(2026, 7, 31), D("50.00"), keys_of(st), date(2026, 7, 1))
    assert cleared(conn, old) == [date(2026, 7, 15)] and cleared(conn, new) == [date(2026, 7, 31)]
    # uitvinken binnen de selectie haalt de datum weg; rest blijft
    st = rc.statement(BANK, date(2026, 7, 1), date(2026, 7, 31))
    res = rc.reconcile(BANK, date(2026, 7, 31), D("30.00"), keys_of(st, lambda ln: ln.source == "B-2"),
                       date(2026, 7, 1))
    assert res.uncleared == 1 and cleared(conn, old) == [None] and cleared(conn, new) == [date(2026, 7, 31)]


def test_unchecking_never_touches_beginning_balance(conn, gl, rc):
    a = book(gl, date(2026, 6, 2), "20.00", "B-1", cleared=date(2026, 6, 30))
    book(gl, date(2026, 7, 2), "5.00", "B-2")
    st = rc.statement(BANK, date(2026, 7, 1), date(2026, 7, 31))
    assert st.beginbalance == D("20.00") and [ln.source for ln in st.lines] == ["B-2"]
    rc.reconcile(BANK, date(2026, 7, 31), D("20.00"), [], date(2026, 7, 1))
    assert cleared(conn, a) == [date(2026, 6, 30)]


def test_later_round_not_cleared_by_earlier_statement(conn, gl, rc):
    """Een regel die in een latere ronde is afgeletterd staat niet aangevinkt en blijft zo."""
    a = book(gl, date(2026, 7, 2), "20.00", "B-1", cleared=date(2026, 8, 31))
    st = rc.statement(BANK, date(2026, 7, 1), date(2026, 7, 31))
    assert [ln.checked for ln in st.lines] == [False]
    rc.reconcile(BANK, date(2026, 7, 31), D("0"), [], date(2026, 7, 1))
    assert cleared(conn, a) == [date(2026, 8, 31)]


def _raw_gl(conn, tid, day, amount, source, name):
    conn.execute("INSERT INTO gl (id, reference, description, transdate) VALUES (%s, %s, %s, %s)",
                 (tid, f"R{tid}", name, day))
    for accno, amt in ((BANK, -amount), ("2400", amount)):
        conn.execute("INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, source) "
                     "SELECT %s, id, %s, %s, %s FROM chart WHERE accno = %s", (tid, amt, day, source, accno))


def test_summary_id_substring(conn, rc):
    """Perl r267: ``id !~ /$ref->{id}/`` — boeking 12 werd niet toegevoegd als 123 er al stond."""
    _raw_gl(conn, 123, date(2026, 7, 5), 10.0, "BATCH", "honderddrieëntwintig")
    _raw_gl(conn, 12, date(2026, 7, 5), 15.0, "BATCH", "twaalf")
    st = rc.statement(BANK, date(2026, 7, 1), date(2026, 7, 31))
    assert len(st.lines) == 1 and sorted(st.lines[0].trans_ids) == [12, 123]
    assert st.lines[0].debit == D("25.00") and sorted(st.lines[0].names) == ["honderddrieëntwintig", "twaalf"]
    rc.reconcile(BANK, date(2026, 7, 31), D("25.00"), st.lines[0].keys, date(2026, 7, 1))
    assert cleared(conn, 12) == [date(2026, 7, 31)] and cleared(conn, 123) == [date(2026, 7, 31)]
    detail = rc.statement(BANK, date(2026, 7, 1), date(2026, 7, 31), summary=False)
    assert len(detail.lines) == 2 and all(ln.checked for ln in detail.lines)


def test_identical_lines_union_all(conn, rc):
    """Twee identieke regels (zelfde boeking, datum, kenmerk, bedrag): Perl's UNION maakte er één van."""
    _raw_gl(conn, 777, date(2026, 7, 5), 10.0, "DUP", "dubbel")
    conn.execute("INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, source) "
                 "SELECT 777, id, -10, '2026-07-05', 'DUP' FROM chart WHERE accno = %s", (BANK,))
    conn.execute("INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, source) "
                 "SELECT 777, id, 10, '2026-07-05', NULL FROM chart WHERE accno = '2400'")
    detail = rc.statement(BANK, date(2026, 7, 1), date(2026, 7, 31), summary=False)
    assert [ln.debit for ln in detail.lines] == [D("10.00"), D("10.00")]
    # zelfde sleutel → één groep, samen aangevinkt en samen afgeletterd
    assert detail.lines[0].group == detail.lines[1].group
    assert detail.difference(D("10.00"), detail.selected_groups(detail.lines[0].keys)) == D("10.00")
    with pytest.raises(OutOfBalanceError):
        rc.reconcile(BANK, date(2026, 7, 31), D("10.00"), detail.lines[0].keys, date(2026, 7, 1), summary=False)
    rc.reconcile(BANK, date(2026, 7, 31), D("20.00"), detail.lines[0].keys, date(2026, 7, 1), summary=False)
    assert cleared(conn, 777) == [date(2026, 7, 31)] * 2


def test_summary_sign_switch_and_names(conn, gl, rc):
    book(gl, date(2026, 7, 5), "10.00", "S", description="een")
    book(gl, date(2026, 7, 5), "20.00", "S", description="twee")
    st = rc.statement(BANK, date(2026, 7, 1), date(2026, 7, 31))
    assert len(st.lines) == 1 and st.lines[0].debit == D("30.00") and sorted(st.lines[0].names) == ["een", "twee"]
    book(gl, date(2026, 7, 5), "-4.00", "S", description="terug")
    st = rc.statement(BANK, date(2026, 7, 1), date(2026, 7, 31))
    assert sorted((ln.debit, ln.credit) for ln in st.lines) == [(D(0), D("4.00")), (D("30.00"), D(0))]


def test_without_fx_account_no_sql_error(conn, gl, rc):
    """Perl r138: ``AND NOT ac.chart_id = $defaults{fxgainloss_accno_id}`` → SQL-fout zonder die rekening."""
    conn.execute("DELETE FROM defaults WHERE fldname LIKE 'fx%%'")
    book(gl, date(2026, 7, 5), "10.00", "B")
    st = rc.statement(BANK, date(2026, 7, 1), date(2026, 7, 31), fx=True)
    assert len(st.lines) == 1


def test_fx_lines(conn, gl, rc):
    tid = gl.post_transaction(GLTransaction(transdate=date(2026, 7, 5), description="fx", lines=[
        JournalLine(BANK, debit=D("100"), source="F"), JournalLine("2400", credit=D("100"))]))
    conn.execute("INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, source, fx_transaction) "
                 "SELECT %s, id, -2, '2026-07-05', 'F', true FROM chart WHERE accno = %s", (tid, BANK))
    conn.execute("INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, source, fx_transaction) "
                 "SELECT %s, id, 2, '2026-07-05', 'F', true FROM chart WHERE accno = '2400'", (tid,))
    st = rc.statement(BANK, date(2026, 7, 1), date(2026, 7, 31))
    assert len(st.lines) == 1 and st.lines[0].debit == D("102.00")
    nofx = rc.statement(BANK, date(2026, 7, 1), date(2026, 7, 31), fx=False)
    assert len(nofx.lines) == 1 and nofx.lines[0].debit == D("100.00")
    # zonder fx afletteren: koersverschilregel met dezelfde sleutel gaat mee (zoals RC::reconcile)
    rc.reconcile(BANK, date(2026, 7, 31), D("100.00"), nofx.lines[0].keys, date(2026, 7, 1), fx=False)
    assert cleared(conn, tid) == [date(2026, 7, 31)] * 2
    detail = rc.statement(BANK, date(2026, 7, 1), date(2026, 7, 31), summary=False)
    assert len(detail.lines) == 2 and detail.lines[1].fx and detail.lines[0].group == detail.lines[1].group
    assert detail.statementbalance == D("102.00") and detail.difference() == 0


def test_without_dates_and_changed_lines(conn, gl, rc):
    book(gl, date(2026, 7, 5), "10.00", "A", cleared=date(2026, 7, 31))
    book(gl, date(2026, 7, 6), "7.00", "B")
    st = rc.statement(BANK)
    # Perl: zonder tot-datum telde het afschriftsaldo ook niet-afgeletterde regels
    assert st.statementbalance == D("10.00") and st.statementdate == date.today()
    assert len(st.lines) == 2 and st.beginbalance == 0
    st = rc.statement(BANK, date(2026, 7, 1), date(2026, 7, 31))
    ghost = (999999, date(2026, 7, 6), "B")
    with pytest.raises(ValidationError):
        rc.reconcile(BANK, date(2026, 7, 31), D("17.00"), keys_of(st) + [ghost], date(2026, 7, 1))
    with pytest.raises(ValidationError):
        rc.statement(BANK, date(2026, 8, 1), date(2026, 7, 1))


def test_report(conn, gl, rc):
    book(gl, date(2026, 6, 5), "10.00", "A", cleared=date(2026, 7, 31))
    book(gl, date(2026, 7, 6), "-7.00", "B", cleared=date(2026, 7, 31))
    book(gl, date(2026, 7, 7), "3.00", "C")
    book(gl, date(2026, 8, 1), "1.00", "D", cleared=date(2026, 8, 31))
    rep = rc.report(BANK, date(2026, 7, 1), date(2026, 7, 31))
    assert [(ln.source, ln.debit, ln.credit) for ln in rep.lines] == [("A", D("10.00"), D(0)),
                                                                     ("B", D(0), D("7.00"))]
    assert rep.total_debit == D("10.00") and rep.total_credit == D("7.00") and rep.beginbalance == 0
    assert len(rc.report(BANK).lines) == 3


def test_warning_when_begin_differs_from_previous_round(conn, gl, rc):
    a = book(gl, date(2026, 7, 2), "20.00", "B-1")
    st = rc.statement(BANK, date(2026, 7, 1), date(2026, 7, 31))
    rc.reconcile(BANK, date(2026, 7, 31), D("20.00"), keys_of(st), date(2026, 7, 1))
    conn.execute("UPDATE acc_trans SET cleared = NULL WHERE trans_id = %s", (a,))
    st = rc.statement(BANK, date(2026, 8, 1), date(2026, 8, 31))
    assert st.warnings and "20" in st.warnings[0]
    assert [h["statementdate"] for h in rc.history(BANK)] == [date(2026, 7, 31)]


def test_liability_sign(conn, gl, rc):
    tid = book(gl, date(2026, 7, 2), "-30.00", "L", accno="1941", contra="2400")
    st = rc.statement("1941", date(2026, 7, 1), date(2026, 7, 31))
    assert st.sign == 1 and st.lines[0].credit == D("30.00") and st.lines[0].balance == D("30.00")
    rc.reconcile("1941", date(2026, 7, 31), D("30.00"), keys_of(st), date(2026, 7, 1))
    assert cleared(conn, tid, "1941") == [date(2026, 7, 31)]


def test_partly_cleared_merged_line(conn, gl, rc):
    """Perl (rc.pl r288): een samengevoegde regel krijgt de status van de eerste regel; het verschil
    klopte dan al bij openen niet. Nu: 'deels', dat deel telt mee en blijft staan."""
    a = book(gl, date(2026, 7, 5), "10.00", None, cleared=date(2026, 7, 31), description="een")
    b = book(gl, date(2026, 7, 5), "20.00", None, description="twee")
    st = rc.statement(BANK, date(2026, 7, 1), date(2026, 7, 31))
    assert len(st.lines) == 1 and not st.lines[0].checked and st.lines[0].partly
    assert st.statementbalance == D("10.00") and st.difference() == 0
    rc.reconcile(BANK, date(2026, 7, 31), D("10.00"), [], date(2026, 7, 1))     # niets veranderd
    assert cleared(conn, a) == [date(2026, 7, 31)] and cleared(conn, b) == [None]
    rc.reconcile(BANK, date(2026, 8, 31), D("30.00"), st.lines[0].keys, date(2026, 7, 1))
    assert cleared(conn, a) == [date(2026, 7, 31)] and cleared(conn, b) == [date(2026, 8, 31)]
