BeforeAll {
    . (Join-Path $PSScriptRoot '../../install.ps1')
}

Describe 'Per-user PATH' {
    It 'keeps variables unexpanded, adds once and is idempotent' {
        $value = '%USERPROFILE%\bin;C:\Other'
        $merged = Merge-UserPath $value 'C:\Users\tester\Programs\tsb-tui'
        $merged | Should -Be ($value + ';C:\Users\tester\Programs\tsb-tui')
        (Merge-UserPath $merged 'C:\Users\tester\Programs\tsb-tui') | Should -Be $merged
    }
    It 'ignores case and trailing backslash' {
        $value = 'C:\Programs\TSB-TUI\;C:\Other'
        (Merge-UserPath $value 'c:\programs\tsb-tui') | Should -Be $value
    }
    It 'compares the expanded form without flattening the stored entry' {
        $env:TSB_INSTALL_TEST_HOME = 'C:\Users\tester'
        try {
            $value = '%TSB_INSTALL_TEST_HOME%\Programs\tsb-tui'
            (Merge-UserPath $value 'C:\Users\tester\Programs\tsb-tui') | Should -Be $value
        } finally { Remove-Item Env:TSB_INSTALL_TEST_HOME }
    }
    It 'creates a missing PATH value' {
        (Merge-UserPath '' 'C:\Programs\tsb-tui') | Should -Be 'C:\Programs\tsb-tui'
    }
}

Describe 'First config' {
    It 'writes UTF-8 without a BOM and escapes TOML strings' {
        $path = Join-Path $TestDrive 'config.toml'
        Write-FirstConfig $path 'https://broker.example/a"b\c'
        $bytes = [IO.File]::ReadAllBytes($path)
        $bytes[0] | Should -Be 91
        [IO.File]::ReadAllText($path) | Should -Be "[broker]`nurl = `"https://broker.example/a\`"b\\c`"`n"
    }
    It 'preserves even an empty existing config' {
        $path = Join-Path $TestDrive 'empty.toml'
        [IO.File]::WriteAllText($path, '')
        Write-FirstConfig $path 'https://new.example'
        (Get-Item $path).Length | Should -Be 0
    }
    It 'does not create config without a known broker' {
        $path = Join-Path $TestDrive 'absent.toml'
        Write-FirstConfig $path ''
        Test-Path $path | Should -BeFalse
    }
}

Describe 'Running-process refusal' {
    It 'recognizes interpreter and launcher paths regardless of case' {
        Test-InstallProcessPath 'c:\programs\TSB-TUI\python\python.exe' 'C:\Programs\tsb-tui' | Should -BeTrue
        Test-InstallProcessPath 'C:\Programs\tsb-tui\tsb-tui.exe' 'C:\Programs\tsb-tui' | Should -BeTrue
    }
    It 'does not refuse unrelated Python processes or a sibling directory' {
        Test-InstallProcessPath 'C:\Python\python.exe' 'C:\Programs\tsb-tui' | Should -BeFalse
        Test-InstallProcessPath 'C:\Programs\tsb-tui-other\python.exe' 'C:\Programs\tsb-tui' | Should -BeFalse
        Test-InstallProcessPath '' 'C:\Programs\tsb-tui' | Should -BeFalse
    }
}

