"""Claude calls. Extraction is bulk and mechanical; scoring is judgement on a few.

Kept deliberately small and shared by both, so model choice, effort and structured
output live in one place. (The job-agent repo has its own copy of this idea; two
private repos did not justify a shared package.)
"""

from __future__ import annotations

import json
import logging
import re

from src.funding_radar.settings import settings

logger = logging.getLogger(__name__)

# Server-side refusal fallbacks exist on these families.
_FALLBACK_PREFIXES = ("claude-opus-5", "claude-fable-5")


class LLMError(RuntimeError):
    """A call failed or came back unusable. Callers skip the batch and retry tomorrow."""


def complete_json(system: str, user: str, schema: dict, *, model: str, effort: str = "low",
                  max_tokens: int = 16000) -> dict:
    """One structured-output call, returning parsed JSON."""
    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    if settings.ANTHROPIC_WORKSPACE_ID:
        # An org-level key is refused without this; a workspace-scoped key ignores it.
        client = client.with_options(
            default_headers={"anthropic-workspace-id": settings.ANTHROPIC_WORKSPACE_ID}
        )
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    }
    try:
        if model.startswith(_FALLBACK_PREFIXES):
            # A policy decline is retried on a suitable fallback inside the same call.
            response = client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
            )
        else:
            response = client.messages.create(**kwargs)
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as one error type
        # The SDK's str() can be a bare "Error code: 400"; the body says what is wrong.
        body = getattr(getattr(exc, "response", None), "text", "")
        detail = f"{exc}" + (f" | {body[:400]}" if body and str(body) not in str(exc) else "")
        raise LLMError(detail) from exc

    if response.stop_reason == "refusal":
        raise LLMError("model declined the request")
    if response.stop_reason == "max_tokens":
        raise LLMError("response hit max_tokens before finishing")

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    return parse_json(text)


def parse_json(text: str) -> dict:
    """Parse a model response, tolerating a stray code fence."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMError(f"response was not JSON: {cleaned[:200]}") from exc
    if not isinstance(parsed, dict):
        raise LLMError(f"expected an object, got {type(parsed).__name__}")
    return parsed
