"""Human-test manifest: snapshot an archive, then prove nothing was lost or altered.

M5 is the first milestone that renames real files, so the human test needs ground
truth rather than eyeballing. Usage:

    .venv\\Scripts\\python tools\\ht_manifest.py snapshot "D:\\ARCHIVE" before.json
    ... run pam apply ...
    .venv\\Scripts\\python tools\\ht_manifest.py verify "D:\\ARCHIVE" before.json --bytes
    ... run pam undo ...
    .venv\\Scripts\\python tools\\ht_manifest.py verify "D:\\ARCHIVE" before.json --exact

Two modes, matching the two things M5 promises:

  --bytes  every file's CONTENT still exists somewhere in the tree, and no
           content appeared or vanished. Names may differ. This is what apply
           guarantees: renames only, no file lost, no bytes modified.
  --exact  paths AND content match the snapshot byte for byte. This is what undo
           guarantees: "restores exact original names".

Exit 0 on pass, 1 on any discrepancy, with the offending paths printed. Reads
only; it never writes to the archive.

M13 Task 16 (HT-M13) adds two things, each of which REFUSES rather than guesses:

    snapshot ROOT MANIFEST --expect N
        counts first and writes NO manifest unless the tree holds exactly N files - HT-M12's F2
        was a snapshot taken after files had been copied in, which no later proof could repair.
    changed MANIFEST ROOT --expect K
        after an apply: every hash still present (the --bytes multiset), and exactly K paths whose
        content now lives under a different name. Note the order: MANIFEST first, as the HT-M13
        plan spells it; a folder where the manifest belongs is refused, never swapped.
"""

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import TypedDict

CHUNK = 1024 * 1024


class Entry(TypedDict):
    size: int
    sha256: str


Manifest = dict[str, Entry]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def snapshot(root: Path) -> Manifest:
    out: Manifest = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        out[rel] = Entry(size=path.stat().st_size, sha256=sha256(path))
    return out


def _report(label: str, items: list[str]) -> None:
    print(f"  {label}: {len(items)}")
    for item in items[:10]:
        print(f"      {item}")
    if len(items) > 10:
        print(f"      ... and {len(items) - 10} more")


def verify_bytes(before: Manifest, after: Manifest) -> bool:
    """Content-preserving check: the multiset of hashes is unchanged."""
    b = Counter(v["sha256"] for v in before.values())
    a = Counter(v["sha256"] for v in after.values())
    if b == a:
        print(f"PASS  {len(before)} files, every hash still present, none added.")
        return True
    before_by_hash: dict[str, list[str]] = {}
    for rel, v in before.items():
        before_by_hash.setdefault(v["sha256"], []).append(rel)
    after_by_hash: dict[str, list[str]] = {}
    for rel, v in after.items():
        after_by_hash.setdefault(v["sha256"], []).append(rel)
    # Report only as many paths as the count actually differs by. Listing every
    # file that happens to share an over-represented hash would name innocent
    # duplicates alongside the real change, which is the kind of misleading
    # output this project does not ship.
    lost = [p for h, n in (b - a).items() for p in sorted(before_by_hash[h])[:n]]
    gained = [p for h, n in (a - b).items() for p in sorted(after_by_hash[h])[:n]]
    print("FAIL  content changed")
    if lost:
        _report("LOST (content no longer anywhere in the tree)", sorted(lost))
    if gained:
        _report("APPEARED (content not in the snapshot)", sorted(gained))
    return False


def verify_exact(before: Manifest, after: Manifest) -> bool:
    """Restoration check: same paths, same content."""
    missing = sorted(set(before) - set(after))
    extra = sorted(set(after) - set(before))
    changed = sorted(
        rel for rel in set(before) & set(after) if before[rel]["sha256"] != after[rel]["sha256"]
    )
    if not (missing or extra or changed):
        print(f"PASS  {len(before)} files, exact paths and exact content.")
        return True
    print("FAIL  tree does not match the snapshot")
    if missing:
        _report("MISSING (in snapshot, not on disk)", missing)
    if extra:
        _report("EXTRA (on disk, not in snapshot)", extra)
    if changed:
        _report("CONTENT CHANGED", changed)
    return False


STOP_SENTENCE = (
    "STOP: {n} files, expected {N}. Something was added to or removed from the folder before the"
    " snapshot. Put it back the way it was, then run this again."
)


