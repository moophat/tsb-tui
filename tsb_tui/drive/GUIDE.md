# tsb-tui drive: a guide for agents

**For:** an agent that works in terminal sessions through TSB · **Companion to:** the TUI design §2.9 · **Status:** built at E2. This file ships with the program and is what `tsb-tui drive --guide` prints; it says what drive does, and nothing it does not.

`tsb-tui drive` runs the TSB terminal client without a screen. You send it one JSON command per line on stdin, and it writes answers and events, one JSON object per line, on stdout. You see each session exactly as a person's TUI would show it, and you act on it through the same buttons and keys.

## 1. What you have, and what you do not

- **Your own TSB login,** supplied by whoever runs drive, and no password for that login available to you. You hold no fleet credentials. `new` opens a session on a target only where TSB resolves a credential for your login; otherwise it answers `no_resolving_credential`.
- **Sessions someone lends you.** A person (the owner) opens a session to a router, a server or a k8s node with their own credentials and lets you work in it. You never see their password or key.
- **The keyboard, only when it is handed to you.** One client types at a time. You start as a viewer, and the owner hands you the keyboard, or you take it from no one when nobody holds it.
- **The owner's brake.** The owner can take the keyboard back at any moment, even mid-command. From that instant another login’s input is refused. An owner’s own client can take it back by typing, subject to the send gate, or with `take` without typing.
- **A full record.** Every byte you type is recorded verbatim, passwords included, and the audit says which client held the keyboard and when. Never type a secret you were not given for that purpose. A login you supply on `new` goes into SSH's authentication for that one connection and is never stored; it is recorded only if the deployment's recording knob says so, and then only after you acknowledge it (`recording_acknowledged`, in the `new` row).

## 2. Starting

The send gate is a **guardrail**, not a control. Its deployment precondition is that something other than the agent
starts drive and hands it the TSB password on a descriptor, and the agent can read neither that password nor a file
containing it. The `login` command's password path is subject to the same precondition.


```text
tsb-tui drive --profile drive --broker https://<broker> --user <your-login> --password-file /dev/fd/N
```

| Flag | Meaning |
|---|---|
| `--broker URL` | the core's address; optional: it is the `broker.url` knob in the config file or `TSB_TUI_BROKER_URL` when the flag is absent, and the flag wins. Never needed for `--guide` or `--help` |
| `--profile drive` | the default: acts for you on nothing (no automatic size requests, no automatic answers); the mouse is off, so `click` is not available |
| `--profile harness` | for test rigs: the mouse is on and output counts are written; `input.guard` is on by default here too (D52 was ruled on 2026-10-12), so a rig that wants to test the core's own refusal switches it off with `--set input.guard=false` |
| `--user`, `--password-file PATH` | log in at start. The password comes only from a file, or from stdin with `--password-file -` (its first line; the lines after it are commands). On Windows, the group/other mode check is skipped and stdin is the safer door. Never from the command line or an environment value: no flag takes one |
| `--size 120x40` | the headless window. It changes what a snapshot shows, never what you read: `read` always returns the session's whole screen |
| `--script FILE` | run the commands in FILE instead of stdin |
| `--record FILE` | keep the exchange: `> ` before each command, `< ` before each answer or event |
| `--ca-file`, `--config`, `--set KEY=VALUE` | as for `tsb-tui` itself |
| `--guide` | print this guide and exit |

A theme and a scheme are chosen with `--set theme.name=… --set theme.term.scheme=…`; the Theme picker is not available under drive.

- Run drive on a machine that is not the TSB host.
- drive writes `{"ev":"ready","version":"tsb-tui <version>","principal":"<your-login>","size":"120x40","profile":"drive","engine":"<version>","send_gate":true,"settled_ms":800,"wait_timeout_ms":30000}` when it is up and logged in. With no `--user`, `principal` is `null` and the first command is `login`.
- **Exit codes.** `0`: a script passed every step, or stdin ended. `1`: a step failed (a failed wait, or any command answering `ok: false`; **a script stops at the first one**, so it never types on after a step that did not do what it should), or drive could not start. `2`: bad usage. In interactive mode a failed wait is an answer and drive keeps running.

## 3. Lines in and out

- **A command:** `{"id": 7, "op": "type", "session": "s.3d81e0", "text": "show version\r", "look": "L1"}`. `id` is yours and comes back on the answer. `session` defaults to the session in front. Blank lines and lines starting with `#` are skipped.
- **An answer:** `{"id": 7, "ok": true, ...}`, or `{"id": 7, "ok": false, "error": "<code>", "why": "<plain words>", ...}`.
- **An event:** `{"ev": "<name>", "session": "s.…", ...}`. Events arrive at any time, between answers.
- A device login has no session yet: its events use `device_login` instead. `device_login_hostkey` carries `kind`
  and `fingerprint`; `device_login_prompt` carries `method` and the device's plain strings, `echo`, `attempt` and
  `prompt_id` when a round takes answers. Banners and empty rounds take no answer. `device_login_result` carries
  `outcome`; on `connected` it also carries the ordinary attach answer's fields. A recorded session emits `gate`
  before that result. An unknown host-key kind or prompt method is cancelled and reported as an error result
  saying drive is too old. `request_refused` reports a refused answer without ending the pending prompt.
