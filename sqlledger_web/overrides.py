"""Eigen woorden per installatie (TL9): ``<data_dir>/vertalingen/<taal>.txt``.

Zoals de ``locale``-bestanden van SQL-Ledger, maar los van de code, zodat een update ze niet overschrijft.
Per regel ``Nederlandse tekst = eigen tekst``; ``[menu] Valuta = …`` voor een tekst in een context;
``#`` aan het begin = commentaar; ``\\n`` in de eigen tekst = nieuwe regel. De sleutel is altijd de Nederlandse
tekst zoals in de code (ook voor ``nl.txt``, om een Nederlands woord aan te passen), en voor meldingen uit de
bibliotheek de Nederlandse tekst van het sjabloon in ``libmsg.py``.

Een regel wordt overgeslagen (en gemeld) als de tekst niet bestaat, de plaatshouders (``{n}``) niet gelijk zijn
of hij te lang is. Het bestand wordt opnieuw gelezen zodra het verandert (per verzoek één ``stat``).
``pybalans translations <taal>`` controleert een bestand en kan alle teksten als startpunt afdrukken.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from . import i18n

log = logging.getLogger("sqlledger_web.overrides")

MAX_BYTES = 1024 * 1024
MAX_TEXT = 2000
CTX_RE = re.compile(r"^\[(?P<ctx>[a-z]+)\]\s*")
FIELDS_RE = re.compile(r"\{(\w*)\}")


class Loaded(NamedTuple):
    texts: Dict[str, str]
    contexts: Dict[str, Dict[str, str]]
    problems: List[str]


#: per taal: (pad, mtime_ns, grootte) van het gelezen bestand
_seen: Dict[str, Tuple[str, int, int]] = {}


def path_for(data_dir, lang: str) -> Path:
    if lang not in i18n.LANGUAGES:
        raise ValueError(lang)
    return Path(data_dir).expanduser() / "vertalingen" / f"{lang}.txt"


def known_texts() -> Dict[str, None]:
    """Alle Nederlandse teksten die een vertaling kunnen krijgen (schermteksten + meldingssjablonen)."""
    from . import libmsg
    keys = dict.fromkeys(i18n.CATALOGS["en"])
    keys.update(dict.fromkeys(nl for nl, __ in libmsg.TEMPLATES.values()))
    return keys


def parse(text: str) -> Loaded:
    known = known_texts()
    contexts_known = i18n.CONTEXTS["en"]
    texts: Dict[str, str] = {}
    contexts: Dict[str, Dict[str, str]] = {}
    problems: List[str] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = CTX_RE.match(line)
        ctx = m.group("ctx") if m else None
        body = line[m.end():] if m else line
        # een Nederlandse tekst kan zelf " = " bevatten: splits waar het linkerdeel een bestaande tekst is
        splits = [i.start() for i in re.finditer(r" = ", body)]
        if not splits:
            problems.append(f"regel {n}: geen 'tekst = vertaling'")
            continue
        pool = contexts_known.get(ctx, {}) if ctx else known
        at = next((i for i in splits if body[:i].strip() in pool), splits[0])
        key, value = body[:at].strip(), body[at + 3:].strip().replace("\\n", "\n")
        if not value:
            problems.append(f"regel {n}: lege vertaling")
            continue
        if len(value) > MAX_TEXT:
            problems.append(f"regel {n}: vertaling langer dan {MAX_TEXT} tekens")
            continue
        if ctx and key not in contexts_known.get(ctx, {}):
            problems.append(f"regel {n}: onbekende tekst in context [{ctx}]: {key!r}")
            continue
        if not ctx and key not in known:
            problems.append(f"regel {n}: onbekende tekst: {key!r}")
            continue
        if sorted(FIELDS_RE.findall(key)) != sorted(FIELDS_RE.findall(value)):
            problems.append(f"regel {n}: plaatshouders verschillen van {key!r}")
            continue
        target = contexts.setdefault(ctx, {}) if ctx else texts
        if key in target:
            problems.append(f"regel {n}: {key!r} staat er al eerder in (deze regel telt)")
        target[key] = value
    return Loaded(texts, contexts, problems)


def read(data_dir, lang: str) -> Optional[Loaded]:
    path = path_for(data_dir, lang)
    if not path.is_file():
        return None
    if path.stat().st_size > MAX_BYTES:
        return Loaded({}, {}, [f"{path}: groter dan {MAX_BYTES // 1024} kB, niet gebruikt"])
    try:
        return parse(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return Loaded({}, {}, [f"{path}: geen UTF-8, niet gebruikt"])


def refresh(data_dir, lang: str) -> None:
    """Lees ``<lang>.txt`` opnieuw als het bestand nieuw, gewijzigd of weg is (aangeroepen per verzoek)."""
    try:
        path = path_for(data_dir, lang)
        st = path.stat()
        stamp = (str(path), st.st_mtime_ns, st.st_size)
    except (OSError, ValueError):
        stamp = None
    if _seen.get(lang) == stamp:
        return
    _seen[lang] = stamp
    loaded = read(data_dir, lang) if stamp else None
    if not loaded:
        i18n.OVERRIDES.pop(lang, None)
        return
    for p in loaded.problems:
        log.warning("vertalingen/%s.txt: %s", lang, p)
    i18n.OVERRIDES[lang] = (loaded.texts, loaded.contexts)


def starter(lang: str) -> str:
    """Alle teksten als commentaarregels ``# Nederlands = huidige tekst``, als startpunt voor een eigen bestand."""
    from . import libmsg
    token = i18n.current_language.set(lang)
    try:
        lines = [f"# Eigen woorden voor {i18n.LANGUAGES[lang]}: haal het hekje weg en pas de tekst na het isgelijkteken aan.",
                 "# Regels die u niet wijzigt kunt u beter weglaten (dan krijgt u na een update de nieuwe tekst).", ""]
        esc = lambda s: s.replace("\n", "\\n")  # noqa: E731
        for ctx, texts in sorted(i18n.CONTEXTS["en"].items()):
            for nl in texts:
                lines.append(f"# [{ctx}] {esc(nl)} = {esc(i18n.pgettext(ctx, nl))}")
        for nl in i18n.CATALOGS["en"]:
            lines.append(f"# {esc(nl)} = {esc(i18n.gettext(nl))}")
        for source, (nl, en) in libmsg.TEMPLATES.items():
            other = i18n.LIBMSG.get(lang)
            current = nl if lang == "nl" else en if lang == "en" else other.LIBMSG.get(source, en)
            lines.append(f"# {esc(nl)} = {esc(current)}")
        return "\n".join(lines) + "\n"
    finally:
        i18n.current_language.reset(token)
