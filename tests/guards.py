"""The architectural guards, as AST walks: filesystem moves, journal resolves, network.

All three answer the same normative question (spec 7.1 rev 7, spec 10): does this
module reach past the one implementation it is supposed to use? `rename.py` owns the
single verified
filesystem move and `journal_ops.py` owns the single journal state machine, and a
second implementation of either - added because it was convenient - is how apply,
undo and recovery drift into three answers to "check, then move an irreplaceable
file" by M8.

M6 Task 8 adds a third of the same shape and for the same reason: spec 10 says the
only HTTP code in the codebase is `pam/net.py`, and `network_import_sites` makes that
mechanical instead of a promise a reviewer has to re-check.

M7 Task 3 adds a fourth, and it is the one that proves the pattern was needed:
`pam/catalog/repo/__init__.py` documents its deliberate cross-table reaches in prose,
and that inventory has already drifted twice (1 reach, then 3, then 4) because nothing
but a reader's diligence held it. `sql_table_names` turns it into a red test.

They live here rather than in the test modules that first needed them because they
are shared: `test_apply.py` imported `move_call_sites` from `test_rename.py`, and
`test_execute_unit.py` imported `resolve_call_sites` from `test_apply.py`. M5 Tasks 8
and 9 add two more consumers. Importing a *test* module by name to borrow a helper
couples unrelated files through collection order and makes deleting a test somebody
else imports a surprise.

All are AST walks rather than greps, following `tests/test_repo.py`'s guard: a text
search matches its own prose (this docstring says `os.rename` and `urllib.request` out
loud, and a grep for either would fire on this very file) and misses `import os as o`
or `import urllib.request as u`, which is exactly the shape someone reaches for when a
plain import trips a guard. Each is proven ENFORCING by planted-source tests, not
merely present.
"""

import ast
import re


