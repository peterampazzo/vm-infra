#!/usr/bin/env python3
"""Fail if a workflow could let pull requests reach the self-hosted runner on the VM.

Rules:
  * no workflow may use pull_request_target (it skips the fork approval gate);
  * a workflow with any job that isn't on a GitHub-hosted runner may only be triggered by workflow_dispatch.

This only catches our own mistakes: a fork can edit this file too. The real gate is the repo setting
"Require approval for all external contributors" (docs/RUNNING.md, step 5).
"""
import pathlib
import re
import sys

import yaml

GITHUB_HOSTED = re.compile(r"^(ubuntu|windows|macos)-[\w.-]+$")


def triggers(workflow):
    on = workflow.get("on", workflow.get(True))  # YAML 1.1 reads a bare `on:` key as True
    if isinstance(on, str):
        return {on}
    return set(on or [])


def github_hosted(job):
    runs_on = job.get("runs-on")
    labels = [runs_on] if isinstance(runs_on, str) else runs_on
    return isinstance(labels, list) and len(labels) == 1 and bool(GITHUB_HOSTED.match(str(labels[0])))


def main():
    errors = []
    for path in sorted(pathlib.Path(".github/workflows").glob("*.y*ml")):
        workflow = yaml.safe_load(path.read_text()) or {}
        on = triggers(workflow)
        if "pull_request_target" in on:
            errors.append(f"{path}: pull_request_target is not allowed")
        for name, job in (workflow.get("jobs") or {}).items():
            if not github_hosted(job) and on != {"workflow_dispatch"}:
                errors.append(f"{path}: job '{name}' runs on the self-hosted runner, so the only allowed "
                              f"trigger is workflow_dispatch (found: {', '.join(sorted(map(str, on)))})")
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
