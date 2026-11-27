"""Documentsoorten (fase 4, D2/DS1): welke documenten er zijn, waar de gegevens vandaan komen en op welk
scherm ze gemaakt kunnen worden.

De sleutel is de naam in de url (``/document/<soort>/<id>.pdf``) en van het sjabloon
(``<soort>.html``). ``formname`` is de naam in de statustabel (geprint/gemaild), zoals SQL-Ledger:
voor verkoop/inkoop zonder artikelen is dat "transaction", het sjabloon heet ``ar_transaction`` of
``ap_transaction`` (arapprn.pl).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .i18n import N_


@dataclass(frozen=True)
class DocKind:
    key: str            # url en sjabloon
    label: str          # Nederlandse naam (N_)
    source: str         # "invoice", "order" of "transaction"
    vc: str             # "customer" of "vendor"
    formname: str       # statustabel / printdata

    @property
    def table(self) -> str:
        if self.source == "order":
            return "oe"
        return "ar" if self.vc == "customer" else "ap"


_KINDS = [
    DocKind("invoice", N_("Factuur"), "invoice", "customer", "invoice"),
    DocKind("credit_invoice", N_("Creditfactuur"), "invoice", "customer", "credit_invoice"),
    DocKind("vendor_invoice", N_("Inkoopfactuur"), "invoice", "vendor", "vendor_invoice"),
    DocKind("debit_invoice", N_("Debetnota"), "invoice", "vendor", "debit_invoice"),
    DocKind("packing_list", N_("Pakbon"), "invoice", "customer", "packing_list"),
    DocKind("pick_list", N_("Picklijst"), "invoice", "customer", "pick_list"),
    DocKind("sales_order", N_("Orderbevestiging"), "order", "customer", "sales_order"),
    DocKind("purchase_order", N_("Inkooporder"), "order", "vendor", "purchase_order"),
    DocKind("sales_quotation", N_("Offerte"), "order", "customer", "sales_quotation"),
    DocKind("request_quotation", N_("Offerteaanvraag"), "order", "vendor", "request_quotation"),
    DocKind("ar_transaction", N_("Factuur"), "transaction", "customer", "transaction"),
    DocKind("credit_note", N_("Creditnota"), "transaction", "customer", "credit_note"),
    DocKind("ap_transaction", N_("Inkoopboeking"), "transaction", "vendor", "transaction"),
    DocKind("debit_note", N_("Debetnota"), "transaction", "vendor", "debit_note"),
]
KINDS: Dict[str, DocKind] = {k.key: k for k in _KINDS}

#: documenten per klant (niet per boeking; fase 4 stap 4a, SB1/SB5): aanmaning per niveau en rekeningoverzicht.
#: Gemaakt vanuit Verkoop > Aanmaningen en Verkoop > Rekeningoverzichten (``routes_dunning``).
LETTERS: Dict[str, str] = {"reminder1": N_("Herinnering"), "reminder2": N_("Tweede herinnering"),
                           "reminder3": N_("Aanmaning"), "statement": N_("Rekeningoverzicht")}

#: documenten per betaling per relatie (fase 4 stap 4b, SB7): betalingsspecificatie bij een ontvangst (klant)
#: of een betaling (leverancier), formnames zoals SQL-Ledger (cp.pl). Gemaakt vanuit ``routes_payspec``.
PAYMENT_DOCS: Dict[str, str] = {"receipt": N_("Betalingsspecificatie"), "check": N_("Betalingsspecificatie")}

#: alle sjablonen met een eigen soort (Beheer > Sjablonen, E-mail, Documentinstellingen)
TEMPLATE_KINDS: Tuple[str, ...] = tuple(KINDS) + tuple(LETTERS) + tuple(PAYMENT_DOCS)

#: pakbon en picklijst kunnen ook uit een verkooporder komen (DS1)
FROM_ORDER = {"packing_list", "pick_list"}

#: per schermsoort de documenten die er gemaakt kunnen worden, de eerste is de standaard (DS1)
SCREEN_KINDS: Dict[str, Tuple[str, ...]] = {
    "invoice": ("invoice", "packing_list", "pick_list"),
    "credit_invoice": ("credit_invoice",),
    "vendor_invoice": ("vendor_invoice",),
    "debit_invoice": ("debit_invoice",),
    "sales_order": ("sales_order", "packing_list", "pick_list"),
    "purchase_order": ("purchase_order",),
    "sales_quotation": ("sales_quotation",),
    "request_quotation": ("request_quotation",),
    "ar_transaction": ("ar_transaction",),
    "credit_note": ("credit_note",),
    "ap_transaction": ("ap_transaction",),
    "debit_note": ("debit_note",),
}

#: scherm waar een document bij hoort: (bron, relatiesoort) -> url-voorvoegsel
SCREEN_URL = {
    ("invoice", "customer"): "verkoopfactuur", ("invoice", "vendor"): "inkoopfactuur",
    ("transaction", "customer"): "verkoop", ("transaction", "vendor"): "inkoop",
    ("order", "sales_order"): "verkooporder", ("order", "purchase_order"): "inkooporder",
    ("order", "sales_quotation"): "offerte", ("order", "request_quotation"): "offerteaanvraag",
}

#: module die aan moet staan
MODULE = {("invoice", "customer"): "ar", ("invoice", "vendor"): "ap", ("transaction", "customer"): "ar",
          ("transaction", "vendor"): "ap", ("order", "sales_order"): "oe", ("order", "purchase_order"): "oe",
          ("order", "sales_quotation"): "quotations", ("order", "request_quotation"): "quotations"}


#: Documenten > Batch (fase 4 stap 4c, SB9): waar de documenten van een soort staan. Pakbon en picklijst
#: komen uit verkoopfacturen (geen creditfacturen) en verkooporders, zoals op de schermen (``SCREEN_KINDS``).
def batch_sources(key: str):
    from sqlledger.batch import Source
    inv_ar = Source("ar", invoice=True, negative=False)
    return {
        "invoice": (inv_ar,),
        "credit_invoice": (Source("ar", invoice=True, negative=True),),
        "vendor_invoice": (Source("ap", invoice=True, negative=False),),
        "debit_invoice": (Source("ap", invoice=True, negative=True),),
        "packing_list": (inv_ar, Source("oe", quotation=False, vc="customer")),
        "pick_list": (inv_ar, Source("oe", quotation=False, vc="customer")),
        "sales_order": (Source("oe", quotation=False, vc="customer"),),
        "purchase_order": (Source("oe", quotation=False, vc="vendor"),),
        "sales_quotation": (Source("oe", quotation=True, vc="customer"),),
        "request_quotation": (Source("oe", quotation=True, vc="vendor"),),
        "ar_transaction": (Source("ar", invoice=False, negative=False),),
        "credit_note": (Source("ar", invoice=False, negative=True),),
        "ap_transaction": (Source("ap", invoice=False, negative=False),),
        "debit_note": (Source("ap", invoice=False, negative=True),),
    }.get(key, ())


def source_module(src) -> str:
    """Module die aan moet staan voor een batchbron."""
    if src.table == "oe":
        return "quotations" if src.quotation else "oe"
    return src.table


def kinds_for(screen: str) -> List[DocKind]:
    return [KINDS[k] for k in SCREEN_KINDS.get(screen, ())]


def label(key: str) -> str:
    k = KINDS.get(key)
    return k.label if k else LETTERS.get(key) or PAYMENT_DOCS.get(key, key)


#: namen in Beheer waar twee soorten hetzelfde heten op het document
LONG_LABELS = {"ar_transaction": N_("Factuur zonder artikelen"), "debit_invoice": N_("Debetnota met artikelen"),
               "debit_note": N_("Debetnota zonder artikelen"), "credit_note": N_("Creditnota zonder artikelen"),
               "ap_transaction": N_("Inkoopboeking zonder artikelen"),
               "reminder1": N_("Aanmaning niveau 1 (herinnering)"), "reminder2": N_("Aanmaning niveau 2"),
               "reminder3": N_("Aanmaning niveau 3"),
               "receipt": N_("Betalingsspecificatie ontvangst (klant)"),
               "check": N_("Betalingsspecificatie betaling (leverancier)")}


def long_label(key: str) -> str:
    return LONG_LABELS.get(key) or label(key)


@dataclass
class Located:
    """Een document gevonden in de database: bron, relatiesoort, nummer en (bij orders) ordersoort."""

    kind: DocKind
    trans_id: int
    source: str
    vc: str
    number: str
    order_type: Optional[str] = None
    table: str = "ar"

    @property
    def screen_url(self) -> str:
        key = (self.source, self.order_type if self.source == "order" else self.vc)
        return f"/{SCREEN_URL[key]}"

    @property
    def module(self) -> str:
        return MODULE[(self.source, self.order_type if self.source == "order" else self.vc)]


def locate(conn, key: str, trans_id: int) -> Optional[Located]:
    """Zoek waar document ``trans_id`` staat en of de soort erbij past (anders None).

    Een pakbon of picklijst kan bij een verkoopfactuur of een verkooporder horen (de id's van ar, ap en oe
    komen uit één reeks)."""
    kind = KINDS.get(key)
    if kind is None:
        return None
    if kind.source == "order" or key in FROM_ORDER:
        r = conn.execute("SELECT customer_id, vendor_id, quotation, ordnumber, quonumber FROM oe WHERE id = %s",
                         (trans_id,)).fetchone()
        if r:
            vc = "customer" if r[0] else "vendor"
            otype = {("customer", False): "sales_order", ("customer", True): "sales_quotation",
                     ("vendor", False): "purchase_order", ("vendor", True): "request_quotation"}[(vc, bool(r[2]))]
            if key in SCREEN_KINDS[otype]:
                return Located(kind, trans_id, "order", vc, (r[4] if r[2] else r[3]) or "", otype, "oe")
            return None
        if kind.source == "order":
            return None
    table = "ar" if kind.vc == "customer" else "ap"
    r = conn.execute(f"SELECT invoice, invnumber, amount < 0 FROM {table} WHERE id = %s", (trans_id,)).fetchone()
    if not r:
        return None
    invoice, number, negative = bool(r[0]), r[1] or "", bool(r[2])
    if invoice:
        screen = (("credit_invoice" if negative else "invoice") if kind.vc == "customer"
                  else ("debit_invoice" if negative else "vendor_invoice"))
        source = "invoice"
    else:
        screen = (("credit_note" if negative else "ar_transaction") if kind.vc == "customer"
                  else ("debit_note" if negative else "ap_transaction"))
        source = "transaction"
    if key not in SCREEN_KINDS[screen]:
        return None
    return Located(kind, trans_id, source, kind.vc, number, None, table)


def screen_of(conn, trans_id: int) -> Optional[str]:
    """Schermsoort van een boeking (sleutel in ``SCREEN_KINDS``), of None (memoriaal, niet gevonden)."""
    r = conn.execute("SELECT customer_id, quotation FROM oe WHERE id = %s", (trans_id,)).fetchone()
    if r:
        return {("customer", False): "sales_order", ("customer", True): "sales_quotation",
                ("vendor", False): "purchase_order", ("vendor", True): "request_quotation"}[
                    ("customer" if r[0] else "vendor", bool(r[1]))]
    for table, vc in (("ar", "customer"), ("ap", "vendor")):
        r = conn.execute(f"SELECT invoice, amount < 0 FROM {table} WHERE id = %s", (trans_id,)).fetchone()
        if r:
            invoice, negative = bool(r[0]), bool(r[1])
            if vc == "customer":
                return (("credit_invoice" if negative else "invoice") if invoice
                        else ("credit_note" if negative else "ar_transaction"))
            return (("debit_invoice" if negative else "vendor_invoice") if invoice
                    else ("debit_note" if negative else "ap_transaction"))
    return None


def key_for_formname(formname: str, module: Optional[str]) -> Optional[str]:
    """Documentsoort (sleutel) bij een formname uit ``status``/``recurringemail``/``recurringprint``:
    "transaction" is ``ar_transaction`` of ``ap_transaction`` naar de module (ar/ap); onbekend: None."""
    if formname == "transaction":
        return {"ar": "ar_transaction", "ap": "ap_transaction"}.get(module or "")
    return formname if formname in KINDS and KINDS[formname].formname == formname else None


def print_vars(pd, loc: Located, variant: Optional[str] = None):
    """Sjabloonvariabelen voor een gevonden document (sqlledger.printdata)."""
    lang = variant or None
    # picklijst op locatie gesorteerd (DS8); de rest in de volgorde van het scherm
    sortby = "bin" if loc.kind.key == "pick_list" else "runningnumber"
    if loc.source == "order":
        return pd.order_vars(loc.trans_id, loc.kind.formname, language_code=lang, sortby=sortby)
    if loc.source == "transaction":
        return pd.transaction_vars(loc.trans_id, loc.vc, loc.kind.formname, language_code=lang)
    return pd.invoice_vars(loc.trans_id, loc.vc, loc.kind.formname, language_code=lang, sortby=sortby)



def screen_docs(conn, screen: str, trans_id: Optional[int], ready: Optional[str] = None) -> Optional[dict]:
    """Gegevens voor de knoppen PDF/E-mail en de regel Geprint/Gemaild op een scherm (DS2).

    ``ready``: soort uit ``?pdf=`` na "Opslaan en PDF" (de link wordt dan bovenaan getoond)."""
    if not trans_id or screen not in SCREEN_KINDS:
        return None
    rows = conn.execute("SELECT formname, printed, emailed FROM status WHERE trans_id = %s", (trans_id,)).fetchall()
    st = {r[0]: (bool(r[1]), bool(r[2])) for r in rows}
    kinds = []
    for k in kinds_for(screen):
        printed, emailed = st.get(k.formname, (False, False))
        kinds.append({"key": k.key, "label": k.label, "printed": printed, "emailed": emailed})
    shown = [k for n, k in enumerate(kinds) if n == 0 or k["printed"] or k["emailed"]]
    return {"id": int(trans_id), "kinds": kinds, "status": shown,
            "ready": ready if ready in SCREEN_KINDS[screen] else None}


def variant_choices(conn, renderer, current: str = "") -> List[dict]:
    """Keuzes voor het veld Sjabloonvariant (DS9): talen uit Beheer > Talen, variantmappen van de
    administratie en de huidige waarde (ook als die nergens meer bestaat)."""
    out: Dict[str, str] = {}
    for code, description in conn.execute("SELECT code, description FROM language ORDER BY code").fetchall():
        out[code] = description or code
    for name in renderer.variants():
        out.setdefault(name, name)
    if current:
        out.setdefault(current, current)
    return [{"code": c, "label": d} for c, d in out.items()]


#: sjabloon -> documentsoort om een proef-PDF of voorbeeld mee te maken (gedeelde delen, DS11)
PARTIAL_KIND = {"_base": "invoice", "_invoice": "invoice", "_parties": "invoice", "_lines": "invoice", "_order": "sales_order",
                "_transaction": "ar_transaction", "_reminder": "reminder1", "_payment": "receipt"}


def find_document(conn, key: str, number: str = "") -> Optional[Located]:
    """Laatste document van soort ``key`` (of met dit nummer) voor een voorbeeld-PDF (DS11)."""
    kind = KINDS.get(PARTIAL_KIND.get(key, key))
    if kind is None:
        return None
    if kind.source == "order":
        quo = kind.key in ("sales_quotation", "request_quotation")
        vc_col = "customer_id" if kind.vc == "customer" else "vendor_id"
        numcol = "quonumber" if quo else "ordnumber"
        q = (f"SELECT id FROM oe WHERE {vc_col} > 0 AND quotation = %s"
             + (f" AND {numcol} = %s" if number else "") + " ORDER BY transdate DESC, id DESC LIMIT 1")
        r = conn.execute(q, (quo, number) if number else (quo,)).fetchone()
    else:
        table = "ar" if kind.vc == "customer" else "ap"
        negative = kind.key in ("credit_invoice", "debit_invoice", "credit_note", "debit_note")
        invoice = kind.source == "invoice"
        q = (f"SELECT id FROM {table} WHERE invoice = %s AND (amount < 0) = %s"
             + (" AND invnumber = %s" if number else "") + " ORDER BY transdate DESC, id DESC LIMIT 1")
        r = conn.execute(q, (invoice, negative, number) if number else (invoice, negative)).fetchone()
    return locate(conn, kind.key, r[0]) if r else None
