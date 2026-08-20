#!/usr/bin/env python3
"""Verify the private vLLM endpoint and preserve forensic request/response evidence."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def request_json(url: str, api_key: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise SystemExit(f"HTTP {error.code} from {url}: {detail}") from error


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--full-length", action="store_true", help="Request 2,048 tokens, per the plan.")
    args = parser.parse_args()

    base_url = os.environ.get("MODEL_BASE_URL", "http://127.0.0.1:8000/v1").rstrip("/")
    api_key = os.environ.get("MODEL_API_KEY") or required("VLLM_API_KEY")
    model_id = required("MODEL_ID")
    revision = required("MODEL_REVISION")
    # Qwen3.5 can spend more than 256 tokens reasoning before emitting content.
    # Keep the quick check below the full validation while allowing a final answer.
    max_tokens = 2048 if args.full_length else 1024
    payload: dict[str, Any] = {
        "model": model_id,
        "messages": [{"role": "user", "content": "Explain, in two short paragraphs, why raw API responses should be retained in a reproducibility study."}],
        "max_tokens": max_tokens,
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 1.5,
        "repetition_penalty": 1.0,
        "seed": 20260815,
        "chat_template_kwargs": {"enable_thinking": True},
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    models = request_json(f"{base_url}/models", api_key)
    response = request_json(f"{base_url}/chat/completions", api_key, payload)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    record = {
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "model_revision": revision,
        "request": payload,
        "models_response": models,
        "raw_response": response,
    }
    output = args.output_dir / f"endpoint-smoke-{stamp}.json"
    output.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")

    message = ((response.get("choices") or [{}])[0].get("message") or {})
    final = message.get("content")
    # vLLM 0.27+ follows the current OpenAI schema and returns `reasoning`;
    # older vLLM releases used the provider-specific `reasoning_content` key.
    reasoning = message.get("reasoning_content") or message.get("reasoning")
    if not final:
        raise SystemExit(f"Saved {output}, but no final answer was found in choices[0].message.content")
    if not reasoning:
        raise SystemExit(
            f"Saved {output}, but no separate choices[0].message.reasoning "
            "or choices[0].message.reasoning_content was found"
        )
    print(f"PASS: endpoint, final response, and reasoning content saved to {output}")


if __name__ == "__main__":
    main()
