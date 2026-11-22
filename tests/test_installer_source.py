r"""The installer's source, read and never run (M15 Task 13B, ruling 7): the guards that NEVER skip.

`tests/test_installer_behaviour.py` compiles and runs the installer where ISCC is, and skips where
it is not. These hold everywhere, on every lane: they read `tools/installer/pam.iss` and
`test-hooks.iss` as text and pin what the maintainer's rulings require of them, each expectation spelled
here from the brief and 10C, never read back from the production symbol it checks.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from pam import install_method, run_registry
from pam.update import command

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "tools" / "installer"
ISS = (INSTALLER / "pam.iss").read_text(encoding="utf-8")
HOOKS = (INSTALLER / "test-hooks.iss").read_text(encoding="utf-8")


def section(name: str, text: str = ISS) -> list[str]:
    """The non-blank, non-comment lines of one `[Section]`."""
    match = re.search(
        rf"^\[{re.escape(name)}\]\s*$(.*?)(?=^\[[A-Za-z]+\]\s*$|\Z)", text, re.M | re.S
    )
    assert match is not None, f"pam.iss has no [{name}] section"
    lines = [line.strip() for line in match.group(1).splitlines()]
    return [line for line in lines if line and not line.startswith(";")]


def setting(name: str) -> str:
    values = [
        line.split("=", 1)[1].strip()
        for line in section("Setup")
        if line.split("=", 1)[0].strip() == name
    ]
    assert len(values) == 1, f"[Setup] {name} is set {len(values)} times"
    return values[0]


def preprocessor_outside_test(text: str) -> str:
    """The text a PRODUCTION compile sees: every `#ifdef PamTestRoot ... #else/#endif` branch
    dropped, the `#else` branch kept. Nesting of other `#if` blocks is tracked, not interpreted."""
    kept: list[str] = []
    stack: list[tuple[bool, bool]] = []  # (is a PamTestRoot block, now in its test branch)
    for line in text.splitlines():
        word = line.strip().split(" ", 1)[0]
        if word in ("#ifdef", "#ifndef", "#if"):
            test = line.strip() == "#ifdef PamTestRoot"
            stack.append((test, test))
            continue
        if word == "#else":
            test, _ = stack[-1]
            stack[-1] = (test, False)
            continue
        if word == "#endif":
            stack.pop()
            continue
        if not any(branch for _, branch in stack):
            kept.append(line)
    assert not stack, "an #if block in pam.iss is not closed"
    return "\n".join(kept)


PRODUCTION = preprocessor_outside_test(ISS)


def test_the_production_view_is_not_vacuous():
    """The splitter above is the guards' instrument: it must keep the [Code] and drop the hooks."""
    assert "function NoPamRunning" in PRODUCTION
    assert "function PrepareToInstall" in PRODUCTION
    assert "test-hooks.iss" not in PRODUCTION
    assert "TestEvent" not in PRODUCTION
    assert "{localappdata}\\pam-install" in PRODUCTION
    code = [
        line for line in PRODUCTION.splitlines() if not line.lstrip().startswith((";", "//"))
    ]
    assert not [line for line in code if "PamTest" in line]


# --- ruling 6/7: what uninstall may delete ---------------------------------------------------


def test_every_UninstallDelete_entry_is_one_exact_file_inside_app():
    entries = section("UninstallDelete")
    assert entries == [
        'Type: files; Name: "{app}\\pam-install.json"'
    ]  # spelled: E13's record only
    for entry in entries:
        name = re.search(r'Name: "([^"]+)"', entry)
        assert name is not None
        assert name.group(1).startswith("{app}\\")
        assert not re.search(
            r"[*?]|\.\.|\{(localappdata|userappdata|userdocs|%)", name.group(1)
        )
        assert entry.startswith("Type: files;")


def test_the_code_never_deletes_a_folder_and_deletes_files_only_in_the_installer_cache():
    code = PRODUCTION[PRODUCTION.index("[Code]") :]
    assert "DelTree" not in code and "RemoveDir" not in code
    deletes = re.findall(r"DeleteFile\(([^)]*)\)", code)
    assert deletes == ["Folder + '\\' + Found.Name"]  # the cache prune, under installers\
    prune = code[
        code.index("procedure CacheInstaller") : code.index("procedure CurStepChanged")
    ]
    assert "Folder := InstallRoot() + '\\installers';" in prune
    assert "FindFirst(Folder + '\\pam-setup-*.exe', Found)" in prune


def test_install_replaces_only_the_interpreter_and_exiftool_trees():
    assert section("InstallDelete") == [
        'Type: filesandordirs; Name: "{app}\\python"',
        'Type: filesandordirs; Name: "{app}\\exiftool"',
    ]


