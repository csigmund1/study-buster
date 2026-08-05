"""The multi-target `Occlusion` shape and its legacy-payload upgrade."""

import pytest
from pydantic import ValidationError

from app.models import Box, Direction, Occlusion, OcclusionKind
from tests.conftest import legacy_occlusion_dict


def test_legacy_payload_upgrades_to_multi_target() -> None:
    occ = Occlusion.model_validate(legacy_occlusion_dict())

    assert occ.kind is OcclusionKind.DIAGRAM
    assert occ.labels == ["Thyroid gland"]
    assert len(occ.target_boxes) == 1
    assert occ.target_boxes[0].left == pytest.approx(0.3)
    assert occ.target_boxes[0].top == pytest.approx(0.3)
    # untouched fields survive
    assert occ.direction is Direction.IDENTIFY
    assert occ.crop_box.width == pytest.approx(0.8)
    assert len(occ.mask_boxes) == 1


def test_legacy_upgrade_drops_the_old_keys() -> None:
    occ = Occlusion.model_validate(legacy_occlusion_dict())
    dumped = occ.model_dump(mode="json")

    assert "label" not in dumped
    assert "label_box" not in dumped
    assert dumped["kind"] == "diagram"


def test_current_payload_round_trips_unchanged() -> None:
    occ = Occlusion(
        kind=OcclusionKind.TEXT,
        direction=Direction.IDENTIFY,
        labels=["Part of the trunk"],
        crop_box=Box(left=0.0, top=0.0, width=1.0, height=1.0),
        target_boxes=[
            Box(left=0.1, top=0.2, width=0.2, height=0.03),
            Box(left=0.1, top=0.24, width=0.15, height=0.03),
        ],
        mask_boxes=[
            Box(left=0.1, top=0.2, width=0.2, height=0.03),
            Box(left=0.1, top=0.24, width=0.15, height=0.03),
        ],
    )
    reparsed = Occlusion.model_validate(occ.model_dump(mode="json"))

    assert reparsed == occ
    # One label spanning two line fragments: lists are deliberately not parallel.
    assert len(reparsed.labels) == 1
    assert len(reparsed.target_boxes) == 2


def test_an_explicit_kind_is_never_treated_as_legacy() -> None:
    payload = legacy_occlusion_dict()
    payload["kind"] = "text"
    payload["labels"] = ["Already migrated"]
    payload["target_boxes"] = [{"left": 0.5, "top": 0.5, "width": 0.1, "height": 0.1}]

    occ = Occlusion.model_validate(payload)

    assert occ.kind is OcclusionKind.TEXT
    assert occ.labels == ["Already migrated"]
    assert occ.target_boxes[0].left == pytest.approx(0.5)


def test_labels_and_target_boxes_must_be_non_empty() -> None:
    base = {
        "kind": "diagram",
        "direction": "identify",
        "crop_box": {"left": 0.0, "top": 0.0, "width": 1.0, "height": 1.0},
        "mask_boxes": [],
    }

    a_box = {"left": 0.1, "top": 0.1, "width": 0.1, "height": 0.1}

    with pytest.raises(ValidationError):
        Occlusion.model_validate({**base, "labels": [], "target_boxes": [a_box]})
    with pytest.raises(ValidationError):
        Occlusion.model_validate({**base, "labels": ["x"], "target_boxes": []})
