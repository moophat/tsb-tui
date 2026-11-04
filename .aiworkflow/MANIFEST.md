# MANIFEST — tsb-tui
**Last-Updated:** 2026-11-04T20:40:00+07:00

## Branches / PRs
- `main`: the TUI's history extracted from tsb-core's `tui/` (cut 25d10226); PRs required, one at a time.
- Open: `chore/repo-setup` (the repo's own gates).

## Status by unit
| Unit | Dir | Status |
|---|---|---|
| U7 TUI | `tsb_tui/`, `tests/`, `bench/` | moved from tsb-core with its history; until tsb-core's PR B lands, changes go here, never to tsb-core's `tui/` |
| U12 client builds | `packaging/` | not started here (`../tsb-program/spine/track-client-builds.md`) |

## Contract
`program/` pinned at v0.12.1, as tsb-core pins it.

## Known issues
- The engine is the Linux build only, vendored, until the client builds track's per-target builds.
- The CI deploy key on tsb-program is exposed to the PR-built image, as in tsb-web: accepted while the repo is private and only the orchestrator pushes.
