# AGENTS.md — tsb-tui

*These rules apply to every task in this project unless explicitly overridden. Bias: caution over speed on non-trivial work. Use judgment on trivial tasks.*

0. **A question is not an authorization.** When Tu asks how, what, whether, or what we should or have to do, answer
   and stop. Act only on an explicit instruction to act. No agent or sub-agent authorizes an action on Tu's behalf, and
   an answer never launches the work it describes (Tu, 2026-10-24).
1. **Think before coding.** State assumptions. Inspect first (files, git, live state); ask only what those cannot answer, with your recommended default; how many questions and when is your call. If a request has several plausible readings, name them and say which you took. Push back when a simpler approach exists. If still confused after inspecting, stop and name what is unclear.
2. **Simplicity first.** Minimum code that solves the problem. Nothing speculative: no features beyond what was asked, no abstractions for single-use code.
3. **Surgical changes.** Touch only what you must. Don't improve adjacent code. Match existing style. Remove only the imports and code your own change orphaned; mention unrelated dead code, don't delete it.
4. **Goal-driven execution.** Before you start, define the check that proves it works (a test, build or command you can run). Loop until it passes and show its output.
5. **Use the model only for judgment calls.** Classification, drafting, summarisation, extraction. Routing, retries, status-code handling and deterministic transforms are code. If code can answer, code answers.
6. **Spend context deliberately.** Read only what the task needs. Bulk reading or searching goes to a sub-agent that returns a summary, not the files. When the context gets heavy, summarise into the ledger and hand off. This rule is about your own reading and chatter, never about the size or format of the code you write: source stays readable. Do not report token budgets or breaches.
7. **Surface conflicts, don't average them.** Pick one (more recent, more tested), say why, and flag the other in your report.

   > **Carve-out — this rule stops at the architecture.** It governs *code* patterns. It does **not** apply to
   > the corpus (`program/srs/` at the pinned release) or to a published contract clause. Two frozen clauses in conflict is a BLOCKER:
   > report it, name both clauses, and stop. Never pick a winner between them and never explain your way
   > past one in a commit message — resolving the corpus is the operator's, in the vault, and the program
   > plan's §6.3 rule 7 says exactly this. Two such conflicts were live in this repo's path in September
   > 2026 and a sub obeying the rule above would have silently decided a wire format.
8. **Read before you write.** Read exports, callers and shared utilities first; if unsure why code is shaped a certain way, check its history (git log/blame) before asking.
9. **Tests verify intent, not just behaviour.** A test that cannot fail when the business logic changes is wrong.
10. **Checkpoint after every significant step** in the ledger or handoff: done, verified, left. A checkpoint is not a commit and not a message to Tu. If you lose track, rebuild the state from the ledger, files and git, write it down, and continue.
11. **Match the codebase's conventions,** even if you disagree; surface a harmful one, don't fork it silently.
12. **Fail loud.** "Completed" is wrong if anything was skipped; "tests pass" is wrong if any were skipped. Say what you did not verify. An orchestrator treats a sub-agent's "done" as a claim until it has checked.

---

# Agent Instructions

## FILE STRUCTURE

Read these files at session start:
- `README.md` - Repo overview, source hierarchy, and current run/build instructions
- `.aiworkflow/WORK_TRACKER.md` - Active + paused work only
- `.aiworkflow/MANIFEST.md` - Project-wide status
- `.aiworkflow/SESSION_{branch-slug}.md` - Last session state for your branch (if exists)
- repo requirement/context docs - Read-only. Examples include `specs/*.md` if present, or whatever source-of-truth docs the repo currently uses.

If a referenced file or directory does not exist, skip it and continue — **except the sibling
`../tsb-program/` repo and the `program/` submodule**, which are not optional. See "The program lives in a sibling repo" below:
without it you cannot read the architecture that binds this repo, so clone it rather than
continue past it.

## WORKING MODE

This repo uses one workflow: direct work.

**Workflow**
1. **Run `python .aiworkflow/rotate-work-tracker.py`** at session start
2. Break down work into steps (write this down!)
3. Add entry to `WORK_TRACKER.md` Active section (see template below)
4. Work through steps, mark `[x]` as you complete them
5. Update `SESSION_{branch-slug}.md` every 15 minutes
6. When all complete:
   - Update `MANIFEST.md` WS matrix status if it implements a requirement
7. **Run `python .aiworkflow/rotate-work-tracker.py`** at session end

---

## MANIFEST.md RULES

MANIFEST is the **project-wide status board**. It answers: "what's the state of the whole project?"

### Allowed content:
- Active branches + PRs (one line each)
- WS feature matrix (one line per WS: status + name + branch)
- Known issues / deferred tech debt (things that affect future work)
- Schema alignment summary (collection count + validation status)
- Implementation artifact references (key file paths, doc links)

