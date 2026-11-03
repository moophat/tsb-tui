# tui — U7

A first-party shipped surface and the core's verification instrument — one artifact, both roles, no conflict (`Core_value` § Surface Philosophy ships it alongside web and desktop; `Desktop_Renderer` §4 gives it the same coverage at days not weeks). Python + Textual. Imports only the published contract client (CI lint), so what ships is a genuine API consumer and the smoke test for every deploy.

Ownership, seams and exit evidence: `../tsb-program/spine/plan.md` §2 (U7). A sub building this unit writes only inside `tui/`.

The flows it drives, as any consumer does, are the TSB protocols on edge [SF-CO](../program/contract/protocols/edges/SF-CO.md) ([`../program/contract/protocols/`](../program/contract/protocols/README.md)): what a surface must and may do is in each protocol's consumer lines.

## Layout

    tsb_tui/app.py              the Textual App: one client, a stack of screens
    tsb_tui/config.py           the knobs: packaged defaults, a profile, the TOML file, `TSB_TUI_*` and `--set`
    tsb_tui/config_default.toml every knob and its default (a knob no code reads is refused at load)
    tsb_tui/theme_files.py      the two catalogues: chrome theme TOML files and terminal colour scheme JSON
    tsb_tui/themes/             the packaged chrome themes (terracotta, paper, contrast, nord, dracula), one
                                `[theme]` table of roles per file
    tsb_tui/schemes/            the packaged terminal colour schemes, one JSON per file, and THIRD_PARTY_SCHEMES.md
                                (author, repository, commit, path, SPDX, licence text for each shipped scheme)
    tsb_tui/_transport.py       the transport exceptions the published client re-exports (v0.2)
    tsb_tui/screens/login.py    TSB's own login (decision 5.34): username + password
    tsb_tui/screens/new_session.py   target id, connection choice; the size a session is created with
    tsb_tui/screens/attach_confirm.py  the gate: the recording text, a first-use host key and its tick
    tsb_tui/screens/terminal.py      the workspace: the rail, the sidebar, every session open here (one
                                     attachment, engine and canvas each), keys -> bytes, resize, the status
                                     row, Take back, the end of a session
    tsb_tui/screens/whos_here.py, size_dialog.py   Who's here (hand off, take, put aside, remove) and Size
    tsb_tui/widgets/rail.py          the four-column rail: Menu, Fleet, need-you and alert counts, one chip per
                                     session, new session
    tsb_tui/widgets/sidebar.py       the rail widened in place: groups, filter, mine|all, details
    tsb_tui/widgets/canvas.py        the grid painted at the session's size, panned over by the window
    tsb_tui/widgets/status_row.py    the one status row: the pill, the state in words, clickable hints
    tsb_tui/glyphs.py                the Unicode glyph set and its ASCII fallback (`glyphs.set`)
    tsb_tui/screens/prompts.py       the stale choice, the quiet prompt, operator alerts, the end dialog
    tsb_tui/model/                   pure state, no Textual: `attachment.py` (every C2 frame and close),
                                     `actions.py` (the one action table), `fleet.py`, `alerts.py`, `countdown.py`
    tsb_tui/screens/fleet_view.py    the full-screen Fleet view: Sessions and Alerts tabs, Needs you, the table
    tsb_tui/screens/chrome_dialogs.py  End session…'s question, the event log, Settings…, All keys, About
    tsb_tui/widgets/mouse.py         what `mouse.enabled` and `mouse.tooltips` mean for the chrome
    tsb_tui/term/backend.py     libghostty-vt screen state, fed by the attached byte stream (Contract
                                §11: the grid is the broker's; this client owns all rendering)
    tsb_tui/term/ghostty.py     the `ctypes` binding over the pinned `libghostty-vt.so`, checked against the
                                library's own ABI manifest at load; `term/keys.py` the key, paste, mouse and
                                focus encoders
    tsb_tui/drive/              `tsb-tui drive`: commands, waits, events, the runner; `GUIDE.md` is the guide
                                an agent reads (`tsb-tui drive --guide`)
    tsb_tui/smoke.py            the non-interactive path the deploy hook drives after a deploy
    tsb_tui/__main__.py         `tsb-tui`'s entry point
    bench/                      the benchmarks the orchestrator runs on the integration node (below); `bench/README.md`
    tests/                      the suite; the shapes it stands on are the contract's (`program/contract/`)
    tests/stub_server.py        a minimal, slow, chunked C1/C2 stub (test scaffolding, not a
                                second fixture server) — built on `tsbc._wsframe`, the same codec
                                `tsb_contract_client` uses
    tests/test_consumer_musts.py     the table of the consumer MUSTs and the test that proves each line
    tests/test_discoverability.py, test_input_fidelity.py, test_size_and_faithful.py, test_snapshots.py
                                     the design's acceptance tests (§3.4): every way in reachable by a click,
                                     what is typed is what the session gets, the grid is the session's, the
                                     screens drawn at 65x30, 120x40 and 200x60
    tests/test_bench_smoke.py        every bench script, run tiny, so none can rot

## Run

Interactive, from any node with network access to the core:

    tsb-tui --broker https://<host> [--ca-file PEM] [--target ID]

From a phone: SSH to the integration node, then `zellij`, then the command above inside it.

Non-interactive smoke path (what the deploy hook runs after every deploy, alongside
`harness/rigs/smoke.sh` — `manage.sh verify` runs only the latter, never the TUI):

    TSB_SUPERADMIN_USER=<user> TSB_SUPERADMIN_PASSWORD_FILE=<file> \
      tsb-tui --smoke --broker https://<host> --target ID [--ca-file PEM]

Exits 0 on a full pass (login, create, attach, one round trip, close), 1 otherwise; one status
line per step, including a failed close (a leaked session is worse than a loud exit code).

On the terminal screen every key reaches the session (F9, Esc, Tab, Ctrl-P, Ctrl-C and Ctrl-Q are the
session's), re-encoded for the session's own modes by libghostty's key encoder. A client that only
watches (a `session.join.ro` login) sends none of its typing; the owner's typing always goes out and
takes the keyboard. The one key the TUI takes is the leader, Ctrl-\\ (`keys.leader`): `^\` then a letter
runs an action (`^\ d` detach, `^\ w` Who's here, `^\ r` Size, `^\ R` auto size on or off, `^\ f` the
fleet sidebar, `^\ 1`-`^\ 9` the session on that chip, `^\ n` and `^\ p` the next and previous, `^\ s` the
sidebar's keys, `^\ t` Take back, `^\ c` New session, `^\ x` End…, `^\ F` the full view, `^\ e` the event log,
`^\ v` Pan (a quick look: with `view.follow_cursor = auto` the next output or keystroke returns the window to the cursor), `^\ ?` All keys, `^\ :q` Quit), `^\` alone opens the Menu, and `^\ ^\` sends Ctrl-\\ to the session. The status row at
the bottom (`status.position`) says who types and lists what can be done now; each hint, the keyboard
pill and `≡ Menu` take a click. The Menu is the complete list of actions, searchable. Letters are
rebound with `[keys.leader_map]` in the config file, by action name.

## tsb-tui drive: an agent, or the harness, uses the TUI as a person does

For an SSH target, New session offers the stored login, username/password fields, or **Type the login in the terminal**.
The last opens a pane before creating anything: TSB asks `login as:`, confirms a first-use host key, then shows the
device's own questions as plain text inside a frame marked **device login**. A password round uses TSB's own
`Password:` outside that frame. Esc cancels. Recording login inputs requires consent before the user name is asked.
On success the same socket becomes the session; its recording gate still precedes all session input.

**Remember** is off by default. It saves a successful password-only login into the personal vault and maps the target
to it, through the existing vault save flow. A keyboard-interactive question removes the offer and drops any candidate;
codes are never saved. The screen lock hides the questions and blocks answers, while authentication continues. A result
and any save wait for the password re-check; sign-out drops the candidate. The session menu also offers **New session on
this connection**, which explicitly reuses its connection without another login. That entry has no shortcut.

    tsb-tui drive --guide                       # print the guide for agents; needs no broker
    tsb-tui drive --profile drive --broker https://<host> --user <login> --password-file <file>
    tsb-tui drive --profile harness --broker https://<host> --script steps.jsonl

`drive` runs the same app headless: one JSON command per line on stdin (or `--script`), one JSON answer or event per
line on stdout. Its commands (`login`, `sessions`, `attach`, `switch`, `read`, `type`, `keys`, `wait`, `hand`,
`hand_back`, `answer`, `size`, `detach`, `snapshot`, `click`, `press`, `resize`, `quit`) each go through the action a
person would fire, the owner's brake works headless (a take-over fails a pending wait within 100 ms of the `token`
frame), and the input guard (`input.guard`, Frontend §9) refuses a viewer's `type` locally. **The guide is the
reference**: `tsb_tui/drive/GUIDE.md`, printed by `--guide` and listed by `--help`. It is the harness's scenario
language too (`harness/` rigs drive the TUI through it).

Two profiles are for it. `drive` acts for the agent on nothing (no automatic size requests, no mouse). `harness` has the
mouse on and counts output; its `input.guard` is on by default like every profile (D52 was ruled on 2026-10-12), so a
rig that wants to test the core's own refusal switches it off with `--set input.guard=false`. `--smoke` stays the
non-drive smoke path it was: drive has no command to create or end a session, which a smoke run needs.

## What reaches the session, and what does not

Every key reaches the session except the leader (Ctrl-\\), re-encoded for the session's modes by libghostty's key
encoder (DECCKM arrows, application keypad, bracketed paste, the kitty keyboard flags). The suite
(`tests/test_input_fidelity.py`) holds three things: the leader's letters, the sidebar's keys and `?`, `:` and `|` are
the session's when they do not follow the leader; the same keystrokes produce the legacy bytes with the kitty protocol
off and CSI-u with it on, and back; and a corpus of what an xterm sends, parsed by Textual and encoded as the screen
does, comes out byte for byte the same. What does not, recorded in that file and here:

* **Ctrl-H** arrives as Backspace (0x7f): Textual reads both as `backspace`.
* **Alt-Enter** arrives as Enter and **Alt-Backspace** as Ctrl-W: Textual drops the Alt of the first and reads the
  second as another key. **Esc b** and **Esc f** (readline's and IOS's word moves) arrive as Ctrl-Left and Ctrl-Right.
* **VT220 Home and End** (`ESC [1~`, `ESC [4~`) and the SS3 Home arrive as the xterm forms.
* A session that asks for **every key as an escape code** (kitty flag 8) gets a shifted letter as `CSI 65 u`, not
  `CSI 97;2 u`: Textual's key event carries the key's text, not its unshifted codepoint. The usual "disambiguate" flag is
  unaffected, since text keys are plain text there.
* Without the kitty protocol on the *outer* terminal, Textual loses Alt on some keys (its issue #6663).

The phone path (an SSH app, then zellij, then the TUI on the integration node; taps and the wheel) is a manual check
the tests cannot make.

## Benchmarks

`bench/` holds the three measurements the plan's merge gate needs, run by the orchestrator on the integration node and
nowhere else, while no post-merge loop is running: they are not rigs (no target, no sshd; each starts the contract's
fixture server as a child process), but they load a machine. `bench/engine_read.py` is the engine-read go/no-go,
`bench/paint.py` keystroke-to-screen under a firehose (p95 under 50 ms at 116x39 and 196x59, every WebSocket ping
answered), `bench/fleet_scale.py` a 500-session fleet (filtering in under 100 ms, the poll never blocking typing). Each
has `--tiny`, which `tests/test_bench_smoke.py` runs in CI. The exact commands and what each number means are in
`bench/README.md`.

## The rail, the sidebar and several sessions

Login lands on the workspace, not on a form: a four-column rail on the left (`rail.side`, `rail.width`), the
canvas of the session in front, and one status row. With no session open the sidebar starts open
(`sidebar.open_when_no_session`).

* **The rail**, top to bottom: `≡` the Menu, `▦▸` the fleet sidebar, `▲n` how many sessions need you, `!n` TSB's
  alerts (only for a `broker.health` login), then one chip per session open here — numbered, then who types
  (`●` you, `◆` another login, `·` nobody, `▲` needs you, `?` asks you, `◌` unreachable, `✕` ended), then `◂` for
  the one in front or `•` for output you have not looked at — and `+` for a new session. Every row is one click.
* **The sidebar** is the rail widened in place to 32 columns (30 on a phone, 42 from 160 columns), beside the
  canvas, never over it. Groups: Needs you, Open here, Yours someone else types, Yours and joinable; `/`
  filters, `mine│all` changes what is listed, `⏎` opens a row, `t` takes the keyboard back, `Esc` or `^\ f`
  closes it. **Opening or closing it never asks any session for a size**: the canvas pans (`cols 1-88 of 116 ▸`).
  The fleet is read every `fleet_view.refresh_s` seconds while it is on screen, on a thread of its own.
* **Several sessions.** Each one open here is attached, parsed and kept; only the one in front is painted, and a
  chip shows `•` when output arrived in another. Each attachment keeps its own state: a session you only watch
  never answers a terminal query.
* **Take back.** `t` on a sidebar row (or `^\ t`, the Menu, the quiet prompt's button) brings the session to the
  front, attaches first when needed (the gate shows first), then sends `handoff` to this client's own id. The
  status row says whose keyboard it took, and a toast says how to give it back.
* **New session** is a dialog over the workspace, on the target picked in the Targets tab, `--target` or typed. It is created with the canvas's size (the window minus the rail
  and the status row); auto size is on by default (`size.auto_request = true`, OPEN-1 ruled 2026-10-12). Turn it off
  and the dialog has no size to carry and says so, creating nothing (a guessed size would be a request the operator
  never made).
* **The full Fleet view** (`full view ▸` in the sidebar, `^\ F`, the Menu) covers the canvas with the rail still
  beside it: tabs Sessions, Targets and Alerts (Alerts only for a `broker.health` login), Needs you first, then a table of
  the sessions you own or may join (target, id, type, state, keys, open, clients, owner, ends in, REC), a `/`
  filter, `mine│joinable`, "show ended" and `fleet_view.group_by`. `⏎` opens a row, `c` New session, `t` Take
  back, `w` Who's here, `x` End…, `Tab` the next tab, `Esc` back to the session it came from (and to the sidebar
  it was opened from). `Ctrl-Q` quits from here.
* **The Targets tab** lists the inventory the core shows this login (name, type, group, tags, source, the sessions
  you see there, and `attach` where one is live), a page at a time (`Targets 100+` until the end is reached), with the
  selected target's host, port, source and last-seen under the table. `/` takes a query the broker answers
  (`type:ssh tag:prod group:core source:nmaa`; `tag:` can be repeated); a word with no `field:` is refused with the
  list of fields. `⏎` opens New session on the target, asking `connection: new` for a telnet target (a telnet
  connection carries one session, so reuse is greyed there). Every string from a target record is drawn as text:
  markup stays literal and a control character (an escape included) shows as its Unicode control picture. A
  New session on a typed id reads `GET /targets/{id}` first, for its type. `tsb-tui drive` has the same two reads as
  commands, `targets` and `new` (`tsb-tui drive --guide`).
* **Mouse and touch.** A single click or tap acts on every piece of chrome: the rail, the sidebar, the status row's
  hints (the full view's and Pan's too) and the dialogs' buttons. The wheel moves through the sidebar and the
  view; over the canvas it goes to the session only when the session asked for the mouse and this client holds
  the keyboard, and otherwise scrolls this client's own window over the grid. Hovering a glyph says its word and
  key. `mouse.enabled = false` turns it all off and asks the terminal for no mouse; `mouse.tooltips = false`
  turns the tooltips off.
* **View, settings and help** (the Menu's last groups): Event log (this client's own record of what the session's
  frames told it), Pan (`^\ v`, then the arrow keys by `view.pan_step`, `Esc` to stop; a quick look, since with `view.follow_cursor = auto` the next output or keystroke returns the window to the cursor), Settings… (every knob in
  force and which layer its value came from), All keys (`^\ ?`, written from the action table), About (the TUI,
  the contract client, the engine). The Inspector and Zoom are left out of E2 (`view.inspector` accepts only its default).
* **Search in the scroll view** (`^\ [` opens the view): `/` asks for text and Enter finds the nearest older match
  from the top row on view; `n` and `N` step to the next older and newer match, with no wrap-around, and the note in
  the status row says "no older match", "no newer match" or "not found: …". The query is a literal string, and a
  capital letter in it makes it case-sensitive. It is searched in the rows the TUI's engine holds, one screen row at a
  time, so **a match broken across a wrapped row is not found**. The current match is `scroll.current_match_style`
  (`reverse bold`) and the others on view are `scroll.match_style` (`underline`). New output drops the marks; `n` or
  `N` finds them again in what is held by then.
* **Copying** (from the scroll view; `^\ [` opens it). `v` starts selecting whole lines at the top row on view, or at
  the current match when one is shown; `v` again takes the selection off. `↑ ↓` and `PgUp PgDn` move its loose end,
  and `y` or `Enter` sends the selected lines to your terminal's clipboard as one OSC 52 write; the selection stays until
  `Esc`. With the mouse on, a drag over the canvas selects the lines it covers and copies on release (a click that does
  not move does nothing). The write is blind: the terminal does not say whether it took it, so the toast says "Sent",
  not "Copied". **Your terminal must allow OSC 52**, and inside tmux set `set -g set-clipboard on`; zellij and other
  multiplexers differ. The lines are read again at copy, and if the history moved under the selection nothing is
  sent. A text over `copy.max_bytes` (100000) is refused, never cut; `copy.join_wrapped` joins a row that wrapped into
  the next selected row; `copy.osc52 = false` stops every write. The selection is `scroll.selection_style`
  (`reverse`). **Without any of this**, turn the mouse off (`mouse.enabled = false`) or hold Shift while dragging, and
  the terminal's own selection works as it always did. Nothing a session sends can write to your clipboard.
* **End session…** (`^\ x`, the Menu, `x` on a sidebar or view row) asks once, naming the session, then sends
  `DELETE /sessions/{id}`; a login that is neither the owner nor an admin is refused by the core, and the TUI says so.
* **Quit** (`^\ :q`, the Menu, `Ctrl-Q` in a full view) leaves every session running and prints which.
* **The vault, in untrusted mode only** (E4): nothing about it is written on this host, the TUI never registers a device or
  sends a challenge, and every passphrase prompt says that typed keys may be recorded when the TUI runs inside a TSB
  session. A New session the core answers `vault_locked` asks the passphrase, unlocks and repeats once; an unset vault is
  offered init, with the warning that a forgotten passphrase cannot be recovered. **Credentials…** (`^\ C`, the Menu)
  lists your own references and kinds, saves a login (a username with a password, or an unencrypted key pasted into a masked
  field or read once from a file of at most 16 KiB), maps credentials to targets and deletes them (which clears their
  mappings); on the Targets tab `Space` marks targets, `l` saves a login for the marked or selected ones and `p` maps a
  credential to them. **Devices…** (`^\ V`) lists your trusted devices and revokes one. The one function login-at-connect
  calls to save a login is `tsb_tui.vault_flow.save_login`.
* **The screen lock** covers every screen after the screen-lock time the core serves (`GET /auth/me`, never a local value)
  with no input; the sessions stay attached and keep running behind it. The login password resumes it (checked by the core;
  a wrong one leaves it locked). **Lock now** (`^\ L`, the Menu) purges your vault unlocks and locks this screen. While you
  type, the TUI tells the core you are active, at most once per the interval the core serves.
* **Knobs:** `glyphs.set` (`ascii` for a terminal without the glyphs), `view.chrome` (`compact` is the phone's),
  `rail.*`, `sidebar.*`, `watch.take_back_brings_to_front`, `fleet_view.*`, `mouse.*`, `view.pan_step`,
  `motion.level`.

## What the screen tells you

Every state a session's frames put it in is on screen, never only in a log:

* **The engine.** The grid is drawn by libghostty-vt at the session's own size; the window pans over it. The
  library is found at `term.library`, else `<sys.prefix>/lib/tsb-tui/libghostty-vt.so`, where the deploy hook
  installs it; start-up fails naming both when it is missing.
* **The status row** says who types and the session's state: a stale screen (the contract's notice line, and
  who is choosing), a lost data path, a refused input and why, and a countdown when the session is inside a
  warning window ("ends in 4:12 unless it answers", from the core's own `ends_at`). While a session is being
  reattached it says which attempt is next and in how many seconds; Esc stops it.
* **A stale screen** puts a dialog over the session for the keyboard holder: the contract's own texts, a
  button each (or 1-4), and keys are ignored for the first second. Choosing "Start a fresh session" opens New
  session on the same target and leaves the old session running.
* **A quiet prompt** ("Kill the session?") is a box that never takes a key: Keep it, Kill it… (asks once
  more), or Take back when someone else types. `^\ a` opens the same two answers for the keyboard.
* **An ended session** keeps its last screen, dimmed, with a dialog saying which end it was; `c` starts a new
  session on the same target, `n` goes to the next session open here and `^\ f` opens the sidebar. Its chip
  shows `✕` until you move on to another session.
* **Operator alerts** pop up for a login that holds `broker.health` only; a 403 hides everything alert-related.
* **Before the first attach to a session** in a run, a gate shows the recording text and, for a first-use host
  key, the fingerprint and a tick. Later attaches show the recording text in the status row for
  `gates.recording_bar_s`; `gates.recording = "every-attach"` shows the gate every time.
* **Settings.** Profiles (`default`, `phone`, `drive`, `harness`), the config file, `TSB_TUI_*` and
  `--set key=value`, in that order of precedence. `broker.timeout_s` (default 70 s) is how long every REST call to
  the core may take, `POST /sessions` included. `size.auto_request` defaults on (OPEN-1 was ruled on 2026-10-12:
  a TUI's auto size is the one that tells the session what fits the window); the phone, drive and harness profiles
  keep it off. `input.guard` is on in every profile too (D52, same day).

## Themes and colours

Two independent picks (ruling 2 — a theme never derives a scheme, nor a scheme a theme):

* **`theme.name`** picks the chrome — the rail, the sidebar, the status row and the dialogs. Packaged ids:
  `terracotta` (the default), `paper`, `contrast`, `nord`, `dracula`.
* **`theme.term.scheme`** picks the session's colours. The list is `tsb_tui/schemes/` (one JSON per scheme;
  `THIRD_PARTY_SCHEMES.md` names each one's author, repository, commit, path, SPDX and licence). `tsb-default`
  is TSB's own palette.

To pick a look: `--set theme.name=nord --set theme.term.scheme=nord`, the env `TSB_TUI_THEME_NAME` and
`TSB_TUI_THEME_TERM_SCHEME`, or the two keys in `config.toml`. They are independent: one does not move the other.

Your own files: `$XDG_CONFIG_HOME/tsb-tui/themes/<id>.toml` (one `[theme]` table of roles; a role the file
leaves out comes from the packaged defaults, terracotta) and `$XDG_CONFIG_HOME/tsb-tui/schemes/<slug>.json`
(the same shape as `tsb_tui/schemes/tsb-default.json`). A user file named like a packaged one replaces it.

The picker (`Menu ▸ View ▸ Theme…` or leader + `T`) previews a look live. `⏎` uses it for this run; `s`
(Save as my default) keeps it for this TSB login. Save writes `$XDG_STATE_HOME/tsb-tui/theme.toml`
(default `~/.local/state/tsb-tui/theme.toml`) — one `["<login>"]` table per TSB login, each with `theme` and
`scheme`. The next start, after login, applies the entry for that login only; another login on the same
machine keeps its own. Only Save writes the file, and only the interactive TUI: `tsb-tui drive` never reads
or writes it. If two TUIs save at once, the last one wins.

Precedence (TT12):

* names: packaged default < profile < saved for your login (after login, interactive only) < config file <
  `TSB_TUI_*` < `--set`;
* roles: packaged default < the named theme file < profile < config file < env < `--set`; a partial theme takes
  its missing roles from the packaged defaults.

`theme.name` used to be only a label. A value that names no theme now stops start-up, listing the themes found
in both folders (R6). The scheme's colours are overridable: `theme.term.fg`, `theme.term.colors` (16 hex) and
`theme.term.bg` override the scheme, and `theme.term.cursor_colour` (`role` keeps TSB's accent/steel, `scheme`
takes the scheme's cursor).

## Install

`tui/pyproject.toml` declares `[project.scripts] tsb-tui`, so `pipx install ./tui` gives a working
`tsb-tui` script — except the contract client, which PEP 508 cannot name by a relative path (see
the comment in `pyproject.toml`). `program/` is a submodule with `update = none`, so this copy
needs `program/` populated by `make contract-init` from the repo root first. Inject it from a
throwaway copy of `program/contract/`, never in place — building writes `build/` and
`*.egg-info` into its source — and:

    make contract-init   # from the repo root: populates program/ at the pinned release
    cp -r program/contract /tmp/contract-$$   # throwaway; pip then builds inside the copy
    pipx inject tsb-tui /tmp/contract-$$

`deploy/manage.sh up`'s post-deploy hook does exactly this, for the operator who ran `sudo`: it
installs `tsb-tui`, injects a throwaway copy of `program/contract/` (so pip's own `build/`
and `*.egg-info` never land in the program submodule), then runs
`harness/rigs/smoke.sh` and `tsb-tui --smoke` against the instance it just brought up.

## Tests

    pip install -e ".[test]"   # from tui/; tests/conftest.py puts program/contract/ and
                                 # program/contract/client/ first on sys.path and imports the
                                 # contract client from there — no separate contract install needed
    pytest tests/

`tests/conftest.py` needs `program/contract/` populated (`make contract-init`) and the engine at
`vt/third_party/libghostty-vt/lib/libghostty-vt.so` (the H1 build, untracked), and fails loudly on either rather than
skipping silently. No test is skipped (the harness's rule, `harness/README.md` "No skips"). Every test talks to the contract's in-process
fixture server (`tsbc._serve`) or to `tests/stub_server.py`, never to a real sshd, a node or the workstation.

* **The consumer MUSTs.** `tests/test_consumer_musts.py` holds the table of the plan's consumer MUSTs with the test that
  proves each line, and a test that fails when a named test no longer exists. No contract fixture grades a consumer, so
  this table is the evidence until one does.
* **The snapshots.** `tests/test_snapshots.py` builds T1, S1, F5, F7, F12's states and A2 of the design's mockups at 65x30,
  120x40 and 200x60, asserts their content, and writes each as text and SVG into `$TSB_TUI_SNAPSHOT_DIR` (a temporary
  directory when it is not set) for a person to open against the mockups.
* **The pilot tests** (`tests/test_flow_pilot.py` and most of the rest) run the real app under Textual's test runner;
  waits are on a condition, never on time, and each docstring says why the behaviour matters and cites the clause.
