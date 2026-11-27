"""Beheer > Documentinstellingen (fase 4 stap 3, DI1–DI4): wat er op facturen, orders, pakbonnen en
picklijsten staat, zonder HTML-kennis. Opslag en standaardwaarden in ``sqlledger.docsettings``.

Eén formulier; teksten en eigen kolomnamen gelden per documenttaal (keuze bovenaan, ``?taal=``), de rest
voor alle talen. Alleen beheerders. Wijzigingen in de audittrail (als die aan staat) en het logboek van de app.
"""

from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, Depends
from starlette.datastructures import FormData

from sqlledger import _db, docsettings as ds

from . import doctypes
from .documents import LANGUAGES, _t
from .i18n import N_, gettext as _
from .routes_arap import _lib
from .web import Ctx, Redirect, csrf_form, ctx_company, render, require_role

router = APIRouter()

LANGUAGE_NAMES = {"nl": N_("Nederlands"), "en": N_("Engels"), "de": N_("Duits"), "fr": N_("Frans")}

GROUP_LABELS = {"sales_invoice": N_("Verkoopfacturen en creditfacturen"),
                "purchase_invoice": N_("Inkoopfacturen en debetnota's"), "order": N_("Orders en offertes"),
                "packing_list": N_("Pakbon"), "pick_list": N_("Picklijst")}

#: veld op het scherm: naam en waar de waarde vandaan komt
FIELD_LABELS = {
    "number": N_("Artikelnummer"), "barcode": N_("EAN/barcode (artikel)"),
    "hscode": N_("Goederencode (artikel)"), "description": N_("Omschrijving"),
    "countryorigin": N_("Land van herkomst (artikel)"), "projectnumber": N_("Project (regel)"),
    "deliverydate": N_("Leverdatum (regel)"), "serialnumber": N_("Serienummer (regel)"),
    "package": N_("Verpakking (regel)"), "bin": N_("Locatie (artikel)"), "qty": N_("Aantal"),
    "unit": N_("Eenheid"), "sellprice": N_("Prijs"), "discount": N_("Korting"),
    "netprice": N_("Nettoprijs (na korting)"),
    "grossweight": N_("Gewicht (regel)"), "netweight": N_("Nettogewicht (regel)"), "linetotal": N_("Bedrag"),
}
MODE_LABELS = {ds.COLUMN: N_("Kolom"), ds.AUTO: N_("Kolom als ingevuld"),
               ds.NOTE: N_("Regel onder de omschrijving"), ds.OFF: N_("Niet tonen")}

#: standaard slottekst per documentsoort (zoals de standaardsjablonen; leeg = geen)
STANDARD_CLOSING = {"invoice": "Dank voor uw bestelling.", "sales_order": "Dank voor uw bestelling.",
                    "purchase_order": "Graag ons ordernummer vermelden op pakbon en factuur.",
                    "sales_quotation": "Wij zien uw opdracht graag tegemoet.",
                    "request_quotation": "Graag ontvangen wij uw offerte.",
                    "reminder1": "Heeft u inmiddels betaald? Dan kunt u deze brief als niet verzonden beschouwen.",
                    "reminder2": "Heeft u inmiddels betaald? Dan kunt u deze brief als niet verzonden beschouwen.",
                    "reminder3": "Heeft u inmiddels betaald? Dan kunt u deze brief als niet verzonden beschouwen."}

#: standaard inleidende tekst van de brieven (zoals doctemplates/standard/_reminder.html en statement.html)
STANDARD_INTRO = {
    "reminder1": "Volgens onze administratie zijn de onderstaande facturen nog niet (volledig) betaald. Wellicht is "
                 "dit aan uw aandacht ontsnapt. Wij verzoeken u het openstaande bedrag binnen 14 dagen te voldoen.",
    "reminder2": "Ondanks onze herinnering hebben wij de betaling van de onderstaande facturen nog niet ontvangen. "
                 "Wij verzoeken u het openstaande bedrag binnen 7 dagen te voldoen.",
    "reminder3": "Ondanks eerdere herinneringen staan de onderstaande facturen nog steeds open. Wij verzoeken u "
                 "dringend het openstaande bedrag binnen 7 dagen te voldoen. Blijft betaling uit, dan zijn wij "
                 "genoodzaakt de vordering uit handen te geven.",
    "statement": "Hieronder vindt u het overzicht van uw openstaande posten. Neemt u bij vragen of verschillen "
                 "contact met ons op.",
    "receipt": "Wij hebben uw betaling ontvangen en als volgt verwerkt.",
    "check": "Wij hebben het onderstaande bedrag aan u overgemaakt voor de volgende facturen.",
}