### BANNED from MANIFEST — put it in plan.md instead:
- Per-phase progress details ("Phase 1: DONE, Phase 2: IN PROGRESS...")
- Effort tracking / time estimates ("Phase 1: 1.5h estimated / 2h actual")
- Step-by-step implementation logs
- "Next Steps" that duplicate what's in the plan file
- Commit hashes for individual phases
- Test count updates per phase (just keep one global "Tests: X passing")

### Update frequency:
- WS matrix status: when a WS changes state (started, paused, completed)
- Known issues: when discovered or resolved
- Schema: when schema changes
- Add `**Last-Updated:** {ISO timestamp}` header

---

## WORK_TRACKER.md RULES

WORK_TRACKER is a **permanent ledger** with a 3-layer queue: Active (hot), Log (warm, max 30), Archive (cold). Stubs are never deleted — only moved between layers by the rotation script.

### Active entry template (max 30 lines):
The stub block (between `BEGIN/END STUB` markers) is embedded in each Active entry. The agent maintains it as part of normal work. The script extracts it mechanically on compression — no separate stub-writing step needed.
```markdown
## Active — {Title} | {YYYY-MM-DD}
<!-- BEGIN STUB: {YYYY-MM-DD} | {Title} -->
### {Title} | {YYYY-MM-DD}
**Meta:** {branch} | {model-id} | `{plan-link}`
**Outcome:** {current status}
**Key:** {decisions made, comma-separated}
**Pending:** {open items or none}
<!-- END STUB: {YYYY-MM-DD} | {Title} -->
- [x] Decision made
- [ ] Decision pending
```

### Agent rules (CRITICAL):
1. **Run `python .aiworkflow/rotate-work-tracker.py`** FIRST at session start
2. **APPEND your new `## Active` entry** — do NOT touch, edit, or delete any existing entries
3. **Update ONLY your own stub block** as you work (Outcome, Key, Pending lines)
4. **Run the script again** at session end
5. **NEVER delete, move, or compress anything** — that is the script's job, not yours

### What the script does (idempotent, safe to run anytime):
1. **COMPRESS**: if multiple `## Active` entries exist, keeps newest, moves older stub blocks to `## Log`. The agent does NOT decide what to compress.
2. **ROTATE**: if stubs in `## Log` exceed 30, moves oldest to archive.

### BANNED from WORK_TRACKER:
- Verbose step-by-step implementation logs (→ plan.md)
- Per-file change lists (→ git log)
- Postmortems or "what went wrong" narratives (→ plans/reports/)
- CSS property lists, test output details (→ plan.md or commit messages)

### File structure:
```markdown
# WORK_TRACKER
**Last-Updated:** {ISO timestamp}

## Active — ...       ← full entries with embedded stub blocks
## Paused             ← full entries or "None"
## Log                ← stub blocks only (warm queue, max 30)
```

---

## SESSION FILES

Session files are **ephemeral, branch-scoped** snapshots of in-progress work.

### Naming: `SESSION_{branch-slug}.md`
- Example: `SESSION_ws5-local-infra.md`
- Example: `SESSION_ws32-edit-fields-fix.md`
- Convention: strip `feat/` prefix from branch name

### Lifecycle:
- Created/overwritten at session start
- Updated every 15 minutes during work
- Stale after 2 hours of no `Last-Updated` change — next agent may ignore it

### Template (~20 lines max):
```markdown
# Session — {branch}
**Model:** {model-id} | **Started:** {ISO timestamp}
**Last-Updated:** {ISO timestamp}

## Current Step
{what you're doing right now}

## Files Being Modified
- {path}:{line-range} — {what}

## Background Tasks
- {task-id}: {description} — {status}

## Next Action
{what comes after current step}
```

### Why branch-scoped (not agent-scoped):
- Branch = work stream. Any agent can pick up where the last one left off.
- Parallel branches = parallel session files = no merge conflicts.
- "SESSION_sonnet.md" is ambiguous (which sonnet? which task?). "SESSION_ws5-local-infra.md" is clear.

---

## UPDATE RULES

**SESSION_{branch}.md:** Every 15 minutes while working
**WORK_TRACKER.md:** When starting task, completing steps, completing task
**MANIFEST.md:** When WS status changes (not per-phase, per-WS)

## STATE UPDATE RULES

Update these files explicitly when relevant:
- `WORK_TRACKER.md` when starting, pausing, resuming, or completing meaningful work
- `SESSION_{branch-slug}.md` during active work and before handoff
- `MANIFEST.md` only when project-level status, architecture, or known issues change

Do not assume shorthand requests imply these updates automatically unless the user explicitly asks for them.

