# contract — U1

The published contract of the Terminal Session Broker: OpenAPI 3.1 for REST (C1), JSON Schema
2020-12 for WebSocket frames (C2), local IPC (C3), manifests (C5), audit events (C6) and recordings
(C8), fixtures under `fixtures/C1…C8/`, the stdlib-only runner `python -m tsbc verify`, and the
thin client `tsb_contract_client`. Tagged `contract-vX.Y`; each tag ships a release tarball a
stranger can use without repo access. CODEOWNERS = @moophat. Imports nothing from `core/`.

**Never hand-write a field name from this README.** The schemas and the fixtures are the record;
this file only says where they are and how to run them. What each tag adds is `CHANGELOG.md`.

## Layout

    CONTRACT_VERSION        the tag this tree is
    openapi/tsb.openapi.json  C1, JSON (the stdlib has no YAML parser)
    schemas/*.schema.json   every other shape; fixture.schema.json is the fixture format itself
                            (vt-ipc.schema.json and vt-screen.schema.json are C4's)
    fixtures/C1 … C8/       <name>.json, plus <name>*.bin byte files where bytes are the truth
    fixtures/graded-by.json the side index: each reference fixture's cited tests, checked
                            (schemas/graded-by.schema.json)
    tsbc/                   the runner; tsbc/_serve.py is `python -m tsbc serve`
    client/tsb_contract_client/   the client
    tests/                  the runner's and the client's own tests (not shipped)
    tools/build-tarball.sh  the release tarball (not shipped)

## Run

Python 3.12, nothing installed. From this directory (or pass `--contract-dir <tree>`, or set
`TSB_CONTRACT_DIR`):

    python -m tsbc verify --self-check                       # the tree itself, offline
    python -m tsbc verify --list                             # every fixture: kind, grade, mark
    python -m tsbc verify https://<core> --profile e2         # C1, C2 against a running core
    python -m tsbc verify --artifacts DIR --profile e2        # C3, C5, C6, C8 from collected files
    python -m tsbc verify https://<core> --artifacts DIR --provider-tests U2,U3,U5,U11   # everything
    python -m tsbc serve [--bind 127.0.0.1] [--port 8787] [--static DIR]   # the fixture server

The default profile is the epoch this tag pins, e3; `--profile e2` still asserts exactly the
v0.5.0 set, and `--profile e1` the contract-v0.2.1 set, for a provider built against either.

`python -m tsbc serve` runs the shipped fixture server the web lane builds against: stdlib, plain
HTTP on loopback (never off it), synthetic credentials only, with scenarios by target id
(`normal`, `alt-screen`, `stale-pending`, `two-client`, `telnet-scenario`), each answering with a
canned dump so no stub shell or VT is needed; `GET /targets` and `GET /targets/{id}` answer from the
inventory in `tsbc/_serve.py`, whose addresses are all in RFC 5737's documentation range.
`--static DIR` serves a web app from the same origin. The prefix-content
fixtures (`attach-prefix-mid-session`, `attach-prefix-modes`, `attach-scrollback-amount`) need a real
shell, and `owner-takeover-then-ctrl-c` grades a real terminal's echo of Ctrl-C, which the stub does not
have; all four, named in `fixtures/C2/.serve-exempt`, are exempt from the serve acceptance run. Its
acceptance is `tsbc verify http://127.0.0.1:P --seams C1,C2 --profile e5` passing every live C1/C2
fixture against it.

Other options: `--seams C1,C2` grades only those seams (the rest are reported as not graded),
`--provider-results FILE` (repeatable) reads the run's own test results — `go test -json` output,
pytest's `--junitxml`, or a small JSON list `[{unit, file, test_id, outcome}]` for a rig — and is
what grades a reference fixture; `--provider-tests UNITS` says which units' own tests ran and
passed alongside this run, and is no longer sufficient (alone it grades no reference fixture);
`--ca-file` verifies the core's certificate against a PEM
bundle (verification is never off), `--target ID` overrides `TSB_TARGET_ID`, `--timeout S`
bounds each network wait. The URL is `https://`; `http://` is refused except to a loopback host.
At its end a live run deletes every session it created and left open, even one whose fixture
failed, so repeated runs against a standing core do not pile up connections; a failed delete is
a warning, never a FAIL.

Exit status:

| Code | Meaning |
|---|---|
| 0 | every required-green fixture of the profile was graded, and all passed |
| 1 | at least one FAIL |
| 2 | a usage error, an unreadable tree, or a schema outside the validator subset |
| 3 | nothing failed, but some required-green fixtures were not graded by this run (UNGRADED, or filtered out by `--seams`) — a partial run never reads as green |

## Inputs

Live fixtures read these at run time. No fixture holds a credential, and a password is read from
a file, never from argv or an environment value. Nothing the run prints carries one: a password a
provider echoes back in a message is printed as `<redacted>`.

| Input | Variable |
|---|---|
| user | `TSB_SUPERADMIN_USER` |
| password | the file named by `TSB_SUPERADMIN_PASSWORD_FILE` (owner-only; the file the core's seed reads) |
| target_id | `TSB_TARGET_ID`, or `--target` |
| target_no_cred_id | `TSB_TARGET_NO_CRED_ID`: a target the user can see but no credential resolves for |
| peer_user, peer_password | `TSB_PEER_USER`, the file named by `TSB_PEER_PASSWORD_FILE`: a second principal that can see the target and holds `session.create` on it, but lacks `session.terminate` and `broker.health` |
| viewer_user, viewer_password | `TSB_VIEWER_USER`, the file named by `TSB_VIEWER_PASSWORD_FILE`: a principal holding `session.join.ro` only — it may attach but never type, resize or take the token |

The **peer login needs `session.join.rw`** (the deploy template already seeds it): the take-over,
hand-off and remove-client fixtures attach as peers, and a peer that cannot hold the token would
grade nothing. The **viewer** login holds `session.join.ro` only, which the deploy template already
seeds; a view-only client is refused `view_only` on a hand-off and `not_typist` on a size request.
The **prefix fixtures' target must be a POSIX shell** (`printf` and the like), since their dump
comes from a real shell's output and not a canned one.

Three live fixtures depend on a timer or a knob value, as C1
`session-create-reuse-empty-connection` (required green from e1) depends on `connection_linger`:

- `session-create-reuse-empty-connection` reuses a connection whose last session has ended, so the
  graded core's `connection_linger` must be above 0 and above the time the fixture takes.
- The size fixtures (`size-request-clamped` and the family) need the graded core's size **floor**
  above the common `cols`/`rows` minimum and its **ceiling** on and below the common maximum, so a
  request below the floor clamps up and one above the ceiling clamps down — both directions can be
  provoked. `size-request-clamped` asks 1×1, 2×2, 65535×65535 and 65534×65534, so its floor must sit
  above 2 and its ceiling below 65534; the fixture saves the bounds from the clamped answers and
  checks against the saved values, expecting no fixed pair.

No other live fixture depends on one.

## Results, grades, marks

Each fixture's `grade` says how it is graded against a provider:

- `live` — against a URL;
- `artifacts:<check>` — the named check over an `--artifacts` directory;
- `with:<fixture>` — the result of that fixture, whose run validates real instances of this
  fixture's shape (a shape fixture leans on the live exchange that produces the shape);
- `reference` — only a provider's own test can grade it (a first-use host key, a worker's death,
  a restart). `graded_by` names the unit and the test; `fixtures/graded-by.json` names the test
  and its file, which `--provider-results` checks against the run's own results.

Self-check validates every fixture offline whatever its grade. A run prints one line per fixture:

- PASS / FAIL — graded by this run;
- SKIP — only for a fixture that is published but not yet required green in the profile;
- PROVIDER — a reference fixture every entry `fixtures/graded-by.json` names for it passed in
  `--provider-results`;
- UNGRADED — a required-green reference fixture whose cited tests are not in this run's results
  (absent, or owed), or for which `--provider-results` was not given.

A required-green fixture whose input is missing (no URL, no `--artifacts`, an unset variable)
is FAIL `input missing`, never SKIP. The run ends with the number of required-green fixtures it
did not grade, and names the seams it graded, those with input missing, and those it did not
grade.

Every fixture carries a `mark`: `required-green: eN` (the provider must pass it from epoch eN
on), `published: eN` (surfaces may build and mock against it from eN), or both. `--profile eN`
asserts every fixture required green at or before eN; later profiles are supersets. The E3b profile follows e4b and precedes e5, because its import save depends on the real vault. Publishing this profile does not move a consumer’s EPOCH; provider evidence remains owed.

## Artefacts

C3, C5, C6 and C8 live on the broker's filesystem and local sockets. A harness copies them into
a directory and lists them in `collected.json` — the shape and every path are
`schemas/artifacts-collected.schema.json` (`x-layout`): worker and supervisor manifests, each
recording (the `.ttyrec` and its `.dir` sidecar), the audit log, a tap of each session's data
socket, and the control captures of each connection (`control/<conn_id>.jsonl`) and supervisor
(`control/supervisors/<supervisor_id>.jsonl`). The runner grades exactly the files listed there,
and the directory holds regular files only: a symbolic link anywhere in it fails the collection.
A file that is not UTF-8, or not JSON where JSON is due, fails its fixtures and never stops the
run.

A required-green check with nothing to grade fails `input missing`, so a collection for e0 must
show: a first-use host key and a changed one refused, a refused password, the `echo` driver, an
unknown-command probe (injected by the harness, marked `probe: true`), a shell exiting on the
target (its channel closed by the target), resize / close_channel /
disconnect, a torn-down connection, a failed spawn, and at least one killed session, whose
recording closes with its end marker. From e1 it must also show:

- a core restart with reattach, in run-bounded captures (control-reattach, frames-continuity);
- a new session's handshake, a lone data-socket re-dial with its replay, and an ended session
  retained across a core restart, replayed and released (control-reattach);
- a forced ring overflow: a gap frame in a tap and its gap marker in the recording, with the
  handshake's numbers (frames-continuity, recordings-gap);
- a linger close (control-linger);
- heartbeats both ways, on a connection and on a supervisor (control-heartbeat, supervisor-heartbeat);
- every connect to a v0.2 worker carrying its timer values and knobs (control-timers);
- an orphan end, spooled by its worker and written by the returning core (audit-orphan-end);
- a worker killed while collecting (recordings-worker-death).

From e2, for the stale flow, it must also show: a forced gap whose stale choice is answered
`ctrl_l`, the broker marker (sidecar code 5) before the `0x0C` input frame in the recording, and
the `session.stale_choice` audit event (recordings-broker-marker).

From e3 a collection must also show a telnet connection (control-connect-telnet).

From e4 it must also show an `open_shell` toward a worker whose version the collection names
(control-open-timeout).

The hung-worker kill, the retention bound and the N-1 worker are graded by the providers' own
tests and rigs (`graded_by`).

**Run boundaries.** A core restart inside a collection is a run boundary. A run is one core process,
named by its start time as a zero-padded 20-digit count of nanoseconds, so runs order. Control lines
carry their `run`, and a run-bounded collection taps each session once per run at
`frames/<run>/<session_id>.bin` (kind `run-frames`); v0.1's `frames/<session_id>.bin` stays for a
collection with no boundary, which is graded exactly as at v0.1. Across a boundary the checks drop
the previous run's in-flight state (a command in flight at the kill is not "never answered"), take
the replay overlap once — a later run's replay supersedes what an earlier run tapped after the
hello's `last_committed_seq`, a replayed frame must repeat the earlier one exactly, and a superseded
gap frame still declares the marker the recording kept for it — and cut a torn last line or frame at
the end of a killed run as the recording's reopen rule cuts a torn tail.

A recording is two files written in order — each frame to the `.ttyrec`, then its code to the
`.dir` — so a crash or a copy taken while the session is live can leave a torn or uncoded last
frame. The reopen rule (`schemas/ttyrec.schema.json`, `x-sidecar`) cuts both files to the last
frame both hold; a writer applies it before appending again, and a harness collects a live
session's recording only cut by it. The runner fails a torn or uncoded tail.

**Credentials never leave the node.** Before collecting, the harness replaces every credential in
a capture (`connect.auth.key`, `connect.auth.password`, `login.auth.password` of a create request wherever one was captured, and every string of an `answers` list on C2 `device_login_answer` or C3 `auth_answer`) with the synthetic constant. The runner
fails the whole collection if any value under a credential-named key is anything else, and scans
every file in the directory — text, JSON strings, hex, raw bytes — for key material, so sessions
recorded for a collection must not display any. The rule reaches a recording's login frames too: a collected recording must not hold a code-7
login answer frame (the username and password a supplied login records, or the user name and answers a device login records, at the head of a recording when the
`recording.login_input` knob is on), and a collector leaves such a recording out. The runner fails the whole
collection, naming the file, if a recording's sidecar holds a code 7; a code-6 frame (the device's prompt) is not
refused. A code-8 frame (TSB's own prompt line, `login as:` or `Password:`) is not refused either: it carries no secret.

## Senders and receivers

Every shape here is closed: a sender emits exactly what the tag defines, and `tsbc` grades
senders that way — so a field this tag does not declare, a broker path in a session record among
them, fails. A receiver ignores fields, frame types (`t`) and events it does not know, so a later
tag's additions never break an earlier peer. Where a receiver must act on a value it does not
know:

- an error `code` → handle the response by its HTTP status;
- a WebSocket close code 4xxx → treat it as 4010 (the session was killed) — 4014 `removed` reads
  safe-by-4010, and never auto-reattach from it;
- a session `state` → treat the session as not attachable;
- a `session_type` → an opaque type, never an error;
- a command on a control socket → the worker answers `error` code `unknown_cmd` and keeps the
  connection; a supervisor answers `error` reason `unknown_cmd`;
- a token event's `reason` → read `holder`;
- a countdown's `ends_by` → show `ends_at`;
- an alert `kind` → show it as TSB's own alert, never as host state;
- a peer's `proto_version` → send `hello` with the core's own; `unsupported_proto` or `unknown_cmd`
  in answer makes the session `unreachable` / `protocol`;
- a `size` frame's `reason` not known → read `cols`/`rows` as the size in effect; a surface shows
  the reason when it is not `applied`; a clamped `size` answer is final until the surface's own
  window changes;
- an `input_refused` reason not known → input was refused;
- no `prefix` frame (a v0.2 core, or a non-`shell` session type) → output follows `attached`
  directly; render the dump at `prefix.cols × prefix.rows`;
- a `device_login_result` `outcome` not known → a failed device login; a `device_login_hostkey` `kind` or a
  `device_login_prompt` `method` not known → send `device_login_cancel`, say the surface is too old for this device
  login, and never offer Accept for an unknown `kind` (Release B; conventions V5);
- on C3, an `auth_failed` `code` not known → read as `refused`; an `auth_prompt` `method` or a `hostkey_ask` `kind` not
  known → the core answers `disconnect` and the device login ends `connect_failed` (ipc-control rule 7);
- a known `t` with an unknown `v` → ignore it like an unknown `t` (the attach first-frame rule
  wins: 4000 on a bad first frame).

From contract-v0.3 a session's control token is distinct from the session's size and the render is
local: only a target size **request** or a `prefix`/`size`/`clients`/stale/token frame crosses C2,
and a token move never sends a resize. A non-holder's `resize` gets a `size` answer with
`not_typist`, and a non-typist's input is refused `input_refused` `not_typist` — never silently
dropped. The dump is C2's one cell-bearing form; there is no structured cell/row API on C1 or C2,
and a grid never crosses C1 — a snapshot crosses C4 only, inside the broker.

From contract-v0.2 a session may be `unreachable` (a v0.1 worker under a v0.2 core): attachable,
showing what the core has, its input refused. A v0.1 consumer treats it as not attachable, which
is safe.

A session ends `killed` (by TSB or through it) or `terminated` (from outside TSB): a
`channel_closed` by the target, a `connection_lost` of any cause but the core's own `disconnect`,
or a lost worker. There is no `disconnected` state, and nothing in the contract reads an exit
status or matches a prompt.

Recordings are read as two clocks: output frames carry the worker's timestamps and input frames
the core's, so a reader never assumes timestamps increase across directions; within a direction
they never decrease. Output lost before the core recorded it is declared, never spliced: the
recording holds a gap marker (sidecar code 2), whose data is one fixed-format line a classic
player prints (`schemas/ttyrec.schema.json`, `x-gap-line`). From contract-v0.3 a recording may
also carry the broker marker (sidecar code 5), its data the fixed `x-broker-line` a classic player
prints, standing for a stale-screen choice the core made (`wiggle`, `ctrl_l`, `continue`,
`fresh_session`) — a v0.2 reader refuses it, as a v0.1 reader refused code 2.
From the release that adds `SessionCreateRequest.login`, a recording may also carry the login frames
(sidecar codes 6, a login prompt, and 7, a login answer), written only when the deployment's
`recording.login_input` knob is on: the username and the password of a login supplied on session create,
at the head of the recording, each one printable row (`schemas/ttyrec.schema.json`, `x-login-line`: a
backslash and every C0, C1 and DEL character of the text is escaped). A reader of an earlier tag refuses
them, as a v0.2 reader refused code 5.

## Client

    from tsb_contract_client import Client
    c = Client("https://<core>", ca_file=None)
    c.login(user, password)
    created = c.create_session(target_id, "new", "xterm-256color", 120, 40)
    page = c.list_sessions(ended="only", limit=100)  # one history page; copy next_cursor to continue
    att = c.attach(created["session"]["id"])        # blocking: scripts, rigs
    att = await c.async_attach(session_id)           # asyncio: inside an event loop (Textual)
    await att.resize(100, 30)                        # the C2 resize frame: the one resize path
    item = await att.recv_message(5)                  # output (bytes), or a server frame (dict)
    await att.answer_quiet_prompt(prompt_id, kill=False)   # the owner's answer to "Kill the session?"
    for alert in c.alerts(read_timeout=60):           # GET /alerts, for a broker.health holder
        ...

A surface shows `session.recording.warning_text`, and `hostkey.fingerprint` when present, before
it attaches. From the vault release the client has one method per vault, device and lock operation
(`vault_salt`, `vault_unlock_untrusted`, `device_challenge`, `register_device`, `store_credential`,
`map_profiles`, `screen_unlock`, …): it sends the byte values its caller derived, as given, and
computes no argon2id and signs nothing; `create_session(..., credential=(ref, layer))` carries the
operator's choice after a 409 `credential_choice_required`. A login for one new connection goes in
`create_session(..., login={"user": ..., "auth": {"kind": "password", "password": ...}})`: the client sends
it as given, in the request body and nowhere else, and never logs it; a surface warns before it sends one
when `me()["recording"]["login_input"]` is true, and sets `"recording_acknowledged": True` in the login
once the operator has seen it (without it the core answers 409 `login_recording_not_acknowledged`). From Release B a device login
(the device's own SSH login questions, answered in the pane) is `create_device_login(target_id, user, term, cols, rows,
*, recording_acknowledged=False)`, which sends no secret and answers the 202's body, and `await AsyncDeviceLogin.open(url,
pending, ssl_context=...)`, whose `recv()`, `answer_hostkey(accept)`, `answer(prompt_id, answers)`, `cancel()` and
`attachment()` (the same socket as an ordinary attach, after `device_login_result` `connected`) carry it; an answer is in
its frame and nowhere in the client's log. The login cookie lives in the Client object's memory and nowhere else. The client
refuses `http://` except to a loopback host. `recv()` returns output only, as at v0.1;
`recv_message()` returns the server frames this client knows too. The transport errors are exported
as the classes the transports raise: `ConnectionClosed` (either transport), `HandshakeError`,
`ProtocolError`, `HttpError`. The alert reader holds the core to two limits, a line of at most
64 KiB (`tsbc._http.MAX_STREAM_LINE`) and one event's data of at most 1 MiB (`MAX_EVENT_DATA`), and
fails the stream with `HttpError` past either.

## Stdlib only

Nothing under `contract/` imports a third-party package: not `jsonschema`, not `websockets`, not
`requests`, not `yaml`. The repo's import-lint enforces it. Tests may import `unittest` (stdlib)
and `pytest`. `pip install ./contract` installs both packages with no dependencies; an installed
`tsbc` carries no schemas or fixtures, so point it at a tree with `--contract-dir`.

## Validator subset

The runner carries its own JSON Schema validator, and the keywords it implements are a published
constraint on every schema in this tree:

- assertions: `type`, `properties`, `required`, `additionalProperties`, `enum`, `const`,
  `minimum`, `maximum`, `minLength`, `maxLength`, `pattern`, `items` (schema form),
  `minItems`, `maxItems`, `oneOf`, `anyOf`, `$ref`;
- annotations: `$schema`, `$comment`, `$defs`, `title`, `description`, `examples`, and any key
  starting with `x-`.

Any other keyword fails self-check loudly. Every live and `--artifacts` run self-checks the tree
first and grades nothing against a tree that fails (exit 2) — a malformed fixture or two fixtures
of one name stop the run instead of crashing it or hiding a result. JSON means RFC 8259: `NaN`
and `Infinity`, which Python's parser would accept, are refused wherever tsbc reads JSON. `$ref` resolves relative to the referencing file's location in the
tree. Patterns stay within the syntax Python `re`, ECMA-262 and Go RE2 share, and mean what
ECMA-262 says: `$` matches only at the end of the string, never before a final newline, and
classes are ASCII. Binary layouts are normative in `x-binary-layout` (and the recording's sidecar
codes in `x-sidecar`); self-check proves the runner's codecs match them. u64 values (`seq`,
`timestamp`) exceed 2^53: decode them as 64-bit integers.

## Synthetic credentials

Every credential-shaped value in a fixture is a fixed synthetic constant that cannot authenticate
anywhere (the list is `SYNTHETIC_CONSTANTS` in `tsbc/selfcheck.py`). Self-check fails any value
under a credential-named key (`password`, `current_password`, `new_password`, `key`, `passphrase`, `ticket`, `private_key`, `secret`,
and from the vault release `part1`, `part2`, `master_hash`, `storage_key`, `encryption_key`)
that is neither one of those constants nor a `${placeholder}`, and never quotes the value. The
published C7 vector files, `fixtures/C7/kat-*.json` and `fixtures/C7/chain-*.json`, are exempt
from that rule by path and by nothing else: they hold the hex values a known-answer or chain test
needs, which are fixtures, not secrets. They are written by tsb-program's
`tools/author-c7-vectors.py`, outside this tree: it needs third-party packages, ships in no
tarball and is run by no CI, and nothing here imports it. It
also scans the fixtures, the schemas, the OpenAPI document and this README, as text, as decoded
JSON strings, as hex payloads and as raw bytes, and fails on a PEM header, an OpenSSH private-key
marker, or a run of 40 or more base64 characters above 4.5 bits of entropy per character.

## Tests and the tarball

    python -m unittest discover contract/              # from the repo root
    bash contract/tools/build-tarball.sh [OUT_DIR]     # → OUT_DIR/tsb-<CONTRACT_VERSION>.tar.gz (default ./dist)

The build runs self-check first and packs only the shipped paths, reproducibly.

## Terminal output accounting

[Terminal output accounting](protocols/terminal-output-accounting.md) specifies C3 0.8 sealing and accounted disposal, code-9
incompleteness and linked audit facts, including explicit unrecorded context and unknown-on-close.
An older reader refuses code 9. Comparison/export includes the integrity facts with the recording. All new terminal fixtures are published/required-green e4; reference provider tests remain owed.

A recording quarantined by a sync failure may continue in an audit-linked pair. Chain-aware reading requires the complete linked audit history and each predecessor's quarantine notice. It presents the predecessor suffix as raw, durability-unconfirmed evidence while effective output comes from the linked replay; input comparison can be unconfirmed. Legacy single-pair readers cannot establish chain completeness. The core and worker provider obligations, including held-ring behavior, remain owed (terminal-output-accounting §5.3).

For chain collections, `collected.json` supplies `recording_chains` with the captured `session_record`, ordered `pairs` paths and session `audit` facts. The collector validates every generation below the recorded next generation and linked quarantine notices; missing evidence fails closed. `recording-chains-complete` grades this evidence; legacy artefact checks refuse chain collections.