Describe 'Install swap restoration' {
    BeforeEach {
        $script:directory = Join-Path $TestDrive 'tsb-tui'
        $script:new = "$directory.new"
        $script:old = "$directory.old"
        New-Item -ItemType Directory -Path $directory, $new -Force | Out-Null
        [IO.File]::WriteAllText((Join-Path $directory 'identity'), 'old')
        [IO.File]::WriteAllText((Join-Path $new 'identity'), 'new')
    }
    AfterEach {
        Get-ChildItem $TestDrive -Force | Remove-Item -Recurse -Force
    }
    It 'replaces successfully and removes the old tree' {
        Switch-InstallTree $directory $new $old
        [IO.File]::ReadAllText((Join-Path $directory 'identity')) | Should -Be 'new'
        Test-Path $old | Should -BeFalse
    }
    It 'restores the old install if activation fails' {
        Mock Rename-Item { throw 'activation denied' } -ParameterFilter { $LiteralPath -eq $script:new }
        { Switch-InstallTree $directory $new $old } | Should -Throw '*activation denied*'
        [IO.File]::ReadAllText((Join-Path $directory 'identity')) | Should -Be 'old'
        Test-Path $new | Should -BeFalse
        Test-Path $old | Should -BeFalse
    }
    It 'retains both trees and prints both manual recovery commands if restoration fails' {
        Mock Rename-Item { throw 'activation denied' } -ParameterFilter { $LiteralPath -eq $script:new }
        Mock Rename-Item { throw 'restoration denied' } -ParameterFilter { $LiteralPath -eq $script:old }
        $message = ''
        try { Switch-InstallTree $directory $new $old } catch { $message = $_.Exception.Message }
        $message | Should -BeLike '*activation denied*restoration denied*'
        $message | Should -BeLike "*Retained: $old*Retained: $new*"
        $message | Should -BeLike "*Rename-Item -LiteralPath '$old' -NewName 'tsb-tui'*"
        $message | Should -BeLike "*Rename-Item -LiteralPath '$new' -NewName 'tsb-tui'*"
        Test-Path $old | Should -BeTrue
        Test-Path $new | Should -BeTrue
        Test-Path $directory | Should -BeFalse
    }
    It 'preserves the original swap error when cleanup is denied and never retries cleanup' {
        $script:swapFailure = [InvalidOperationException]::new('first rename denied')
        Mock Rename-Item { throw $script:swapFailure } -ParameterFilter { $LiteralPath -eq $script:directory }
        Mock Remove-Item { throw 'cleanup removal denied' } -ParameterFilter { $LiteralPath -eq $script:new }
        Mock Write-Warning {}
        $failure = $null
        try {
            try { Switch-InstallTree $directory $new $old } catch {
                # The installer also asks for cleanup when a swap failure reaches its outer handler.
                Remove-FailedNewTree $new $_
                throw
            }
        } catch { $failure = $_ }
        $failure.Exception.Message | Should -Be 'first rename denied'
        [object]::ReferenceEquals($failure.Exception, $script:swapFailure) | Should -BeTrue
        Should -Invoke Remove-Item -Times 1 -Exactly -ParameterFilter { $LiteralPath -eq $script:new }
        Should -Invoke Write-Warning -Times 1 -Exactly -ParameterFilter {
            $Message -like '*first rename denied*' -and
            $Message -like '*cleanup removal denied*' -and
            $Message.Contains($script:new)
        }
        Test-Path $new | Should -BeTrue
        [IO.File]::ReadAllText((Join-Path $directory 'identity')) | Should -Be 'old'
    }
    It 'leaves the existing tree alone if the first rename fails' {
        Mock Rename-Item { throw 'first rename denied' } -ParameterFilter { $LiteralPath -eq $script:directory }
        { Switch-InstallTree $directory $new $old } | Should -Throw '*first rename denied*'
        [IO.File]::ReadAllText((Join-Path $directory 'identity')) | Should -Be 'old'
        Test-Path $new | Should -BeFalse
    }
}


