"""Typed planning snapshots, independent of persistence and solver models."""
from datetime import datetime, timedelta
from typing import Literal
from pydantic import Field, field_validator, model_validator
from .input_models import InputRow, InputSnapshot, Code, Month, Pieces, ProductionDate, WeeklyDemand
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


class LineStateRecord(InputRow):
    id: Code
    line_code: Code
    effective_at: datetime
    recipe_code: Code
    source: str = Field(min_length=1, max_length=500)
    actor: str
    recorded_at: datetime

    @field_validator('effective_at', 'recorded_at')
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('线体状态时点必须包含时区')
        return value


class LineStateAudit(InputRow):
    action_id: str
    actor: str
    recorded_at: datetime
    reason: str | None = None
    changes: list[dict] = Field(default_factory=list)


class LineStateContext(InputRow):
    records: list[LineStateRecord] = Field(default_factory=list)
    audit: list[LineStateAudit] = Field(default_factory=list)


class OrdinaryIdle(InputRow):
    date: ProductionDate
    line_code: Code
    hours: Exact


class RecipeHours(InputRow):
    recipe_code: Code
    hours: Exact


class CurrentDayWitness(InputRow):
    date: ProductionDate
    line_code: Code
    basis_digest: str
    plan_digest: str
    segments: list[ProductionSegment] = Field(default_factory=list)
    switches: list[RecipeSwitch] = Field(default_factory=list)
    prior_recipe_hours: list[RecipeHours] = Field(default_factory=list)
    prior_switch_hours: Exact = '0'
    prior_switches: list[RecipeSwitch] = Field(default_factory=list)


class Plan(InputRow):
    current_day_witnesses: list[CurrentDayWitness] = Field(default_factory=list)
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


class ExecutionAssociation(InputRow):
    segment_id: Code
    quantity: Pieces


class ExecutionAudit(InputRow):
    action_id: str
    actor: str
    recorded_at: datetime
    reason: str | None = None
    changes: list[dict] = Field(default_factory=list)


class CurrentDayCapacity(InputRow):
    date: ProductionDate
    line_code: Code
    remaining_hours: Exact
    current_recipe: Code
    confirmed_at: datetime
    basis_digest: str