def move_call_sites(source: str) -> list[tuple[str, str]]:
    """Every filesystem-move call in `source`, as (enclosing function, call).

    An AST walk rather than a grep, following `tests/test_repo.py`'s fault-injection
    guard: a text search matches its own prose (this docstring says `os.rename` out
    loud) and misses `import os as o`, which is exactly the shape someone reaches for
    when a plain import trips a guard.

    `.replace` is only an offence when its receiver is the `os` module or when it
    takes a single argument - `str.replace(old, new)` takes two and is all over the
    codebase, while `Path.replace(target)` takes one and would move a file just as
    silently as `os.replace` does. `shutil.move` is included because it is the third
    route to the same place, and on POSIX it overwrites.
    """
    tree = ast.parse(source)
    os_names: set[str] = set()
    shutil_names: set[str] = set()
    direct: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    os_names.add(alias.asname or "os")
                elif alias.name == "shutil":
                    shutil_names.add(alias.asname or "shutil")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "os":
                for alias in node.names:
                    if alias.name in ("rename", "replace"):
                        direct[alias.asname or alias.name] = f"os.{alias.name}"
            elif node.module == "shutil":
                for alias in node.names:
                    if alias.name == "move":
                        direct[alias.asname or alias.name] = "shutil.move"

    def kind(call: ast.Call) -> str | None:
        func = call.func
        if isinstance(func, ast.Name):
            return direct.get(func.id)
        if not isinstance(func, ast.Attribute):
            return None
        receiver = func.value
        on_os = isinstance(receiver, ast.Name) and receiver.id in os_names
        on_shutil = isinstance(receiver, ast.Name) and receiver.id in shutil_names
        if func.attr == "rename":
            return "os.rename" if on_os else "path.rename"
        if func.attr == "replace":
            if on_os:
                return "os.replace"
            if len(call.args) == 1 and not call.keywords:
                return "path.replace"
        if func.attr == "move" and on_shutil:
            return "shutil.move"
        return None

    sites: list[tuple[str, str]] = []

    def walk(node: ast.AST, enclosing: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                walk(child, child.name)
                continue
            if isinstance(child, ast.Call) and (found := kind(child)) is not None:
                sites.append((enclosing, found))
            walk(child, enclosing)

    walk(tree, "<module>")
    return sites


# The complete allowlist for the production package, as (module, function, call).
# Sorted comparison against a LIST, not a set: a second identical call inside
# perform_verified_rename must fail the guard, and a set would swallow the duplicate.
ALLOWED_MOVE_SITES = [
    # spec 7.1 rev 7: THE single verified rename. Apply, undo and recovery share it.
    ("execute/rename.py", "perform_verified_rename", "os.rename"),
    # M11 TASK 13, AND IT IS `record_archive_root`'S CLASS EXACTLY, not a new one. A20 is
    # normative that `pam faces export` is "written to a temp file and atomically
    # replaced, with partial sidecars deleted on failure", and `os.replace` is the only
    # way to say that on this platform. BOTH ENDS ARE PAM'S OWN FILES UNDER PAM_HOME: the
    # source is a `.tmp` this function created seconds earlier in the destination
    # directory, and the destination is a `faces-export-*` file under the workspace's
    # `reports/`, which `export._refuse_archive_path` RAISES on if it ever resolves inside
    # the archive root. No photograph is at either end and none can be.
    #
    # THIS LIST GOES 2 -> 3 AND THE M11 CLOSE MUST RATIFY IT (plan Task 14 Step 5 compares
    # the four guard lists against `v0.1-m10` at 2 / 4 / 5 / 2). Deleting the entry would
    # not make the guard stronger - it would make a data-access export fail a rule about
    # renaming photographs, which is the same argument the entry above it carries.
    ("faces/export.py", "_write_pair", "os.replace"),
    # Sanctioned and nowhere near the archive: a pam-owned pointer file under
    # PAM_HOME whose previous content is MEANT to be replaced, written atomically
    # (M4 finding 12). Deleting this entry would not make the guard stronger - it
    # would make paths.py fail a rule about renaming photographs.
    ("paths.py", "record_archive_root", "os.replace"),
]


def resolve_call_sites(source: str) -> list[str]:
    """Every `repo.resolve(...)`-shaped call, by AST rather than by text.

    A text search would match this module's own prose - the same trap `test_repo.py`
    documents - and would miss `from pam.catalog.repo import resolve`, which is the
    shape someone reaches for when a plain call trips a guard.
    """
    tree = ast.parse(source)
    direct = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module in ("pam.catalog.repo", "pam.catalog.repo.journal")
        for alias in node.names
        if alias.name == "resolve"
    }
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in direct:
            found.append(func.id)
        elif (
            isinstance(func, ast.Attribute)
            and func.attr == "resolve"
            # Only the journal state machine, not every `.resolve()` in Python:
            # `Path.resolve()` is ordinary code, and a guard that fires on ordinary
            # code gets worked around rather than obeyed (test_repo.py's lesson).
            and isinstance(func.value, ast.Name)
            and func.value.id in ("repo", "journal")
        ):
            found.append(f"{func.value.id}.resolve")
    return found


# --- the network guard (spec 10) ---------------------------------------------------

# The three spec 10 names outright: an HTTP client pam did not write and cannot
# constrain. `requests` and `httpx` would also be new dependencies, which the standing
# constraints forbid without a spec amendment - but the guard does not rely on that,
# because a vendored copy or a transitive install would satisfy the import just as well.
BANNED_NETWORK_MODULES = ("requests", "httpx", "urllib.request")

# The wider set: stdlib modules that can open a socket. Spec 10 says the only HTTP code
# in the codebase is `net.py`, and that claim is about capability, not about which
# third-party library was avoided - `http.client` reaches the network exactly as well as
# `requests` does. `urllib.parse` is deliberately absent: it is string manipulation and
# banning it would be a guard firing on ordinary code, which is how guards get worked
# around rather than obeyed (test_repo.py's lesson).
NETWORK_CAPABLE_MODULES = (
    "socket",
    "socketserver",
    "ssl",
    "http",
    "urllib.request",
    "ftplib",
    "smtplib",
    "poplib",
    "imaplib",
    "telnetlib",
    "xmlrpc",
    "asyncio",
    "webbrowser",
    "requests",
    "httpx",
    "aiohttp",
)

