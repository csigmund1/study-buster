"""Grouped-mode transform for occlusions (plan §6).

Pure, deterministic list manipulation applied after detection and before card
creation: it constructs no new `Box` and therefore has no degenerate-geometry
path. Failures here are bugs, not best-effort drops.
"""

from app.models import Box, Occlusion
from app.schemas.generation_options import MaskGrouping


def _dedupe(boxes: list[Box]) -> list[Box]:
    """De-duplicate boxes by value, preserving first-occurrence order.

    Required, not cosmetic: every diagram occlusion on a page carries the same
    `mask_boxes` (all of that page's label boxes), so a naive concatenation would
    emit N identical copies of every mask.
    """
    seen: set[tuple[float, float, float, float]] = set()
    unique: list[Box] = []
    for box in boxes:
        key = (box.left, box.top, box.width, box.height)
        if key in seen:
            continue
        seen.add(key)
        unique.append(box)
    return unique


def group_occlusions(occlusions: list[Occlusion], mode: MaskGrouping) -> list[Occlusion]:
    """Merge **one page's** occlusions into one occlusion per kind.

    The caller is responsible for bucketing by page: this function assumes every
    input belongs to the same page, and only separates them by `kind`.

    `INDIVIDUAL` returns the input unchanged. `GROUPED` returns one `Occlusion`
    per kind, in first-appearance order of the kinds, whose `labels` are every
    input's labels in order and whose `target_boxes`/`mask_boxes` are the
    concatenated boxes de-duplicated by value. `crop_box` and `direction` come
    from the first input of that kind.
    """
    if mode is MaskGrouping.INDIVIDUAL:
        return occlusions

    buckets: dict[str, list[Occlusion]] = {}
    for occ in occlusions:
        buckets.setdefault(occ.kind, []).append(occ)

    grouped: list[Occlusion] = []
    for members in buckets.values():
        first = members[0]
        grouped.append(
            Occlusion(
                kind=first.kind,
                direction=first.direction,
                labels=[label for occ in members for label in occ.labels],
                crop_box=first.crop_box,
                target_boxes=_dedupe([box for occ in members for box in occ.target_boxes]),
                mask_boxes=_dedupe([box for occ in members for box in occ.mask_boxes]),
            )
        )
    return grouped
