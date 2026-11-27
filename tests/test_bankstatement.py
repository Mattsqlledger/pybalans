"""Tests voor het inlezen van bankafschriften (CAMT.053 en MT940).

Alle voorbeelddata is verzonnen; de IBAN's hebben wel een geldig controlegetal.
Geen database nodig.
"""

from datetime import date
from decimal import Decimal

import pytest

from sqlledger.bankstatement import (
    BankStatementError,
    Statement,
    detect_format,
    is_valid_iban,
    parse,
)
from sqlledger.errors import ValidationError

D = Decimal

# ------------------------------------------------------------------ CAMT .02

CAMT_02 = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">
 <BkToCstmrStmt>
  <GrpHdr><MsgId>MSG-1</MsgId><CreDtTm>2023-03-02T06:00:00</CreDtTm></GrpHdr>
  <Stmt>
   <Id>STMT-2023-061</Id>
   <ElctrncSeqNb>61</ElctrncSeqNb>
   <CreDtTm>2023-03-02T06:00:00+01:00</CreDtTm>
   <Acct><Id><IBAN>NL20INGB0001234567</IBAN></Id><Ccy>EUR</Ccy></Acct>
   <Bal><Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp>
    <Amt Ccy="EUR">1000.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Dt><Dt>2023-03-01</Dt></Dt></Bal>
   <Bal><Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp>
    <Amt Ccy="EUR">995.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Dt><Dt>2023-03-01</Dt></Dt></Bal>
   <Ntry>
    <Amt Ccy="EUR">250.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Sts>BOOK</Sts>
    <BookgDt><Dt>2023-03-01</Dt></BookgDt><ValDt><Dt>2023-03-01</Dt></ValDt>
    <AcctSvcrRef>REF-0001</AcctSvcrRef>
    <BkTxCd><Domn><Cd>PMNT</Cd><Fmly><Cd>RCDT</Cd><SubFmlyCd>ESCT</SubFmlyCd></Fmly></Domn></BkTxCd>
    <NtryDtls><TxDtls>
     <Refs><EndToEndId>E2E-1</EndToEndId></Refs>
     <RltdPties>
      <Dbtr><Nm>Jansen BV</Nm></Dbtr>
      <DbtrAcct><Id><IBAN>NL44RABO0123456789</IBAN></Id></DbtrAcct>
      <Cdtr><Nm>Eigen Bedrijf</Nm></Cdtr>
     </RltdPties>
     <RltdAgts><DbtrAgt><FinInstnId><BIC>RABONL2U</BIC></FinInstnId></DbtrAgt></RltdAgts>
     <RmtInf><Ustrd>Factuur 2023-001</Ustrd><Ustrd>en 2023-002</Ustrd></RmtInf>
    </TxDtls></NtryDtls>
   </Ntry>
   <Ntry>
    <Amt Ccy="EUR">300.00</Amt><CdtDbtInd>DBIT</CdtDbtInd><Sts>BOOK</Sts>
    <BookgDt><Dt>2023-03-01</Dt></BookgDt><ValDt><Dt>2023-03-01</Dt></ValDt>
    <AcctSvcrRef>REF-0002</AcctSvcrRef>
    <AddtlNtryInf>Batch 2 posten</AddtlNtryInf>
    <NtryDtls>
     <Btch><NbOfTxs>2</NbOfTxs></Btch>
     <TxDtls>
      <Refs><EndToEndId>BATCH-A</EndToEndId></Refs>
      <AmtDtls><TxAmt><Amt Ccy="EUR">100.00</Amt></TxAmt></AmtDtls>
      <RltdPties><Cdtr><Nm>Leverancier A</Nm></Cdtr>
       <CdtrAcct><Id><IBAN>NL91ABNA0417164300</IBAN></Id></CdtrAcct></RltdPties>
      <RmtInf><Strd><CdtrRefInf><Ref>1234567890123456</Ref></CdtrRefInf></Strd></RmtInf>
     </TxDtls>
     <TxDtls>
      <Refs><EndToEndId>BATCH-B</EndToEndId></Refs>
      <AmtDtls><TxAmt><Amt Ccy="EUR">200.00</Amt></TxAmt></AmtDtls>
      <RltdPties><Cdtr><Nm>Leverancier B</Nm></Cdtr>
       <CdtrAcct><Id><IBAN>NL12TRIO0198765432</IBAN></Id></CdtrAcct></RltdPties>
      <RmtInf><Ustrd>Inkoop maart</Ustrd></RmtInf>
     </TxDtls>
    </NtryDtls>
   </Ntry>
   <Ntry>
    <Amt Ccy="EUR">50.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><RvslInd>true</RvslInd><Sts>BOOK</Sts>
    <BookgDt><Dt>2023-03-01</Dt></BookgDt><ValDt><Dt>2023-03-01</Dt></ValDt>
    <AcctSvcrRef>REF-0003</AcctSvcrRef>
    <NtryDtls><TxDtls>
     <Refs><EndToEndId>INC-77</EndToEndId></Refs>
     <RltdPties>
      <Dbtr><Nm>Eigen Bedrijf</Nm></Dbtr>
      <DbtrAcct><Id><IBAN>NL20INGB0001234567</IBAN></Id></DbtrAcct>
      <Cdtr><Nm>Energie NV</Nm></Cdtr>
      <CdtrAcct><Id><IBAN>NL21BUNQ2025550001</IBAN></Id></CdtrAcct>
     </RltdPties>
     <RmtInf><Ustrd>Storno incasso februari</Ustrd></RmtInf>
    </TxDtls></NtryDtls>
   </Ntry>
   <Ntry>
    <Amt Ccy="EUR">999.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Sts>PDNG</Sts>
    <BookgDt><Dt>2023-03-01</Dt></BookgDt>
    <AddtlNtryInf>Nog niet geboekt</AddtlNtryInf>
   </Ntry>
   <Ntry>
    <Amt Ccy="EUR">5.00</Amt><CdtDbtInd>DBIT</CdtDbtInd><Sts>BOOK</Sts>
    <BookgDt><Dt>2023-03-01</Dt></BookgDt><ValDt><Dt>2023-03-01</Dt></ValDt>
    <AcctSvcrRef>REF-0004</AcctSvcrRef>
    <BkTxCd><Prtry><Cd>KOSTEN</Cd><Issr>BANK</Issr></Prtry></BkTxCd>
    <AddtlNtryInf>Kosten   betalingsverkeer</AddtlNtryInf>
   </Ntry>
  </Stmt>
 </BkToCstmrStmt>
