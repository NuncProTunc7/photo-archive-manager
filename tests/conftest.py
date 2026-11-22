import os
import sqlite3

import pytest
from census import TESTS_DIR, active_census, census_errors
from faces_stack import REQUIRED_VAR as _FACES_REQUIRED_VAR
from faces_stack import STACK_READY as _FACES_STACK_READY
from faces_stack import STACK_REASON as _FACES_STACK_REASON
from inno_stack import REQUIRED_VAR as _INNO_REQUIRED_VAR
from inno_stack import STACK_READY as _INNO_STACK_READY
from inno_stack import STACK_REASON as _INNO_STACK_REASON
from ocr_stack import REQUIRED_VAR as _OCR_REQUIRED_VAR
from ocr_stack import STACK_READY as _OCR_STACK_READY
from ocr_stack import STACK_REASON as _OCR_STACK_REASON

from pam import faults

# THE OPTIONAL STACKS, AS A TABLE (M12 Task 12, Step B9): (marker, its `*_REQUIRED` variable,
# ready?, why not). Each row is decided ONCE, at import, by its `tests/<name>_stack.py`, and the
# hook below applies each row's one answer to every item carrying that marker - so each lane
# skips ALL of a stack's tests or NONE of them (A2), and a lane that REQUIRES a stack fails at
# collection instead of skipping it (the M11 review's R3). A third optional stack is a third row.
OPTIONAL_STACKS = (
    ("faces", _FACES_REQUIRED_VAR, _FACES_STACK_READY, _FACES_STACK_REASON),
    ("ocr", _OCR_REQUIRED_VAR, _OCR_STACK_READY, _OCR_STACK_REASON),
    # M15 Task 13B: Inno Setup's compiler, for the installer's behavioural tests.
    ("inno", _INNO_REQUIRED_VAR, _INNO_STACK_READY, _INNO_STACK_REASON),
)


def pytest_collection_modifyitems(config, items):
    """Skip every optional-stack-marked test on ONE shared decision per stack (A2).

    THE POINT IS THE SKIP COUNT, not the convenience. `tests/faces_stack.py` answers "is
    the whole faces stack here?" once, at import, and this hook applies that one answer to
    every marked item - so a lane skips ALL of them or NONE of them, and the two lanes'
    counts are reconcilable against the marker instead of against whatever each test
    happened to check for itself.

    A per-test `skipif` would look identical and would not have that property.

    `get_closest_marker`, NEVER `"faces" in item.keywords`, AND THAT IS A MEASUREMENT.
    Written the second way first, this hook skipped
    `test_pam_enrich_refuses_an_enricher_no_milestone_has_built_yet[faces]` in
    `tests/test_enrich_command.py` - because `item.keywords` holds the node's NAME and its
    PARAMETRIZE IDS as well as its markers, and that test is parametrized over the
    enrichers pam does not ship yet, one of which is spelled `faces`. The suite went green
    with a refusal test silently disarmed, which is this project's recurring shape arriving
    through a brand-new instrument. A marker lookup asks the question that was meant.

    THE SUITE CENSUS RUNS FIRST (M12 Task 1, `m11.md` C4), above the optional stacks' table,
    so it runs on every lane whatever any optional stack says: `tests/census.py` is the one
    inventory, outside every guarded module, of the controls the suite must still hold. A
    module, a control or a registration gone is a COLLECTION error, before any test runs.
    """
    # The whole census in this repository; its public view in the export, which carries no
    # `tests/private/` (M15 Task 14). `census.active_census` decides, from the tree on disk.
    census, registries, floors = active_census(TESTS_DIR)
    errors = census_errors(TESTS_DIR, census, registries, floors=floors)
    if errors:
        raise pytest.UsageError(
            "THE SUITE CENSUS FAILED (tests/census.py). A guarded module or control is gone:\n  "
            + "\n  ".join(errors)
        )
    for marker, required_var, ready, reason in OPTIONAL_STACKS:
        if ready:
            continue
        # R3 (the M11 review): where the stack is REQUIRED, "unavailable" is a failure, never
        # a skip. See `faces_stack.REQUIRED_VAR` for the measurement.
        if os.environ.get(required_var) == "1":
            raise pytest.UsageError(
                f"{required_var}=1 but the {marker} stack is unavailable: {reason}. This lane"
                f" exists to run the {marker}-marked tests; skipping them would report green"
                " over nothing."
            )
        skip = pytest.mark.skip(reason=f"{marker} stack unavailable: {reason}")
        for item in items:
            if item.get_closest_marker(marker) is not None:
                item.add_marker(skip)