def _taal(value) -> str:
    v = str(value or "")
    return v if v in LANGUAGES else "nl"


def _page(ctx: Ctx, conn, s: ds.DocSettings, taal: str, status: int = 200, **kw):
    with _db.cursor(conn) as cur:
        company = _db.get_defaults(cur, "company", "address", "tel", "businessnumber", "companyemail")
    t = _t(taal)
    groups = []
    for g, kinds in ds.GROUPS.items():
        rows = []
        for name in ds.GROUP_FIELDS[g]:
            c, f = s.column(g, name), ds.FIELDS[name]
            rows.append({"name": name, "label": FIELD_LABELS[name], "mode": c.mode, "modes": f.modes,
                         "own": c.labels.get(taal, ""), "standard": t(f.label),
                         "changed": c.mode != ds.default_mode(g, name)})
        groups.append({"key": g, "label": GROUP_LABELS[g], "rows": rows, "weights": bool(s.weights.get(g)),
                       "kinds": [doctypes.long_label(k) for k in kinds]})
    kinds = [{"key": k, "label": doctypes.long_label(k), "closing": s.text(taal, "closing", k),
              "note": s.text(taal, "note", k), "standard": t(STANDARD_CLOSING[k]) if k in STANDARD_CLOSING else "",
              "letter": k in ds.LETTER_KINDS, "intro": s.text(taal, "intro", k),
              "intro_standard": t(STANDARD_INTRO[k]) if k in STANDARD_INTRO else ""}
             for k in ds.KINDS]
    ok = ctx.request.query_params.get("ok")
    if ok == "1":
        kw.setdefault("notice", _("Documentinstellingen opgeslagen."))
    return render(ctx, "am/docsettings.html", status, s=s, taal=taal, languages=LANGUAGE_NAMES, groups=groups,
                  kinds=kinds, banks=ds.bank_accounts(conn), company=company, modes=MODE_LABELS,
                  return_text=s.text(taal, "return_line"), footer=s.text(taal, "footer"),
                  doc_language=ctx.company.get("doc_language") or "nl", **kw)


@router.get("/beheer/documentinstellingen", name="docsettings")
def docsettings_page(ctx: Ctx = Depends(ctx_company)):
    require_role(ctx, "admin")
    conn, lib = _lib(ctx)
    with conn:
        return _page(ctx, conn, ds.load(conn), _taal(ctx.request.query_params.get("taal")))


def _from_form(s: ds.DocSettings, form: FormData, taal: str) -> ds.DocSettings:
    """Formulier verwerken op de huidige instellingen: teksten en kolomnamen alleen voor ``taal``."""
    def get(name: str) -> str:
        return str(form.get(name) or "").replace("\r\n", "\n").strip()

    s.taxnumber = get("taxnumber")
    s.bank_mode = ds.BANK_FIXED if get("bank_mode") == ds.BANK_FIXED else ds.BANK_BOOKING
    s.bank_accno = get("bank_accno") if s.bank_mode == ds.BANK_FIXED else ""
    s.bank_name = form.get("bank_name") == "1"
    s.payinfo = form.get("payinfo") == "1"
    s.return_line = form.get("return_line") == "1"
    texts: Dict[str, str] = {"return_line": get("text_return_line"), "footer": get("text_footer")}
    for k in ds.KINDS:
        texts[f"closing:{k}"] = get(f"closing_{k}")
        texts[f"note:{k}"] = get(f"note_{k}")
        if k in ds.LETTER_KINDS:
            texts[f"intro:{k}"] = get(f"intro_{k}")
    s.texts[taal] = {k: v for k, v in texts.items() if v}
    for g in ds.GROUPS:
        cols = s.columns.setdefault(g, {})
        for name in ds.GROUP_FIELDS[g]:
            current = s.column(g, name)
            mode = get(f"mode_{g}_{name}") or current.mode
            labels = {k: v for k, v in current.labels.items() if k != taal}
            own = get(f"label_{g}_{name}")
            if own:
                labels[taal] = own
            cols[name] = ds.Column(mode, labels)
        s.weights[g] = form.get(f"weights_{g}") == "1"
    return s


@router.post("/beheer/documentinstellingen")
def docsettings_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    require_role(ctx, "admin")
    taal = _taal(form.get("taal"))
    conn, lib = _lib(ctx)
    with conn:
        s = _from_form(ds.load(conn), form, taal)
        errors: List[str] = ds.validate(conn, s)
        if errors:
            return _page(ctx, conn, s, taal, 400, error=" ".join(_(e) for e in errors))
        changed = ds.save(conn, s, login=lib.cfg.login)
    ctx.log("docsettings_saved", keys=changed)
    raise Redirect(f"/beheer/documentinstellingen?taal={taal}&ok=1")
