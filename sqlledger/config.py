"""Gebruikersinstellingen (het Perl-``%myconfig``)."""

from __future__ import annotations

from dataclasses import dataclass

NUMBERFORMATS = ("1,000.00", "1000.00", "1.000,00", "1000,00", "1'000.00", "100000")
DATEFORMATS = ("mm/dd/yy", "mm-dd-yy", "dd/mm/yy", "dd-mm-yy", "dd.mm.yy", "yyyy-mm-dd")


@dataclass
class UserConfig:
    """Instellingen van de ingelogde gebruiker.

    login        -- employee.login; bepaalt employee_id, locks en referenties
    numberformat -- formaat voor parse_amount/format_amount
    dateformat   -- formaat voor datumnotatie in nummersjablonen (<%DATE%> e.d.)
    countrycode  -- taalcode voor vertaalde rekeningomschrijvingen (translation)
    timeout      -- levensduur van een record-lock in seconden
    """

    login: str
    numberformat: str = "1,000.00"
    dateformat: str = "yyyy-mm-dd"
    countrycode: str = ""
    timeout: int = 3600

    def __post_init__(self) -> None:
        if self.numberformat not in NUMBERFORMATS:
            raise ValueError(f"onbekend numberformat: {self.numberformat!r}")
        if self.dateformat.lower() not in DATEFORMATS:
            raise ValueError(f"onbekend dateformat: {self.dateformat!r}")

    @property
    def short_login(self) -> str:
        """Login zonder ``@dataset``-deel (Perl: ``$login =~ s/@.*//``)."""
        return self.login.split("@", 1)[0]