</Document>
"""


def test_camt02_header_and_balances():
    [st] = parse(CAMT_02, "afschrift.xml")
    assert isinstance(st, Statement)
    assert st.format == "camt053"
    assert st.version == "001.02"
    assert st.account == "NL20INGB0001234567"
    assert st.iban == "NL20INGB0001234567"
    assert st.currency == "EUR"
    assert st.id == "STMT-2023-061"
    assert st.statement_number == "61"
    assert st.created == date(2023, 3, 2)
    assert st.opening_balance == D("1000.00")
    assert st.opening_date == date(2023, 3, 1)
    assert st.closing_balance == D("995.00")
    assert st.check_balance() == 0


def test_camt02_lines():
    [st] = parse(CAMT_02)
    # 4 geboekte Ntry's, waarvan één batch van 2 -> 5 regels; PDNG valt weg
    assert [ln.amount for ln in st.lines] == [D("250"), D("-100"), D("-200"), D("50"), D("-5")]
    assert [ln.sequence for ln in st.lines] == [1, 2, 3, 4, 5]
    assert all(ln.amount != D("999") for ln in st.lines)

    first = st.lines[0]
    assert first.booking_date == date(2023, 3, 1)
    assert first.value_date == date(2023, 3, 1)
    assert first.currency == "EUR"
    assert first.counterparty_name == "Jansen BV"
    assert first.counterparty_iban == "NL44RABO0123456789"
    assert first.counterparty_bic == "RABONL2U"
    assert first.description == "Factuur 2023-001 en 2023-002"
    assert first.end_to_end_id == "E2E-1"
    assert first.bank_reference == "REF-0001"
    assert first.transaction_code == "PMNT/RCDT/ESCT"
    assert not first.is_reversal


def test_camt02_batch_entry():
    [st] = parse(CAMT_02)
    a, b = st.lines[1], st.lines[2]
    assert (a.counterparty_name, a.counterparty_iban) == ("Leverancier A", "NL91ABNA0417164300")
    assert a.payment_reference == "1234567890123456"
    assert a.description == "Batch 2 posten"  # terugval op AddtlNtryInf
    assert a.end_to_end_id == "BATCH-A"
    assert a.bank_reference == "REF-0002"
    assert b.counterparty_name == "Leverancier B"
    assert b.description == "Inkoop maart"


def test_camt02_reversal_keeps_cdtdbtind_sign():
    [st] = parse(CAMT_02)
    storno = st.lines[3]
    assert storno.is_reversal
    assert storno.amount == D("50.00")  # CRDT = bij, ook bij RvslInd
    # tegenpartij van de oorspronkelijke afschrijving
    assert storno.counterparty_name == "Energie NV"
    assert storno.counterparty_iban == "NL21BUNQ2025550001"


def test_camt02_entry_without_details():
    [st] = parse(CAMT_02)
    kosten = st.lines[4]
    assert kosten.description == "Kosten betalingsverkeer"
    assert kosten.transaction_code == "KOSTEN"
    assert kosten.counterparty_name is None


# ------------------------------------------------------------------ CAMT .08

CAMT_08 = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">
 <BkToCstmrStmt>
  <GrpHdr><MsgId>MSG-8</MsgId><CreDtTm>2024-05-02T07:00:00</CreDtTm></GrpHdr>
  <Stmt>
   <Id>S8-1</Id><LglSeqNb>12</LglSeqNb><CreDtTm>2024-05-02T07:00:00</CreDtTm>
   <Acct><Id><IBAN>NL09KNAB0255555555</IBAN></Id><Ccy>EUR</Ccy></Acct>
   <Bal><Tp><CdOrPrtry><Cd>PRCD</Cd></CdOrPrtry></Tp>
    <Amt Ccy="EUR">100.00</Amt><CdtDbtInd>DBIT</CdtDbtInd><Dt><Dt>2024-04-30</Dt></Dt></Bal>
   <Bal><Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp>
    <Amt Ccy="EUR">450.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Dt><DtTm>2024-05-01T23:59:59</DtTm></Dt></Bal>
   <Ntry>
    <Amt Ccy="EUR">500.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Sts><Cd>BOOK</Cd></Sts>
    <BookgDt><DtTm>2024-05-01T10:00:00</DtTm></BookgDt><ValDt><Dt>2024-05-01</Dt></ValDt>
    <AcctSvcrRef>K-1</AcctSvcrRef>
    <NtryDtls><TxDtls>
     <Refs><EndToEndId>NOTPROVIDED</EndToEndId></Refs>
     <Amt Ccy="EUR">500.00</Amt><CdtDbtInd>CRDT</CdtDbtInd>
     <RltdPties>
      <Dbtr><Pty><Nm>Klant A</Nm></Pty></Dbtr>
      <DbtrAcct><Id><IBAN>NL02ABNA0123456789</IBAN></Id></DbtrAcct>
     </RltdPties>
     <RltdAgts><DbtrAgt><FinInstnId><BICFI>ABNANL2A</BICFI></FinInstnId></DbtrAgt></RltdAgts>
     <RmtInf><Strd><CdtrRefInf><Tp><CdOrPrtry><Cd>SCOR</Cd></CdOrPrtry></Tp>
      <Ref>RF18539007547034</Ref></CdtrRefInf></Strd></RmtInf>
    </TxDtls></NtryDtls>
   </Ntry>
   <Ntry>
    <Amt Ccy="EUR">50.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Sts><Cd>BOOK</Cd></Sts>
    <BookgDt><Dt>2024-05-01</Dt></BookgDt>
    <AcctSvcrRef>K-2</AcctSvcrRef>
    <NtryDtls><TxDtls>
      <Amt Ccy="EUR">30.00</Amt>
      <RltdPties><Dbtr><Pty><Nm>Lid 1</Nm></Pty></Dbtr></RltdPties>
      <RmtInf><Ustrd>Contributie</Ustrd></RmtInf>
     </TxDtls><TxDtls>
      <Amt Ccy="EUR">20.00</Amt>
      <RltdPties><Dbtr><Pty><Nm>Lid 2</Nm></Pty></Dbtr></RltdPties>
      <RmtInf><Ustrd>Contributie</Ustrd></RmtInf>
    </TxDtls></NtryDtls>
   </Ntry>
   <Ntry>
    <Amt Ccy="EUR">75.00</Amt><CdtDbtInd>DBIT</CdtDbtInd><Sts><Cd>PDNG</Cd></Sts>
    <BookgDt><Dt>2024-05-01</Dt></BookgDt>
   </Ntry>
  </Stmt>
  <Stmt>
   <Id>S8-2</Id><Acct><Id><Othr><Id>123456</Id></Othr></Id></Acct>
   <Bal><Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp>
    <Amt Ccy="USD">10.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Dt><Dt>2024-05-01</Dt></Dt></Bal>
  </Stmt>
 </BkToCstmrStmt>
</Document>
"""


