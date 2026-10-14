import os
import requests
from typing import Dict, Any

class GitHubService:
    def __init__(self):
        self.token = os.getenv("GITHUB_TOKEN")
        self.repo = os.getenv("GITHUB_REPO")
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json"
        }

    def create_pull_request(
        self,
        incident_id: str,
        patch_str: str,
        forensic_evidence: str,
        verification_summary: str
    ) -> Dict[str, Any]:
        if not self.token or not self.repo:
            return {"status": "skipped", "reason": "GitHub token or repo configuration missing"}

        branch_name = f"rca-fix-{incident_id}"
        pr_title = f"RCA Fix: Patch for Incident #{incident_id}"
        pr_body = f"""
## 🤖 Automated Root Cause Remediation

### 🔍 Forensic Evidence
{forensic_evidence}

### 🛠️ Verification Results
{verification_summary}

---
⚠️ **Human Approval Required**: Auto-merging is gated. Review before merging.
        """

        url = f"https://api.github.com/repos/{self.repo}/pulls"
        payload = {
            "title": pr_title,
            "head": branch_name,
            "base": "main",
            "body": pr_body,
            "draft": False
        }

        res = requests.post(url, headers=self.headers, json=payload)
        if res.status_code == 201:
            return {"status": "created", "pr_url": res.json().get("html_url")}
        else:
            return {"status": "failed", "error": res.text}