"""Deck-level chrome detection: which OCR text on every page is furniture
rather than content.

Computed once per job from the OCR pass the pipeline already runs, so it costs
no model call. It exists because a selector shown one page in isolation cannot
tell a running footer from a heading.

Detection is deliberately conservative: it would rather miss a footer than
suppress a recurring key term, because a suppressed term is invisible to the
model and cannot be recovered downstream.

Two limits worth knowing, both measured on the fixture corpus:

- Chrome is matched on *normalized exact* line text, so OCR variants of one
  footer do not group: the anatomy deck's school name is recognized variously as
  `MEDICI`, `MEDICIN`, and `MEDICINE`, and only lines repeated identically are
  caught.
- A page number is not repeated text, so repetition alone never catches it.
  Pure-digit and date-shaped lines are therefore treated as furniture outright.
"""

import re

from app.services.diagram_detection.ocr import OcrItem
from app.services.draft_validation import normalize_text

#: A line must appear on at least this fraction of a deck's pages to be chrome.
#: Swept against the fixture corpus: 0.4 through 0.6 select exactly the same
#: lines on both decks, so the midpoint is chosen for headroom on short decks.
CHROME_MIN_PAGE_RATIO = 0.5

#: Never call something chrome on the strength of a single repeat.
CHROME_MIN_PAGES = 2

#: Chrome is furniture, and furniture is short. This guard is what stops a long
#: content sentence that happens to recur from being suppressed.
MAX_CHROME_LINE_WORDS = 6

_DATE_LINE = re.compile(r"^\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}$")

#: A page needs at least this many pedagogical-verb list items to count as an
#: objectives slide on the strength of its list alone (a lone "1. Define ..."
#: is just a content line, not a learning-objectives slide).
MIN_OBJECTIVE_ITEMS = 2

#: Heading phrases that mark a slide as learning-objectives / agenda meta-content.
#: Matched against a whole line (normalized), so an incidental mention of the
#: word "objectives" mid-sentence does not trip it.
_OBJECTIVE_HEADINGS = frozenset(
    {
        "objectives",
        "learning objectives",
        "lecture objectives",
        "course objectives",
        "learning goals",
        "goals and objectives",
        "agenda",
        "outline",
        "overview",
    }
)

#: Verbs a learning objective typically opens with ("Define ...", "Explain ...").
#: Vision/PDF text is compared case-folded against this set.
_PEDAGOGICAL_VERBS = frozenset(
    {
        "define", "describe", "explain", "compare", "contrast", "discuss",
        "identify", "interpret", "list", "outline", "analyze", "evaluate",
        "summarize", "state", "distinguish", "recognize", "apply", "illustrate",
        "review", "predict", "understand", "differentiate", "classify",
    }
)

#: Leading list markers stripped before reading an item's first word: "1.", "2)",
#: "-", "•", "*", "a.", etc.
_LIST_MARKER = re.compile(r"^\s*(?:[\-\*•●▪]|\(?[0-9a-zA-Z][.)])\s+")


def _is_objectives_page(text: str) -> bool:
    """True when a page's PDF text reads as a learning-objectives / agenda slide.

    Qualifies two ways: a heading line like "Learning Objectives", or a list of
    at least `MIN_OBJECTIVE_ITEMS` items each opening with a pedagogical verb.
    A numbered content list ("1. Altered Calcium Handling") matches neither.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False

    for line in lines:
        if normalize_text(line) in _OBJECTIVE_HEADINGS:
            return True

    verb_items = 0
    for line in lines:
        item = _LIST_MARKER.sub("", line)
        first = item.split(maxsplit=1)[0].lower() if item else ""
        if first in _PEDAGOGICAL_VERBS:
            verb_items += 1
    return verb_items >= MIN_OBJECTIVE_ITEMS


def detect_objectives(texts: list[str]) -> tuple[str | None, frozenset[int]]:
    """Find the deck's learning-objectives slides in the per-page PDF text layer.

    Returns the objectives text (every qualifying slide's text, concatenated in
    page order) and the 1-indexed page numbers of those slides. When no page
    qualifies, returns `(None, frozenset())`. The text is context for the
    selector — "prefer targets that serve these objectives" — never a mask
    source itself, and the page numbers let the caller skip those pages.
    """
    objective_texts: list[str] = []
    pages: set[int] = set()
    for index, text in enumerate(texts):
        if _is_objectives_page(text):
            objective_texts.append(text.strip())
            pages.add(index + 1)
    if not objective_texts:
        return None, frozenset()
    return "\n\n".join(objective_texts), frozenset(pages)


def _is_page_furniture(text: str) -> bool:
    """True for a bare page number or a date, which repetition cannot catch.

    A page number differs on every page by definition, so it never clears the
    repetition threshold; it is furniture regardless.
    """
    stripped = text.strip()
    if not stripped:
        return False
    return stripped.isdigit() or bool(_DATE_LINE.match(stripped))


def is_chrome(text: str, chrome_lines: frozenset[str]) -> bool:
    """True when this line's text is furniture on this deck."""
    return _is_page_furniture(text) or normalize_text(text) in chrome_lines


def detect_chrome_lines(pages: list[list[OcrItem]]) -> frozenset[str]:
    """Normalized line texts repeated across enough of the deck to be furniture.

    Counts each line at most once per page, so a header repeated twice on one
    slide does not look like a header repeated on two slides.
    """
    if not pages:
        return frozenset()

    counts: dict[str, int] = {}
    for lines in pages:
        seen = {
            normalized
            for line in lines
            if (normalized := normalize_text(line.text))
            and len(normalized.split()) <= MAX_CHROME_LINE_WORDS
        }
        for normalized in seen:
            counts[normalized] = counts.get(normalized, 0) + 1

    threshold = max(CHROME_MIN_PAGES, round(CHROME_MIN_PAGE_RATIO * len(pages)))
    return frozenset(text for text, count in counts.items() if count >= threshold)