# Non-empty while a test holds the `set_fault` fixture. Cleared at the start of every
# test by isolated_faults, so the tripwire below cannot inherit a stale entry.
_sanctioned_fault_setters: list[str] = []

# Non-empty while a test holds `allow_split_unit`. Same shape, same reason.
_sanctioned_split_units: list[str] = []


@pytest.fixture(autouse=True)
def isolated_pam_home(tmp_path):
    """Every test gets a private PAM_HOME so nothing touches the real one.

    Deliberately NOT monkeypatch. monkeypatch.undo() reverses every patch on the
    shared per-test instance, so one test calling it lifted this fixture too and
    ran against the developer's real PAM_HOME (M5 Task 3, mutation M40's test -
    caught by luck). From Task 5 the suite renames files, so the consequence of an
    escape goes from "reads real pointers" to "renames files under the real home".
    A raw os.environ write with a try/finally restore cannot be undone by a test.
    """
    home = tmp_path / "pam-home"
    previous = os.environ.get("PAM_HOME")
    os.environ["PAM_HOME"] = str(home)
    try:
        yield home
    finally:
        if previous is None:
            os.environ.pop("PAM_HOME", None)
        else:
            os.environ["PAM_HOME"] = previous


@pytest.fixture(autouse=True)
def _pam_home_stayed_isolated(isolated_pam_home):
    """Tripwire: an escape by any OTHER route fails the test loudly.

    Requesting isolated_pam_home orders this fixture's teardown BEFORE the env
    restore above, so the assertion sees whatever PAM_HOME the test left behind.
    """
    yield
    from pam.paths import app_root

    assert app_root() == isolated_pam_home, "PAM_HOME escaped isolation during this test"


@pytest.fixture(scope="session", autouse=True)
def _session_schema_ledger(tmp_path_factory):
    """The ledger isolation BEFORE any module-scoped fixture runs.

    The per-test fixture below is function-scoped, so it is set up AFTER every module-scoped
    fixture - and eleven of those (the webui servers, `test_q_measure.py`'s archive) scan and
    migrate in their own setup. Measured at M15 Task 9's first full run: without this layer
    they appended eleven lines to the developer's REAL `%LOCALAPPDATA%\\pam-install\\
    schema-ledger`. Session-scoped autouse fixtures are set up before any module-scoped one.
    """
    from pam.catalog.ledger import LEDGER_ENV

    path = tmp_path_factory.mktemp("pam-ledger-session") / "schema-ledger"
    previous = os.environ.get(LEDGER_ENV)
    os.environ[LEDGER_ENV] = str(path)
    try:
        yield path
    finally:
        if previous is None:
            os.environ.pop(LEDGER_ENV, None)
        else:
            os.environ[LEDGER_ENV] = previous


@pytest.fixture(autouse=True)
def isolated_schema_ledger(tmp_path, _session_schema_ledger):
    """Every test gets a private compatibility ledger (E12, M15 Task 9), exactly as PAM_HOME.

    The ledger is OUTSIDE every PAM_HOME (`%LOCALAPPDATA%\\pam-install\\schema-ledger`), so
    isolating PAM_HOME does not isolate it: every catalog a test migrates or opens would append
    to the developer's real ledger, which the installer reads (the HT-M14 F2 lesson, one file
    over). A raw os.environ write, for the reason `isolated_pam_home` gives, and inherited by
    every subprocess a test starts.
    """
    from pam.catalog.ledger import LEDGER_ENV

    path = tmp_path / "pam-ledger" / "schema-ledger"
    previous = os.environ.get(LEDGER_ENV)
    os.environ[LEDGER_ENV] = str(path)
    try:
        yield path
    finally:
        if previous is None:
            os.environ.pop(LEDGER_ENV, None)
        else:
            os.environ[LEDGER_ENV] = previous


