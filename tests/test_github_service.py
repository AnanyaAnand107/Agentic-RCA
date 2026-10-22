from backend.services.github_service import GitHubService


def test_github_publish_requires_verified_patch(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")
    service = GitHubService()
    result = service.create_pull_request(
        incident_id="incident-1",
        patch_str="--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a=1\n+b=2\n",
        forensic_evidence="evidence",
        verification_summary="summary",
        verification_passed=False,
        human_approved=True,
    )
    assert result["status"] == "blocked"


def test_github_publish_requires_human_approval(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")
    service = GitHubService()
    result = service.create_pull_request(
        incident_id="incident-1",
        patch_str="--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a=1\n+b=2\n",
        forensic_evidence="evidence",
        verification_summary="summary",
        verification_passed=True,
        human_approved=False,
    )
    assert result["status"] == "blocked"