# Every network-capable import the production package is allowed to contain, as
# (module, imported name). Spec 10: exactly one module may reach the network.
ALLOWED_NETWORK_IMPORTS = [
    ("net.py", "http.client"),
    ("net.py", "socket"),
    # spec 8.2's loopback review server, stdlib per amendment A1 (never FastAPI or
    # uvicorn - the runtime dependency closure was `{click, pam}` when M7 chose this, and
    # the vision treats that as a property rather than an accident). It binds
    # `("127.0.0.1", 0)` and nothing else; `tests/test_webui_server.py` pins the bound
    # address off the live server object.
    #
    # M9 Task 6 SPENT the closure - design 1's four image and geocoding packages, listed
    # in `tests/test_deps.py`. THIS LIST DID NOT MOVE, and that is the point: none of the
    # four is imported by anything under `pam/`, and `reverse_geocoder` in particular
    # imports `urllib.request` inside ITS OWN package, which this guard neither sees nor
    # should. What holds S4 for a third-party dependency is behaviour, not an AST walk -
    # `tests/test_net_canary.py` runs the geocoder under a socket wrapper.
    ("webui/server.py", "http.server"),
    # spec 8.2 "opens the browser". This navigates the USER's machine to pam's own
    # loopback URL - it is not egress of pam's, and the printed URL is authoritative if
    # it fails. Spec 10 rev 12 names exactly these two modules as M7's, and names them
    # as ANTICIPATED: each entry here is earned by the import that landed beside it.
    ("webui/server.py", "webbrowser"),
    # E3 / E10 (M15 Task 12, OD-2): `pam update`'s RELEASE CLIENT, the second network module and
    # the only one allowed a non-loopback host - exactly `api.github.com`, `github.com` and
    # `release-assets.githubusercontent.com` (spike 10B), HTTPS on 443, checked before resolution.
    # `http.client` speaks HTTP over the socket it is handed; `ssl` verifies the certificate
    # (`create_default_context`, never off); `socket` resolves and connects to the checked address
    # (net.py's rule: dial what was checked). `pam/net.py` is unchanged and stays loopback-only.
    ("update/fetch.py", "http.client"),
    ("update/fetch.py", "socket"),
    ("update/fetch.py", "ssl"),
]

# --- the release-client reach guard (E4, M15 Task 12) -------------------------------------

# E4: "No command that touches an archive imports `pam.update`." The package's only importer outside
# `pam/update/` itself is `pam/cli.py`, and only inside the body of its `update` command - so the
# release client is loaded by `pam update` and by nothing else. Keyed by (module, the function the
# import sits in): AN IMPORT IS NOT A WIRING, so the enclosing function is part of the entry, and
# `tests/test_update_isolation.py` checks that the function click registered is that one.
UPDATE_PACKAGE = "pam.update"
ALLOWED_UPDATE_IMPORTS = [("cli.py", "update")]


