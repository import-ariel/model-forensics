"""Fail-fast response-integrity checks for rollout collection."""

from __future__ import annotations

from typing import Any


def assess_response_integrity(raw_response: dict[str, Any]) -> dict[str, object]:
    """Return content-free integrity metadata for one provider response."""
    choice = ((raw_response.get("choices") or [{}])[0])
    message = choice.get("message") or {}
    finish_reason = choice.get("finish_reason")
    reasoning_present = bool(message.get("reasoning") or message.get("reasoning_content"))
    final_content_nonempty = bool((message.get("content") or "").strip())
    invalid_reasons: list[str] = []
    if finish_reason == "length":
        invalid_reasons.append("finish_reason_length")
    if not reasoning_present:
        invalid_reasons.append("reasoning_missing")
    if not final_content_nonempty:
        invalid_reasons.append("final_content_empty")
    return {
        "finish_reason": finish_reason,
        "reasoning_present": reasoning_present,
        "final_content_nonempty": final_content_nonempty,
        "analysis_eligible": not invalid_reasons,
        "invalid_reasons": invalid_reasons,
    }
