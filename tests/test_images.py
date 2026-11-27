"""Foto's verkleinen en opschonen bij het bewaren als bijlage (fase 4 stap 6b, RB4/BB3)."""

import io

import pytest
from PIL import Image, ImageCms

from sqlledger.errors import ValidationError
from sqlledger.images import MAX_SIDE, normalize, strip_jpeg, strip_png

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def photo(w, h, fmt="JPEG", orientation=None, gps=True, icc=False, text=False, noise=True):
    """Testfoto met (optioneel) EXIF-oriëntatie, GPS, kleurprofiel of png-tekst."""
    im = Image.new("RGB", (w, h), (200, 120, 40))
    if noise:                       # wat structuur, anders comprimeert alles tot niets
        im = Image.effect_noise((w, h), 40).convert("RGB")
    kw = {}
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation
    if gps:
        exif[0x010F] = "Telefoonmerk"                          # toestel
        exif[0x8825] = {1: "N", 2: (52.0, 5.0, 0.0)}          # GPS-blok
    if orientation or gps:
        kw["exif"] = exif.tobytes()
    if icc:
        kw["icc_profile"] = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    if fmt == "PNG" and text:
        from PIL.PngImagePlugin import PngInfo
        info = PngInfo()
        info.add_text("Comment", "geheime locatie")
        kw["pnginfo"] = info
    buf = io.BytesIO()
    im.save(buf, fmt, **({"quality": 95} if fmt == "JPEG" else {}), **kw)
    return buf.getvalue()


def opened(data):
    return Image.open(io.BytesIO(data))


def has_exif(data):
    return bool(opened(data).info.get("exif")) or b"Exif\x00\x00" in data[:70000]


def test_pdf_untouched():
    assert normalize(PDF, "pdf") is PDF


def test_big_jpeg_resized_rotated_and_cleaned():
    src = photo(4000, 3000, orientation=6)                  # telefoon liggend opgeslagen, staand bedoeld
    out = normalize(src, "jpg")
    im = opened(out)
    assert im.format == "JPEG" and im.size == (1800, 2400)  # rechtop en lange kant 2.400
    assert not has_exif(out) and b"Telefoonmerk" not in out
    assert len(out) < len(src)


def test_small_jpeg_metadata_removed_without_recompressing():
    src = photo(800, 600)
    out = normalize(src, "jpg")
    assert not has_exif(out) and b"Telefoonmerk" not in out
    sos = src.index(b"\xff\xda")
    assert out.endswith(src[sos:])                           # beelddata byte voor byte gelijk
    assert opened(out).size == (800, 600)


def test_small_jpeg_without_metadata_unchanged():
    src = photo(800, 600, gps=False)
    assert normalize(src, "jpg") == src


def test_rotated_small_jpeg_is_turned():
    out = normalize(photo(600, 400, orientation=8, gps=False), "jpg")
    assert opened(out).size == (400, 600) and not has_exif(out)


def test_icc_profile_kept():
    out = normalize(photo(3000, 1000, icc=True), "jpg")
    assert opened(out).info.get("icc_profile") and opened(out).size == (2400, 800)
    small = normalize(photo(300, 100, icc=True), "jpg")
    assert opened(small).info.get("icc_profile")


def test_png_stays_png_resized_and_cleaned():
    src = photo(3600, 1200, fmt="PNG", gps=False, text=True)
    out = normalize(src, "png")
    im = opened(out)
    assert im.format == "PNG" and im.size == (2400, 800) and b"geheime locatie" not in out
    small = photo(400, 300, fmt="PNG", gps=False, text=True)
    out = normalize(small, "png")
    assert b"geheime locatie" not in out and opened(out).size == (400, 300)
    assert strip_png(out) == out


def test_resized_only_used_when_smaller():
    """Een groot maar eenvoudig plaatje (weinig detail) kan als png na verkleinen groter worden; dan het
    opgeschoonde origineel."""
    src = photo(2600, 100, fmt="PNG", gps=False, noise=False)
    out = normalize(src, "png")
    assert len(out) <= len(strip_png(src))


def test_too_many_pixels_refused():
    im = Image.new("1", (12000, 11000))                       # 132 miljoen beeldpunten
    buf = io.BytesIO()
    im.save(buf, "PNG")
    with pytest.warns(Image.DecompressionBombWarning), pytest.raises(ValidationError):
        normalize(buf.getvalue(), "png")


def test_strip_functions_leave_other_data_alone():
    assert strip_jpeg(b"\xff\xd8garbage") == b"\xff\xd8garbage"
    assert strip_png(b"not a png") == b"not a png"


def test_max_side():
    assert MAX_SIDE == 2400
