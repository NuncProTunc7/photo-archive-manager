// TEST BUILDS ONLY (ruling 5). pam.iss includes this file only under #ifdef PamTestRoot, and
// tools/installer/build.py never passes PamTestRoot to a production compile
// (tests/test_installer_source.py). Nothing here is in the shipped installer.
//
// Each hook writes one line, "<event> <pid>", to events.log in the test root, so a test can see
// which process ran which step. Test-only switches:
//   /PAMGATE=<file>       setup waits in PrepareToInstall (uninstall: at usUninstall) while the
//                         file exists, holding setup.lock, so a test can look at it;
//   /PAMFAULT=copy        the copy FAILS after the whole payload is replaced, the way a full disk
//                         fails it (Abort, then rollback);
//   /PAMFAULT=cancel      the Cancel button is clicked half-way through the copy (run it with
//                         the wizard on screen);
//   /PAMCANCELAT=<page>   the wizard walks on with Next and clicks Cancel on that page.
// An exception raised in an event function does NOT abort Inno 6.7.4 (measured), so faults are
// made as real failures, and cancellations as real clicks.

var
  TestFaultDone: Boolean;

function GetCurrentProcessId(): Cardinal;
  external 'GetCurrentProcessId@kernel32.dll stdcall';
function PostMessage(Wnd: HWND; Msg, WParam, LParam: LongInt): BOOL;
  external 'PostMessageW@user32.dll stdcall';

// A test READING events.log can make one append fail (a sharing violation, measured: the line
// was lost and the test waited for it): each append is retried for up to a second.
procedure TestEvent(Name: String);
var
  Tries: Integer;
begin
  for Tries := 1 to 100 do begin
    if SaveStringToFile(ExpandConstant('{#PamTestRoot}') + '\events.log',
      Name + ' ' + IntToStr(GetCurrentProcessId()) + #13#10, True) then exit;
    Sleep(10);
  end;
end;

procedure TestClick(Button: TNewButton);
begin
  PostMessage(Button.Handle, $00F5, 0, 0); // BM_CLICK, processed as the user's own click
end;

function TestPrepare(Refused: String): String;
var
  Gate: String;
begin
  Result := Refused;
  TestEvent('prepare-to-install ' + IntToStr(Ord(Result = '')));
  Gate := ExpandConstant('{param:PAMGATE|}');
  if (Result = '') and (Gate <> '') then begin
    TestEvent('gate-wait');
    while FileExists(Gate) do Sleep(20);
  end;
end;

procedure TestUninstallStep(Step: TUninstallStep);
var
  Gate: String;
begin
  TestEvent('uninstall-step ' + IntToStr(Ord(Step)));
  Gate := ExpandConstant('{param:PAMGATE|}');
  if (Step = usUninstall) and (Gate <> '') then begin
    TestEvent('gate-wait');
    while FileExists(Gate) do Sleep(20);
  end;
end;

function TestFaultHere(): Boolean;
var
  FaultPath: String;
begin
  Result := ExpandConstant('{param:PAMFAULT|}') = 'copy'; // the probe file exists only to fail
  FaultPath := ExpandConstant('{app}') + '\fault-probe.txt';
  if Result and (WizardForm.CurPageID = wpInstalling) and not DirExists(FaultPath) then begin
    TestEvent('fault-copy');
    DeleteFile(FaultPath);
    ForceDirectories(FaultPath);
    SaveStringToFile(FaultPath + '\blocker', 'x', False);
  end;
end;

procedure CurInstallProgressChanged(CurProgress, MaxProgress: Integer);
begin
  if TestFaultDone or (MaxProgress = 0) or (CurProgress * 2 < MaxProgress) then exit;
  TestFaultDone := True;
  TestEvent('mid-copy');
  if ExpandConstant('{param:PAMFAULT|}') = 'cancel' then TestClick(WizardForm.CancelButton);
end;

procedure CurPageChanged(CurPageID: Integer);
var
  Target: Integer;
begin
  TestEvent('page ' + IntToStr(CurPageID));
  Target := StrToIntDef(ExpandConstant('{param:PAMCANCELAT|0}'), 0);
  // A cancel during the copy needs the wizard on screen (a /VERYSILENT Cancel button ignores a click,
  // measured): the walker takes it to the end, and the click comes from CurInstallProgressChanged.
  if ExpandConstant('{param:PAMFAULT|}') = 'cancel' then Target := wpFinished;
  if Target = CurPageID then PostMessage(WizardForm.Handle, $0010, 0, 0) // WM_CLOSE: the title bar's X
  // Inno 6.7.4 reports wpWelcome and then leaves it ON ITS OWN (DisableWelcomePage, measured): a
  // click posted there lands on the next page, so the walker clicks from the first real page.
  else if (Target > CurPageID) and (CurPageID > wpWelcome) and (CurPageID < wpInstalling) then
    TestClick(WizardForm.NextButton);
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  TestEvent('cancel ' + IntToStr(CurPageID));
  Confirm := False;
end;
