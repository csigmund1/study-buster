import pytest
from pydantic import ValidationError

from app.config import get_settings
from app.schemas.generation_options import (
    GenerationOptions,
    MaskGrouping,
    TextCardMode,
    resolve_options,
)


def test_none_and_empty_dict_resolve_identically() -> None:
    settings = get_settings()
    assert resolve_options(None, settings) == resolve_options({}, settings)


def test_unspecified_keys_take_the_documented_defaults() -> None:
    resolved = resolve_options(None, get_settings())
    assert resolved == GenerationOptions(
        text_card_mode=TextCardMode.BASIC_CLOZE,
        diagram_occlusion_enabled=True,
        diagram_mask_grouping=MaskGrouping.INDIVIDUAL,
        text_mask_grouping=MaskGrouping.INDIVIDUAL,
    )


def test_env_text_card_mode_is_the_default_when_the_key_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEXT_CARD_MODE", "text_occlusion")
    resolved = resolve_options({"text_mask_grouping": "grouped"}, get_settings())
    assert resolved.text_card_mode is TextCardMode.TEXT_OCCLUSION
    assert resolved.text_mask_grouping is MaskGrouping.GROUPED


def test_invalid_env_text_card_mode_falls_back_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEXT_CARD_MODE", "nonsense")
    assert resolve_options(None, get_settings()).text_card_mode is TextCardMode.BASIC_CLOZE


def test_client_sent_key_beats_the_environment_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEXT_CARD_MODE", "text_occlusion")
    resolved = resolve_options({"text_card_mode": "basic_cloze"}, get_settings())
    assert resolved.text_card_mode is TextCardMode.BASIC_CLOZE


def test_invalid_client_value_raises() -> None:
    with pytest.raises(ValidationError):
        resolve_options({"text_card_mode": "nonsense"}, get_settings())

    with pytest.raises(ValidationError):
        resolve_options({"text_mask_grouping": "sideways"}, get_settings())

    with pytest.raises(ValidationError):
        resolve_options({"diagram_mask_grouping": "sideways"}, get_settings())

    # An invalid LEGACY value must fail too, not be silently dropped.
    with pytest.raises(ValidationError):
        resolve_options({"mask_grouping": "sideways"}, get_settings())


def test_unknown_key_raises() -> None:
    with pytest.raises(ValidationError):
        resolve_options({"mask_gruping": "grouped"}, get_settings())


def test_all_keys_round_trip() -> None:
    resolved = resolve_options(
        {
            "text_card_mode": "text_occlusion",
            "diagram_occlusion_enabled": False,
            "diagram_mask_grouping": "individual",
            "text_mask_grouping": "grouped",
        },
        get_settings(),
    )
    assert resolved.model_dump(mode="json") == {
        "text_card_mode": "text_occlusion",
        "diagram_occlusion_enabled": False,
        "diagram_mask_grouping": "individual",
        "text_mask_grouping": "grouped",
    }


def test_the_two_groupings_are_independent() -> None:
    resolved = resolve_options({"text_mask_grouping": "grouped"}, get_settings())
    assert resolved.text_mask_grouping is MaskGrouping.GROUPED
    assert resolved.diagram_mask_grouping is MaskGrouping.INDIVIDUAL


def test_legacy_mask_grouping_applies_to_both_kinds() -> None:
    """A job stored before the per-kind split must stay readable."""
    resolved = resolve_options({"mask_grouping": "grouped"}, get_settings())
    assert resolved.diagram_mask_grouping is MaskGrouping.GROUPED
    assert resolved.text_mask_grouping is MaskGrouping.GROUPED
    # The legacy key never survives into the resolved shape.
    assert "mask_grouping" not in resolved.model_dump(mode="json")


def test_a_specific_grouping_key_beats_the_legacy_key() -> None:
    resolved = resolve_options(
        {"mask_grouping": "grouped", "diagram_mask_grouping": "individual"}, get_settings()
    )
    assert resolved.diagram_mask_grouping is MaskGrouping.INDIVIDUAL
    assert resolved.text_mask_grouping is MaskGrouping.GROUPED


def test_the_model_upgrades_a_legacy_payload_on_direct_validation() -> None:
    """Reading a stored row straight through the model must not raise."""
    upgraded = GenerationOptions.model_validate(
        {
            "text_card_mode": "text_occlusion",
            "diagram_occlusion_enabled": True,
            "mask_grouping": "grouped",
        }
    )
    assert upgraded.diagram_mask_grouping is MaskGrouping.GROUPED
    assert upgraded.text_mask_grouping is MaskGrouping.GROUPED