# --- ruling 4/7: privileges, Restart Manager, and the flags pam update passes ----------------------


def test_the_installer_is_per_user():
    assert setting("PrivilegesRequired") == "lowest"
    assert setting("DefaultDirName") == "{localappdata}\\Programs\\pam"
    assert "PrivilegesRequiredOverridesAllowed" not in ISS


def test_restart_manager_is_off_both_ways_and_no_mutex_stands_in_for_E11():
    assert setting("CloseApplications") == "no"
    assert setting("RestartApplications") == "no"
    keys = {line.split("=", 1)[0].strip() for line in section("Setup")}
    assert not keys & {"AppMutex", "SetupMutex", "CloseApplicationsFilter"}


def test_pam_updates_flags_are_the_ones_the_iss_agrees_with():
    """Task 12 passes these; the .iss's settings mean the same thing, so the flags are a second
    lock on the same door, never the only one."""
    assert command.INSTALLER_FLAGS == ("/NOCLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS")
    assert command.UPDATER_PID_FLAG.format(pid=4242) == "/UPDATERPID=4242"
    assert "ExpandConstant('{param:UPDATERPID|}')" in PRODUCTION


def test_the_updater_waits_30_s_and_any_other_pam_gets_the_2_s_grace():
    code = PRODUCTION
    assert re.search(r"^\s*GRACE_MS = 2000;", code, re.M)
    assert re.search(r"^\s*UPDATER_MS = 30000;", code, re.M)
    assert re.search(r"^\s*RETRY_MS = 2000;", code, re.M)
    assert re.search(r"^\s*SETUP_RETRY_MS = 250;", code, re.M)
    assert run_registry.RETRY_S * 1000 == 2000  # the same DENIED retry on both sides
    body = code[code.index("function NoPamRunning") : code.index("function Exclusive")]
    assert "HeldPids(Updater)" in body  # the updater is excluded from the 2 s grace
    assert "(Others <> '') and (GetTickCount() - Started > GRACE_MS)" in body
    assert "GetTickCount() - Started > UPDATER_MS" in body


def test_setup_takes_setup_lock_before_it_looks_for_a_running_pam():
    """10C's order (Dekker): setup.lock FIRST, then the run files; and the same in uninstall."""
    exclusive = PRODUCTION[PRODUCTION.index("function Exclusive") :]
    assert "Result := TakeSetupLock() and NoPamRunning();" in exclusive.split("end;")[0]
    take = PRODUCTION[
        PRODUCTION.index("function TakeSetupLock") : PRODUCTION.index("function NoPamRunning")
    ]
    assert "'\\setup.lock'" in take and "DELETE_ON_CLOSE" in take
    assert re.search(r"DELETE_ACCESS,\s*0, 0, OPEN_ALWAYS", take)  # share 0
    for entry in ("function InitializeSetup", "function InitializeUninstall"):
        body = PRODUCTION[PRODUCTION.index(entry) :].split("end;")[0]
        assert "Result := Exclusive();" in body
    prepare = PRODUCTION[PRODUCTION.index("function PrepareToInstall") :].split("end;")[0]
    assert prepare.index("LedgerRefusal()") < prepare.index("NoPamRunning()")


def test_the_run_file_grammar_matches_pams():
    held = PRODUCTION[
        PRODUCTION.index("function HeldPids") : PRODUCTION.index("function TakeSetupLock")
    ]
    assert "IsDigits(Pid)" in held and "= '.lock')" in held
    assert run_registry.RUN_DIR == "run" and run_registry.SETUP_LOCK == "setup.lock"
    assert "InstallRoot() + '\\run'" in held


def test_the_installer_reads_no_environment_variable():
    """Ruling 5: the production artefact reads nothing from the environment."""
    for text in (ISS, HOOKS):
        assert "GetEnv" not in text
        assert "{%" not in text
    assert re.search(r'#define InstallRoot "\{localappdata\}\\pam-install"', ISS)


# --- ruling 5: test builds and only test builds ------------------------------------------------


def test_the_test_hooks_are_reachable_only_from_a_test_build():
    assert '#include "test-hooks.iss"' in ISS
    assert '#include "test-hooks.iss"' not in PRODUCTION
    for name in ("TestPrepare", "TestUninstallStep", "TestFaultHere", "TestEvent"):
        assert name not in PRODUCTION


