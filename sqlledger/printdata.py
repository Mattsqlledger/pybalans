"""Gegevens voor documenten (port van io.pl print_form, IS/IR invoice_details en AA::company_details).

``invoice_vars`` geeft voor een opgeslagen factuur dezelfde sjabloonvariabelen als SQL-Ledger vlak
voor ``parse_template`` heeft: losse velden (``invnumber``, ``name``, ``subtotal`` …) en lijsten per
regel (``number``, ``description``, ``qty``, ``linetotal`` …), per btw-rekening (``taxdescription``,
``taxbase``, ``tax`` …) en per betaling (``payment``, ``paymentdate`` …). Bedragen en aantallen zijn
opgemaakt in de getalnotatie van de gebruiker, datums in diens datumnotatie, zoals in Perl. Tekst
wordt niet ge-escaped: dat doet het sjabloon (Jinja autoescape).

Bewuste afwijkingen (README 165–173):

* Printen wijzigt de boeking niet. Perl schreef bij elke afdruk ``ar.dcn`` en ``ar.bank_id`` weg
  (IS::invoice_details); het kenmerk wordt al bij het opslaan berekend.
* ``part``/``service`` zijn leeg in plaats van de tekst "NULL" (Perl duwde het kale woord NULL in de
  lijst, waardoor ``<%if service%>`` altijd waar was).
* ``<accno>_tax`` is de btw van die rekening (Perl gaf daar de btw na contantkorting, meestal 0).
* Bedragen worden met Decimal gerekend en pas aan het eind afgerond (Perl: drijvende komma). Daardoor
  geen "-0", "****" of een cent te weinig in het bedrag in woorden (Perl kapte 12,36 af tot 35 cent),
  en gewichten zonder e-notatie. Gewichten en volume per regel zijn altijd opgemaakt (Perl gaf ze deels
  kaal uit de verborgen velden en telde "4.14" in de notatie 1.000,00 als 414 mee in het totaalgewicht).
* De lijsten ``discount`` en ``projectnumber`` per regel bestaan (Perl duwde de korting in een
  symbolische array, omdat ``discount`` al de klantkorting bevatte, en verloor het projectnummer).
* ``fdy``/``ldy`` (±n) zijn de eerste en laatste dag van het jaar (Perl gaf de maand van de
  factuurdatum in dat jaar).
* ``cd_<accno>_tax`` en ``cd_available`` zijn opgemaakt als bedrag (Perl: kaal getal).
* Betalingen op een rekening zonder koppeling "betaling" staan in de lijst (zoals op het
  factuurscherm, README 25b/c); Perl liet ze weg.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import psycopg

from . import _db, support
from .amounts import _plain, format_amount, round_amount, to_decimal
from .arap import format_dcn
from .config import UserConfig
from .dates import format_date
from .invoice import InvoiceLedger
from .num2text import document_language, language_for, num2text

ZERO = Decimal(0)

#: lijsten per regel die Perl voor elke regel vult (IS::invoice_details)
LINE_LISTS = ("runningnumber", "number", "sku", "serialnumber", "ordernumber", "customerponumber", "bin",
              "description", "unit", "deliverydate", "projectnumber", "sell", "sellprice", "listprice",
              "netprice", "discount", "discountrate", "linetotal", "linetax", "itemnotes", "lineitemdetail",
              "package", "netweight", "grossweight", "volume", "countryorigin", "hscode", "barcode", "lot",
              "expires", "make", "model", "qty", "ship", "part", "service", "taxrates", "bom")
#: orders (OE::order_details): leverdatum per regel heet ``requiredate``, plus tekening/gereedschap
ORDER_LINE_LISTS = tuple(k for k in LINE_LISTS if k != "deliverydate") + ("requiredate", "drawing", "toolnumber",
                                                                       "toship")   # toship: niet in Perl (DS4)
TAX_LISTS = ("taxdescription", "taxnumber", "taxrate", "taxbaseinclusive", "taxbase", "tax")
#: lijsten per boekingsregel van een verkoop/inkoop zonder artikelen (arapprn.pl print_transaction)
TRANSACTION_LISTS = ("accno", "account", "description", "projectnumber", "amount")
PAYMENT_LISTS = ("payment", "paymentaccount", "paymentdate", "paymentsource", "paymentmemo", "paymentmethod")

#: documentsoort -> (label zoals Perl, nummerveld in defaults)
FORMS = {
    "invoice": ("Invoice", "sinumber"),
    "credit_invoice": ("Credit Invoice", "sinumber"),
    "vendor_invoice": ("Invoice", "vinumber"),
    "debit_invoice": ("Debit Invoice", "sinumber"),
    "packing_list": ("Packing List", "sinumber"),
    "pick_list": ("Pick List", "sinumber"),
    "bin_list": ("Bin List", "sinumber"),
}

#: documentsoorten uit een order/offerte -> (label zoals Perl, datumvoorvoegsel)
ORDER_FORMS = {
    "sales_order": ("Sales Order", "ord"),
    "purchase_order": ("Purchase Order", "ord"),
    "sales_quotation": ("Quotation", "quo"),
    "request_quotation": ("Quotation", "quo"),        # Perl: ook "Quotation"
    "packing_list": ("Packing List", "ord"),
    "pick_list": ("Pick List", "ord"),
}


def _d(v: Any) -> Decimal:
    return to_decimal(v)


def _decimals(v: Decimal) -> int:
    s = _plain(v)
    return len(s.split(".", 1)[1]) if "." in s else 0


def _str(v: Any) -> str:
    return "" if v is None else str(v)


class PrintData:
    """Sjabloonvariabelen voor documenten van één administratie."""

    def __init__(self, conn: psycopg.Connection, config: UserConfig, *, username: str = "",
                 useremail: str = "", doc_language: Optional[str] = None):
        self.conn = conn
        self.config = config
        self.username = username
        self.useremail = useremail
        #: documenttaal van de administratie (webapp, DS10/DT2); zonder: taal zoals Perl (taalcode/landcode)
        self.doc_language = doc_language

    def text_language(self, language_code: Any) -> str:
        """Taal van het bedrag in woorden: in de webapp die van het document (variant of administratie),
        anders zoals Perl (CP->new: taalcode van het document, anders de landcode van de gebruiker)."""
        if self.doc_language:
            return document_language(_str(language_code), self.doc_language)
        return language_for(_str(language_code), self.config.countrycode)

    # ------------------------------------------------------------ opmaak

    def amt(self, v: Any, places: Any = None, dash: str = "") -> str:
        return format_amount(_d(v), self.config.numberformat, places, dash)

    def date(self, v: Any) -> str:
        if v is None or v == "":
            return ""
        if isinstance(v, date):
            return format_date(v, self.config.dateformat)
        return str(v)

    # ------------------------------------------------------------ bedrijf en relatie

    def company_details(self, cur, vc: str, vc_id: int, employee_id: Optional[int]) -> Dict[str, Any]:
        """AA::company_details: relatie, eerste contactpersoon, medewerker, bedrijfsgegevens."""
        v: Dict[str, Any] = {}
        cur.execute(f"""SELECT ct.{vc}number, ct.name, ad.address1, ad.address2, ad.city, ad.state, ad.zipcode,
                              ad.country, ct.contact, ct.phone, ct.fax, ct.taxnumber, ct.sic_code,
                              bk.iban, bk.bic, bk.membernumber, bk.clearingnumber, ct.startdate, ct.enddate,
                              ct.threshold, pm.description AS payment_method, ct.email, ct.cc, ct.bcc
                         FROM {vc} ct
                         JOIN address ad ON (ad.trans_id = ct.id)
                         LEFT JOIN paymentmethod pm ON (pm.id = ct.paymentmethod_id)
                         LEFT JOIN bank bk ON (bk.id = ct.id)
                         WHERE ct.id = %s""", (vc_id,))
        r = cur.fetchone() or {}
        v[f"{vc}number"] = _str(r.get(f"{vc}number"))
        v[vc] = _str(r.get("name"))
        for k in ("name", "address1", "address2", "city", "state", "zipcode", "country"):
            v[k] = _str(r.get(k))
        v[f"{vc}phone"] = _str(r.get("phone"))
        v[f"{vc}fax"] = _str(r.get("fax"))
        v[f"{vc}taxnumber"] = _str(r.get("taxnumber"))
        v["sic"] = _str(r.get("sic_code"))
        v[f"{vc}iban"] = _str(r.get("iban"))
        v[f"{vc}bic"] = _str(r.get("bic"))
        v[f"{vc}bankmembernumber"] = _str(r.get("membernumber"))
        v[f"{vc}bankclearingnumber"] = _str(r.get("clearingnumber"))
        v["startdate"] = self.date(r.get("startdate"))
        v["enddate"] = self.date(r.get("enddate"))
        v["threshold"] = _plain(_d(r["threshold"])) if r.get("threshold") is not None else ""
        v["payment_method"] = _str(r.get("payment_method"))
        for k in ("email", "cc", "bcc"):
            v[k] = _str(r.get(k))
        cur.execute("SELECT * FROM contact WHERE trans_id = %s ORDER BY id LIMIT 1", (vc_id,))
        c = cur.fetchone() or {}
        for k in ("firstname", "lastname", "salutation", "contacttitle", "occupation", "typeofcontact", "gender"):
            v[k] = _str(c.get(k))
        v["contact"] = f"{v['firstname']} {v['lastname']}"
        for k in ("phone", "fax", "mobile"):
            v[f"{vc}{k}"] = v.get(f"{vc}{k}") or _str(c.get(k))
        v["workphone"] = v["workfax"] = v["workmobile"] = ""
        if employee_id:
            cur.execute("SELECT workphone, workfax, workmobile FROM employee WHERE id = %s", (employee_id,))
            e = cur.fetchone() or {}
            for k in ("workphone", "workfax", "workmobile"):
                v[k] = _str(e.get(k))
        d = _db.get_defaults(cur, "weightunit", "cdt", "company", "companyemail", "companywebsite", "address",
                             "tel", "fax", "businessnumber", "annualinterest", "latepaymentfee",
                             "restockingcharge")
        for k in ("weightunit", "cdt", "company", "companyemail", "companywebsite", "address", "tel", "fax",
                  "businessnumber", "annualinterest", "latepaymentfee", "restockingcharge"):
            v[k] = _str(d.get(k))
        return v

    @staticmethod
    def fdld(v: Dict[str, Any], transdate: date, dateformat: str) -> None:
        """Form::fdld: eerste/laatste dag van de maand en het jaar, ±11 maanden en ±3 jaar."""
        import calendar

        def ld(y: int, m: int) -> date:
            return date(y, m, calendar.monthrange(y, m)[1])
        y, m = transdate.year, transdate.month
        v["fdm"] = format_date(date(y, m, 1), dateformat)
        v["ldm"] = format_date(ld(y, m), dateformat)
        v["yy"] = f"{y % 100:02d}"
        v["yyyy"], v["mm"], v["dd"] = f"{y:04d}", f"{m:02d}", f"{transdate.day:02d}"
        for n in range(1, 12):
            m1, y1 = m + n, y
            if m1 > 12:
                m1, y1 = m1 - 12, y1 + 1
            m2, y2 = m - n, y
            if m2 < 1:
                m2, y2 = m2 + 12, y2 - 1
            v[f"fdm+{n}"] = format_date(date(y1, m1, 1), dateformat)
            v[f"ldm+{n}"] = format_date(ld(y1, m1), dateformat)
            v[f"fdm-{n}"] = format_date(date(y2, m2, 1), dateformat)
            v[f"ldm-{n}"] = format_date(ld(y2, m2), dateformat)
        v["fdy"] = format_date(date(y, 1, 1), dateformat)
        v["ldy"] = format_date(date(y, 12, 31), dateformat)
        for n in range(1, 4):
            v[f"fdy+{n}"] = format_date(date(y + n, 1, 1), dateformat)
            v[f"ldy+{n}"] = format_date(date(y + n, 12, 31), dateformat)
            v[f"fdy-{n}"] = format_date(date(y - n, 1, 1), dateformat)
            v[f"ldy-{n}"] = format_date(date(y - n, 12, 31), dateformat)

    # ------------------------------------------------------------ facturen

    def invoice_vars(self, trans_id: int, vc: str, formname: Optional[str] = None, *,
                     language_code: Optional[str] = None, sortby: str = "runningnumber",
                     groupprojectnumber: bool = False, grouppartsgroup: bool = False) -> Dict[str, Any]:
        """Sjabloonvariabelen van een opgeslagen factuur (io.pl print_form + IS/IR invoice_details)."""
        il = InvoiceLedger(self.conn, self.config)
        inv = il.get_invoice(trans_id, vc)
        ar = vc == "customer"
        formname = formname or (inv.type if ar else ("debit_invoice" if inv.credit else "vendor_invoice"))
        with _db.cursor(self.conn) as cur:
            precision = _db.get_precision(cur)
            ids = [l.id for l in inv.lines if l.id]
            extra = self._line_extra(cur, "invoice", ids)
            parts = il.parts_info(cur, [l.parts_id for l in inv.lines])
            rates = il.tax_rates(inv)
            taxinfo = self._taxinfo(cur, rates)
            emp_name = None
            if inv.employee_id:
                cur.execute("SELECT name FROM employee WHERE id = %s", (inv.employee_id,))
                e = cur.fetchone()
                emp_name = e["name"] if e else None
            pm_names: Dict[int, str] = {}
            cur.execute("SELECT id, description FROM paymentmethod")
            for r in cur.fetchall():
                pm_names[r["id"]] = r["description"]
            cur.execute("SELECT accno, description FROM chart")
            chart_desc = {r["accno"]: r["description"] for r in cur.fetchall()}
            v = self.company_details(cur, vc, inv.vc_id, inv.employee_id)
            v["exchangerate"] = self._exchangerate(cur, inv.currency, inv.exchangerate)
            dflt = _db.get_defaults(cur, "roundchange")
            # betaalrekening voor het kenmerk: rekening van de laatste betaling, anders die van de factuur
            pay_accno = inv.payments[-1].accno if inv.payments else inv.bank_accno
            bank = {}
            if pay_accno:
                cur.execute("""SELECT bk.iban, bk.bic, bk.membernumber, bk.dcn, bk.rvc FROM bank bk
                               JOIN chart c ON (c.id = bk.id) WHERE c.accno = %s""", (pay_accno,))
                bank = cur.fetchone() or {}

        label, _numberfld = FORMS.get(formname, ("Invoice", "sinumber"))
        inv_lang = inv.language_code if language_code is None else language_code
        v.update({
            "formname": formname, "label": label, "vc": vc,
            "invnumber": _str(inv.invnumber), "ordnumber": _str(inv.ordnumber), "quonumber": _str(inv.quonumber),
            "ponumber": _str(inv.ponumber), "transdate": self.date(inv.transdate), "invdate": self.date(inv.transdate),
            "duedate": self.date(inv.duedate or inv.transdate),
            "invdescription": _str(inv.description), "notes": _str(inv.notes).lstrip(),
            "intnotes": _str(inv.intnotes).lstrip(), "currency": _str(inv.currency),
            "shippingpoint": _str(inv.shippingpoint), "shipvia": _str(inv.shipvia), "waybill": _str(inv.waybill),
            "employee": _str(emp_name), "language_code": _str(inv_lang), "username": self.username,
            "useremail": self.useremail, "precision": precision, "taxincluded": "1" if inv.taxincluded else "",
            "cashdiscount": self.amt(inv.cashdiscount), "discountterms": _str(inv.discountterms or ""),
        })
        duedate = inv.duedate or inv.transdate
        days = (duedate - inv.transdate).days if isinstance(duedate, date) else 0
        v["terms"] = str(days if ar else -days)
        lang = self.text_language(inv_lang)

        # ---- regels (IS/IR invoice_details)
        r = self._line_lists("invoice", vc, inv.lines, rates, inv.taxincluded, precision, lang,
                            sortby=sortby, groupprojectnumber=groupprojectnumber,
                            grouppartsgroup=grouppartsgroup, parts=parts, extra=extra)
        lists, taxacc, taxbase, lineitems = r["lists"], r["taxacc"], r["taxbase"], r["lineitems"]
        total, totals = r["total"], r["totals"]
        v["packages"], v["text_packages"] = r["packages"], r["text_packages"]
        v.update(lists)

        # ---- btw
        tax = self._round_taxes(v, taxacc, lineitems, precision)
        if inv.taxincluded:
            invtotal = total
            sub = total - tax
        else:
            sub = total
            invtotal = total + tax
        # contantkorting (cd_*): alleen als er een korting betaald is of beschikbaar
        cdt_on = bool(v.get("cdt"))
        discount_paid = _d(inv.discount.amount) if inv.discount else ZERO   # al met het teken van het scherm
        if discount_paid and cdt_on and sub:
            # het scherm rekent het totaal met de btw na korting (form_footer, invtotal vóór aftrek korting)
            share = discount_paid / sub
            screen_tax = sum((round_amount((taxbase[a] - (taxbase[a] * share if not inv.taxincluded else ZERO))
                                           * rates.get(a, ZERO), precision) for a in taxacc), ZERO)
            cd_available = (sub + screen_tax) * _d(inv.cashdiscount) / 100
        else:
            cd_available = invtotal * _d(inv.cashdiscount) / 100
        cdt = discount_paid or cd_available
        cd_sub = sub - cdt
        cashdisc = cdt / sub if sub else ZERO
        cd_tax = ZERO
        for k in TAX_LISTS + ("cd_taxbase", "cd_tax"):
            v[k] = []
        for a in sorted(taxacc):
            r = rates.get(a, ZERO)
            amount = taxbase[a] * cashdisc if (cdt_on and not inv.taxincluded) else ZERO
            inclusive = taxbase[a] + taxacc[a]
            if cdt:
                cdtb = taxbase[a] - amount
                cdt_a = round_amount(cdtb * r, precision)
                cd_tax += cdt_a
                v["cd_taxbase"].append(self.amt(cdtb, precision))
                v["cd_tax"].append(self.amt(cdt_a, precision))
                v[f"cd_{a}_taxbase"] = self.amt(cdtb, precision)
                v[f"cd_{a}_tax"] = self.amt(cdt_a, precision, "0")
                v[f"cd_{a}_taxbaseinclusive"] = self.amt(inclusive - amount, precision)
                if cdt_on and discount_paid:
                    inclusive -= amount
                    taxbase[a] -= amount
                    taxacc[a] = cdt_a
            info = taxinfo.get(a, {})
            v["taxdescription"].append(_str(info.get("description")))
            v["taxnumber"].append(_str(info.get("taxnumber")))
            v["taxrate"].append(self.amt(r * 100, None, "0"))
            v["taxbaseinclusive"].append(self.amt(inclusive, precision))
            v["taxbase"].append(self.amt(taxbase[a], precision))
            v["tax"].append(self.amt(taxacc[a], precision, "0"))
            v[f"{a}_description"] = _str(info.get("description"))
            v[f"{a}_taxnumber"] = _str(info.get("taxnumber"))
            v[f"{a}_taxbaseinclusive"] = self.amt(inclusive, precision)
            v[f"{a}_taxbase"] = self.amt(taxbase[a], precision)
            v[f"{a}_tax"] = self.amt(taxacc[a], precision, "0")
            v[f"{a}_taxrate"] = self.amt(r * 100, None, "0")

        # ---- betalingen
        for k in PAYMENT_LISTS:
            v[k] = []
        paid = ZERO
        for pmt in inv.payments:
            amt = _d(pmt.amount)
            if not amt:
                continue
            v["payment"].append(self.amt(amt, precision))
            v["paymentaccount"].append(_str(chart_desc.get(pmt.accno)))
            v["paymentdate"].append(self.date(pmt.datepaid))
            v["paymentsource"].append(_str(pmt.source))
            v["paymentmemo"].append(_str(pmt.memo))
            v["paymentmethod"].append(_str(pm_names.get(pmt.paymentmethod_id)) if pmt.paymentmethod_id else "")
            paid += round_amount(amt, precision)
        v["payment_accno"] = _str(pay_accno)

        if cdt_on and discount_paid:
            invtotal = cd_sub + cd_tax
            tax = cd_tax
        cd_invtotal = cd_sub + cd_tax
        if not cdt:
            cd_available = ZERO
            cd_sub = ZERO
            cd_invtotal = ZERO
        v["totaltax"] = self.amt(tax, precision)
        whole, dec = self._split(invtotal, precision)
        v["decimal"] = dec
        v["text_decimal"] = num2text(int(dec), lang)
        v["text_amount"] = num2text(whole, lang)
        v["integer_amount"] = str(whole)

        roundto = self._roundto(dflt.get("roundchange"), inv.payments)
        if roundto > Decimal("0.01"):
            out = round_amount(round_amount((invtotal - paid) / roundto, 0) * roundto, precision)
            v["roundingdifference"] = self.amt(round_amount(paid + out - invtotal, precision), precision)
        else:
            out = invtotal - paid
        v["roundto"] = _plain(roundto) if roundto else ""
        whole, dec = self._split(out, precision)
        v["out_decimal"] = dec
        v["text_out_decimal"] = num2text(int(dec), lang)
        v["text_out_amount"] = num2text(whole, lang)
        v["integer_out_amount"] = str(whole)
        if cdt:
            whole, dec = self._split(cd_invtotal, precision)
            v["cd_decimal"] = dec
            v["text_cd_decimal"] = num2text(int(dec), lang)
            v["text_cd_invtotal"] = num2text(whole, lang)
            v["integer_cd_invtotal"] = str(whole)
        v["cd_amount"] = self.amt(cdt, precision) if cdt else ""
        v["cd_available"] = self.amt(cd_available, precision, "0")
        v["paid"] = self.amt(paid, precision)
        v["discount_paid"] = self.amt(discount_paid, precision) if discount_paid else ""
        for k, val in (("cd_subtotal", cd_sub), ("cd_invtotal", cd_invtotal), ("invtotal", invtotal),
                       ("subtotal", sub), ("total", out), ("totalparts", totals["parts"]),
                       ("totalservices", totals["services"])):
            v[k] = self.amt(val, precision, "0")
        for k in ("qty", "ship", "netweight", "grossweight"):
            v[f"total{k}"] = self.amt(totals[k])

        # ---- kenmerk en bank (alleen tonen; niet opslaan)
        v["iban"] = _str(bank.get("iban"))
        v["bic"] = _str(bank.get("bic"))
        v["membernumber"] = _str(bank.get("membernumber"))
        fields = {**v, "invnumber": inv.invnumber, "customernumber": v.get("customernumber")}
        v["dcn"] = _str(format_dcn(bank.get("dcn"), fields)) if bank.get("dcn") else _str(inv.dcn)
        v["rvc"] = _str(format_dcn(bank.get("rvc"), fields)) if bank.get("rvc") else ""

        self.fdld(v, inv.transdate, self.config.dateformat)
        self._shipto(v, inv.shipto, formname, vc, inv.type)

        # <%veld%> in omschrijving en notities vervangen (io.pl r1802)
        for k in ("invdescription", "notes", "intnotes"):
            v[k] = self._fill(v, v[k])
        return v

    # ------------------------------------------------------------ orders en offertes

    def order_vars(self, oe_id: int, formname: Optional[str] = None, *, language_code: Optional[str] = None,
                   sortby: str = "runningnumber", groupprojectnumber: bool = False,
                   grouppartsgroup: bool = False) -> Dict[str, Any]:
        """Sjabloonvariabelen van een opgeslagen order/offerte (io.pl print_form + OE::order_details).

        ``formname``: sales_order, purchase_order, sales_quotation, request_quotation, packing_list of
        pick_list (standaard: de soort van de order)."""
        from .order import OrderLedger
        ol = OrderLedger(self.conn, self.config)
        o = ol.get_order(oe_id)
        vc = o.vc
        formname = formname or o.type
        with _db.cursor(self.conn) as cur:
            precision = _db.get_precision(cur)
            extra = self._line_extra(cur, "orderitems", [l.id for l in o.lines if l.id])
            parts = InvoiceLedger(self.conn, self.config).parts_info(cur, [l.parts_id for l in o.lines])
            rates = ol.tax_rates(o)
            taxinfo = self._taxinfo(cur, rates)
            emp_name = None
            if o.employee_id:
                cur.execute("SELECT name FROM employee WHERE id = %s", (o.employee_id,))
                e = cur.fetchone()
                emp_name = e["name"] if e else None
            v = self.company_details(cur, vc, o.vc_id, o.employee_id)
            v["exchangerate"] = self._exchangerate(cur, o.currency, o.exchangerate)

        label = ORDER_FORMS.get(formname, ("Sales Order", "ord"))[0]
        prefix = "quo" if o.quotation else "ord"     # print_form: ${inv}date
        lang_code = o.language_code if language_code is None else language_code
        v.update({
            "formname": formname, "label": label, "vc": vc,
            "ordnumber": _str(o.ordnumber), "quonumber": _str(o.quonumber), "ponumber": _str(o.ponumber),
            "transdate": self.date(o.transdate), f"{prefix}date": self.date(o.transdate),
            "reqdate": self.date(o.reqdate),
            "orddescription": _str(o.description), "quodescription": _str(o.description),
            "notes": _str(o.notes).lstrip(), "intnotes": _str(o.intnotes).lstrip(), "currency": _str(o.currency),
            "shippingpoint": _str(o.shippingpoint), "shipvia": _str(o.shipvia), "waybill": _str(o.waybill),
            "employee": _str(emp_name), "language_code": _str(lang_code), "username": self.username,
            "useremail": self.useremail, "precision": precision, "taxincluded": "1" if o.taxincluded else "",
            "terms": str(o.terms) if o.terms else "",       # Perl: leeg bij 0 dagen
        })
        lang = self.text_language(lang_code)

        r = self._line_lists("order", vc, o.lines, rates, o.taxincluded, precision, lang, sortby=sortby,
                             groupprojectnumber=groupprojectnumber, grouppartsgroup=grouppartsgroup,
                             parts=parts, extra=extra)
        taxacc, taxbase, lineitems, totals = r["taxacc"], r["taxbase"], r["lineitems"], r["totals"]
        v["packages"], v["text_packages"] = r["packages"], r["text_packages"]
        v.update(r["lists"])

        tax = self._round_taxes(v, taxacc, lineitems, precision, zero="")
        ordtotal = r["total"]
        sub = ordtotal - tax if o.taxincluded else ordtotal
        if not o.taxincluded:
            ordtotal += tax
        self._tax_lists(v, taxacc, taxbase, rates, taxinfo, precision)
        v["totaltax"] = self.amt(tax, precision)
        for k in PAYMENT_LISTS:
            v[k] = []
        v["paid"] = ""
        v["total"] = self.amt(ordtotal, precision, "0")
        v["subtotal"] = self.amt(sub, precision, "0")
        v["ordtotal"] = v["quototal"] = self.amt(ordtotal, precision, "0")
        whole, dec = self._split(ordtotal, precision)
        v["decimal"] = dec
        v["text_decimal"] = num2text(int(dec), lang)
        v["text_amount"] = num2text(whole, lang)
        v["integer_amount"] = str(whole)
        for k in ("parts", "services"):
            v[f"total{k}"] = self.amt(totals[k], precision)
        for k in ("qty", "ship", "netweight", "grossweight"):
            v[f"total{k}"] = self.amt(totals[k])

        self.fdld(v, o.transdate, self.config.dateformat)
        self._shipto(v, o.shipto, formname, vc, o.type)
        for k in ("orddescription", "quodescription", "notes", "intnotes"):
            v[k] = self._fill(v, v[k])
        return v

    def _exchangerate(self, cur, currency: Optional[str], rate: Any) -> str:
        """Koers zoals op het scherm: alleen bij een andere valuta dan de standaardvaluta."""
        if not currency or currency == _db.default_currency(cur):
            return ""
        return self.amt(rate) if rate else ""

    def _roundto(self, default: Any, payments: List[Any]) -> Decimal:
        """Afronden van het te betalen bedrag (IS::invoice_details/print_transaction): ``defaults.roundchange``,
        of als er betaalmethoden zijn die van de betaalmethode van de laatste betaling (zonder eigen
        afronding: niet afronden; zonder betaalmethode: de standaard)."""
        roundto = _d(default)
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT id, roundchange FROM paymentmethod")
            methods = {r["id"]: r["roundchange"] for r in cur.fetchall()}
        if methods:
            for pmt in payments:
                if _d(pmt.amount):
                    pm = pmt.paymentmethod_id
                    roundto = _d(methods.get(pm)) if pm else _d(default)
        return roundto

    @staticmethod
    def _fill(v: Dict[str, Any], text: str) -> str:
        """<%veld%> in omschrijving en notities vervangen (io.pl r1802)."""
        return re.sub(r"<%(.*?)%>", lambda m_: _str(v.get(m_.group(1).lower())) if not isinstance(
            v.get(m_.group(1).lower()), list) else "", text)

    @staticmethod
    def _taxinfo(cur, rates: Dict[str, Decimal]) -> Dict[str, Dict[str, Any]]:
        """Omschrijving en btw-nummer per btw-rekening, van de tariefregel die gebruikt is."""
        cur.execute("""SELECT c.accno, c.description, t.taxnumber, t.rate FROM chart c
                       LEFT JOIN tax t ON (t.chart_id = c.id) WHERE c.accno = ANY(%s)
                       ORDER BY c.accno, t.validto NULLS LAST""", (list(rates),))
        info: Dict[str, Dict[str, Any]] = {}
        for r in cur.fetchall():
            a = r["accno"]
            if a not in info or (r["rate"] is not None and _d(r["rate"]) == _d(rates.get(a))
                                 and _d(info[a]["rate"]) != _d(rates.get(a))):
                info[a] = r
        return info

    def _tax_lists(self, v: Dict[str, Any], taxacc: Dict[str, Decimal], taxbase: Dict[str, Decimal],
                   rates: Dict[str, Decimal], taxinfo: Dict[str, Dict[str, Any]], precision: int) -> None:
        """Btw per rekening als lijsten en losse velden (zonder contantkorting; OE::order_details)."""
        for k in TAX_LISTS:
            v[k] = []
        for a in sorted(taxacc):
            r = rates.get(a, ZERO)
            inclusive = taxbase[a] + taxacc[a]
            info = taxinfo.get(a, {})
            v["taxdescription"].append(_str(info.get("description")))
            v["taxnumber"].append(_str(info.get("taxnumber")))
            v["taxrate"].append(self.amt(r * 100, None, "0"))
            v["taxbaseinclusive"].append(self.amt(inclusive, precision))
            v["taxbase"].append(self.amt(taxbase[a], precision))
            v["tax"].append(self.amt(taxacc[a], precision, "0"))
            v[f"{a}_description"] = _str(info.get("description"))
            v[f"{a}_taxnumber"] = _str(info.get("taxnumber"))
            v[f"{a}_taxbaseinclusive"] = self.amt(inclusive, precision)
            v[f"{a}_taxbase"] = self.amt(taxbase[a], precision)
            v[f"{a}_tax"] = self.amt(taxacc[a], precision, "0")
            v[f"{a}_taxrate"] = self.amt(r * 100, None, "0")

    # ------------------------------------------------------------ verkoop/inkoop zonder artikelen

    def transaction_vars(self, trans_id: int, vc: str, formname: Optional[str] = None, *,
                         language_code: Optional[str] = None) -> Dict[str, Any]:
        """Sjabloonvariabelen van een verkoop-/inkoopboeking zonder artikelen (arapprn.pl
        print_transaction): regels per rekening, btw per rekening, betalingen, bedrag in woorden.

        ``formname``: transaction (bestand ``ar_transaction``/``ap_transaction``), credit_note of
        debit_note (standaard: de soort van de boeking)."""
        from .arap import ArapLedger
        al = ArapLedger(self.conn, self.config)
        t = al.get_transaction(trans_id, vc)
        formname = formname or t.type
        with _db.cursor(self.conn) as cur:
            precision = _db.get_precision(cur)
            taxinfo = self._taxinfo(cur, {a: t.taxrates.get(a, ZERO) for a in set(t.taxes) | set(t.taxrates)})
            cur.execute("SELECT accno, description FROM chart")
            chart_desc = {r["accno"]: r["description"] for r in cur.fetchall()}
            projects: Dict[int, str] = {}
            pids = sorted({l.project_id for l in t.lines if l.project_id})
            if pids:
                cur.execute("SELECT id, projectnumber FROM project WHERE id = ANY(%s)", (pids,))
                projects = {r["id"]: r["projectnumber"] for r in cur.fetchall()}
            cur.execute("SELECT id, description FROM paymentmethod")
            pm_names = {r["id"]: r["description"] for r in cur.fetchall()}
            emp_name = None
            if t.employee_id:
                cur.execute("SELECT name FROM employee WHERE id = %s", (t.employee_id,))
                e = cur.fetchone()
                emp_name = e["name"] if e else None
            v = self.company_details(cur, vc, t.vc_id, t.employee_id)
            v["exchangerate"] = self._exchangerate(cur, t.currency, t.exchangerate)
            dflt = _db.get_defaults(cur, "roundchange")
            cur.execute(f"SELECT intnotes FROM {'ar' if vc == 'customer' else 'ap'} WHERE id = %s", (trans_id,))
            intnotes = (cur.fetchone() or {}).get("intnotes")
            pay_accno = t.payments[-1].accno if t.payments else t.bank_accno
            bank = {}
            if pay_accno and vc == "customer":
                cur.execute("""SELECT bk.dcn, bk.rvc FROM bank bk JOIN chart c ON (c.id = bk.id)
                               WHERE c.accno = %s""", (pay_accno,))
                bank = cur.fetchone() or {}
        rates = dict(t.taxrates)
        lang_code = t.language_code if language_code is None else language_code
        lang = self.text_language(lang_code)
        label = {"transaction": "Transaction", "credit_note": "Credit Note", "debit_note": "Debit Note"}.get(
            formname, "Transaction")
        v.update({
            "formname": formname, "label": label, "vc": vc,
            "invnumber": _str(t.invnumber), "ordnumber": _str(t.ordnumber), "ponumber": _str(t.ponumber),
            "transdate": self.date(t.transdate), "invdate": self.date(t.transdate),
            "duedate": self.date(t.duedate or t.transdate), "invdescription": _str(t.description),
            "notes": _str(t.notes), "intnotes": _str(intnotes), "currency": _str(t.currency),
            "employee": _str(emp_name), "language_code": _str(lang_code), "username": self.username,
            "useremail": self.useremail, "precision": precision, "taxincluded": "1" if t.taxincluded else "",
            "cashdiscount": self.amt(t.cashdiscount), "discountterms": _str(t.discountterms or ""),
            "terms": str(t.terms) if t.terms else "",
        })

        # ---- regels
        for k in TRANSACTION_LISTS:
            v[k] = []
        subtotal = ZERO
        for l in t.lines:
            v["accno"].append(_str(l.accno))
            v["account"].append(_str(chart_desc.get(l.accno)))
            v["description"].append(_str(l.description))
            v["projectnumber"].append(_str(projects.get(l.project_id)))
            v["amount"].append(self.amt(l.amount, precision))
            subtotal += _d(l.amount)

        # ---- contantkorting en btw
        cdt_on = bool(v.get("cdt"))
        # een creditnota/debetnota heeft geen contantkorting (Perl laat het veld daar leeg)
        cashdiscount = ZERO if t.type.endswith("_note") else _d(t.cashdiscount) / 100
        cd_available = subtotal * cashdiscount
        discount_paid = _d(t.discount.amount) if t.discount else ZERO
        cdt = discount_paid or cd_available
        cd_sub = subtotal - cdt
        share = cdt / subtotal if subtotal else ZERO
        tax = cd_tax = ZERO
        for k in TAX_LISTS + ("cd_tax",):
            v.setdefault(k, [])
        for a in sorted(set(t.taxes) | set(rates)):   # Perl: alle btw-rekeningen, ook met 0,00
            amount = _d(t.taxes.get(a))
            tax += amount
            v[f"{a}_tax"] = self.amt(amount, precision, "0")
            v["tax"].append(self.amt(amount, precision, "0"))
            if cdt_on:
                c = amount if discount_paid else amount * (1 - share)
                cd_tax += round_amount(c, precision)
                v["cd_tax"].append(self.amt(c, precision))
            else:
                v["cd_tax"].append(self.amt(amount, precision, "0"))
            info = taxinfo.get(a, {})
            v["taxdescription"].append(_str(info.get("description")))
            v[f"{a}_description"] = _str(info.get("description"))
            v[f"{a}_taxrate"] = self.amt(_d(rates.get(a)) * 100, None, "0")
            v["taxrate"].append(v[f"{a}_taxrate"])
            v["taxnumber"].append(_str(info.get("taxnumber")))
            v[f"{a}_taxnumber"] = _str(info.get("taxnumber"))

        # ---- betalingen
        for k in PAYMENT_LISTS:
            v[k] = []
        paid = ZERO
        for pmt in t.payments:
            amt = _d(pmt.amount)
            if not amt:
                continue
            paid += amt
            v["payment"].append(self.amt(amt, precision))
            v["paymentdate"].append(self.date(pmt.datepaid))
            v["paymentaccount"].append(_str(chart_desc.get(pmt.accno)))
            v["paymentsource"].append(_str(pmt.source))
            v["paymentmemo"].append(_str(pmt.memo))
            v["paymentmethod"].append(_str(pm_names.get(pmt.paymentmethod_id)) if pmt.paymentmethod_id else "")

        if t.taxincluded:
            tax = cd_tax = ZERO
        invtotal = subtotal + tax
        cd_invtotal = cd_sub + cd_tax
        whole, dec = self._split(invtotal, precision)
        v["decimal"] = dec
        v["text_decimal"] = num2text(int(dec), lang)
        v["text_amount"] = num2text(whole, lang)
        v["integer_amount"] = str(whole)
        roundto = self._roundto(dflt.get("roundchange"), t.payments)
        if roundto > Decimal("0.01"):
            out = round_amount(round_amount((invtotal - paid) / roundto, 0) * roundto, precision)
            v["roundingdifference"] = self.amt(round_amount(paid + out - invtotal, precision), precision)
        else:
            out = invtotal - paid
        v["roundto"] = _plain(roundto) if roundto else ""
        whole, dec = self._split(out, precision)
        v["out_decimal"] = dec
        v["text_out_decimal"] = num2text(int(dec), lang)
        v["text_out_amount"] = num2text(whole, lang)
        v["integer_out_amount"] = str(whole)
        for k, val in (("cd_subtotal", cd_sub), ("cd_amount", cdt), ("cd_invtotal", cd_invtotal),
                       ("invtotal", invtotal), ("subtotal", subtotal), ("paid", paid), ("total", out)):
            v[k] = self.amt(val, precision)
        v["cd_available"] = self.amt(cd_available, precision, "0")
        v["discount_paid"] = self.amt(discount_paid, precision) if discount_paid else ""
        v["amountdue"] = self.amt(out, precision, "0")        # te betalen, ook bij 0 opgemaakt (niet in Perl)
        # kenmerk zoals Perl bij klanten: sjabloon van de betaalrekening, anders het opgeslagen kenmerk
        fields = {**v, "invnumber": t.invnumber}
        v["dcn"] = _str(format_dcn(bank.get("dcn"), fields)) if bank.get("dcn") else _str(t.dcn)
        v["rvc"] = _str(format_dcn(bank.get("rvc"), fields)) if bank.get("rvc") else ""
        self.fdld(v, t.transdate, self.config.dateformat)
        for k in ("invdescription", "notes", "intnotes"):
            v[k] = self._fill(v, v[k])
        return v

    # ------------------------------------------------------------ regels (gedeeld door facturen en orders)

    @staticmethod
    def _line_extra(cur, table: str, ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """Artikel-, project- en vrachtgegevens per regel (``invoice`` of ``orderitems``)."""
        if table not in ("invoice", "orderitems"):
            raise ValueError("ongeldige tabel")
        cur.execute(f"""SELECT i.id, i.qty::numeric AS qty, p.partnumber, p.bin, p.listprice::numeric AS listprice,
                               p.weight::numeric AS weight, p.lot, p.expires, p.tariff_hscode AS hscode,
                               p.countryorigin, p.barcode, p.drawing, p.toolnumber, p.inventory_accno_id,
                               p.income_accno_id, p.expense_accno_id, COALESCE(p.assembly, false) AS assembly,
                               p.project_id AS jobitem, pg.partsgroup, pr.projectnumber, pr.description AS projectdescription,
                               c.package, c.netweight::numeric AS netweight,
                               c.grossweight::numeric AS grossweight, c.volume::numeric AS volume,
                               (SELECT string_agg(mm.make, E'\\n' ORDER BY mm.ctid) FROM makemodel mm
                                 WHERE mm.parts_id = p.id) AS make,
                               (SELECT string_agg(mm.model, E'\\n' ORDER BY mm.ctid) FROM makemodel mm
                                 WHERE mm.parts_id = p.id) AS model
                        FROM {table} i JOIN parts p ON (p.id = i.parts_id)
                        LEFT JOIN partsgroup pg ON (pg.id = p.partsgroup_id)
                        LEFT JOIN project pr ON (pr.id = i.project_id)
                        LEFT JOIN cargo c ON (c.id = i.id AND c.trans_id = i.trans_id)
                        WHERE i.id = ANY(%s)""", (ids,))
        return {r["id"]: r for r in cur.fetchall()}

    @staticmethod
    def _bom(cur, assembly_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        """Onderdelen met "op documenten" (``assembly.bom``) per samengesteld artikel (OE/IS assembly_details)."""
        out: Dict[int, List[Dict[str, Any]]] = {}
        if not assembly_ids:
            return out
        cur.execute("""SELECT a.aid, p.partnumber, p.description, p.unit, a.qty::numeric AS qty, p.bin, p.barcode,
                              p.drawing, p.toolnumber, p.notes AS itemnotes
                       FROM assembly a JOIN parts p ON (p.id = a.parts_id)
                       WHERE a.bom AND a.aid = ANY(%s) ORDER BY a.aid, a.id""", (sorted(set(assembly_ids)),))
        for r in cur.fetchall():
            out.setdefault(r["aid"], []).append(r)
        return out

    def _line_lists(self, kind: str, vc: str, doc_lines: List[Any], rates: Dict[str, Decimal], taxincluded: bool,
                    precision: int, lang: str, *, sortby: str, groupprojectnumber: bool, grouppartsgroup: bool,
                    parts: Dict[int, Dict[str, Any]], extra: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
        """Lijsten per regel, btw per rekening en totalen (IS/IR invoice_details, OE order_details).

        ``kind`` is "invoice" of "order". Verschillen zoals Perl: een order heeft ``ship`` (geleverd)
        en ``requiredate`` per regel, een factuur ``ship`` = aantal en ``deliverydate``; bij een order
        rekenen korting/nettoprijs ook bij inkoop met de decimalen van de prijs, en een samengesteld
        artikel telt altijd als artikel."""
        ar = vc == "customer"
        order = kind == "order"
        keys = ORDER_LINE_LISTS if order else LINE_LISTS
        rows = []
        for idx, l in enumerate(doc_lines, start=1):
            p = parts.get(int(l.parts_id)) if l.parts_id else None
            if p is None:
                continue
            rows.append((idx, l, p, extra.get(l.id, {})))
        lists: Dict[str, List[Any]] = {k: [] for k in keys}
        sortlist = []
        packages = ""
        for idx, l, p, x in rows:
            invent = "1" if (p.get("inventory_accno_id") or ((ar or order) and p.get("assembly"))) else ""
            projectnumber = ""
            group = ""
            pn_shown = ""
            if groupprojectnumber or grouppartsgroup:
                if groupprojectnumber and x.get("projectnumber"):
                    projectnumber = x["projectnumber"]
                    pn_shown = (f"{projectnumber}, {_str(x.get('projectdescription'))}" if order
                                else projectnumber)
                if grouppartsgroup and x.get("partsgroup"):
                    group = x["partsgroup"]
                    pn_shown = f"{pn_shown} / {group}" if pn_shown else group
            sortby_key = projectnumber + group
            if sortby != "runningnumber":
                sortby_key += {"partnumber": _str(p.get("partnumber")), "description": _str(l.description),
                               "bin": _str(p.get("bin"))}.get(sortby, "")
            sortlist.append((idx, projectnumber + group + invent, pn_shown, sortby_key, l, p, x))
            if x.get("package"):
                packages = x["package"]
        # laatste pakketnummer: cijfers aan het eind
        m = re.search(r"(\d+)$", packages or "")
        if m:
            packages = m.group(1)
        # bedrag in woorden: Perl rekent met de tekst als getal ("1 Chep" -> 1)
        lead = re.match(r"\s*(\d+)", packages or "")
        npack = int(packages) if m else (int(lead.group(1)) if lead else 0)

        sortlist.sort(key=lambda t: t[3])          # Perl: sort { $a->[5] cmp $b->[5] } (stabiel)
        with _db.cursor(self.conn) as cur:
            bom = self._bom(cur, [int(l.parts_id) for _i, _k, _s, _sk, l, p, _x in sortlist if p.get("assembly")])
        taxacc: Dict[str, Decimal] = {}
        taxbase: Dict[str, Decimal] = {}
        lineitems: List[Dict[str, Decimal]] = []
        total = ZERO
        totals = {k: ZERO for k in ("qty", "ship", "netweight", "grossweight", "parts", "services")}
        running = 1
        same = None
        subtotal = ZERO

        def blank(desc: str) -> None:
            for k in keys:
                if k in ("description", "linetotal"):
                    continue
                lists[k].append("")
            lists["description"].append(desc)

        for j, (idx, sortkey, pn_shown, _sk, l, p, x) in enumerate(sortlist, start=1):
            if (groupprojectnumber or grouppartsgroup) and sortkey != same:
                same = sortkey
                if pn_shown:
                    blank(pn_shown)
                    lists["linetotal"].append("")
                    lineitems.append({"amount": ZERO, "tax": ZERO})
            qty = _d(l.qty)
            if qty:
                ship = _d(l.ship) if order else qty
                totals["qty"] += qty
                totals["ship"] += ship
                totals["netweight"] += _d(x.get("netweight") or 0)
                totals["grossweight"] += _d(x.get("grossweight") or 0)
                lists["runningnumber"].append(running)
                running += 1
                lists["number"].append(_str(p.get("partnumber")))
                lists["projectnumber"].append(_str(x.get("projectnumber")))
                lists["sku"].append(_str(p.get("partnumber")))
                lists["serialnumber"].append(_str(l.serialnumber))
                lists["ordernumber"].append(_str(l.ordernumber))
                lists["customerponumber"].append(_str(l.ponumber))
                lists["bin"].append(_str(p.get("bin")))
                lists["description"].append(_str(l.description))
                lists["unit"].append(_str(l.unit))
                if order:
                    lists["toship"].append(self.amt(qty - ship))       # nog te leveren (pakbon, DS4)
                    lists["requiredate"].append(self.date(l.reqdate))
                    lists["drawing"].append(_str(x.get("drawing")))
                    lists["toolnumber"].append(_str(x.get("toolnumber")))
                else:
                    lists["deliverydate"].append(self.date(l.deliverydate))
                sell = _d(l.sellprice)
                dec = max(_decimals(sell), precision)
                ldec = dec if (ar or order) else precision
                # sell: prijs van de regel; bij inkooporders de verkoopprijs van het artikel (OE retrieve)
                lists["sell"].append(_plain(sell) if (ar or not order) else _plain(_d(p.get("sellprice"))))
                lists["sellprice"].append(self.amt(sell, dec, "0"))
                lists["listprice"].append(_plain(_d(x["listprice"])) if (ar or order) and x.get("listprice") is not None
                                          else "")  # IR laadt hem niet
                lists["itemnotes"].append(_str(l.itemnotes))
                lists["lineitemdetail"].append("1" if l.lineitemdetail else "")
                lists["package"].append(_str(x.get("package")))
                for k in ("netweight", "grossweight", "volume"):
                    lists[k].append(self.amt(x[k]) if x.get(k) is not None else "")
                lists["countryorigin"].append(_str(x.get("countryorigin")))
                lists["hscode"].append(_str(x.get("hscode")))
                lists["barcode"].append(_str(x.get("barcode")))
                lists["lot"].append(_str(x.get("lot")))
                lists["expires"].append(self.date(x.get("expires")))
                lists["make"].append(_str(x.get("make")))
                lists["model"].append(_str(x.get("model")))
                lists["qty"].append(self.amt(qty))
                lists["ship"].append(self.amt(ship))
                lists["bom"].append("")
                pct = _d(l.discount)
                discount = round_amount(sell * pct / 100, ldec)
                netprice = sell - discount
                linetotal = round_amount(qty * netprice, precision)
                if p.get("inventory_accno_id") or ((ar or order) and p.get("assembly")):
                    lists["part"].append(_str(p.get("partnumber")))
                    lists["service"].append("")
                    totals["parts"] += linetotal
                else:
                    lists["service"].append(_str(p.get("partnumber")))
                    lists["part"].append("")
                    totals["services"] += linetotal
                lists["netprice"].append(self.amt(netprice, ldec) if netprice else " ")
                lists["discount"].append(self.amt(-discount, ldec) if discount else " ")
                lists["discountrate"].append(self.amt(pct))
                total += linetotal
                subtotal += linetotal
                lists["linetotal"].append(self.amt(linetotal, precision, "0"))
                accs = list(p.get("taxaccounts") or [])
                taxrates = []
                tax = ZERO
                for mlt in (1, -1):
                    rate = sum((rates[a] for a in accs if a in rates and rates[a] * mlt > 0), ZERO) * mlt
                    taxamount = linetotal * rate / (1 + rate) if rate != -1 else ZERO
                    tb = linetotal - taxamount
                    for a in accs:
                        r = rates.get(a)
                        if r is not None and r * mlt > 0:
                            taxrates.append(r * 100)
                            if taxincluded:
                                taxacc[a] = taxacc.get(a, ZERO) + linetotal * r / (1 + rate)
                                taxbase[a] = taxbase.get(a, ZERO) + tb
                            else:
                                taxbase[a] = taxbase.get(a, ZERO) + linetotal
                                taxacc[a] = taxacc.get(a, ZERO) + linetotal * r
                    if taxincluded:
                        tax += linetotal * (rate / (1 + rate * mlt)) if rate else ZERO
                    else:
                        tax += linetotal * rate
                for a in accs:
                    if a in rates and rates[a] == 0:
                        taxrates.append(ZERO)
                        taxacc[a] = ZERO
                        taxbase[a] = taxbase.get(a, ZERO) + linetotal
                tax = round_amount(tax, precision)
                lineitems.append({"amount": linetotal, "tax": tax})
                lists["taxrates"].append(" ".join(_plain(t) for t in sorted(taxrates)))
                # onderdelen "op documenten" onder een samengesteld artikel (assembly_details, SA10/DS7)
                for c in bom.get(int(l.parts_id), []) if p.get("assembly") else []:
                    for k in keys:
                        lists[k].append("")
                    for k in ("number", "sku"):
                        lists[k][-1] = _str(c["partnumber"])
                    for k in ("description", "unit", "bin", "barcode", "itemnotes"):
                        lists[k][-1] = _str(c[k])
                    if order:
                        lists["drawing"][-1] = _str(c["drawing"])
                        lists["toolnumber"][-1] = _str(c["toolnumber"])
                    lists["qty"][-1] = self.amt(_d(c["qty"]) * qty)
                    lists["bom"][-1] = "1"
                    lineitems.append({"amount": ZERO, "tax": ZERO})
            if (groupprojectnumber or grouppartsgroup) and subtotal:
                nxt = sortlist[j][1] if j < len(sortlist) else None
                if j < len(sortlist) and nxt != same or j == len(sortlist):
                    blank("Subtotal")
                    lists["linetotal"].append(self.amt(subtotal, precision))
                    lineitems.append({"amount": ZERO, "tax": ZERO})
                    subtotal = ZERO
        return {"lists": lists, "taxacc": taxacc, "taxbase": taxbase, "lineitems": lineitems, "total": total,
                "totals": totals, "packages": packages, "text_packages": num2text(npack, lang)}

    def _round_taxes(self, v: Dict[str, Any], taxacc: Dict[str, Decimal], lineitems: List[Dict[str, Decimal]],
                     precision: int, zero: str = "0") -> Decimal:
        """Btw per rekening afronden; het afrondingsverschil met de regel-btw op de regel met de meeste btw
        (IS/OE: "adjust taxes for lineitems"). Geeft het btw-totaal."""
        tax = ZERO
        for a in sorted(taxacc):
            taxacc[a] = round_amount(taxacc[a], precision)
            tax += taxacc[a]
        ltotal = sum((li["tax"] for li in lineitems), ZERO)
        if round_amount(ltotal, precision) != round_amount(tax, precision) and lineitems:
            k = max(range(len(lineitems)), key=lambda n: (lineitems[n]["tax"], n))
            lineitems[k]["tax"] -= ltotal - tax
        v["linetax"] = [self.amt(li["tax"], precision, zero) for li in lineitems]
        return tax

    @staticmethod
    def _split(amount: Decimal, precision: int) -> Tuple[int, str]:
        s = _plain(round_amount(amount, max(precision, 2)))
        whole, _, dec = s.partition(".")
        return int(whole or 0), (dec + "00")[:2]

    def _shipto(self, v: Dict[str, Any], shipto: Dict[str, str], formname: str, vc: str, itype: str) -> None:
        """io.pl print_form: zonder afleveradres het adres van de relatie (of bij creditfactuur,
        inkooporder e.d. het eigen bedrijfsadres) invullen."""
        f = ("email", "name", "address1", "address2", "city", "state", "zipcode", "country", "contact",
             "phone", "fax")
        for k in f:
            v[f"shipto{k}"] = _str(shipto.get(k))
        if any(v[f"shipto{k}"] for k in f):
            return
        own = bool(re.search(r"credit_invoice|purchase_order|request_quotation|bin_list", formname)) or (
            itype == "invoice" and vc == "vendor")
        v["shiptophone"] = v.get("tel", "")
        v["shiptofax"] = v.get("fax", "")
        v["shiptocontact"] = v.get("employee", "")
        if own:
            v["shiptoname"] = v.get("company", "")
            v["shiptoaddress1"] = v.get("address", "")
        else:
            for k in f:
                v[f"shipto{k}"] = _str(v.get(k))
            for k in ("phone", "fax"):
                v[f"shipto{k}"] = _str(v.get(f"{vc}{k}"))


def mark_status(conn: psycopg.Connection, config: UserConfig, trans_id: int, formname: str, *,
                printed: bool = False, emailed: bool = False, table: str = "ar",
                reference: Optional[str] = None) -> None:
    """Form::update_status + audittrail: zet het vinkje geprint/gemaild voor dit document
    (het andere vinkje en een spoolbestand blijven staan)."""
    if not re.match(r"^[a-z_0-9]{1,40}$", formname):
        raise ValueError("ongeldige documentsoort")
    with conn.transaction(), _db.cursor(conn) as cur:
        cur.execute("SELECT printed, emailed, spoolfile FROM status WHERE trans_id = %s AND formname = %s",
                    (trans_id, formname))
        old = cur.fetchone() or {}
        cur.execute("DELETE FROM status WHERE trans_id = %s AND formname = %s", (trans_id, formname))
        cur.execute("""INSERT INTO status (trans_id, printed, emailed, spoolfile, formname)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (trans_id, bool(old.get("printed")) or printed, bool(old.get("emailed")) or emailed,
                     old.get("spoolfile"), formname))
        support.audittrail(cur, config, trans_id=trans_id, tablename=table, reference=reference,
                           formname=formname, action="emailed" if emailed else "printed")


def document_status(conn: psycopg.Connection, trans_id: int, formname: str) -> Dict[str, bool]:
    """Vinkjes geprint/gemaild van een document (``status``, zoals io.pl print_options)."""
    with _db.cursor(conn) as cur:
        cur.execute("SELECT printed, emailed FROM status WHERE trans_id = %s AND formname = %s",
                    (trans_id, formname))
        r = cur.fetchone() or {}
    return {"printed": bool(r.get("printed")), "emailed": bool(r.get("emailed"))}


def add_intnote(conn: psycopg.Connection, trans_id: int, text: str, *, table: str = "ar") -> None:
    """Form::save_intnotes na het mailen: tekst onder de interne notities van de boeking
    (met een lege regel ertussen, zoals io.pl print_form). Alleen ``intnotes`` wijzigt."""
    if table not in ("ar", "ap", "oe"):
        raise ValueError("ongeldige tabel")
    with conn.transaction(), _db.cursor(conn) as cur:
        cur.execute(f"""UPDATE {table} SET intnotes = CASE WHEN coalesce(intnotes, '') = '' THEN %(t)s
                                                         ELSE intnotes || E'\\n\\n' || %(t)s END
                        WHERE id = %(id)s""", {"t": text, "id": trans_id})
