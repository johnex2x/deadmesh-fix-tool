from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from dmfix.core.nif_io import NifFileLayout


_SAFE_COLLISION_TYPES = {
    "bhkCollisionObject",
    "bhkCompressedMeshShape",
    "bhkCompressedMeshShapeData",
    "bhkMoppBvTreeShape",
    "bhkRigidBody",
    "bhkRigidBodyT",
}


@dataclass(frozen=True)
class OrphanRemovalResult:
    success: bool
    report_only: bool
    output_path: Path
    reason: str
    removed_block_indexes: tuple[int, ...]


def remove_orphan_collision(
    input_path: str | Path, output_path: str | Path
) -> OrphanRemovalResult:
    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.unlink(missing_ok=True)
    try:
        layout = NifFileLayout.read(input_path)
        first_index = _collision_suffix(layout)
        if first_index is None:
            return _report_only(
                output_path,
                "no safely removable unreferenced collision-block suffix; "
                "remove the orphan in NifSkope (orphans are harmless in game)",
            )
        root_count = struct.unpack_from("<I", layout.data, layout.footer_offset)[0]
        roots = struct.unpack_from(
            f"<{root_count}I", layout.data, layout.footer_offset + 4
        )
        if any(root >= first_index for root in roots):
            return _report_only(
                output_path,
                "a NIF root points at the candidate suffix; remove it in NifSkope",
            )
        if _has_potential_ref_at_or_past(
            layout, first_index, len(layout.blocks)
        ):
            return _report_only(
                output_path,
                "a possible block reference points at-or-past the candidate suffix; "
                "remove it in NifSkope",
            )

        removed = tuple(range(first_index, len(layout.blocks)))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(layout.remove_trailing_blocks(first_index))
        NifFileLayout.read(output_path)
        return OrphanRemovalResult(
            True,
            False,
            output_path,
            f"removed trailing orphan blocks {list(removed)}",
            removed,
        )
    except (ValueError, OSError, IndexError, struct.error) as exc:
        output_path.unlink(missing_ok=True)
        return _report_only(output_path, str(exc))


def _collision_suffix(layout: NifFileLayout) -> int | None:
    first_index = len(layout.blocks)
    for block in reversed(layout.blocks):
        if block.type_name not in _SAFE_COLLISION_TYPES:
            break
        first_index = block.index
    return first_index if first_index < len(layout.blocks) else None


def _has_potential_ref(
    layout: NifFileLayout, target: int, retained_block_count: int
) -> bool:
    needle = struct.pack("<i", target)
    return any(
        needle in layout.payload(block.index)
        for block in layout.blocks[:retained_block_count]
    )


def _has_potential_ref_at_or_past(
    layout: NifFileLayout, first_target: int, retained_block_count: int
) -> bool:
    for target in range(first_target, len(layout.blocks)):
        if _has_potential_ref(layout, target, retained_block_count):
            return True
    return False


def _report_only(output_path: Path, reason: str) -> OrphanRemovalResult:
    return OrphanRemovalResult(False, True, output_path, reason, ())
