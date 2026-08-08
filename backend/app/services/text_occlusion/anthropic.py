"""Text-span selection: low-resolution visual context plus exact OCR text.

The model sees a cheap slide thumbnail so it can understand hierarchy, layout,
and emphasis, but it never predicts geometry or transcribes the answer from the
image. It must choose exact phrases from the numbered OCR lines shown beside the
preview. `spans.py` maps those phrases back to OCR word boxes and `filters.py`
applies structural safety checks.
"""

import base64
import logging
from io import BytesIO

import anthropic
from anthropic.types import ImageBlockParam, TextBlockParam
from PIL import Image

from app.services.text_occlusion.base import TextOcclusionError, TextPage
from app.services.text_occlusion.document_context import is_chrome
from app.services.text_occlusion.filters import (
    MAX_SPAN_WORDS,
    MAX_SPANS_PER_PAGE,
    MIN_LINE_CONFIDENCE,
)
from app.services.text_occlusion.schemas import BatchSelection, SelectedSpan

MAX_TOKENS = 16000
THUMBNAIL_MAX_EDGE_PX = 512
THUMBNAIL_JPEG_QUALITY = 70

# Uvicorn configures this logger at INFO in both foreground and detached dev
# modes, so per-batch token usage is visible in the backend log without changing
# the application's global logging policy.
logger = logging.getLogger("uvicorn.error")

