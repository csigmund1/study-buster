from pathlib import Path
from typing import Any

import pytest

from app.services.card_generation.anthropic_generator import MODEL, AnthropicCardGenerator
from app.services.card_generation.base import CardGenerationError
from app.services.card_generation.page_group import GroupPage, PageGroup
from app.services.card_generation.schemas import GeneratedCard, GeneratedCards


class _FakeResponse:
    def __init__(self, parsed_output: GeneratedCards | None) -> None:
        self.parsed_output = parsed_output


def _make_group(tmp_path: Path, page_numbers: list[int]) -> PageGroup:
    pages = []
    for number in page_numbers:
        image_path = tmp_path / f"page_{number}.png"
        image_path.write_bytes(b"\x89PNGfakepixels")
        pages.append(
            GroupPage(
                page_number=number,
                image_path=image_path,
                supplemental_text=f"text for page {number}",
            )
        )
    return PageGroup(deck_name="Biology Lecture 3", pages=pages)


def test_generate_composes_request_and_returns_parsed_cards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    group = _make_group(tmp_path, [3, 4, 5])
    generator = AnthropicCardGenerator()

    captured: dict[str, Any] = {}
    expected_cards = [
        GeneratedCard(note_type="basic", front="Q", back="A", source_page=3),
    ]
    parsed = GeneratedCards(cards=expected_cards, diagram_pages=[4])

    def fake_parse(**kwargs: Any) -> _FakeResponse:
        captured.update(kwargs)
        return _FakeResponse(parsed)

    monkeypatch.setattr(generator._client.messages, "parse", fake_parse)

    result = generator.generate(group)

    assert result == parsed
    assert result.cards == expected_cards
    assert result.diagram_pages == [4]
    assert captured["model"] == MODEL
    assert captured["output_format"] is GeneratedCards
    assert captured["max_tokens"] == 8192

    messages = captured["messages"]
    assert len(messages) == 1
    content = messages[0]["content"]

    image_blocks = [block for block in content if block["type"] == "image"]
    assert len(image_blocks) == 3
    for block in image_blocks:
        assert block["source"]["media_type"] == "image/png"

    text_blocks = [block for block in content if block["type"] == "text"]
    page_labels = [block["text"] for block in text_blocks if block["text"].startswith("Page ")]
    assert page_labels == ["Page 3", "Page 4", "Page 5"]


def test_generate_raises_domain_error_when_parsed_output_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    group = _make_group(tmp_path, [1])
    generator = AnthropicCardGenerator()

    monkeypatch.setattr(
        generator._client.messages, "parse", lambda **kwargs: _FakeResponse(None)
    )

    with pytest.raises(CardGenerationError):
        generator.generate(group)


def test_generate_wraps_sdk_status_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import anthropic

    group = _make_group(tmp_path, [1])
    generator = AnthropicCardGenerator()

    def raise_status_error(**kwargs: Any) -> _FakeResponse:
        request = anthropic.APIStatusError(
            message="boom",
            response=_fake_httpx_response(),
            body=None,
        )
        raise request

    monkeypatch.setattr(generator._client.messages, "parse", raise_status_error)

    with pytest.raises(CardGenerationError):
        generator.generate(group)


def _fake_httpx_response() -> Any:
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return httpx.Response(status_code=500, request=request)
