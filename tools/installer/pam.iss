; pam's per-user Windows installer (M15 Task 13B; OD-1, OD-3, OD-9, E11, E12, E13).
;
; COMPILED ONLY BY tools/installer/build.py, which passes every value below with ISCC /D:
;   PamVersion        pam.__version__
;   HighestMigration  the newest migration the payload ships (pam.catalog.db.shipped_migrations)
;   ComponentLabel    pam.install_method.COMPONENT_LABEL - imported by the build, never retyped
;   NeedsNewer        pam.catalog.ledger.NEEDS_NEWER - the E12 sentence, spelled once in pam
;   PayloadDir        the base payload: pam.cmd, python\, exiftool\, LICENSE
;   ComponentDir      the optional component's site-packages (onnxruntime and its three)
;   LongestPayloadPath  the longest file path below {app} that the payload or the component
;                     installs, in characters (Task 13B-F: setup refuses an {app} that would
;                     push a file's full path to 260)
;   OutputDir         where the installer is written
;
; A TEST BUILD (tests only, ruling 5) also passes PamTestRoot (a temporary install root) and
; PamTestAppId (a fresh AppId per run). A production build passes neither, so the production
; artefact reads nothing from the environment: the install root is {localappdata}\pam-install,
; as pam's own run registry and ledger are without their test overrides.
;
; E11 (spike 10C, section 1 and 4): setup takes setup.lock FIRST, then enumerates the run files;
; pam takes its run file first, then probes setup.lock (pam/run_registry.py). AppMutex and
; SetupMutex are NOT used: a mutex cannot say whether ANOTHER process holds it. Restart Manager
; is off both ways; an in-use file is a refusal or a rollback, never a termination.

#ifndef PamVersion
  #error PamVersion is not defined: compile with tools/installer/build.py
#endif
#ifndef HighestMigration
  #error HighestMigration is not defined: compile with tools/installer/build.py
#endif
#ifndef ComponentLabel
  #error ComponentLabel is not defined: compile with tools/installer/build.py
#endif
#ifndef LongestPayloadPath
  #error LongestPayloadPath is not defined: compile with tools/installer/build.py
#endif

; Q8 (Task 13B-F): once the copy has started, a failed or cancelled install leaves pam unusable
; until "Repair pam" runs (Inno 6.7.4's rollback does not restore the files it replaced). Every
; message a user can see after that point names the shortcut. The owner reads it at the close.
#define RepairHint 'If pam does not start now, open the Start menu and run "Repair pam".'

#ifdef PamTestRoot
  #define InstallRoot PamTestRoot
  #define AppIdValue PamTestAppId
  ; a test build's shortcuts stay inside its temporary root, never in the Start menu (ruling 6)
  #define Shortcuts PamTestRoot + "\menu"
#else
  #define InstallRoot "{localappdata}\pam-install"
  #define AppIdValue "{{5C2B7E1A-9D43-4E8F-A6B1-3F0D2C7E9A54}"
  #define Shortcuts "{group}"
#endif

[Setup]
AppId={#AppIdValue}
AppName=pam
AppVersion={#PamVersion}
AppVerName=pam {#PamVersion}
AppPublisher=Photo Archive Manager contributors
DefaultDirName={localappdata}\Programs\pam
DefaultGroupName=pam
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
CloseApplications=no
RestartApplications=no
ChangesEnvironment=yes
UsePreviousSetupType=yes
UsePreviousTasks=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=pam-setup-{#PamVersion}
#ifdef PamTestRoot
Compression=none
#else
Compression=lzma2/max
SolidCompression=yes
#endif
WizardStyle=modern
UninstallDisplayName=pam {#PamVersion}

[Messages]
SetupAborted=Setup did not finish. {#RepairHint}
ExitSetupMessage=Setup is not finished. {#RepairHint}%n%nExit Setup?

[Types]
Name: "base"; Description: "pam"
Name: "custom"; Description: "Choose"; Flags: iscustom

[Components]
Name: "base"; Description: "pam"; Types: base custom; Flags: fixed
Name: "reading"; Description: "{#ComponentLabel}"; Types: custom

[Tasks]
Name: "path"; Description: "Add pam to my PATH (for a terminal)"; Flags: unchecked

[InstallDelete]
; The interpreter tree and exiftool are replaced WHOLE, so no file of a package the new payload
; no longer carries (an upgraded onnxruntime, a deselected component) is left beside it.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\exiftool"

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Components: base; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ComponentDir}\*"; DestDir: "{app}\python\Lib\site-packages"; Components: reading; Flags: ignoreversion recursesubdirs createallsubdirs
#ifdef PamTestRoot
; TEST BUILDS ONLY: Q8's fault flag fails the copy here, after the whole payload is replaced.
Source: "{#PayloadDir}\LICENSE"; DestDir: "{app}"; DestName: "fault-probe.txt"; Flags: ignoreversion; Check: TestFaultHere
#endif

[Icons]
Name: "{#Shortcuts}\pam"; Filename: "{app}\pam.cmd"; Parameters: "start"; WorkingDir: "{userdocs}"
Name: "{#Shortcuts}\Check for pam updates"; Filename: "{app}\pam.cmd"; Parameters: "update"; WorkingDir: "{userdocs}"
Name: "{#Shortcuts}\Repair pam"; Filename: "{#InstallRoot}\installers\pam-setup-{#PamVersion}.exe"

[UninstallDelete]
; ONLY files the installer itself creates inside {app}, listed exactly. Never a folder, never a
; wildcard, never anything outside {app}: catalogs, PAM_HOME, the ledger and the installer cache
; are the user's, and outlive pam.
Type: files; Name: "{app}\pam-install.json"

[Code]
const
  GENERIC_READ = $80000000;
  GENERIC_WRITE = $40000000;
  DELETE_ACCESS = $00010000;
  SHARE_RWD = 7;
  OPEN_EXISTING = 3;
  OPEN_ALWAYS = 4;
  DELETE_ON_CLOSE = $04000000;
  ERROR_FILE_NOT_FOUND = 2;
  ERROR_PATH_NOT_FOUND = 3;
  ERROR_ACCESS_DENIED = 5;
  ERROR_SHARING_VIOLATION = 32;
  RETRY_MS = 2000;      // 10C A2: a transient DENIED is retried this long, then refused
  SETUP_RETRY_MS = 250; // 10C question 7 (owner): a second installer is refused, not queued
  GRACE_MS = 2000;      // 10C A3: a pam that started in the race and is itself refusing
  UPDATER_MS = 30000;   // 10C: how long the /UPDATERPID pam may stay
  HELD = 1;
  ABSENT = 0;
  CANNOT_TELL = -1;
  PATH_KEY = 'Environment';
  MAX_PATH_CHARS = 260; // Windows' MAX_PATH, terminator included: Inno 6.7.4 has no long paths
  UNINSTALL_KEYS = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\';

var
  SetupLock: THandle; // never closed: the kernel closes it when this process ends, rollback included
  Refusal: String;
  PathAdded: String;
  PreviousPathAdded: String; // read BEFORE ssInstall: Inno re-creates the uninstall key first (F-P1)
  PreviousVersion: String;
  UninstallPathEntry: String;

function CreateFileW(Name: String; Access, Share, Security, Disposition, Flags, Template: Cardinal): THandle;
  external 'CreateFileW@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): BOOL;
  external 'CloseHandle@kernel32.dll stdcall';
function GetTickCount(): Cardinal;
  external 'GetTickCount@kernel32.dll stdcall';

function InstallRoot(): String;
begin
  Result := ExpandConstant('{#InstallRoot}');
end;

function UninstallKey(): String;
begin
  Result := UNINSTALL_KEYS + ExpandConstant('{#emit SetupSetting("AppId")}') + '_is1';
end;

function IsDigits(Value: String): Boolean;
var
  I: Integer;
begin
  Result := Value <> '';
  for I := 1 to Length(Value) do
    if (Value[I] < '0') or (Value[I] > '9') then Result := False;
end;

// The probe (10C A2): an open for DELETE, every share mode, delete-on-close. Never os.remove.
function Probe(Path: String): Integer;
var
  Handle: THandle;
  Error: LongInt;
  Started: Cardinal;
begin
  Started := GetTickCount();
  while True do begin
    Handle := CreateFileW(Path, DELETE_ACCESS, SHARE_RWD, 0, OPEN_EXISTING, DELETE_ON_CLOSE, 0);
    if Handle <> THandle(-1) then begin
      CloseHandle(Handle); // nobody held it: stale, and removed on sight
      Result := ABSENT;
      exit;
    end;
    Error := DLLGetLastError();
    if Error = ERROR_SHARING_VIOLATION then begin Result := HELD; exit; end;
    if (Error = ERROR_FILE_NOT_FOUND) or (Error = ERROR_PATH_NOT_FOUND) then begin Result := ABSENT; exit; end;
    if (Error <> ERROR_ACCESS_DENIED) or (GetTickCount() - Started > RETRY_MS) then begin Result := CANNOT_TELL; exit; end;
    Sleep(5);
  end;
end;

// The pids of every held run file but Excluded, space-separated; '' for none; 'X' for cannot tell.
function HeldPids(Excluded: String): String;
var
  Folder, Pid: String;
  Found: TFindRec;
begin
  Result := '';
  Folder := InstallRoot() + '\run';
  if not DirExists(Folder) then exit;
  if not FindFirst(Folder + '\*', Found) then begin Result := 'X'; exit; end; // '.' always lists
  try
    repeat
      Pid := Copy(Found.Name, 1, Length(Found.Name) - 5);
      if IsDigits(Pid) and (Copy(Found.Name, Length(Found.Name) - 4, 5) = '.lock') and (Pid <> Excluded) then
        case Probe(Folder + '\' + Found.Name) of
          HELD: Result := Result + Pid + ' ';
          CANNOT_TELL: begin Result := 'X'; exit; end;
        end;
    until not FindNext(Found);
  finally
    FindClose(Found);
  end;
end;

// setup.lock FIRST (10C, the order): share 0, delete-on-close, held until this process ends.
function TakeSetupLock(): Boolean;
var
  Started: Cardinal;
begin
  ForceDirectories(InstallRoot());
  Started := GetTickCount();
  repeat
    SetupLock := CreateFileW(InstallRoot() + '\setup.lock', GENERIC_READ or GENERIC_WRITE or DELETE_ACCESS,
      0, 0, OPEN_ALWAYS, DELETE_ON_CLOSE, 0);
    Result := SetupLock <> THandle(-1);
    if not Result then Sleep(10);
  until Result or (GetTickCount() - Started > SETUP_RETRY_MS);
  if not Result then
    Refusal := 'Another pam installer is running. Let it finish, then try again.';
end;

// THEN the run files: the updater (/UPDATERPID) may stay 30 s, any other pam gets 2 s (A3).
function NoPamRunning(): Boolean;
var
  Updater, Others: String;
  UpdaterHeld: Boolean;
  Started: Cardinal;
begin
  Updater := ExpandConstant('{param:UPDATERPID|}');
  if not IsDigits(Updater) then Updater := '';
  Started := GetTickCount();
  Result := False;
  while True do begin
    Others := HeldPids(Updater);
    if Others = 'X' then begin
      Refusal := 'pam''s installer cannot tell whether pam is running. Make ' + InstallRoot() +
        ' readable and writable, then try again.';
      exit;
    end;
    UpdaterHeld := (Updater <> '') and (Probe(InstallRoot() + '\run\' + Updater + '.lock') <> ABSENT);
    if (Others = '') and not UpdaterHeld then begin Result := True; exit; end;
    if (Others <> '') and (GetTickCount() - Started > GRACE_MS) then begin
      Refusal := 'pam is running. Close it (and stop pam start), then run the installer again.';
      exit;
    end;
    if GetTickCount() - Started > UPDATER_MS then begin
      Refusal := 'pam update has not closed. Close it, then run the installer again.';
      exit;
    end;
    Sleep(20);
  end;
end;

function Exclusive(): Boolean;
begin
  Result := TakeSetupLock() and NoPamRunning();
  if not Result then Log('pam refused: ' + Refusal);
end;

#ifdef PamTestRoot
#include "test-hooks.iss"
#endif

function InitializeSetup(): Boolean;
begin
  Result := Exclusive();
  if not Result then SuppressibleMsgBox(Refusal, mbError, MB_OK, IDOK);
#ifdef PamTestRoot
  TestEvent('initialize-setup ' + IntToStr(Ord(Result)));
#endif
end;

function InitializeUninstall(): Boolean;
begin
  Result := Exclusive();
  if not Result then SuppressibleMsgBox(Refusal, mbError, MB_OK, IDOK);
#ifdef PamTestRoot
  TestEvent('initialize-uninstall ' + IntToStr(Ord(Result)));
#endif
end;

// pam/catalog/ledger.py's MIGRATION_NAME: four digits, '_', [a-z0-9_]+, '.sql', nothing else.
function IsMigrationName(Line: String): Boolean;
var
  I: Integer;
begin
  Result := (Length(Line) > 9) and IsDigits(Copy(Line, 1, 4)) and (Line[5] = '_') and
    (Copy(Line, Length(Line) - 3, 4) = '.sql');
  if Result then
    for I := 6 to Length(Line) - 4 do
      if not (((Line[I] >= 'a') and (Line[I] <= 'z')) or ((Line[I] >= '0') and (Line[I] <= '9')) or (Line[I] = '_')) then
        Result := False;
end;

// E12: '' when this payload may replace pam; else the refusal. A ledger it cannot read refuses.
function LedgerRefusal(): String;
var
  Ledger, Level, Line: String;
  Lines: TArrayOfString;
  I: Integer;
begin
  Result := '';
  Ledger := InstallRoot() + '\schema-ledger';
  if not FileExists(Ledger) and not DirExists(Ledger) then exit; // no ledger: no constraint
  if not LoadStringsFromFile(Ledger, Lines) then begin
    Result := 'pam''s installer cannot read ' + Ledger + ', which records the newest catalog on this' +
      ' PC, so it cannot tell whether this version can open your catalogs. Make it readable, then try again.';
    exit;
  end;
  Level := '';
  for I := 0 to GetArrayLength(Lines) - 1 do begin
    Line := Lines[I];
    if (Length(Line) > 0) and (Line[Length(Line)] = #13) then Line := Copy(Line, 1, Length(Line) - 1);
    if IsMigrationName(Line) and (CompareStr(Line, Level) > 0) then Level := Line;
  end;
  if (Level <> '') and (CompareStr('{#HighestMigration}', Level) < 0) then begin
    Result := '{#NeedsNewer}';
    StringChangeEx(Result, '{offered}', '{#PamVersion}', True);
    StringChangeEx(Result, '{path}', Ledger, True);
    Result := 'pam''s installer will not install pam {#PamVersion}: ' + Result;
  end;
end;

// Task 13B-F: '' when every file fits below Dir; else the refusal. The build measured the longest
// path below {app}; Dir + '\' + that path must stay under MAX_PATH, or the copy fails half-way.
function PathTooLong(Dir: String): String;
begin
  Result := '';
  Dir := RemoveBackslashUnlessRoot(Dir);
  if Length(Dir) + 1 + {#LongestPayloadPath} >= MAX_PATH_CHARS then
    Result := 'pam''s installer cannot install into ' + Dir + ': that folder''s path is ' +
      IntToStr(Length(Dir)) + ' characters long, and pam''s files need one of at most ' +
      IntToStr(MAX_PATH_CHARS - 2 - {#LongestPayloadPath}) + '. Choose a shorter folder.';
end;

// The folder page: refused there, so the user can choose another folder.
function NextButtonClick(CurPageID: Integer): Boolean;
var
  Refused: String;
begin
  Result := True;
  if CurPageID = wpSelectDir then begin
    Refused := PathTooLong(WizardDirValue());
    if Refused <> '' then begin
      Log('pam refused: ' + Refused);
      SuppressibleMsgBox(Refused, mbError, MB_OK, IDOK);
      Result := False;
    end;
  end;
end;

// Every entry path (pam update, a directly run installer, Repair pam), BEFORE anything is
// replaced: the folder's length (an upgrade or a silent run may never show the folder page),
// E12, then E11's re-check with A3's grace (ruling 4).
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  if not RegQueryStringValue(HKCU, UninstallKey(), 'DisplayVersion', PreviousVersion) then
    PreviousVersion := '';
  PreviousPathAdded := GetPreviousData('PathAdded', '');
  Result := PathTooLong(ExpandConstant('{app}'));
  if Result = '' then Result := LedgerRefusal();
  if (Result = '') and not NoPamRunning() then Result := Refusal;
  if Result <> '' then Log('pam refused: ' + Result);
#ifdef PamTestRoot
  Result := TestPrepare(Result);
#endif
end;

function UserPath(): String;
begin
  if not RegQueryStringValue(HKCU, PATH_KEY, 'Path', Result) then Result := '';
end;

function OnUserPath(Entry: String): Boolean;
begin
  Result := Pos(';' + Uppercase(Entry) + ';', ';' + Uppercase(UserPath()) + ';') > 0;
end;

// The PATH task adds {app} once. Uninstall removes that one entry and keeps every other edit.
procedure AddToPath();
var
  Path: String;
begin
  Path := UserPath();
  if OnUserPath(PathAdded) then exit;
  if (Path <> '') and (Path[Length(Path)] <> ';') then Path := Path + ';';
  RegWriteExpandStringValue(HKCU, PATH_KEY, 'Path', Path + PathAdded);
end;

// Uninstall takes back exactly what AddToPath appended when it is still last (the PATH is then
// byte for byte what it was); otherwise it removes that one entry wherever the user moved it.
procedure RemoveFromPath(Entry: String);
var
  Path, Kept, Part: String;
  At: Integer;
  First: Boolean;
begin
  Path := UserPath();
  if not OnUserPath(Entry) then exit;
  if CompareText(Path, Entry) = 0 then Kept := ''
  else if CompareText(Copy(Path, Length(Path) - Length(Entry), Length(Entry) + 1), ';' + Entry) = 0 then
    Kept := Copy(Path, 1, Length(Path) - Length(Entry) - 1)
  else begin
    Path := Path + ';';
    Kept := '';
    First := True;
    while Path <> '' do begin
      At := Pos(';', Path);
      Part := Copy(Path, 1, At - 1);
      Delete(Path, 1, At);
      if CompareText(Part, Entry) <> 0 then begin
        if not First then Kept := Kept + ';';
        Kept := Kept + Part;
        First := False;
      end;
    end;
  end;
  RegWriteExpandStringValue(HKCU, PATH_KEY, 'Path', Kept);
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'PathAdded', PathAdded);
end;

function JsonString(Value: String): String;
begin
  StringChangeEx(Value, '\', '\\', True);
  StringChangeEx(Value, '"', '\"', True);
  Result := '"' + Value + '"';
end;

// E13: the record pam/install_method.py reads.
procedure WriteRecord();
var
  Components: String;
begin
  Components := '"base"';
  if WizardIsComponentSelected('reading') then Components := Components + ', "reading"';
  SaveStringToFile(ExpandConstant('{app}\pam-install.json'), '{"method": "installer", "version": ' +
    JsonString('{#PamVersion}') + ', "components": [' + Components + ']}' + #13#10, False);
end;

function CacheEntry(Folder, Name: String): String;
begin
  Result := '    {"file": ' + JsonString(Name) + ', "sha256": ' +
    JsonString(GetSHA256OfFile(Folder + '\' + Name)) + '}';
end;

// A cache name this installer wrote: pam-setup-<version>.exe, never a path.
function IsInstallerName(Name: String): Boolean;
begin
  Result := (Copy(Name, 1, 10) = 'pam-setup-') and (Length(Name) > 14) and
    (CompareText(Copy(Name, Length(Name) - 3, 4), '.exe') = 0) and (Pos('\', Name) = 0) and
    (Pos('/', Name) = 0) and (Pos(':', Name) = 0);
end;

// F-P2: the previous version's installer that installers.json already lists beside Current, or ''.
function ListedPrevious(Folder, Current: String): String;
var
  Lines: TArrayOfString;
  I, At: Integer;
  Name: String;
begin
  Result := '';
  if not LoadStringsFromFile(Folder + '\installers.json', Lines) then exit;
  for I := 0 to GetArrayLength(Lines) - 1 do begin
    At := Pos('{"file": "', Lines[I]);
    if At > 0 then begin
      Name := Copy(Lines[I], At + 10, Length(Lines[I]));
      Name := Copy(Name, 1, Pos('"', Name) - 1);
      if IsInstallerName(Name) and (CompareText(Name, Current) <> 0) then Result := Name;
    end;
  end;
end;

// The previous-installer cache: this installer and the previous version's, and no other.
procedure CacheInstaller();
var
  Folder, Current, Previous, Entries: String;
  Found: TFindRec;
begin
  Folder := InstallRoot() + '\installers';
  ForceDirectories(Folder);
  Current := 'pam-setup-{#PamVersion}.exe';
  Previous := '';
  if (PreviousVersion <> '') and (PreviousVersion <> '{#PamVersion}') then
    Previous := 'pam-setup-' + PreviousVersion + '.exe'
  else if PreviousVersion = '{#PamVersion}' then
    Previous := ListedPrevious(Folder, Current); // Repair pam keeps the way back (F-P2)
  if CompareText(ExpandConstant('{srcexe}'), Folder + '\' + Current) <> 0 then
    FileCopy(ExpandConstant('{srcexe}'), Folder + '\' + Current, False);
  if FindFirst(Folder + '\pam-setup-*.exe', Found) then
    try
      repeat
        if (CompareText(Found.Name, Current) <> 0) and (CompareText(Found.Name, Previous) <> 0) then
          DeleteFile(Folder + '\' + Found.Name);
      until not FindNext(Found);
    finally
      FindClose(Found);
    end;
  Entries := CacheEntry(Folder, Current);
  if (Previous <> '') and FileExists(Folder + '\' + Previous) then
    Entries := Entries + ',' + #13#10 + CacheEntry(Folder, Previous);
  SaveStringToFile(Folder + '\installers.json', '{"current": ' + JsonString(Current) +
    ', "installers": [' + #13#10 + Entries + #13#10 + ']}' + #13#10, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  // Decided before the uninstall entry is written, so RegisterPreviousData records it. An {app}
  // ALREADY on the user's PATH that this installer did not put there is the user's own entry: it
  // is not recorded, so no uninstall takes it away (F-P3).
  if (CurStep = ssInstall) and WizardIsTaskSelected('path') then begin
    PathAdded := ExpandConstant('{app}');
    if OnUserPath(PathAdded) and (CompareText(PreviousPathAdded, PathAdded) <> 0) then PathAdded := '';
  end;
  if CurStep = ssPostInstall then begin
    WriteRecord();
    // F-P1: what the previous install recorded, read in PrepareToInstall (GetPreviousData is
    // empty by now). A re-run that deselects the task takes that entry back.
    if (PreviousPathAdded <> '') and (CompareText(PreviousPathAdded, PathAdded) <> 0) then
      RemoveFromPath(PreviousPathAdded);
    if PathAdded <> '' then AddToPath();
    CacheInstaller();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
#ifdef PamTestRoot
  TestUninstallStep(CurUninstallStep);
#endif
  if (CurUninstallStep = usUninstall) and not RegQueryStringValue(HKCU, UninstallKey(),
    'Inno Setup CodeFile: PathAdded', UninstallPathEntry) then
    UninstallPathEntry := '';
  if (CurUninstallStep = usPostUninstall) and (UninstallPathEntry <> '') then
    RemoveFromPath(UninstallPathEntry);
end;
