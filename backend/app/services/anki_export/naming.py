"""Deterministic naming helpers: deck IDs and export filenames.

genanki deck/model IDs are arbitrary but must be stable and fit in the range Anki
uses for these identifiers (roughly 32-bit signed positive integers in practice,
though genanki itself does not enforce a range). We reduce a stable hash of the
deck name into a 10-digit-ish positive integer range so re-exporting the same
deck name always produces the same deck ID (letting Anki update it in place),
while different names produce different IDs.
"""

import hashlib
import re

# Range chosen to match genanki's own convention of using large-but-bounded
# integers for model/deck IDs (see genanki examples, which use 10-digit values).
_DECK_ID_MIN = 1_000_000_000
_DECK_ID_MAX = 1_999_999_999
_DECK_ID_RANGE = _DECK_ID_MAX - _DECK_ID_MIN


def deck_id_for(deck_name: str) -> int:
    """Derive a stable, deterministic deck ID from a deck name.

    Same `deck_name` always yields the same ID, so re-exporting a job with an
    unchanged deck name updates the same Anki deck rather than creating a new one.
    """
    digest = hashlib.sha256(deck_name.encode("utf-8")).digest()
    offset = int.from_bytes(digest[:8], "big") % _DECK_ID_RANGE
    return _DECK_ID_MIN + offset


_SLUG_INVALID_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Lowercase, alnum + hyphens; falls back to "deck" if nothing usable remains."""
    slug = _SLUG_INVALID_RE.sub("-", value.strip().lower()).strip("-")
    return slug or "deck"
