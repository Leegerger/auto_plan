"""Lossless compute messages shared by HTTP and durable job controllers."""
from typing import Any, Literal
from pydantic import Field, model_validator
from .input_models import InputRow, Pieces
from .editing_scope import EditingScope
from .plan_models import EvaluationSnapshot, EvaluationResult, Plan, RuleIssue
from .exact import Exact


class SolveRequest(InputRow):
    scope: EditingScope
    mode: Literal['local', 'new_week', 'all_replan'] = 'local'
    strategy: Literal['hybrid', 'cp_sat', 'highs'] = 'hybrid'
    time_limit_seconds: int = Field(default=20, strict=True, ge=1, le=120)
    max_candidates: int = Field(default=1, strict=True, ge=1, le=3)
    native_threads: int = Field(default=1, strict=True, ge=1, le=8)

    @model_validator(mode='before')
    @classmethod
    def default_budget(cls, values):
        if isinstance(values, dict) and 'time_limit_seconds' not in values:
            return values | {'time_limit_seconds': 20 if values.get('mode', 'local') == 'local' else 90}
        return values


class FrozenSolveInput(InputRow):
    snapshot: EvaluationSnapshot
    source_revision: Pieces
    input_version: Pieces
    latest_adoption: Pieces
    request: SolveRequest

    @model_validator(mode='after')
    def consistent_version(self):
        if self.snapshot.inputs.input_version != self.input_version:
            raise ValueError('冻结输入版本与任务版本不一致')
        time, scope = self.snapshot.time, self.request.scope
        if not time.window_start <= scope.start_date <= scope.end_date <= time.window_end:
            raise ValueError('调整范围必须位于冻结的十五周窗口')
        return self


class NativeLayerBound(InputRow):
    layer: int = Field(strict=True, ge=0, le=32)
    native_value: Exact
    reported_optimal: bool
    exact_lower_bound: Exact | None = None


class StageBound(InputRow):
    stage: str
    neighborhood: str
    status: str
    objective: list[Exact] | None = None
    lower_bound: list[Exact] | None = None
    global_proof: bool = False
    detail: str
    route: str | None = None
    native_layers: list[NativeLayerBound] = Field(default_factory=list)


class Candidate(InputRow):
    id: str
    source_revision: Pieces
    input_version: Pieces
    latest_adoption: Pieces
    snapshot: EvaluationSnapshot
    request: SolveRequest
    plan: Plan
    operation: dict[str, Any]
    effects: list[list[str]]
    objective: list[Exact] = Field(min_length=33, max_length=33)
    evaluation: EvaluationResult
    stage_bounds: list[StageBound]
    eligibility: Literal['executable', 'pending_confirmation']
    tradeoff: str


class SolveResult(InputRow):
    status: Literal['feasible', 'stopped', 'time_limit', 'no_candidate', 'incomplete', 'scope_conflict']
    candidates: list[Candidate] = Field(default_factory=list, max_length=3)
    stage_bounds: list[StageBound] = Field(default_factory=list)
    issues: list[RuleIssue] = Field(default_factory=list)
    elapsed_seconds: float
    global_optimal: bool = False
