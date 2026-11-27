"""Overzicht NL/EN/DE/FR van alle schermteksten en meldingen ter beoordeling (TL8/DT4).

Gebruik: ``python dev/vertalingen.py <pad.xlsx>`` (vanuit de projectmap).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "web"))

SCREENS = {"gl": "Memoriaal/grootboek", "arap": "Verkoop/inkoop zonder artikelen", "is": "Facturen met artikelen",
           "oe": "Orders en offertes", "ic": "Artikelen", "ct": "Relaties", "am": "Beheer", "rc": "Afletteren",
           "bank": "Bankafschrift", "cp": "Ontvangsten en betalingen", "reports": "Rapporten", "system": "Systeem",
           "recurring": "Terugkerende boekingen", "mail": "E-mail", "asm": "Samengestelde artikelen",
           "docs": "Documenten", "tpl": "Sjablonen", "auth": "Inloggen en account", "main": "Algemeen", "menu": "Menu",
           "reportdefs": "Rapporten", "accounts": "Inloggen en account", "activate": "Inloggen en account",
           "login": "Inloggen en account", "totp": "Inloggen en account", "choose": "Inloggen en account",
           "settings": "Inloggen en account", "doctypes": "Documenten", "documents": "Documenten", "mailer": "E-mail",
           "web": "Algemeen", "base": "Algemeen", "home": "Algemeen", "error": "Algemeen", "tables": "Algemeen",
           "appdb": "Algemeen", "": "Algemeen"}


def screen(where: list[str]) -> str:
    if not where:
        return "Algemeen"
    w = where[0]
    if w.startswith("templates/"):
        part = w.split("/")[1]
        key = part if "." not in part else part.split(".")[0].split("_")[0]
    else:
        key = w.split(":")[0].replace("routes_", "").replace(".py", "")
    return SCREENS.get(key, key) or "Algemeen"


def main(path: str) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    from sqlledger_web import i18n_de, i18n_fr, libmsg
    from sqlledger_web.i18n_en import EN, LIBRARY
    from test_i18n import all_strings

    where = all_strings()
    font, bold = Font(name="Arial", size=10), Font(name="Arial", size=10, bold=True)
    edit = PatternFill("solid", fgColor="E2EFDA")
    wb = Workbook()

    def sheet(ws, head, rows, widths):
        ws.append(head)
        for r in rows:
            ws.append(r)
        for row in ws.iter_rows():
            for c in row:
                c.font = bold if c.row == 1 else font
                c.alignment = Alignment(wrap_text=True, vertical="top")
        for r in range(2, ws.max_row + 1):
            ws.cell(r, len(head)).fill = edit
        for i, w in enumerate(widths):
            ws.column_dimensions[chr(65 + i)].width = w
        ws.freeze_panes = "C2"
        ws.auto_filter.ref = f"A1:{chr(64 + len(head))}{ws.max_row}"

    texts = {**LIBRARY, **EN}
    rows = sorted(([screen(where.get(nl, [])), nl, en, i18n_de.TEXTS.get(nl) or i18n_de.LIBRARY.get(nl, ""),
                    i18n_fr.TEXTS.get(nl) or i18n_fr.LIBRARY.get(nl, ""), ""] for nl, en in texts.items()),
                  key=lambda r: (r[0], r[1].lower()))
    ws = wb.active
    ws.title = "Schermteksten"
    sheet(ws, ["Scherm", "Nederlands", "Engels", "Duits", "Frans", "Opmerking"], rows, (22, 45, 45, 45, 45, 30))
    rows = [[src, nl, en, i18n_de.LIBMSG[src], i18n_fr.LIBMSG[src], ""] for src, (nl, en) in libmsg.TEMPLATES.items()]
    sheet(wb.create_sheet("Meldingen"), ["Sjabloon (code)", "Nederlands", "Engels", "Duits", "Frans", "Opmerking"],
          rows, (35, 45, 45, 45, 45, 30))
    info = wb.create_sheet("Uitleg")
    for line in ["Alle schermteksten (tab Schermteksten, per scherm) en foutmeldingen (tab Meldingen) naast elkaar.",
                 "Vaktermen volgen de goedgekeurde woordenlijst (dev/woordenlijst.py).",
                 "Opmerkingen in de groene kolom; leeg = akkoord. Plaatshouders als {n} worden ingevuld door de app."]:
        info.append([line])
        info.cell(info.max_row, 1).font = font
    info.column_dimensions["A"].width = 110
    wb.save(path)
    print(len(texts), "teksten +", len(libmsg.TEMPLATES), "meldingen ->", path)


if __name__ == "__main__":
    main(sys.argv[1])
