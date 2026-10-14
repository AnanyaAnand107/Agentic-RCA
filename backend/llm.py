"""Model routing for Agentic RCA."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable

from backend.config import settings

logger = logging.getLogger(__name__)

GEMINI_TIMEOUT_SECONDS = 60
HF_TIMEOUT_SECONDS = 75
TRANSIENT_RETRIES = 2


class ModelUnavailable(RuntimeError):
    pass


def _strip_fences(text: str) -> str:
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json|python|javascript|typescript|java|text)?\s*",
            "",
            text,
            count=1,
            flags=re.I,
        )
        text = re.sub(r"\s*```$", "", text)

    return text.strip()


def extract_json(text: str) -> Any:
    cleaned = _strip_fences(text)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = min(
            [i for i in (cleaned.find("{"), cleaned.find("[")) if i >= 0],
            default=-1,
        )

        if start < 0:
            raise

        end = max(cleaned.rfind("}"), cleaned.rfind("]"))

        if end <= start:
            raise

        return json.loads(cleaned[start : end + 1])


def _gemini(prompt: str, *, temperature: float = 0.0) -> str:
    if not settings.google_api_key:
        raise ModelUnavailable("GOOGLE_API_KEY is not configured")

    from langchain_google_genai import ChatGoogleGenerativeAI

    llm = ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.google_api_key,
        temperature=temperature,
        max_retries=1,
        timeout=GEMINI_TIMEOUT_SECONDS,
    )

    response = llm.invoke(prompt)
    content = response.content

    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )

    return str(content).strip()


def _hf(
    prompt: str,
    *,
    model: str,
    temperature: float = 0.1,
    max_tokens: int = 5000,
) -> str:
    if not settings.hf_token:
        raise ModelUnavailable("HF_TOKEN is not configured")

    from huggingface_hub import InferenceClient

    client = InferenceClient(
        token=settings.hf_token,
        timeout=HF_TIMEOUT_SECONDS,
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a production software reliability agent. "
                    "Follow the requested output contract exactly and "
                    "never invent repository evidence."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )

    return str(response.choices[0].message.content).strip()


def _providers(
    task: str,
    prompt: str,
    *,
    temperature: float = 0.0,
    max_tokens: int = 5000,
) -> list[tuple[str, Callable[[], str]]]:
    if task in {"telemetry", "forensics"}:
        return [
            (
                "gemini",
                lambda: _gemini(
                    prompt,
                    temperature=temperature,
                ),
            ),
            (
                "huggingface",
                lambda: _hf(
                    prompt,
                    model=settings.hf_analysis_model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ),
            ),
        ]
    return [
        (
            "huggingface",
            lambda: _hf(
                prompt,
                model=settings.hf_code_model,
                temperature=temperature,
                max_tokens=max_tokens,
            ),
        ),
        (
            "gemini",
            lambda: _gemini(
                prompt,
                temperature=temperature,
            ),
        ),
    ]


def _generate(
    task: str,
    prompt: str,
    *,
    temperature: float,
    max_tokens: int,
    validator: Callable[[str], Any] | None = None,
) -> tuple[str, str]:
    providers = _providers(
        task,
        prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    errors = []

    for provider, call in providers:
        for attempt in range(TRANSIENT_RETRIES + 1):
            try:
                text = call()
                if validator is not None:
                    validator(text)
                return text, provider
            except Exception as exc:
                is_transient = any(
                    marker in str(exc).lower()
                    for marker in (
                        "429",
                        "502",
                        "503",
                        "504",
                        "unavailable",
                        "timed out",
                        "timeout",
                        "deadline exceeded",
                        "connection reset",
                        "connection error",
                        "rate limit",
                        "resource_exhausted",
                        "internal server error",
                    )
                )
                if is_transient and attempt < TRANSIENT_RETRIES:
                    delay = 0.5 * (2**attempt)
                    logger.warning(
                        "%s model temporarily unavailable for %s; retrying in %.1fs",
                        provider,
                        task,
                        delay,
                    )
                    time.sleep(delay)
                    continue

                errors.append(f"{provider}: {exc}")
                logger.warning(
                    "%s model failed for %s: %s",
                    provider,
                    task,
                    exc,
                )
                break

    raise ModelUnavailable("; ".join(errors) or "No model provider available")


def generate(
    task: str,
    prompt: str,
    *,
    temperature: float = 0.0,
    max_tokens: int = 5000,
) -> tuple[str, str]:
    return _generate(
        task,
        prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def generate_validated(
    task: str,
    prompt: str,
    *,
    validator: Callable[[str], Any],
    temperature: float = 0.0,
    max_tokens: int = 5000,
) -> tuple[str, str]:
    return _generate(
        task,
        prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        validator=validator,
    )


def model_status() -> dict[str, Any]:
    return {
        "gemini_configured": bool(settings.google_api_key),
        "gemini_model": settings.gemini_model,
        "huggingface_configured": bool(settings.hf_token),
        "hf_code_model": settings.hf_code_model,
        "hf_analysis_model": settings.hf_analysis_model,
    }
