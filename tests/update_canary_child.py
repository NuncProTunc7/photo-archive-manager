"""A fresh pam process for the release-client canary (E4, M15 Task 12). Not a test module.

    python update_canary_child.py archive|control ROOT

It installs a recording watch over `socket` FIRST (connections, name lookups, datagram sends -
design 10's three records), then drives the REAL entry point, `pam.cli.main`, and prints one JSON
object: which `pam.update` modules are loaded after `import pam.cli` and after the commands, what
the watch recorded, and every command's exit code.

  archive  every archive command that runs headless, in a chain that does real work: scan, status,
           enrich, plan, report skipped / duplicates, apply --all (stdin says `apply`), report
           session, undo, verify, bare `pam`, forget. `pam review` and `pam start` serve until
           Ctrl+C and are covered by the AST guard and the page-chain canary instead.
  control  `pam update --check`, which in this venv takes the pip route (no request): it proves
           the detector SEES the lazy import the CLI makes, and one loopback connection, one
           name lookup and one loopback datagram: each of the three watches sees.

A fresh interpreter, because the suite's own process has imported `pam.update` long before.
The exiftool step uses the suite's stand-in (`tests/fake_exiftool.py`), patched in this process
only - nothing about the watch depends on which exiftool answers.
"""

import json
import socket
import sqlite3
import sys
from pathlib import Path

RECORD: dict[str, list[str]] = {"attempts": [], "lookups": [], "sends": []}


def _watch() -> None:
    def wrap_method(name: str, kind: str, pick) -> None:
        original = getattr(socket.socket, name)

        def wrapper(self, *args, **kwargs):
            RECORD[kind].append(repr(pick(args, kwargs)))
            return original(self, *args, **kwargs)

        setattr(socket.socket, name, wrapper)

    def wrap_function(name: str) -> None:
        original = getattr(socket, name)

        def wrapper(*args, **kwargs):
            RECORD["lookups"].append(repr(args[0] if args else kwargs.get("host")))
            return original(*args, **kwargs)

        setattr(socket, name, wrapper)

    for name in ("connect", "connect_ex"):
        wrap_method(name, "attempts", lambda a, k: a[0] if a else k.get("address"))
    wrap_method("sendto", "sends", lambda a, k: a[-1] if a else k.get("address"))
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        wrap_function(name)


def _loaded() -> list[str]:
    return sorted(m for m in sys.modules if m == "pam.update" or m.startswith("pam.update."))


def main() -> int:
    mode, root = sys.argv[1], Path(sys.argv[2])
    _watch()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import pam.cli
    from pam.paths import workspace_if_exists
    from pam.scan import exiftool

    after_import = _loaded()
    from archives import FAKE_COMMAND, put  # after the first reading: it imports pam modules

    exiftool.default_command = lambda: list(FAKE_COMMAND)
    codes: dict[str, int] = {}

    def run(name: str, *args: str) -> None:
        codes[name] = pam.cli.main(list(args))

    if mode == "control":
        run("update --check", "update", "--check")
        # ...and proves the WATCH sees: one real connection to a loopback listener.
        with socket.create_server(("127.0.0.1", 0)) as listener:
            with socket.socket() as probe:
                probe.connect(listener.getsockname())
        # ...one name lookup and one loopback datagram, so the LOOKUP and SEND watches are proven
        # to see as well (Stage 1 review, M4348/M4349: without these, either watch could be
        # removed with every row green - the archive row's empty lists would mean nothing).
        socket.getaddrinfo("localhost", None)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(("127.0.0.1", 0))
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(b"x", receiver.getsockname())
    else:
        root.mkdir(parents=True, exist_ok=True)
        put(root, "IMG_1.JPG", "2019:07:04 18:23:11")
        put(root, "IMG_2.JPG", "2020:08:05 09:15:00")
        archive = ("--archive", str(root))
        run("scan", "scan", str(root))
        run("status", "status", *archive)
        run("enrich", "enrich", "geocode", *archive)
        run("plan", "plan", *archive)
        workspace = workspace_if_exists(root)
        assert workspace is not None
        with sqlite3.connect(workspace.db_path) as conn:
            plan_id = conn.execute("SELECT MAX(id) FROM plans").fetchone()[0]
        run("report skipped", "report", "skipped", *archive)
        run("report duplicates", "report", "duplicates", *archive)
        run("apply", "apply", str(plan_id), "--all", *archive)
        with sqlite3.connect(workspace.db_path) as conn:
            (session,) = conn.execute("SELECT DISTINCT session_id FROM journal").fetchone()
            renamed = conn.execute(
                "SELECT COUNT(*) FROM journal WHERE state='done'"
            ).fetchone()[0]
        codes["renamed"] = renamed
        run("report session", "report", "session", session, *archive)
        run("undo", "undo", session, *archive)
        run("verify", "verify", *archive)
        run("pam")
        run("forget", "forget", str(root))
    print(
        json.dumps({"after_import": after_import, "after": _loaded(), "codes": codes, **RECORD})
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
