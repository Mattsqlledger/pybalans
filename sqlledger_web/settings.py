"""Instellingen van de webapp: TOML-bestand en/of omgevingsvariabelen.

Volgorde: standaardwaarde < bestand (``SQLLEDGER_CONFIG``) < omgevingsvariabele
``SQLLEDGER_<NAAM>``. Wachtwoorden kunnen ook uit een bestand komen
(``SQLLEDGER_DB_PASSWORD_FILE``, bruikbaar met Docker secrets).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Optional

from psycopg.conninfo import conninfo_to_dict, make_conninfo


@dataclass
class Settings:
    #: verbinding met de eigen app-database (gebruikers, rechten, sessies, logboek)
    app_dsn: str = "host=127.0.0.1 port=5432 dbname=sqlledger_app"
    #: standaardverbinding naar de administratie-databases; dbname/host/port per administratie
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_user: str = ""
    db_password: str = ""
    #: cookies alleen over https (zet uit voor lokaal gebruik in Safari over http)
    secure_cookies: bool = True
    #: sessie verloopt na zoveel seconden zonder activiteit
    session_timeout: int = 3600
    #: mislukte pogingen per account én per IP voordat er geblokkeerd wordt
    lockout_attempts: int = 5
    lockout_minutes: int = 15
    #: geldigheid van een eenmalige code in uren
    setup_code_hours: int = 24
    password_min_length: int = 12
    totp_issuer: str = "PyBalans"
    #: map voor eigen gegevens van de app: documentsjablonen en logo's per administratie (D5)
    data_dir: str = "~/.local/share/sqlledger"
    #: sleutel (Fernet, 44 tekens) voor opgeslagen geheimen zoals het SMTP-wachtwoord (D9); leeg = de app
    #: maakt eenmalig ``<data_dir>/secret.key`` (alleen leesbaar voor de eigenaar)
    secret_key: str = ""
    #: programma voor Systeem > Back-up maken en ``sqlledger-web backup`` (6d-1); zelfde of nieuwere hoofdversie
    #: dan de PostgreSQL-server
    pg_dump: str = "pg_dump"

    @classmethod
    def load(cls, path: Optional[str] = None, environ=None) -> "Settings":
        env = os.environ if environ is None else environ
        values: dict = {}
        path = path or env.get("SQLLEDGER_CONFIG")
        if path:
            with open(path, "rb") as fh:
                values.update(tomllib.load(fh))
        for f in fields(cls):
            key = f"SQLLEDGER_{f.name.upper()}"
            if key in env:
                values[f.name] = env[key]
            elif f"{key}_FILE" in env:
                values[f.name] = Path(env[f"{key}_FILE"]).read_text().strip()
        unknown = set(values) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"onbekende instelling(en): {', '.join(sorted(unknown))}")
        out = {}
        for f in fields(cls):
            if f.name not in values:
                continue
            v = values[f.name]
            if f.type == "bool" and isinstance(v, str):
                v = v.strip().lower() in ("1", "true", "ja", "yes", "on")
            elif f.type == "int":
                v = int(v)
            out[f.name] = v
        return cls(**out)

    def app_conninfo(self) -> str:
        """``app_dsn``, aangevuld met ``db_password`` als de DSN zelf geen wachtwoord heeft."""
        if self.db_password and "password" not in conninfo_to_dict(self.app_dsn):
            return make_conninfo(self.app_dsn, password=self.db_password)
        return self.app_dsn

    def company_conninfo(self, dbname: str, host: Optional[str] = None,
                         port: Optional[int] = None) -> dict:
        info = {"dbname": dbname, "host": host or self.db_host, "port": port or self.db_port}
        if self.db_user:
            info["user"] = self.db_user
        if self.db_password:
            info["password"] = self.db_password
        return info