def test_a_test_build_cannot_redirect_the_production_root_or_appid():
    test_branch = ISS[ISS.index("#ifdef PamTestRoot") : ISS.index("#else")]
    assert "#define InstallRoot PamTestRoot" in test_branch
    assert "#define AppIdValue PamTestAppId" in test_branch
    assert re.search(r'#define AppIdValue "\{\{5C2B7E1A-9D43-4E8F-A6B1-3F0D2C7E9A54\}"', ISS)


# --- E13 and the component (OD-9) ---------------------------------------------------------------


def test_the_component_is_labelled_from_pam_and_kept_on_upgrade():
    assert section("Components") == [
        'Name: "base"; Description: "pam"; Types: base custom; Flags: fixed',
        'Name: "reading"; Description: "{#ComponentLabel}"; Types: custom',
    ]
    assert install_method.COMPONENT_LABEL == "Face and text reading"  # the brief's words
    assert setting("UsePreviousSetupType") == "yes"
    assert setting("UsePreviousTasks") == "yes"
    # Inno 6.7.4 has NO `UsePreviousComponents` directive (ISCC refuses it: the Stage 1 review's
    # M4606). What keeps the component across an upgrade is `UsePreviousSetupType=yes`: it restores
    # the previous setup type AND its component selection ("Inno Setup: Selected Components" in the
    # uninstall key), and setting it to no drops the component (M4606b, measured).
    assert "UsePreviousComponents" not in ISS


def test_the_record_written_is_the_one_pam_reads():
    write = PRODUCTION[
        PRODUCTION.index("procedure WriteRecord") : PRODUCTION.index("function CacheEntry")
    ]
    assert f"ExpandConstant('{{app}}\\{install_method.RECORD_NAME}')" in write
    assert '\'{"method": "installer", "version": \'' in write
    assert "WizardIsComponentSelected('reading')" in write


def test_the_payload_reaches_app_and_the_component_its_site_packages():
    files = [line for line in section("Files", PRODUCTION)]
    assert files == [
        'Source: "{#PayloadDir}\\*"; DestDir: "{app}"; Components: base; Flags: ignoreversion recursesubdirs createallsubdirs',
        'Source: "{#ComponentDir}\\*"; DestDir: "{app}\\python\\Lib\\site-packages"; Components: reading; Flags: ignoreversion recursesubdirs createallsubdirs',
    ]
    assert ".onnx" not in ISS.lower()


def test_the_shortcuts_start_pam_check_for_updates_and_repair():
    assert section("Icons") == [
        'Name: "{#Shortcuts}\\pam"; Filename: "{app}\\pam.cmd"; Parameters: "start"; WorkingDir: "{userdocs}"',
        'Name: "{#Shortcuts}\\Check for pam updates"; Filename: "{app}\\pam.cmd"; Parameters: "update"; WorkingDir: "{userdocs}"',
        'Name: "{#Shortcuts}\\Repair pam"; Filename: "{#InstallRoot}\\installers\\pam-setup-{#PamVersion}.exe"',
    ]
    assert '#define Shortcuts "{group}"' in PRODUCTION


def test_the_path_task_is_off_unless_chosen():
    assert section("Tasks") == [
        'Name: "path"; Description: "Add pam to my PATH (for a terminal)"; Flags: unchecked'
    ]
    assert setting("ChangesEnvironment") == "yes"


# --- PAM_INNO_REQUIRED: the CI job cannot pass having run nothing --------------------------------


def _inno_collection(required: bool) -> subprocess.CompletedProcess[str]:
    env = {
        k: v for k, v in os.environ.items() if k not in ("PAM_INNO_REQUIRED", "PAM_INNO_ISCC")
    }
    env["PAM_INNO_ISCC"] = str(REPO / "no-such-folder" / "ISCC.exe")
    if required:
        env["PAM_INNO_REQUIRED"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", "inno", "--collect-only",
         "tests/test_installer_behaviour.py"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=120,
    )  # fmt: skip


def test_a_REQUIRED_inno_stack_that_is_unavailable_fails_instead_of_skipping():
    result = _inno_collection(required=True)
    assert result.returncode == 4, result.stdout[-2000:]
    assert (
        "PAM_INNO_REQUIRED=1 but the inno stack is unavailable" in result.stdout + result.stderr
    )


def test_the_control_WITHOUT_the_inno_requirement_the_same_collection_succeeds():
    result = _inno_collection(required=False)
    assert result.returncode == 0, result.stdout[-2000:]
    assert "test_a_silent_install" in result.stdout


@pytest.mark.parametrize("marker", ["inno"])
def test_the_inno_marker_is_registered(marker):
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(rf'^\s*"{marker}: ', text, re.M)


