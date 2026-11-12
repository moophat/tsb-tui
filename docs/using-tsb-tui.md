# Using tsb-tui, start to finish

**For:** an AI agent (such as a fleet orchestrator) or a person who wants to open terminal sessions on network
gear and servers through TSB, from any Linux box.

**TSB** (Terminal Session Broker) is a server, called the **broker** or **core**. It holds a list of **targets**
(routers, switches, servers). You ask it to open a **session** (one live terminal) on a target. It records every
byte typed and shown. `tsb-tui` is the client you run. `tsb-tui drive` is its headless mode: no screen. You send
it one JSON command per line and it answers with one JSON line. An agent uses `drive`.

This guide is the short route. The full list of commands, fields and errors is `tsb_tui/drive/GUIDE.md` in the
repo. It is also printed by `tsb-tui drive --guide`, which needs no broker. When this guide and that one differ,
that one wins. Say so to the operator.

Placeholders used below: `<broker>` (the broker's web address), `<your-login>` (your TSB login), `<target>`
(a target's name or id), `<file>` (a password file).

## 1. What you need

- A Linux x86_64 box with glibc 2.29 or newer (Ubuntu 20.04, Debian 11 or newer), Python 3.12 or newer, and
  `pipx` (a tool that installs a Python program in its own private environment).
- Download access to the broker, or read access to the private GitHub repository for backup release assets.
- Network reach to `<broker>` over HTTPS. If the broker's certificate is signed by a private authority, you also
  need that authority's PEM file and pass it as `--ca-file <PEM>`.
- **A TSB login of your own.** Ask the operator for one. Do not share another person's. The audit names who typed.
- **A way to give the password to `tsb-tui` without printing it.** This is a rule, not a nicety. You may know your
  login's password, as a person knows theirs; you never print, log, type into a session or forward it, and you use it
  only through `drive`. Hand it to `drive` on a file descriptor (a numbered open file, such as `/dev/fd/N`) or in an
  owner-only file, fetched straight into that descriptor or file, never through your own output. Never put the
  password on the command line, in an environment variable or in a command: no flag or command takes one. Protecting
  that file is yours, as any credential you hold is. `drive`'s send gate (section 5) is a guardrail, not a lock.
- Know that **every byte you type is recorded**, passwords included. Never type a secret you were not given
  for that purpose.

## 2. Install

Replace `broker.example` with your broker's host (and port, if needed). The instance serves downloads without a
login; no GitHub account is needed for these commands.

**Windows 11, x64**, in PowerShell:

```powershell
irm https://broker.example/downloads/tsb-tui/install.ps1 | iex
```

This installs per user under `%LOCALAPPDATA%\Programs\tsb-tui`, adds that directory to your user PATH, and creates
`config.toml` beside the program with the broker address only if no config exists. Close a running TUI before
upgrading. Open a new terminal tab after installing, then type `tsb-tui`; existing tabs do not see the PATH change.
The bundled Python needs no separate installation. The installer runs `tsb-tui-check.cmd` once and prints its output;
a failed check is reported and the install is kept.

**Browser-download fallback** (including Windows 10): open these URLs in a browser, replacing `<version>` with the
release version (for example, `0.5.0`):

- `https://broker.example/downloads/tsb-tui/install.ps1`
- `https://broker.example/downloads/tsb-tui/v<version>/tsb-tui-<version>-windows-x64.zip`
- `https://broker.example/downloads/tsb-tui/v<version>/SHA256SUMS`

Save the zip and `SHA256SUMS` together, then run the saved installer:

```powershell
.\install.ps1 -Zip .\tsb-tui-<version>-windows-x64.zip -Broker https://broker.example
```

`-Zip` makes no network requests. With an adjacent `SHA256SUMS`, the zip's SHA-256 must match; without it, the script
warns that there is no release-hash verification and checks extraction and bundle layout only. Without `-Broker`
(or a known `-Base`), an offline install keeps any existing config and otherwise writes none; it prints the config
path and the flag to use. The one-line download requires Windows 11 because the server accepts TLS 1.3 only.

**Linux**, x86_64 with glibc 2.29+, Python 3.12+ and `pipx`:

```bash
curl -fsSL https://broker.example/downloads/tsb-tui/install.sh | bash
```

Run in a directory without release wheels to fetch the latest release. The installer checks the downloaded wheels,
Linux engine and installer against `SHA256SUMS`, installs with pipx and checks the engine. It writes
`$XDG_CONFIG_HOME/tsb-tui/config.toml` (default `~/.config/tsb-tui/config.toml`) only if a broker is known and no file
exists. Existing files, even empty ones, are kept. It needs no root and edits no shell profile. If pipx's bin directory
is absent from PATH, it prints a reminder to run `pipx ensurepath`.

For a downloaded repository copy, pass `bash install.sh --base https://broker.example/downloads/tsb-tui`;
the equivalent Windows online flag is `-Base`. An unrendered script cannot infer the download host.
On Linux, `TSB_TUI_BASE` supplies the base and `--base` overrides it; `TSB_TUI_VERSION=x.y.z` fetches that tag's
`SHA256SUMS` directly, without requesting `latest.json`. `--broker <url>` overrides `TSB_TUI_BROKER`; when fetching,
the default broker is the download base's origin. These three installer environment variables are removed before
the application is run; use `TSB_TUI_BROKER_URL` for the application's broker knob.

**macOS:** no release artifact yet; support is pending. `install.sh` refuses on macOS.

The manifest and checksum files detect corruption, not a hostile download server: they come from the same origin.
Release signing is deferred. GitHub releases remain a backup for people with access to this private repository.

For a Linux install from previously downloaded GitHub assets, keep these five files in one directory:

- `tsb_tui-<version>-py3-none-any.whl`
- `tsb_contract-<version>-py3-none-any.whl` (the contract client: the Python package that speaks TSB's wire
  protocol)
- `libghostty-vt-linux-x86_64.tar.gz` (the engine: the library that turns a session's bytes into a screen)
- `install.sh`
- `SHA256SUMS`

Then run:

    bash install.sh --broker https://broker.example

`bash install.sh` alone still installs the local assets without fetching; if no broker is supplied, it keeps any
existing config and otherwise writes none, printing the config path and `--broker` flag.

`install.sh` does these steps in order and stops at the first one that fails:

1. Checks that the machine is x86_64. The engine is built for x86_64 Linux only.
2. Checks that glibc is 2.29 or newer.
3. Picks the first of `python3.13`, `python3.12`, `python3` reporting Python 3.12+; fetches assets when no TUI wheel
   is present.
4. Requires exactly one TUI wheel and one contract wheel, then checks both wheels, the engine archive and
   `install.sh` against `SHA256SUMS` (each must have exactly one entry).
5. Runs `pipx install` on the tsb-tui wheel and `pipx inject` of the contract wheel into the same environment.
6. Unpacks the engine into that environment's `lib/tsb-tui/` and loads it once, through the TUI's own loader.
7. Runs `tsb-tui drive --help`, then prints where the program is.

It needs no root and edits no shell profile. If pipx's bin directory is not on `PATH`, it prints a reminder; run
`pipx ensurepath` yourself. pipx may fetch the TUI's own Python dependencies from the package index. Every TSB
piece comes from the release files you downloaded and checked.

To see that the install works (this needs no broker):

    tsb-tui drive --help
    tsb-tui drive --guide | head

## 3. First run

Interactive users can save the broker address once in `config.toml`:

```toml
[broker]
url = "https://<broker>"
```

Then run `tsb-tui` (Windows zip: `tsb-tui.exe`). The address order is `--broker URL`,
`--set broker.url=URL`, `TSB_TUI_BROKER_URL`, then the file. Interactive, smoke and drive use the same order.
No address means exit 2 with a message naming the config path and `TSB_TUI_BROKER_URL`.
`--ca-file PEM` remains the flag for a private certificate authority.

`--config FILE` chooses the file explicitly. Otherwise the Windows launcher sets `TSB_TUI_HOME` to its folder;
if `config.toml` exists there, that is the only file read. Otherwise the OS file is used:
`$XDG_CONFIG_HOME/tsb-tui/config.toml` (default `~/.config/tsb-tui/config.toml`) on Linux, or
`%APPDATA%\tsb-tui\config.toml` on Windows (XDG wins if set). One file, never a merge. Settings shows the
source file. An absent file is allowed, and a UTF-8 BOM from Notepad or PowerShell is accepted.

`TSB_TUI_HOME` is a launcher variable, not a knob. pipx and manual `python -m tsb_tui` starts do not set it;
no folder is guessed from the executable, Python environment or working directory. There is no `TSB_TUI_CONFIG`:
use `--config`. The TUI never writes `config.toml`. Themes and schemes remain under the OS config directory
(`themes/` and `schemes/`), and saved themes and other state remain in the OS state directory, even with a config
beside the program. Moving that program folder therefore carries its broker address, not per-user state.

The password arrives on a descriptor or an owner-only file. Whoever starts drive, the agent included, fetches it straight into that descriptor or file, never through its own output:

    tsb-tui drive --profile drive --broker https://<broker> --user <your-login> \
      --password-file /dev/fd/N --set size.auto_request=true

- `--set size.auto_request=true` is **required** to open sessions with `new`. The `drive` profile has it off,
  and a new session carries a size; with it off, `new` answers `not_available`.
- Other flags you may want: `--ca-file <PEM>`, `--size 120x40` (the headless window), `--script FILE` (run
  commands from a file instead of stdin), `--record FILE` (keep the whole exchange).
- `drive` prints one line when it is up and logged in:

      {"ev":"ready","principal":"<your-login>","size":"120x40","profile":"drive","engine":"<version>","send_gate":true,"settled_ms":800,"wait_timeout_ms":30000}

  No `ready` line means it did not start. Read stderr. Exit code 1 means a failed start or a failed step.
  Exit code 2 means bad usage.
- Every line you send is one JSON object with an `op` and optionally an `id` that comes back on the answer:
  `{"id":7,"op":"targets"}`. An answer is `{"id":7,"ok":true,...}` or
  `{"id":7,"ok":false,"error":"<code>","why":"<words>"}`. **Events** (`"ev":...`) arrive between answers.
  Blank lines and lines starting with `#` are skipped. Commands run one at a time, in order.

## 4. A worked example on a network device

Task: on a Junos switch, run two read-only commands and end the session. The device and the session id below
are examples from a real run through a staging broker; your names will differ.

### The script

Save as `task.jsonl`. One JSON object per line.

    {"id":1,"op":"targets"}
    {"id":2,"op":"new","target":"<target-id>"}
    {"id":3,"op":"wait","until":{"prompt":"[>#]\\s*$","settled_ms":true},"timeout_ms":30000}
    {"id":4,"op":"type","text":"show version | no-more\r","look":"last"}
    {"id":5,"op":"read","what":"reply"}
    {"id":6,"op":"wait","until":{"prompt":"[>#]\\s*$","settled_ms":true},"timeout_ms":30000}
    {"id":7,"op":"type","text":"show system uptime | no-more\r","look":"last"}
    {"id":8,"op":"read","what":"reply"}
    {"id":9,"op":"end","confirm":true}
    {"id":10,"op":"wait","until":{"ended":true},"timeout_ms":20000}
    {"id":11,"op":"logout"}

Run it (the launcher supplies the descriptor, as in section 3):

    tsb-tui drive --profile drive --broker https://<broker> --user <your-login> \
      --password-file /dev/fd/N --set size.auto_request=true \
      --script task.jsonl --record task.record

A script stops at the first step that fails (exit code 1), so it never types on after something went wrong.
Exit code 0 means every step passed.

### The answers, trimmed

Step 1, `targets`: the targets this login may see, one page, in id order. Each has `id`, `type`, `name`,
`group`, `tags`. `"more": true` means TSB has a next page that this command did not fetch. A query is words
like `type:ssh`, `tag:prod`, `group:core`, `source:...`; a word with no `field:` in front is `bad_request`.
`new` takes the target's `id` from this list. Put it in the script as `<target-id>`. If `new` says
`target_not_found`, check the id against the list.

Step 2, `new`. First a `gate` event, then the answer:

    {"ev":"gate","session":"s.xxxxxxxx","recording":"This session is recorded byte-perfect. Anything typed will be stored as-is."}
    {"id":2,"ok":true,"session":"s.xxxxxxxx","chip":1,"front":true,"you":"type","holder":"<your-login>","you_may_type":true,"size":"116x39"}

`you: "type"` and `you_may_type: true` mean you hold the keyboard. You opened it, so you are its **owner**.

Step 3, wait for the device's prompt. `"settled_ms":true` means "no output for 800 ms". The regex `[>#]\s*$`
matches a Junos or IOS prompt ending in `>` or `#`. The answer carries a **look** (see section 5):

    {"id":3,"ok":true,"elapsed_ms":1172,"matched":{"prompt":">"},"screen":{"lines":[..., "juniper@SW_CORELAB>"],"cursor":[5,20],"cols":116,"rows":39,"alt":false,...},"look":"L1","look_kind":"ordinary"}

Step 4, `type` with `"look":"last"` (the most recent look in this session):

    {"id":4,"ok":true,"front":"s.xxxxxxxx","queued":23,"sent":true,"settled":true,"screen":{...},"look":"L2","look_kind":"ordinary"}

`sent: true` means the bytes were handed to the network. It does **not** mean the command worked.

Step 5, `read` with `"what":"reply"`: everything printed since your last `type`, scrollback included, starting
with your typed line if the device echoed it:

    {"id":5,"ok":true,"lines":["show version | no-more","fpc0:","----...","Hostname: SW_CORELAB","Model: ex4550-32f","Junos: 15.1R7-S13", ...]}

Steps 6 to 8 repeat 3 to 5 for a second command. Why the second `type` may name `"look":"last"` again: the wait
in step 6 made a fresh look, and a look is used up by a send.

Step 9, `end` with `confirm:true`. It ends the session for everyone and cannot be undone, so without
`confirm:true` it answers `bad_request` and sends nothing. The `ended` event may come before the answer:

    {"ev":"ended","session":"s.xxxxxxxx","state":"killed"}
    {"id":9,"ok":true,"session":"s.xxxxxxxx"}

Step 10, `wait` for `{"ended":true}` answers once the end frame has reached your client. Step 11, `logout`:

    {"id":11,"ok":true,"detached":["s.xxxxxxxx"],"broker":"signed_out"}

`broker: "unconfirmed"` means TSB did not confirm the sign-out; your login there ends when it expires.

The `--record` file has the same exchange with `> ` before each command and `< ` before each answer or event.

## 5. The send gate

**The rule: a send must name a look.** A **look** is a numbered snapshot of the screen, made when you ask for the
screen: any `read` of `screen`, or a `wait` that holds a prompt, screen or reply condition. The answer has
`look` (an id such as `L1`) and `look_kind`. When you `type` or send `keys`, you name the look you read:
`"look":"L1"`, or `"look":"last"` for the session's latest. It shows you looked before you typed. The gate
refuses the send if the screen changed since. It never types for you and never repairs a mismatch: no automatic
Ctrl-C, no retry.

Every refusal sends **nothing**, answers `sent: false`, and returns the current screen and a fresh look state.
A look is used up by any send in its session, so read again before each send. In practice: `wait` for the
prompt, then `type` with `look:"last"`, then `read`, then `wait` again.

| Refusal (`error`) | Why | What to do |
|---|---|---|
| `no_look` | The look is missing, unknown, used up, or from another session or attachment | Make a new look (a `wait` on the prompt), then send again |
| `screen_changed` | Output, a keyboard move, a size change or a stale frame arrived after your look. `changed` lists the causes | Read the new screen, then decide if your command is still right |
| `unsettled` | Output is still arriving. That look permits only a brake | Wait for `settled_ms`, take a new look |
| `silent` | Nothing came back for the silence time (the look's kind is `after_silence`; the error code is `silent`). Only a brake or a bare Enter (`"\r"`) may go | Send a bare Enter, then check that a prompt came back. If still silent, send a brake or hand it to a person |
| `full_screen` | A full-screen program (vi, top) owns the screen and you did not say so | Add `"full_screen":true` to the send |
| `not_full_screen` | You said `full_screen:true` but no program owns the screen | Remove `full_screen` |
| `multi_line` | More than one line end in one send, or a tab with a line end | Send one line per `type` |
| `control_bytes` | Control bytes in the text, or a control key mixed with others | Send control keys alone with `keys` |
| `not_typist` | You do not hold the keyboard. Nothing was sent | See section 9, `takeover` |

The rest of the rules, in plain words:

- **One line per send.** `type` ends in one CR: `"show clock\r"`. Never LF, never CRLF. A control key (arrows,
  Escape, function keys, Ctrl combinations) goes alone, with `keys`: `{"op":"keys","keys":["up"],"look":"last"}`.
  Key names are listed in GUIDE.md section 5.
- **Brakes.** To stop a running command, send Ctrl-C as text: `{"op":"type","text":"\u0003","look":"last"}`.
  A brake needs no `full_screen` and is allowed even on an `unsettled` look. Some Cisco IOS commands abort only on
  Ctrl-^, which is `"\u001e"`. The device decides; check the screen after. An owner can always stop you too.
- **Full-screen programs.** If `screen.alt` is true or `screen.cursor_hidden` is true, a program (vi, top) owns
  the screen. Text sends then need `"full_screen":true`. Inside tmux the alternate screen is always on, so
  every text send there says it.
- **A wait is not a proof.** `settled` means quiet, not finished. A pager, a REPL or a silent hang can look like a
  prompt. Judge by the reply.

## 6. How to drive

**One script per task.** This is the documented path. The example in section 4 is one. Write the commands to a
file, run `drive` with `--script FILE` and `--record FILE`. The script stops at the first failure and exits 0 or
1, and the record keeps the proof. For a longer job, run one script per task. Do not keep one `drive` alive across
tasks.

Feeding a long-running `drive` through a named pipe is not documented here. It has not been proven.

## 7. First use of a device: the host key

The first time TSB connects to a device it has never seen, nobody has checked who is answering. `new` then
answers:

    {"id":2,"ok":false,"error":"not_available","why":"the host key is seen for the first time: check it out of band, then tick it in the gate (press space, then enter); the session is created","session":"s.xxxxxxxx","fingerprint":"SHA256:..."}

The fingerprint is the device's host key. **Check it out of band**: compare it with the one the device itself
shows, through another route you trust (the operator, or the device's console). Do not accept it just because it
is there. If it matches, the gate tick is `{"op":"press","keys":["space","enter"]}`.

From v0.3.0, `drive` answers this `not_available` and keeps running. In v0.2.0 it crashed here (tsb-tui issue
#12). If you run v0.2.0, a person must open such a target once in the attached TUI, check the fingerprint, tick it
and attach: `tsb-tui --broker https://<broker> --target <target>`.

## 8. Ending and cleaning up

| You send | What happens |
|---|---|
| `end` with `confirm:true` | The session is ended for **everyone**, for good. Only its owner (or someone allowed to terminate sessions) may; others get `refused` |
| `detach` | You leave. The session **keeps running** |
| `logout` | Detaches every session you have here (each keeps running) and signs your login out |
| `quit` | Stops `drive`. Every session keeps running |

A session you leave open keeps running under your login. It ends when someone ends it, or when the core's timers
do: 7 days with no bytes, or 30 days with nobody typing (defaults; TSB's session model section 11, and a target
can override them). So end what you open. Before you finish, send
`{"op":"sessions"}` and check that nothing of yours is still listed as live. Work only in sessions you opened or
were lent. `sessions` may show others on the same device; leave them alone.

If a socket was lost during `new`, a session may have opened anyway. Send `sessions` before you start again.

## 9. Errors you will meet

The full table is [GUIDE.md section 11](../tsb_tui/drive/GUIDE.md#11-errors). The ones you meet first:

| `error` | What it means | What to do |
|---|---|---|
| `no_resolving_credential` | On `new`: TSB holds no device login for your TSB login on this target | Ask the operator to map a credential to your login for that target. Do not look for or type a password |
| `target_not_found` | On `new`: no such target | Send `targets` and use an `id` from it |
| `not_available` | The action is not possible now. `why` says which: `size.auto_request` is off, or a first-use host key (section 7) | Read `why`. Set `--set size.auto_request=true`; or section 7 |
| `refused` | Your input reached TSB and TSB refused it. `reason` says why | Stop typing in that session and report the `reason` |
| `takeover` | The owner took the keyboard back during your wait (a `token` event with `reason:"takeover"` came first) | **Stop typing there.** Report what you were doing, with the `tail` in the answer. You are now a viewer. To continue, ask the owner outside TSB, then `wait` with `{"holder":"me"}`. Your other sessions are not affected |
| `timeout` | A wait ran out. The answer has `tail`, the last screen lines | Read `tail`. The command may still be running |
| `login_failed` | TSB refused the login. `why` is TSB's sentence | Check the login with the operator. Do not retry in a loop |
| `login_recording_not_acknowledged` | The deployment records device logins | Only if you accept that, repeat with `"recording_acknowledged":true` |
| `target_auth_failed` | On `new`: the target refused the login TSB used for it (a credential TSB holds for you, or the login you supplied); it is not your TSB login | Do not retry in a loop — a retry sends the same login and the target refuses it again. Report it to whoever keeps that target's credential (on staging, an issue on moophat/tsb-core), or supply a login with `new` if you have one |
| `target_connect_failed` | On `new`: the target could not be reached, or the connection failed before a session opened | Check the target's address and reachability; report to the operator |
| `hostkey_changed` | On `new`: the target's host key changed since TSB recorded it | Report to the operator. Do not accept a changed key |

The gate's refusal codes are in section 5. After a refusal, the answer carries `sent: false`: nothing went out.

Events to act on: `ended` (stop, you cannot reopen it), `removed` (stop, do not attach again by yourself),
`stale` with `you_choose` (see GUIDE.md section 10), `notice` (the session's end countdown entered its warning
window; tell whoever runs you, your typing keeps it alive). The owner is asked "Kill the session?" if your input gets no
answer for 3 minutes, so tell the operator if a command legitimately prints nothing for long.

## 10. Network gear notes

Most targets are routers, switches and consoles, not Linux.

- **No exit codes.** A network CLI reports no success number. Read the reply and judge it.
- **Prompts** end in `>` (user mode) or `#` (privileged mode, or Junos configuration mode). Wait on the prompt
  of the mode you expect. A regex such as `[>#]\s*$` matches either; use a stricter one when the mode matters,
  for example `sw-corelab\(config\)#\s*$` on IOS.
- **Pagers.** Long output stops at `--More--` and waits for a key. On Junos add `| no-more` to each command, as
  the example does, or run `set cli screen-length 0` once. On IOS use `terminal length 0`. Otherwise `wait` for
  `{"screen":"--More--"}` and send `{"op":"keys","keys":["space"],"look":"last"}`.
- **Confirmations** such as `[confirm]` or `[yes/no]` wait for you. `wait` for them with `screen`, then answer
  deliberately.
- **Stay read-only** unless the operator told you otherwise. `show` commands are safe. Configuration changes are
  recorded and are the operator's call.
- **Telnet and serial** targets have no size; `size` answers `not_resizable`. A telnet target carries one
  session per connection.
- **Asynchronous output** (syslog, console messages) can end a quiet window too early. `terminal no monitor` or
  `logging synchronous` reduce it.
- **A failed-attempt line before the banner.** Network gear may print a failed-attempt line (for example Junos's
  "Password incorrect.") before a successful login's banner, when its authentication order tries a source that
  refuses before one that accepts. TSB relays it verbatim. The prompt, not the first line, says whether you are in:
  wait for the prompt (`[>#]\s*$`), not for the absence of an error line.

## 11. Targets: add, edit, delete

A target is a device or server TSB can open a session on. You can add, change and remove targets from the TUI or from
`drive`. In the TUI this needs the `target.edit` permission on your login: without it, `n`, `e` and `d` do nothing. The core, not the client, decides what your login may see and change; when it refuses, it
says why and the client shows its message.

**In the TUI.** Open the full Fleet view (`Ctrl-\` then `F`) and press `Tab` to the Targets tab. Then:

| Key | Does |
|---|---|
| `n` | Opens the **New target** form. |
| `e` | Opens the **Edit target** form on the selected target, filled with its stored definition. The id cannot change. |
| `d` | Asks "Delete `<target>`? Open sessions stay open. This cannot be undone." Choose **Delete it** or **Keep it** (`Esc` keeps it). |

The form asks for:

- **Id** (required): the target's name in TSB.
- **Type** (required): SSH or Telnet.
- **Name** and **Group** (both optional).
- **Tags** (optional): comma-separated, none of them empty.
- **Host** (required).
- **Port** (optional): a whole number from 1 to 65535; blank means the default for the type.
- **Account** (optional, SSH only): the device user name. It is not a secret.

Press **Save target**. If the core refuses, the form stays open with everything you typed, and the core's message is
shown under it. `Esc` cancels. After a save the Targets tab selects the target.

**Only TSB's own targets can be changed.** A target that another source discovered (the Source column on the Targets
tab says which) cannot be edited or deleted here; the tab says it comes from that source. Targets you add go into TSB's
own source, `tsb`.

**Credentials are separate.** The form saves the target, never its password or key. After you save, press `l` on the
Targets tab to save a login for the target (`Space` marks several first). If you do not, TSB asks for the login when you
connect.

**In `drive`.** Three commands, fields as in [GUIDE.md section 5](../tsb_tui/drive/GUIDE.md#5-commands):

    {"op":"target_new","id":"<target-id>","type":"ssh","host":"<host>"}
    {"op":"target_edit","target":"<target-id>","group":"core","tags":["prod"]}
    {"op":"target_delete","target":"<target-id>","confirm":true}

- `target_new`: `id`, `type` (`ssh` or `telnet`) and `host` are required; `name`, `group`, `tags` (a list), `port` and
  `user` (SSH only) are optional. Here `id` is the new target's id.
- `target_edit`: `target` is the id; every other field is optional and an absent field keeps its value. `null` clears
  `name`, `group`, `tags`, `port` or `user`; `null` for `type` or `host` is `bad_request`.
- `target_delete`: `target` and `confirm: true`. Without `confirm: true` the answer is `bad_request` and nothing is
  sent.
- Each answers with the `target` record the core returned, or with the core's error code and message. Drive does not
  save credentials: that is the TUI's `l`.

## 12. Upgrading

Find the version you run:

- In the TUI, open the Menu and choose About. It prints `tsb-tui <version>`, the contract client and the engine.
- On Windows, run `tsb-tui-check.cmd`. Its last line is `TUI: <version>`.
- On Linux, the file you installed from is `tsb_tui-<version>-py3-none-any.whl`.

`tsb-tui` has no `--version` flag.

Find the latest release on the [releases page](https://github.com/moophat/tsb-tui/releases), or from a shell:

    gh release view --repo moophat/tsb-tui

Upgrading is installing the newer release the same way as the first time.

- **Linux.** Make a new empty directory. Download the five files of section 2 from the newer release into it and run
  `bash install.sh` there. It installs with `pipx install --force`, so the old version is replaced; every check in
  section 2 runs again.
- **Windows.** Extract the newer `tsb-tui-<version>-windows-x64.zip` and run `tsb-tui-check.cmd`, then `tsb-tui` as
  before.

Your config file is not touched. When extracting a new Windows zip into a different folder, copy your
beside-program `config.toml` there yourself if you want to keep using it.

Downgrading to a version before `broker.url` was added refuses that key as unknown. Remove it and pass
`--broker URL` when using that older version.


## Update notices

Release builds check for updates after the first screen is usable. When behind, a toast points to
Settings… for the full version and platform-specific command, which you can select and copy. Run
that command yourself to update; the TUI never installs or replaces anything. The notice appears
at every start while behind, including starts that reuse the cached version.

By default no request goes anywhere but your broker's host: the check reads
`<broker origin>/downloads/tsb-tui/latest.json`. It sends only the running version in `User-Agent`,
no authorization or cookies, verifies TLS with the app's CA file, refuses redirects, reads at most
64 KiB and waits at most 1.5 seconds. A user-set `update.url` can select another manifest host.
HTTP is accepted only for loopback. Missing downloads (404), malformed answers and other failures
are silent. A failed refresh retains any previously known update.

| Knob | Default | Meaning |
|---|---|---|
| `update.check` | `true` | Enable the check and notices; false makes no request or cache write |
| `update.interval_h` | `24` | Minimum hours between requests, at least 1 |
| `update.url` | empty | Override the manifest URL; otherwise use the broker origin |

Disable with `TSB_TUI_UPDATE_CHECK=0`, `--set update.check=false`, or a config file's `[update]`
table containing `check = false`. Environment names for the other knobs are
`TSB_TUI_UPDATE_INTERVAL_H` and `TSB_TUI_UPDATE_URL`. `drive` and `harness` profiles disable the
feature; `phone` keeps it enabled. Development version `0.0.0` makes no check.

The cache is `update-check.json` in the same per-user state directory as `theme.toml`, never beside
the portable program. It records the attempt time even on failure, so a broken host is not retried
before the interval. With no known release the cached latest value is null. A successful answer
replaces the known version, including an equal or older version. If you may downgrade, use `--set`
or environment variables for these knobs: older versions reject config files containing unknown
`[update]` keys.
