from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

import lz4.frame


_HEADER = struct.Struct("<4s8I")
_FOLDER_RECORD_V104 = struct.Struct("<QII")
_FOLDER_RECORD_V105 = struct.Struct("<QIIQ")
_FILE_RECORD = struct.Struct("<QII")
_COMPRESS_TOGGLE = 0x40000000
_SIZE_MASK = 0x3FFFFFFF


@dataclass(frozen=True)
class _Entry:
    offset: int
    size: int
    compressed: bool


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").strip("/").casefold()


class BsaArchive:
    """Read-only index and random-access reader for BSA versions 104 and 105."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._stream = self._path.open("rb")
        self._version = 0
        self._archive_flags = 0
        self._names: list[str] = []
        self._entries: dict[str, _Entry] = {}
        try:
            self._parse_index()
        except Exception:
            self._stream.close()
            raise

    @property
    def version(self) -> int:
        return self._version

    def namelist(self) -> list[str]:
        return self._names.copy()

    def contains(self, inner_path: str) -> bool:
        return _normalize_path(inner_path) in self._entries

    def read(self, inner_path: str) -> bytes:
        normalized = _normalize_path(inner_path)
        try:
            entry = self._entries[normalized]
        except KeyError:
            raise KeyError(inner_path) from None

        self._stream.seek(entry.offset)
        payload = self._read_exact(entry.size)
        if self._archive_flags & 0x100:
            if not payload:
                raise ValueError(f"empty embedded-name block for {normalized!r}")
            name_length = payload[0]
            if len(payload) < name_length + 1:
                raise ValueError(f"truncated embedded name for {normalized!r}")
            payload = payload[name_length + 1 :]

        if not entry.compressed:
            return payload
        if len(payload) < 4:
            raise ValueError(f"truncated compressed data for {normalized!r}")

        original_size = struct.unpack_from("<I", payload)[0]
        compressed = payload[4:]
        try:
            if self._version == 104:
                result = zlib.decompress(compressed)
            else:
                result = lz4.frame.decompress(compressed)
        except (zlib.error, RuntimeError) as error:
            raise ValueError(f"invalid compressed data for {normalized!r}") from error
        if len(result) != original_size:
            raise ValueError(
                f"decompressed size mismatch for {normalized!r}: "
                f"expected {original_size}, got {len(result)}"
            )
        return result

    def close(self) -> None:
        self._stream.close()

    def __enter__(self) -> BsaArchive:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def _parse_index(self) -> None:
        (
            magic,
            version,
            folder_records_offset,
            archive_flags,
            folder_count,
            file_count,
            _total_folder_name_length,
            total_file_name_length,
            _file_flags,
        ) = _HEADER.unpack(self._read_exact(_HEADER.size))
        if magic != b"BSA\0":
            raise ValueError(f"not a BSA archive: {self._path}")
        if version not in (104, 105):
            raise ValueError(f"unsupported BSA version {version}")
        if not archive_flags & 0x1 or not archive_flags & 0x2:
            raise ValueError("BSA directory and file names are required")

        self._version = version
        self._archive_flags = archive_flags
        folder_struct = (
            _FOLDER_RECORD_V105 if version == 105 else _FOLDER_RECORD_V104
        )
        self._stream.seek(folder_records_offset)
        folder_records: list[tuple[int, int]] = []
        for _ in range(folder_count):
            values = folder_struct.unpack(self._read_exact(folder_struct.size))
            folder_records.append((values[1], values[-1]))

        records_end = folder_records_offset + folder_count * folder_struct.size
        folder_offset_bias = 0
        if folder_records:
            first_offset = folder_records[0][1]
            # Actual Skyrim SE archives store offsets with the complete filename
            # block length added, despite many format summaries calling them direct.
            if first_offset - total_file_name_length == records_end:
                folder_offset_bias = total_file_name_length
            elif first_offset != records_end:
                raise ValueError("unexpected first folder-block offset")

        indexed_records: list[tuple[str, int, int]] = []
        folder_blocks_end = records_end
        for folder_file_count, stored_offset in folder_records:
            self._stream.seek(stored_offset - folder_offset_bias)
            folder_name = self._read_bzstring()
            for _ in range(folder_file_count):
                _name_hash, size, offset = _FILE_RECORD.unpack(
                    self._read_exact(_FILE_RECORD.size)
                )
                indexed_records.append((folder_name, size, offset))
            folder_blocks_end = max(folder_blocks_end, self._stream.tell())

        if len(indexed_records) != file_count:
            raise ValueError(
                f"file-count mismatch: header has {file_count}, index has "
                f"{len(indexed_records)}"
            )

        self._stream.seek(folder_blocks_end)
        names_blob = self._read_exact(total_file_name_length)
        encoded_names = names_blob.split(b"\0")
        if not encoded_names or encoded_names[-1] != b"":
            raise ValueError("file-name block is not null-terminated")
        encoded_names.pop()
        if len(encoded_names) != file_count:
            raise ValueError(
                f"file-name count mismatch: expected {file_count}, got "
                f"{len(encoded_names)}"
            )

        compressed_by_default = bool(archive_flags & 0x4)
        for (folder_name, raw_size, offset), encoded_name in zip(
            indexed_records, encoded_names, strict=True
        ):
            file_name = encoded_name.decode("cp1252")
            name = _normalize_path(f"{folder_name}/{file_name}")
            if name in self._entries:
                raise ValueError(f"duplicate archive path {name!r}")
            toggled = bool(raw_size & _COMPRESS_TOGGLE)
            self._names.append(name)
            self._entries[name] = _Entry(
                offset=offset,
                size=raw_size & _SIZE_MASK,
                compressed=compressed_by_default != toggled,
            )

    def _read_bzstring(self) -> str:
        length = self._read_exact(1)[0]
        encoded = self._read_exact(length)
        if not encoded or encoded[-1] != 0:
            raise ValueError("folder name is not null-terminated")
        return encoded[:-1].decode("cp1252")

    def _read_exact(self, size: int) -> bytes:
        data = self._stream.read(size)
        if len(data) != size:
            raise ValueError(f"truncated BSA archive: {self._path}")
        return data
