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
        Test-Path $new | Should -BeTrue
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
    It 'leaves the existing tree alone if the first rename fails' {
        Mock Rename-Item { throw 'first rename denied' } -ParameterFilter { $LiteralPath -eq $script:directory }
        { Switch-InstallTree $directory $new $old } | Should -Throw '*first rename denied*'
        [IO.File]::ReadAllText((Join-Path $directory 'identity')) | Should -Be 'old'
        Test-Path $new | Should -BeTrue
    }
}
