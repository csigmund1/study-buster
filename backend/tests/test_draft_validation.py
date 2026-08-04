from app.services.card_generation.schemas import GeneratedCard
from app.services.draft_validation import validate_and_dedupe


def _basic(front: str, back: str, source_page: int = 1) -> GeneratedCard:
    return GeneratedCard(note_type="basic", front=front, back=back, source_page=source_page)


def _cloze(cloze_text: str, source_page: int = 1) -> GeneratedCard:
    return GeneratedCard(note_type="cloze", cloze_text=cloze_text, source_page=source_page)


def test_drops_cloze_card_without_valid_syntax() -> None:
    cards = [_cloze("no cloze markers here")]

    result = validate_and_dedupe(cards, page_count=5)

    assert result == []


def test_drops_card_with_out_of_range_source_page() -> None:
    cards = [_basic("Q1", "A1", source_page=99)]

    result = validate_and_dedupe(cards, page_count=5)

    assert result == []


def test_drops_basic_card_missing_back() -> None:
    cards = [GeneratedCard(note_type="basic", front="Q1", back=None, source_page=1)]

    result = validate_and_dedupe(cards, page_count=5)

    assert result == []


def test_drops_overlong_field() -> None:
    cards = [_basic("Q1", "A" * 2001, source_page=1)]

    result = validate_and_dedupe(cards, page_count=5)

    assert result == []


def test_keeps_valid_basic_and_cloze_cards() -> None:
    cards = [_basic("Q1", "A1", source_page=1), _cloze("{{c1::term}} is key.", source_page=2)]

    result = validate_and_dedupe(cards, page_count=5)

    assert len(result) == 2


def test_near_duplicates_collapse_across_groups() -> None:
    # Simulates two different page groups both surfacing the same fact, with
    # different casing/punctuation/whitespace.
    group_one = [_basic("What enzyme unwinds DNA?", "Helicase", source_page=1)]
    group_two = [_basic("what enzyme unwinds dna", "Helicase", source_page=7)]

    result = validate_and_dedupe(group_one + group_two, page_count=10)

    assert len(result) == 1
    assert result[0].source_page == 1


def test_distinct_cards_are_not_deduplicated() -> None:
    cards = [
        _basic("What enzyme unwinds DNA?", "Helicase", source_page=1),
        _basic("What enzyme synthesizes DNA?", "Polymerase", source_page=2),
    ]

    result = validate_and_dedupe(cards, page_count=5)

    assert len(result) == 2
