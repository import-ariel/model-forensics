"""Minimal OpenAI-compatible client that retains the provider's unmodified JSON."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any


class EndpointClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 600) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def chat(self, payload: dict[str, Any], retries: int = 2) -> tuple[dict[str, Any], float, int]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        for attempt in range(retries + 1):
            started = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.load(response), time.monotonic() - started, attempt
            except urllib.error.URLError:
                if attempt == retries:
                    raise
                time.sleep(2**attempt)
        raise AssertionError("unreachable")