- Commands are taken one at a time, in the order you send them. A `wait` holds the ones behind it until it answers.
- Everything drive writes is plain ASCII JSON.

## 4. The loop

1. `sessions`: list what you may see. Work only in the sessions you were lent; today you may also see other sessions on the same devices, and you leave them alone.
2. `attach` each session you were lent. You get a `gate` event with the recording warning, then the answer, with `you`: `type` or `view_only`.
3. `wait` with `{"holder": "me"}` until the owner hands you the keyboard.
4. `type` a command, ending in `\r`.
5. `wait` with `{"settled_ms": 800, "prompt": "<the prompt's regex>"}`.
6. `read` with `"what": "reply"` to get everything the command printed, including what scrolled off the screen.
7. Repeat 4 to 6, in any of your sessions. Commands to a session in the background bring it to the front for you; reads and waits never move anything.
8. When you are done: `hand_back`, then `detach`; `logout` when you are finished with this login altogether, which detaches everything and signs out.

## 5. Commands

| `op` | Fields | Answers |
|---|---|---|
| `login` | `user`, `password_file` | `principal`. One driver is one login |
| `sessions` | optional `live` (bool, default false), `own` (bool, default false) | `sessions`: each with `session`, `target`, `state`, `owner`, `holder` (a login or null), `rec`, `ends_in`. Sessions open here come first. `live` keeps only sessions that have not ended (no end state); `own` keeps only sessions whose owner is this login. Both may be given |
| `targets` | optional `query` | `targets`: the targets this login may see, each with `id`, `type`, `name`, `group`, `tags`, `discovered_by`, in id order, one page of them. The answer also carries `more`: true, and only then, when TSB has a next page that this command does not fetch. `query` is the Targets tab's: `type:`, `tag:` (repeatable), `group:` and `source:` words, matched by TSB; a word with no `field:` is `bad_request`. Every string is text: a control character in it (an escape included) is replaced by its picture, and a type you do not know is shown as it is |
| `target_new` | `id`, `type` (`ssh` or `telnet`), `host`; optional `name`, `group`, `tags` (list), `port`, `user` (SSH only) | `target`: the record the core returned. The definition fields are top-level command fields; here `id` is the target id and is also echoed as the command correlation id. Drives the target form; a refusal keeps its fields and answers the core's error code and message. Credentials are saved separately with Save a login on the Targets tab |
| `target_edit` | `target` (id); optional `type`, `name`, `group`, `tags` (list), `host`, `port`, `user` | `target`: the record the core returned. Reads the editable definition, fills the same form, applies supplied fields and submits the whole. Absent fields keep their values; `null` clears `name`, `group`, `tags`, `port` or `user`; `null` for `type` or `host` is `bad_request`. The target id never changes. Switching to telnet drops `user`. A refusal answers the core's error code and message |
| `target_delete` | `target`, `confirm` (must be `true`) | `target`: the id, after the core answers 204. Without `confirm: true`, `bad_request` and nothing is sent. Open sessions stay open. A refusal answers the core's error code and message |
| `new` | `target`; optionally `login_user` with one password source: `password_from` (`"stdin"`) or `password_file`; `recording_acknowledged` | a session opened on that target with `connection: new` (the only thing a telnet connection allows), then attached as `attach` does: `session`, `chip`, `front`, `you`, `holder`, `you_may_type`, `size`, with a `gate` event first when the session is recorded. A refused create answers TSB's own `error` code and `why`, for example `no_resolving_credential` or `target_not_found`. With `login_user` the create carries that account's password for this one new connection (an SSH target; with no `login_user` TSB uses the login it holds). The password comes from `password_from: "stdin"`, the next line of stdin, which you send right after the command: it is read raw, never echoed and never in the transcript, and refused in `--script` mode (drive takes that line even when it refuses the command, so the stream stays in step); or from `password_file`, a regular file or a pipe such as `/dev/fd/N` from a process substitution (a device is refused; the password is its first line, at most 1024 bytes). `password` in the command is refused. If the deployment records logins, TSB answers `login_recording_not_acknowledged` and opens nothing; repeat the command with `"recording_acknowledged": true` and the password again (drive keeps none), once you accept that your login answers are recorded. It needs `size.auto_request` on (`--set size.auto_request=true`; the `drive` profile has it off), because a new session carries a size; with it off the answer is `not_available`. A first-use host key is also `not_available`: it names the `session` and the `fingerprint`, and the gate waits for you to check the key out of band and tick it (`press` `space`, then `enter`) |
| `attach` | `session` | `session`, `chip` (its number on the rail), `front`, `you` (`type` or `view_only`), `holder`, `you_may_type`, `size`. A `gate` event comes first when the session is recorded |
| `switch` | `session` | `front` |
| `read` | `session`, `what`, optional `join_wrapped`, `lines` | see § 6 |
| `type` | `session`, `text`, `look`, optional `full_screen`, `timeout_ms` | `front`, `queued` (bytes), `sent: true`, `settled`, the `screen` block and look state. Waits for output since the send, then the quiet window. At timeout it still reports `sent: true`, with `settled: false`. `switched_from` when it brought the session forward. TSB sends no acknowledgement: a core refusal arrives as an event |
| `keys` | `session`, `keys`, `look`, optional `full_screen`, `timeout_ms` | as `type`. Every name is checked first: an unknown one is refused and nothing is sent |
| `wait` | `session`, `until`, `timeout_ms` | `elapsed_ms`, and `matched` for what a regex or an event matched; a held `prompt`, `screen` or `reply` wait also carries the screen block and look state of that moment; or the reason it failed (§ 7) |
| `hand_back` | `session`, optional `to` | `to`: the owner's client that now holds the keyboard |
| `hand` | `session`, `to` | `to`: the client that now holds it |
| `take` | `session` | `to`: your own client now holds the keyboard; owner only, types no bytes, needs no look. Refused with the action table’s reason when already holding it |
| `ask_to_type` | `session` | always `not_available`: TSB has no way to ask yet |
| `answer` | `session`, `choice` (1 to 4) | `choice`: the name of the one sent. Only the keyboard holder is asked |
| `size` | `session` | `asked`, `size` and `reason` of the answer. Only the keyboard holder may ask |
| `detach` | `session` | — |
| `end` | `session`, `confirm` (must be `true`) | `session`. **Ends the session for everyone and cannot be undone**, as the Menu's End session… does, so it needs `confirm: true`: without it the answer is `bad_request` and nothing is sent. It answers when TSB has: use `wait` with `{"ended": true}` for the end frame (§ 7). Only the session's owner, or someone allowed to terminate sessions, may end it; anyone else gets `refused`, whose `why` is the sentence the screen shows, and the session keeps running. `ended` when the record already says it ended (nothing is sent), `no_such_session` when TSB no longer knows it, `request_failed` for any other failure, `not_attached` when this drive has no such session open |
| `logout` | — | `detached`: the sessions it left, in the order they were opened (each keeps running, none is ended), and `broker`: `signed_out`, or `unconfirmed` when TSB did not confirm the logout (your login there then ends when it expires; drive holds no login either way). It is the Menu's Sign out. A `login` is accepted afterwards. `not_logged_in` when there is nothing to sign out of |
| `snapshot` | `format`: `text` (default) or `svg` | `text` or `svg`: the whole UI, as a person would see it |
| `click`, `press` | `label`; `keys` | `click` presses a visible control by its label (not available while the mouse is off, as in the `drive` profile); `press` sends Textual key names to the UI, dropping every key bound for the session and reporting its count in `dropped`; for example `["ctrl+backslash", "w"]` (for tests of the UI itself) |
| `resize` | `size` (`"100x30"`) or `cols` and `rows` | `size`: the headless window changed |
| `login_hostkey` | `device_login`, `accept` | — Answers a pending first-use host-key question after you check its fingerprint out of band |
| `login_answer` | `device_login`, `prompt_id`, `answers_from` (`"stdin"`) or `answers_file` | — One stdin line per prompt, read raw and never in the transcript; `answers_file` names a pipe (`/dev/fd/N`), never a regular file or a device |
| `login_cancel` | `device_login` | — Cancels the device login |
| `quit` | — | — Every session keeps running |

