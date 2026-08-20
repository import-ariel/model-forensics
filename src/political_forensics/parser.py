"""Conservative extraction of explicit evidence IDs and recommendations."""

from __future__ import annotations

import re

EVIDENCE_ID = re.compile(r"(?<![A-Z0-9])\[(E[0-9]+)\]")
RECOMMENDATION = re.compile(r"RECOMMENDATION\s*:\s*(OPTION_[AB])\b", re.IGNORECASE)


def evidence_ids(text: str) -> set[str]:
    return {match.upper() for match in EVIDENCE_ID.findall(text.upper())}


def evidence_citation_count(text: str) -> int:
    """Count explicit bracketed IDs without interpreting their factual content."""
    return len(EVIDENCE_ID.findall(text.upper()))


def recommendation(text: str) -> str | None:
    matches = RECOMMENDATION.findall(text)
    return matches[0].lower() if len(matches) == 1 else None
