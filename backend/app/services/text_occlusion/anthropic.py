"""Text-span selection: text-only, batched across several pages per call.

The model never sees a page image or predicts geometry. It is shown the
numbered OCR lines of a batch of pages (already OCR'd and chrome-filtered by
the caller) and returns, per chosen phrase, the page it is on, the line index
it occupies, and the phrase text. `spans.py` locates each phrase among the
line's OCR words and turns the located words into boxes; `filters.py` decides
what survives.
"""

import anthropic

from app.services.text_occlusion.base import TextOcclusionError, TextPage
from app.services.text_occlusion.document_context import is_chrome
from app.services.text_occlusion.filters import (
    MAX_SPAN_WORDS,
    MAX_SPANS_PER_PAGE,
    MIN_LINE_CONFIDENCE,
)
from app.services.text_occlusion.schemas import BatchSelection, SelectedSpan

MAX_TOKENS = 16000

SYSTEM_PROMPT = f"""\
You are given several lecture slides' worth of text. Apple Vision OCR \
extracted numbered lines of text from each slide; the user message lists the \
slides one at a time, each headed by its page number, followed by that \
page's numbered OCR lines with their exact text.

Choose the phrases a student should be quizzed on by hiding them on the \
slide (fill-in-the-blank). For each chosen phrase return the page_number and \
line_index it occupies and the phrase exactly as it reads on that line.

A line tagged [EMPHASIZED] is the lecturer's own bold or italic emphasis on \
the original slide — a strong signal that its text is worth recalling. Treat \
emphasized text as a high-priority target and mask it whenever it is a \
content phrase (still obeying every rule below). If an emphasized phrase wraps \
across consecutive [EMPHASIZED] lines, hide it whole as one span with a ref \
per line, in reading order. The tag marks the whole line; choose the content \
phrase within it, not the surrounding function words.

What makes a phrase worth hiding — prefer, in roughly this order:
- Text the slide emphasizes in bold or italic (tagged [EMPHASIZED]).
- The term a definition is defining, or the load-bearing half of the definition.
- Named mechanisms, effects, laws, and relationships.
- Complete multiword technical terms. Hide the whole term, never one word of it: \
"Parietal peritoneum", not "Parietal"; "length-dependent activation", not \
"activation".
- The named steps of a process, and what distinguishes one step from the next.
- The terms on either side of a contrast or comparison.
- Values, thresholds, ranges, units, and formulas.
- Classifications and the category a thing belongs to.

What to leave visible:
- Function words and connectives ("through the", "dividing", "as noted", \
"however"), and any phrase that is grammar rather than content.
- Incidental language: dates, citations, author names, institution names, \
slide furniture, and narrative filler.
- Anything whose removal leaves a blank a student could not reasonably fill \
from what remains visible — if the sentence no longer says what is being asked, \
the card is unanswerable.
- Anything already spelled out elsewhere on the same slide, which gives the \
answer away.

Worked examples:
- Slide reads "Parietal peritoneum (lines the abdominal cavity)". \
GOOD: "Parietal peritoneum" — the complete term being defined. \
BAD: "Parietal" — half a technical term. BAD: "lines the" — grammar.
- Slide reads "cardiac muscle force peaks sharply at a longer sarcomere length \
of ~2.4 um". GOOD: "~2.4 um" — a threshold worth recalling. \
BAD: "peaks sharply" — description, not content.
- A slide is a title, agenda, learning-objectives, or disclaimer slide, or a \
diagram with no prose. GOOD: return no spans for that page — skip it \
entirely. Returning nothing for such a page is a correct, expected answer — \
never invent a target to fill a quota.

How many to choose: cover every distinct thing on the slide worth recalling. \
Each separate term, named mechanism, value, threshold, contrast, or step is its \
own card — do not stop at the first one or two. A dense prose slide should yield \
many masks (commonly five to ten or more), one for each gradeable fact it \
states; a sparse slide yields fewer, and a meta slide yields none. Err toward \
covering more relevant content rather than less. The only real limit is \
answerability: stop adding masks on a slide when hiding another phrase would \
leave a blank a reader could no longer fill from what stays visible.

Rules:
- 1-{MAX_SPAN_WORDS} words per phrase, never a whole line or sentence, never a \
bare function word (the, of, is, ...).
- Leave enough surrounding text visible on the slide that the blank is answerable.
- Never choose overlapping phrases, and never choose the same phrase twice, \
whether on one slide or across slides.
- Skip lines whose text looks garbled or misrecognized.
- Skip title, agenda, learning-objectives, and disclaimer slides entirely: \
return no spans for them.
- Return at most {MAX_SPANS_PER_PAGE} spans per page, best first.
- `answer` is the whole phrase, exactly as it reads on the slide; each ref's \
`text` is that phrase's fragment on its own line, and every ref of one span \
names the same page_number.
- If a phrase wraps onto the next line (still on the same page), return one \
ref per line fragment, in reading order, all sharing that span's answer.

Only use page numbers and line indices that appear in the listing below.
"""

