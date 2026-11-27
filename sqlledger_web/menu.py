"""Menu in de SQL-Ledger-indeling, gefilterd op modules en rol.

Een item zonder ``url`` is nog niet gebouwd en wordt grijs getoond met de stap
waarin het komt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .appdb import MODULES, ROLE_LEVEL
from .i18n import N_

F2 = N_("fase 2")
F3 = N_("fase 3")
F4 = N_("fase 4")

#: productie (PB20): alleen met samengestelde artikelen of als de beheerder het aanzet (``production.py``)
PRODUCTION = "production"

#: Btw-rubriek (MN1): zichtbaar zodra verkoop of inkoop aan staat (de tarieven ook zonder, voor de beheerder)
VAT_MODULES = ("ar", "ap")


@dataclass
class Item:
    label: str
    url: Optional[str] = None
    planned: str = ""
    role: str = "readonly"  # minimale rol
    match: str = ""         # padvoorvoegsel waarop het item ook 'hier' is (bv. /memoriaal/123)
    modules: Tuple[str, ...] = ()  # alleen als één van deze modules aan staat (leeg = altijd)
    heading: bool = False   # tussenkopje (MN2), geen link
    flag: str = ""          # alleen als deze kolom van de administratie aan staat (bv. vat_return)


def Head(label: str) -> Item:
    return Item(label, heading=True)


@dataclass
class Group:
    key: str  # module, of 'vat' / 'admin' / 'system'
    label: str
    items: List[Item] = field(default_factory=list)


def _batch(kind: str, **kw) -> Item:
    # Documenten in batch (MN4): onder Verkoop en Inkoop (en Orders/Offertes, voor een administratie zonder
    # verkoop en inkoop), met de soort van die rubriek voorgekozen
    return Item(N_("Documenten in batch"), url=f"/documenten/batch?soort={kind}", **kw)


def _company_menu() -> List[Group]:
    return [
        Group("ar", MODULES["ar"], [
            Head(N_("Boeken")),
            Item(N_("Boeking toevoegen"), url="/verkoop/nieuw", role="bookkeeper", match="/verkoop/"),
            Item(N_("Verkoopfactuur"), url="/verkoopfactuur/nieuw", role="bookkeeper", match="/verkoopfactuur/"),
            Item(N_("Creditnota"), url="/verkoop/nieuw?creditnota=1", role="bookkeeper"),
            Head(N_("Overzichten")),
            Item(N_("Overzicht"), url="/rapporten/overzicht-verkoop"),
            Item(N_("Openstaand"), url="/rapporten/openstaand-verkoop"),
            Item(N_("Ouderdomsanalyse"), url="/rapporten/ouderdom-verkoop"),
            _batch("invoice", match="/documenten/batch"),
            Head(N_("Debiteurenbeheer")),
            Item(N_("Aanmaningen"), url="/aanmaningen"),
            Item(N_("Rekeningoverzichten"), url="/rekeningoverzichten"),
            Head(N_("Klanten")),
            Item(N_("Klanten zoeken"), url="/klanten", match="/klanten/"),
            Item(N_("Klant toevoegen"), url="/klanten/nieuw", role="bookkeeper"),
        ]),
        Group("ap", MODULES["ap"], [
            Head(N_("Boeken")),
            Item(N_("Boeking toevoegen"), url="/inkoop/nieuw", role="bookkeeper", match="/inkoop/"),
            Item(N_("Inkoopfactuur uploaden"), url="/inkoop/uploaden", role="bookkeeper"),
            Item(N_("Inkoopfactuur"), url="/inkoopfactuur/nieuw", role="bookkeeper", match="/inkoopfactuur/"),
            Item(N_("Creditnota"), url="/inkoop/nieuw?creditnota=1", role="bookkeeper"),
            Head(N_("Overzichten")),
            Item(N_("Overzicht"), url="/rapporten/overzicht-inkoop"),
            Item(N_("Openstaand"), url="/rapporten/openstaand-inkoop"),
            Item(N_("Ouderdomsanalyse"), url="/rapporten/ouderdom-inkoop"),
            _batch("vendor_invoice"),
            Head(N_("Leveranciers")),
            Item(N_("Leveranciers zoeken"), url="/leveranciers", match="/leveranciers/"),
            Item(N_("Leverancier toevoegen"), url="/leveranciers/nieuw", role="bookkeeper"),
        ]),
        Group("cash", MODULES["cash"], [
            Head(N_("Ontvangen en betalen")),
            Item(N_("Ontvangst"), url="/ontvangst/nieuw", role="bookkeeper", match="/ontvangst/"),
            Item(N_("Ontvangsten (meerdere klanten)"), url="/ontvangsten", role="bookkeeper"),
            Item(N_("Betaling"), url="/betaling/nieuw", role="bookkeeper", match="/betaling/"),
            Item(N_("Betalingen (meerdere leveranciers)"), url="/betalingen", role="bookkeeper"),
            Item(N_("Betaalbestand maken"), url="/betalingen/betaalbestand", role="bookkeeper", flag="sepa"),
            Head(N_("Corrigeren")),
            Item(N_("Ontvangsten corrigeren"), url="/ontvangsten/corrigeren", role="bookkeeper"),
            Item(N_("Betalingen corrigeren"), url="/betalingen/corrigeren", role="bookkeeper"),
            Item(N_("Koersverschilboeking"), url="/memoriaal/nieuw?fx=1", role="bookkeeper"),
            Head(N_("Bank")),
            Item(N_("Bankafschrift inlezen"), url="/bank/inlezen", role="bookkeeper", match="/bank/afschriften"),
            Item(N_("Bankregels verwerken"), url="/bank/regels", role="bookkeeper", match="/bank/regel/"),
            Item(N_("Vaste bankregels"), url="/bank/vaste-regels", role="bookkeeper"),
            Item(N_("Afletteren"), url="/afletteren", role="bookkeeper"),
            Head(N_("Overzichten")),
            Item(N_("Overzicht ontvangsten"), url="/rapporten/ontvangsten"),
            Item(N_("Overzicht betalingen"), url="/rapporten/betalingen"),
            Item(N_("Afgeletterde posten"), url="/afletteren/rapport"),
            Item(N_("Betalingsregister"), url="/betalingsregister"),
        ]),
        Group("oe", MODULES["oe"], [
            Item(N_("Verkooporder"), url="/verkooporder/nieuw", role="bookkeeper", match="/verkooporder/"),
            Item(N_("Inkooporder"), url="/inkooporder/nieuw", role="bookkeeper", match="/inkooporder/"),
            Item(N_("Verkooporders"), url="/verkooporders"),
            Item(N_("Inkooporders"), url="/inkooporders"),
            _batch("sales_order"),
        ]),
        Group("quotations", MODULES["quotations"], [
            Item(N_("Offerte"), url="/offerte/nieuw", role="bookkeeper", match="/offerte/"),
            Item(N_("Offerteaanvraag"), url="/offerteaanvraag/nieuw", role="bookkeeper", match="/offerteaanvraag/"),
            Item(N_("Offertes"), url="/offertes"),
            Item(N_("Offerteaanvragen"), url="/offerteaanvragen"),
            _batch("sales_quotation"),
        ]),
        Group("ic", MODULES["ic"], [
            Item(N_("Artikel toevoegen"), url="/artikelen/nieuw", role="bookkeeper"),
            Item(N_("Dienst toevoegen"), url="/artikelen/nieuw?soort=dienst", role="bookkeeper"),
            Item(N_("Samengesteld artikel toevoegen"), url="/artikelen/nieuw?soort=samengesteld", role="bookkeeper",
                 flag=PRODUCTION),
            Item(N_("Overzicht"), url="/artikelen", match="/artikelen/"),
            Item(N_("Assembleren"), url="/artikelen-assembleren", flag=PRODUCTION),
            Item(N_("Stuklijsten"), url="/artikelen-stuklijsten", flag=PRODUCTION),
            Item(N_("Productiehistorie"), url="/artikelen-productie", flag=PRODUCTION),
            Item(N_("Artikelgroepen"), url="/artikelen-groepen"),
        ]),
        Group("gl", MODULES["gl"], [
            Item(N_("Memoriaal"), url="/memoriaal/nieuw", role="bookkeeper", match="/memoriaal/"),
            Item(N_("Boekingen zoeken"), url="/rapporten/journaal"),
        ]),
        # Btw (MN1): rapporten uit Verkoop/Inkoop, tarieven uit Beheer; aangifte en rubrieken komen na het menu
        Group("vat", N_("Btw"), [
            Item(N_("Btw-aangifte"), url="/btw/aangifte", modules=VAT_MODULES, flag="vat_return", match="/btw/aangifte"),
            Item(N_("ICP-opgaaf"), url="/btw/icp", modules=("ar",), flag="icp", match="/btw/icp"),
            Head(N_("Overzichten")),
            Item(N_("Btw verkoop"), url="/rapporten/btw-verkoop", modules=("ar",)),
            Item(N_("Onbelaste omzet"), url="/rapporten/onbelast-verkoop", modules=("ar",)),
            Item(N_("Btw inkoop"), url="/rapporten/btw-inkoop", modules=("ap",)),
            Item(N_("Onbelaste inkoop"), url="/rapporten/onbelast-inkoop", modules=("ap",)),
            Head(N_("Beheer")),
            Item(N_("Btw-tarieven"), url="/beheer/btw", role="admin"),
            Item(N_("Btw-rubrieken"), url="/btw/rubrieken", role="admin", modules=VAT_MODULES, flag="vatform"),
        ]),
        Group("reports", MODULES["reports"], [
            Item(N_("Rekeningschema"), url="/rapporten/rekeningschema"),
            Item(N_("Rekeningmutaties"), url="/rapporten/rekeningmutaties"),
            Item(N_("Proefbalans"), url="/rapporten/proefbalans"),
            Item(N_("Winst en verlies"), url="/rapporten/winst-en-verlies"),
            Item(N_("Balans"), url="/rapporten/balans"),
            Item(N_("Bijlagen"), url="/bijlagen", match="/bijlagen"),
        ]),
        Group("recurring", MODULES["recurring"], [
            Item(N_("Terugkerende boekingen"), url="/terugkerend", role="bookkeeper", match="/herhalen/"),
        ]),
        Group("projects", MODULES["projects"], [
            Item(N_("Afdelingen"), url="/beheer/afdelingen", role="bookkeeper"),
            Item(N_("Projecten"), url="/beheer/projecten", role="bookkeeper"),
            Head(N_("Uren")),
            Item(N_("Mijn uren"), url="/uren/mijn", role="hours", match="/uren/mijn"),
            Item(N_("Maandstaat"), url="/uren/maandstaat", role="bookkeeper", match="/uren/maandstaat"),
            Item(N_("Toewijzingen"), url="/uren/toewijzingen", role="bookkeeper", match="/uren/toewijzing"),
            Item(N_("Tijdkaarten"), url="/uren/tijdkaarten", match="/uren/tijdkaart"),
            Item(N_("Tijdkaart toevoegen"), url="/uren/tijdkaart/nieuw", role="bookkeeper"),
            Item(N_("Verkooporders uit uren"), url="/uren/verkooporders", role="bookkeeper", modules=("oe",)),
            # v1.1 interne uren (IN3, IN5)
            Item(N_("Interne tarieven"), url="/uren/tarieven", role="bookkeeper", match="/uren/tarieven"),
            Item(N_("Uren per project"), url="/rapporten/uren-per-project"),
        ]),
        Group("admin", N_("Beheer"), [
            Head(N_("Administratie")),
            Item(N_("Instellingen"), url="/beheer/instellingen", role="admin"),
            Item(N_("Ingebruikname"), url="/beheer/ingebruikname", role="admin", match="/beheer/ingebruikname"),
            Item(N_("Rekeningschema"), url="/beheer/rekeningschema", role="admin"),
            Item(N_("GIFI-indeling"), url="/beheer/gifi", role="admin", match="/beheer/gifi", flag="gifi"),
            Item(N_("RGS-koppeling"), url="/beheer/rgs", role="admin", flag="nl"),
            Item(N_("Talen"), url="/beheer/talen", role="admin"),
            Item(N_("Branches"), url="/beheer/branches", role="admin"),
            Item(N_("Betaalmethoden"), url="/beheer/betaalmethoden", role="admin"),
            Item(N_("Importeren"), url="/beheer/importeren", role="admin", match="/beheer/importeren"),
            Item(N_("Exporteren"), url="/beheer/exporteren", role="admin", match="/beheer/exporteren"),
            Head(N_("Valuta")),
            Item(N_("Valuta"), url="/beheer/valuta", role="admin"),
            Item(N_("Koersen"), url="/beheer/koersen", role="admin"),
            Head(N_("Documenten en e-mail")),
            Item(N_("Huisstijl"), url="/beheer/huisstijl", role="admin"),
            Item(N_("E-mail"), url="/beheer/email", role="admin"),
            Item(N_("Documentinstellingen"), url="/beheer/documentinstellingen", role="admin"),
            Item(N_("Sjablonen"), url="/beheer/sjablonen", role="admin"),
            Head(N_("Afsluiten")),
            Item(N_("Afsluiten"), url="/beheer/afsluiten", role="admin"),
            Item(N_("Jaarafsluiting"), url="/beheer/jaarafsluiting", role="admin"),
            Head(N_("Controle")),
            Item(N_("Audittrail"), url="/beheer/audittrail", role="admin"),
            Item(N_("Locks"), url="/beheer/locks", role="admin"),
            Item(N_("Run my Accounts-gegevens"), url="/beheer/rma", role="admin", flag="rma"),
        ]),
    ]


SYSTEM_GROUP = Group("system", N_("Systeem"), [
    Item(N_("Gebruikers"), url="/systeem/gebruikers"),
    Item(N_("Administraties"), url="/systeem/administraties"),
    Item(N_("Back-up maken"), url="/systeem/back-up-maken"),
    Item(N_("Back-up inlezen"), url="/systeem/back-up"),
    Item(N_("RGS-lijst"), url="/systeem/rgs", flag="nl"),
    Item(N_("Logboek"), url="/systeem/logboek"),
])


def _visible(items: List[Item], modules: List[str], level: int, flags: frozenset = frozenset()) -> List[Item]:
    """Items die de rol en de modules toelaten; een tussenkopje alleen als er iets onder staat."""
    out: List[Item] = []
    for i in items:
        if i.heading:
            out.append(i)
        elif ROLE_LEVEL[i.role] <= level and (not i.modules or set(i.modules) & set(modules)) \
                and (not i.flag or i.flag in flags):
            out.append(i)
    return [i for n, i in enumerate(out) if not i.heading or (n + 1 < len(out) and not out[n + 1].heading)]


def build_menu(modules: Optional[List[str]], role: Optional[str], is_sysadmin: bool,
               flags: frozenset = frozenset()) -> List[Group]:
    """Menu voor de huidige gebruiker; ``modules``/``role`` zijn None zonder gekozen administratie;
    ``flags``: aangezette kolommen van de administratie (``Item.flag``)."""
    groups: List[Group] = []
    if modules is not None and role is not None:
        level = ROLE_LEVEL[role]
        for g in _company_menu():
            if g.key not in ("vat", "admin") and g.key not in modules:
                continue
            items = _visible(g.items, modules, level, flags)
            if any(not i.heading for i in items):
                groups.append(Group(g.key, g.label, items))
    if is_sysadmin:
        groups.append(Group(SYSTEM_GROUP.key, SYSTEM_GROUP.label,
                            [i for i in SYSTEM_GROUP.items if not i.flag or i.flag in flags]))
    return groups
