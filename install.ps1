# Per-user Windows installer. Download hashes detect corruption, not a hostile server:
# the manifest and assets share an origin and TLS connection. Signing comes later.
[CmdletBinding()]
param(
    [string]$Base = '@@TSB_ORIGIN@@/downloads/tsb-tui',
    [string]$Broker = '',
    [string]$Zip = ''
)

function Merge-UserPath([string]$Value, [string]$Directory) {
    $wanted = $Directory.TrimEnd('\')
    foreach ($entry in ($Value -split ';')) {
        if ($entry.TrimEnd('\') -ieq $wanted -or
            [Environment]::ExpandEnvironmentVariables($entry).TrimEnd('\') -ieq $wanted) {
            return $Value
        }
    }
    if ([string]::IsNullOrEmpty($Value)) { return $Directory }
    return $Value.TrimEnd(';') + ';' + $Directory
}

function Test-InstallProcessPath([string]$ProcessPath, [string]$Directory) {
    if (-not $ProcessPath) { return $false }
    return $ProcessPath.StartsWith($Directory.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)
}

function Get-ConfigText([string]$Url) {
    # TOML basic strings use these same escapes; ConvertTo-Json also escapes controls.
    $quoted = ConvertTo-Json -InputObject $Url -Compress
    return "[broker]`nurl = $quoted`n"
}

function Write-FirstConfig([string]$Path, [string]$Url) {
    if (Test-Path -LiteralPath $Path) {
        Write-Host "Keeping existing config: $Path"
    } elseif ($Url) {
        [IO.File]::WriteAllText($Path, (Get-ConfigText $Url), (New-Object Text.UTF8Encoding $false))
    } else {
        Write-Host "No broker known; no config written at '$Path'. Pass -Broker <url> to the installer."
    }
}

function Switch-InstallTree([string]$Directory, [string]$New, [string]$Old) {
    $hadOld = Test-Path -LiteralPath $Directory
    if ($hadOld) { Rename-Item -LiteralPath $Directory -NewName (Split-Path $Old -Leaf) -ErrorAction Stop }
    try {
        Rename-Item -LiteralPath $New -NewName (Split-Path $Directory -Leaf) -ErrorAction Stop
    } catch {
        $moveError = $_.Exception.Message
        if ($hadOld) {
            try {
                Rename-Item -LiteralPath $Old -NewName (Split-Path $Directory -Leaf) -ErrorAction Stop
            } catch {
                $restoreError = $_.Exception.Message
                # Single-quote escaping keeps the printed recovery commands copyable in PowerShell.
                $qOld = $Old.Replace("'", "''")
                $qNew = $New.Replace("'", "''")
                $qLeaf = (Split-Path $Directory -Leaf).Replace("'", "''")
                throw "Moving '$New' to '$Directory' failed: $moveError. Restore also failed: $restoreError.`nRetained: $Old`nRetained: $New`nTo restore the old install: Rename-Item -LiteralPath '$qOld' -NewName '$qLeaf'`nOr to activate the new install: Rename-Item -LiteralPath '$qNew' -NewName '$qLeaf'"
            }
        }
        throw "Moving '$New' to '$Directory' failed: $moveError. The previous install was restored if one existed."
    }
    if ($hadOld) {
        try { Remove-Item -LiteralPath $Old -Recurse -Force -ErrorAction Stop }
        catch { Write-Warning "Installed, but could not remove '$Old': $($_.Exception.Message)" }
    }
}

function Install-TsbTui {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'
    $install = Join-Path $env:LOCALAPPDATA 'Programs\tsb-tui'
    $new = "$install.new"
    $old = "$install.old"
    foreach ($process in (Get-Process)) {
        if (Test-InstallProcessPath $process.Path $install) {
            throw 'Close tsb-tui and run again: a process is running from the install directory.'
        }
    }
    if ((Test-Path -LiteralPath $new) -or (Test-Path -LiteralPath $old)) {
        throw "Existing recovery directory: inspect '$new' and '$old' before retrying."
    }
    if (-not $Base.Contains('@@TSB_' + 'ORIGIN@@')) {
        $uri = [uri]$Base
        if (-not $uri.IsAbsoluteUri -or $uri.Scheme -notin @('https', 'http') -or $uri.UserInfo -or $uri.Query -or $uri.Fragment) {
            throw 'Invalid -Base URL.'
        }
        if (-not $Broker) { $Broker = $uri.GetLeftPart([UriPartial]::Authority) }
    }
    $temp = $null
    try {
        if ($Zip) {
            $archive = (Resolve-Path -LiteralPath $Zip).Path
            $sums = Join-Path (Split-Path $archive -Parent) 'SHA256SUMS'
            if (Test-Path -LiteralPath $sums) {
                $name = Split-Path $archive -Leaf
                $matches = @(Get-Content -LiteralPath $sums | Where-Object {
                    $_ -cmatch ('^[0-9a-fA-F]{64} [ *]' + [regex]::Escape($name) + '$')
                })
                if ($matches.Count -ne 1) { throw "Expected exactly one SHA256SUMS entry for $name." }
                $expected = $matches[0].Substring(0, 64)
                if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ine $expected) {
                    throw 'Zip SHA-256 mismatch; installation stopped.'
                }
                Write-Host 'Zip SHA-256 checked against adjacent SHA256SUMS.'
            } else {
                Write-Warning 'No adjacent SHA256SUMS: extraction and bundle layout only; no release SHA-256 verification.'
            }
        } else {
            if ($Base.Contains('@@TSB_' + 'ORIGIN@@')) { throw 'Pass -Base <url>; this installer has no rendered download base.' }
            foreach ($name in @('Tls12', 'Tls13')) {
                if ([Enum]::GetNames([Net.SecurityProtocolType]) -contains $name) {
                    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Enum]::Parse([Net.SecurityProtocolType], $name)
                }
            }
            $baseUrl = $Base.TrimEnd('/')
            $manifest = Invoke-RestMethod -Uri "$baseUrl/latest.json"
            if (($manifest.schema -isnot [int] -and $manifest.schema -isnot [long]) -or $manifest.schema -ne 1 -or
                $manifest.version -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
                $manifest.tag -cne "v$($manifest.version)" -or $manifest.dir -cne "v$($manifest.version)/") {
                throw 'Invalid latest.json; schema 1 with a matching version, tag and directory is required.'
            }
            $name = "tsb-tui-$($manifest.version)-windows-x64.zip"
            $asset = $manifest.assets.PSObject.Properties[$name].Value
            if (-not $asset -or $asset.sha256 -cnotmatch '^[0-9a-fA-F]{64}$' -or
                ($asset.size -isnot [int] -and $asset.size -isnot [long]) -or $asset.size -lt 0) {
                throw "Missing or invalid manifest asset: $name"
            }
            $temp = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
            New-Item -ItemType Directory -Path $temp | Out-Null
            $archive = Join-Path $temp $name
            Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/$($manifest.dir)$name" -OutFile $archive
            if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ine $asset.sha256 -or
                (Get-Item -LiteralPath $archive).Length -ne $asset.size) {
                Remove-Item -LiteralPath $archive -Force
                throw 'Downloaded zip SHA-256 or size mismatch; deleted and stopped.'
            }
        }
        # Expand into a disposable sibling before touching the installed tree.
        $parent = Split-Path $install -Parent
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
        $extract = Join-Path $parent ('tsb-tui.extract-' + [guid]::NewGuid().ToString())
        try {
            Expand-Archive -LiteralPath $archive -DestinationPath $extract
            $trees = @(Get-ChildItem -LiteralPath $extract -Force)
            if ($trees.Count -ne 1 -or -not $trees[0].PSIsContainer -or
                $trees[0].Name -cnotmatch '^tsb-tui-[0-9]+\.[0-9]+\.[0-9]+-windows-x64$') {
                throw 'Zip must contain one versioned Windows bundle directory.'
            }
            foreach ($required in @('tsb-tui.exe', 'tsb-tui-check.cmd', 'python\python.exe')) {
                if (-not (Test-Path -LiteralPath (Join-Path $trees[0].FullName $required) -PathType Leaf)) {
                    throw "Zip is missing $required."
                }
            }
            Move-Item -LiteralPath $trees[0].FullName -Destination $new
        } finally {
            if (Test-Path -LiteralPath $extract) { Remove-Item -LiteralPath $extract -Recurse -Force }
        }
        $config = Join-Path $install 'config.toml'
        if (Test-Path -LiteralPath $config) {
            Copy-Item -LiteralPath $config -Destination (Join-Path $new 'config.toml') -Force
            Write-Host "Keeping existing config: $config"
        }
        Switch-InstallTree $install $new $old
        Write-FirstConfig $config $Broker
        $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment')
        try {
            $path = $key.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
            $updated = Merge-UserPath $path $install
            $key.SetValue('Path', $updated, [Microsoft.Win32.RegistryValueKind]::ExpandString)
        } finally { $key.Close() }
        if (-not ('TsbInstaller.Native' -as [type])) {
            Add-Type @'
using System;
using System.Runtime.InteropServices;
namespace TsbInstaller {
    public static class Native {
        [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern IntPtr SendMessageTimeout(IntPtr hwnd, uint msg, UIntPtr wparam,
            string lparam, uint flags, uint timeout, out UIntPtr result);
    }
}
'@
        }
        $result = [UIntPtr]::Zero
        [void][TsbInstaller.Native]::SendMessageTimeout([IntPtr]0xffff, 0x1a, [UIntPtr]::Zero, 'Environment', 2, 5000, [ref]$result)
        $env:Path = Merge-UserPath $env:Path $install
        Get-ChildItem -LiteralPath $install -Recurse -File | Unblock-File
        & (Join-Path $install 'tsb-tui-check.cmd')
        if ($LASTEXITCODE -ne 0) { Write-Warning 'tsb-tui-check.cmd failed; the install has been kept. See its output above.' }
        # Read the preserved config, rather than claiming the supplied broker replaced it.
        $summary = @'
import pathlib, sys, tomllib
p = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(p.parent / 'lib' / 'site'))
from importlib.metadata import version
print('Version:', version('tsb-tui'))
print('Broker:', tomllib.loads(p.read_text(encoding='utf-8-sig')).get('broker', {}).get('url', 'not configured') if p.exists() else 'not configured')
'@
        $summary | & (Join-Path $install 'python\python.exe') - $config
        if ($LASTEXITCODE -ne 0) { Write-Warning "Could not read version/broker summary; see config at '$config'." }
        Write-Host "Installed at: $install"
        Write-Host 'Open a new terminal tab; tabs already open do not see the change. Type tsb-tui.'
    } finally {
        if ($temp -and (Test-Path -LiteralPath $temp)) { Remove-Item -LiteralPath $temp -Recurse -Force }
    }
}

# Dot-sourcing exposes functions for Pester without touching the registry or the network.
if ($MyInvocation.InvocationName -ne '.') { Install-TsbTui }
