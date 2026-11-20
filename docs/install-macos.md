# Installing tsb-tui on macOS

## 1. What you need

`<tsb-host>` below is the address of your TSB instance; your administrator gives it to you with your login.

- **macOS:** macOS 13 or later.
- **Chip:** Apple silicon (`arm64`, M-series) or Intel (`x64`).
- **Network:** Access to the company LAN (required to reach the download server and broker).
- **Tools:** Standard command-line tools: `curl`, `shasum`, and `unzip`.
- **Account:** A TSB username and temporary password from your administrator.

## 2. Install

`install.sh` refuses macOS because macOS support uses standalone zip bundles instead of pipx. Install using the release zip for your chip.

### Find the version
The latest version is listed in `latest.json` at:
`https://<tsb-host>/downloads/tsb-tui/latest.json`

Read the `version` field for `<version>` (for example, `0.5.0`).

### Download the zip and checksum
Download using `curl` in Terminal. Do not download via a web browser, as macOS will quarantine browser downloads. (If you do use a browser, clear the quarantine attribute after extracting with `xattr -dr com.apple.quarantine <folder>`).

**For Apple silicon (M1/M2/M3/M4):**
```bash
curl -fsSL -O https://<tsb-host>/downloads/tsb-tui/v<version>/tsb-tui-<version>-macos-arm64.zip
curl -fsSL -O https://<tsb-host>/downloads/tsb-tui/v<version>/tsb-tui-<version>-macos-arm64.zip.sha256
```

**For Intel:**
```bash
curl -fsSL -O https://<tsb-host>/downloads/tsb-tui/v<version>/tsb-tui-<version>-macos-x64.zip
curl -fsSL -O https://<tsb-host>/downloads/tsb-tui/v<version>/tsb-tui-<version>-macos-x64.zip.sha256
```

### Verify the checksum
Run `shasum` against the `.sha256` file:

**Apple silicon:**
```bash
shasum -a 256 -c tsb-tui-<version>-macos-arm64.zip.sha256
```

**Intel:**
```bash
shasum -a 256 -c tsb-tui-<version>-macos-x64.zip.sha256
```

The output must show `OK`.

### Unzip
Extract the archive:
```bash
unzip tsb-tui-<version>-macos-<arch>.zip
cd tsb-tui-<version>-macos-<arch>
```

To run `tsb-tui` from any directory, add this folder to your `PATH` or symlink `tsb-tui` and `tsb-tui-check` into a directory on your PATH (such as `~/.local/bin` or `/usr/local/bin`).

## 3. Check it works

Inside the unzipped folder, run the check script:
```bash
./tsb-tui-check
```
It tests the terminal engine and prints:
```text
Engine: <version>
Manifest check: PASS
TUI: <version>
```

Verify that the CLI reports its version:
```bash
./tsb-tui --version
```

## 4. First run

Start the TUI with the broker address:
```bash
./tsb-tui --broker https://<tsb-host>
```

To avoid typing the broker address every time, create a `config.toml` file beside the `tsb-tui` executable (or in `~/.config/tsb-tui/config.toml`):
```toml
[broker]
url = "https://<tsb-host>"
```
With `config.toml` in place, you can start simply with `./tsb-tui`.

### Sign in and change password
1. Sign in with the username and temporary password provided by your administrator.
2. At first sign-in, change your password: press `F1` or `Ctrl-\` to open the Menu, then select **Change my password…**.

## 5. Update

The TUI checks for updates in the background on startup. When an update is available, a toast notification points to **Settings…** with the newest version information.

To update:
1. Check `https://<tsb-host>/downloads/tsb-tui/latest.json` for the new `<version>`.
2. Download the newer zip and `.sha256` for your chip with `curl`.
3. Verify the checksum with `shasum -a 256 -c`.
4. Unzip into a new folder. If you had a `config.toml` beside the previous binary, copy it into the new folder.

Your remote sessions on the broker and your settings in `~/.config/tsb-tui/` and `~/.local/state/tsb-tui/` are preserved.

## 6. Uninstall

To uninstall:
1. Delete the unzipped `tsb-tui-<version>-macos-<arch>` folder.
2. Remove any symlinks or `PATH` entries you added.
3. (Optional) Remove stored preferences and cached state:
   ```bash
   rm -rf ~/.config/tsb-tui ~/.local/state/tsb-tui
   ```
