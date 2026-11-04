# Release workflow — 2026-11-04

**AI-Model:** codex
**Branch:** feat/release-wheel, starting at 16747bb

Status: DONE_WITH_CONCERNS — implementation and available local checks pass; actionlint is unavailable and the release workflow was not executed locally, as instructed.

## Completed steps and checkpoint

- [x] Inspect repository instructions, release ruling, test workflow, CI Dockerfile and packaging manifest.
- [x] Apply the operator's direct tag-check specification and 22:08 backend ruling.
- [x] Add release.yml and the exact setuptools backend pin to the CI image.
- [x] Parse YAML, check every run block's shell syntax and compile the embedded Python.
- [x] Confirm the three reused CI steps match test.yml exactly.
- [x] Run the requested make lint; audit the final paths and protected files.

No implementation work remains within the authorized scope. No further design gap was identified.
The previous missing-reference and backend-provisioning blockers are resolved by the operator's follow-ups.

## Changes

`.github/workflows/release.yml` triggers on pushed `v*.*.*` tags, on `[self-hosted, tsb-tui-ci]`.
Checkout uses `fetch-depth: 0` and `fetch-tags: true`. Before building or fetching the program, the job:

1. Requires the tag name to match `^v[0-9]+\.[0-9]+\.[0-9]+$`.
2. Requires `git cat-file -t refs/tags/<tag>` to print `tag`, refusing lightweight tags.
3. Fetches origin main, peels the tag to a commit and requires that commit to be an ancestor of origin/main.
4. Confirms checkout HEAD equals that tagged commit.

The CI-image build, deploy-key-isolated program fetch and program-pin initialization/check steps are
copied unchanged from test.yml. The read-only program deploy key is removed before project code runs.
The build container has `--network none`, `PIP_NO_INDEX=1`, and pip's version check disabled.
It stamps only the job checkout's `pyproject.toml`, requiring the source version to be `0.0.0`, then runs:

```sh
pip wheel --no-deps --no-build-isolation -w dist .
```

Stale dist/build/egg-info output is removed first. Exactly one wheel must exist; SHA256SUMS uses its
basename and is checked before upload. Existing package selection remains in force: only tsb_tui
and its declared package data are built, without bundling the engine or contract client.
`gh release create --verify-tag` uploads the wheel and SHA256SUMS. `GH_TOKEN` is supplied only to
that step; `contents: write` is granted only on the release job, with workflow-level contents read.
Runs for the same tag are serialized without cancellation. No helper script was needed.

`.github/ci/py.Dockerfile` gains only `setuptools==84.2.1` on its existing pip install line.
That is the current release reported by [PyPI's setuptools metadata](https://pypi.org/pypi/setuptools/json)
at verification and satisfies the project's >=69 floor and Python 3.12. No separate wheel package
is required: the pinned backend carries [wheel.wheelfile](https://github.com/pypa/setuptools/blob/v84.2.1/setuptools/_vendor/wheel/wheelfile.py)
and exposes its vendor directory through [setuptools initialization](https://github.com/pypa/setuptools/blob/v84.2.1/setuptools/__init__.py).
Backend installation happens during the existing network-enabled image construction; wheel construction
uses that image without network access, as the 22:08 ruling requires.

## Verification

- `python -c 'import yaml; ... yaml.safe_load(...) ...'` using the supplied venv: PASS.
- Every release run block passed `bash -n` via stdin: PASS.
- Embedded version-stamping Python compiled: PASS.
- Parsed comparison of image build, program fetch and program-pin check steps against test.yml: identical.
- `git diff --check`: PASS.
- `actionlint`: not installed on PATH, so not run.
- No helper script was added, so no helper test applies.

`PATH=<supplied-test-venv>/bin:$PATH make lint` exited 0:

```text
All checks passed!
============================== 51 passed in 3.94s ==============================
import-lint: clean (./ 164 files)
docs_branch_guard: no event, not pull_request — no-op
clean: no node addresses, dev hostnames, secret paths, or workstation/mount paths
engine-check: shared object and all headers verified; object matches PIN
```

No tests were skipped. The standalone docs guard was a no-op outside a PR event; its unit tests ran.
TMPDIR and pytest's basetemp were confined to this clone's temporary `.review/release-workflow-tmp/`,
removed afterward. The workflow, Docker image build, wheel build and release upload were not executed
locally. Local checks establish syntax and repository lint, not a successful GitHub release run.
The first v0.1.0 tag remains the orchestrator's action after merge.

## Scope

The only final changed/added paths are release.yml, py.Dockerfile and this report.
No application, tests, program submodule, AGENTS.md or ledger files changed. Source pyproject.toml
still declares 0.0.0. No commit, push, tag, branch change or clone Git configuration change was made.
The task's ledger-write prohibition overrides normal rotation/session requirements; this checklist
records the checkpoint instead.
