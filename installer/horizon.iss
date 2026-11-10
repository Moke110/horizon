#ifndef AppVersion
  #define AppVersion "0.1.0-preview.1"
#endif
#ifndef NumericVersion
  #define NumericVersion "0.1.0.0"
#endif
#ifndef PackageDir
  #error PackageDir must name a built Horizon distribution
#endif
[Setup]
AppId={{FB7ABBA7-2DAB-43D9-8C17-7F164304E919}
AppName=Horizon
AppVersion={#AppVersion}
VersionInfoVersion={#NumericVersion}
DefaultDirName={localappdata}\Programs\Horizon
DefaultGroupName=Horizon
DisableDirPage=no
PrivilegesRequired=lowest
MinVersion=10.0
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
OutputDir=output
OutputBaseFilename=Horizon-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
AppMutex=Local\HorizonConsoleRunning
UninstallDisplayIcon={app}\runtime\python.exe
UninstallFilesDir={app}\.horizon\installer
[Files]
Source: "{#PackageDir}\*"; DestDir: "{tmp}\horizon-bootstrap"; Flags: dontcopy recursesubdirs createallsubdirs
Source: "{#PackageArchive}"; DestDir: "{tmp}"; DestName: "horizon-package.zip"; Flags: dontcopy
[Icons]
Name: "{app}\Horizon"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-B -X utf8 -m hostrun.desktop --code ""{app}"" --archive ""{app}\horizon-archive"""; WorkingDir: "{app}"

[Code]
function GetFileAttributesW(FileName: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';

function SafeOwnedPath(RelativePath: String): Boolean;
var Candidate, Root: String; Attributes: LongWord;
begin
  Result := False;
  if (RelativePath = '') or (Pos(':', RelativePath) > 0) or
     (Pos('..', RelativePath) > 0) or (RelativePath[1] = '\') or
     (RelativePath[1] = '/') then Exit;
  Root := ExpandConstant('{app}');
  Candidate := Root + '\' + RelativePath;
  while CompareText(Candidate, Root) <> 0 do begin
    if Pos(Lowercase(Root + '\'), Lowercase(Candidate)) <> 1 then Exit;
    Attributes := GetFileAttributesW(Candidate);
    if (Attributes <> $FFFFFFFF) and ((Attributes and $400) <> 0) then Exit;
    Candidate := ExtractFileDir(Candidate);
  end;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var Paths: TArrayOfString; I: Integer; RelativePath, Parent, Root: String;
begin
  if CurUninstallStep <> usUninstall then Exit;
  Root := ExpandConstant('{app}');
  if not LoadStringsFromFile(Root + '\.horizon\installer\owned-files.txt', Paths) then Exit;
  for I := 0 to GetArrayLength(Paths) - 1 do begin
    RelativePath := Paths[I];
    StringChangeEx(RelativePath, '/', '\', True);
    if SafeOwnedPath(RelativePath) then begin
      DeleteFile(Root + '\' + RelativePath);
      Parent := ExtractFileDir(Root + '\' + RelativePath);
      while CompareText(Parent, Root) <> 0 do begin
        if not RemoveDir(Parent) then Break;
        Parent := ExtractFileDir(Parent);
      end;
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var ExitCode: Integer;
begin
    Result := '';
    ExtractTemporaryFiles('*');
    if not Exec(ExpandConstant('{tmp}\horizon-bootstrap\runtime\pythonw.exe'),
      '-B -X utf8 -m hostrun.desktop_upgrade --code "' + ExpandConstant('{app}') +
      '" --package "' + ExpandConstant('{tmp}\horizon-package.zip') + '" --sha256 {#PackageDigest}',
      ExpandConstant('{tmp}\horizon-bootstrap'), SW_HIDE, ewWaitUntilTerminated, ExitCode) then
      Result := 'Could not start the bundled upgrade runtime.';
    if ExitCode <> 0 then
      Result := 'Upgrade was not applied. Close Horizon and retry; existing data is retained.';
end;
