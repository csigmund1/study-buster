"""Vision-based card generation via the Anthropic Messages API (plan.md §4, §9)."""

import base64

import anthropic
from anthropic.types import ImageBlockParam, TextBlockParam

from app.services.card_generation.base import CardGenerationError
from app.services.card_generation.page_group import PageGroup
from app.services.card_generation.schemas import GeneratedCard, GeneratedCards

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 8192

SYSTEM_PROMPT = """\
You generate Anki flashcards from lecture slide images. The slides may have \
handwritten annotations on top of printed content; the images are the primary \
source of truth, and any supplied extracted text is supplemental (it only \
captures selectable text, never handwriting).

Rules for every card you produce:
- Test one main fact or relationship per card.
- Make each card understandable on its own, without reopening the original notes.
- Avoid vague questions, unsupported claims, and unnecessary duplicates.
- Prefer concise answers.
- Use the "cloze" note type only when the resulting sentence remains readable and \
self-contained; otherwise use "basic".
- For "basic" cards, set both `front` and `back`; leave `cloze_text` null.
- For "cloze" cards, set `cloze_text` using valid `{{c1::...}}` syntax; leave \
`front` and `back` null.
- Set `source_page` to the real PDF page number given in that page's "Page N" \
label — never invent or renumber pages.
- Set `needs_page_image` to true only when understanding the card truly requires \
seeing the slide's visual (a diagram, chart, or figure) — not for plain text facts.
- Let the number of cards follow how much teachable content is on the pages; there \
is no fixed quota, and pages with little content may yield zero cards.
"""


class AnthropicCardGenerator:
    def __init__(self) -> None:
        self._client = anthropic.Anthropic()

    def generate(self, group: PageGroup) -> list[GeneratedCard]:
        content: list[TextBlockParam | ImageBlockParam] = []
        for page in group.pages:
            content.append({"type": "text", "text": f"Page {page.page_number}"})
            image_bytes = page.image_path.read_bytes()
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.standard_b64encode(image_bytes).decode("ascii"),
                    },
                }
            )
            if page.supplemental_text.strip():
                content.append(
                    {
                        "type": "text",
                        "text": f"Supplemental extracted text for page {page.page_number}:\n"
                        f"{page.supplemental_text}",
                    }
                )

        content.append(
            {
                "type": "text",
                "text": (
                    f"Deck name: {group.deck_name}\n"
                    "Generate flashcards for the pages above, following the system "
                    "instructions exactly."
                ),
            }
        )

        try:
            response = self._client.messages.parse(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
                output_format=GeneratedCards,
            )
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            raise CardGenerationError(f"Card generation request failed: {exc}") from exc

        parsed = response.parsed_output
        if parsed is None:
            raise CardGenerationError(
                "Card generation response could not be parsed into the expected schema."
            )
        return parsed.cards
