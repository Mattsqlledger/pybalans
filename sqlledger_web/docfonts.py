"""Meegeleverde lettertypen voor de documenten (huisstijl, keuze 1; HUISSTIJL_ANALYSE.md HS3).

Vijf lettertypen onder de SIL Open Font License 1.1, **onveranderd** meegeleverd in ``sqlledger_web/fonts/<sleutel>/``
met hun licentiebestand (zo blijven de Reserved Font Names van de makers buiten spel). In de PDF komt alleen het
gekozen lettertype, en daarvan alleen de gebruikte tekens (WeasyPrint maakt een subset).

"Standaard" (sleutel ``""``) is de systeem-Helvetica van de standaardopmaak, niet ingesloten.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Tuple

FONT_DIR = Path(__file__).parent / "fonts"
#: lettertypefamilie van de standaardopmaak (doctemplates/standard/_base.html)
STANDARD_FAMILY = '"Helvetica Neue", Helvetica, Arial, sans-serif'


@dataclass(frozen=True)
class Font:
    label: str
    generic: str                                # sans-serif / serif
    files: Tuple[Tuple[str, str], ...]          # (bestand, font-weight)
    author: str
    copyright: str
    source: str                                 # waar het vandaan komt
    license_file: str = "OFL.txt"

    @property
    def family(self) -> str:
        """Interne familienaam in de documenten (botst niet met een lettertype dat op de server staat)."""
        return "sldoc-" + self.files[0][0].split("-")[0].lower()


FONTS: Dict[str, Font] = {
    "liberation-sans": Font(
        "Liberation Sans", "sans-serif", (("LiberationSans-Regular.ttf", "400"), ("LiberationSans-Bold.ttf", "700")),
        "Red Hat, Inc. (gebaseerd op Arimo van Google)",
        "Digitized data copyright (c) 2010 Google Corporation; copyright (c) 2012 Red Hat, Inc.",
        "https://github.com/liberationfonts/liberation-fonts (2.1.5)"),
    "inter": Font(
        "Inter", "sans-serif", (("Inter-Variable.ttf", "100 900"),),
        "The Inter Project Authors (Rasmus Andersson)",
        "Copyright 2020 The Inter Project Authors", "https://github.com/rsms/inter (via google/fonts)"),
    "source-sans-3": Font(
        "Source Sans 3", "sans-serif", (("SourceSans3-Variable.ttf", "200 900"),),
        "Adobe (Paul D. Hunt)", "Copyright 2010-2020 Adobe", "https://github.com/adobe-fonts/source-sans (via google/fonts)"),
    "source-serif-4": Font(
        "Source Serif 4", "serif", (("SourceSerif4-Variable.ttf", "200 900"),),
        "The Source Serif 4 Project Authors (Frank Grießhammer, Adobe)",
        "Copyright 2014 The Source Serif 4 Project Authors",
        "https://github.com/adobe-fonts/source-serif (via google/fonts)"),
    "lora": Font(
        "Lora", "serif", (("Lora-Variable.ttf", "400 700"),),
        "The Lora Project Authors (Olga Karpushina, Cyreal)", "Copyright 2011 The Lora Project Authors",
        "https://github.com/cyrealtype/Lora-Cyrillic (via google/fonts)"),
}


def family(key: str) -> str:
    """CSS ``font-family`` voor een keuze (``""`` = standaard)."""
    f = FONTS.get(key)
    return f'"{f.family}", {f.generic}' if f else STANDARD_FAMILY


@lru_cache(maxsize=8)
def _data_url(key: str, name: str) -> str:
    return "data:font/ttf;base64," + base64.b64encode((FONT_DIR / key / name).read_bytes()).decode()


def face_css(key: str, url_prefix: Optional[str] = None) -> str:
    """``@font-face``-regels voor een keuze; leeg bij standaard. Zonder ``url_prefix`` als data-URL (de PDF-renderer
    laat alleen data:-URL's toe); met ``url_prefix`` (bv. ``/lettertypen/``) voor het voorbeeld in de browser."""
    f = FONTS.get(key)
    if not f:
        return ""
    out = []
    for name, weight in f.files:
        src = f"{url_prefix}{key}/{name}" if url_prefix is not None else _data_url(key, name)
        out.append(f'@font-face {{ font-family: "{f.family}"; src: url({src}) format("truetype"); '
                   f"font-weight: {weight}; font-style: normal; }}")
    return "\n".join(out)


def font_file(key: str, name: str) -> Optional[Path]:
    """Pad van een meegeleverd lettertype- of licentiebestand (alleen bekende namen)."""
    f = FONTS.get(key)
    if not f or name not in {n for n, _ in f.files} | {f.license_file}:
        return None
    p = FONT_DIR / key / name
    return p if p.is_file() else None


def license_text(key: str) -> str:
    p = font_file(key, FONTS[key].license_file)
    return p.read_text(encoding="utf-8", errors="replace") if p else ""
