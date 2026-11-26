# Installing tsb-tui on macOS

## 1. What you need

`<tsb-host>` below is the address of your TSB instance; your administrator gives it to you with your login.

- **macOS:** macOS 13 or later.
- **Chip:** Apple silicon (`arm64`, M-series) or Intel (`x64`).
- **Network:** Access to the company LAN (required to reach the download server and broker).
- **Tools:** Standard command-line tools: `curl`, `shasum`, and `unzip`.
- **Account:** A TSB username and temporary password from your administrator.

## 2. Install

### Recommended: One-line installer
Run in Terminal:
```bash
curl -fsSL https://<tsb-host>/downloads/tsb-tui/install.sh | bash
```

This version-independent command:
- Reads `latest.json` and picks the zip for your chip (`macos-arm64` on Apple silicon, `macos-x64` on Intel).
- Downloads it with `curl` and checks its SHA-256 and size against `latest.json`; on a mismatch it stops and installs nothing.
- Unpacks it into `~/.local/share/tsb-tui` (replacing the previous install) and clears macOS's download quarantine flag from that folder.
- Links `tsb-tui` and `tsb-tui-check` into `~/.local/bin`, then runs `tsb-tui-check`.
- Writes `~/.config/tsb-tui/config.toml` with your TSB address (or the `--broker` value), only if that file does not exist yet; an existing file is never changed. A `config.toml` beside the previous install is carried over to the new one.

If `~/.local/bin` is not on your `PATH`, the installer says so; add it to your shell profile. Then type `tsb-tui`.

Signing and notarisation of the program come later. Until then the program clears macOS's download quarantine itself each time it starts, so a zip downloaded in a browser and unzipped in Finder runs without a "Not Opened" dialog for every library.

### Manual install (fallback)
Use this if the one-line installer cannot run. Install the release zip for your chip by hand.

#### Find the version
The latest version is listed in `latest.json` at:
`https://<tsb-host>/downloads/tsb-tui/latest.json`

Read the `version` field for `<version>` (for example, `0.5.0`).

#### Download the zip and checksum
Download using `curl` in Terminal. A zip downloaded in a browser also works: the program clears macOS's download quarantine from its own folder when it starts, so there is no "Not Opened" dialog per library.

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

#### Verify the checksum
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

#### Unzip
Extract the archive:
```bash
unzip tsb-tui-<version>-macos-<arch>.zip
cd tsb-tui-<version>-macos-<arch>
```

To run `tsb-tui` from any directory, add this folder to your `PATH` or symlink `tsb-tui` and `tsb-tui-check` into a directory on your PATH (such as `~/.local/bin` or `/usr/local/bin`).

## 3. Check it works

The one-line installer runs this check for you. To run it again (from the unzipped folder after a manual install, or anywhere after the one-line install):
```bash
./tsb-tui-check    # or: tsb-tui-check
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

After the one-line install, the broker address is already saved in `~/.config/tsb-tui/config.toml`; start the TUI with:
```bash
tsb-tui
```

After a manual install, start the TUI with the broker address:
```bash
./tsb-tui --broker https://<tsb-host>
```

To avoid typing it every time, create a `config.toml` file beside the `tsb-tui` executable (or in `~/.config/tsb-tui/config.toml`):
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

To update with the one-line installer, run it again: it replaces `~/.local/share/tsb-tui` and keeps a `config.toml` beside the program.

To update a manual install:
1. Check `https://<tsb-host>/downloads/tsb-tui/latest.json` for the new `<version>`.
2. Download the newer zip and `.sha256` for your chip with `curl`.
3. Verify the checksum with `shasum -a 256 -c`.
4. Unzip into a new folder. If you had a `config.toml` beside the previous binary, copy it into the new folder.

Your remote sessions on the broker and your settings in `~/.config/tsb-tui/` and `~/.local/state/tsb-tui/` are preserved.

## 6. Uninstall

To uninstall a one-line install, delete `~/.local/share/tsb-tui` and the two links `~/.local/bin/tsb-tui` and `~/.local/bin/tsb-tui-check`.

To uninstall a manual install:
1. Delete the unzipped `tsb-tui-<version>-macos-<arch>` folder.
2. Remove any symlinks or `PATH` entries you added.
3. (Optional) Remove stored preferences and cached state:
   ```bash
   rm -rf ~/.config/tsb-tui ~/.local/state/tsb-tui
   ```