SYSTEM_PROMPT = f"""\
You choose high-quality fill-in-the-blank masks for lecture slides. For each \
page, the user message provides a low-resolution preview followed by numbered \
Apple Vision OCR lines containing the exact selectable text.

Use the preview only to understand hierarchy, layout, columns, diagrams, and \
visual emphasis. Never transcribe a mask from the preview. Every returned ref \
must quote exact text from one of that page's numbered OCR lines; deterministic \
code turns those refs into mask geometry.

The same selected masks are used in two modes: one card per mask, or every mask \
on the page hidden together. Therefore choose one shared set that remains \
answerable even when all selected masks on the page are hidden simultaneously. \
Do not select two facts when hiding either one removes the cue needed to recall \
the other.

Select only material whose recall helps a student learn the lecture:
- The term a definition is defining, while leaving its explanatory cue visible.
- Named mechanisms, effects, laws, and relationships.
- Complete multiword technical terms. Hide the whole term, never one word of it: \
  "Parietal peritoneum", not "Parietal"; "length-dependent activation", not \
  "activation".
- The named steps of a process, and what distinguishes one step from the next.
- The terms on either side of a contrast or comparison.
- Values, thresholds, ranges, units, and formulas.
- Classifications and the category a thing belongs to.
- Content the lecturer visually emphasizes, when it is one of the targets above.

Never select:
- Function words, connectives, predicate fragments, or prose that merely \
  completes the sentence. Bad masks include "through the", "dividing", \
  "is a function of", "only mechanism", "increased preload produces", and \
  "blood to back up".
- Incidental language: dates, citations, author names, institution names, \
  slide furniture, and narrative filler.
- Anything whose removal leaves a blank a student could not reasonably fill \
  from what remains visible after every selected mask is hidden.
- A definition sentence or descriptive clause as one large answer. Prefer the \
  named term, value, or relationship it teaches.

Term boundaries and repeated answers:
- Function words inside one canonical term stay inside its mask: \
  "Law of the Heart" is one complete term.
- Coordinated concepts become separate masks and the conjunction stays visible: \
  in "Rest Potentiation and Post-Extrasystolic Potentiation", select \
  "Rest Potentiation" and "Post-Extrasystolic Potentiation" separately, not \
  the whole phrase and never "and".
- Select a concept only once per page. Put its best-context occurrence in \
  `refs`. Put every other visible occurrence or obvious equivalent that would \
  reveal the answer in `leakage_refs`, so it is hidden on the same card. If \
  hiding all revealing occurrences removes the useful recall cue, omit the \
  concept instead.

Examples:
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

There is no mask quota. Select every distinct high-value learning target that \
passes the rules, and nothing merely to increase the count. A dense slide may \
produce several masks, a sparse slide fewer, and a meta slide none. Quality, \
collective answerability, and non-redundancy always matter more than coverage.

Rules:
- 1-{MAX_SPAN_WORDS} words per phrase, never a whole line or sentence, never a \
  bare function word (the, of, is, ...).
- Leave enough surrounding text visible after all chosen masks are applied.
- Never choose overlapping learning targets or the same concept twice on one slide. \
  Repetition on different slides is allowed and often pedagogically useful.
- Skip lines whose text looks garbled or misrecognized.
- Skip title, agenda, learning-objectives, and disclaimer slides entirely: \
return no spans for them.
- Return at most {MAX_SPANS_PER_PAGE} spans per page, best first.
- Each span names its `page_number` once. `refs` contains the answer's best-context \
  occurrence; each ref's `text` is that occurrence's fragment on its own line. \
  `leakage_refs` contains other revealing occurrences on the same page.
- If a phrase wraps onto the next line (still on the same page), return one \
  ref per line fragment in `refs`, in reading order.

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
        f'[{index}] "{line.text}"'
        for index, line in enumerate(page.lines)
        if line.confidence >= MIN_LINE_CONFIDENCE and not is_chrome(line.text, chrome_lines)
    ]
    if not lines:
        return None
    return f"=== Page {page.page_number} ===\n" + "\n".join(lines)


def _batches(pages: list[TextPage], batch_pages: int) -> list[list[TextPage]]:
    return [pages[i : i + batch_pages] for i in range(0, len(pages), batch_pages)]


def _thumbnail_block(image_path: str) -> ImageBlockParam:
    """A cheap layout preview; OCR text remains the source of every answer."""
    with Image.open(image_path) as raw:
        thumbnail = raw.convert("RGB")
        thumbnail.thumbnail(
            (THUMBNAIL_MAX_EDGE_PX, THUMBNAIL_MAX_EDGE_PX), Image.Resampling.LANCZOS
        )
        encoded = BytesIO()
        thumbnail.save(
            encoded,
            format="JPEG",
            quality=THUMBNAIL_JPEG_QUALITY,
            optimize=True,
        )
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.standard_b64encode(encoded.getvalue()).decode("ascii"),
        },
    }


class AnthropicTextSpanSelector:
    def __init__(self, model: str, batch_pages: int) -> None:
        if batch_pages <= 0:
            raise ValueError("TEXT_OCCLUSION_BATCH_PAGES must be greater than zero")
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
            prepared = [
                (page, formatted)
                for page in batch
                if (formatted := _format_page(page, chrome_lines)) is not None
            ]
            if not prepared:
                continue  # every page in this batch was empty or all-chrome
            try:
                spans.extend(self._select_batch(prepared, objectives))
            except TextOcclusionError as exc:
                page_numbers = [page.page_number for page, _listing in prepared]
                logger.warning(
                    "Text-occlusion selection failed for pages %s: %s", page_numbers, exc
                )
        return spans

    def _select_batch(
        self, prepared: list[tuple[TextPage, str]], objectives: str | None
    ) -> list[SelectedSpan]:
        content: list[TextBlockParam | ImageBlockParam] = []
        if objectives:
            content.append({"type": "text", "text": f"{OBJECTIVES_PREFACE}{objectives}"})
        try:
            for page, listing in prepared:
                content.append(
                    {"type": "text", "text": f"Low-resolution preview for page {page.page_number}:"}
                )
                content.append(_thumbnail_block(str(page.image_path)))
                content.append({"type": "text", "text": listing})
        except OSError as exc:
            raise TextOcclusionError(f"Could not build a slide preview: {exc}") from exc

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
                messages=[{"role": "user", "content": content}],
                output_format=BatchSelection,
            )
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            raise TextOcclusionError(f"Text span selection request failed: {exc}") from exc

        parsed = response.parsed_output
        if parsed is None:
            raise TextOcclusionError(
                "Text span selection response could not be parsed into the expected schema."
            )
        usage = response.usage
        logger.info(
            "Text-occlusion selection usage pages=%s input_tokens=%s output_tokens=%s "
            "cache_creation_input_tokens=%s cache_read_input_tokens=%s",
            [page.page_number for page, _listing in prepared],
            usage.input_tokens,
            usage.output_tokens,
            getattr(usage, "cache_creation_input_tokens", 0) or 0,
            getattr(usage, "cache_read_input_tokens", 0) or 0,
        )
        return parsed.spans
