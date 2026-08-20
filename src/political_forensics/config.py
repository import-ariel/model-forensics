"""YAML loading with schema validation and repository-relative paths."""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel

from .schemas import AnalysisConfig, Cue, DiscoveryConfig, Scenario, ScoringConfig

T = TypeVar("T", bound=BaseModel)
REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path, model: type[T]) -> T:
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise TypeError(f"Expected a mapping in {path}")
    return model.model_validate(raw)


def load_scenario(scenario_id: str, root: Path = REPO_ROOT) -> Scenario:
    return _load(root / "prompts" / "scenarios" / f"{scenario_id}.yaml", Scenario)


def load_cue(cue_id: str, root: Path = REPO_ROOT) -> Cue:
    return _load(root / "prompts" / "cues" / f"{cue_id}.yaml", Cue)


def load_discovery_config(path: Path | None = None) -> DiscoveryConfig:
    return _load(path or REPO_ROOT / "configs" / "discovery.yaml", DiscoveryConfig)


def load_scoring_config(path: Path | None = None) -> ScoringConfig:
    return _load(path or REPO_ROOT / "configs" / "scoring.yaml", ScoringConfig)


def load_analysis_config(path: Path | None = None) -> AnalysisConfig:
    return _load(path or REPO_ROOT / "configs" / "analysis.yaml", AnalysisConfig)
