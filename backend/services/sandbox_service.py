import os
import shutil
import tempfile
import subprocess
from typing import Dict, Any

class SandboxVerificationService:
    def __init__(self, timeout_seconds: int = 30):
        self.timeout = timeout_seconds

    def _create_disposable_copy(self, original_repo_path: str) -> str:
        temp_dir = tempfile.mkdtemp(prefix="rca_sandbox_")
        target_dir = os.path.join(temp_dir, "repo")
        shutil.copytree(original_repo_path, target_dir, dirs_exist_ok=True)
        return target_dir

    def _run_in_docker(self, repo_path: str, command: str) -> Dict[str, Any]:
        docker_cmd = [
            "docker", "run", "--rm",
            "-v", f"{os.path.abspath(repo_path)}:/workspace",
            "-w", "/workspace",
            "python:3.11-slim",
            "bash", "-c", f"pip install pytest -q && {command}"
        ]
        try:
            res = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=self.timeout)
            return {"exit_code": res.returncode, "stdout": res.stdout, "stderr": res.stderr}
        except subprocess.TimeoutExpired:
            return {"exit_code": -1, "stdout": "", "stderr": "Execution timed out"}
        except Exception as e:
            return {"exit_code": -1, "stdout": "", "stderr": str(e)}

    def verify_patch(self, original_repo_path: str, patch_str: str, test_file: str) -> Dict[str, Any]:
        temp_repo = self._create_disposable_copy(original_repo_path)

        try:
            pre_res = self._run_in_docker(temp_repo, f"pytest {test_file}")
            if pre_res["exit_code"] == 0:
                return {
                    "verified": False,
                    "reason": "Pre-patch test passed unexpectedly. Test case reproduction invalid."
                }

            patch_file_path = os.path.join(temp_repo, "candidate.patch")
            with open(patch_file_path, "w") as f:
                f.write(patch_str)

            apply_res = subprocess.run(
                ["git", "apply", "candidate.patch"],
                cwd=temp_repo, capture_output=True, text=True
            )
            if apply_res.returncode != 0:
                return {"verified": False, "reason": f"Patch application failed: {apply_res.stderr}"}

            post_res = self._run_in_docker(temp_repo, "pytest")
            if post_res["exit_code"] != 0:
                return {
                    "verified": False,
                    "reason": f"Post-patch tests/regressions failed: {post_res.stderr or post_res['stdout']}"
                }

            return {
                "verified": True,
                "reason": "Patch verified: Failed before fix, passed after fix without regressions."
            }

        finally:
            shutil.rmtree(os.path.dirname(temp_repo), ignore_errors=True)