## AI ATTRIBUTION (MANDATORY)

Every agent MUST self-identify in all artifacts:
- **Git commits** — add trailer: `AI-Model: {model-id}` (e.g., `claude-opus-4-6`, `codex`, `gemini-2.5-pro`)
- **WORK_TRACKER.md** — include `**Model:** {model-id}` field on every entry
- **PR descriptions** — include `**AI-Model:** {model-id}` line in summary

## CROSS-MODEL AWARENESS

Multiple AI models work on this repo. Before building on another agent's work:
1. Check `git log --oneline -10` for recent changes and who made them
2. If a key file changed significantly (>50% size change), investigate before continuing
3. Build-verify using the repo's standard verification command from `README.md` or package scripts after checkout/merge and before writing new code
4. If something looks wrong (missing UI, broken layout, unexpected state), check git history — don't silently "fix" what might be a regression from another agent

## MERGE CONFLICT RESOLUTION

- **NEVER** wholesale "keep theirs" or "keep ours" on files >50 lines
- Always review conflicts line-by-line and reconcile both sides
- Build-verify after every merge
- Document conflict resolution in commit message (what was kept from each side)

## CREDENTIALS SAFETY

- **NEVER** commit files containing plaintext secrets (API keys, client secrets, passwords)
- Check `git diff --cached` for secrets before committing
- If you find credentials in tracked files, warn the human — do not commit

## SUBAGENT / DELEGATION POLICY

When delegating to subagents, set the model or reasoning tier explicitly when the runtime supports it.

Use:
- small/fast models for file search, grep, simple reads, and test-output parsing
- medium models for implementation, debugging, and code review
- top-tier models for architecture, hard refactors, and ambiguous design problems

Rules:
- when unsure, use the middle tier first
- top-tier is allowed when the problem genuinely requires synthesis or hard design work
- do not use top-tier models for bulk file reads, simple grep, test running, or straightforward CRUD

**Who writes what** (operator, 2026-10-03). The files that steer the work are the orchestrator's alone,
under the operator's rulings: unit plans and their phase files, rulings and decision records
(`plans/reports/rulings-*`; in the program repo `decisions.md` and the spine), the ledgers (`.aiworkflow/`), this
file, and every brief. A planner sub may draft a plan for the orchestrator to review; a coding lane never writes
or alters one, not even a checkbox.

**Coding lanes write code.** opencode, agy and the Sonnet or Opus builders write code, tests and scripts in the
directories their brief names, plus their own report under `plans/reports/`. The orchestrator hands them the
hands-on work by size rather than doing it itself:
- small and fully specified (a review nit, a script tweak, a pin bump) → opencode or agy;
- medium, or anything that needs code read first → a Sonnet sub in its own sibling worktree;
- judgment, design or red-team → an Opus sub.

**A brief's guardrail is not a control:** some lanes ignore it. Before a lane's work leaves the machine, the
orchestrator checks its diff against the paths the brief allowed (`git diff --name-only`) and throws away any
change outside them, above all to a plan, a ruling, a ledger, the program submodule `program/` or this file.

