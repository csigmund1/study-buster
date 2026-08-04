"""Deterministic fixture generator — zero-cost, no I/O beyond the group it is given."""

from app.services.card_generation.page_group import PageGroup
from app.services.card_generation.schemas import GeneratedCard, GeneratedCards


class MockCardGenerator:
    """Returns deterministic fixture cards keyed off real page numbers in the group.

    For every ~5 pages in the group, emits 2 basic cards + 1 cloze card, referencing
    real page numbers. Exactly one card in the group is flagged `needs_page_image`.
    The group's first page is flagged as a labeled diagram so the diagram-detection
    path is exercised end-to-end in mock mode.
    """

    def generate(self, group: PageGroup) -> GeneratedCards:
        cards: list[GeneratedCard] = []
        pages = group.pages
        if not pages:
            return GeneratedCards(cards=cards, diagram_pages=[])

        chunk_size = 5
        flagged_one = False
        for start in range(0, len(pages), chunk_size):
            chunk = pages[start : start + chunk_size]
            anchor_page = chunk[0].page_number
            back_page = chunk[-1].page_number

            needs_image = not flagged_one
            flagged_one = True

            cards.append(
                GeneratedCard(
                    note_type="basic",
                    front=f"[mock] What is fact #1 on page {anchor_page} of {group.deck_name}?",
                    back=f"[mock] Fact #1 from page {anchor_page}.",
                    source_page=anchor_page,
                    needs_page_image=needs_image,
                )
            )
            cards.append(
                GeneratedCard(
                    note_type="basic",
                    front=f"[mock] What is fact #2 on page {back_page} of {group.deck_name}?",
                    back=f"[mock] Fact #2 from page {back_page}.",
                    source_page=back_page,
                    needs_page_image=False,
                )
            )
            cloze_text = f"[mock] The key term on page {anchor_page} is {{{{c1::mock term}}}}."
            cards.append(
                GeneratedCard(
                    note_type="cloze",
                    cloze_text=cloze_text,
                    source_page=anchor_page,
                    needs_page_image=False,
                )
            )

        return GeneratedCards(cards=cards, diagram_pages=[pages[0].page_number])
