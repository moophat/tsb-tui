# tui/bench — the TUI's benchmarks

Three scripts that measure what the design's acceptance tests 4 and 6 (§3.4) ask for. They are run by the orchestrator on
the integration node, with no post-merge loop running, and never on a shared workstation: none is a rig (there is no
target and no sshd; each starts the contract's fixture server, `tsbc._serve`, as a child process of its own), but a
firehose on a loaded machine measures the machine. They import only the stdlib, `tsb_tui` and `tsbc` (and `textual`, which
`tsb_tui` itself imports), so the import-lint passes them as shipped code. Each has `--tiny`, which
`tests/test_bench_smoke.py` runs in CI so that none rots between node runs.

## What they need

* the `tsb-tui` venv (Textual 8.2.8) and `program/` populated at the pinned release (`make contract-init`), run from the
  repository root;
* the engine, `libghostty-vt.so`, from the same build the deploy installs: `--library <path>` (the default is the
  repository's `vt/third_party/libghostty-vt/lib/libghostty-vt.so`, the build step's output).

## The commands

Each runs under `systemd-run` so it is a unit of its own that ends with its script (placeholders in angle brackets):

    systemd-run --wait --collect --pipe -p WorkingDirectory=<repo> \
        <venv>/bin/python tui/bench/engine_read.py --library <so>

    systemd-run --wait --collect --pipe -p WorkingDirectory=<repo> \
        <venv>/bin/python tui/bench/paint.py --library <so>

    systemd-run --wait --collect --pipe -p WorkingDirectory=<repo> \
        <venv>/bin/python tui/bench/fleet_scale.py --library <so>

Each prints a table and a last line `PASS` or `FAIL`, and exits 0 or 1. A miss is not met by reporting it: the numbers go
to the operator, with the fallbacks the plan's engine-read go/no-go names.

## engine_read.py — the go/no-go (plan R4)

A scrolling frame dirties the whole grid, so a firehose re-reads every cell of a 196x59 grid on the loop that also
answers pings. It feeds a generated stream to an in-process engine and times the read. **Gate:** the 196x59 full-grid read
p95 is at most 15 ms. `--varied` is the worst case (every cell redrawn with random glyphs and styles).

It then prints the **feed rate**: megabytes a second through `ghostty_terminal_vt_write` for the `yes` and `tech`
streams (8 KiB chunks) at each grid, with one thread and with three threads each feeding a terminal of its own, and how
far three scale (`x one thread`). Nothing is gated: the rate says whether any TUI design can keep up with the firehose,
and a scaling well under 3 on free cores says the engine serialises separate terminals. `--feed-mb` (default 64) is the
megabytes fed per thread for each row; `--no-feed` skips the rows.

## paint.py — keystroke to screen (design §3.4 test 4)

Starts the real TUI headless against the fixture server in a child process, with three sessions attached (the one in
front, two behind, all receiving output), the sidebar open and polling every second, and a ping from the server every
250 ms. A keystroke is an uppercase letter the stream never contains, posted to the terminal screen; the server echoes it as
output; the script takes the time of the letter reaching the engine's grid, the canvas rebuilding its rows and Textual
first drawing a line of the canvas. One row per (grid, stream): `116x39` and `196x59`, each under a `yes`-like stream
(the same short line, as fast as the rate allows) and a long `show tech-support`-like one (lines of mixed length, some
coloured). **Gate:** p95 of the whole journey under 50 ms in every row, no keystroke lost, every WebSocket ping answered
(session-detach 9) and no attachment dropped and reattached during the run.

| Option | Default | Means |
|---|---|---|
| `--sizes` | `116x39,196x59` | the grids; the window is the grid plus the rail and the sidebar |
| `--streams` | `yes,tech` | which streams |
| `--keys`, `--spacing-ms` | 200, 50 | keystrokes timed per row, the pause after each is drawn |
| `--mb-per-s` | 4 | output per session, megabytes a second; raise it to see where it breaks |
| `--poll-s` | 1 | the sidebar's poll interval (`fleet_view.refresh_s`) |

Read the tables. The first has one row per scenario: `stall ms` is the longest time the event loop was kept from
running anything else, `pongs` is pings answered over pings sent, `queue MB` the deepest any client's server-side queue
got. The second splits the keystroke's journey into stages, p50 / p95 ms each, so a miss says where the time went:
`key->server` (the key to the server), `server queue` (the server's own), `echo->read` (the echo's wait in the socket
and the pump, the stamp being the moment the pump hands the frame to the screen), `read->feed` (queued before the engine
write), `engine write`, `feed->paint` (the canvas's paint tick), `paint->draw` (Textual); `echo->screen` is all that
the TUI does with a frame once it has arrived, and `key->screen` is the gate's number. The server's stamps share the
machine's monotonic clock with the client's.

**The server models the core**: one queue and one writer thread per client, so delivery never blocks and a slow
client delays nobody else's echo or pings; a client whose queue passes 8 MiB is closed with 4011; sockets are
`TCP_NODELAY` as the core's are; and the server stops pinging when the measured window ends, before the TUI shuts
down, so pings the closing TUI never read are not counted. Only pings sent more than a second before that are judged. What the number leaves out: the headless driver does not write the final bytes to a terminal,
so the outer terminal's own draw is not in it; the server is local, so the network is not.

## fleet_scale.py — a fleet of 500 (design §3.4 test 6)

Lists 500 sessions (and the one attached), filters them in the sidebar and in the full view a character at a time (three
words typed and deleted again), and types into the attached session while a `GET /sessions` that the server holds back for
250 ms arrives every second. **Gate:** the filter's p95 from keystroke to the first line drawn, under 100 ms in both views;
typing under polls p95 under 50 ms; at least one poll applied while typing was timed; the sessions listed are all there.
It also reports the time each poll took to apply on the loop and the longest stall.

| Option | Default | Means |
|---|---|---|
| `--sessions` | 500 | sessions the core lists |
| `--window`, `--grid` | `120x40`, `80x24` | the window and the attached session's size |
| `--keys` | 100 | keystrokes timed while the poll runs |
| `--poll-s`, `--poll-delay-ms` | 1, 250 | the poll interval and how long the server holds each poll back |

## Snapshots

The screens the design draws are exported by `tests/test_snapshots.py`, not by a benchmark: run it with
`TSB_TUI_SNAPSHOT_DIR=<dir>` and every state is written there as `<id>-<cols>x<rows>.txt` and `.svg` for review against the
mockups.
