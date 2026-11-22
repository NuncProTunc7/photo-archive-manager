# pam user guide

pam renames the photos and videos in a folder to readable, dated names. It never changes what is
inside a file, never moves, copies or deletes one, renames only what you approve, and can put every
name back. This guide covers installing, using, updating, repairing and removing pam on Windows.

**Before anything else: keep a backup.** A complete, separate copy of your photo folder, made before
you rename anything, is the one safety net no program can replace.

## 1. Install

1. Go to pam's **Releases** page on GitHub and download `pam-setup-X.Y.Z.exe` from the newest
   release (the one at the top that is not marked "Pre-release").
2. Optional but worthwhile: check that the download is the file that was published. Open
   PowerShell in your Downloads folder and run

       Get-FileHash pam-setup-X.Y.Z.exe

   The long `Hash` it prints must match the "Installer SHA-256" in the release notes, letter for
   letter (upper or lower case does not matter).
3. Double-click the installer. pam installs for **your Windows user only**, into
   `%LOCALAPPDATA%\Programs\pam`, and needs **no administrator rights**.
   - The installer is not code-signed yet. If Windows shows **"Windows protected your PC"**,
     choose **More info**, then **Run anyway**.
   - **Face and text reading** is an optional component, unticked. Tick it only if you want pam
     to read faces or the dates printed on scanned photos (see section 6).
   - **Add pam to my PATH** is optional too: tick it if you want to type `pam` in a terminal.

Nothing else is needed: the installer brings its own copy of Python and of exiftool (the tool pam
uses to read dates and camera details), so it does not touch any Python you may already have.

## 2. The Start-menu shortcuts

The installer adds three shortcuts to the Start menu:

| Shortcut | What it does |
|---|---|
| **pam** | Opens pam's page in your web browser. This is where you use pam. |
| **Check for pam updates** | Asks whether a newer pam has been released, and installs it only if you agree (section 4). |
| **Repair pam** | Runs the installer of the pam you have again, to put back a damaged or missing file (section 5). |

## 3. Using pam

Open the **pam** shortcut (or type `pam start` in a terminal). pam's page opens in your browser. It
is served by your own computer and is reachable only from it.

1. **Choose a folder** of photos. pam reads it: names, dates, cameras and companion files (a RAW and
   its JPEG, a Live Photo's picture and video, an editor's sidecar). Reading changes nothing.
2. **Review the names pam proposes.** Each one says how sure pam is. Approve the names you like,
   skip the ones you do not, or type a better one. A photo's companions are always renamed together,
   so a pair is never split.
3. **Rename.** pam renames only what you approved, writing down each rename before it makes it.
4. **Undo** is always there: from the page's History, or `pam undo` in a terminal, pam puts every
   name of a session back.

pam keeps what it learns about each folder - its catalog, settings (`pam.toml`), logs and reports -
in `%LOCALAPPDATA%\pam`, never inside your photo folder.

In a terminal, `pam --help` lists every command; `pam status` says what pam knows and what to do
next; `pam doctor` checks that pam is set up correctly and says how to fix anything that is not.

## 4. Updating pam

pam never checks for updates on its own. To check, open **Check for pam updates** (or type
`pam update`):

- pam asks GitHub for the newest release and tells you what it would install and what it would
  change.
- It installs only after you **type `update`** to confirm. Anything else cancels.
- It refuses while another pam is running (close pam's page and any terminal running pam first),
  and it refuses a release older than the newest pam that has opened your catalogs, because an
  older pam cannot read them.

`pam update --channel rc` follows release candidates (test versions) instead of stable releases.

## 5. Repairing pam

If pam will not start, or a file in its folder was deleted or damaged, open **Repair pam**. It runs
the installer of the version you have again and puts every program file back. Your folders,
catalogs and settings are not touched. If the Repair shortcut itself is gone, download the same
version's installer from the Releases page and run it.

## 6. Optional: face and text reading

pam can suggest people's names (from faces you name once) and dates printed on scanned photos. Both
run entirely on your computer, and both need:

1. the **Face and text reading** component, ticked in the installer (run the installer again to
   add it); and
2. a **model pack** - the trained models themselves. **pam's installer contains no model, and pam
   never downloads one.** You download the pack yourself, put it in a folder of your choice, and
   name that folder in your folder's `pam.toml` (`[enrich.faces] model_dir`, or
   `[enrich.ocr] model_dir` for text reading). `pam doctor` checks the pack and says exactly which
   files it expects.

Photo captions are optional too: they use a local [Ollama](https://ollama.com) model that you
install and run yourself; `pam models` helps set it up.

## 7. Uninstalling

Open Windows **Settings > Apps > Installed apps**, find **pam**, and choose **Uninstall**.

pam's program files are removed. **Kept, always:**

- **your photos** - pam never deletes one, installed or not;
- **pam's catalogs, settings, logs and reports** in `%LOCALAPPDATA%\pam`;
- pam's small install records in `%LOCALAPPDATA%\pam-install` (the saved installers that Repair
  uses, and the record that stops an older pam from opening a newer catalog).

Reinstalling pam later picks up where you left off. To remove everything, delete those two folders
yourself after uninstalling.

## 8. Without the installer (pip)

If you already use Python 3.12 or newer, pip can install pam from the wheel each release carries
instead:

    pip install --upgrade "https://github.com/NuncProTunc7/photo-archive-manager/releases/download/vX.Y.Z/photo_archive_manager-X.Y.Z-py3-none-any.whl"

Replace `X.Y.Z` with the release's version, both times. You then need exiftool on your `PATH` yourself
(`pam doctor` says so if it is missing), and `pam update` points you back to this command rather
than running an installer.
