"""A stand-in exiftool that speaks the -stay_open stdin protocol, for tests.

For each path received it emits a JSON record with SourceFile, PamFakeArgs
(the argument lines it saw - tests assert the frozen read-only args), and any
tags found in a sidecar file named <path>.tags.json. Paths ending in
'unreadable.jpg' produce no record, simulating a file exiftool cannot read.
A batch containing a path ending in 'poison.jpg' emits malformed non-JSON
output; one containing 'dictshape.jpg' emits valid JSON that is not a list -
both simulate broken exiftool output for the whole batch.
"""

import json
import sys
from pathlib import Path


def main() -> None:
    args: list[str] = []
    paths: list[str] = []
    pending_value = False
    closing = False
    for raw in sys.stdin:
        line = raw.rstrip("\n")
        if closing:
            if line == "False":
                return
            closing = False
        if pending_value:
            args.append(line)
            pending_value = False
            continue
        if line == "-stay_open":
            closing = True
            continue
        if line == "-charset":
            args.append(line)
            pending_value = True
            continue
        if line == "-execute":
            if any(p.endswith("dictshape.jpg") for p in paths):
                print(json.dumps({"error": "Unknown option"}))
                print("{ready}")
                sys.stdout.flush()
                args = []
                paths = []
                continue
            if any(p.endswith("poison.jpg") for p in paths):
                print("this is not json")
                print("{ready}")
                sys.stdout.flush()
                args = []
                paths = []
                continue
            records = []
            for p in paths:
                if p.endswith("unreadable.jpg"):
                    continue
                record: dict[str, object] = {
                    "SourceFile": p.replace("\\", "/"),
                    "PamFakeArgs": list(args),
                }
                sidecar = Path(p + ".tags.json")
                if sidecar.exists():
                    record.update(json.loads(sidecar.read_text(encoding="utf-8")))
                records.append(record)
            print(json.dumps(records))
            print("{ready}")
            sys.stdout.flush()
            args = []
            paths = []
            continue
        if line.startswith("-"):
            args.append(line)
        else:
            paths.append(line)


if __name__ == "__main__":
    main()
