# Build Report: Publishing Preparation (tsb-tui)

**Date:** 2026-11-25  
**Branch:** `feat/contract-copy`  
**Status:** Complete (pending orchestrator secrets and settings)

---

## 1. Overview

This report covers implementation of steps 2, 3, 4, and 6 for publishing preparation in `tsb-tui`.
Steps 1 (pin bump PR) and 5 (GitHub repository settings) were excluded per instructions.

All operations were executed offline against fixtures and test doubles without external network access or broker dependencies.

---

## 2. Step 2: The Contract Copy

### Changes Made

- **`tools/contract-pin.sh`**:
  - Added `copy` mode: replaces `contract/` using `git -C program archive --prefix=contract/ HEAD:contract | tar -x` and writes `contract.pin` recording the release tag, commit hash, and contract tree hash.
  - Added `copy-check` mode: verifies offline that `contract.pin` matches the git index tree for `contract/`, verifies `contract/CONTRACT_VERSION` matches `contract.pin` tag, and ensures working tree matches the index.
  - Enhanced `check` mode: verifies index tree equals submodule contract tree and `contract.pin` tree, validates gitlink alignment, and enforces index equals `HEAD` when `CONTRACT_COMMITTED=1`.
- **`Makefile`**:
  - Added `.PHONY` targets `contract-copy` and `contract-copy-check`.
  - Updated `test` target dependency from `contract-check` to `contract-copy-check` for standalone operation without `program/`.
- **`contract.pin`**:
  - Generated at pinned release `v0.18.0` with `commit` and `tree` attributes.
- **`contract/`**:
  - Copied tree from pinned release containing protocols, schemas, fixtures, tests, tools, and `tsbc` library. Staged in index.
- **Repointed Readers from `program/contract/` to `contract/`**:
  - `tests/conftest.py`: imports `stub_server` and points fixture search to `contract/`.
  - `tests/test_contract_pin.py`: validates root `contract/` and `contract.pin`.
  - `tests/stub_server.py`: schema and fixture paths point to `contract/schemas` and `contract/fixtures`.
  - `tests/test_drive_send_gate_fixes.py`: fixtures path updated to `contract/fixtures`.
  - `packaging/tests/test_packaging.py`: schema search path updated to `contract/schemas`.
  - `pyproject.toml`: reference comment updated to `contract/`.
  - `bench/README.md`: fixture references updated to `contract/`.
  - `.github/scripts/docs_branch_guard.py`: treats `contract.pin` and `contract/` as code to prevent modification in docs branches.
  - `.github/scripts/tests/test_docs_branch_guard.py`: tests verifying rejection of `contract.pin` and `contract/` changes in docs branches.

### Red / Green Demonstration

- **Red (Missing/Tampered Pin and Copy)**:
  - Invoking `make contract-copy-check` prior to generating `contract.pin` exited with code `1` (`contract-copy-check: contract.pin is missing`).
  - Tampering with a byte in `contract/` caused `make contract-copy-check` and `make contract-check` to fail with exit code `1` (tree hash mismatch).
  - Modifying `contract.pin` tag or commit caused `make contract-copy-check` to fail with exit code `1`.
- **Green (Verified Pin and Copy)**:
  - `make contract-copy-check` exits `0`: `contract-copy-check: contract/ matches contract.pin at v0.18.0`.
  - `make contract-check` exits `0`: validates index tree against submodule tree and pin.
- **Bump Verification Test (Acceptance 02-A0)**:
  - Implemented `.github/scripts/tests/test_contract_bump.py` running an isolated, network-free synthetic bump across two tags.
  - Tests step 1 through 7 in sequence and verifies failures when step 3 is skipped (exit 1), step 4 is skipped (exit 1), or commit is created before step 4 (exit 1). All 4 tests pass (exit 0).

---

## 3. Step 3: The Leak Guard

### Changes Made

- **`.github/scripts/leak-check.sh`**:
  - Removed hardcoded patterns from script source.
  - Pattern resolution hierarchy:
    1. Reads from `TSB_LEAK_PATTERN` and `TSB_LEAK_PUSH_PATTERN` environment variables (CI secrets).
    2. Reads from maintainer file path (`../tsb-program/ops/publish/leak-pattern`).
    3. Outside CI, outputs `SKIP: the environment check runs on the maintainers' side` and exits `0`. Inside CI, fails with exit code `1`.
  - CI output sanitised: outputs `file:line: environment detail` without printing matched line content.
  - Neutral guidance messages updated to avoid leaking internal directory names.
- **Workflow Security Enhancements**:
  - `.github/workflows/test.yml`: Added package cache address masking (`PACKAGE_CACHE_ADDR`); added PR repo guard condition (`github.event.pull_request.head.repo.full_name == github.repository`); passed `TSB_LEAK_PATTERN` to docker container for `lint`.
  - `.github/workflows/secrets.yml`: Added package cache address masking and PR repo guard condition.
  - `.github/workflows/release.yml`: Added package cache address masking.
