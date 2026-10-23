import json

import pytest

from backend import llm


def test_generate_validated_uses_fallback_when_primary_response_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
):
    expected = json.dumps(
        {
            "root_cause_explanation": "Guard missing price values.",
            "patched_code": "def apply_discount(item, pct):\n    return item.get('price', 0)\n",
        }
    )
    calls = []

    def invalid_huggingface(
        prompt: str,
        *,
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 5000,
    ) -> str:
        assert prompt == "generate a valid candidate patch"
        assert model == llm.settings.hf_code_model
        assert temperature == 0.0
        assert max_tokens == 5000
        calls.append("huggingface")
        return "not valid JSON"

    def valid_gemini(prompt: str, *, temperature: float = 0.0) -> str:
        assert prompt == "generate a valid candidate patch"
        assert temperature == 0.0
        calls.append("gemini")
        return expected

    def validate_patch(text: str) -> dict[str, str]:
        value = json.loads(text)
        assert value["patched_code"]
        return value

    monkeypatch.setattr(llm, "_hf", invalid_huggingface)
    monkeypatch.setattr(llm, "_gemini", valid_gemini)

    text, provider = llm.generate_validated(
        "patch",
        "generate a valid candidate patch",
        validator=validate_patch,
    )

    assert calls == ["huggingface", "gemini"]
    assert provider == "gemini"
    assert json.loads(text)["patched_code"]


def test_generate_validated_retries_transient_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    expected = json.dumps(
        {
            "root_cause_explanation": "Guard missing price values.",
            "patched_code": "def apply_discount(item, pct):\n    return item.get('price', 0)\n",
        }
    )
    gemini_calls = 0

    def invalid_huggingface(
        prompt: str,
        *,
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 5000,
    ) -> str:
        return "not valid JSON"

    def temporarily_unavailable_gemini(
        prompt: str,
        *,
        temperature: float = 0.0,
    ) -> str:
        nonlocal gemini_calls
        gemini_calls += 1
        if gemini_calls == 1:
            raise RuntimeError("503 UNAVAILABLE")
        return expected

    def validate_patch(text: str) -> dict[str, str]:
        value = json.loads(text)
        assert value["patched_code"]
        return value

    monkeypatch.setattr(llm, "_hf", invalid_huggingface)
    monkeypatch.setattr(llm, "_gemini", temporarily_unavailable_gemini)
    monkeypatch.setattr(llm.time, "sleep", lambda _delay: None)

    text, provider = llm.generate_validated(
        "patch",
        "generate a valid candidate patch",
        validator=validate_patch,
    )

    assert gemini_calls == 2
    assert provider == "gemini"
    assert json.loads(text)["patched_code"]


def test_generate_does_not_retry_exhausted_provider_quota(
    monkeypatch: pytest.MonkeyPatch,
):
    gemini_calls = 0
    expected = json.dumps({"analysis": "Fallback used.", "evidence": []})

    def exhausted_gemini(prompt: str, *, temperature: float = 0.0) -> str:
        nonlocal gemini_calls
        gemini_calls += 1
        raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")

    def fallback_hf(
        prompt: str,
        *,
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 5000,
    ) -> str:
        return expected

    monkeypatch.setattr(llm, "_gemini", exhausted_gemini)
    monkeypatch.setattr(llm, "_hf", fallback_hf)

    text, provider = llm.generate("telemetry", "parse the incident")

    assert gemini_calls == 1
    assert provider == "huggingface"
    assert json.loads(text)["analysis"] == "Fallback used."
