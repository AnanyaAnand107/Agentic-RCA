"""Explicitly invoked GitHub PR publisher for already-verified patches.

This service works in a fresh temporary clone of GITHUB_REPO. It never creates
branches or commits in the RCA application's working directory.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()


class GitHubService:
    def __init__(self) -> None:
        self.token = os.getenv("GITHUB_TOKEN", "").strip()
        self.repo = os.getenv("GITHUB_REPO", "").strip().strip("/")
        self.base_branch = os.getenv("GITHUB_BASE_BRANCH", "main").strip() or "main"
        self.timeout = max(5, int(os.getenv("GITHUB_HTTP_TIMEOUT", "15")))
        self.api_url = f"https://api.github.com/repos/{self.repo}" if self.repo else ""
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def create_pull_request(
        self,
        incident_id: str,
        patch_str: str,
        forensic_evidence: str,
        verification_summary: str,
        repo_path: str | None = None,
        verification_passed: bool = False,
        human_approved: bool = False,
    ) -> dict[str, Any]:
        # Gate independently in the service; callers cannot bypass it accidentally.
        if verification_passed is not True:
            return {"status": "blocked", "reason": "Patch has not passed sandbox and regression verification."}
        if human_approved is not True:
            return {"status": "blocked", "reason": "Explicit human approval is required before publishing a PR."}
        if not self.token or not self.repo or "/" not in self.repo:
            return {"status": "not_configured", "reason": "Set GITHUB_TOKEN and GITHUB_REPO=owner/repository to enable publishing."}
        if not patch_str or "--- a/" not in patch_str or "+++ b/" not in patch_str or "@@" not in patch_str:
            return {"status": "invalid_patch", "reason": "A valid unified diff is required."}

        safe_incident = re.sub(r"[^A-Za-z0-9._-]", "-", incident_id)[:60] or "incident"
        branch_name = f"rca-fix/{safe_incident}"
        try:
            existing = requests.get(
                f"{self.api_url}/pulls",
                params={"state": "open", "head": f"{self.repo.split('/')[0]}:{branch_name}", "base": self.base_branch},
                headers=self.headers,
                timeout=self.timeout,
            )
            if existing.status_code != 200:
                return {"status": "failed", "reason": f"Could not check existing PRs ({existing.status_code}): {existing.text[:2000]}"}
            existing_prs = existing.json()
            if existing_prs:
                pr = existing_prs[0]
                return {"status": "exists", "pr_url": pr.get("html_url"), "pr_number": pr.get("number")}
        except requests.RequestException as exc:
            return {"status": "error", "reason": f"GitHub duplicate check failed: {exc}"}

        temp_parent = Path(tempfile.mkdtemp(prefix="rca_github_publish_"))
        try:
            clone_path = temp_parent / "repo"
            askpass = temp_parent / "git-askpass.py"
            askpass.write_text(
                "#!/usr/bin/env python3\nimport os, sys\nprompt = sys.argv[1].lower() if len(sys.argv) > 1 else ''\nprint('x-access-token' if 'username' in prompt else os.environ.get('GITHUB_TOKEN', ''))\n",
                encoding="utf-8",
            )
            askpass.chmod(0o700)
            env = os.environ.copy()
            env.update({"GITHUB_TOKEN": self.token, "GIT_ASKPASS": str(askpass), "GIT_TERMINAL_PROMPT": "0"})
            clone = subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", self.base_branch,
                 f"https://github.com/{self.repo}.git", str(clone_path)],
                capture_output=True, text=True, timeout=90, env=env,
            )
            if clone.returncode != 0:
                return {"status": "git_clone_failed", "reason": (clone.stderr or clone.stdout)[-4000:]}

            checkout = subprocess.run(["git", "checkout", "-b", branch_name], cwd=clone_path, capture_output=True, text=True, timeout=15, env=env)
            if checkout.returncode != 0:
                return {"status": "branch_failed", "reason": (checkout.stderr or checkout.stdout)[-3000:]}

            patch_path = temp_parent / "candidate.patch"
            patch_path.write_text(patch_str, encoding="utf-8")
            check = subprocess.run(["git", "apply", "--check", str(patch_path)], cwd=clone_path, capture_output=True, text=True, timeout=20, env=env)
            if check.returncode != 0:
                return {"status": "patch_failed", "reason": (check.stderr or check.stdout)[-4000:]}
            apply = subprocess.run(["git", "apply", str(patch_path)], cwd=clone_path, capture_output=True, text=True, timeout=20, env=env)
            if apply.returncode != 0:
                return {"status": "patch_failed", "reason": (apply.stderr or apply.stdout)[-4000:]}

            changed = subprocess.run(["git", "diff", "--name-only"], cwd=clone_path, capture_output=True, text=True, timeout=10, env=env)
            files = [line.strip() for line in changed.stdout.splitlines() if line.strip()]
            if changed.returncode != 0 or not files:
                return {"status": "empty_patch", "reason": "Patch produced no tracked file changes."}
            stage = subprocess.run(["git", "add", "--", *files], cwd=clone_path, capture_output=True, text=True, timeout=15, env=env)
            if stage.returncode != 0:
                return {"status": "stage_failed", "reason": (stage.stderr or stage.stdout)[-3000:]}
            commit = subprocess.run(
                ["git", "-c", "user.name=Agentic RCA", "-c", "user.email=agentic-rca[bot]@users.noreply.github.com",
                 "commit", "-m", f"fix(rca): verified candidate for incident {safe_incident}"],
                cwd=clone_path, capture_output=True, text=True, timeout=30, env=env,
            )
            if commit.returncode != 0:
                return {"status": "commit_failed", "reason": (commit.stderr or commit.stdout)[-3000:]}
            push = subprocess.run(["git", "push", "origin", branch_name], cwd=clone_path, capture_output=True, text=True, timeout=90, env=env)
            if push.returncode != 0:
                return {"status": "push_failed", "reason": (push.stderr or push.stdout)[-4000:]}

            body = (
                "## Automated RCA candidate\n\n"
                f"**Incident:** `{safe_incident}`\n\n"
                "### Root-cause evidence\n" + (forensic_evidence or "Not provided") + "\n\n"
                "### Verification\n" + (verification_summary or "Sandbox and regression checks passed.") + "\n\n"
                "### Review requirements\n"
                "- [x] Candidate passed isolated reproduction and regression checks before publishing.\n"
                "- [ ] Human review completed.\n\n"
                "This PR is not automatically merged. Review the diff and test evidence before merging."
            )
            response = requests.post(
                f"{self.api_url}/pulls",
                json={"title": f"fix(rca): incident {safe_incident}", "head": branch_name, "base": self.base_branch, "body": body, "draft": False},
                headers=self.headers, timeout=self.timeout,
            )
            if response.status_code not in (200, 201):
                return {"status": "pr_creation_failed", "reason": f"GitHub API {response.status_code}: {response.text[:3000]}", "branch": branch_name}
            data = response.json()
            return {"status": "created", "pr_url": data.get("html_url"), "pr_number": data.get("number"), "branch": branch_name}
        except subprocess.TimeoutExpired as exc:
            return {"status": "git_timeout", "reason": f"Git operation timed out: {exc.cmd}"}
        except requests.RequestException as exc:
            return {"status": "github_api_error", "reason": str(exc)}
        except Exception as exc:
            return {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
        finally:
            shutil.rmtree(temp_parent, ignore_errors=True)
