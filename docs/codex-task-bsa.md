# Task: read-only BSA v104/v105 parser (`src/dmfix/core/bsa.py`)

## Goal

A dependency-free (stdlib + `lz4` only) read-only parser for Bethesda BSA archives
(Skyrim LE v104 / Skyrim SE v105) that can (a) list contained files and (b) extract a single
file's bytes by its archive-internal path. Used to pull `.nif` files out of mod BSAs for
collision repair. We never write or repack BSAs.

## Environment

- Project root: this repo. Python: `.venv/Scripts/python` (3.11, `lz4` installed).
- Real test archive (READ-ONLY, do not modify/move):
  `C:/Users/Johnex2x/Documents/DeadMesh-Fix-Tool/Midnight Sun/Midnight Sun.bsa` (~825 MB, v105,
  header starts `BSA\0`, version 0x69, archive flags 0x24 => compressed + retain names… verify
  from the actual bytes).
  Also `Midnight Sun - Update.bsa` in the same folder.
- Ground truth for verification: the same mod's loose extracted meshes exist at
  `C:/Users/Johnex2x/Documents/DeadMesh-Fix-Tool/Midnight Sun meshes/meshes/*.nif` (18 files,
  extracted earlier with BSA Browser). If a same-named file exists inside the BSA (e.g.
  `meshes/<name>.nif` under some subpath — search the listing), extracted bytes should be
  compared. If the loose copies came from `Midnight Sun - Update.bsa` instead, check both.
  NOTE: loose copies may differ from BSA copies if the mod author fixed them — a mismatch in
  content is NOT automatically a parser bug; verify parser correctness primarily via NIF
  validity (see below).

## Format notes (verify against real bytes, don't trust blindly)

- Header (36 bytes): magic "BSA\0", u32 version (104/105), u32 folderRecordsOffset,
  u32 archiveFlags, u32 folderCount, u32 fileCount, u32 totalFolderNameLength,
  u32 totalFileNameLength, u32 fileFlags [+ v105: no extra —  folder records differ instead].
- v105 folder record: u64 nameHash, u32 count, u32 padding, u64 offset (24 bytes);
  v104: u64 hash, u32 count, u32 offset (16 bytes).
- Folder block: if flags bit 0x1 (includeDirectoryNames): bzstring folder name (length-prefixed,
  null-terminated), then file records (u64 nameHash, u32 size, u32 offset).
- File names: one big block of null-terminated strings after all folder blocks
  (if flags 0x2 includeFileNames), in folder order.
- File data: if archive flag 0x4 (compressed by default), each file is compressed UNLESS its
  size field has bit 30 (0x40000000) set — that bit TOGGLES compression per file. Compressed
  v105 data = u32 original size then an LZ4 **frame** stream (lz4.frame.decompress);
  v104 = zlib. If flags bit 0x100 (embedded file names, "retain file name offsets"?? — the
  0x100 flag embeds a bstring full path before the data when set on v104/v105 SSE) — handle it:
  when archiveFlags & 0x100, each file data block starts with a length-prefixed path string.
- Path convention inside BSA: backslash-separated, lowercase.

## Deliverables

1. `src/dmfix/core/bsa.py`:
   ```python
   class BsaArchive:
       def __init__(self, path: str | Path): ...   # parses header + full index eagerly
       @property
       def version(self) -> int
       def namelist(self) -> list[str]              # normalized 'meshes/foo/bar.nif' forward-slash lowercase
       def contains(self, inner_path: str) -> bool  # accepts / or \, any case
       def read(self, inner_path: str) -> bytes     # raises KeyError if absent
       def close(self)                              # context manager support too
   ```
   - Random access via seek; do NOT load the whole archive into RAM.
   - Must handle: uncompressed archives, per-file compression toggle bit, embedded-name flag,
     both v104 (zlib) and v105 (lz4 frame).
2. `tests/test_bsa.py` — runnable with venv python (pytest style ok):
   - open `Midnight Sun.bsa`: header parses, file count matches header, namelist length ==
     header fileCount.
   - find at least one `.nif` entry, extract it, and assert the bytes start with the NIF magic
     `Gamebryo File Format` / `NetImmerse File Format` header line and that the embedded
     version parses (proves decompression is byte-correct, not just "no exception").
   - extract a `.nif` that also exists in the loose set (match by filename) from
     `Midnight Sun.bsa` AND `Midnight Sun - Update.bsa` if present; if bytes equal the loose
     copy, assert equality; if not, print a notice (mod author may have patched loose) and still
     assert NIF-validity.
   - performance sanity: opening the 825MB archive and listing must take well under 10s.

## Constraints

- stdlib + lz4 only. Type hints. No prints in library code (logging ok).
- Read-only: never open archives with write modes.
- If the real archive contradicts the format notes above, TRUST THE BYTES, document the
  discrepancy in a comment, and handle what's actually there.

## Final report format

- What was implemented; how the real archive deviated (if at all) from the spec notes.
- Test output (verbatim).
- Which .nif files were found in each BSA matching the 18 loose fixture names; byte-equality results.
- Open risks / unhandled corners (e.g. Oblivion v103, BA2 — explicitly out of scope).
