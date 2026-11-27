# Builder report: CI durations list and CI image layer cache

Date: 2026-11-27. Branch `ci/durations-and-image-cache`. Model: claude-sonnet-5-5.

## What I read about the runner

- `.github/workflows/test.yml`, `.github/ci/py.Dockerfile`, the grep of the repo for runner notes.
- Both jobs run `runs-on: [self-hosted, tsb-tui-ci]`; every step is a plain `docker build` / `docker run` against the
  runner's Docker daemon. The repo has no doc on the runner's internals (pod per job, Docker-in-Docker); that comes from
  the brief and the profile report, which found no `CACHED` layer in any log. So I assumed a fresh daemon per job and
  that nothing local survives.
- Only `actions/checkout@v4` is used today; no other third-party action.

## Mechanism chosen

buildx with the GitHub cache service (`type=gha`, `mode=max`, scope `ci-tui-py`), in both jobs, via
`docker/setup-buildx-action@v3` and `docker/build-push-action@v6` (`load: true`, tag `ci-tui-py`). It needs only repo
changes, keeps jobs, order, and the check names `test` and `lint`. The cache lives off the pod, so both jobs of one
run and later runs share layers.
Rejected: building once and passing the image between jobs (needs a third job or `needs:`, changes the job structure, a
~1 GB artifact); a registry (outside this repo).
Safety net: the buildx setup step is `continue-on-error`; if it fails (for example the pod cannot run the buildkit
container), the old plain `docker build` step runs instead, so the required checks still work.
The Dockerfile was reordered so the apt and PowerShell/Pester layers sit above `COPY tsb_tui`; before, any source change
invalidated the 42 s PowerShell download. Content and pins are unchanged. A source change now rebuilds only the pip layer
(about 14 s).

## Diff in two sentences

`test.yml`: the unit suite's `PYTEST_ADDOPTS` gains `--durations=40`, and each job's image build is now buildx with a gha layer
cache plus a plain-build fallback. `py.Dockerfile`: the pip install block moved after the PowerShell/Pester block, nothing else changed.
The "No test skips" grep does not match durations lines.

## Checks

- YAML parse of `test.yml` (python yaml): exit 0; jobs `test`, `lint` intact.
- `actionlint`: not installed; not run. Read the file by hand.
- `make lint`: exit 0.

## Unverified

- The workflow was not run; the first CI run on the PR is the proof.
- Whether the runner pod can run the buildx `docker-container` driver (pulls the buildkit image, needs the daemon to allow it
  and network egress to the registry). If not, the fallback step runs and nothing gets cached; look for "Build the Python CI image (no cache)" in the log.
- Whether the runner version exposes the cache service token to the action (cache service v2 needs a current runner).
  A cache export error appears as a warning in the build step.
- Total saving. The first run populates the cache. On the second run, look in the build step for `CACHED` on the apt, PowerShell and
  Pester layers and a build time of well under the old ~2:20. Also the Docker Hub pull rate for the buildkit image.
- Build context is the whole checkout (`fetch-depth: 0`, no `.dockerignore`); the buildx step sends it to the builder each
  time. Not changed (out of scope); a `.dockerignore` for `.git` would trim it.
- Does not address the `program/` double fetch (~36 s), by instruction.
- The durations list: look for "slowest 40 durations" at the end of the "Unit suite" step log.

Status: DONE_WITH_CONCERNS
Summary: Added `--durations=40` and a buildx gha layer cache (with plain-build fallback) to both jobs, and reordered the Dockerfile so the heavy layers cache. Lint and YAML parse pass; the cache's working on this runner is unverified until the first CI run.
