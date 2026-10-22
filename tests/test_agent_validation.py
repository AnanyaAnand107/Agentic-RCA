from backend.agents.reproduction import _validate_test
from backend.agents.patch_synthesizer import _validate_python


def test_reproduction_rejects_dangerous_code():
    code = "import pytest\nimport os\ndef test_x():\n    os.system('whoami')\n    with pytest.raises(KeyError):\n        pass\n"
    ok, reason = _validate_test(code, "pricing", "apply_discount")
    assert not ok
    assert "unsupported import" in reason or "blocked" in reason


def test_reproduction_accepts_correct_behavior_assertion():
    code = "import pytest\nfrom pricing import apply_discount\ndef test_apply_discount():\n    result = apply_discount({'price': 100}, 10)\n    assert result == 90\n"
    ok, reason = _validate_test(code, "pricing", "apply_discount")
    assert ok, reason


def test_reproduction_rejects_pytest_raises_bug_preservation():
    code = "import pytest\nfrom pricing import apply_discount\ndef test_apply_discount():\n    with pytest.raises(KeyError):\n        apply_discount({'name': 'x'}, 10)\n"
    ok, reason = _validate_test(code, "pricing", "apply_discount")
    assert not ok
    assert "expected correct behavior" in reason


def test_patch_python_syntax_validation():
    ok, _ = _validate_python("def apply_discount(item, pct):\n    return item.get('price', 0)\n")
    assert ok


def test_patch_python_rejects_dangerous_import():
    ok, reason = _validate_python("import subprocess\nsubprocess.run(['whoami'])\n")
    assert not ok
    assert "blocked" in reason


def test_telemetry_fallback_extracts_python_stack_frame(tmp_path, monkeypatch):
    from backend.agents import telemetry_parser as telemetry_module

    source = tmp_path / "pricing.py"
    source.write_text('def apply_discount(item, pct):\n    return item["price"] * pct\n', encoding="utf-8")

    def unavailable(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(telemetry_module, "generate_validated", unavailable)
    description = 'KeyError: \'price\'\n  File "pricing.py", line 2, in apply_discount\n'
    result = telemetry_module.telemetry_parser({
        "description": description,
        "repo_path": str(tmp_path),
        "telemetry_source": "test",
    })

    assert result["error_type"] == "KeyError"
    assert result["affected_file"] == "pricing.py"
    assert result["affected_line"] == 2
    assert result["affected_function"] == "apply_discount"
    assert result["stack_trace"]
    assert "fallback" in result["stage_logs"][0]


def test_verification_requests_only_one_reflection():
    from backend.agents.verification import verification

    failed = {
        "patched_code": "def broken(:\n    pass\n",
        "patch_file": "pricing.py",
        "reflection_count": 0,
    }
    first = verification(failed)
    assert first["needs_reflection"] is True
    assert first["reflection_count"] == 1

    second = verification({**failed, "reflection_count": 1})
    assert second["needs_reflection"] is False
    assert second["reflection_count"] == 1


def test_git_line_proximity_uses_added_line_numbers():
    from backend.agents.git_forensic import _line_distance

    diff = "@@ -10,2 +10,3 @@ def checkout():\n old()\n+raise KeyError('price')\n next()\n"
    assert _line_distance(diff, 11)
    assert not _line_distance(diff, 30)


def test_verification_does_not_publish_github_automatically(monkeypatch, tmp_path):
    import importlib
    verification_module = importlib.import_module("backend.agents.verification")

    class FakeSandbox:
        def verify_patch(self, **kwargs):
            assert kwargs["patch_str"].startswith("--- a/")
            assert "def test_fix" in kwargs["generated_test"]
            return {"verified": True, "status": "verified", "regression_passed": True, "reason": "all checks passed"}

    class ForbiddenGitHub:
        def __init__(self):
            raise AssertionError("GitHub publishing must wait for explicit human approval")

    monkeypatch.setattr(verification_module, "SandboxVerificationService", FakeSandbox)
    monkeypatch.setattr(verification_module, "GitHubService", ForbiddenGitHub, raising=False)
    result = verification_module.verification({
        "patched_code": "def fix():\n    return True\n",
        "patch_file": "pricing.py",
        "diff_text": "--- a/pricing.py\n+++ b/pricing.py\n@@ -1 +1 @@\n-def fix():\n-    return False\n+def fix():\n+    return True\n",
        "repo_path": str(tmp_path),
        "generated_test": "def test_fix():\n    from pricing import fix\n    assert fix() is True\n",
        "test_file_name": "tests/test_generated_reproduction.py",
        "reflection_count": 0,
    })
    assert result["verification_passed"] is True
    assert result["needs_reflection"] is False
    assert result["pr_url"] is None
