"""Bijlagen die nog niet bij een boeking horen (fase 4 stap 4e).

Een bestand dat bij een nieuwe boeking of op Inkoop > Inkoopfactuur uploaden binnenkomt, staat
tot het boeken in de datamap (``uploads/``), zoals SQL-Ledger het in ``userspath`` zet tot de
boeking opgeslagen wordt (referencetmpfile). Het formulier draagt alleen een willekeurig token;
het bestand hoort bij één administratie en één gebruiker. Na het boeken (of "Verwijderen")
verdwijnt het; wat blijft liggen wordt na een dag opgeruimd.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from starlette.datastructures import FormData, UploadFile

from sqlledger.attachments import MAX_SIZE, TYPES, prepare, safe_filename, sha256

MAX_AGE = 24 * 3600
TOKEN = re.compile(r"[0-9a-f]{32}")


@dataclass
class PendingFile:
    token: str
    filename: str
    kind: str                   # pdf | png | jpg
    size: int
    sha256: str = ""            # van de inhoud zoals hij bewaard wordt (foto verkleind, stap 6b)

    @property
    def content_type(self) -> str:
        return TYPES[self.kind]

    @property
    def is_image(self) -> bool:
        return self.kind != "pdf"


def read_upload(form: FormData, name: str, limit: int = MAX_SIZE) -> Optional[Tuple[str, bytes]]:
    """Bestand uit een multipart-formulier: (naam, inhoud), of None als er niets gekozen is.
    Leest hoogstens ``limit`` + 1 byte (de controle meldt dan "groter dan … MB")."""
    f = form.get(name)
    if not isinstance(f, UploadFile) or not (f.filename or "").strip():
        return None
    f.file.seek(0)
    return f.filename, f.file.read(limit + 1)


class PendingFiles:
    def __init__(self, data_dir: str):
        self.dir = Path(data_dir) / "uploads"

    def _paths(self, token: str) -> Tuple[Path, Path]:
        return self.dir / f"{token}.bin", self.dir / f"{token}.json"

    def save(self, company_id: int, login: str, filename: str, data: bytes, limit: int = MAX_SIZE) -> PendingFile:
        """Controleren (ValidationError; ``limit`` = maximale uploadgrootte in bytes, van het origineel) en
        bewaren zoals het archiefstuk straks wordt: een foto meteen verkleind en zonder metadata (RB4), zodat
        het voorbeeld klopt en de hash voor de dubbelcontrole (RB5) vastligt. Ruimt meteen oude bestanden op."""
        kind, data = prepare(filename, data, limit)
        self.cleanup()
        self.dir.mkdir(parents=True, exist_ok=True)
        token = secrets.token_hex(16)
        pf = PendingFile(token=token, filename=safe_filename(filename, kind), kind=kind, size=len(data),
                         sha256=sha256(data))
        content, meta = self._paths(token)
        content.write_bytes(data)
        meta.write_text(json.dumps({"company_id": company_id, "login": login, "filename": pf.filename,
                                    "kind": kind, "size": pf.size, "sha256": pf.sha256}))
        return pf

    def get(self, token: str, company_id: int, login: str) -> Optional[PendingFile]:
        """Het bestand bij dit token, alleen voor dezelfde administratie en gebruiker."""
        if not TOKEN.fullmatch(token or ""):
            return None
        content, meta = self._paths(token)
        try:
            m = json.loads(meta.read_text())
        except (OSError, ValueError):
            return None
        if m.get("company_id") != company_id or m.get("login") != login or not content.exists():
            return None
        return PendingFile(token=token, filename=m["filename"], kind=m["kind"], size=m["size"],
                           sha256=m.get("sha256") or sha256(content.read_bytes()))

    def data(self, pf: PendingFile) -> bytes:
        return self._paths(pf.token)[0].read_bytes()

    def discard(self, token: str) -> None:
        if TOKEN.fullmatch(token or ""):
            for p in self._paths(token):
                p.unlink(missing_ok=True)

    def cleanup(self, max_age: int = MAX_AGE) -> None:
        if not self.dir.is_dir():
            return
        limit = time.time() - max_age
        for p in self.dir.iterdir():
            try:
                if p.stat().st_mtime < limit:
                    p.unlink()
            except OSError:
                pass