def update_import_sites(source: str, package: str = "pam") -> list[tuple[str, str]]:
    """Every import of `pam.update` (or a module under it) in `source`, as (dotted name, the
    enclosing function's qualified name or `<module>`), in source order.

    Every spelling: `import pam.update.x`, `from pam.update import x`, `from pam import update`,
    RELATIVE forms resolved against `package` (the importing module's package: `from .update import
    command` in `pam/cli.py`), and `__import__` / `importlib.import_module` with a constant name.
    """

    def under(dotted: str) -> bool:
        return dotted == UPDATE_PACKAGE or dotted.startswith(UPDATE_PACKAGE + ".")

    def absolute(module: str, level: int) -> str:
        if not level:
            return module
        parts = package.split(".")
        base = ".".join(parts[: len(parts) - level + 1])
        return f"{base}.{module}" if module else base

    sites: list[tuple[str, str]] = []

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        where = ".".join(scope) or "<module>"
        if isinstance(node, ast.Import):
            sites.extend((alias.name, where) for alias in node.names if under(alias.name))
        elif isinstance(node, ast.ImportFrom):
            module = absolute(node.module or "", node.level)
            if under(module):
                sites.append((module, where))
            else:
                for alias in node.names:
                    if under(f"{module}.{alias.name}"):
                        sites.append((f"{module}.{alias.name}", where))
        elif isinstance(node, ast.Call):
            func = node.func
            dynamic = (isinstance(func, ast.Name) and func.id == "__import__") or (
                isinstance(func, ast.Attribute) and func.attr == "import_module"
            )
            if dynamic and node.args and isinstance(node.args[0], ast.Constant):
                name = node.args[0].value
                if isinstance(name, str) and under(name):
                    sites.append((name, where))
        inner = scope
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            inner = (*scope, node.name)
        for child in ast.iter_child_nodes(node):
            visit(child, inner)

    visit(ast.parse(source), ())
    return sites


# --- the webui reach guard (M7's defining property, spec 8.2) -----------------------

# "There is still one mutation path (vision principle 1): two ways to start it, never two
# implementations of it" (spec 8.2, rev 34 - it superseded M7's "The UI reads and approves; it
# never applies" when the page began to rename through U19's one door). Until this guard that
# was a sentence. `ALLOWED_MOVE_SITES` above is not enough on its own: it counts the places that
# CALL a move, so a webui module that imported `perform_verified_rename` and called it would be
# counted against `execute/rename.py`'s blessed entry and pass. This forbids the reach itself.
WEBUI_PACKAGE = "pam.webui"
FORBIDDEN_WEBUI_PACKAGE = "pam.execute"

# The modules under `pam/execute/` a webui module may import, as (webui module, imported module)
# pairs - PER MODULE since M14 Task 6a (U19): before it this was a list of two module names any
# webui module could import. Written as an allowlist over the whole package rather than a
# denylist of the mutation modules, so that a module added to `pam/execute/` is forbidden by
# default; and keyed by the IMPORTING module, so that a second webui module reaching
# `pam.execute.apply` is a failure even though `rename_jobs.py` may (U19: "exactly one webui
# module").
#
# THIS LIST GOES 2 -> 6 AND THE M14 CLOSE MUST RATIFY IT (U19: "the M14 close must ratify it").
# Four entries are the two old names keyed to the modules that import them today (`git grep`
# at `87e7f8e`); two are U19's new door, listed by Task 6a ahead of Task 6b's use.
ALLOWED_WEBUI_EXECUTE_IMPORTS = [
    # Every archive-scoped command wires spec 7.2 recovery, and `test_recover.py`'s wiring guard
    # REQUIRES this call to exist in `pam review`. It heals a journal; it cannot move a file
    # (`test_recovery_never_moves_a_file_or_resolves_a_journal_row_itself` pins that from the
    # other side).
    ("webui/__init__.py", "pam.execute.recover"),
    # Shared console copy: `announce_interrupted` is the ONE implementation of spec 13's log line
    # for a run that never reached its summary. It prints; it moves nothing.
    ("webui/__init__.py", "pam.execute.reporter"),
    # Task 5's join-then-recover: a job that died mid-work is healed by spec 7.2 recovery, and
    # `recovery_line` is recovery's own sentence for the page (U12).
    ("webui/jobs.py", "pam.execute.recover"),
    # Task 4's bind: binding to a folder takes its lock, and spec 7.2 recovery runs inside it
    # before anything else (U10, U12).
    ("webui/launcher.py", "pam.execute.recover"),
    # U19 (M14 Task 6): THE PAGE'S ONE DOOR TO A RENAME. `rename_jobs.py`'s job bodies run
    # `apply_locked` / `undo_locked` in the job thread under the lock the server already holds;
    # the rename itself stays in `execute/unit.py` (`ALLOWED_MOVE_SITES` does not change). Listed
    # at 6a, imported at 6b. M15 Task 4 (`m14.md` C5, pre-authorised by the M14 close) moved
    # these two pairs from `webui/jobs.py` with the bodies and changed no other entry: still six.
    ("webui/rename_jobs.py", "pam.execute.apply"),
    # U19, the same door for undo.
    ("webui/rename_jobs.py", "pam.execute.undo"),
]

