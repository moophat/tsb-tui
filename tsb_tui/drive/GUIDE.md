# tsb-tui drive: a guide for agents

**For:** an agent that works in terminal sessions through TSB · **Companion to:** the TUI design §2.9 · **Status:** built at E2. This file ships with the program and is what `tsb-tui drive --guide` prints; it says what drive does, and nothing it does not.

`tsb-tui drive` runs the TSB terminal client without a screen. You send it one JSON command per line on stdin, and it writes answers and events, one JSON object per line, on stdout. You see each session exactly as a person's TUI would show it, and you act on it through the same buttons and keys.

## 1. What you have, and what you do not

- **Your own TSB login,** and nothing else. You hold no fleet credentials. `new` opens a session on a target only where TSB resolves a credential for your login; otherwise it answers `no_resolving_credential`.
- **Sessions someone lends you.** A person (the owner) opens a session to a router, a server or a k8s node with their own credentials and lets you work in it. You never see their password or key.
- **The keyboard, only when it is handed to you.** One client types at a time. You start as a viewer, and the owner hands you the keyboard, or you take it from no one when nobody holds it.
- **The owner's brake.** The owner can take the keyboard back at any moment, even mid-command. From that instant your input is refused.
- **A full record.** Every byte you type is recorded verbatim, passwords included, and the audit says which client held the keyboard and when. Never type a secret you were not given for that purpose.

## 2. Starting

```text
tsb-tui drive --profile drive --broker https://<broker> --user <your-login> --password-file <file>
```

| Flag | Meaning |
|---|---|
| `--broker URL` | the core's address; required to run, never for `--guide` or `--help` |
| `--profile drive` | the default: acts for you on nothing (no automatic size requests, no automatic answers); the mouse is off, so `click` is not available |
| `--profile harness` | for test rigs: the mouse is on and output counts are written; `input.guard` is on by default here too (D52 was ruled on 2026-10-12), so a rig that wants to test the core's own refusal switches it off with `--set input.guard=false` |
| `--user`, `--password-file PATH` | log in at start. The password comes only from a file, or from stdin with `--password-file -` (its first line; the lines after it are commands). Never from the command line or an environment value: no flag takes one |
| `--size 120x40` | the headless window. It changes what a snapshot shows, never what you read: `read` always returns the session's whole screen |
| `--script FILE` | run the commands in FILE instead of stdin |
| `--record FILE` | keep the exchange: `> ` before each command, `< ` before each answer or event |
| `--ca-file`, `--config`, `--set KEY=VALUE` | as for `tsb-tui` itself |
| `--guide` | print this guide and exit |

A theme and a scheme are chosen with `--set theme.name=… --set theme.term.scheme=…`; the Theme picker is not available under drive.

- Run drive on a machine that is not the TSB host.
- drive writes `{"ev":"ready","principal":"<your-login>","size":"120x40","profile":"drive","engine":"<version>"}` when it is up and logged in. With no `--user`, `principal` is `null` and the first command is `login`.
- **Exit codes.** `0`: a script passed every step, or stdin ended. `1`: a step failed (a failed wait, or any command answering `ok: false`; **a script stops at the first one**, so it never types on after a step that did not do what it should), or drive could not start. `2`: bad usage. In interactive mode a failed wait is an answer and drive keeps running.

## 3. Lines in and out

- **A command:** `{"id": 7, "op": "type", "session": "s.3d81e0", "text": "show version\r"}`. `id` is yours and comes back on the answer. `session` defaults to the session in front. Blank lines and lines starting with `#` are skipped.
- **An answer:** `{"id": 7, "ok": true, ...}`, or `{"id": 7, "ok": false, "error": "<code>", "why": "<plain words>", ...}`.
- **An event:** `{"ev": "<name>", "session": "s.…", ...}`. Events arrive at any time, between answers.
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
8. When you are done: `hand_back`, then `detach`.

## 5. Commands