Describe 'Download policy' {
    It 'never accesses the global TLS protocol selector' {
        (Get-Content (Join-Path $PSScriptRoot '../../install.ps1') -Raw) | Should -Not -Match 'ServicePointManager|SecurityProtocol'
    }
    It 'accepts HTTPS and exact HTTP loopback hosts' {
        foreach ($url in @('https://example.test/assets', 'http://localhost:123/a', 'http://127.0.0.1/a', 'http://[::1]/a')) {
            (Get-DownloadBase $url).IsAbsoluteUri | Should -BeTrue
        }
    }
    It 'refuses cleartext remote hosts' {
        foreach ($url in @('http://example.test', 'http://127.0.0.2', 'http://localhost.example')) {
            { Get-DownloadBase $url } | Should -Throw '*HTTPS*'
        }
    }
    It 'disables redirects for manifest and asset requests' {
        Mock Invoke-WebRequest { [pscustomobject]@{ StatusCode = 302; Content = '{}' } }
        { Get-ReleaseFile 'https://example.test/latest.json' } | Should -Throw '*Redirect*'
        { Get-ReleaseFile 'https://example.test/archive.zip' 'archive.zip' } | Should -Throw '*Redirect*'
        Should -Invoke Invoke-WebRequest -Times 2 -Exactly -ParameterFilter { $MaximumRedirection -eq 0 }
    }
}

Describe 'Startup recovery' {
    BeforeEach {
        $script:directory = Join-Path $TestDrive 'tsb-tui'
        $script:new = "$directory.new"
        $script:old = "$directory.old"
        New-Item -ItemType Directory -Path $directory, $new, $old -Force | Out-Null
    }
    AfterEach { Get-ChildItem $TestDrive -Force | Remove-Item -Recurse -Force }
    It 'removes stale siblings only for a complete installation with warnings' {
        New-Item -ItemType File -Path (Join-Path $directory 'tsb-tui.exe') | Out-Null
        Mock Write-Warning {}
        Initialize-InstallTree $directory $new $old
        Test-Path $new | Should -BeFalse
        Test-Path $old | Should -BeFalse
        Should -Invoke Write-Warning -Times 2 -Exactly
    }
    It 'preserves every folder when the live install is incomplete' {
        $message = ''
        try { Initialize-InstallTree $directory $new $old } catch { $message = $_.Exception.Message }
        foreach ($path in @($directory, $old, $new)) {
            Test-Path $path | Should -BeTrue
            $message | Should -BeLike "*$path*"
        }
        $message | Should -BeLike '*Rename-Item*'
    }
    It 'preserves recovery folders when the live installation is absent' {
        Remove-Item $directory -Recurse
        { Initialize-InstallTree $directory $new $old } | Should -Throw '*Rename-Item*'
        Test-Path $old | Should -BeTrue
        Test-Path $new | Should -BeTrue
    }
}

Describe 'Pre-swap failure cleanup' {
    It 'removes the new tree when preserving config fails and leaves the live tree intact' {
        $previousLocal = $env:LOCALAPPDATA
        $env:LOCALAPPDATA = $TestDrive
        $fixtureZip = Join-Path $TestDrive 'fixture.zip'
        New-Item -ItemType File -Path $fixtureZip | Out-Null
        . (Join-Path $PSScriptRoot '../../install.ps1') -Zip $fixtureZip
        $directory = Join-Path $TestDrive 'Programs/tsb-tui'
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
        [IO.File]::WriteAllText((Join-Path $directory 'config.toml'), 'preserved')
        New-Item -ItemType File -Path (Join-Path $directory 'tsb-tui.exe') | Out-Null
        Mock Get-Process {}
        Mock Expand-Archive {
            $tree = Join-Path $DestinationPath 'tsb-tui-1.2.3-windows-x64'
            New-Item -ItemType Directory -Path (Join-Path $tree 'python') -Force | Out-Null
            foreach ($file in @('tsb-tui.exe', 'tsb-tui-check.cmd', 'python/python.exe')) {
                New-Item -ItemType File -Path (Join-Path $tree $file) | Out-Null
            }
        }
        Mock Copy-Item { throw 'config copy denied' }
        try {
            { Install-TsbTui } | Should -Throw '*config copy denied*'
            Test-Path "$directory.new" | Should -BeFalse
            Test-Path "$directory.old" | Should -BeFalse
            [IO.File]::ReadAllText((Join-Path $directory 'config.toml')) | Should -Be 'preserved'
        } finally {
            $env:LOCALAPPDATA = $previousLocal
        }
    }
}
