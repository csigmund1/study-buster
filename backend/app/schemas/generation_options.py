"""Per-job generation options (plan §6).

The choices the user makes on the Upload page before generating. They are
resolved server-side at job creation, stored on the `Job` row, and echoed on
every job response — clients never see a null or re-derive a default.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from app.config import Settings

#: The pre-split options key: one grouping choice that applied to both kinds.
LEGACY_MASK_GROUPING_KEY = "mask_grouping"


class TextCardMode(StrEnum):
    """Which text stage runs. Mutually exclusive."""

    BASIC_CLOZE = "basic_cloze"
    TEXT_OCCLUSION = "text_occlusion"


class MaskGrouping(StrEnum):
    """How the detected masks of ONE occlusion kind become cards."""

    INDIVIDUAL = "individual"  # one card per mask
    GROUPED = "grouped"  # one card per page, for this kind


def _expand_legacy_mask_grouping(data: dict[str, Any]) -> dict[str, Any]:
    """Expand a legacy `mask_grouping` into the two per-kind keys.

    Options persisted before the per-kind split carried a single `mask_grouping`
    that governed both kinds. Expanding here (rather than migrating) keeps
    existing jobs readable with no dev-DB reset — `storage/database.py` can add
    columns but never rewrite their contents.

    A specific key always wins; the legacy value only fills a key left
    unspecified. An invalid legacy value still fails enum validation downstream,
    so it surfaces as a `422` rather than being silently dropped.
    """
    if LEGACY_MASK_GROUPING_KEY not in data:
        return data

    expanded = dict(data)
    legacy = expanded.pop(LEGACY_MASK_GROUPING_KEY)
    expanded.setdefault("diagram_mask_grouping", legacy)
    expanded.setdefault("text_mask_grouping", legacy)
    return expanded


class GenerationOptions(BaseModel):
    """A fully resolved set of generation options.

    `extra="forbid"` so an unknown key from a client is a validation error
    (surfaced as `422`) rather than a silently ignored setting.

    The two grouping keys are **independent**: diagrams may stay individual
    while text occlusions are grouped, or the reverse.
    """

    model_config = ConfigDict(extra="forbid")

    text_card_mode: TextCardMode = TextCardMode.BASIC_CLOZE
    diagram_occlusion_enabled: bool = True
    diagram_mask_grouping: MaskGrouping = MaskGrouping.INDIVIDUAL
    text_mask_grouping: MaskGrouping = MaskGrouping.INDIVIDUAL

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy(cls, data: Any) -> Any:
        """Accept the pre-split payload shape when validating directly.

        `resolve_options` expands the legacy key itself (it has to, so the value
        is applied *before* server-side defaults fill the new keys); this makes
        the model self-sufficient for any other caller, mirroring
        `Occlusion._upgrade_legacy`.
        """
        if not isinstance(data, dict):
            return data
        return _expand_legacy_mask_grouping(data)


def _default_text_card_mode(settings: Settings) -> TextCardMode:
    """The deployment's default card style, from `TEXT_CARD_MODE`.

    An unrecognized env value falls back to `basic_cloze` silently — that is the
    documented behavior of the env knob and must not become an error. Client-sent
    values are never resolved through here, so they stay strictly validated.
    """
    try:
        return TextCardMode(settings.text_card_mode)
    except ValueError:
        return TextCardMode.BASIC_CLOZE


def resolve_options(raw: dict[str, Any] | None, settings: Settings) -> GenerationOptions:
    """Resolve a partial (or absent) options payload into a complete one.

    `None` and `{}` behave identically: every unspecified key is resolved
    server-side. A key the client did send always wins and is strictly
    validated — an invalid value raises `ValidationError`.

    The legacy key is expanded *before* defaults are merged in: merging first
    would leave the new keys already populated, and the legacy value would be
    silently ignored.
    """
    defaults: dict[str, Any] = {
        "text_card_mode": _default_text_card_mode(settings),
        "diagram_occlusion_enabled": True,
        "diagram_mask_grouping": MaskGrouping.INDIVIDUAL,
        "text_mask_grouping": MaskGrouping.INDIVIDUAL,
    }
    merged = {**defaults, **_expand_legacy_mask_grouping(raw or {})}
    return GenerationOptions.model_validate(merged)