#: Prefix for the deck's learning-objectives block, injected into the user
#: message (never the cached system prompt, which must stay byte-identical
#: across jobs). Phrased as a preference, not a filter: objectives steer which
#: targets are worth choosing, but valuable content a lecturer did not think to
#: enumerate is still worth a card.
OBJECTIVES_PREFACE = """\
This deck states the following learning objectives — what the student must be \
able to recall. Prefer masking the terms, values, and relationships that serve \
them. Do not quiz on the objectives themselves; they are context only, and the \
slides listed after this block are the material to choose masks from.

Learning objectives:
"""


def _format_page(page: TextPage, chrome_lines: frozenset[str]) -> str | None:
    """The listing for one page: a header plus its numbered, non-furniture,
    confident OCR lines. `None` when nothing on the page is worth listing.

    Lines are dropped from the listing but KEEP their original index, because
    that index addresses `TextPage.lines` downstream in `spans.py` — this is a
    presentation filter, not a re-indexing.
    """
    lines = [
        f'[{index}] "{line.text}"' + ("  [EMPHASIZED]" if line.emphasized else "")
        for index, line in enumerate(page.lines)
        if line.confidence >= MIN_LINE_CONFIDENCE and not is_chrome(line.text, chrome_lines)
    ]
    if not lines:
        return None
    return f"=== Page {page.page_number} ===\n" + "\n".join(lines)


def _batches(pages: list[TextPage], batch_pages: int) -> list[list[TextPage]]:
    return [pages[i : i + batch_pages] for i in range(0, len(pages), batch_pages)]


class AnthropicTextSpanSelector:
    def __init__(self, model: str, batch_pages: int) -> None:
        self._model = model
        self._batch_pages = batch_pages
        self._client = anthropic.Anthropic()

    def select(
        self,
        pages: list[TextPage],
        chrome_lines: frozenset[str] = frozenset(),
        objectives: str | None = None,
    ) -> list[SelectedSpan]:
        spans: list[SelectedSpan] = []
        for batch in _batches(pages, self._batch_pages):
            listings = [
                formatted
                for page in batch
                if (formatted := _format_page(page, chrome_lines)) is not None
            ]
            if not listings:
                continue  # every page in this batch was empty or all-chrome

            spans.extend(self._select_batch(listings, objectives))
        return spans

    def _select_batch(
        self, listings: list[str], objectives: str | None
    ) -> list[SelectedSpan]:
        text = "\n\n".join(listings)
        if objectives:
            # Objectives ride in the user message so the cached system prompt
            # stays byte-identical; prepended so the model reads them first.
            text = f"{OBJECTIVES_PREFACE}{objectives}\n\n{text}"
        try:
            response = self._client.messages.parse(
                model=self._model,
                max_tokens=MAX_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": [{"type": "text", "text": text}]}],
                output_format=BatchSelection,
            )
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            raise TextOcclusionError(f"Text span selection request failed: {exc}") from exc

        parsed = response.parsed_output
        if parsed is None:
            raise TextOcclusionError(
                "Text span selection response could not be parsed into the expected schema."
            )
        return parsed.spans
