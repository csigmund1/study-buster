"""Pedagogical selection plus deterministic closure and text-only card audit.

The first model call sees a cheap slide thumbnail and exact OCR so it can choose
worthwhile targets without predicting geometry. `spans.py` then hides every
exact slide-local occurrence. A second, text-only call reviews the simultaneous
grouped-card state, adds semantic leakage aliases, and drops weak or ambiguous
targets before `filters.py` applies the final structural safety checks.
"""

import base64
import logging
from io import BytesIO

import anthropic
from anthropic.types import ImageBlockParam, TextBlockParam
from PIL import Image

from app.services.draft_validation import normalize_text
from app.services.text_occlusion.base import TextOcclusionError, TextPage
from app.services.text_occlusion.document_context import is_chrome
from app.services.text_occlusion.filters import (
    MAX_SPAN_WORDS,
    MAX_SPANS_PER_PAGE,
    MIN_LINE_CONFIDENCE,
    covers_entire_line,
    is_acceptable_size,
    is_chrome_span,
    is_confident,
    is_locatable,
    is_stopword_only,
)
from app.services.text_occlusion.schemas import (
    BatchAudit,
    CandidateBatch,
    CandidateSpan,
    SelectedSpan,
    SpanRef,
)
from app.services.text_occlusion.spans import (
    all_refs,
    expand_exact_leakage,
    locate_phrase,
    span_text,
)

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

Choose only the primary answer occurrence for each worthwhile learning target. \
Later deterministic and text-only stages conceal repetitions and aliases and \
validate the finished grouped card. Do not attempt leakage detection here.

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

Term boundaries:
- Function words inside one canonical term stay inside its mask: \
  "Law of the Heart" is one complete term.
- Coordinated concepts become separate masks and the conjunction stays visible: \
  in "Rest Potentiation and Post-Extrasystolic Potentiation", select \
  "Rest Potentiation" and "Post-Extrasystolic Potentiation" separately, not \
  the whole phrase and never "and".
- Select a concept only once per page and put only its best-context occurrence \
  in `refs`. Do not select repeated occurrences as separate targets.

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
- Prefer targets with enough surrounding explanatory text to support recall.
- Never choose overlapping learning targets or the same concept twice on one slide. \
  Repetition on different slides is allowed and often pedagogically useful.
- Skip lines whose text looks garbled or misrecognized.
- Skip title, agenda, learning-objectives, and disclaimer slides entirely: \
return no spans for them.
- Return at most {MAX_SPANS_PER_PAGE} spans per page, best first.
- Each span names its `page_number` once. `refs` contains the answer's best-context \
  occurrence; each ref's `text` is that occurrence's fragment on its own line.
- If a phrase wraps onto the next line (still on the same page), return one \
  ref per line fragment in `refs`, in reading order.

Only use page numbers and line indices that appear in the listing below.
"""

AUDIT_SYSTEM_PROMPT = """\
You are the final quality gate for fill-in-the-blank lecture-slide cards. You \
receive indexed OCR text, proposed learning targets, and a preview of the slide \
with every proposed target and exact repetition hidden simultaneously.

Return exactly one decision for every candidate_index. Keep a candidate only if:
- recalling it materially helps learn the lecture;
- it is an atomic term, mechanism, relationship, classification, value, or threshold;
- the visible grouped-card context makes the answer reasonably inferable; and
- no visible text directly states, aliases, expands, or plainly restates the answer.

Use `additional_leakage` for visible phrases that reveal a kept answer: aliases, \
acronym expansions, parenthetical alternate names, synonymous mechanism names, \
or a direct restatement. Each leakage group is one revealing phrase; if it wraps, \
use one exact ref per OCR line in reading order. Use only exact text and line \
indices shown for that page.

Drop rather than keep when:
- the proposed answer is broad prose, a predicate fragment, or a whole conclusion sentence;
- another visible sentence gives the answer and hiding that sentence would remove the useful cue;
- aliases cannot all be hidden while preserving an adequate recall cue;
- several candidates depend on one another and hiding them together makes the \
  grouped card ambiguous;
- the fact is incidental, redundant, or too low-value for a study card.