- **Tests**:
  - `.github/scripts/tests/test_leak_check_prefixes.py`: Updated to pass synthetic pattern via `TSB_LEAK_PATTERN`.
  - `.github/scripts/tests/test_leak_check_push.py`: Updated to pass synthetic pattern via `TSB_LEAK_PUSH_PATTERN`.
  - `.github/scripts/tests/test_workflows.py`: Implemented workflow trigger and PR repo guard test verifying only permitted triggers (`push`, `pull_request`, `schedule`, `workflow_dispatch`) are used and PR jobs enforce the repo guard.

### Red / Green Demonstration

- **Red (Leak Detection & Missing Secret)**:
  - Planted synthetic pattern in a tracked file: `leak-check.sh` detected the pattern and exited with code `1`.
  - Running in simulated CI without `TSB_LEAK_PATTERN` exited with code `1`.
  - Planted `pull_request_target` trigger in workflow test caused test to fail with exit code `1`.
- **Green (Clean State)**:
  - Clean working tree with synthetic pattern exits `0`.
  - Outside CI without secrets, exits `0` with skip notification.
  - `.github/scripts/tests/test_workflows.py` passes all 4 checks (exit `0`).

---

## 4. Step 4: Outsider Text

### Changes Made

- **`README.md`**:
  - Added "Build from source" section detailing clean clone setup, Python 3.12 virtual environment creation, `pip install -e ".[test]"`, and `make lint test`.
  - Added "Contributing" section detailing pull request expectations, fork CI execution policy, and maintainer workflow.
  - Repointed contract client and fixture paths from `program/contract/` to `contract/`.
- **`AGENTS.md`**:
  - Added "Outside contributors" preamble at the top of the document.
  - Updated contract copy references and documented the revised 7-step pin bump procedure.

### Red / Green Demonstration

- Documentation and markdown links verified locally against existing structure.

---

## 5. Step 6: Contract Tarball on Release

### Changes Made

- **`.github/workflows/release.yml`**:
  - Repointed wheel build from `program/contract/` to root `contract/`.
  - Added build step invoking `contract/tools/build-tarball.sh` with `SOURCE_DATE_EPOCH` set from release commit timestamp in `program/`.
  - Included `tsb-*.tar.gz` in `sha256sum` manifest generation and GitHub release asset upload.
- **Tests**:
  - `tests/test_installers.py`: Added `tsb-1.tar.gz` to expected release assets for checksum coverage verification.
  - `.github/scripts/tests/test_release_install.py`: Added `TARBALL` payload to simulated release bundle fixtures.
  - `.github/scripts/tests/test_contract_tarball.py`: Added tests asserting tarball build naming (`tsb-v<VERSION>.tar.gz`), member manifest structure, and tarball verification via `python -m tsbc verify --self-check`. Also asserts byte-for-byte wheel equality between `contract/` and `program/contract/`.

### Red / Green Demonstration

- **Red (Missing Tarball)**:
  - `tests/test_installers.py` failed when `tsb-*.tar.gz` was added to `release.yml` sha256sum line without fixture alignment.
- **Green (Verified Artifacts)**:
  - `.github/scripts/tests/test_contract_tarball.py` passes all checks (exit `0`).
  - `.github/scripts/tests/test_release_install.py` passes all checks (exit `0`).
  - `tests/test_installers.py` passes all checks (exit `0`).

---

## 6. Gate Commands Executed

Each gate command was executed directly to a log file without pipes:

| Command | Exit Code | Notes |
|---|---|---|
| `make contract-check` | `0` | Verified submodule contract tree against `contract/` and `contract.pin` |
| `make contract-copy-check` | `0` | Verified index contract tree against `contract.pin` and `CONTRACT_VERSION` offline |
| `make lint` | `0` | Ruff, engine check, import lint, and 120 script tests passed |
| `make test` (`env -u NO_COLOR`) | `0` | Full test suite passed (2673 passed) |

---

## 7. Needs from the Orchestrator

The following secrets and repository settings must be configured by the orchestrator:

### 1. GitHub Actions Secrets
- **`TSB_LEAK_PATTERN`**: Pattern containing sensitive environment definitions for PR and branch leak checking.
- **`TSB_LEAK_PUSH_PATTERN`**: Pattern containing sensitive push-path definitions.
- **`PACKAGE_CACHE_ADDR`**: Internal package cache address to mask in CI runner logs (`::add-mask::`).

### 2. GitHub Repository Settings (Step 5)
- **Branch Protection on `main`**:
  - Require status checks to pass before merging: enforce `test` and `lint` jobs.
- **Fork Pull Request Approval Policy**:
  - Require maintainer approval for all outside collaborators before Actions workflows execute on pull requests from forks.

---

## 8. Anything Not Verified

- **Step 1 (Pin Bump PR)**: Pinned to `v0.18.0` as instructed; subsequent bump to fixed release will be performed via the orchestrator's PR.
- **Step 5 (GitHub API Settings)**: Live GitHub repository settings changes were not executed directly and remain the responsibility of the orchestrator.
- **Live GitHub Actions Cloud Runs**: Workflow runs were validated locally via unit tests and simulated runners, not on GitHub hosted runners.