**A design gap stops a lane** (operator, 2026-10-11). A lane that meets a design decision its plan, phase file and
the contract leave unspecified — a behaviour, a value, an ordering, an error path — stops and reports it with the
options. It never chooses. The decision comes back from the orchestrator or the operator before the work goes on.
Every brief that sends a lane to write code says so in one line ("If the plan leaves any design decision
unspecified, STOP and report the options; never choose one yourself"); the orchestrator's dispatch hook refuses a
brief without it. The reason is on record: in v0.3 a builder filled such a gap the cheap way, cutting recorded bytes
from disk to keep one counter true (tsb-program #64 fixes it), against a LOCKED clause.

**Contract and core semantics are designed before they are built.** The recording, continuity, restart, the
handshake, audit and every wire format are designed by the orchestrator or an Opus planner and red-teamed before a
builder touches them; builders implement a specified design, never derive one.

## CRITICAL RULES

1. **NEVER modify requirement/context source docs unless explicitly asked** - Treat them as read-only by default
2. **ALWAYS write a task breakdown for non-trivial direct work** - Or it's lost on connection drop
3. **UPDATE SESSION file every 15 minutes while actively working** - Non-negotiable
4. **Read WORK_TRACKER.md at session start** - Continue from there
5. **NEVER put phase-level detail in MANIFEST** - Use plan files
6. **NEVER leave completed work in WORK_TRACKER** - Archive it monthly
7. **Do not preserve parallel obsolete architectures longer than necessary** - Delete superseded render paths after parity is verified
8. **NEVER hand-write schema/contract definitions from scratch** - Read the project's authoritative schema source first (here: `program/contract/`, at the release this branch pins — see the TSB section below). Hand-writing caused a ghost alias bug (2026-03-30) that silently dropped data for a full session.

---

# TSB — repo-specific context (read after the rules above)

This repo is `tsb-tui`: the Terminal Session Broker's first-party terminal surface, extracted from tsb-core's `tui/`
with its history on 2026-11-04 (cut tsb-core `25d10226`; the README says how the history was rewritten). It is a
consumer of the broker: it reaches the core only through the published contract client, as any surface does
(Contract §2, §3). The program plan and the per-lane development plan live in `../tsb-program/spine/`; the TUI's
capabilities by status are `../tsb-program/spine/track-tui.md`. **A sub never cooks from the master plan**, only from a
unit plan the orchestrator writes for one unit and one epoch.

## Units this repo owns (`plan.md` §2)

| Dir | Unit | Binding |
|---|---|---|
| `tsb_tui/`, `tests/`, `bench/` | U7 — the TUI: a first-party shipped surface and the core's verification instrument | imports only the published contract client (the import lint) |
| `packaging/` (when it lands) | U12 — client builds: the TUI as one zip per OS, built on Linux | `../tsb-program/spine/track-client-builds.md` |

`program/` — tsb-program as a sparse submodule: `program/srs/` the corpus, `program/contract/` the contract, at a pinned
release; read-only here.

## The program lives in a sibling repo

`../tsb-program/` holds the architecture and the plan: `spine/` (`plan.md`, `development-plan.md`, the phase and track
files) and `decisions.md`. `program/decisions.md` and `program/AGENTS.md` are never read. Where it runs is
`../tsb-program/ops/`, which is never copied into here. If the sibling is missing, clone it before doing anything else.

## Frozen inputs

`program/srs/architecture-contract.md` §1–§15 and every LOCKED item in `program/srs/open-decisions.md` bind this repo.
Work that would need one changed is BLOCKED: stop and report the clause. An OPEN item is a blocker, a DEFERRED one gates
nothing. Read `program/srs/` at the pinned release, never the vault and never a summary.

## Contract discipline

- `make contract-init` populates `program/` at the release this branch pins (`tools/contract-pin.sh`), in every clone
  and worktree; `make contract-check` proves the pin. A bump is its own PR: inside `program/` fetch the release tag and
  check it out detached, stage `program`, run both targets, commit the moved gitlink. Never `submodule deinit`,
  `sparse-checkout add`, or `git show` inside `program/`; never edit inside it.
- Read the protocols the TUI drives (`program/contract/protocols/`: `README.md`, `conventions.md`, the edge `SF-CO`
  and its flows) before their schemas and fixtures. Never hand-write a schema or a fixture from memory.
- A contract change is a PR to tsb-program, never part of a TUI PR.

## The engine

libghostty-vt is vendored under `vendor/libghostty-vt/` at tsb-core `vt/`'s PIN, with its headers and licences, and
`tools/check-engine.sh` (run by `make lint`) refuses a file that does not match `SHA256SUMS` and the PIN.
`TSB_TUI_TERM_LIBRARY` points the TUI and the tests at another build. The client builds track's per-target builds
replace this copy.

## Boundaries enforced by CI

- `tools/import_lint.py`: `tsb_tui` imports only the standard library, its `pyproject.toml` dependencies (with `rich`,
  Textual's renderer) and the contract client (`tsb_contract_client`, `tsbc`).
- The leak check (`.github/scripts/leak-check.sh`), the docs-branch guard, gitleaks (8.24.3 in CI, 8.30.0 pre-commit).
- `make test` runs with `NO_COLOR` unset: the colour assertions must not depend on the caller's environment.

## Environment

**Not here.** Node addresses, hostnames and trust between machines are `../tsb-program/ops/environment.md`'s and are
never copied into this repo, which may become public. Runtime state is node-local.

**Where a sub tests.** Unit tests use the contract's fixture server and test doubles, never a real broker or sshd on the
orchestrator's workstation, which is shared and is not a target. End-to-end proofs through the TUI run from tsb-core's
harness against the lab and staging. A local terminal experiment runs a throwaway non-login shell with history off on
a private tmux socket, killed afterwards.

## Commits

Conventional message; add files by explicit path; trailer `AI-Model: <model-id>`; no co-authored-by. PRs required on
`main`, merged one at a time. Qodo reviews every PR but docs-only ones (`.pr_agent.toml`: `plan/…`, `chore/ledger…`,
`docs/…`, `research/…` branches carry plans, ledgers or notes only, and merge on green CI; the docs-branch guard fails
one that touches anything else). Licensing is undecided (`../tsb-program/spine/plan.md` §5.3): no LICENSE file, and
the repo stays private until it is.
