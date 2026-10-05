; Inno Setup 6 script: GUI installer + uninstaller for SkyDispatch (Windows 10/11, 64-bit).
; Build: ISCC /DAppVersion=1.4.2 packaging\windows\skydispatch.iss   (or: python packaging/build.py)
#ifndef AppVersion
  #define AppVersion "1.4.2"
#endif
#define AppName "SkyDispatch"
#define AppExe "SkyDispatch.exe"

[Setup]
AppId={{6B1E5C0A-9F3D-4B7E-8A52-3C9D2E41F7A8}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=SkyDispatch
AppSupportURL=https://github.com/SubduedGaming/MSFS-FlightDispatch
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=..\..\LICENSE
SetupIconFile=skydispatch.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir=..\..\dist
OutputBaseFilename=SkyDispatch-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} Setup

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Components]
Name: "main"; Description: "SkyDispatch application"; Types: full compact custom; Flags: fixed

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\..\dist\SkyDispatch\*"; DestDir: "{app}"; Components: main; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
; An in-app update runs silently (/UPDATING=1): start the app again afterwards, as the normal user (not elevated).
Filename: "{app}\{#AppExe}"; Flags: nowait runasoriginaluser; Check: IsUpdate

[Code]
var
  InstallFinished: Boolean;

function IsUpdate: Boolean;
begin
  Result := ExpandConstant('{param:UPDATING|0}') = '1';
end;

// An in-app update starts this installer while SkyDispatch is still shutting down. The app holds the named mutex
// until its process ends, so wait for it (up to 30 s) instead of failing on locked files and rolling back.
function InitializeSetup: Boolean;
var
  i: Integer;
begin
  if IsUpdate then
    for i := 1 to 60 do
    begin
      if not CheckForMutexes('SkyDispatch.Running') then
        Break;
      Sleep(500);
    end;
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssDone then
    InstallFinished := True;
end;

// If an in-app update did not complete (cancelled, rolled back), reopen the version that is still installed so the
// app never just disappears.
procedure DeinitializeSetup;
var
  ResultCode: Integer;
  Exe: String;
begin
  if IsUpdate and (not InstallFinished) then
  begin
    Exe := ExpandConstant('{app}\{#AppExe}');
    if FileExists(Exe) then
      ExecAsOriginalUser(Exe, '', ExtractFilePath(Exe), SW_SHOWNORMAL, ewNoWait, ResultCode);
  end;
end;

// Career data lives in %LOCALAPPDATA%\SkyDispatch. Keep it by default so reinstalling never loses a career.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\SkyDispatch');
    if DirExists(DataDir) and (not UninstallSilent()) then
      if MsgBox('Also delete your career data (pilot, hangar, logbook, settings, downloaded voices)?' + #13#10 +
                'Choose No to keep it for a future reinstall.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