def default_schema_ledgers() -> tuple:
    """The files `ledger_path()` falls back to with no override - the developer's REAL ledger.

    Spelled from design 4 rev 35, NOT read through `ledger_path()`: `%LOCALAPPDATA%\\pam-install\\
    schema-ledger` and `~/.pam/schema-ledger`. Both, whatever this machine has: a test that
    drops LOCALAPPDATA reaches the second."""
    from pathlib import Path

    paths = [Path.home() / ".pam" / "schema-ledger"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        paths.insert(0, Path(local) / "pam-install" / "schema-ledger")
    return tuple(paths)


def schema_ledger_states(paths) -> dict:
    """Each file's (size, mtime_ns), or None when it does not exist. An append changes the size."""
    states = {}
    for path in paths:
        try:
            stat = path.stat()
        except FileNotFoundError:
            states[path] = None
        else:
            states[path] = (stat.st_size, stat.st_mtime_ns)
    return states


def schema_ledger_escapes(before: dict, after: dict) -> list:
    """The real ledgers whose state changed between two `schema_ledger_states` readings."""
    return [str(path) for path in before if before[path] != after.get(path)]


@pytest.fixture(scope="session", autouse=True)
def real_schema_ledgers():
    """The developer's real ledger files, as this session found them, checked at its end.

    THE TRIPWIRE BELOW ALONE WAS BLIND TO THREE OF FOUR ESCAPES (M15 Task 9 review, F1): a test
    that `monkeypatch.delenv`s or empties PAM_SCHEMA_LEDGER is restored before it reads the env,
    and a subprocess started with the variable dropped never touches this process's env. All
    three wrote to the real ledger with the suite green. So the guard watches the FILES."""
    paths = default_schema_ledgers()
    before = schema_ledger_states(paths)
    yield paths
    escaped = schema_ledger_escapes(before, schema_ledger_states(paths))
    assert not escaped, (
        f"the suite wrote the developer's REAL schema ledger: {escaped} (or a real pam ran"
        " during the run). Remove what the run added."
    )


@pytest.fixture(autouse=True)
def _schema_ledger_stayed_isolated(isolated_schema_ledger, real_schema_ledgers):
    """Tripwire: the ledger path at teardown is still this test's private one, AND the real
    ledger files are as they were before this test (the env check alone cannot see an escape
    through monkeypatch or a subprocess - F1)."""
    before = schema_ledger_states(real_schema_ledgers)
    yield
    from pam.catalog.ledger import ledger_path

    escaped = schema_ledger_escapes(before, schema_ledger_states(real_schema_ledgers))
    assert not escaped, f"this test wrote the developer's REAL schema ledger: {escaped}"
    assert ledger_path() == isolated_schema_ledger, "the schema ledger escaped isolation"


@pytest.fixture(scope="session", autouse=True)
def _session_run_registry(tmp_path_factory):
    """E11's run registry isolated BEFORE any module-scoped fixture runs (the ledger's lesson,
    above: a module-scoped fixture that starts a pam would otherwise register in the real one)."""
    from pam.run_registry import REGISTRY_ENV

    path = tmp_path_factory.mktemp("pam-run-registry-session")
    previous = os.environ.get(REGISTRY_ENV)
    os.environ[REGISTRY_ENV] = str(path)
    try:
        yield path
    finally:
        if previous is None:
            os.environ.pop(REGISTRY_ENV, None)
        else:
            os.environ[REGISTRY_ENV] = previous


@pytest.fixture(autouse=True)
def isolated_run_registry(tmp_path, _session_run_registry):
    """Every test gets a private E11 run registry (M15 Task 13A), exactly as the ledger.

    The registry is OUTSIDE every PAM_HOME (`%LOCALAPPDATA%\\pam-install`), and every `main()`
    a test drives registers there: un-isolated, the suite would create the developer's real
    `pam-install\\run` folder, and a test holding a fake `setup.lock` would make the developer's
    real pam refuse. A raw os.environ write, inherited by every subprocess a test starts."""
    from pam.run_registry import REGISTRY_ENV

    path = tmp_path / "pam-run-registry"
    previous = os.environ.get(REGISTRY_ENV)
    os.environ[REGISTRY_ENV] = str(path)
    try:
        yield path
    finally:
        if previous is None:
            os.environ.pop(REGISTRY_ENV, None)
        else:
            os.environ[REGISTRY_ENV] = previous


def default_run_registries() -> tuple:
    """The run folders `registry_root()` falls back to with no override - the REAL ones.

    Spelled from design 3 rev 35 (E11), NOT read through `registry_root()`:
    `%LOCALAPPDATA%\\pam-install\\run` and `~/.pam/run`. Both, as for the ledger."""
    from pathlib import Path

    paths = [Path.home() / ".pam" / "run"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        paths.insert(0, Path(local) / "pam-install" / "run")
    return tuple(paths)


def run_registry_states(paths) -> dict:
    """Each folder's (mtime_ns, sorted names), or None when it does not exist. A registration
    creates the folder, or creates and deletes a file in it, and either moves its mtime."""
    states = {}
    for path in paths:
        try:
            stat = path.stat()
            names = tuple(sorted(os.listdir(path)))
        except FileNotFoundError:
            states[path] = None
        else:
            states[path] = (stat.st_mtime_ns, names)
    return states


def run_registry_escapes(before: dict, after: dict) -> list:
    """The real run folders whose state changed between two `run_registry_states` readings."""
    return [str(path) for path in before if before[path] != after.get(path)]


@pytest.fixture(scope="session", autouse=True)
def real_run_registries():
    """The developer's real run folders, as this session found them, checked at its end. It
    watches the FOLDERS, not the env (the Task 9 review's F1: a monkeypatched or dropped variable
    is invisible to an env check)."""
    paths = default_run_registries()
    before = run_registry_states(paths)
    yield paths
    escaped = run_registry_escapes(before, run_registry_states(paths))
    assert not escaped, (
        f"the suite registered a pam in the developer's REAL run registry: {escaped} (or a real"
        " pam ran during the run)."
    )


@pytest.fixture(autouse=True)
def _run_registry_stayed_isolated(isolated_run_registry, real_run_registries):
    """Tripwire: the registry root at teardown is still this test's private one, AND the real run
    folders are as they were before this test."""
    before = run_registry_states(real_run_registries)
    yield
    from pam.run_registry import registry_root

    escaped = run_registry_escapes(before, run_registry_states(real_run_registries))
    assert not escaped, (
        f"this test registered a pam in the developer's REAL run registry: {escaped}"
    )
    assert registry_root() == isolated_run_registry, "the run registry escaped isolation"


class _RealOllama:
    """The production callables that can dial the default Ollama port, unblocked.

    Handed out by the `real_ollama` fixture, so that "this test drives the real HTTP
    client" is visible in the test's signature rather than being something a reader has
    to infer - the same idiom `set_fault` and `allow_split_unit` use.
    """

    def __init__(self, tags, pull, client):
        self.tags = tags
        self.pull = pull
        self.client = client


@pytest.fixture(autouse=True)
def _no_test_talks_to_the_real_ollama():
    """Tripwire: nothing in this suite may reach the Ollama on this machine.

    THE REAL OLLAMA IS INSTALLED HERE, and that is exactly what makes this necessary
    (M10 plan, clarification 7). A test that reached it would pass on the maintainer's
    machine and mean nothing anywhere else; `pam models pull` reaching it would start a
    multi-gigabyte download from a test run. And the failure is silent in the worst
    direction: on this machine the honest answer to every Ollama check is "everything is
    fine", so a test that forgot to script a fake would go green here and red in CI.

    M10 TASK 8 ADDED A THIRD ROUTE AND IT IS NOT A FUNCTION. `enrich/caption.py` builds
    its own `net.LoopbackHttpClient`, so a caption test that forgot to point at the stub
    would take design 11's DEFAULT `ollama_port` (11434) and reach the server on this
    machine - the same silent-in-the-worst-direction failure, one layer down. The class is
    therefore wrapped too, and the wrapper refuses exactly the combination that can get
    off this suite's leash: THE DEFAULT OLLAMA PORT together with THE REAL RESOLVER.
    `tests/test_net.py` legitimately builds clients on 11434 to prove the DNS-rebinding
    defence, and every one of them passes a fake resolver, so that pair is the precise
    line rather than the port alone.

    So `pam doctor`'s probe, `pam models pull` and the captioner's client are replaced,
    for every test, with something that fails loudly. A test drives a FAKE by monkeypatching over this (which
    monkeypatch restores to the refusal, not to the real function), or drives the REAL
    client against `tests/ollama_stub.py` by requesting the `real_ollama` fixture.

    Raw setattr with try/finally rather than monkeypatch, for the reason written on
    `isolated_pam_home`: a test calling `monkeypatch.undo()` would otherwise lift this
    fixture too, and the whole point is that it cannot be lifted by accident.
    """
    import pam.cli as cli_mod
    import pam.doctor as doctor
    import pam.net as net_mod

    # BOTH NAMES, because `pam/cli.py` imports the function rather than the module, so a
    # patch on `pam.doctor.pull_model` alone leaves `pam models pull` calling the real
    # one - which is the single most expensive thing in this suite to get wrong.
    real = _RealOllama(doctor._ollama_tags, doctor.pull_model, net_mod.LoopbackHttpClient)

    def refuse(*args, **kwargs):
        raise AssertionError(
            "a test reached the real Ollama on this machine. Script a fake over"
            " pam.doctor._ollama_tags / pull_model, or drive the real client against"
            " tests/ollama_stub.py through the `real_ollama` fixture."
        )

    class RefusingDefaultOllamaPort(real.client):
        def __init__(self, host="127.0.0.1", port=80, **kwargs):
            asks_the_machine = kwargs.get("resolver", net_mod.system_resolver) is (
                net_mod.system_resolver
            )
            if port == doctor.DEFAULT_OLLAMA_PORT and asks_the_machine:
                refuse()
            super().__init__(host, port, **kwargs)

    doctor._ollama_tags = refuse
    doctor.pull_model = refuse
    cli_mod.pull_model = refuse
    net_mod.LoopbackHttpClient = RefusingDefaultOllamaPort
    try:
        yield real
    finally:
        doctor._ollama_tags = real.tags
        doctor.pull_model = real.pull
        cli_mod.pull_model = real.pull
        net_mod.LoopbackHttpClient = real.client


@pytest.fixture()
def real_ollama(_no_test_talks_to_the_real_ollama):
    """The unblocked `_ollama_tags` / `pull_model` / client, for tests driving the stub.

    Returned rather than re-installed: the module attributes stay refusing, so a test
    that meant to drive the stub on an explicit port cannot accidentally reach the
    default one through some other code path in the same test.
    """
    return _no_test_talks_to_the_real_ollama


@pytest.fixture(autouse=True)
def isolated_faults():
    """Both halves of the fault-injection switch are process-global; reset both.

    `PAM_FAULT` is an environment variable and `faults._counts` is a module-level
    dict, so without this a test that arms a checkpoint changes how the NEXT test's
    production code behaves - and a 'crash:X:3' left armed makes an unrelated test
    fail at hit 3 with a SystemExit that names a checkpoint it never mentioned. M5
    Task 11's fault matrix is 36+ scenarios; this has to be structural, not a habit.

    Raw os.environ with a try/finally, deliberately NOT monkeypatch, for the reason
    written on isolated_pam_home: a test calling monkeypatch.undo() would otherwise
    lift this fixture too.
    """
    previous = os.environ.get("PAM_FAULT")
    os.environ.pop("PAM_FAULT", None)
    faults.reset()
    # ASSERTED rather than cleared, so the teardown below is load-bearing. A sanction
    # that outlives its test disarms `_faults_stayed_isolated` for every test after
    # it, silently - and M5 Task 11's fault matrix is 24+ scenarios all leaning on it.
    # Deleting the teardown's clear() used to leave the whole suite green.
    assert not _sanctioned_fault_setters, (
        f"{_sanctioned_fault_setters} survived the previous test, so its PAM_FAULT"
        " tripwire was disarmed; isolated_faults' teardown is what clears this"
    )
    try:
        yield
    finally:
        os.environ.pop("PAM_FAULT", None)
        faults.reset()
        _sanctioned_fault_setters.clear()
        if previous is not None:
            os.environ["PAM_FAULT"] = previous


class _FaultControl:
    """The test-side fault switch: arm it, then step to the next invocation.

    Every fault scenario has the same four beats - arm, run the command (it raises),
    simulate the next invocation, run recovery and assert - and the third beat is the
    one that is easy to get wrong, so it lives here rather than in each test body.
    """

    @staticmethod
    def _validate(spec: str) -> None:
        """Refuse a spec that could never fire. Shared by `arm` and `fault_env`.

        Production `maybe_fault` stays LENIENT on purpose (a typo in a real user's
        environment must not break their run), so a scenario armed with
        `crash:after_renmae:1` runs a fully SUCCESSFUL apply and passes all of its
        assertions trivially. Validating here makes the test route strict without
        changing what ships.

        BOTH entry points validate, and they used to disagree: `fault_env` built a
        child's environment with no checks at all, so a typo'd subprocess spec ran a
        fully successful command in a fresh interpreter and the only thing standing
        between that and a green cell was the caller remembering to assert on the
        aftermath. M5 Task 11's matrix is 31 subprocess cells; that has to be
        structural.
        """
        mode, checkpoint, n = spec.split(":")
        assert mode in faults.MODES, f"unknown mode {mode!r}"
        assert checkpoint in faults.CHECKPOINTS, f"unknown checkpoint {checkpoint!r}"
        assert n.isdigit() and n != "0", f"bad hit count {n!r}"

    def arm(self, spec: str, *, malformed: bool = False) -> None:
        """Set PAM_FAULT, refusing a spec that could never fire.

        Tests that deliberately arm a malformed spec - the ones proving production's
        leniency - pass malformed=True.
        """
        if not malformed:
            self._validate(spec)
        _sanctioned_fault_setters.append(spec)
        os.environ["PAM_FAULT"] = spec

    def __call__(self, spec: str, *, malformed: bool = False) -> None:
        """`set_fault(spec)` is `set_fault.arm(spec)`; the older spelling still works."""
        self.arm(spec, malformed=malformed)

    def next_invocation(self) -> None:
        """The crash is over. Put the process back where a fresh one would start.

        BOTH halves are needed. Popping PAM_FAULT alone leaves `faults._counts`
        carrying the hits the crashed run spent, so `crash:after_rename:2` on a
        three-member unit can have its remaining hit consumed by the REVERT path
        instead of the apply path - and forgetting the reset produces no error at
        all, just a scenario that crashed somewhere other than where it says.
        """
        os.environ.pop("PAM_FAULT", None)
        faults.reset()

    def hits(self, name: str) -> int:
        """How many times `name` was reached. Zero means the checkpoint never fired."""
        return faults._counts.get(name, 0)

    def fault_env(self, spec: str, *, malformed: bool = False) -> dict[str, str]:
        """A child process's environment with PAM_FAULT armed, leaving this one clean.

        `crash:` raises SystemExit, which unwinds the stack rather than killing the
        process (see faults.maybe_fault), so a genuine process-level crash - the thing
        spec 9.3's "recovery runs on the next invocation" describes - only happens in
        a subprocess. This builds that subprocess's env without arming the parent.

        Validated exactly as `arm` is, and for the same reason: a spec that could
        never fire is the failure mode that reports green. See `_validate`.
        """
        if not malformed:
            self._validate(spec)
        return {**os.environ, "PAM_FAULT": spec}


@pytest.fixture()
def set_fault(isolated_faults):
    """The only sanctioned way for a test to arm PAM_FAULT. Enforced by the tripwire.

    Returns a _FaultControl: call it (or `.arm(...)`) with a '<mode>:<checkpoint>:<n>'
    spec, then `.next_invocation()` between the crash and recovery, and `.hits(name)`
    to prove the checkpoint actually fired.

    Requesting this fixture is what tells the tripwire below that the test meant to
    arm a fault. What the tripwire catches is a raw `os.environ["PAM_FAULT"] = ...`
    LEFT BEHIND at teardown; a raw set that the test cleans up again passes. That is
    the right line: the leak is what would arm the next test, and only the leak is
    detectable from outside the test body.
    """
    return _FaultControl()


@pytest.fixture(autouse=True)
def _faults_stayed_isolated(isolated_faults):
    """Tripwire: PAM_FAULT set by any route other than set_fault fails the test.

    Requesting isolated_faults orders this teardown BEFORE that fixture's cleanup, so
    the assertion sees whatever the test actually left behind.
    """
    yield
    leaked = os.environ.get("PAM_FAULT")
    assert leaked is None or _sanctioned_fault_setters, (
        f"PAM_FAULT={leaked!r} was set without the set_fault fixture; use it so fault"
        " arming stays visible in the test signature"
    )


# spec 7.0 J6 - unit atomicity. For one `unit_key`, either every member is `done` or
# every member is `failed`/`reverted`. Mixing `done` with either terminal state is a
# companion group left split across two stems (vision S3), which is the outcome M5
# exists to prevent.
#
# `intent` is deliberately NOT part of the check: J6 calls a mixed unit a TRANSIENT
# state that spec 7.2's recovery resolves, and a crash between members legitimately
# leaves one member `done` and the next in `intent`.
_SPLIT_UNIT_QUERY = (
    "SELECT p.unit_key, j.session_id, j.state FROM journal j"
    " JOIN plan_items p ON p.id = j.plan_item_id"
)


def catalogs_under(home):
    """Every pam catalog below one PAM_HOME - the J6 tripwire's REACH, named.

    A named function rather than an inline glob because the tripwire cannot prove its
    own reach: pointing the pattern at something that matches nothing leaves the whole
    suite green while the milestone's headline invariant quietly asserts nothing at
    all, and four mutations in Task 7's table are credited to a tripwire that would
    then be inert. `test_the_j6_tripwire_reaches_the_catalog_an_apply_wrote` pins it
    from outside, which is the only place it can be pinned from.
    """
    return sorted(home.rglob("catalog.db"))


def _split_units(db_path):
    """(session_id, unit_key) pairs holding both a `done` and a resolved-back member."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not {"journal", "plan_items"} <= tables:
            return []
        states: dict[tuple[str, str], set[str]] = {}
        for row in conn.execute(_SPLIT_UNIT_QUERY):
            states.setdefault((row["session_id"], row["unit_key"]), set()).add(row["state"])
    finally:
        conn.close()
    return [
        unit
        for unit, seen in states.items()
        if "done" in seen and seen & {"failed", "reverted"}
    ]


@pytest.fixture(autouse=True)
def _no_unit_was_left_split(isolated_pam_home):
    """Tripwire: spec 7.0 J6 asserted over every catalog every test touched.

    Suite-wide rather than per-scenario on purpose (M5 Task 7). A per-test assertion
    proves J6 for the cases someone remembered to write; this one proves it for the
    cases nobody thought of, including the ones a later milestone adds. Its arrival is
    what forced the deletion of Task 6's
    `test_a_member_failing_after_an_earlier_one_moved_is_reported_not_hidden`, which
    pinned the interim split as acceptable - a structural guarantee that the interim
    behaviour could not quietly become permanent.

    `allow_split_unit` is the sanctioned opt-out, for a test that MANUFACTURES the
    external interference spec 7.0 describes (something outside pam taking a path back
    mid-run). pam cannot produce a split unit; the world can, and pam's answer is to
    report it. A test proving that has to be able to reach it.

    THE CATEGORY IS NARROW AND IS THE WHOLE POINT. A holder must be a test where pam
    has looked at the split and cannot honestly heal it - something else is on the old
    name, the file was edited since the intent snapshot, or a `files` row owns the old
    path. "The process died mid-repair" is NOT in the category: M5 Task 8's spec 7.2
    phase 2 heals that, and the test that used to hold a sanction for it now runs
    recovery and asserts the unit comes back whole. An escape hatch that quietly
    accumulates users stops being an exception and becomes the rule.
    """
    # ASSERTED at setup and CLEARED at teardown, never the other way round: a sanction
    # that outlives the test that declared it stops J6 being asserted for every test
    # after it, and nothing goes red. Deleting the teardown's clear() used to leave
    # the whole suite green - which is the same shape of hole as `_sanctioned_fault_
    # setters` above, and both are fixed the same way.
    assert not _sanctioned_split_units, (
        f"{_sanctioned_split_units} survived the previous test, so spec 7.0 J6 was"
        " no longer being asserted; this fixture's teardown is what clears it"
    )
    yield
    sanctioned = bool(_sanctioned_split_units)
    _sanctioned_split_units.clear()
    if sanctioned:
        return
    for db_path in catalogs_under(isolated_pam_home):
        split = _split_units(db_path)
        assert split == [], (
            f"spec 7.0 J6: {split} ended with both a `done` and a `failed`/`reverted`"
            f" member in {db_path} - a companion group split across two stems"
        )


@pytest.fixture()
def allow_split_unit(_no_unit_was_left_split):
    """Declare that this test deliberately reaches a split unit. See the tripwire.

    Requesting `_no_unit_was_left_split` orders this fixture's setup AFTER that
    fixture's clear(), so the declaration survives to its teardown - the same
    ordering trick `set_fault` uses with `isolated_faults`.
    """
    _sanctioned_split_units.append("declared")