| `op` | Fields | Answers |
|---|---|---|
| `login` | `user`, `password_file` | `principal`. One driver is one login |
| `sessions` | — | `sessions`: each with `session`, `target`, `state`, `owner`, `holder` (a login or null), `rec`, `ends_in`. Sessions open here come first |
| `targets` | optional `query` | `targets`: the targets this login may see, each with `id`, `type`, `name`, `group`, `tags`, `discovered_by`, in id order, one page of them. The answer also carries `more`: true, and only then, when TSB has a next page that this command does not fetch. `query` is the Targets tab's: `type:`, `tag:` (repeatable), `group:` and `source:` words, matched by TSB; a word with no `field:` is `bad_request`. Every string is text: a control character in it (an escape included) is replaced by its picture, and a type you do not know is shown as it is |
| `new` | `target` | a session opened on that target with `connection: new` (the only thing a telnet connection allows), then attached as `attach` does: `session`, `chip`, `front`, `you`, `holder`, `you_may_type`, `size`, with a `gate` event first when the session is recorded. A refused create answers TSB's own `error` code and `why`, for example `no_resolving_credential` or `target_not_found`. It needs `size.auto_request` on (`--set size.auto_request=true`; the `drive` profile has it off), because a new session carries a size; with it off the answer is `not_available`. A first-use host key is also `not_available`: it names the `session` and the `fingerprint`, and the gate waits for you to check the key out of band and tick it (`press` `space`, then `enter`) |
| `attach` | `session` | `session`, `chip` (its number on the rail), `front`, `you` (`type` or `view_only`), `holder`, `you_may_type`, `size`. A `gate` event comes first when the session is recorded |
| `switch` | `session` | `front` |
| `read` | `session`, `what`, optional `join_wrapped`, `lines` | see § 6 |
| `type` | `session`, `text` | `front`, `queued` (bytes), and `switched_from` when it brought the session forward. TSB sends no acknowledgement: a refusal arrives as an event |
| `keys` | `session`, `keys` | as `type`. Every name is checked first: an unknown one is refused and nothing is sent |
| `wait` | `session`, `until`, `timeout_ms` | `elapsed_ms`, and `matched` for what a regex or an event matched; or the reason it failed (§ 7) |
| `hand_back` | `session`, optional `to` | `to`: the owner's client that now holds the keyboard |
| `hand` | `session`, `to` | `to`: the client that now holds it |
| `ask_to_type` | `session` | always `not_available`: TSB has no way to ask yet |
| `answer` | `session`, `choice` (1 to 4) | `choice`: the name of the one sent. Only the keyboard holder is asked |
| `size` | `session` | `asked`, `size` and `reason` of the answer. Only the keyboard holder may ask |
| `detach` | `session` | — |
| `snapshot` | `format`: `text` (default) or `svg` | `text` or `svg`: the whole UI, as a person would see it |
| `click`, `press` | `label`; `keys` | `click` presses a visible control by its label (not available while the mouse is off, as in the `drive` profile); `press` sends Textual key names, for example `["ctrl+backslash", "w"]` (for tests of the UI itself) |
| `resize` | `size` (`"100x30"`) or `cols` and `rows` | `size`: the headless window changed |
| `quit` | — | — Every session keeps running |

`hand` and `hand_back` name a client by its `client_id`, or by its login when that login has one client here (`choose_client` lists them when it has several). Both are the Who's here buttons, so they are refused when the button would be grey: only the keyboard holder or the owner may hand it over.

**Key names** for `keys`: `enter`, `tab`, `escape`, `backspace`, `delete`, `insert`, `space`, `up`, `down`, `left`, `right`, `home`, `end`, `pageup`, `pagedown`, `f1` to `f25`, `ctrl+<letter>`, `alt+<key>`, `shift+<key>`. They are encoded for the session's modes (an arrow is a different byte string once a program asks for application cursor keys). An unknown name is refused with `unknown_key`, and nothing is sent; use `type` for text.

## 6. Reading the screen

Every read is made in your own client, from what it already received. TSB itself never serves screen content.

