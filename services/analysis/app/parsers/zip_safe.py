"""Safe ZIP extraction: path-traversal proof, zip-bomb resistant, extension-allowlisted.
Uploaded archives are treated as hostile input; nothing is ever executed."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

DEFAULT_ALLOWED_EXTENSIONS = {
    ".kicad_sch", ".kicad_pcb", ".kicad_pro", ".net", ".cir", ".spice", ".csv", ".pdf", ".obj", ".mtl",
}

MAX_COMPRESSION_RATIO = 200


class UnsafeZipError(ValueError):
    pass


def safe_extract_zip(
    zip_bytes: bytes,
    dest_dir: Path,
    *,
    max_files: int = 500,
    max_total_mb: int = 100,
    max_file_mb: int = 50,
    allowed_extensions: set[str] | None = None,
) -> tuple[list[Path], list[str]]:
    """Extract to dest_dir, flattening directory structure (basenames only, deduplicated).
    Returns (extracted paths, warnings). Raises UnsafeZipError on hostile archives."""
    allowed = allowed_extensions or DEFAULT_ALLOWED_EXTENSIONS
    warnings: list[str] = []
    extracted: list[Path] = []
    dest_dir = dest_dir.resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    max_total = max_total_mb * 1024 * 1024
    max_file = max_file_mb * 1024 * 1024
    total = 0

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise UnsafeZipError(f"invalid ZIP archive: {exc}") from exc

    infos = zf.infolist()
    if len(infos) > max_files:
        raise UnsafeZipError(f"archive has {len(infos)} entries (limit {max_files})")

    seen_names: set[str] = set()
    for info in infos:
        raw_name = info.filename
        if info.is_dir():
            continue
        # symlinks: external_attr high bits 0xA000 = symlink
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            warnings.append(f"skipped symlink entry: {raw_name}")
            continue
        if raw_name.startswith("/") or raw_name.startswith("\\") or ".." in Path(raw_name).parts:
            raise UnsafeZipError(f"path traversal attempt in entry: {raw_name}")
        if ":" in raw_name.split("/")[0] and len(raw_name.split("/")[0]) == 2:
            raise UnsafeZipError(f"absolute Windows path in entry: {raw_name}")
        base = Path(raw_name).name
        if not base or base.startswith("."):
            warnings.append(f"skipped hidden/empty entry: {raw_name}")
            continue
        ext = Path(base).suffix.lower()
        if ext not in allowed:
            warnings.append(f"skipped disallowed file type: {raw_name}")
            continue
        if info.file_size > max_file:
            raise UnsafeZipError(f"entry too large: {raw_name} ({info.file_size} bytes)")
        if info.compress_size > 0 and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
            raise UnsafeZipError(f"suspicious compression ratio for {raw_name}")
        total += info.file_size
        if total > max_total:
            raise UnsafeZipError(f"total uncompressed size exceeds {max_total_mb} MB")

        # deduplicate basenames from nested dirs
        candidate = base
        n = 1
        while candidate in seen_names:
            candidate = f"{Path(base).stem}_{n}{ext}"
            n += 1
        seen_names.add(candidate)

        target = (dest_dir / candidate).resolve()
        if not str(target).startswith(str(dest_dir)):
            raise UnsafeZipError(f"resolved path escapes destination: {raw_name}")
        with zf.open(info) as src:
            payload = src.read(max_file + 1)
            if len(payload) > max_file:
                raise UnsafeZipError(f"entry exceeded declared size: {raw_name}")
        target.write_bytes(payload)
        extracted.append(target)

    return extracted, warnings
