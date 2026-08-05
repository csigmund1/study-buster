from app.models import Box, Direction, Occlusion, OcclusionKind
from app.schemas.generation_options import MaskGrouping
from app.services.occlusion_grouping import group_occlusions
from tests.conftest import _box

CROP = Box(left=0, top=0, width=1, height=1)


def _diagram_page(labels: list[str]) -> list[Occlusion]:
    """One page of diagram occlusions: each carries EVERY label box as its mask."""
    boxes = [_box(0.1 * (index + 1), 0.2, 0.05, 0.05) for index in range(len(labels))]
    return [
        Occlusion(
            kind=OcclusionKind.DIAGRAM,
            direction=Direction.IDENTIFY,
            labels=[label],
            crop_box=CROP,
            target_boxes=[boxes[index]],
            mask_boxes=boxes,
        )
        for index, label in enumerate(labels)
    ]


def _text_occlusion(label: str, left: float) -> Occlusion:
    box = _box(left, 0.6, 0.08, 0.03)
    return Occlusion(
        kind=OcclusionKind.TEXT,
        direction=Direction.IDENTIFY,
        labels=[label],
        crop_box=CROP,
        target_boxes=[box],
        mask_boxes=[box],
    )


def test_individual_mode_is_the_identity() -> None:
    occlusions = _diagram_page(["Aorta", "Vena cava"])
    assert group_occlusions(occlusions, MaskGrouping.INDIVIDUAL) == occlusions


def test_grouped_merges_a_page_into_one_occlusion_with_deduped_masks() -> None:
    occlusions = _diagram_page(["Aorta", "Vena cava", "Pulmonary artery"])

    grouped = group_occlusions(occlusions, MaskGrouping.GROUPED)

    assert len(grouped) == 1
    merged = grouped[0]
    assert merged.labels == ["Aorta", "Vena cava", "Pulmonary artery"]
    assert merged.target_boxes == [occ.target_boxes[0] for occ in occlusions]
    # Every input carried the same three mask boxes: 3x3 concatenated, deduped to 3.
    assert merged.mask_boxes == occlusions[0].mask_boxes
    assert len(merged.mask_boxes) == 3
    assert merged.crop_box == CROP
    assert merged.direction is Direction.IDENTIFY
    assert merged.kind is OcclusionKind.DIAGRAM


def test_grouped_emits_one_occlusion_per_kind() -> None:
    occlusions = [
        *_diagram_page(["Aorta", "Vena cava"]),
        _text_occlusion("glomerulus", 0.2),
        _text_occlusion("nephron", 0.5),
    ]

    grouped = group_occlusions(occlusions, MaskGrouping.GROUPED)

    assert [occ.kind for occ in grouped] == [OcclusionKind.DIAGRAM, OcclusionKind.TEXT]
    assert grouped[0].labels == ["Aorta", "Vena cava"]
    assert grouped[1].labels == ["glomerulus", "nephron"]
    assert len(grouped[1].mask_boxes) == 2
    assert len(grouped[1].target_boxes) == 2


def test_grouped_single_occlusion_bucket_round_trips() -> None:
    only = _text_occlusion("glomerulus", 0.2)

    grouped = group_occlusions([only], MaskGrouping.GROUPED)

    assert grouped == [only]


def test_grouped_empty_input_is_empty() -> None:
    assert group_occlusions([], MaskGrouping.GROUPED) == []