Evaluate the final set jointly: decisions must describe a coherent grouped card \
after every kept candidate and every additional leakage phrase is hidden. Do not \
invent replacement targets or rewrite an answer. `additional_leakage` must be \
empty for a dropped candidate.
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


def _is_structurally_valid(
    page: TextPage, span: SelectedSpan, chrome_lines: frozenset[str]
) -> bool:
    """The cheap pre-audit subset of the final deterministic quality gate."""
    if not is_confident(page.lines, span):
        return False
    if is_chrome_span(page.lines, span, chrome_lines):
        return False
    if not is_locatable(page.lines, span):
        return False
    answer = span_text(page.lines, span)
    if not is_acceptable_size(answer) or is_stopword_only(answer):
        return False
    return not covers_entire_line(page.lines, span)


def _masked_page_listing(
    page: TextPage,
    candidates: list[tuple[int, SelectedSpan]],
    chrome_lines: frozenset[str],
) -> str:
    """OCR listing showing the simultaneous grouped-card masking state."""
    ranges: dict[int, list[tuple[int, int, int]]] = {}
    for candidate_index, span in candidates:
        for ref in all_refs(span):
            line = page.lines[ref.line_index]
            if ref.word_start is not None and ref.word_count is not None:
                start, count = ref.word_start, ref.word_count
            else:
                located = locate_phrase(line, ref.text)
                if located is None:
                    continue
                start, count = located.start, located.count
            ranges.setdefault(ref.line_index, []).append((start, count, candidate_index))

    rendered: list[str] = []
    for line_index, line in enumerate(page.lines):
        if line.confidence < MIN_LINE_CONFIDENCE or is_chrome(line.text, chrome_lines):
            continue
        line_ranges = sorted(ranges.get(line_index, []), key=lambda item: (item[0], -item[1]))
        parts: list[str] = []
        cursor = 0
        for start, count, candidate_index in line_ranges:
            if start < cursor:
                continue
            parts.extend(word.text for word in line.words[cursor:start])
            parts.append(f"[[C{candidate_index}]]")
            cursor = start + count
        parts.extend(word.text for word in line.words[cursor:])
        rendered.append(f'[{line_index}] "{" ".join(parts)}"')
    return f"=== Page {page.page_number}: grouped masked preview ===\n" + "\n".join(rendered)


