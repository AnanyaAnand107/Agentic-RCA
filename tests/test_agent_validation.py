from backend.agents.reproduction import _validate_test
from backend.agents.patch_synthesizer import _validate_python


def test_reproduction_rejects_dangerous_code():
    code = "import pytest\nimport os\ndef test_x():\n    os.system('whoami')\n    with pytest.raises(KeyError):\n        pass\n"
    ok, reason = _validate_test(code, "pricing", "apply_discount")
    assert not ok
    assert "unsupported import" in reason or "blocked" in reason


def test_reproduction_accepts_grounded_shape():
    code = "```python\nimport pytest\nfrom pricing import apply_discount\ndef test_apply_discount():\n    with pytest.raises(KeyError):\n        apply_discount({'name': 'x'}, 10)\n```"
    ok, reason = _validate_test(code, "pricing", "apply_discount")
    assert ok, reason


def test_patch_python_syntax_validation():
    ok, _ = _validate_python("def apply_discount(item, pct):\n    return item.get('price', 0)\n")
    assert ok


def test_patch_python_rejects_dangerous_import():
    ok, reason = _validate_python("import subprocess\nsubprocess.run(['whoami'])\n")
    assert not ok
    assert "blocked" in reason
