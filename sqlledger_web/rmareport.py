"""Inleesrapport van een Run my Accounts- / ledger123-back-up op het scherm (RMA_IMPORT_ANALYSE.md RI6/RI7).

Het rapport (``sqlledger.rmaimport.finish``, bewaard in ``rma.import_report``) bevat sleutels; hier worden ze
vertaalde regels voor het resultaatscherm na het inlezen, het foutscherm (verschil in de controle) en
Beheer > Run my Accounts-gegevens.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from sqlledger.amounts import format_amount

from .i18n import N_, gettext as _

#: omgezet naar PyBalans (of door de keten al goed overgezet; ter controle geteld)
DONE = {
    "vendor_expense_account": N_("Vaste kostenrekening per leverancier"),
    "fiscal_year_end": N_("Boekjaar (laatste maand)"),
    "customer_iban": N_("IBAN/BIC van klanten"),
    "vendor_iban": N_("IBAN/BIC van leveranciers"),
    "employee_iban": N_("IBAN/BIC van medewerkers"),
    "employee_address": N_("Adres van medewerkers"),
    "currency_precision": N_("Decimalen per valuta"),
}
#: zonder plek in PyBalans: blijft in schema rma
KEPT = {
    "financial_years": N_("Boekjaren van Run my Accounts"),
    "employee_role": N_("Rol van medewerkers"),
    "exchangerate_sell_differs": N_("Verkoopkoersen die afwijken van de aankoopkoers (PyBalans kent één koers per dag)"),
    "customer_income_account": N_("Vaste opbrengstrekening per klant"),
    "customer_clearing_account": N_("Tussenrekening betalingen per klant"),
    "vendor_clearing_account": N_("Tussenrekening betalingen per leverancier"),
    "customer_reminderstop": N_("Klanten met aanmaningsstop"),
    "vendor_reminderstop": N_("Leveranciers met aanmaningsstop"),
    "vendor_early_payment_discount": N_("Korting bij vroege betaling (leveranciers)"),
    "bank_qriban": N_("QR-IBAN van bankrekeningen"),
    "vendor_expense_account_missing": N_("Kostenrekening per leverancier die niet meer bestaat"),
    "inventory_rows": N_("Voorraadregels per magazijn (nog niet omgezet)"),
}
WARNINGS = {
    "fx_loss_account_merged": N_("Koersverschillen: Run my Accounts had een aparte winst- ({gain}) en verliesrekening "
                                 "({loss}); PyBalans gebruikt er één ({gain}). Controleer bij Beheer > Instellingen."),
    "customer_iban_lost": N_("IBAN/BIC van {count} klanten niet teruggevonden."),
    "vendor_iban_lost": N_("IBAN/BIC van {count} leveranciers niet teruggevonden."),
    "employee_iban_lost": N_("IBAN/BIC van {count} medewerkers niet teruggevonden."),
    "employee_address_lost": N_("Adres van {count} medewerkers niet teruggevonden."),
}
KINDS = {"trigger": N_("triggers"), "function": N_("functies"), "constraint": N_("koppelingen (foreign keys)"),
         "view": N_("views")}


def _amount(value: Optional[str], nf: str) -> str:
    if value in (None, "", "None"):
        return "—"
    try:
        return format_amount(Decimal(value), nf, 2) or "0"
    except InvalidOperation:
        return str(value)


def view(report: Optional[Dict[str, Any]], user: dict) -> Optional[Dict[str, Any]]:
    """Rapport → regels voor het sjabloon ``system/_rma_report.html``; None zonder rapport."""
    if not report:
        return None
    nf = user.get("numberformat") or "1.000,00"
    restored = report.get("restored") or {}
    done = []
    for i in restored.get("done") or []:
        label = _(DONE.get(i["key"], i["key"]))
        if i["key"] == "fiscal_year_end":
            done.append({"label": label, "value": str(i.get("month", ""))})
        else:
            done.append({"label": label, "value": str(i["count"])})
    kept = [{"label": _(KEPT.get(i["key"], i["key"])), "value": str(i["count"])} for i in restored.get("kept") or []]
    warnings = []
    for w in restored.get("warnings") or []:
        text = WARNINGS.get(w["key"])
        warnings.append(_(text).format(**w) if text else w["key"])
    objects: Dict[str, int] = {}
    for o in report.get("objects") or []:
        objects[o["kind"]] = objects.get(o["kind"], 0) + 1
    tables = sorted(report.get("extra_tables") or [], key=lambda t: t["table"])
    columns = sorted((report.get("extra_columns") or {}).items())
    differences = []
    for kind, key in (("balance", N_("Rekening {key}")), ("open_items", N_("Document {key}"))):
        for k, before, after in (report.get(kind) or {}).get("differences") or []:
            differences.append({"what": _(key).format(key=k), "before": _amount(before, nf),
                                "after": _amount(after, nf)})
    counts = [c for c in report.get("counts") or [] if not c.get("ok")]
    return {
        "version": report.get("source_version") or "",
        "accounts": (report.get("balance") or {}).get("accounts", 0),
        "documents": (report.get("open_items") or {}).get("documents", 0),
        "differences": differences,
        "done": done, "kept": kept, "warnings": warnings,
        "tables": tables, "rows": sum(t.get("rows", 0) for t in tables),
        "columns": columns, "ncolumns": sum(len(c) for _t, c in columns),
        "objects": [{"label": _(KINDS.get(k, k)), "count": n} for k, n in sorted(objects.items())],
        "extension": report.get("skipped_extension") or "",
        "chain_errors": report.get("chain_errors") or [],
        "counts": counts,
        "warehouse": not report.get("warehouse_converted"),
    }
