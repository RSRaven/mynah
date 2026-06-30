; Mynah — Inno Setup installer
;
; Per-user install of the small base (no GPU runtime, no model — fetched on first run). Built
; by CI from the PyInstaller onedir output (dist\Mynah). Compile:
;     iscc /DMyAppVersion=0.1.0 installer.iss   ->   dist\Mynah-Setup-0.1.0.exe
;
; The uninstaller removes everything app-specific automatically (app files + engine runtime
; packs + config + logs via "--purge-runtime"), then asks per-model which downloaded models to
; delete from the shared cache (the "--purge-ui" checklist, nothing checked by default).

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppName "Mynah"
#define MyAppExe "Mynah.exe"
#define MyAppPublisher "Mynah"
#define MyAppURL "https://github.com/RSRaven/mynah"

[Setup]
AppId={{B3F1B7C2-6E1E-4B0A-9C7E-1A2B3C4D5E6F}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
; Per-user install — no admin prompt.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExe}
OutputDir=dist
OutputBaseFilename=Mynah-Setup-{#MyAppVersion}
SetupIconFile=mynah\assets\mynah.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "runatlogin"; Description: "Start Mynah automatically when I sign in"; GroupDescription: "Startup:"
Name: "launchafter"; Description: "Launch Mynah after install"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
; The entire PyInstaller onedir output.
Source: "dist\{#MyAppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start-menu shortcut (always) + uninstall entry; optional desktop shortcut.
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Registry]
; "Run at login" — a per-user HKCU Run value, created only if the task is selected and removed
; on uninstall. The in-app Settings toggle writes the same value live.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
  ValueName: "Mynah"; ValueData: """{app}\{#MyAppExe}"""; Tasks: runatlogin; \
  Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Launch Mynah"; Tasks: launchafter; \
  Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  { Run both purges while the app exe still exists (usUninstall fires before files are
    deleted). This lives in Code rather than an UninstallRun entry because Inno evaluates
    an UninstallRun entry's "Check:" while saving uninstall info during Setup, where the
    uninstaller-only UninstallSilent function isn't callable and aborts the install. }
  if CurUninstallStep = usUninstall then
  begin
    { 1) Silently remove engine runtime packs + config + logs + the autostart key (never
         touches the shared model cache). }
    Exec(ExpandConstant('{app}\{#MyAppExe}'), '--purge-runtime', '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode);
    { 2) Ask, per-model, which downloaded models to delete from the shared cache (none by
         default). Shown only on an interactive uninstall — never on a silent /VERYSILENT one,
         where the GUI checklist would block with no one to click it. }
    if not UninstallSilent then
      Exec(ExpandConstant('{app}\{#MyAppExe}'), '--purge-ui', '', SW_SHOW,
        ewWaitUntilTerminated, ResultCode);
  end;
end;