`hand` and `hand_back` name a client by its `client_id`, or by its login when that login has one client here (`choose_client` lists them when it has several). Both are the Who's here buttons, so they are refused when the button would be grey: only the keyboard holder or the owner may hand it over.

**Key names** for `keys`: `enter`, `tab`, `escape`, `backspace`, `delete`, `insert`, `space`, `up`, `down`, `left`, `right`, `home`, `end`, `pageup`, `pagedown`, `f1` to `f25`, `ctrl+<letter>`, `alt+<key>`, `shift+<key>`. They are encoded for the session's modes (an arrow is a different byte string once a program asks for application cursor keys). An unknown name is refused with `unknown_key`, and nothing is sent; use `type` for text.

## 6. Reading the screen

To type the device's own SSH login, use `new` with `login_mode: "prompt"`, `login_user` and the target. As with any
`new`, `size.auto_request` must be on. When TSB records logins, pass `recording_acknowledged: true` after considering
that the user name, device prompts and every answer will be recorded; otherwise the create is refused with
`login_recording_not_acknowledged` and opens nothing. The immediate answer is `{"device_login":"…"}`, followed by
the device-login events above. There is no Remember offer under drive.

- `login_hostkey {device_login, accept}` answers a first-use key question after checking its fingerprint out of band.
- `login_answer {device_login, prompt_id, answers_from:"stdin"}` consumes one raw stdin line per prompt. These lines
  are never echoed or written to the transcript, and this stdin door is refused in `--script` mode. Empty lines send
  empty answers; spaces are preserved. Alternatively, `answers_file` names a pipe such as `/dev/fd/N`, one line per
  answer. Each pipe line has a `drive.answer_timeout_ms` deadline (30000 ms by default). If the answers do not
  arrive in time, the command returns `bad_request` and sends no answer round. Regular files and devices are refused. `answers` in a command is refused and redacted from the transcript.
