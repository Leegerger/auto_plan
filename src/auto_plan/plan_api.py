from fastapi import APIRouter, Depends, Request
from .app import require_editor
from .input_models import CurrentInputSnapshot, InputRow
from .plan_models import (EvaluationSnapshot, EvaluationResult, EvaluationTime,
                          SourceEvaluationSnapshot, CertificatePreviewResult)
from .rules import evaluate
from .certificate import construct_hour_certificate, CertificateError

router = APIRouter()


class PreviewSnapshot(EvaluationSnapshot):
    inputs: CurrentInputSnapshot
    time: EvaluationTime | None = None


class PlanPreview(InputRow):
    snapshot: PreviewSnapshot


def frozen_snapshot(source, request, *, plan=None):
    values = source.model_dump() | {'time': source.time or EvaluationTime.at(request.app.state.clock())}
    if plan is not None:
        values['plan'] = plan
    return EvaluationSnapshot.model_validate(values)


@router.post('/plans/evaluate', response_model=EvaluationResult)
def preview_plan(payload: PlanPreview, request: Request, principal=Depends(require_editor)):
    return evaluate(frozen_snapshot(payload.snapshot, request))


class SourcePreviewSnapshot(SourceEvaluationSnapshot):
    inputs: CurrentInputSnapshot
    time: EvaluationTime | None = None


class CertificatePreview(InputRow):
    snapshot: SourcePreviewSnapshot


@router.post('/plans/certificate/preview', response_model=CertificatePreviewResult)
def preview_certificate(payload: CertificatePreview, request: Request, principal=Depends(require_editor)):
    source = payload.snapshot
    frozen_time = source.time or EvaluationTime.at(request.app.state.clock())
    from fractions import Fraction
    from .line_states import certificate_anchors, active_unfinished_jobs
    from .execution_rules import confirmed_day_capacity
    from .production_time import production_date
    frozen = source.model_copy(update={'time': frozen_time})
    unfinished = active_unfinished_jobs(frozen)
    today = production_date(frozen_time.as_of)
    # A standalone construction cannot borrow unconfirmed current-day time.
    # Valid budgets still require the declared current-day witness at evaluation.
    limits = {}
    for line in unfinished:
        budget = confirmed_day_capacity(frozen, line)
        limits[today, line] = Fraction(budget.remaining_hours) if budget is not None else Fraction(0)
    try:
        plan = construct_hour_certificate(source.inputs, source.plan.segments, source.plan.initial_states,
                                          window_start=frozen_time.window_start, confirmed_states=certificate_anchors(frozen),
                                          unfinished_jobs=unfinished.values(), end_date=frozen_time.window_end,
                                          unfinished_start=today, day_limits=limits)
    except CertificateError as error:
        return CertificatePreviewResult(plan=None, evaluation=None, issues=error.issues)
    snapshot = frozen_snapshot(source, request, plan=plan)
    return CertificatePreviewResult(plan=plan, evaluation=evaluate(snapshot))
