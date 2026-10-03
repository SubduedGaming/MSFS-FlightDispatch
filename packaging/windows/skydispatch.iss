; Inno Setup 6 script: GUI installer + uninstaller for SkyDispatch (Windows 10/11, 64-bit).
; Build: ISCC /DAppVersion=1.1.2 packaging\windows\skydispatch.iss   (or: python packaging/build.py)
#ifndef AppVersion
  #define AppVersion "1.1.2"
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

[Code]
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