# --- Task 13B-F: the [Code] read STRUCTURALLY, so a string that is present but never used fails ---
#
# The Stage 1 review's M4601, M4608, M4619 and M4620 were killed only by the behaviour module, which
# needs ISCC. These guards always run. An import is not a wiring: each one checks the ORDER of the
# operands, the ASSIGNMENTS to a value and its USE, never only that some text is in the file.


def routines(code: str = PRODUCTION) -> dict[str, str]:
    """Each `function`/`procedure` of the [Code] section by name: its text up to the next one."""
    code = code[code.index("[Code]") :]
    heads = list(re.finditer(r"^(?:function|procedure)\s+(\w+)", code, re.M))
    assert len(heads) > 20, "the [Code] reader found too few routines to be reading pam.iss"
    return {
        head.group(1): code[head.start() : nxt.start() if nxt else len(code)]
        for head, nxt in zip(heads, [*heads[1:], None], strict=True)
    }


def statements(body: str) -> list[str]:
    """A routine's statements after its first `begin`, `//` comments dropped, one per `;`."""
    text = "\n".join(line.split("//", 1)[0] for line in body.splitlines())
    begin = re.search(r"\bbegin\b", text)
    assert begin is not None
    text = text[begin.end() :]
    return [" ".join(part.split()) for part in text.split(";") if part.strip()]


def and_operands(expression: str) -> list[str]:
    """The top-level operands of an `and` chain, in order, parentheses respected."""
    operands, depth, start = [], 0, 0
    for i, char in enumerate(expression):
        depth += {"(": 1, ")": -1}.get(char, 0)
        if depth == 0 and expression[i : i + 5] == " and ":
            operands.append(expression[start:i].strip())
            start = i + 5
    operands.append(expression[start:].strip())
    return operands


def assignments(body: str, name: str) -> list[str]:
    """Every statement that assigns `name` in a routine, in order (a guarding `if` kept)."""
    pattern = rf"(?:^|\bthen |\bbegin ){re.escape(name)} :="
    return [s for s in statements(body) if re.search(pattern, s)]


ROUTINES = routines()


def test_the_structural_reader_is_not_vacuous():
    """The instrument first: it finds the routines, splits a two-operand `and`, and sees order."""
    names = {"Exclusive", "NoPamRunning", "HeldPids", "PrepareToInstall", "CurStepChanged"}
    assert names <= set(ROUTINES)
    assert and_operands("(a and b) and not c(d, e)") == ["(a and b)", "not c(d, e)"]
    assert assignments("var X: S; begin X := 1; if y then X := 2; Z := X; end;", "X") == [
        "X := 1",
        "if y then X := 2",
    ]


def test_M4601_the_exclusion_takes_setup_lock_FIRST_and_both_entries_use_it():
    (result,) = [s for s in statements(ROUTINES["Exclusive"]) if s.startswith("Result :=")]
    operands = and_operands(result.removeprefix("Result := "))
    assert operands == ["TakeSetupLock()", "NoPamRunning()"]
    for entry in ("InitializeSetup", "InitializeUninstall"):
        assert statements(ROUTINES[entry])[0] == "Result := Exclusive()"
    # nothing else takes the lock, so it is never taken after the run files were looked at
    callers = [n for n, b in ROUTINES.items() if n != "TakeSetupLock" and "TakeSetupLock(" in b]
    assert callers == ["Exclusive"]


def test_M4608_the_UPDATERPID_value_is_read_kept_and_used_twice():
    body = ROUTINES["NoPamRunning"]
    assert (
        assignments(body, "Updater")
        == [
            "Updater := ExpandConstant('{param:UPDATERPID|}')",
            "if not IsDigits(Updater) then Updater := ''",  # only a value that is not a pid is dropped
        ]
    )
    uses = [s for s in statements(body) if "Updater" in s and not s.startswith("Updater :=")]
    assert any("HeldPids(Updater)" in s for s in uses)  # excluded from the 2 s grace
    assert any("'\\run\\' + Updater + '.lock'" in s for s in uses)  # and probed on its own


def test_M4619_the_updater_is_waited_for_only_while_its_run_file_is_held():
    body = ROUTINES["NoPamRunning"]
    (held,) = assignments(body, "UpdaterHeld")
    assert and_operands(held.removeprefix("UpdaterHeld := ")) == [
        "(Updater <> '')",
        "(Probe(InstallRoot() + '\\run\\' + Updater + '.lock') <> ABSENT)",
    ]
    go_on = "if (Others = '') and not UpdaterHeld then"
    assert len([s for s in statements(body) if s.startswith(go_on)]) == 1  # the value decides