- **`screen`:** `lines` (exactly the session's rows, trailing spaces trimmed, blank rows `""`), `cursor` as `[row, column]`, `cols`, `rows`, `alt` (a full-screen program such as vim or top owns the screen), and `modes` (cursor keys, keypad, bracketed paste, mouse tracking, focus events, kitty keyboard flags, synchronized output).
- **`reply`:** everything printed since your last `type` or `keys` in that session, scrollback included, **starting with what you typed if the session echoed it**. With no input yet it is everything this client has received. `truncated` says the knob `drive.reply_max_lines` (5 000) cut it, keeping the first lines. In a full-screen program there is no scrollback: `reply` returns the screen and says `alt: true`.
- **`history`:** the last `lines` lines (at most `drive.reply_max_lines`), scrollback included.
- **`who`:** `holder` (a login or null), `holder_client`, `owner`, `you` (your client id), `clients` (each with `user`, `client_id`, `may_control`) and `you_may_type`.
- `join_wrapped: true` joins a line that wrapped over several rows into one (and drops the blank rows at the bottom of `screen`); the cursor stays in screen rows.

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
- **`timeout_ms`** defaults to 30 000 and is capped at 600 000 (`drive.wait_timeout_ms`, `drive.wait_timeout_max_ms`). A timeout is an ordinary answer, `"error": "timeout"`, with `tail`: the last lines of the screen (up to ten, trailing blank rows dropped). A wait that fails for another reason carries `tail` too.
- **Settled is a hint,** counted from the later of the wait and the last byte, so a wait never answers sooner than that after you sent it. A slow command can pause longer. Always pair `settled_ms` with a `prompt`.
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
- **Ctrl-C does not always interrupt.** IOS aborts some commands with Ctrl-^, which you send as the byte itself: `{"op": "type", "text": "\u001e"}`. Check the device before relying on a key.

## 10. Events

Each is written unless the knob `drive.events` leaves it out. A wait can still match an event the knob leaves out.

| `ev` | Fields | Means |
|---|---|---|
| `ready` | `principal`, `size`, `profile`, `engine` | drive is up (always written, once) |
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

| `error` | Means |
|---|---|
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

## 12. A short example

```text
> {"id":1,"op":"sessions"}
< {"id":1,"ok":true,"sessions":[{"session":"s.7f3a2c","target":"rtr-core-01","state":"live","owner":"tu","holder":"tu","rec":true,"ends_in":"6d 23h"}]}
> {"id":2,"op":"attach","session":"s.7f3a2c"}
< {"ev":"gate","session":"s.7f3a2c","recording":"This session is recorded byte-perfect. Anything typed will be stored as-is."}
< {"ev":"clients","session":"s.7f3a2c","clients":[{"user":"tu","client_id":"cl.5c20","may_control":true},{"user":"ci-agent","client_id":"cl.91c2","may_control":true}]}
< {"id":2,"ok":true,"session":"s.7f3a2c","chip":1,"front":true,"you":"view_only","holder":"tu","you_may_type":true,"size":"120x40"}
> {"id":3,"op":"wait","until":{"holder":"me"},"timeout_ms":600000}
< {"ev":"token","session":"s.7f3a2c","reason":"handoff","holder":"me","from":"tu"}
< {"id":3,"ok":true,"elapsed_ms":12020}
> {"id":4,"op":"type","text":"terminal length 0\r"}
< {"id":4,"ok":true,"front":"s.7f3a2c","queued":18}
> {"id":5,"op":"wait","until":{"settled_ms":800,"prompt":"rtr-core-01#\\s*$"}}
< {"id":5,"ok":true,"elapsed_ms":1240,"matched":{"prompt":"rtr-core-01#"}}
> {"id":6,"op":"type","text":"show ip interface brief\r"}
< {"id":6,"ok":true,"front":"s.7f3a2c","queued":24}
> {"id":7,"op":"wait","until":{"settled_ms":800,"prompt":"rtr-core-01#\\s*$"}}
< {"id":7,"ok":true,"elapsed_ms":1010,"matched":{"prompt":"rtr-core-01#"}}
> {"id":8,"op":"read","what":"reply"}
< {"id":8,"ok":true,"lines":["show ip interface brief","Interface              IP-Address      OK? Method Status                Protocol","GigabitEthernet0/0/0   192.0.2.1       YES NVRAM  up                    up","...","rtr-core-01#"],"alt":false,"truncated":false}
> {"id":9,"op":"hand_back"}
< {"ev":"token","session":"s.7f3a2c","reason":"handoff","holder":{"user":"tu","client_id":"cl.5c20"},"you":"view_only"}
< {"id":9,"ok":true,"to":{"user":"tu","client_id":"cl.5c20"}}
> {"id":10,"op":"detach"}
< {"id":10,"ok":true}
```

## 13. Not available yet

- **Asking for the keyboard** (`ask_to_type`).
- **Seeing only the sessions lent to you:** today you may see every session on a device you were granted. How lending works is still to be designed.
- **Files and scroll back beyond what your client holds** arrive later.