class ExecutionContext(InputRow):
    facts: list[PlanCell] = Field(default_factory=list)
    coverage: list[ExecutionCoverage] = Field(default_factory=list)
    associations: list[ExecutionAssociation] = Field(default_factory=list)
    association_cells: list[PlanCell] = Field(default_factory=list)
    audit: list[ExecutionAudit] = Field(default_factory=list)
    day_capacities: list[CurrentDayCapacity] = Field(default_factory=list)
    cutoff: datetime | None = None

    @field_validator('cutoff')
    @classmethod
    def aware_cutoff(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError('执行截止时点必须包含时区')
        return value


class ResetModel(InputRow):
    model_code: Code
    wip: Pieces
    net_balance: int = Field(strict=True)


class ResetContext(InputRow):
    batch_id: Code
    production_week: ProductionDate
    cutoff: datetime
    models: list[ResetModel]

    @field_validator('cutoff')
    @classmethod
    def aware_cutoff(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('重置截止时点必须包含时区')
        return value


class AdoptedBaseline(InputRow):
    version: Pieces
    plan: Plan
    coverage: list[ExecutionCoverage]


class EvaluationPreferences(InputRow):
    near_weeks: int = Field(default=4, strict=True, ge=1, le=15)
    near_tolerance_percent: Exact = '10'
    far_tolerance_percent: Exact = '30'
    near_week_weight: Exact = '3'
    far_week_weight: Exact = '1'

    @model_validator(mode='after')
    def near_cost_is_higher(self):
        from fractions import Fraction
        if Fraction(self.near_week_weight) <= Fraction(self.far_week_weight):
            raise ValueError('近期变动权重必须大于远期变动权重')
        return self


class StabilityWeek(InputRow):
    week: ProductionDate
    number: Pieces
    band: Literal['near', 'far']
    state: Literal['new_plan', 'compared', 'zero_baseline', 'partial', 'unknown_execution']
    old_remaining: Pieces
    new_remaining: Pieces
    absolute_change: Pieces
    added: Pieces
    rate: str | None
    rate_display: str
    tolerance_percent: Exact
    exceeds_tolerance: bool
    baseline_complete: bool
    unknown_baseline_cells: Pieces
    unknown_execution_cells: Pieces


class StabilityResult(InputRow):
    baseline_version: Pieces | None
    weeks: list[StabilityWeek]
    weighted_cost: Exact
    order_change_count: Pieces
    comparison_complete: bool


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
        try:
            value + timedelta(days=104)
        except OverflowError:
            raise ValueError('十五周窗口超出可表示日历范围') from None
        return value

    @property
    def window_end(self):
        return self.window_start + timedelta(days=self.weeks * 7 - 1)

    @classmethod
    def at(cls, instant):
        from .production_time import production_date, production_week
        return cls(as_of=instant, window_start=production_week(production_date(instant)))


class EvaluationSnapshot(InputRow):
    line_states: LineStateContext = Field(default_factory=LineStateContext)
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
    yield_month: Month | None = Field(default=None, exclude_if=lambda value: value is None)
    date: ProductionDate | None = None
    line_code: str | None = None
    model_code: str | None = None
    recipe_code: str | None = None
    segment_id: str | None = None
    switch_id: str | None = None
    demand_week: ProductionDate | None = None
    batch_id: str | None = None
    source_id: str | None = None
    input_date: ProductionDate | None = None
    line_codes: list[Code] = Field(default_factory=list)
    related_batch_ids: list[Code] = Field(default_factory=list)


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
    available_hours: Pieces | Exact
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


class ReconciledCell(PlanCell):
    planned: Pieces
    actual: Pieces | None
    remaining: Pieces
    expired: Pieces
    known: bool


class OutputPortion(InputRow):
    date: ProductionDate
    fraction: str
    before_rounding: str
    quantity: Pieces
    unallocated: Pieces = 0


class OutputSource(InputRow):
    id: str
    kind: Literal['input', 'wip']
    model_code: Code
    input_date: ProductionDate
    quantity: Pieces
    lines: list[ReconciledCell] = Field(default_factory=list)
    yield_month: str
    line_yield: str
    model_yield: str
    cycle_time: str | None
    portions: list[OutputPortion]
    allocated_output: Pieces = 0
    unallocated_output: Pieces = 0


class BatchUse(InputRow):
    batch_id: Code
    quantity: Pieces


class OutputAttribution(InputRow):
    source_id: str
    model_code: Code
    demand_week: ProductionDate
    input_date: ProductionDate
    date: ProductionDate
    quantity: Pieces
    source_week_output: Pieces = 0
    apportionment_denominator: Pieces = 0
    apportioned_input: Pieces = 0
    input_share_exact: str = '0'
    material_required: bool = False
    material_input_used: Pieces = 0
    material_input_pending: Pieces = 0
    material_satisfied: Pieces = 0
    pending_material: Pieces = 0
    pending_shipping: Pieces = 0
    forbidden_material: bool = False
    forbidden_shipping: bool = False
    on_time: Pieces = 0
    late: Pieces = 0
    material_batches: list[BatchUse] = Field(default_factory=list)
    shipping_batches: list[BatchUse] = Field(default_factory=list)


class WeeklyResult(InputRow):
    computation_complete: bool = True
    model_code: Code
    week: ProductionDate
    demand: Pieces
    attributed: Pieces
    on_time: Pieces
    late: Pieces
    pending_material: Pieces
    pending_shipping: Pieces
    pending: Pieces
    gap: Pieces
    on_time_rate: str | None
    on_time_rate_display: str = '不适用'


class ConfirmationUsage(InputRow):
    model_code: Code
    week: ProductionDate
    kind: str
    batch_id: Code
    status: str
    date: ProductionDate | None
    quota: Pieces | None
    used: Pieces
    remaining: Pieces
    banned: bool


class OutputTotals(InputRow):
    computation_complete: bool = True
    expected: Pieces
    outside_window: Pieces
    unallocated: Pieces
    on_time: Pieces
    late: Pieces
    pending: Pieces
    gap: Pieces
    on_time_rate: str | None
    on_time_rate_display: str = '不适用'


class EvaluationResult(InputRow):
    adoption_conditions: list[RuleIssue] = Field(default_factory=list)
    time: EvaluationTime
    capacity_valid: bool
    computation_complete: bool
    executable: bool
    issues: list[RuleIssue]
    days: list[DayEvaluation]
    switches: list[SwitchEvaluation]
    initial_states: list[InitialStateEvaluation]
    switch_count: Pieces
    output_sources: list['OutputSource'] = Field(default_factory=list)
    attributions: list['OutputAttribution'] = Field(default_factory=list)
    weekly_results: list['WeeklyResult'] = Field(default_factory=list)
    confirmation_usage: list['ConfirmationUsage'] = Field(default_factory=list)
    reconciled_cells: list['ReconciledCell'] = Field(default_factory=list)
    output_totals: 'OutputTotals | None' = None
    excluded_demands: list[WeeklyDemand] = Field(default_factory=list)
    stability: StabilityResult | None = None


class SourcePlan(InputRow):
    segments: list[SourceSegment] = Field(default_factory=list)
    initial_states: list[InitialRecipeState] = Field(default_factory=list)


class SourceEvaluationSnapshot(EvaluationSnapshot):
    plan: SourcePlan


class CertificatePreviewResult(InputRow):
    plan: Plan | None
    evaluation: EvaluationResult | None
    issues: list[RuleIssue] = Field(default_factory=list)