def test_camt08():
    st, st2 = parse(CAMT_08)
    assert st.version == "001.08"
    assert st.statement_number == "12"
    assert st.opening_balance == D("-100.00")
    assert st.closing_date == date(2024, 5, 1)
    assert [ln.amount for ln in st.lines] == [D("500"), D("30"), D("20")]
    assert st.check_balance() == 0

    a = st.lines[0]
    assert a.booking_date == date(2024, 5, 1)
    assert a.counterparty_name == "Klant A"
    assert a.counterparty_iban == "NL02ABNA0123456789"
    assert a.counterparty_bic == "ABNANL2A"
    assert a.payment_reference == "RF18539007547034"
    assert a.end_to_end_id is None  # NOTPROVIDED
    assert a.description is None
    assert [ln.counterparty_name for ln in st.lines[1:]] == ["Lid 1", "Lid 2"]
    assert st.lines[1].value_date is None

    # tweede afschrift: rekeningnummer zonder IBAN, valuta uit saldo
    assert st2.account == "123456"
    assert st2.iban is None
    assert st2.currency == "USD"
    assert st2.lines == ()
    assert st2.check_balance() is None


def test_camt_namespace_versions():
    for v in ("02", "04", "10", "13"):
        data = CAMT_02.replace(b"camt.053.001.02", f"camt.053.001.{v}".encode())
        [st] = parse(data)
        assert st.version == f"001.{v}"
        assert len(st.lines) == 5
    with pytest.raises(BankStatementError):
        parse(CAMT_02.replace(b"camt.053.001.02", b"camt.053.001.14"))