def snapshot_checked(root: Path, expect: int | None) -> tuple[Manifest | None, str | None]:
    """The snapshot, or None and the STOP sentence when `expect` is given and not met.

    The count is taken from the SAME walk that would be written, so there is no window between
    counting and recording in which a sync client could add a file.
    """
    data = snapshot(root)
    if expect is not None and len(data) != expect:
        return None, STOP_SENTENCE.format(n=len(data), N=expect)
    return data, None


def renamed_paths(before: Manifest, after: Manifest) -> list[tuple[str, str]]:
    """(old path, new path) for every file whose content is now under a different path.

    Per hash, the paths present on both sides are unchanged; the rest are paired in sorted
    order. Only meaningful when the hash multisets are equal - `changed` checks that first.
    """
    before_by_hash: dict[str, list[str]] = {}
    for rel, v in before.items():
        before_by_hash.setdefault(v["sha256"], []).append(rel)
    after_by_hash: dict[str, list[str]] = {}
    for rel, v in after.items():
        after_by_hash.setdefault(v["sha256"], []).append(rel)
    pairs: list[tuple[str, str]] = []
    for digest, olds in before_by_hash.items():
        news = after_by_hash.get(digest, [])
        gone = sorted(set(olds) - set(news))
        came = sorted(set(news) - set(olds))
        pairs.extend(zip(gone, came, strict=False))
    return sorted(pairs)


def verify_changed(before: Manifest, after: Manifest, expect: int) -> bool:
    """`changed`: every hash present, and exactly `expect` files renamed. PASS or FAIL, printed."""
    if Counter(v["sha256"] for v in before.values()) != Counter(
        v["sha256"] for v in after.values()
    ):
        verify_bytes(before, after)  # prints FAIL and names what was lost or appeared
        return False
    pairs = renamed_paths(before, after)
    k, n = len(pairs), len(before)
    if k == expect:
        print(f"PASS  {k} files renamed, {n - k} unchanged, every hash present")
        return True
    print(f"FAIL  {k} files renamed, expected {expect} ({n - k} unchanged, every hash present)")
    _report("RENAMED (old -> new)", [f"{old} -> {new}" for old, new in pairs])
    return False


def _expect(argv: list[str]) -> int | None | str:
    """The `--expect N` value; None if absent; an error sentence if malformed."""
    if "--expect" not in argv:
        return None
    at = argv.index("--expect")
    if at + 1 >= len(argv) or not argv[at + 1].isdigit():
        return "--expect needs a whole number, e.g. --expect 100"
    return int(argv[at + 1])


def _changed(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    manifest_path = Path(argv[1]).expanduser()
    root = Path(argv[2]).expanduser().resolve()
    expect = _expect(argv)
    if expect is None:
        print("changed needs --expect K: the number of renames `pam apply` reported")
        return 2
    if isinstance(expect, str):
        print(expect)
        return 2
    if not manifest_path.is_file():
        print(f"Not a manifest file: {manifest_path} (the order is: changed MANIFEST ROOT)")
        return 2
    if not root.is_dir():
        print(f"Not a folder: {root} (the order is: changed MANIFEST ROOT)")
        return 2
    before: Manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return 0 if verify_changed(before, snapshot(root), expect) else 1


def main(argv: list[str]) -> int:
    if argv and argv[0] == "changed":
        return _changed(argv)
    if len(argv) < 3:
        print(__doc__)
        return 2
    command, root_arg, manifest_arg = argv[0], argv[1], argv[2]
    root = Path(root_arg).expanduser().resolve()
    manifest_path = Path(manifest_arg).expanduser()
    if not root.is_dir():
        print(f"Not a folder: {root}")
        return 2

    if command == "snapshot":
        expect = _expect(argv)
        if isinstance(expect, str):
            print(expect)
            return 2
        data, stop = snapshot_checked(root, expect)
        if data is None:
            print(stop)
            return 2
        manifest_path.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        total = sum(v["size"] for v in data.values())
        print(f"Snapshot: {len(data)} files, {total / 1e9:.2f} GB -> {manifest_path}")
        return 0

    if command == "verify":
        before: Manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        after = snapshot(root)
        if "--exact" in argv:
            return 0 if verify_exact(before, after) else 1
        if "--bytes" in argv:
            return 0 if verify_bytes(before, after) else 1
        print("verify needs --bytes (after apply) or --exact (after undo)")
        return 2

    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