# Named as a module-level constant so that `os.rename` and `shutil.move` appear once in
# this file's data rather than being spelled inside the walker - the same reason
# `move_call_sites` documents for being an AST walk over its own prose.
_MOVE_ATTRIBUTES = {"rename", "replace", "move"}


def webui_execute_reaches(source: str, module: str, package: str = WEBUI_PACKAGE) -> list[str]:
    """Every reach from `source` into a rename primitive, sorted. Empty is the rule.

    `module` is the importing file as `ALLOWED_WEBUI_EXECUTE_IMPORTS` keys it (`webui/jobs.py`):
    since M14 Task 6a (U19) an import is allowed only for the module the pair names.

    Two families, because there are two ways to rename a file from the review UI:

    1. IMPORTING ONE OF PAM'S OWN. Any import under `pam.execute` that is not paired with
       `module` in `ALLOWED_WEBUI_EXECUTE_IMPORTS`. Caught in every spelling the other guards catch
       - plain, aliased, `from`-form, and `__import__` / `import_module` with a constant
       name - plus RELATIVE imports, which the network guard deliberately skips and this
       one deliberately does not: `from ..execute import rename` inside `pam/webui/` is
       precisely the shape somebody reaches for when a plain import trips a guard.
       `package` is the importing module's own package, needed to resolve those.

    2. NAMING THE FILESYSTEM MOVE DIRECTLY: `os.rename`, `os.replace`, `shutil.move`.
       ANY MENTION, not only a call - `handler = os.rename` is not an `ast.Call` and
       would rename files just as thoroughly one line later. Restricted to receivers
       bound to `os` or `shutil` on purpose: a rule over the bare attribute name would
       fire on `text.replace(a, b)`, which is ordinary code, and a guard that fires on
       ordinary code gets routed around rather than obeyed (test_repo.py's lesson).
    """
    tree = ast.parse(source)
    reaches = [
        dotted
        for dotted in webui_execute_imports(source, package)
        if (module, dotted) not in ALLOWED_WEBUI_EXECUTE_IMPORTS
    ]
    os_names: set[str] = set()
    shutil_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    os_names.add(alias.asname or "os")
                elif alias.name == "shutil":
                    shutil_names.add(alias.asname or "shutil")
        elif isinstance(node, ast.ImportFrom) and not node.level:
            if node.module == "os":
                for alias in node.names:
                    if alias.name in ("rename", "replace"):
                        reaches.append(f"os.{alias.name}")
            elif node.module == "shutil":
                for alias in node.names:
                    if alias.name == "move":
                        reaches.append("shutil.move")

    # A second pass, because the alias sets above are only complete once every import
    # has been seen: `os` may be imported below the line that uses it in a nested scope.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr not in _MOVE_ATTRIBUTES:
            continue
        receiver = node.value
        if not isinstance(receiver, ast.Name):
            continue
        if receiver.id in os_names and node.attr in ("rename", "replace"):
            reaches.append(f"os.{node.attr}")
        elif receiver.id in shutil_names and node.attr == "move":
            reaches.append("shutil.move")
    return sorted(reaches)


