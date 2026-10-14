"""Typed planning snapshots, independent of persistence and solver models."""
from datetime import datetime, timedelta
from typing import Literal
from pydantic import Field, field_validator
from .input_models import InputRow, InputSnapshot, Code, Pieces, ProductionDate
from .exact import Exact, Hours


class SourceSegment(InputRow):
    id: Code
    date: ProductionDate
    line_code: Code
    order: Pieces
    model_code: Code
    quantity: Pieces


class ProductionSegment(SourceSegment):
    hours: Exact


class SwitchAllocation(InputRow):
    date: ProductionDate
    order: Pieces
    hours: Exact
    idle_hours: Exact = '0'


class RecipeSwitch(InputRow):
    id: Code
    line_code: Code
    from_recipe: Code
    to_recipe: Code
    allocations: list[SwitchAllocation] = Field(min_length=1)


class InitialRecipeState(InputRow):
    line_code: Code
    recipe_code: Code | None = None


class OrdinaryIdle(InputRow):
    date: ProductionDate
    line_code: Code
    hours: Exact


class Plan(InputRow):
    segments: list[ProductionSegment] = Field(default_factory=list)
    switches: list[RecipeSwitch] = Field(default_factory=list)
    initial_states: list[InitialRecipeState] = Field(default_factory=list)
    ordinary_idle: list[OrdinaryIdle] = Field(default_factory=list)


class PlanCell(InputRow):
    date: ProductionDate
    line_code: Code
    model_code: Code
    quantity: Pieces


class ExecutionCoverage(InputRow):
    start_date: ProductionDate
    end_date: ProductionDate
    line_codes: list[Code]
    model_codes: list[Code]
    blanks_are_zero: bool = Field(strict=True)

    @field_validator('end_date')
    @classmethod
    def ordered_dates(cls, value, info):
        if 'start_date' in info.data and value < info.data['start_date']:
            raise ValueError('覆盖终日不能早于起日')
        return value


class ExecutionContext(InputRow):
    facts: list[PlanCell] = Field(default_factory=list)
    coverage: list[ExecutionCoverage] = Field(default_factory=list)
    cutoff: datetime | None = None


class ResetModel(InputRow):
    model_code: Code
    wip: Pieces
    net_balance: int = Field(strict=True)


class ResetContext(InputRow):
    batch_id: Code
    production_week: ProductionDate
    cutoff: datetime
    models: list[ResetModel]


class AdoptedBaseline(InputRow):
    version: Pieces
    plan: Plan
    coverage: list[ExecutionCoverage]


class EvaluationPreferences(InputRow):
    near_week_weight: Exact = '3'
    far_week_weight: Exact = '1'


class EvaluationTime(InputRow):
    as_of: datetime
    window_start: ProductionDate
    weeks: Literal[15] = 15

    @field_validator('as_of')
    @classmethod
    def aware_instant(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('计算时点必须包含时区')
        return value

    @field_validator('window_start')
    @classmethod
    def monday(cls, value):
        if value.weekday() != 0:
            raise ValueError('排程窗口起日必须为生产周周一')
        return value

    @property
    def window_end(self):
        return self.window_start + timedelta(days=self.weeks * 7 - 1)

    @classmethod
    def at(cls, instant):
        from .production_time import production_date, production_week
        return cls(as_of=instant, window_start=production_week(production_date(instant)))


class EvaluationSnapshot(InputRow):
    time: EvaluationTime
    inputs: InputSnapshot
    plan: Plan
    execution: ExecutionContext = Field(default_factory=ExecutionContext)
    reset: ResetContext | None = None
    locks: list[PlanCell] = Field(default_factory=list)
    baseline: AdoptedBaseline | None = None
    preferences: EvaluationPreferences = Field(default_factory=EvaluationPreferences)


class RuleIssue(InputRow):
    code: str
    reason: str
    severity: Literal['error', 'incomplete', 'warning'] = 'error'
    date: ProductionDate | None = None
    line_code: str | None = None
    model_code: str | None = None
    recipe_code: str | None = None
    segment_id: str | None = None
    switch_id: str | None = None


class RecipeCapacity(InputRow):
    recipe_code: Code
    quantity: Pieces
    hours: Hours
    capacity_state: Literal['known', 'zero', 'missing', 'forbidden']
    full_day_capacity: Pieces
    limit: Pieces
    model_codes: list[Code]


class DayEvaluation(InputRow):
    date: ProductionDate
    line_code: Code
    available_hours: Pieces
    production_hours: Hours
    switch_hours: Hours
    ordinary_idle_hours: Hours
    used_idle_hours: Hours
    occupied_hours: Hours
    output_holiday: bool
    recipes: list[RecipeCapacity]
    ending_recipe: Code | None = None


class SwitchEvaluation(InputRow):
    id: Code
    line_code: Code
    from_recipe: Code
    to_recipe: Code
    duration_state: Literal['known', 'default_missing', 'same_recipe', 'unknown_initial']
    total_hours: Hours
    allocated_hours: Hours
    idle_hours: Hours
    extra_hours: Hours


class InitialStateEvaluation(InputRow):
    line_code: Code
    recipe_code: Code | None
    state: Literal['known', 'unknown_initial']


class EvaluationResult(InputRow):
    time: EvaluationTime
    capacity_valid: bool
    computation_complete: bool
    executable: bool
    issues: list[RuleIssue]
    days: list[DayEvaluation]
    switches: list[SwitchEvaluation]
    initial_states: list[InitialStateEvaluation]
    switch_count: Pieces


class SourcePlan(InputRow):
    segments: list[SourceSegment] = Field(default_factory=list)
    initial_states: list[InitialRecipeState] = Field(default_factory=list)


class SourceEvaluationSnapshot(EvaluationSnapshot):
    plan: SourcePlan


class CertificatePreviewResult(InputRow):
    plan: Plan | None
    evaluation: EvaluationResult | None
    issues: list[RuleIssue] = Field(default_factory=list)