def test_M4620_held_pids_never_counts_the_excluded_pid_and_the_updater_is_the_excluded_one():
    body = ROUTINES["HeldPids"]
    assert body.startswith("function HeldPids(Excluded: String): String;")
    (check,) = [s for s in statements(body) if s.startswith("if IsDigits(Pid)")]
    condition = check.removeprefix("if ").split(" then", 1)[0]
    assert "(Pid <> Excluded)" in and_operands(condition)
    assert re.findall(r"HeldPids\(([^)]*)\)", ROUTINES["NoPamRunning"]) == ["Updater"]


# --- Task 13B-F: F-P1, F-P3, F-P2, Q8 and the path budget, at the source --------------------------


def test_F_P1_the_previous_PATH_entry_is_read_before_the_install_step_and_used_after():
    reads = [n for n, body in ROUTINES.items() if "GetPreviousData(" in body]
    assert reads == ["PrepareToInstall"]  # by ssPostInstall, Inno has re-created the key
    prepare = statements(ROUTINES["PrepareToInstall"])
    assert "PreviousPathAdded := GetPreviousData('PathAdded', '')" in prepare
    assert "RemoveFromPath(PreviousPathAdded)" in ROUTINES["CurStepChanged"]
    # Stage 2 review (M4811, M4812): the removal is guarded by BOTH operands. Without the first, a
    # first install with the task calls RemoveFromPath('') and drops every empty segment of the
    # user's PATH (killed by the behaviour module only on a PATH that has one); without the
    # second, Repair or an upgrade moves pam's own entry to the end of the PATH.
    guarded = [
        and_operands(s.removeprefix("if ").split(" then", 1)[0])
        for s in statements(ROUTINES["CurStepChanged"])
        if s.startswith("if ") and s.endswith("then RemoveFromPath(PreviousPathAdded)")
    ]
    assert guarded == [
        ["(PreviousPathAdded <> '')", "(CompareText(PreviousPathAdded, PathAdded) <> 0)"]
    ]


def test_F_P3_an_app_folder_already_on_the_PATH_is_not_recorded_as_the_installers():
    found = [
        and_operands(s.removeprefix("if ").split(" then", 1)[0])
        for s in statements(ROUTINES["CurStepChanged"])
        if s.startswith("if ") and s.endswith("then PathAdded := ''")
    ]
    assert found == [
        ["OnUserPath(PathAdded)", "(CompareText(PreviousPathAdded, PathAdded) <> 0)"]
    ]


def test_F_P2_a_repair_keeps_the_previous_installer_installers_json_lists():
    cache = " ".join(ROUTINES["CacheInstaller"].split())
    assert "else if PreviousVersion = '{#PamVersion}' then" in cache
    assert "Previous := ListedPrevious(Folder, Current)" in cache
    assert "'\\installers.json'" in ROUTINES["ListedPrevious"]


REPAIR_SHORTCUT = "Repair pam"  # spelled from the brief, and the [Icons] entry Q8 measured


def test_every_message_after_the_copy_started_names_the_Repair_pam_shortcut():
    """Q8: a failed or cancelled install leaves pam unusable until "Repair pam" runs."""
    hint = re.search(r"^#define RepairHint '(.*)'$", ISS, re.M)
    assert hint is not None
    assert hint.group(1) == (
        'If pam does not start now, open the Start menu and run "' + REPAIR_SHORTCUT + '".'
    )
    messages = dict(line.split("=", 1) for line in section("Messages"))
    assert messages == {
        "SetupAborted": "Setup did not finish. {#RepairHint}",
        "ExitSetupMessage": "Setup is not finished. {#RepairHint}%n%nExit Setup?",
    }
    assert any(f'\\{REPAIR_SHORTCUT}"; Filename:' in line for line in section("Icons"))


def test_the_folder_is_checked_on_the_folder_page_and_again_before_the_copy():
    assert (
        "Length(Dir) + 1 + {#LongestPayloadPath} >= MAX_PATH_CHARS" in ROUTINES["PathTooLong"]
    )
    assert re.search(r"^\s*MAX_PATH_CHARS = 260;", PRODUCTION, re.M)
    page = statements(ROUTINES["NextButtonClick"])
    wanted = "if CurPageID = wpSelectDir then begin Refused := PathTooLong(WizardDirValue())"
    assert wanted in page
    prepare = statements(ROUTINES["PrepareToInstall"])
    first = next(i for i, s in enumerate(prepare) if "PathTooLong(" in s)
    assert prepare[first] == "Result := PathTooLong(ExpandConstant('{app}'))"
    assert first < next(i for i, s in enumerate(prepare) if "LedgerRefusal()" in s)
    assert "#ifndef LongestPayloadPath" in ISS
