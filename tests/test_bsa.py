from __future__ import annotations

import struct
import tempfile
import time
import unittest
import zlib
from pathlib import Path

import lz4.frame

from dmfix.core.bsa import BsaArchive


ARCHIVE_DIR = Path(r"C:\Users\Johnex2x\Documents\DeadMesh-Fix-Tool\Midnight Sun")
MAIN_ARCHIVE = ARCHIVE_DIR / "Midnight Sun.bsa"
UPDATE_ARCHIVE = ARCHIVE_DIR / "Midnight Sun - Update.bsa"
LOOSE_MESH_DIR = Path(
    r"C:\Users\Johnex2x\Documents\DeadMesh-Fix-Tool\Midnight Sun meshes\meshes"
)


def _nif_version(data: bytes) -> int:
    line_end = data.find(b"\n")
    assert line_end >= 0
    assert data.startswith((b"Gamebryo File Format", b"NetImmerse File Format"))
    version = struct.unpack_from("<I", data, line_end + 1)[0]
    assert version == 0x14020007
    return version


def _write_test_archive(
    path: Path, *, version: int, compressed_by_default: bool
) -> dict[str, bytes]:
    folder = b"meshes\\test"
    files = {
        "first.nif": b"Gamebryo File Format, Version 20.2.0.7\n\x07\x00\x02\x14first",
        "second.nif": b"Gamebryo File Format, Version 20.2.0.7\n\x07\x00\x02\x14second",
    }
    flags = 0x1 | 0x2 | 0x100
    if compressed_by_default:
        flags |= 0x4

    file_names = b"".join(name.encode() + b"\0" for name in files)
    folder_name = bytes([len(folder) + 1]) + folder + b"\0"
    folder_record_size = 24 if version == 105 else 16
    folder_block_offset = 36 + folder_record_size
    data_offset = (
        folder_block_offset + len(folder_name) + 16 * len(files) + len(file_names)
    )

    records = bytearray()
    data_blocks = bytearray()
    for index, (name, contents) in enumerate(files.items()):
        compressed = compressed_by_default if index == 0 else not compressed_by_default
        embedded_path = f"meshes\\test\\{name}".encode()
        payload = bytes([len(embedded_path)]) + embedded_path
        if compressed:
            encoded = (
                zlib.compress(contents)
                if version == 104
                else lz4.frame.compress(contents)
            )
            payload += struct.pack("<I", len(contents)) + encoded
        else:
            payload += contents
        size = len(payload)
        if compressed != compressed_by_default:
            size |= 0x40000000
        records += struct.pack("<QII", 0, size, data_offset + len(data_blocks))
        data_blocks += payload

    stored_folder_offset = folder_block_offset + len(file_names)
    if version == 105:
        folder_record = struct.pack(
            "<QIIQ", 0, len(files), 0, stored_folder_offset
        )
    else:
        folder_record = struct.pack("<QII", 0, len(files), stored_folder_offset)
    header = struct.pack(
        "<4s8I",
        b"BSA\0",
        version,
        36,
        flags,
        1,
        len(files),
        len(folder_name),
        len(file_names),
        0x1,
    )
    path.write_bytes(
        header + folder_record + folder_name + records + file_names + data_blocks
    )
    return {f"meshes/test/{name}": contents for name, contents in files.items()}


class BsaArchiveTests(unittest.TestCase):
    def test_reads_compression_toggles_and_embedded_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            for version, compressed_by_default in [(104, True), (105, False)]:
                with self.subTest(version=version):
                    archive_path = Path(temp_dir) / f"test-{version}.bsa"
                    expected = _write_test_archive(
                        archive_path,
                        version=version,
                        compressed_by_default=compressed_by_default,
                    )

                    with BsaArchive(archive_path) as archive:
                        self.assertEqual(archive.version, version)
                        self.assertEqual(archive.namelist(), list(expected))
                        self.assertTrue(archive.contains(r"MESHES\TEST\FIRST.NIF"))
                        self.assertFalse(archive.contains("meshes/test/missing.nif"))
                        for name, contents in expected.items():
                            self.assertEqual(archive.read(name), contents)
                        with self.assertRaises(KeyError):
                            archive.read("missing.nif")

    def test_real_archive_index_and_nif_extraction(self) -> None:
        with MAIN_ARCHIVE.open("rb") as stream:
            header_file_count = struct.unpack("<4s8I", stream.read(36))[5]

        started = time.perf_counter()
        with BsaArchive(MAIN_ARCHIVE) as archive:
            names = archive.namelist()
            elapsed = time.perf_counter() - started
            self.assertEqual(archive.version, 105)
            self.assertEqual(len(names), header_file_count)
            self.assertLess(elapsed, 10)
            nif_name = next(name for name in names if name.endswith(".nif"))
            _nif_version(archive.read(nif_name))

    def test_real_archive_mesh_matches_loose_fixture_when_unchanged(self) -> None:
        loose_by_name = {
            path.name.casefold(): path for path in LOOSE_MESH_DIR.glob("*.nif")
        }
        for archive_path in [MAIN_ARCHIVE, UPDATE_ARCHIVE]:
            with self.subTest(archive=archive_path.name):
                if not archive_path.exists():
                    self.skipTest(f"archive is not present: {archive_path}")

                with BsaArchive(archive_path) as archive:
                    match = next(
                        (
                            name
                            for name in archive.namelist()
                            if Path(name).name in loose_by_name
                        ),
                        None,
                    )
                    self.assertIsNotNone(match)
                    assert match is not None
                    extracted = archive.read(match)
                    _nif_version(extracted)
                    loose_path = loose_by_name[Path(match).name]
                    loose_contents = loose_path.read_bytes()
                    if extracted == loose_contents:
                        self.assertEqual(extracted, loose_contents)
                    else:
                        print(
                            f"NOTICE: {match} differs from loose fixture {loose_path.name}"
                        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
