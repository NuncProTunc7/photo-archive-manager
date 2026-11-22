"""Q8's two checks that are not about the installer itself (M15 Task 13B): `pam start` serving the
review page and every asset it references, and a file held the way an antivirus scan holds it."""

import ctypes
import re
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

import _proctree


def review_page(
    pam_cmd: Path, lib: Path, env: dict[str, str], work: Path, url_file: Path
) -> dict[str, object]:
    """`pam start LIB` with `BROWSER` set to a script that writes the address to `url_file`: the
    page and each `src`/`href` it names are fetched over loopback; then pam start is stopped."""
    proc = subprocess.Popen(
        [str(pam_cmd), "start", str(lib)], env=env, cwd=work,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        end = time.monotonic() + 90
        while (
            not (url_file.exists() and url_file.read_text().strip()) and time.monotonic() < end
        ):
            if proc.poll() is not None:
                return {"ok": False, "exit": proc.returncode, "detail": "pam start exited"}
            time.sleep(0.2)
        if not url_file.exists():
            return {"ok": False, "exit": None, "detail": "no browser call within 90 s"}
        url = url_file.read_text().strip()
        if not url.startswith("http://127.0.0.1:"):
            return {"ok": False, "exit": None, "detail": "not a loopback address"}
        page = urllib.request.urlopen(url, timeout=30).read().decode("utf-8")
        assets = sorted(set(re.findall(r'(?:src|href)="([^"#]+)"', page)))
        assets = [a for a in assets if not a.startswith(("http:", "https:", "data:"))]
        sizes = {
            a: len(urllib.request.urlopen(urllib.parse.urljoin(url, a), timeout=30).read())
            for a in assets
        }
        ok = bool(sizes) and all(sizes.values())
        return {
            "ok": ok,
            "exit": None,
            "detail": f"page {len(page)} B, {len(sizes)} assets all served: {ok}",
        }
    except OSError as error:
        return {"ok": False, "exit": None, "detail": f"{type(error).__name__}: {error}"}
    finally:
        _proctree.kill_tree(proc.pid)
        proc.wait(30)


def open_exclusive(path: Path) -> int:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = ctypes.c_void_p
    handle = k32.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise OSError(f"could not hold {path}")
    return int(handle)


def close(handle: int) -> None:
    ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(handle))
