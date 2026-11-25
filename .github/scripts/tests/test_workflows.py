"""Workflow trigger and pull request security checks."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pytest
import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / "workflows"
ALLOWED_TRIGGERS = {"push", "pull_request", "schedule", "workflow_dispatch"}
REQUIRED_PR_CONDITION = "github.event.pull_request.head.repo.full_name == github.repository"


def _check_workflow(path: Path) -> list[str]:
    errors: list[str] = []
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return [f"{path}: not a valid workflow dictionary"]

    on = data.get("on") if "on" in data else data.get(True)
    if isinstance(on, dict):
        triggers = set(on.keys())
    elif isinstance(on, list):
        triggers = set(on)
    elif isinstance(on, str):
        triggers = {on}
    else:
        return [f"{path}: missing or invalid 'on' trigger specification"]

    unallowed = triggers - ALLOWED_TRIGGERS
    if unallowed:
        errors.append(f"{path}: trigger(s) {sorted(unallowed)} not permitted")

    if "pull_request" in triggers:
        jobs = data.get("jobs", {})
        if not isinstance(jobs, dict):
            errors.append(f"{path}: 'jobs' section is missing or invalid")
        else:
            for job_id, job in jobs.items():
                if not isinstance(job, dict):
                    continue
                condition = str(job.get("if", ""))
                if REQUIRED_PR_CONDITION not in condition:
                    errors.append(
                        f"{path}: job '{job_id}' lacks required pull request repo guard condition"
                    )

    return errors


class WorkflowSecurityChecks(unittest.TestCase):
    def test_repository_workflows_meet_trigger_and_guard_requirements(self):
        workflow_files = sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml"))
        self.assertTrue(workflow_files, "No workflow files found in repository")
        all_errors: list[str] = []
        for workflow_path in workflow_files:
            all_errors.extend(_check_workflow(workflow_path))
        self.assertEqual(all_errors, [], f"Workflow check failures: {all_errors}")

    def test_planted_pull_request_target_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "pr_target.yml"
            path.write_text(
                "name: unsafe\n"
                "on:\n"
                "  pull_request_target:\n"
                "jobs:\n"
                "  run:\n"
                "    runs-on: ubuntu-latest\n"
                f"    if: {REQUIRED_PR_CONDITION}\n"
                "    steps: [{run: true}]\n"
            )
            errors = _check_workflow(path)
            self.assertTrue(any("pull_request_target" in e for e in errors), f"Expected trigger error, got: {errors}")

    def test_planted_unpermitted_trigger_fails(self):
        for trigger in ("workflow_run", "issue_comment"):
            with tempfile.TemporaryDirectory() as tmp_dir:
                path = Path(tmp_dir) / f"{trigger}.yml"
                path.write_text(
                    f"name: {trigger}\n"
                    f"on: [{trigger}]\n"
                    "jobs:\n"
                    "  run:\n"
                    "    runs-on: ubuntu-latest\n"
                    "    steps: [{run: true}]\n"
                )
                errors = _check_workflow(path)
                self.assertTrue(any(trigger in e for e in errors), f"Expected trigger error for {trigger}, got: {errors}")

    def test_planted_pull_request_job_without_repo_guard_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "missing_guard.yml"
            path.write_text(
                "name: unguarded\n"
                "on: [pull_request]\n"
                "jobs:\n"
                "  unguarded_job:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps: [{run: true}]\n"
            )
            errors = _check_workflow(path)
            self.assertTrue(any("lacks required pull request repo guard condition" in e for e in errors),
                            f"Expected guard error, got: {errors}")


if __name__ == "__main__":
    unittest.main()
