; Inno Setup script for EVE Fleet Management Tool (step 8.3).
;
; Built by tools/build_release.py, which passes:
;   /DAppVersion=<app/version.py>   /DSourceDir=<PyInstaller output folder>   /O<output folder>
;
; Installs per user (no admin rights) into %LOCALAPPDATA%\Programs, because the app
; writes data\ next to the exe (app/paths.py). Upgrades install over the old version
; and never touch data\, so logins, the library and logs survive. The uninstaller
; asks whether to keep data\.

#ifndef AppVersion
  #error AppVersion is not defined: build with tools/build_release.py
#endif
#ifndef SourceDir
  #error SourceDir is not defined: build with tools/build_release.py
#endif

#define AppName "EVE Fleet Management Tool"
#define AppExe "EveFleetManagementTool.exe"

[Setup]
; Never change AppId: it is how upgrades find the installed copy.
AppId={{4216CD3E-C32D-481A-A83F-18209D462B5D}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=EveFleetManagementTool-{#AppVersion}-setup
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Clear the old version's libraries first, so files a newer build no longer has
; don't linger. data\ is not touched.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "\data"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{app}\data');
    if DirExists(DataDir) and not UninstallSilent then
    begin
      if MsgBox('Keep your data (character logins, doctrine library, fittings, logs and the EVE database)?' + #13#10#13#10 +
                'Choose Yes if you plan to reinstall. Choose No to delete it.',
                mbConfirmation, MB_YESNO) = IDNO then
      begin
        DelTree(DataDir, True, True, True);
        RemoveDir(ExpandConstant('{app}'));
      end;
    end;
  end;
end;
