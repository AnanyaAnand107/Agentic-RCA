from backend.services.sandbox_service import SandboxVerificationService


def test_sandbox_rejects_missing_reproduction_test(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.services.sandbox_service.shutil.which", lambda _: "/usr/bin/docker")
    result = SandboxVerificationService().verify_patch(
        original_repo_path=str(tmp_path),
        patch_str="--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a=1\n+b=2\n",
        generated_test="",
    )
    assert result["verified"] is False
    assert result["status"] == "missing_reproduction_test"


def test_sandbox_fails_closed_when_docker_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.services.sandbox_service.shutil.which", lambda _: None)
    result = SandboxVerificationService().verify_patch(
        original_repo_path=str(tmp_path),
        patch_str="--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a=1\n+b=2\n",
        generated_test="def test_fix():\n    assert True\n",
    )
    assert result["verified"] is False
    assert result["status"] == "sandbox_unavailable"
    assert "not executed on the host" in result["reason"]


def test_sandbox_rejects_complete_source_instead_of_diff(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.services.sandbox_service.shutil.which", lambda _: "/usr/bin/docker")
    result = SandboxVerificationService().verify_patch(
        original_repo_path=str(tmp_path),
        patch_str="def fixed():\n    return True\n",
        generated_test="def test_fix():\n    assert True\n",
    )
    assert result["verified"] is False
    assert result["status"] == "invalid_patch_format"


def test_assertion_failure_classification_rejects_setup_errors():
    assert SandboxVerificationService._is_assertion_failure("FAILED tests/test_x.py::test_x - AssertionError")
    assert not SandboxVerificationService._is_assertion_failure("ERROR collecting tests/test_x.py\nModuleNotFoundError")
    assert not SandboxVerificationService._is_assertion_failure("collected 0 items")
