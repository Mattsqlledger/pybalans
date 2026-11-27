"""Wachtwoorden (argon2), eenmalige codes, sessietokens en TOTP."""

from __future__ import annotations

import hashlib
import secrets
import time
from typing import Optional, Tuple

import pyotp
import segno
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()
# vergelijkt tegen deze hash als de gebruiker niet bestaat, zodat de responstijd niets verraadt
_DUMMY_HASH = _hasher.hash(secrets.token_hex(16))

# geen 0/O, 1/I/L: makkelijk over te nemen van papier of telefoon
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored: Optional[str], password: str) -> Tuple[bool, Optional[str]]:
    """(klopt, nieuwe hash als herhashen nodig is)."""
    try:
        _hasher.verify(stored or _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False, None
    if stored is None:
        return False, None
    return True, (_hasher.hash(password) if _hasher.check_needs_rehash(stored) else None)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_setup_code() -> str:
    """Eenmalige code, 3 blokken van 4 tekens (±59 bits)."""
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(12))
    return "-".join(raw[i:i + 4] for i in range(0, 12, 4))


def normalize_code(code: str) -> str:
    raw = "".join(ch for ch in code.upper() if ch.isalnum())
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, login: str, issuer: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=login, issuer_name=issuer)


def totp_qr_svg(uri: str) -> str:
    return segno.make(uri, error="m").svg_inline(scale=4, dark="#000", light="#fff")


def verify_totp(secret: str, code: str, last_step: Optional[int],
                now: Optional[float] = None) -> Optional[int]:
    """Geeft het tijdstap-nummer terug bij een geldige, nog niet gebruikte code.

    Eén stap speling naar voren en achteren; een code wordt maar één keer
    geaccepteerd (``last_step``).
    """
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != 6:
        return None
    totp = pyotp.TOTP(secret)
    now = time.time() if now is None else now
    step = int(now // totp.interval)
    for s in (step - 1, step, step + 1):
        if last_step is not None and s <= last_step:
            continue
        if secrets.compare_digest(totp.generate_otp(s), code):
            return s
    return None
