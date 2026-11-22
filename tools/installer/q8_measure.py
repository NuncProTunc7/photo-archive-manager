r"""Q8 (M15 Task 13B, ruling 8): measure Inno Setup's rollback on the REAL payload, and after every
failure and every Repair run the whole chain from the INSTALLED pam, outside the checkout.

    python tools/installer/q8_measure.py --stage <build.py --keep-stage folder> --work <empty dir>
        [--faces-pack <folder holding face_detection_yunet_2023mar.onnx>] [--only <case,...>]

It compiles TEST builds of the real staged payload (`harness.TestInstall`: a temporary install
root, `{app}` and AppId, so nothing of the maintainer's is touched), installs 1.0.0 with the optional
component, then for each case tries to upgrade to 1.0.1 and fails it:

    cancel-<page>   the wizard's Cancel on that page (7 components, 9 tasks, 10 ready)
    cancel-copy     Cancel half-way through the copy
    locked          a payload file held open with share 0 (an antivirus scan, a running tool)
    fault           the copy fails on a file it cannot write: the test build's /PAMFAULT=copy,
                    which stands in for a full disk (Inno fails both the same way: Abort, rollback)
    quarantine      not an upgrade: one installed file deleted, as a quarantine would

and records the exit code, what is left in `{app}`, the chain on what is left, then Repair pam
(the cached installer) and the chain again. The chain: `pam doctor`, `pam scan`, `pam plan`,
`pam apply N --all` + typed `apply`, `pam undo` (paths and content checked), `pam enrich
geocode`, `pam start` serving the review page and every asset it references, a HEIC encode and a
fresh-process decode, and, with the component, onnxruntime's import and a session.

Every `PAM_*` path is a temporary one; the result is printed and written to <work>\q8.json.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import harness  # noqa: E402
import q8_checks  # noqa: E402

REPO = HERE.parents[1]
TINY = REPO / "tests" / "tiny_library"
HEIC_ENCODE = (
    "import sys, pillow_heif; from PIL import Image; pillow_heif.register_heif_opener();"
    " Image.new('RGB', (64, 48), (200, 100, 50)).save(sys.argv[1], format='HEIF')"
)
HEIC_DECODE = (
    "import sys, pillow_heif; from PIL import Image; pillow_heif.register_heif_opener();"
    " im = Image.open(sys.argv[1]); im.load(); print(im.format, im.size, im.mode)"
)
ONNX = (
    "import sys, onnxruntime as o; print(o.__version__, o.get_available_providers());"
    " s = o.InferenceSession(sys.argv[1], providers=['CPUExecutionProvider']) if len(sys.argv) > 1 else None;"
    " print(s and [i.name for i in s.get_inputs()], s and s.get_providers())"
)


def long(path: Path) -> Path:
    r"""The tiny library holds a name longer than MAX_PATH: copy and read it through `\\?\`."""
    return Path("\\\\?\\" + str(path))


def digests(folder: Path) -> dict[str, str]:
    root = long(folder)
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


class Chain:
    def __init__(self, inst: harness.TestInstall, work: Path, faces_pack: Path | None) -> None:
        self.inst, self.work, self.faces_pack = inst, work, faces_pack
        self.count = 0

    def env(self, n: int) -> dict[str, str]:
        browser = self.work / f"browser{n}.bat"
        browser.write_text(f'@echo %~1> "{self.work / f"url{n}.txt"}"\r\n', encoding="ascii")
        return dict(
            {
                k: v
                for k, v in os.environ.items()
                if not k.startswith(("PAM_", "PYTHON", "VIRTUAL_ENV"))
            },
            PAM_HOME=str(self.work / f"home{n}"),
            PAM_SCHEMA_LEDGER=str(self.inst.root / "schema-ledger"),
            PAM_RUN_REGISTRY=str(self.inst.root),
            BROWSER=str(browser),
        )

    def pam(
        self, env: dict[str, str], *args: str, stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.inst.app / "pam.cmd"), *args], input=stdin, env=env, cwd=self.work,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        )  # fmt: skip

    def py(
        self, env: dict[str, str], code: str, *args: str
    ) -> subprocess.CompletedProcess[str]:
        python = self.inst.app / "python" / "python.exe"
        return subprocess.run(
            [str(python), "-I", "-B", "-c", code, *args], env=env, cwd=self.work,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
        )  # fmt: skip

    def run(self, reading: bool) -> dict[str, object]:
        self.count += 1
        n = self.count
        env = self.env(n)
        lib = self.work / f"lib{n}"
        shutil.copytree(long(TINY), long(lib))
        before = digests(lib)
        out: dict[str, object] = {}

        def step(
            name: str, done: subprocess.CompletedProcess[str], ok: bool, detail: str = ""
        ) -> bool:
            last = (done.stdout + done.stderr).strip().splitlines()
            out[name] = {
                "ok": ok,
                "exit": done.returncode,
                "detail": detail or (last[-1] if last else ""),
            }
            return ok

        if not (self.inst.app / "pam.cmd").exists():
            out["launcher"] = {"ok": False, "exit": None, "detail": "pam.cmd is missing"}
            return out
        done = self.pam(env, "doctor")
        step("doctor", done, done.returncode == 0)
        done = self.pam(env, "scan", str(lib))
        if not step("scan", done, done.returncode == 0):
            return out
        done = self.pam(env, "plan")
        plan = re.search(r"pam apply (\d+)", done.stdout)
        if not step("plan", done, done.returncode == 0 and plan is not None):
            return out
        assert plan is not None
        done = self.pam(env, "apply", plan.group(1), "--all", stdin="apply\n")
        after = digests(lib)
        moved = len(set(after) - set(before))
        same = sorted(after.values()) == sorted(before.values())
        session = re.search(r"pam undo (\S+)", done.stdout)
        if not step(
            "apply",
            done,
            done.returncode == 0 and same and moved > 0,
            f"{moved} renamed, content same: {same}",
        ):
            return out
        assert session is not None
        done = self.pam(env, "undo", session.group(1))
        step(
            "undo",
            done,
            done.returncode == 0 and digests(lib) == before,
            f"restored exactly: {digests(lib) == before}",
        )
        done = self.pam(env, "enrich", "geocode")
        step("geocode", done, done.returncode == 0)
        out["start"] = self.start(env, lib, n)
        heic = self.work / f"probe{n}.heic"
        done = self.py(env, HEIC_ENCODE, str(heic))
        if step("heic encode", done, done.returncode == 0):
            done = self.py(env, HEIC_DECODE, str(heic))
            step("heic decode", done, done.returncode == 0 and "(64, 48) RGB" in done.stdout)
        if reading:
            model = (
                self.faces_pack / "face_detection_yunet_2023mar.onnx"
                if self.faces_pack
                else None
            )
            done = self.py(env, ONNX, *([str(model)] if model and model.is_file() else []))
            step(
                "onnxruntime",
                done,
                done.returncode == 0,
                done.stdout.strip().replace("\n", " | "),
            )
        else:
            done = self.py(env, "import onnxruntime")
            step(
                "onnxruntime absent",
                done,
                done.returncode != 0 and "ModuleNotFoundError" in done.stderr,
            )
        return out

    def start(self, env: dict[str, str], lib: Path, n: int) -> dict[str, object]:
        url_file = self.work / f"url{n}.txt"
        return q8_checks.review_page(self.inst.app / "pam.cmd", lib, env, self.work, url_file)


def state(inst: harness.TestInstall) -> dict[str, object]:
    record = inst.record()
    return {
        "files": len(inst.files()),
        "python tree": (inst.app / "python" / "python.exe").exists(),
        "pam.cmd": (inst.app / "pam.cmd").exists(),
        "record": record and record.get("version"),
        "DisplayVersion": inst.value("DisplayVersion"),
        "cache": sorted(os.listdir(inst.root / "installers"))
        if (inst.root / "installers").exists()
        else [],
    }


def chain_ok(result: dict[str, object]) -> bool:
    return (
        all(isinstance(v, dict) and v.get("ok") for v in result.values()) and "undo" in result
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--faces-pack")
    parser.add_argument("--iscc", default=str(harness.build.ISCC_DEFAULT))
    parser.add_argument("--only", default="")
    args = parser.parse_args(argv)
    stage, work = Path(args.stage).resolve(), Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=False)
    pack = Path(args.faces_pack) if args.faces_pack else None
    cases = [
        "cancel-7",
        "cancel-9",
        "cancel-10",
        "cancel-copy",
        "locked",
        "fault",
        "quarantine",
    ]
    if args.only:
        cases = [c for c in cases if c in args.only.split(",")]
    inst = harness.TestInstall(work / "w", Path(args.iscc))
    chain = Chain(inst, work, pack)
    results: dict[str, object] = {}
    before = harness.real_environment()
    try:
        v1 = inst.build(stage / "payload", stage / "component", "1.0.0")
        v2 = inst.build(stage / "payload", stage / "component", "1.0.1")
        repair = inst.root / "installers" / "pam-setup-1.0.0.exe"
        started = time.monotonic()
        code = inst.run(v1, "/COMPONENTS=reading", timeout=600)
        results["install 1.0.0"] = {
            "exit": code,
            "s": round(time.monotonic() - started, 1),
            **state(inst),
        }
        results["chain after install"] = chain.run(reading=True)
        for case in cases:
            row: dict[str, object] = {}
            started = time.monotonic()
            if case.startswith("cancel-") and case != "cancel-copy":
                code = inst.run(v2, f"/PAMCANCELAT={case[7:]}", quiet=False, timeout=600)
            elif case == "cancel-copy":
                code = inst.run(v2, "/PAMFAULT=cancel", quiet=False, timeout=600)
            elif case == "locked":
                held = (
                    inst.app
                    / "python"
                    / "Lib"
                    / "site-packages"
                    / "pillow_heif"
                    / "__init__.py"
                )
                handle = q8_checks.open_exclusive(held)
                try:
                    code = inst.run(v2, *harness_flags(), timeout=600)
                finally:
                    q8_checks.close(handle)
            elif case == "fault":
                code = inst.run(v2, "/PAMFAULT=copy", timeout=600)
                blocker = inst.app / "fault-probe.txt"
                if blocker.is_dir():
                    shutil.rmtree(blocker)
            else:
                code = None
                (inst.app / "python" / "Lib" / "site-packages" / "PIL" / "Image.py").unlink()
            row["exit"] = code
            row["s"] = round(time.monotonic() - started, 1)
            after = row["after"] = state(inst)
            row["events"] = [e for e in inst.events() if e.startswith("cancel")][-1:]
            result = row["chain"] = chain.run(reading=True)
            ok = row["chain ok"] = chain_ok(result)
            if not ok or after["record"] != "1.0.0":
                started = time.monotonic()
                row["repair exit"] = inst.run(repair, timeout=600)
                row["repair s"] = round(time.monotonic() - started, 1)
                row["after repair"] = state(inst)
                repaired = row["chain after repair"] = chain.run(reading=True)
                row["chain ok after repair"] = chain_ok(repaired)
            results[case] = row
            print(case, json.dumps(row, default=str)[:600], flush=True)
        started = time.monotonic()
        results["upgrade 1.0.1"] = {
            "exit": inst.run(v2, timeout=600),
            "s": round(time.monotonic() - started, 1),
            **state(inst),
        }
        results["chain after upgrade"] = chain.run(reading=True)
        started = time.monotonic()
        results["uninstall"] = {
            "exit": inst.uninstall(timeout=600),
            "s": round(time.monotonic() - started, 1),
            "app gone": inst.gone(),
        }
    finally:
        results["cleanup: key gone"] = inst.cleanup()
        results["real environment unchanged"] = harness.real_environment() == before
        (work / "q8.json").write_text(
            json.dumps(results, indent=2, default=str), encoding="utf-8"
        )
    print(json.dumps(results, indent=2, default=str))
    if not results["real environment unchanged"]:
        print(
            f"x the real environment changed: {before} -> {harness.real_environment()}",
            file=sys.stderr,
        )
        return 1
    return 0


def harness_flags() -> tuple[str, ...]:
    from pam.update.command import INSTALLER_FLAGS

    return INSTALLER_FLAGS


if __name__ == "__main__":
    sys.exit(main())
