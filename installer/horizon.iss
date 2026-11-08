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
[Files]
Source: "{#PackageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{app}\Horizon"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-X utf8 -m hostrun.desktop --code ""{app}"" --archive ""{app}\horizon-archive"""; WorkingDir: "{app}"
