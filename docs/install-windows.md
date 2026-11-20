# Installing tsb-tui on Windows

## 1. What you need

`<tsb-host>` below is the address of your TSB instance; your administrator gives it to you with your login.

- **Windows:** Windows 11 x64 (Windows 10 x64 is supported via browser download / manual installer).
- **Architecture:** 64-bit AMD/Intel (`x64`).
- **Network:** Access to the company LAN (required to reach the download server and broker).
- **Shell:** PowerShell.
- **Account:** A TSB username and temporary password from your administrator.

## 2. Install

### Recommended: One-line PowerShell installer (Windows 11)
Open PowerShell and run:
```powershell
irm https://<tsb-host>/downloads/tsb-tui/install.ps1 | iex
```

This version-independent installer:
- Reads `latest.json` to find the newest release.
- Downloads `tsb-tui-<version>-windows-x64.zip` and checks its SHA-256 checksum and size.
- Extracts the files into `$env:LOCALAPPDATA\Programs\tsb-tui`.
- Writes a default `config.toml` configured with the broker address.
- Adds the install folder to your User `PATH`.
- Unblocks downloaded files and runs `tsb-tui-check.cmd`.

### Browser-download fallback (Windows 10 or offline)
1. Open `https://<tsb-host>/downloads/tsb-tui/latest.json` to look up `<version>` (for example, `0.5.0`).
2. Download these three files into the same directory:
   - `https://<tsb-host>/downloads/tsb-tui/install.ps1`
   - `https://<tsb-host>/downloads/tsb-tui/v<version>/tsb-tui-<version>-windows-x64.zip`
   - `https://<tsb-host>/downloads/tsb-tui/v<version>/SHA256SUMS`
3. In PowerShell, navigate to that folder and run:
   ```powershell
   .\install.ps1 -Zip .\tsb-tui-<version>-windows-x64.zip -Broker https://<tsb-host>
   ```

## 3. Check it works

The installer automatically runs `tsb-tui-check.cmd`. You can run it manually at any time:
```powershell
tsb-tui-check.cmd
```
(Or run `& "$env:LOCALAPPDATA\Programs\tsb-tui\tsb-tui-check.cmd"`). It verifies the engine and prints:
```text
Engine: <version>
Manifest check: PASS
TUI: <version>
```

Open a **new** terminal tab (existing tabs do not see PATH updates) and verify:
```powershell
tsb-tui --version
```

## 4. First run

In a **new** terminal tab, launch the TUI:
```powershell
tsb-tui
```
(If no config file exists or you need to supply the address explicitly, run `tsb-tui --broker https://<tsb-host>`).

### Sign in and change password
1. Sign in with the username and temporary password provided by your administrator.
2. At first sign-in, change your password: press `F1` or `Ctrl-\` to open the Menu, then choose **Change my password…**.

## 5. Update

The TUI checks for updates in the background on startup. A toast notification points to **Settings…** when an update is available.

To update:
1. Close any running `tsb-tui` windows.
2. Re-run the one-line installer in PowerShell:
   ```powershell
   irm https://<tsb-host>/downloads/tsb-tui/install.ps1 | iex
   ```
   Or re-run `install.ps1` with `-Zip` and `-Broker` using the newer zip archive.

The installer updates the files safely while preserving your existing `config.toml`.

## 6. Uninstall

To uninstall:
1. Close `tsb-tui`.
2. Delete the program folder in PowerShell:
   ```powershell
   Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Programs\tsb-tui"
   ```
3. (Optional) Remove `%LOCALAPPDATA%\Programs\tsb-tui` from your User PATH environment variable.
4. (Optional) Delete configuration and saved state:
   Remove `%APPDATA%\tsb-tui` and `%LOCALAPPDATA%\tsb-tui`.