def _audit_listing(
    prepared: list[tuple[TextPage, str]],
    candidates: list[SelectedSpan],
    chrome_lines: frozenset[str],
) -> str:
    by_page: dict[int, list[tuple[int, SelectedSpan]]] = {}
    for index, span in enumerate(candidates):
        by_page.setdefault(span.page_number, []).append((index, span))

    sections: list[str] = []
    for page, _listing in prepared:
        page_candidates = by_page.get(page.page_number)
        if not page_candidates:
            continue
        sections.append(_masked_page_listing(page, page_candidates, chrome_lines))
        sections.append("Candidates:")
        sections.extend(
            f'C{index}: answer="{span_text(page.lines, span)}"'
            for index, span in page_candidates
        )
    return "\n".join(sections)


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
                spans.extend(self._select_batch(prepared, chrome_lines, objectives))
            except TextOcclusionError as exc:
                page_numbers = [page.page_number for page, _listing in prepared]
                logger.warning(
                    "Text-occlusion selection failed for pages %s: %s", page_numbers, exc
                )
        return spans

    def _select_batch(
        self,
        prepared: list[tuple[TextPage, str]],
        chrome_lines: frozenset[str],
        objectives: str | None,
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
                output_format=CandidateBatch,
            )
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            raise TextOcclusionError(f"Text span selection request failed: {exc}") from exc

        parsed: CandidateBatch | None = response.parsed_output
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
        candidates = self._prepare_candidates(prepared, parsed.spans, chrome_lines)
        if not candidates:
            return []
        audited = self._audit_batch(prepared, candidates, chrome_lines)
        logger.info(
            "Text-occlusion candidate audit proposed=%s prepared=%s retained=%s dropped=%s",
            len(parsed.spans),
            len(candidates),
            len(audited),
            len(candidates) - len(audited),
        )
        return audited

    def _prepare_candidates(
        self,
        prepared: list[tuple[TextPage, str]],
        candidates: list[CandidateSpan],
        chrome_lines: frozenset[str],
    ) -> list[SelectedSpan]:
        """Resolve, exact-expand, validate, and exactly deduplicate raw targets."""
        pages = {page.page_number: page for page, _listing in prepared}
        seen_answers: dict[int, set[str]] = {}
        page_counts: dict[int, int] = {}
        selected: list[SelectedSpan] = []
        exact_leakage_count = 0
        for candidate in candidates:
            page = pages.get(candidate.page_number)
            if page is None or not candidate.refs:
                continue
            refs = [
                SpanRef(line_index=ref.line_index, text=ref.text)
                for ref in candidate.refs
            ]
            span = SelectedSpan(page_number=candidate.page_number, refs=refs)
            expanded = expand_exact_leakage(page.lines, span)
            if expanded is None or not _is_structurally_valid(page, expanded, chrome_lines):
                continue
            answer_key = normalize_text(span_text(page.lines, expanded))
            page_seen = seen_answers.setdefault(candidate.page_number, set())
            if not answer_key or answer_key in page_seen:
                continue
            if page_counts.get(candidate.page_number, 0) >= MAX_SPANS_PER_PAGE:
                continue
            page_seen.add(answer_key)
            page_counts[candidate.page_number] = page_counts.get(candidate.page_number, 0) + 1
            selected.append(expanded)
            exact_leakage_count += len(expanded.leakage_refs)
        logger.info(
            "Text-occlusion candidate preparation proposed=%s prepared=%s "
            "exact_leakage_refs=%s",
            len(candidates),
            len(selected),
            exact_leakage_count,
        )
        return selected

    def _audit_batch(
        self,
        prepared: list[tuple[TextPage, str]],
        candidates: list[SelectedSpan],
        chrome_lines: frozenset[str],
    ) -> list[SelectedSpan]:
        content: list[TextBlockParam] = [
            {
                "type": "text",
                "text": _audit_listing(prepared, candidates, chrome_lines),
            }
        ]
        try:
            response = self._client.messages.parse(
                model=self._model,
                max_tokens=MAX_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": AUDIT_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": content}],
                output_format=BatchAudit,
            )
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            raise TextOcclusionError(f"Text span audit request failed: {exc}") from exc

        parsed: BatchAudit | None = response.parsed_output
        if parsed is None:
            raise TextOcclusionError(
                "Text span audit response could not be parsed into the expected schema."
            )
        expected = set(range(len(candidates)))
        returned = [decision.candidate_index for decision in parsed.decisions]
        if len(returned) != len(set(returned)) or set(returned) != expected:
            raise TextOcclusionError("Text span audit did not decide every candidate exactly once.")

        usage = response.usage
        logger.info(
            "Text-occlusion audit usage pages=%s candidates=%s input_tokens=%s "
            "output_tokens=%s cache_creation_input_tokens=%s cache_read_input_tokens=%s",
            sorted({span.page_number for span in candidates}),
            len(candidates),
            usage.input_tokens,
            usage.output_tokens,
            getattr(usage, "cache_creation_input_tokens", 0) or 0,
            getattr(usage, "cache_read_input_tokens", 0) or 0,
        )

        pages = {page.page_number: page for page, _listing in prepared}
        decisions = {decision.candidate_index: decision for decision in parsed.decisions}
        retained: list[SelectedSpan] = []
        semantic_leakage_count = 0
        for index, candidate in enumerate(candidates):
            decision = decisions[index]
            if not decision.keep:
                continue
            page = pages[candidate.page_number]
            aliases = [
                [SpanRef(line_index=ref.line_index, text=ref.text) for ref in group.refs]
                for group in decision.additional_leakage
            ]
            expanded = expand_exact_leakage(page.lines, candidate, aliases)
            if expanded is None or not _is_structurally_valid(page, expanded, chrome_lines):
                continue
            semantic_leakage_count += len(expanded.leakage_refs) - len(
                candidate.leakage_refs
            )
            retained.append(expanded)
        logger.info(
            "Text-occlusion audit leakage candidates=%s added_refs=%s",
            len(retained),
            semantic_leakage_count,
        )
        return retained
