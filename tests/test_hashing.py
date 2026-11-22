import hashlib

import pytest

from pam.scan.hashing import CHUNK_SIZE, sha256_file

# SHA-256 of zero bytes - a well-known constant.
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_empty_file(tmp_path):
    f = tmp_path / "empty.jpg"
    f.write_bytes(b"")
    assert sha256_file(f) == EMPTY_SHA256


def test_multi_chunk_file_matches_hashlib(tmp_path):
    data = b"a" * (CHUNK_SIZE * 2 + 17)  # forces at least three read() calls
    f = tmp_path / "big.jpg"
    f.write_bytes(data)
    assert sha256_file(f) == hashlib.sha256(data).hexdigest()


def test_missing_file_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        sha256_file(tmp_path / "nope.jpg")