def test_camt_batch_without_amounts_uses_entry():
    data = CAMT_02.replace(b"<AmtDtls><TxAmt><Amt Ccy=\"EUR\">200.00</Amt></TxAmt></AmtDtls>", b"")
    [st] = parse(data)
    # batch valt terug op één regel met het Ntry-bedrag
    assert [ln.amount for ln in st.lines] == [D("250"), D("-300"), D("50"), D("-5")]
    assert st.check_balance() == 0


def test_xml_doctype_refused():
    bomb = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"><BkToCstmrStmt>&lol2;</BkToCstmrStmt></Document>
"""
    with pytest.raises(BankStatementError, match="DOCTYPE"):
        parse(bomb)
    with pytest.raises(BankStatementError):
        parse(CAMT_02.replace(b"<?xml version=\"1.0\" encoding=\"UTF-8\"?>", b"<?xml version=\"1.0\"?><!entity x \"y\">"))


def test_other_xml_refused():
    with pytest.raises(BankStatementError, match="CAMT.053"):
        parse(b'<?xml version="1.0"?><Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.054.001.02"/>')
    with pytest.raises(BankStatementError, match="XML"):
        parse(b"<Document xmlns='urn:x:camt.053.001.02'><Stmt>")


# ------------------------------------------------------------------ MT940

MT940_ING = b"""{1:F01INGBNL2ABXXX0000000000}{2:I940INGBNL2AXN}{4:
:20:P231231000000001
:25:NL20INGB0001234567EUR
:28C:00000
:60F:C231229EUR1000,00
:61:2312310102D25,50NDDTEREF//00000000001234
/TRCD/01028/
:86:/EREF/E2E-ING-1//MARF/MANDAAT1//CSID/NL00ZZZ000000000000//CNTP/NL
44RABO0123456789/RABONL2U/Energie NV/Utrecht//REMI/USTD//Termijn jan
uari 2024/
:61:2312311231C100,00NTRFEREF//00000000001235
:86:/EREF/NOTPROVIDED//CNTP/NL91ABNA0417164300/ABNANL2A/Klant BV///REMI
/STRD/CUR/1234567890123456/
:62F:C231231EUR1074,50
:64:C231231EUR1074,50
:86:/SUM/2/1/25,50/100,00/
-}"""


def test_mt940_ing_structured():
    assert detect_format(MT940_ING) == "mt940"
    [st] = parse(MT940_ING, "ing.940")
    assert st.format == "mt940"
    assert st.id == "P231231000000001"
    assert st.account == "NL20INGB0001234567"
    assert st.currency == "EUR"
    assert st.statement_number == "00000"
    assert st.opening_balance == D("1000.00")
    assert st.opening_date == date(2023, 12, 29)
    assert st.closing_balance == D("1074.50")
    assert st.available_balance == D("1074.50")
    assert st.check_balance() == 0
    assert len(st.lines) == 2

    a, b = st.lines
    assert a.amount == D("-25.50")
    assert a.value_date == date(2023, 12, 31)
    assert a.booking_date == date(2024, 1, 2)  # jaarwissel
    assert a.transaction_code == "NDDT"
    assert a.bank_reference == "00000000001234"
    assert a.end_to_end_id == "E2E-ING-1"
    assert a.counterparty_iban == "NL44RABO0123456789"
    assert a.counterparty_bic == "RABONL2U"
    assert a.counterparty_name == "Energie NV"
    assert a.description == "Termijn januari 2024"

    assert b.amount == D("100.00")
    assert b.booking_date == date(2023, 12, 31)
    assert b.end_to_end_id is None
    assert b.payment_reference == "1234567890123456"
    assert b.counterparty_name == "Klant BV"
    assert b.description is None
    # de /SUM/-regel na :64: hoort bij geen enkele mutatie
    assert all("SUM" not in (ln.description or "") for ln in st.lines)


def test_mt940_booking_date_year_back():
    data = MT940_ING.replace(b":61:2312311231C100,00", b":61:2401011229C100,00")
    [st] = parse(data)
    assert st.lines[1].value_date == date(2024, 1, 1)
    assert st.lines[1].booking_date == date(2023, 12, 29)


MT940_RABO = b""":940:
:20:940S231115
:25:NL44RABO0123456789 EUR
:28C:23315
:60F:C231114EUR500,00
:61:231115C000000000150,00N541NONREF
NL91ABNA0417164300
:86:/EREF/RABO-E2E-1/ORDP//NAME/Pietje Puk/ADDR/Dorpsstraat 1 Ergens/R
EMI/Factuur 77/ISDT/2023-11-15
:61:231115RC000000000020,00N543NONREF
:86:/RTRN/MS03/EREF/RABO-E2E-0/BENM//NAME/Retour BV/REMI/Teruggeboekt
:62F:C231115EUR630,00
"""


def test_mt940_rabo_style():
    [st] = parse(MT940_RABO)
    assert st.account == "NL44RABO0123456789"
    assert st.currency == "EUR"
    a, b = st.lines
    assert a.amount == D("150.00")
    assert a.transaction_code == "N541"
    assert a.bank_reference is None
    assert a.counterparty_iban == "NL91ABNA0417164300"  # uit 2e regel van :61:
    assert a.counterparty_name == "Pietje Puk"
    assert a.end_to_end_id == "RABO-E2E-1"
    assert a.description == "Factuur 77"
    # RC = storno van een bijschrijving -> af
    assert b.is_reversal
    assert b.amount == D("-20.00")
    assert b.counterparty_name == "Retour BV"
    assert st.check_balance() == 0


MT940_ABN = b"""ABNANL2A
940
ABNANL2A
:20:ABN AMRO BANK NV
:25:417164300
:28:30801/1
:60F:C230801EUR2000,00
:61:230801D50,00N422NONREF
:86:/TRTP/SEPA OVERBOEKING/IBAN/NL20INGB0001234567/BIC/INGBNL2A/NAME/Webs
hop BV/REMI/Bestelling 4711/EREF/NOTPROVIDED
:61:230801C12,34N196NONREF
:86:GIRO   1234567 BETALINGSKENM. 1234 5678 9012 3456
HR JANSEN
:62F:C230801EUR1962,34
-
ABNANL2A
940
ABNANL2A
:20:ABN AMRO BANK NV
:25:417164300
:28:30802/1
:60F:C230801EUR1962,34
:61:230802D10,00N426NONREF
:86:BEA   NR:12345 02.08.23/10.15 SUPERMARKT
AMSTERDAM
:62F:C230802EUR1952,34
-
"""


def test_mt940_abn_with_header_and_multiple_statements():
    st1, st2 = parse(MT940_ABN)
    assert st1.account == "417164300"
    # ABN zet een kaal rekeningnummer in :25:; met de BIC uit de kop wordt het een IBAN (BT9)
    assert st1.bic == "ABNANL2A" and st1.iban == "NL91ABNA0417164300"
    assert st1.statement_number == "30801/1"
    a, b = st1.lines
    assert a.amount == D("-50.00")
    assert a.counterparty_iban == "NL20INGB0001234567"
    assert a.counterparty_bic == "INGBNL2A"
    assert a.counterparty_name == "Webshop BV"
    assert a.description == "Bestelling 4711"
    assert a.end_to_end_id is None
    # ongestructureerd: hele tekst als omschrijving, kenmerk eruit gehaald
    assert b.description == "GIRO 1234567 BETALINGSKENM. 1234 5678 9012 3456 HR JANSEN"
    assert b.payment_reference == "1234567890123456"
    assert st1.check_balance() == 0

    assert st2.statement_number == "30802/1"
    [c] = st2.lines
    assert c.description == "BEA NR:12345 02.08.23/10.15 SUPERMARKT AMSTERDAM"
    assert c.counterparty_name is None
    assert st2.check_balance() == 0


def test_mt940_abn_without_separators():
    data = MT940_ABN.replace(b"\n-\n", b"\n").replace(b"\n", b"\r\n")
    st1, st2 = parse(data)
    assert st1.lines[1].description.endswith("HR JANSEN")
    assert st2.lines[0].description.endswith("AMSTERDAM")
    assert st1.check_balance() == 0


def test_mt940_unstructured_with_labels_latin1():
    data = (
        ":20:STARTUMS\n:25:NL12TRIO0198765432\n:28C:1\n:60F:C240105EUR0,00\n"
        ":61:240105C10,00NTRFNONREF//TRIO-1\n"
        ":86:SEPA OVERBOEKING IBAN: NL21BUNQ2025550001 BIC: BUNQNL2A NAAM: Café De Hoek "
        "OMSCHRIJVING: Borrel KENMERK: ABC-1\n"
        ":62F:C240105EUR10,00\n"
    ).encode("latin-1")
    [st] = parse(data)
    [ln] = st.lines
    assert ln.bank_reference == "TRIO-1"
    assert ln.counterparty_iban == "NL21BUNQ2025550001"
    assert ln.counterparty_bic == "BUNQNL2A"
    assert ln.counterparty_name == "Café De Hoek"
    assert ln.end_to_end_id == "ABC-1"
    assert ln.description.startswith("SEPA OVERBOEKING IBAN:")


MT940_DUP = b""":20:BUNQ-1
:25:NL21BUNQ2025550001
:28C:7
:60F:C240301EUR100,00
:61:240301D4,50NMSCNONREF
:86:Koffie
:61:240301D4,50NMSCNONREF
:86:Koffie
:62F:C240301EUR91,00
"""


def test_dedup_key():
    [st] = parse(MT940_DUP)
    a, b = st.lines
    assert (a.occurrence, b.occurrence) == (0, 1)
    assert a.dedup_key != b.dedup_key
    assert len(a.dedup_key) == 64
    # opnieuw inlezen geeft dezelfde sleutels
    [again] = parse(MT940_DUP)
    assert [ln.dedup_key for ln in again.lines] == [a.dedup_key, b.dedup_key]
    # andere rekening -> andere sleutel
    [other] = parse(MT940_DUP.replace(b"NL21BUNQ2025550001", b"NL09KNAB0255555555"))
    assert other.lines[0].dedup_key != a.dedup_key
    # sleutel hangt niet af van de schrijfwijze van het bedrag
    [same] = parse(MT940_DUP.replace(b"D4,50", b"D4,5"))
    assert same.lines[0].dedup_key == a.dedup_key


def test_dedup_keys_unique_in_camt():
    [st] = parse(CAMT_02)
    keys = [ln.dedup_key for ln in st.lines]
    assert len(set(keys)) == len(keys)


def test_check_balance_difference_and_unknown():
    [st] = parse(MT940_DUP.replace(b":62F:C240301EUR91,00", b":62F:C240301EUR90,00"))
    assert st.check_balance() == D("-1.00")
    [st] = parse(MT940_DUP.replace(b":62F:C240301EUR91,00\n", b""))
    assert st.closing_balance is None
    assert st.check_balance() is None
    assert st.total == D("-9.00")


def test_debit_balance():
    [st] = parse(MT940_DUP.replace(b":60F:C240301EUR100,00", b":60F:D240301EUR100,00")
                 .replace(b":62F:C240301EUR91,00", b":62F:D240301EUR109,00"))
    assert st.opening_balance == D("-100")
    assert st.check_balance() == 0


# ------------------------------------------------------------------ fouten


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"   \n",
        b"Datum;Bedrag\n2024-01-01;10,00\n",
        b":20:ALLEEN-REF\n:25:123\n",
    ],
)
def test_unknown_format(data):
    with pytest.raises(BankStatementError):
        parse(data, "x.csv")


def test_error_is_validation_error():
    assert issubclass(BankStatementError, ValidationError)


def test_bad_61_line():
    with pytest.raises(BankStatementError, match=":61:"):
        parse(MT940_DUP.replace(b":61:240301D4,50NMSCNONREF\n:86:Koffie\n:61:", b":61:XYZ\n:61:", 1))


def test_is_valid_iban():
    assert is_valid_iban("NL20INGB0001234567")
    assert is_valid_iban("nl20 ingb 0001 2345 67")
    assert not is_valid_iban("NL21INGB0001234567")
    assert not is_valid_iban("417164300")
