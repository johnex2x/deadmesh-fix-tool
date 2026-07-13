from __future__ import annotations

import struct
import sys
from dataclasses import dataclass
from pathlib import Path


SE_VERSION = 0x14020007
SE_USER_VERSION = 12
SE_STREAM_VERSION = 100
MOPP_FIXED_SIZE = 41


@dataclass(frozen=True)
class NifBlock:
    index: int
    type_name: str
    offset: int
    size: int
    size_entry_offset: int

    @property
    def end(self) -> int:
        return self.offset + self.size


@dataclass(frozen=True)
class NifFileLayout:
    path: Path
    data: bytes
    version: int
    user_version: int
    stream_version: int
    header_end: int
    footer_offset: int
    blocks: tuple[NifBlock, ...]

    @classmethod
    def read(cls, path: str | Path) -> NifFileLayout:
        path = Path(path)
        data = path.read_bytes()
        pos = data.index(b"\n") + 1
        version = _unpack("I", data, pos)
        pos += 4
        endian = data[pos]
        pos += 1
        user_version = _unpack("I", data, pos)
        pos += 4
        block_count = _unpack("I", data, pos)
        pos += 4
        stream_version = _unpack("I", data, pos)
        pos += 4

        if (version, endian, user_version, stream_version) != (
            SE_VERSION,
            1,
            SE_USER_VERSION,
            SE_STREAM_VERSION,
        ):
            raise ValueError("only little-endian Skyrim SE NIF 20.2.0.7 is supported")

        for _ in range(3):
            length = data[pos]
            pos += 1 + length

        type_count = _unpack("H", data, pos)
        pos += 2
        block_types: list[str] = []
        for _ in range(type_count):
            length = _unpack("I", data, pos)
            pos += 4
            block_types.append(data[pos : pos + length].decode("utf-8"))
            pos += length

        type_indexes = struct.unpack_from(f"<{block_count}H", data, pos)
        pos += block_count * 2
        size_table_offset = pos
        block_sizes = struct.unpack_from(f"<{block_count}I", data, pos)
        pos += block_count * 4

        string_count, _ = struct.unpack_from("<II", data, pos)
        pos += 8
        for _ in range(string_count):
            length = _unpack("I", data, pos)
            pos += 4
            if length != 0xFFFFFFFF:
                pos += length

        group_count = _unpack("I", data, pos)
        pos += 4 + group_count * 4
        header_end = pos

        blocks: list[NifBlock] = []
        offset = header_end
        for index, (type_index, size) in enumerate(zip(type_indexes, block_sizes)):
            if type_index >= len(block_types):
                raise ValueError(f"block {index} has invalid type index {type_index}")
            blocks.append(
                NifBlock(
                    index=index,
                    type_name=block_types[type_index],
                    offset=offset,
                    size=size,
                    size_entry_offset=size_table_offset + index * 4,
                )
            )
            offset += size

        footer_offset = offset
        if len(data) < footer_offset + 4:
            raise ValueError("missing NIF footer")
        root_count = _unpack("I", data, footer_offset)
        if footer_offset + 4 + root_count * 4 != len(data):
            raise ValueError("NIF footer size does not match root count")
        return cls(
            path=path,
            data=data,
            version=version,
            user_version=user_version,
            stream_version=stream_version,
            header_end=header_end,
            footer_offset=footer_offset,
            blocks=tuple(blocks),
        )

    def payload(self, block_index: int) -> bytes:
        block = self.blocks[block_index]
        return self.data[block.offset : block.end]

    def replace_block(self, block_index: int, payload: bytes) -> bytes:
        return self.replace_blocks({block_index: payload})

    def replace_blocks(self, replacements: dict[int, bytes]) -> bytes:
        """Replace block payloads using offsets from this original layout."""
        invalid = set(replacements) - set(range(len(self.blocks)))
        if invalid:
            raise IndexError(f"invalid NIF block indexes: {sorted(invalid)}")

        header = bytearray(self.data[: self.header_end])
        payloads: list[bytes] = []
        for block in self.blocks:
            payload = replacements.get(block.index, self.payload(block.index))
            if block.index in replacements:
                struct.pack_into("<I", header, block.size_entry_offset, len(payload))
            payloads.append(payload)
        return b"".join((bytes(header), *payloads, self.data[self.footer_offset :]))


@dataclass(frozen=True)
class MoppData:
    child_shape_index: int
    unused: tuple[int, int, int]
    shape_scale: float
    data_size: int
    origin: tuple[float, float, float]
    scale: float
    build_type: int
    code: bytes


@dataclass(frozen=True)
class CollisionInfo:
    target_node_name: str
    target_node_index: int
    collision_block_index: int
    rigid_body_block_index: int
    rigid_body_type: str
    shape_block_index: int
    shape_chain: tuple[str, str]
    child_shape_block_index: int
    mopp_code: bytes
    mopp_origin: tuple[float, float, float]
    mopp_scale: float


def read_mopp(layout: NifFileLayout, block_index: int) -> MoppData:
    block = layout.blocks[block_index]
    if block.type_name != "bhkMoppBvTreeShape":
        raise ValueError(f"block {block_index} is {block.type_name}, not a MOPP shape")
    payload = layout.payload(block_index)
    if len(payload) < MOPP_FIXED_SIZE:
        raise ValueError("truncated MOPP block")
    values = struct.unpack_from("<iIII f I 3f f B", payload)
    data_size = values[5]
    if len(payload) != MOPP_FIXED_SIZE + data_size:
        raise ValueError("MOPP block size does not match moppDataSize")
    return MoppData(
        child_shape_index=values[0],
        unused=(values[1], values[2], values[3]),
        shape_scale=values[4],
        data_size=data_size,
        origin=(values[6], values[7], values[8]),
        scale=values[9],
        build_type=values[10],
        code=payload[MOPP_FIXED_SIZE:],
    )


def locate_collisions(path: str | Path) -> list[CollisionInfo]:
    vendor = Path(__file__).resolve().parents[3] / "vendor"
    if str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    from pyn.pynifly import NifFile, NiNode

    nif = NifFile(str(Path(path).resolve()))
    nif.nodes
    collisions: list[CollisionInfo] = []
    for node_index, node in sorted(nif.node_ids.items()):
        if not isinstance(node, NiNode):
            continue
        collision = node.collision_object
        if collision is None:
            continue
        body = collision.body
        shape = body.shape
        if shape.blockname != "bhkMoppBvTreeShape":
            continue
        child = shape.child
        code, origin, scale = shape.mopp_data
        target_index = collision.properties.targetID
        target = nif.read_node(id=target_index)
        collisions.append(
            CollisionInfo(
                target_node_name=target.name,
                target_node_index=target_index,
                collision_block_index=collision.id,
                rigid_body_block_index=body.id,
                rigid_body_type=body.blockname,
                shape_block_index=shape.id,
                shape_chain=(shape.blockname, child.blockname),
                child_shape_block_index=child.id,
                mopp_code=code,
                mopp_origin=origin,
                mopp_scale=scale,
            )
        )
    return collisions


def _unpack(format_code: str, data: bytes, offset: int) -> int:
    return struct.unpack_from(f"<{format_code}", data, offset)[0]
