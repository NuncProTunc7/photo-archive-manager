"""Streaming SHA-256 of file contents, 1 MiB chunks (design spec section 5.1)."""

import hashlib
from pathlib import Path

CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path) -> str:
    """Hex digest of the file's bytes. Raises OSError; callers record and continue."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()
