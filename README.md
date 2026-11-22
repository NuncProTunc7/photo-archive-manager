# pam - Photo Archive Manager

pam gives the photos and videos in a folder meaningful, readable names - `2019-07-04_Lake_001.jpg`
instead of `IMG_4321.JPG` - and does it safely:

- **It never changes a photo.** Not a pixel, not a byte of its metadata. pam only renames files,
  in the folder they are already in. It never moves, copies, overwrites or deletes one.
- **You approve every name.** pam proposes; nothing is renamed until you say so.
- **Everything can be undone.** Every rename is written down before it happens, so pam can put
  every name back, even after a crash or a power cut.
- **Your photos stay on your computer.** pam has no account, sends no photo anywhere, and checks
  for updates only when you ask it to.
- **It is useful with no AI at all.** Dates come from the files themselves. Face and text reading
  are optional, run on your own computer, and use models you choose to download yourself.

**Keep a backup.** pam is careful, but your archive is irreplaceable: keep a complete, separate
copy of it before you rename anything with any tool.

## Install (Windows 10 and 11)

1. Open the [Releases page](../../releases) and download `pam-setup-X.Y.Z.exe` from the newest
   release.
2. Optionally, check the download: in PowerShell, `Get-FileHash pam-setup-X.Y.Z.exe` must print the
   SHA-256 written in the release notes.
3. Run it. pam installs for your Windows user only and needs no administrator rights. The
   installer is not code-signed yet, so Windows may show "Windows protected your PC": choose
   **More info**, then **Run anyway**.

You do not need Python or anything else: the installer brings everything pam needs.

The full guide, with every step, is [docs/user-guide.md](docs/user-guide.md).

## Use

The Start menu gains three shortcuts:

- **pam** opens pam's page in your web browser. Choose a folder, let pam read it, review the names
  it proposes, approve the ones you like, and rename. **Undo** puts them back.
- **Check for pam updates** asks GitHub whether a newer pam exists, tells you what it would change,
  and installs it only after you type `update`.
- **Repair pam** runs the installer you installed from again, to put back a damaged or deleted pam
  file.

From a terminal, `pam start` opens the same page, and `pam --help` lists every command.

## Uninstall

Use Windows' **Settings > Apps > Installed apps > pam > Uninstall**. pam's program files are removed.
**Your photos, pam's catalogs of your folders and your settings are kept**, so a reinstall picks up
where you left off.

## Going back to an older pam

pam 1.0 and later refuse a catalog that a newer pam wrote, and the installer and `pam update` refuse
to put back an older pam once a newer one has opened your catalogs.

A development build of pam older than 1.0 does not check: it opens a catalog a newer pam wrote
without refusing it, and can misread it. Do not run such a build on a folder a pam of 1.0 or later
has opened.

## Models are never bundled

pam's installer contains no machine-learning model, and pam never downloads one itself.
(`pam models pull` asks Ollama, which you installed, to fetch a caption model.) Face and text
reading need a model pack you download and place yourself; the user guide says where.

## Licence

pam is MIT-licensed ([LICENSE](LICENSE)). The installer also contains third-party software under its
own licences: [THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt).

## For developers

pam is a Python 3.12+ package. `pip install -e ".[dev]"`, then `pytest`, `ruff check .`,
`ruff format --check .` and `mypy pam`. The suite needs exiftool on `PATH`, and node for the page's
script checks. Without the installer, pip installs pam from the wheel each release carries (put the
version in place of `X.Y.Z`, both times):

    pip install --upgrade "https://github.com/NuncProTunc7/photo-archive-manager/releases/download/vX.Y.Z/photo_archive_manager-X.Y.Z-py3-none-any.whl"