def webui_execute_imports(source: str, package: str = WEBUI_PACKAGE) -> list[str]:
    """Every module under `pam.execute` that `source` imports, ALLOWED OR NOT, sorted.

    The reading half of `webui_execute_reaches` (M14 Task 6a split it out, unchanged, so the
    suite can also ask which allowances are USED): plain, aliased, `from`-form, relative, and
    `__import__` / `import_module` with a constant name. `from pam.execute import a, b` yields
    `pam.execute.a` and `pam.execute.b` - the names beside the package are what was imported.
    """
    tree = ast.parse(source)
    found: list[str] = []

    def under(dotted: str) -> bool:
        return dotted == FORBIDDEN_WEBUI_PACKAGE or dotted.startswith(
            FORBIDDEN_WEBUI_PACKAGE + "."
        )

    def absolute(module: str, level: int) -> str:
        """`from ..execute import rename` inside `pam.webui` is `pam.execute.rename`."""
        if not level:
            return module
        parts = package.split(".")
        base = ".".join(parts[: len(parts) - level + 1])
        return f"{base}.{module}" if module else base

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names if under(alias.name)]
        elif isinstance(node, ast.ImportFrom):
            module = absolute(node.module or "", node.level)
            if module == FORBIDDEN_WEBUI_PACKAGE:
                # `from pam.execute import recover, rename` - the package itself is not
                # what was imported; the names beside it are.
                found += [f"{module}.{alias.name}" for alias in node.names]
            elif under(module):
                found.append(module)
        elif isinstance(node, ast.Call):
            func = node.func
            dynamic = (isinstance(func, ast.Name) and func.id == "__import__") or (
                isinstance(func, ast.Attribute) and func.attr == "import_module"
            )
            if dynamic and node.args and isinstance(node.args[0], ast.Constant):
                name = node.args[0].value
                if isinstance(name, str) and under(name):
                    found.append(name)
    return sorted(found)


def network_import_sites(
    source: str, banned: tuple[str, ...] = BANNED_NETWORK_MODULES
) -> list[str]:
    """Every import of a `banned` module in `source`, as the dotted name imported.

    An AST walk, not a grep, and this is the guard where that matters most: the M6 plan
    asks for a "grep-gate", and a literal grep for `urllib.request` would fire on the
    docstring above (which names it), on this comment, and on the test that proves the
    guard works - while missing `import urllib.request as u` only if the grep were for
    `urllib.request\\b`... which it would match anyway. The real miss is subtler and is
    what the planted cases below cover: `from urllib import request`, which a grep for
    `urllib.request` never sees at all, and which imports precisely the banned module.

    A prefix match, so `import requests.adapters` and `from httpx._client import
    Client` are caught: reaching a submodule imports the package. The `.` is required in
    the prefix test, or `requests` would match a hypothetical `requestsomething`.

    Relative imports (`from . import x`) are skipped. `node.module` for those is the
    name INSIDE pam, so `from .http import client` in some future `pam/http/` package
    would otherwise be reported as the stdlib `http` - a guard firing on pam's own code.

    Dynamic imports are covered too - `__import__("requests")` and
    `importlib.import_module("httpx")` are the obvious ways around a guard that only
    reads import statements, and they cost four lines to close. A computed name
    (`import_module(name)`) is not detectable here and is not pretended to be.
    """

    def is_banned(dotted: str) -> bool:
        return any(dotted == name or dotted.startswith(name + ".") for name in banned)

    sites: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if is_banned(alias.name):
                    sites.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            module = node.module or ""
            if is_banned(module):
                sites.append(module)
                continue
            # `from urllib import request` imports `urllib.request`, and nothing about
            # the statement says so until the two halves are joined.
            for alias in node.names:
                if is_banned(f"{module}.{alias.name}"):
                    sites.append(f"{module}.{alias.name}")
        elif isinstance(node, ast.Call):
            func = node.func
            dynamic = (isinstance(func, ast.Name) and func.id == "__import__") or (
                isinstance(func, ast.Attribute) and func.attr == "import_module"
            )
            if dynamic and node.args and isinstance(node.args[0], ast.Constant):
                name = node.args[0].value
                if isinstance(name, str) and is_banned(name):
                    sites.append(name)
    return sites


