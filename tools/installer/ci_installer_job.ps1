# The body of the CI installer job (M15 Task 13B, ruling 7). Task 14 calls it from ci.yml on a
# Windows runner with Inno Setup 6.7.4. The job installs nothing of its own: every installer it
# runs is a TEST build with its own temporary root, {app} and AppId (tools/installer/harness.py).
#
#   powershell -NoProfile -File tools/installer/ci_installer_job.ps1 [-Iscc <path to ISCC.exe>]
#
# PAM_INNO_REQUIRED=1 turns a missing ISCC into a collection error, so the job cannot go green
# having run nothing (the M11 review's R3). -rs prints every skip; there must be none.
param([string]$Iscc = "")

$ErrorActionPreference = "Stop"
$env:PAM_INNO_REQUIRED = "1"
if ($Iscc -ne "") { $env:PAM_INNO_ISCC = $Iscc }
python -m pytest -q -rs -p no:cacheprovider -m inno --junitxml=inno.xml `
    tests/test_installer_behaviour.py
$code = $LASTEXITCODE
python -m pytest -q -rs -p no:cacheprovider tests/test_installer_source.py tests/test_installer_build.py
if ($LASTEXITCODE -ne 0 -and $code -eq 0) { $code = $LASTEXITCODE }
exit $code
