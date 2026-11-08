param(
    [Parameter(Mandatory=$true)][string]$ISCC,
    [Parameter(Mandatory=$true)][string]$PackageDirectory,
    [string]$Version = '0.1.0-preview.1'
)
$ErrorActionPreference = 'Stop'
if ($Version -notmatch '^([0-9]+)\.([0-9]+)\.([0-9]+)(-[A-Za-z0-9.-]+)?$') { throw 'Invalid version' }
$NumericVersion = "$($Matches[1]).$($Matches[2]).$($Matches[3]).0"
$PackagePath = (Resolve-Path -LiteralPath $PackageDirectory).Path
foreach ($RequiredFile in @('manifest.json', 'runtime/python.exe', 'runtime/node.exe', 'agent/dist/main.js')) {
    if (-not (Test-Path -LiteralPath (Join-Path $PackagePath $RequiredFile) -PathType Leaf)) { throw "Incomplete distribution: $RequiredFile" }
}
$PackageArchive = "$PackagePath.zip"
if (-not (Test-Path -LiteralPath $PackageArchive -PathType Leaf)) { throw 'Build distribution zip before compiling the installer' }
$PackageDigest = (Get-FileHash -LiteralPath $PackageArchive -Algorithm SHA256).Hash.ToLowerInvariant()
Push-Location $PSScriptRoot
try {
    & $ISCC /Q "/DAppVersion=$Version" "/DNumericVersion=$NumericVersion" "/DPackageDir=$PackagePath" "/DPackageArchive=$PackageArchive" "/DPackageDigest=$PackageDigest" horizon.iss
    if ($LASTEXITCODE) { throw 'Installer compilation failed' }
} finally { Pop-Location }
