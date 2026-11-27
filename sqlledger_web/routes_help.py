"""Pagina *Help* "Hoe doe ik …?" (HELP_ANALYSE HL10 A, golf 4 K, V5/V6 A).

De taken staan in ``templates/help/tasks.html`` (zo controleren de label- en vertaaltoetsen ze net als de helpblokken).
Elke stap is een menu-item; deze route bepaalt per adres wat de gebruiker ziet:

- ``has(url)``: het menu-item bestaat in deze administratie (module en vlag aan), ongeacht de rol;
- ``go(url, groep, item)``: het menupad, als link als de gebruiker het mag openen, anders met de rol erachter
  ("(beheerder)") zodat hij weet wie hij moet vragen;
- ``task_on([urls])``: de taak verschijnt als één van de kernstappen bestaat (een urenschrijver: als hij er één mag
  openen);
- ``show_system``: de taken voor de systeembeheerder ziet ook de beheerder.
"""

from __future__ import annotations

from typing import Dict, Iterable, Set

from fastapi import APIRouter, Depends
from markupsafe import Markup

from .helptext import hm
from .i18n import N_, gettext as _
from .menu import PRODUCTION, SYSTEM_GROUP, build_menu
from .web import Ctx, ctx_user, render

router = APIRouter()

#: wie een stap mag doen, als de gebruiker het zelf niet mag
WHO = {"bookkeeper": N_("(boekhouder)"), "admin": N_("(beheerder)"), "system": N_("(systeembeheerder)")}


def _urls(groups) -> Dict[str, str]:
    """Adres -> minimale rol ("system" voor de groep Systeem)."""
    out: Dict[str, str] = {}
    for g in groups:
        for i in g.items:
            if i.url:
                out[i.url] = "system" if g.key == "system" else i.role
    return out


def context(ctx: Ctx) -> dict:
    sysadmin = bool(ctx.user["is_sysadmin"])
    if ctx.company:
        modules = list(ctx.company["modules"] or [])
        flags = {k for k in ("vat_return", "icp") if ctx.company.get(k)}
        if ctx.production():
            flags.add(PRODUCTION)
        avail = _urls(build_menu(modules, "admin", True, frozenset(flags)))
    else:
        avail = _urls([SYSTEM_GROUP])
    mine: Set[str] = set(_urls(ctx.menu()))

    def has(url: str) -> bool:
        return url in avail

    def go(url: str, *parts: str) -> Markup:
        path = hm(*parts)
        if url in mine:
            return Markup('<a href="{}">{}</a>').format(url, path)
        who = WHO.get(avail.get(url, ""))
        return Markup("{} {}").format(path, _(who)) if who else path

    def task_on(urls: Iterable[str]) -> bool:
        urls = list(urls)
        if ctx.role == "hours":
            return any(u in mine for u in urls)
        return any(u in avail for u in urls)

    return dict(has=has, go=go, task_on=task_on,
                show_system=sysadmin or ctx.has_role("admin"), no_company=not ctx.company)


@router.get("/help", name="help_tasks")
def help_page(ctx: Ctx = Depends(ctx_user)):
    return render(ctx, "helppage.html", **context(ctx))