# --- the catalog cross-table reach guard (M6 review condition 2) --------------------

# A table name follows exactly these keywords in the SQL this package writes.
# `DELETE FROM x` needs no clause of its own - the `FROM` alternative matches it.
#
# The negative lookahead on the word SET is load-bearing, not a nicety: `capture.py`
# and `files.py` both end their UPSERT with `ON CONFLICT (...) DO UPDATE SET ...`, and
# without it the extractor reports a table named after that keyword in both. Measured -
# and it is what the containment pin below exists to catch.
#
# (The lookahead is deliberately NOT spelled out in this comment. It used to be, and a
# textual replace aimed at the pattern hit the comment first and left the pattern
# intact - a mutation that reads as applied, passes, and gets logged as a survivor.)
#
# `FROM (SELECT ...)` is not matched, because `(` is not a name character - subqueries
# contribute their own inner FROM instead, which is the right answer. A common table
# expression (`WITH c AS (...) SELECT ... FROM c`) WOULD be reported as a table named
# `c` and would fail the containment pin; there are none today, and the loud failure is
# the correct outcome - a CTE name has to be added to the exemptions deliberately.
_SQL_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE)\s+(?!SET\b)([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE
)


def sql_table_names(source: str) -> set[str]:
    """Every table named by the SQL string literals in `source`, lowercased.

    An AST walk over string constants rather than a grep over the text, and here the
    difference is not theoretical - it is the whole guard. `catalog/repo/` is the most
    heavily commented package in `pam`, its docstrings discuss the very reaches this
    guard pins, and a text scan of `journal.py` reports tables named `carries`,
    `disagreeing`, `keeps`, `one`, `the`, `two` and `would` - all from prose like
    "a `plan_items` status disagreeing with a `journal` outcome". Measured, not feared:
    the first draft of this extractor returned exactly that.

    So DOCSTRINGS ARE SKIPPED, by position in the AST (the first statement of a module,
    class or function) rather than by guessing at content. That is a structural test a
    reword cannot defeat, and it leaves the prose free to name tables out loud - which
    matters, because `test_repo.py`'s lesson is that a guard firing on a docstring gets
    the docstring reworded around it. Every string that actually reaches sqlite3 is an
    argument to `conn.execute(...)`, never a docstring, so nothing real is lost.

    Names are lowercased: SQLite table names are case-insensitive, and the schema
    spells them in lower case.
    """
    tree = ast.parse(source)

    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstrings.add(id(body[0].value))

    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            names.update(m.group(1).lower() for m in _SQL_TABLE_RE.finditer(node.value))
    return names


# Which table(s) each module under `pam/catalog/repo/` OWNS - written out here from the
# package docstring and spec 4's schema, and NEVER derived from the SQL the module
# happens to contain.
#
# That distinction is the entire guard. If "own" meant "whatever this module names",
# every reach would be its own by definition and the pin would pass vacuously forever.
# This project has been bitten by that shape twice: `repo.open_plan` in M5 (twelve test
# oracles all calling the production SQL they were checking) and `repo.ENRICHERS` in M6
# (a test iterating the production tuple, so four of five enrichers could be deleted
# from `pam status` with 1238 tests green).
REPO_OWN_TABLES = {
    # The package front door: re-exports only, no SQL at all. An empty set rather than
    # an exemption, so any SQL that ever appears here is reported as a foreign reach.
    "__init__.py": frozenset(),
    "capture.py": frozenset({"capture"}),
    "companions.py": frozenset({"companion_groups"}),
    # `enrichment_runs` is M9's resume bookkeeping (spec 4, spec 8.1's runner).
    # `inferred_facts` is spec 4's content-addressed fact table. `face_instances` was
    # here through M10 and is NOT any more: M11 Task 8 gives it to `faces.py` with
    # `persons`, and A10 replaced the `_face_coverage` query that named it with the
    # ordinary marker-fact read. Own-table sets are asserted PAIRWISE DISJOINT, so this
    # is a move and not an addition.
    "enrichment.py": frozenset({"inferred_facts", "enrichment_runs"}),
    # M8 Task 7. Two tables, one owner: `file_events` is the membership side of
    # `events` and holds nothing else, exactly as `companion_groups` pairs with
    # `files.companion_group_id`. Ownership, NOT a reach - spec 8.3's grouping input
    # query (which does need `files` and `capture`) lives in `plans.py`, which already
    # holds both as reach 1, so `ALLOWED_REPO_REACHES` stays at five pairs.
    "events.py": frozenset({"events", "file_events"}),
    # M11 Task 8. Two tables, one owner, on `events.py`'s precedent: `persons` is the
    # user-truth side of `face_instances` and `face_instances.person_id` is the schema's
    # only reference to it. NOT a sixth reach - the reasoning is in the package docstring.
    "faces.py": frozenset({"face_instances", "persons"}),
    "files.py": frozenset({"files"}),
    "journal.py": frozenset({"journal"}),
    "plan_items.py": frozenset({"plan_items"}),
    "plans.py": frozenset({"plans"}),
}

# The three modules the `__init__` docstring names as NOT keyed to a table: each is keyed
# to a CONSUMER instead, so "foreign table" has no meaning for any of them. Every query in
# `integrity.py` serves spec 7.5's `pam verify` and every one of them asks about the
# relationship BETWEEN two tables; `reports.py` does the same for `pam report`; and
# `review.py` (M7 Task 8) does it for spec 8.2's review API, where every read is
# `plan_items` JOIN `files` LEFT JOIN `capture` because one review card shows the proposed
# name and the provenance beside the media type, the on-disk extension case, the
# placeholder flag and the recorded EXIF orientation. Filing their queries by whichever
# table each names first would turn the four documented reaches into more than a dozen.
# They are still walked by the containment and coverage pins below - only the reach
# arithmetic skips them. Note that an exempt module is deliberately ABSENT from
# `REPO_OWN_TABLES`: `repo_cross_table_reaches` filters on this tuple before it looks a
# module up, so there is no KeyError to avoid, and listing one in both would make
# `test_the_repo_module_map_covers_every_module_on_disk` count it twice.
REPO_REACH_EXEMPT = ("integrity.py", "reports.py", "review.py")

# The complete inventory of deliberate cross-table reaches, as (module, foreign table).
# FIVE pairs expressing the FOUR reaches the `__init__` docstring numbers: its reach 1
# (`plans.files_for_planning`) names two foreign tables, `files` and `capture`.
#
# Sorted comparison against a LIST, following ALLOWED_MOVE_SITES: a new reach landing
# beside a blessed one has to show up as an extra element rather than being absorbed.
# The reasoning for each lives in `pam/catalog/repo/__init__.py`'s docstring; this is
# only the enforcement, and a change to one without the other is a red test.
ALLOWED_REPO_REACHES = [
    ("companions.py", "files"),  # reach 2
    ("enrichment.py", "files"),  # reach 4
    ("journal.py", "plan_items"),  # reach 3
    ("plans.py", "capture"),  # reach 1
    ("plans.py", "files"),  # reach 1
]


def repo_cross_table_reaches(sources: dict[str, str]) -> list[tuple[str, str]]:
    """Every (module, foreign table) pair in `sources`, sorted.

    Takes a name -> source mapping rather than a directory so the planted-source tests
    drive the SAME arithmetic the real guard runs, not a re-implementation of it. A
    module absent from `REPO_OWN_TABLES` raises `KeyError` on purpose: silently
    defaulting it to "owns nothing" would let a deleted map entry pass unnoticed, and a
    guard that walks a module it knows nothing about is guessing.
    """
    reaches = [
        (name, table)
        for name, source in sources.items()
        if name not in REPO_REACH_EXEMPT
        for table in sql_table_names(source)
        if table not in REPO_OWN_TABLES[name]
    ]
    return sorted(reaches)
