"""Validated data contracts for scenarios, discovery runs, and captured rollouts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceCard(BaseModel):
    id: str = Field(pattern=r"^E[0-9]+$")
    text: str
    supports: Literal["option_a", "option_b", "mixed"]
    reliability: Literal["high", "medium", "low"]
    materiality: Literal["high", "medium", "low"]
    provenance: str = Field(min_length=12)


class Scenario(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    title: str
    decision_question: str
    fixed_objective: str
    option_a: str
    option_b: str
    partisan_association: dict[Literal["option_a", "option_b"], Literal["democrat", "republican"]]
    evidence: list[EvidenceCard] = Field(min_length=6, max_length=6)
    mandatory_evidence_ids: list[str] = Field(min_length=2, max_length=2)
    strongest_counterevidence_by_recommendation: dict[Literal["option_a", "option_b"], str]

    @model_validator(mode="after")
    def counterevidence_ids_exist(self) -> Scenario:
        if self.partisan_association != {"option_a": "democrat", "option_b": "republican"}:
            raise ValueError(
                "scenarios must explicitly encode option_a as democrat-associated and option_b as republican-associated"
            )
        ids = {card.id for card in self.evidence}
        if len(ids) != len(self.evidence):
            raise ValueError("evidence card IDs must be unique")
        selected = set(self.mandatory_evidence_ids) | set(
            self.strongest_counterevidence_by_recommendation.values()
        )
        missing = selected - ids
        if missing:
            raise ValueError(f"scenario refers to unknown evidence IDs: {sorted(missing)}")
        counts = {
            direction: sum(card.supports == direction for card in self.evidence)
            for direction in ("option_a", "option_b")
        }
        if counts != {"option_a": 3, "option_b": 3}:
            raise ValueError("scenarios require exactly three evidence cards for each option")
        for option in ("option_a", "option_b"):
            opposing = "option_b" if option == "option_a" else "option_a"
            strongest = self.strongest_counterevidence_by_recommendation[option]
            card = next(item for item in self.evidence if item.id == strongest)
            if (
                card.supports != opposing
                or card.reliability != "high"
                or card.materiality != "high"
            ):
                raise ValueError(
                    "strongest counterevidence must be high-reliability, high-materiality opposing evidence"
                )
        strongest_ids = set(self.strongest_counterevidence_by_recommendation.values())
        if set(self.mandatory_evidence_ids) != strongest_ids:
            raise ValueError(
                "mandatory evidence IDs must be exactly the two strongest opposing cards"
            )
        return self


class Cue(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    biography: str


class SamplingConfig(BaseModel):
    temperature: float = Field(ge=0, le=2)
    top_p: float = Field(gt=0, le=1)
    top_k: int = Field(ge=1)
    min_p: float = Field(ge=0, le=1)
    presence_penalty: float = Field(ge=-2, le=2)
    repetition_penalty: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=256)


class DiscoveryConfig(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    run_id: str
    stage: Literal["pilot", "discovery"]
    model_id: str
    model_revision: str = Field(min_length=40, max_length=64)
    scenarios: list[str] = Field(min_length=1)
    cues: list[str] = Field(min_length=2)
    seeds: list[int] = Field(min_length=1)
    option_order: Literal["option_a_first", "option_b_first"] = "option_a_first"
    sampling: SamplingConfig
    chat_template_kwargs: dict[str, bool] = {"enable_thinking": True}


class ScoringConfig(BaseModel):
    primary_outcome: Literal["human_confirmed_content_omission"]
    recommendation_pattern: str
    evidence_citation_pattern: str
    rules: list[str] = Field(min_length=1)


class AnomalyGateConfig(BaseModel):
    minimum_absolute_omission_rate_difference: float = Field(ge=0, le=1)
    minimum_matched_party_congruent_reversals: int = Field(ge=1)
    maximum_schema_failure_rate_exclusive: float = Field(gt=0, le=1)
    maximum_truncation_rate_exclusive: float = Field(gt=0, le=1)
    require_human_materiality_judgment: bool
    require_neutrality_or_completeness_violation: bool
    require_two_cue_paraphrases: bool


class AnalysisConfig(BaseModel):
    """Pre-specified, outcome-independent discovery analysis contract."""

    run_id: str
    expected_scenarios: int = Field(ge=1)
    expected_cues: int = Field(ge=1)
    expected_seeds: int = Field(ge=1)
    unit_key: tuple[Literal["scenario_id", "cue_id", "seed"], ...]
    primary_outcome: Literal["human_confirmed_content_omission"]
    direct_cue_contrast: dict[Literal["numerator", "denominator"], str]
    neutral_cue: str
    blind_randomization_seed: int
    exploratory: bool
    denominator_rules: dict[str, str]
    anomaly_gate: AnomalyGateConfig

    @model_validator(mode="after")
    def validate_contract(self) -> AnalysisConfig:
        if self.unit_key != ("scenario_id", "cue_id", "seed"):
            raise ValueError("analysis unit_key must be scenario_id, cue_id, seed")
        if self.direct_cue_contrast["numerator"] == self.direct_cue_contrast["denominator"]:
            raise ValueError("direct-cue contrast requires two distinct cues")
        return self


class ResponseIntegrity(BaseModel):
    finish_reason: str | None
    reasoning_present: bool
    final_content_nonempty: bool
    analysis_eligible: bool
    invalid_reasons: list[str]


class RolloutRecord(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    run_id: str
    stage: Literal["pilot", "discovery"]
    request_id: str
    captured_at_utc: datetime
    scenario_id: str
    cue_id: str
    seed: int
    model_id: str
    model_revision: str = Field(min_length=40, max_length=64)
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scoring_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_git_commit: str | None = None
    environment_manifest_id: str | None = None
    request: dict[str, Any]
    raw_response: dict[str, Any]
    prompt_sha256: str
    latency_seconds: float
    retry_count: int
    response_integrity: ResponseIntegrity
    derived_score: dict[str, Any]
