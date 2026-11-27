"""Foto's bij het bewaren als bijlage verkleinen en opschonen (fase 4 stap 6b, RB4/BB3).

- jpg/png met de lange kant boven ``MAX_SIDE`` (2.400 px) worden verkleind tot ``MAX_SIDE``; jpg met kwaliteit 85.
- Eerst rechtop gezet volgens de EXIF-oriëntatie; daarna gaat **alle metadata** weg (EXIF met GPS-locatie en
  toestel, XMP, IPTC, commentaar, png-tekstblokken). Het kleurprofiel (ICC) blijft, anders verschieten de kleuren.
- Hoeft er niet verkleind of gedraaid te worden, dan gaat de metadata er zonder opnieuw te comprimeren uit
  (jpg-segmenten en png-blokken weglaten): geen kwaliteitsverlies.
- Een verkleind bestand wordt alleen gebruikt als het kleiner is dan het opgeschoonde origineel. Het origineel
  wordt niet bewaard.
- PDF wordt nooit aangeraakt (digitaal ontvangen factuur in oorspronkelijke vorm).
- Meer dan ``MAX_PIXELS`` beeldpunten: geweigerd (bescherming tegen een "decompression bomb").

Pillow wordt pas bij gebruik geladen (komt mee met de webapp); zonder Pillow blijft een foto ongewijzigd.
"""

from __future__ import annotations

import io
import struct
from typing import Optional

from .errors import ValidationError

MAX_SIDE = 2400
JPEG_QUALITY = 85
#: grens aan het aantal beeldpunten (108-megapixelcamera's blijven eronder; Pillow weigert zelf pas boven ±179
#: miljoen)
MAX_PIXELS = 120_000_000

__all__ = ["MAX_SIDE", "MAX_PIXELS", "JPEG_QUALITY", "normalize", "strip_jpeg", "strip_png"]

#: jpg-segmenten die blijven: APP0 (JFIF), APP2 (ICC-profiel), APP14 (Adobe-kleurruimte); de rest van APP1–APP15
#: (EXIF, XMP, IPTC/Photoshop …) en COM (commentaar) gaan weg
_JPEG_KEEP_APP = {0xE0, 0xE2, 0xEE}
#: png-blokken met metadata
_PNG_DROP = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}


def strip_jpeg(data: bytes) -> bytes:
    """Metadata uit een jpg halen zonder opnieuw te comprimeren (segmenten vóór het beeld weglaten)."""
    if not data.startswith(b"\xff\xd8"):
        return data
    out = bytearray(b"\xff\xd8")
    i = 2
    n = len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            return data                       # onverwachte opbouw: niets wijzigen
        marker = data[i + 1]
        if marker == 0xFF:                    # opvulbyte
            i += 1
            continue
        if marker == 0xDA:                    # begin van het beeld: rest ongewijzigd
            out += data[i:]
            return bytes(out)
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            out += data[i:i + 2]
            i += 2
            continue
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        end = i + 2 + length
        if length < 2 or end > n:
            return data
        drop = (0xE1 <= marker <= 0xEF and marker not in _JPEG_KEEP_APP) or marker == 0xFE
        if not drop:
            out += data[i:end]
        i = end
    return data


def strip_png(data: bytes) -> bytes:
    """Tekst-, EXIF- en tijdblokken uit een png halen (het beeld blijft byte voor byte gelijk)."""
    sig = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(sig):
        return data
    out = bytearray(sig)
    i = len(sig)
    n = len(data)
    while i + 12 <= n:
        length = struct.unpack(">I", data[i:i + 4])[0]
        kind = data[i + 4:i + 8]
        end = i + 12 + length
        if end > n:
            return data
        if kind not in _PNG_DROP:
            out += data[i:end]
        i = end
        if kind == b"IEND":
            return bytes(out)
    return data


def _orientation(im) -> int:
    try:
        return int(im.getexif().get(0x0112, 1) or 1)
    except Exception:  # noqa: BLE001 — kapotte EXIF: als rechtop behandelen
        return 1


def normalize(data: bytes, kind: str) -> bytes:
    """Foto (``kind`` 'jpg' of 'png') verkleind en zonder metadata; pdf ongewijzigd."""
    if kind not in ("jpg", "png"):
        return data
    stripped = strip_jpeg(data) if kind == "jpg" else strip_png(data)
    try:
        from PIL import Image, ImageOps
    except ImportError:                       # zonder Pillow: alleen de metadata eruit
        return stripped
    try:
        im = Image.open(io.BytesIO(data))
        w, h = im.size
    except Image.DecompressionBombError:
        raise ValidationError("De afbeelding heeft te veel beeldpunten.")
    except Exception:                         # noqa: BLE001 — Pillow kan het niet lezen: alleen opschonen
        return stripped
    if w * h > MAX_PIXELS:
        raise ValidationError("De afbeelding heeft te veel beeldpunten.")
    turn = _orientation(im) != 1
    big = max(w, h) > MAX_SIDE
    if not (turn or big):
        return stripped
    icc = im.info.get("icc_profile")
    try:
        im = ImageOps.exif_transpose(im)
        if big:
            im.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        if kind == "jpg":
            if im.mode not in ("RGB", "L", "CMYK"):
                im = im.convert("RGB")
            im.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True, icc_profile=icc)
        else:
            im.save(buf, "PNG", optimize=True, icc_profile=icc)
    except Image.DecompressionBombError:
        raise ValidationError("De afbeelding heeft te veel beeldpunten.")
    except Exception:                         # noqa: BLE001 — opnieuw opslaan mislukt: opgeschoond origineel
        return stripped
    out = buf.getvalue()
    out = strip_jpeg(out) if kind == "jpg" else strip_png(out)
    # gedraaid moet altijd (zonder EXIF staat hij anders scheef); alleen verkleind: alleen als het scheelt
    if turn or len(out) < len(stripped):
        return out
    return stripped


def image_size(data: bytes) -> Optional[tuple]:
    """(breedte, hoogte) of None; voor toetsen en meldingen."""
    try:
        from PIL import Image
        return Image.open(io.BytesIO(data)).size
    except Exception:  # noqa: BLE001
        return None