- `login_cancel {device_login}` cancels a pending login.

The look rule gates `type` and `keys` only; it does not gate these three login commands. The attached TUI's existing screen-lock behavior remains until the headless lock contract release. After `connected`, the ordinary session input and recording gates apply. On a lost socket, a session may have
opened: refresh `sessions` before deciding whether to start again.

Every read is made in your own client, from what it already received. TSB itself never serves screen content.

- **`screen`:** `lines` (exactly the session's rows, trailing spaces trimmed, blank rows `""`), `cursor` as `[row, column]`, `cols`, `rows`, `alt` (a full-screen program such as vim or top owns the screen), `cursor_hidden` (mode 25), `quiet_ms`, and `modes` (cursor keys, keypad, bracketed paste, mouse tracking, focus events, kitty keyboard flags, synchronized output).
- **`reply`:** everything printed since your last `type` or `keys` in that session, scrollback included, **starting with what you typed if the session echoed it**. With no input yet it is everything this client has received. `truncated` says the knob `drive.reply_max_lines` (5 000) cut it, keeping the first lines. In a full-screen program there is no scrollback: `reply` returns the screen and says `alt: true`.
- **`history`:** the last `lines` lines (at most `drive.reply_max_lines`), scrollback included.
- **`who`:** `holder` (a login or null), `holder_client`, `owner`, `you` (your client id), `clients` (each with `user`, `client_id`, `may_control`) and `you_may_type`.
- **A scroll view** open in this TUI (`press` `["ctrl+backslash", "left_square_bracket"]`, then `pageup`) changes none of these reads: they are about the session, never about which rows the window shows. While it is open nothing typed reaches the session (the engine still answers the session's own queries): `type` and `keys` answer `not_available` and send nothing, and `press` `escape` closes it.
- `join_wrapped: true` joins a line that wrapped over several rows into one (and drops the blank rows at the bottom of `screen`); the cursor stays in screen rows.

A screen answer mints a `look` id and `look_kind`: `ordinary`, `unsettled`, or `after_silence`. Name it on the next
send. `look: "last"` resolves to this session's latest non-refusal look; a refusal's look can only be named by its id.
A look is used up by any send in its session; an answer reporting `sent: false` does not spend it. An old attachment's
look is `no_look`, even after reattaching the same session. Reads of reply, history or who, and waits on holder, state,
events or settled alone, mint no look.
At each mint drive drops looks from ended attachments, spent sends, or changed keyboard/stale counts. Looks with
older output/size counts remain usable by a young brake until `drive.brake_look_max_ms`. At most 64 looks are kept
per session, oldest dropped first. Naming a dropped look returns `no_look` with "that look is too old".

After a send, quiet time alone is insufficient: there must be output since that send, then `drive.settled_ms` quiet.
Before any output the answer has `look: null` and `no_look_yet: "no output since your send"`. While output still arrives
it carries an `unsettled` look and `no_look_yet: "output still arriving"`, good only for a brake. Synchronized output
(DEC 2026) always mints an `unsettled` look, so a brake can stop a program mid-frame. Once the silence time passes
without output, an `after_silence` look permits only a brake or bare Enter. Any output ends that silence restriction.
History survives detach, attach, automatic reattach and logout/login for this process's life; an attach dump is not
output since a send, but an attach redraw is.

A gate refusal sends nothing and returns `sent: false`, `screen` (the whole block), and the look state just described.
Earlier refusals (`bad_request`, `unknown_key`, `not_attached`, `not_available`, `not_typist`) keep their existing shape.
drive never repairs a mismatch: no automatic Ctrl-C, q, retry or other typed input.

Shells that emit OSC 133 marks give drive an extra check after all other send checks, including the
target prompt-pattern check. After a live
prompt-start `A`, a line-ending `type` or `keys` send without `full_screen: true` is refused with
`not_at_prompt` if a command-start `C` or command-finished `D` followed that prompt. Prompt-end `B`
keeps the prompt state. A new `A` permits the send only if all the other checks pass too.
Attach and reattach reset this state: dump marks never count, and the check starts only after a live `A`.
Network gear emits no marks; sessions without marks behave as before.

A settled `type` answer adds integer `exit_code` only if a `D` carrying a code arrived after that send
and before the answer. It reports the last `D`'s code if several arrived; a final `D` without a code,
no new `D`, or an unsettled answer omits the field. The exit code is data for the sender to read;
drive never acts on it.

## 7. Waiting

`until` holds one or more predicates, and all must hold:

| Predicate | Holds when |
|---|---|
| `{"settled_ms": 800}` | no output for 800 ms; `true` means 800 (`drive.settled_ms`) |
| `{"prompt": "<regex>"}` | the cursor's row matches |
| `{"screen": "<regex>"}` | anywhere on the screen matches |
| `{"reply": "<regex>"}` | anywhere in the reply so far matches |
| `{"holder": "me"}`, `"not_me"`, `"nobody"` | the keyboard is where you say (`not_me` includes `nobody`) |
| `{"state": "live"}`; `{"ended": true}` | the session is in that state |
| `{"event": {"ev": "...", ...}}` | an event with those fields arrives, after the wait began (`output` counts cannot be waited for) |

- A regex is searched with `^` and `$` meaning a line.
- After your own `end`, which answers when TSB has, `wait` with `{"ended": true}` returns once the end frame has reached your client.
- **`timeout_ms`** defaults to 30 000 and is capped at 600 000 (`drive.wait_timeout_ms`, `drive.wait_timeout_max_ms`). A timeout is an ordinary answer, `"error": "timeout"`, with `tail`: the last lines of the screen (up to ten, trailing blank rows dropped). A wait that fails for another reason carries `tail` too.
- A wait answers as soon as its predicates hold, even if its look is null or unsettled. The predicates and the
  returned screen and look are read in the same backend lock section.
- Before a send use a screen wait such as `{"screen":".*","settled_ms":true}` (or the expected prompt with
  `settled_ms: true`). A holder wait alone is not a look. Settled is a hint about quiet, never proof of completion.
- `drive.settled_ms` defaults to 800 and cannot load below 800 ms; `drive.wait_timeout_ms` defaults to 30000 and
  cannot load below 5000 ms, from a file, environment or `--set`. A command's own `timeout_ms` may be shorter.
  `ready` reports the effective values. Both `type` and `keys` use the silence time as their default timeout.
- `[drive] send_gate = true` is on in both profiles. Its temporary rollback switch is **config file only**:
  `TSB_TUI_DRIVE_SEND_GATE`, `--set drive.send_gate=...` and all `[profiles.*]` settings refuse it. With it off,
  `ready`, every send answer (including errors) and the record show `send_gate: false`. No rig switches it off.
  It is removed one release after the lab rig and staging have run with it on and no rig needs it.
- The attached screen lock is unchanged in this release; drive's look-based screen lock awaits its contract release.
- **A wait fails at once,** with the reason, when one of these happens, unless the wait is for that very thing:

| Happens | Answer's `error` |
|---|---|
| the keyboard leaves you because the owner took it | `takeover` |
| the keyboard leaves you another way (`handoff`, `owner`, `detach`) | `token_moved` |
| your input is refused (its `reason` is in the answer) | `refused` |
| a stale prompt arrives and the screen may be wrong | `stale` |
| the countdown enters its warning window | `notice` |
| the session ends | `ended` |
| the owner removes you | `removed` |
| TSB closes your view (the session lives and drive reattaches) | `detached` |
| the fleet poll finds the session unreachable | `unreachable` |

A wait that starts after the session ended or you were removed fails at once, too.

## 8. When the owner takes the keyboard

You will see:

```text
< {"ev":"token","session":"s.e4a913","reason":"takeover","holder":{"user":"tu","client_id":"cl.91c2"},"you":"view_only","your_input":"refused from now"}
< {"id":10,"ok":false,"error":"takeover","why":"tu, the owner, took the keyboard","elapsed_ms":40210,"tail":["...","^C","ops@k8s-node-07:~$"]}
```

What to do:

1. **Stop typing in that session.** With the guard on (the `drive` profile), drive refuses your `type` and `keys` there with `not_typist` and sends nothing, but do not try. (In the `harness` profile the guard may be off: the input then reaches TSB, which refuses it, and you get an `input_refused` event.)
2. **Report what you were doing** to whoever runs you, with the `tail`. The owner may have stopped your command with Ctrl-C.
3. **Keep reading if useful.** You are still attached as a viewer.
4. **To continue,** ask the owner outside TSB, then `wait` with `{"holder": "me"}`. TSB has no way to ask for the keyboard yet (`ask_to_type` answers `not_available`).

Your other sessions are not affected.

Two things to know. Your client answers a terminal's questions (where is the cursor, what are you) for the session, but **only while you hold the keyboard**, and never while a hand-off of yours is in flight: an answer from a client that is not the holder would take the keyboard, or count as typing. The one case the rule cannot cover: the owner has two devices, and one takes the keyboard while you are answering a query within the same round trip; the keyboard then moves back to you until that device's next keystroke. And **every attach makes the session redraw once** (a wiggle of its size, today): if a screen repaints when the owner opens or takes back a session you are in, that is why.

## 9. Network gear

Most devices you will meet are routers, switches and consoles, not Linux.

- **No exit codes.** Network CLIs do not report success as a number. Read the reply and judge it.
- **Prompts** end in `#` (privileged), `>` (user), or `(config)#`, `(config-if)#` in configuration mode. Wait on the prompt for the mode you expect, for example `{"prompt": "rtr-core-01\\(config\\)#\\s*$"}`.
- **Pagers.** Long output stops at `--More--`. Either switch paging off for your session first (IOS: `terminal length 0`; Junos: `set cli screen-length 0`), or wait for `{"screen": "--More--"}` and press `space`.
- **Confirmations** such as `[confirm]` or `[yes/no]` wait for you. Wait for them with `screen`, then answer deliberately.
- **Telnet and serial** sessions have no size: `size` answers with the reason `not_resizable`.
- **Ctrl-C does not always interrupt.** IOS aborts some commands with Ctrl-^, which you send as the byte itself: `{"op": "type", "text": "\u001e", "look": "L1"}`. Check the device before relying on a key.
- **A failed-attempt line before the banner.** Network gear may print a failed-attempt line (for example Junos's "Password incorrect.") before a successful login's banner, when its authentication order tries a source that refuses before one that accepts. TSB relays it verbatim. The prompt, not the first line, says whether you are in: wait for the prompt (`{"prompt": "[>#]\\s*$"}`), not for the absence of an error line.

One line per send: `type` allows one final CR only, never LF or CRLF. `keys` allows one line-end key last, by identity
(`enter` with any modifiers, Ctrl-M or Ctrl-J in either case) or by a legacy payload ending in CR or LF. A tab and line
end never share a send. A control key (arrows, function keys, escape, deletion, navigation, Ctrl/Alt keys included)
goes alone in `keys`. `type` refuses other C0 controls except a lone brake byte. No paste or batch command is added.

A program has taken the screen if the alternate screen is on or mode 25 hides the cursor. `type` and text keys
(printable characters, punctuation names, space and Shift-text) then need `full_screen: true`; that statement is
refused when no program took the screen. Non-text keys need a look but no statement. A brake needs no statement.

Brakes are whole payloads of Ctrl-C (`0x03`) or Ctrl-^ (`0x1e`), including the aliases Ctrl-6 and Ctrl-circumflex_accent.
`type "\u0003"` and `type "\u001e"` are brakes under every keyboard mode; kitty may encode a `keys` Ctrl-C as an
escape sequence, which is an ordinary control key instead. A young brake look tolerates output and size changes for
`drive.brake_look_max_ms` (10000 by default), but never keyboard moves, stale choices or a change of alternate screen
or mode 25. Past that age it gets the strict change check. An old attachment's look never counts.

After silence, a bare Enter is `type "\r"` or a single key whose legacy payload is exactly CR (Enter, Ctrl-M).
Ctrl-J is a line end but never that probe. If a probe also brings nothing back, stop with a brake or hand it to a person.
After a probe, check that a prompt came back: an echo or unrelated output can end the silence too.

### tmux: attach read-only, read, detach

tmux takes the alternate screen, so every text send inside it says `full_screen: true`; control keys (the prefix) go
alone with `keys`.

```text
> {"id":1,"op":"type","text":"tmux attach -r -t mysession\r","look":"last"}
< {"id":1,"ok":true,"sent":true,...}
> {"id":2,"op":"wait","until":{"screen":"\\[mysession\\]","settled_ms":true}}
< {"id":2,"ok":true,"look":"L2","look_kind":"ordinary",...}
> {"id":3,"op":"read","what":"reply"}
< {"id":3,"ok":true,"lines":[...],"alt":true,...}
> {"id":4,"op":"keys","keys":["ctrl+b"],"look":"L2"}
< {"id":4,"ok":true,"sent":true,...}
> {"id":5,"op":"keys","keys":["d"],"look":"last","full_screen":true}
< {"id":5,"ok":true,"sent":true,...}
```

The first `type` runs at the shell prompt (no alt screen, no `full_screen`). After tmux attaches the alt screen is
on: `read` reports `alt: true`, and the detach's `d` needs `full_screen: true`. The prefix `ctrl+b` is a control key
and goes alone in `keys` with no `full_screen` statement. Step 2 waits for tmux's status line, which shows the
session's name in brackets.

## 10. Events

Each is written unless the knob `drive.events` leaves it out. A wait can still match an event the knob leaves out.

| `ev` | Fields | Means |
|---|---|---|
| `ready` | `version` (the same `tsb-tui <version>` string as About and `--version`), `principal`, `size`, `profile`, `engine`, `send_gate`, `settled_ms`, `wait_timeout_ms` | drive is up (always written, once) |
| `gate` | `recording` | the recording warning, told before an attach; nothing is attached yet |
| `token` | `reason`, `holder`, and `from` (when it came to you) or `you`, `your_input` | who holds the keyboard now. `holder` is `"me"`, a `{user, client_id}`, or `null` (nobody). `your_input: "refused from now"` says it left you by someone else's hand and your input is refused (it is left out for an owner's client, which takes the keyboard back by typing, and when you handed it over yourself) |
| `input_refused` | `reason` | your input did not reach the device: `not_typist`, `stale`, `replaying`, `unreachable` |
| `clients` | `clients` | who is attached now, each with `may_control` |
| `stale` | `state`, `you_choose`, `choices` or `cause` | the screen may be wrong. If you hold the keyboard `you_choose` is true and `choices` are the four, numbered for `answer`; otherwise only `cause` is told. `state: "chosen"` tells the choice made |
| `notice` | `ending`, `ends_at`, `ends_by` | the countdown entered (`ending: true`) or left its warning window |
| `size` | `size`, `reason` | the size in effect changed, and why |
| `ended` | `state` | the session ended: `killed` or `terminated` |
| `removed` | — | the owner removed you |
| `detached` | — | TSB closed your view of the session; it lives, and drive reattaches by itself |
| `output` | `bytes` | how many bytes arrived; only in the `harness` profile or with `--set drive.output_events=counts`; at most one a second per session |

| Event | Do |
|---|---|
| `stale` with `you_choose` | you must choose: `answer` 1 (redraw) for a full-screen program, 2 (Ctrl-L) only at a shell prompt, or hand back to the owner. Input is refused until someone chooses |
| `notice` | tell whoever runs you; your typing keeps it alive |
| `ended` | stop; you cannot reopen it |
| `removed` | stop, and do not attach again by yourself |
| `detached` | wait; drive reattaches by itself |

The owner, not you, is asked "Kill the session?" when your input gets no answer for 3 minutes. If a command legitimately prints nothing for a long time, say so to whoever runs you.

## 11. Errors

When `new` gets a front's HTTP 502, 503 or 504 instead of TSB's answer, its error code stays the
same and `why` says the front answered while TSB did not (TSB may be restarting). If the create
got it, whether a session was created is not known: send `sessions` to list your sessions before
sending `new` again. If the ticket or attach upgrade failed, the session was created: the answer
carries its `session` id, and it may be attached again or ended. TSB's own JSON errors on the
create and ticket keep their usual answers. A 502, 503 or 504 refusing the WebSocket upgrade is
always treated as the front's answer: TSB accepts the WebSocket first and refuses an attach with a
close code afterwards.

| `error` | Means |
|---|---|
| `no_look` | missing, unknown, used-up or wrong-session/attachment look; take a new screen look |
| `screen_changed` | output bytes or a `token`, `size` or `stale` frame followed the look; `changed` lists only the actual causes (`output`, `keyboard`, `size`, `stale`). Clients, notices and prompt frames do not count as changes |
| `unsettled` | the named look permits only a brake while output is arriving or synchronized output is on |
| `prompt_mismatch` | the cursor row does not match the target’s `prompt_pattern`; the answer names the pattern |
| `prompt_unusable` | the target record could not be read, or its pattern could not be parsed or built; `why` says which |
| `not_at_prompt` | live shell marks show no ready prompt for a line-ending send |
| `silent` | nothing came back for the silence time; only a brake or bare Enter may go |
| `full_screen` | a program took the screen but the text send did not say so |
| `not_full_screen` | the text send said full_screen but no program took the screen |
| `multi_line` | only one final CR/line-end key, and no tab with a line end |
| `control_bytes` | control bytes in text, or a control key mixed with other keys |
| `not_typist` | you do not hold the keyboard; nothing was sent |
| `takeover` | the owner took the keyboard during your wait |
| `token_moved` | the keyboard moved away from you for another reason (`handoff`, `owner`, `detach`) |
| `timeout` | the wait (or the attach, or an answer you were waiting for) ran out; waits carry `tail` |
| `refused` | your input or hand-off reached TSB and was refused; see `reason` |
| `stale`, `notice`, `detached`, `unreachable` | a wait failed on that event or state (§ 7) |
| `no_such_session` | not a session you may see |
| `no_such_client` | `hand` named a client that is not attached |
| `not_attached` | attach first (or the session is still attaching) |
| `not_logged_in` | log in first |
| `login_failed` | the login was refused; `why` is TSB's own sentence |
| `attach_failed` | the session could not be attached; `why` says how |
| `unknown_key` | a key name drive does not know |
| `owner_not_attached` | `hand_back` found no owner's client; choose `detach`, or keep the keyboard |
| `choose_client` | several clients fit; name one by `client_id` (the answer lists them in `clients`) |
| `not_available` | the action is not available now, or the feature does not exist yet; `why` says which |
| `ended`, `removed` | the session ended, or you were removed |
| `request_failed` | a request to TSB failed (the network, or an error status); `why` has it |
| `bad_request` | the command is malformed |
| `internal` | a defect in drive itself; its trace is on stderr. Report it |
| `target_auth_failed` | On `new`: the target refused the login TSB used for it (a credential TSB holds for you, or the login you supplied); it is not your TSB login. Do not retry in a loop — a retry sends the same login and the target refuses it again. Report it to whoever keeps that target's credential, or supply a login with `new` if you have one |
| `target_connect_failed` | On `new`: the target could not be reached, or the connection failed before a session opened |
| `no_resolving_credential` | On `new`: TSB holds no device login for your TSB login on this target. Ask the operator to map a credential to your login for that target |
| `target_not_found` | On `new`: no such target. Send `targets` and use an `id` from it |
| `login_recording_not_acknowledged` | On `new` with a supplied login: the deployment records device logins and you have not acknowledged it. Only if you accept that, repeat with `"recording_acknowledged": true` |
| `hostkey_changed` | On `new`: the target's host key changed since TSB recorded it. TSB refuses the connection; report it to the operator — do not accept a changed key |

## 12. A short example

```text
> {"id":1,"op":"attach","session":"s.7f3a2c"}
< {"ev":"gate","session":"s.7f3a2c","recording":"This session is recorded byte-perfect. Anything typed will be stored as-is."}
< {"id":1,"ok":true,"session":"s.7f3a2c","chip":1,"front":true,"you":"type","holder":"ci-agent","you_may_type":true,"size":"120x40"}
> {"id":2,"op":"wait","until":{"settled_ms":true,"screen":".*"}}
< {"id":2,"ok":true,"elapsed_ms":801,"matched":{"screen":"rtr-core-01#"},"screen":{"lines":["rtr-core-01#"],"cursor":[0,12],"cols":120,"rows":40,"alt":false,"cursor_hidden":false,"quiet_ms":801,"modes":{}},"look":"L1","look_kind":"ordinary"}
> {"id":3,"op":"type","text":"show clock\r","look":"L1"}
< {"id":3,"ok":true,"front":"s.7f3a2c","queued":11,"sent":true,"settled":true,"screen":{"lines":["rtr-core-01#show clock","12:34:56 UTC","rtr-core-01#"],"cursor":[2,12],"cols":120,"rows":40,"alt":false,"cursor_hidden":false,"quiet_ms":801,"modes":{}},"look":"L2","look_kind":"ordinary"}
> {"id":4,"op":"detach"}
< {"id":4,"ok":true}
```

## 13. Not available yet

- **Asking for the keyboard** (`ask_to_type`).
- **Seeing only the sessions lent to you:** today you may see every session on a device you were granted. How lending works is still to be designed.
- **Files and scroll back beyond what your client holds** arrive later.

## 14. What the gate does not guarantee

Use one drive process per login per session. Clients of the same login can take the keyboard by typing; the control
token excludes other logins, not those clients. Between a look and the send decision nothing may have reached this
client; bytes already in flight and the socket send-lock wait still leave a race of roughly a target round trip.
`sent` says bytes were handed to the socket, never that the command finished or worked.

A pager on the main screen, REPL, IOS text mode, or editor without alternate screen/hidden cursor can resemble a
prompt. A silent hang, an unfinished password line from the previous typist, a default on Enter, and a look you did
not actually read can still mislead you. Any output may satisfy the post-send rule: syslog, wall, a background job,
an attach redraw, or the probe's own echo. Minimize asynchronous output (`terminal no monitor`, `logging synchronous`)
and disable paging where suitable. In tmux the alternate screen is always on, so text sends say `full_screen: true`;
the look is the protection there. A brake can still land in a boot loader or a different program in a compound line.

When a target has `prompt_pattern`, drive reads it at open or attach and keeps it for that attachment.
Edits are seen at the next attach. A 404 leaves that attachment unchecked until the next attach; other read
failures refuse checked sends and are retried, awaited before the checks, at the next checked send.

The pattern is I-Regexp (RFC 9485), matched against the whole cursor row with trailing spaces trimmed.
It must reach the row’s end, including any text already typed. Only line-ending sends without `full_screen: true`
are checked, after the other checks; brakes and non-line-ending keys bypass it. A bare Enter after silence is
checked too. Cover every prompt you answer, including `[confirm]` and `Password:`. I-Regexp has no `\s` or `\d`;
use `[ \t\n\r]` or `[0-9]` as appropriate. Oversized patterns (over 4,096 NFA states) fail closed with
`prompt_unusable`. No prompt pattern is accepted on a send command.
