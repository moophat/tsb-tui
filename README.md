# tui — U7

A first-party shipped surface and the core's verification instrument — one artifact, both roles, no conflict (`Core_value` § Surface Philosophy ships it alongside web and desktop; `Desktop_Renderer` §4 gives it the same coverage at days not weeks). Python + Textual. Imports only the published contract client (CI lint), so what ships is a genuine API consumer and the smoke test for every deploy.

Ownership, seams and exit evidence: `../tsb-program/spine/plan.md` §2 (U7). A sub building this unit writes only inside `tui/`.

## Layout

    tsb_tui/app.py              the Textual App: one client, a stack of screens
    tsb_tui/_transport.py       the handful of tsbc-private transport exceptions this client needs
    tsb_tui/screens/login.py    TSB's own login (decision 5.34): username + password
    tsb_tui/screens/new_session.py   target id, connection choice
    tsb_tui/screens/attach_confirm.py  recording ack, host-key ack — every attach passes through it
    tsb_tui/screens/terminal.py      attach, keys -> bytes, resize, WS close -> banner
    tsb_tui/screens/sessions.py      every session the principal may see; re-attach
    tsb_tui/render.py           pyte screen state, fed by the attached byte stream (Contract §11:
                                the grid is the broker's; this client owns all rendering)
    tsb_tui/smoke.py            the non-interactive path the deploy hook drives after a deploy
    tsb_tui/__main__.py         `tsb-tui`'s entry point
    tests/stub_server.py        a minimal, slow, chunked C1/C2 stub (test scaffolding, not a
                                second fixture server) — built on `tsbc._wsframe`, the same codec
                                `tsb_contract_client` uses
    tests/test_flow_pilot.py    the Textual pilot through the whole E0 journey against the stub
    tests/test_attach_and_close.py   attach failures, close codes and connection loss
    tests/test_terminal_keys.py      the key encoding table, and the keys Textual reserves by default
    tests/test_attach_confirm.py     the recording and host-key acknowledgement gate
    tests/test_smoke.py              the non-interactive path's own exit codes

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

Detach with **F9** from the terminal screen (shown in the footer) — the only key that screen
reserves for itself; the command palette is off and every other key, including quit, reaches the
target instead. Detaching leaves the session running, `detached`, and the Sessions list shows it
that way; re-attach from there.

## Install

`tui/pyproject.toml` declares `[project.scripts] tsb-tui`, so `pipx install ./tui` gives a working
`tsb-tui` script — except the contract client, which PEP 508 cannot name by a relative path (see
the comment in `pyproject.toml`). Put it in the same venv with:

    pipx inject tsb-tui ./contract

`deploy/manage.sh up`'s post-deploy hook does exactly this, for the operator who ran `sudo`: it
installs `tsb-tui`, injects the contract client, then runs `harness/rigs/smoke.sh` and
`tsb-tui --smoke` against the instance it just brought up.

## Tests

    pip install -e ../contract -e ".[test]"   # from tui/, or from the repo root with the paths
                                                # .github/workflows/test.yml uses
    pytest tests/

The pilot test (`tests/test_flow_pilot.py`) runs the whole E0 journey against a slow, chunked
stub and asserts the app stays responsive (a keystroke is handled while output is still arriving)
— see its module docstring for why that property, not just the byte count, is what is graded. The
other test files exercise attach failures and close codes, the key-encoding table (including the
keys Textual reserves for its own command palette and quit by default), the recording/host-key
acknowledgement gate, and the smoke path's own exit codes.
