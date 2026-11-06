"""ISO 3166-1 country codes with names in several languages (data/countries.json,
generated from the Unicode CLDR via Intl.DisplayNames). Used for the country
picker in the UI and to recognise printed country names ("COSTA RICA", "España")."""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "countries.json"

# common spellings not covered by the official names
ALIASES = {
    "usa": "US",
    "u.s.a.": "US",
    "vereinigte staaten von amerika": "US",
    "united states of america": "US",
    "uk": "GB",
    "england": "GB",
    "great britain": "GB",
    "grossbritannien": "GB",
    "holland": "NL",
    "tschechische republik": "CZ",
    "czech republic": "CZ",
    "turkiye": "TR",
    "turkei": "TR",
    "russland": "RU",
    "korea": "KR",
    "south korea": "KR",
    "suedkorea": "KR",
    "brd": "DE",
}


def _norm(text: str) -> str:
    """Lower case, accents removed, single spaces: "SAN JOSÈ" == "san jose"."""
    text = unicodedata.normalize("NFKD", text.strip().lower().replace("ß", "ss"))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip(" .,;")


@lru_cache(maxsize=1)
def countries() -> list[dict]:
    """[{code, name (German), names}] sorted by German name."""
    return json.loads(DATA.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _by_name() -> dict[str, str]:
    index = {}
    for c in countries():
        for n in c["names"]:
            index[_norm(n)] = c["code"]
            index[_norm(n).replace("ae", "a").replace("oe", "o").replace("ue", "u")] = c["code"]
    index.update({_norm(k): v for k, v in ALIASES.items()})
    return index


def country_code(text: str) -> str | None:
    """ISO code for a printed country name or code ("COSTA RICA" -> "CR", "Österreich" -> "AT")."""
    t = _norm(text)
    if not t:
        return None
    if len(t) == 2 and t.isalpha():
        code = t.upper()
        return code if any(c["code"] == code for c in countries()) else None
    return _by_name().get(t) or _by_name().get(t.replace("ae", "a").replace("oe", "o").replace("ue", "u"))
