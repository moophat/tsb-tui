# Installing tsb-tui on Linux

## 1. What you need

`<tsb-host>` below is the address of your TSB instance; your administrator gives it to you with your login.

- **OS & C library:** Linux x86_64 with glibc 2.29 or newer (e.g. Ubuntu 20.04+, Debian 11+).
- **Architecture:** `x86_64` (the screen engine is built for 64-bit x86 Linux only).
- **Python & tools:** Python 3.12 or newer, and `pipx`. Also `curl` (or `wget`).
- **Network:** Access to the company LAN (required to reach the download server and broker).
- **Account:** A TSB username and temporary password from your administrator.

## 2. Install

### Recommended: One-line installer
Run in an empty directory (or any directory without local `.whl` files):
```bash
curl -fsSL https://<tsb-host>/downloads/tsb-tui/install.sh | bash
```

This version-independent command:
- Reads `latest.json` to find the newest release.
- Fetches `SHA256SUMS` and downloads the wheels, Linux engine, and `install.sh`.
- Verifies all SHA-256 checksums strictly.
- Installs `tsb-tui` and injects `tsb-contract` into a dedicated virtual environment with `pipx`.
- Unpacks and checks the screen engine (`libghostty-vt.so`).
- Writes default configuration to `$XDG_CONFIG_HOME/tsb-tui/config.toml` (default `~/.config/tsb-tui/config.toml`).

*Note:* If pipx's bin directory is not on your `PATH`, run `pipx ensurepath` and restart your terminal.

### Manual / offline install
1. Look up `<version>` from `https://<tsb-host>/downloads/tsb-tui/latest.json`.
2. Download these five release files into an empty directory from `https://<tsb-host>/downloads/tsb-tui/v<version>/`:
   - `tsb_tui-<version>-py3-none-any.whl`
   - `tsb_contract-<version>-py3-none-any.whl`
   - `libghostty-vt-linux-x86_64.tar.gz`
   - `install.sh`
   - `SHA256SUMS`
3. Run:
   ```bash
   bash install.sh --broker https://<tsb-host>
   ```

## 3. Check it works

Verify that the CLI is installed and shows the version:
```bash
tsb-tui --version
```
(Prints `tsb-tui <version>`).

You can also run:
```bash
tsb-tui drive --help
```

## 4. First run

If the installer wrote `config.toml`, simply run:
```bash
tsb-tui
```

If no configuration was written, pass the broker explicitly:
```bash
tsb-tui --broker https://<tsb-host>
```

Or write `~/.config/tsb-tui/config.toml`:
```toml
[broker]
url = "https://<tsb-host>"
```

### Sign in and change password
1. Sign in with the username and temporary password provided by your administrator.
2. At first sign-in, change your password: press `F1` or `Ctrl-\` to open the Menu, then choose **Change my password…**.

## 5. Update

The TUI checks for updates in the background on startup. A toast notification points to **Settings…** when an update is available.

To update:
Run the one-line installer again in a directory without `.whl` files:
```bash
curl -fsSL https://<tsb-host>/downloads/tsb-tui/install.sh | bash
```
The installer uses `pipx install --force`, which upgrades the package in place. Your existing `config.toml` and saved state in `~/.local/state/tsb-tui/` are preserved.

## 6. Uninstall

To uninstall `tsb-tui`:
```bash
pipx uninstall tsb-tui
```

To remove configuration and state files:
```bash
rm -rf ~/.config/tsb-tui ~/.local/state/tsb-tui
```
