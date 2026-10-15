from fastapi import APIRouter, Depends, Request
from .app import require_editor
from .input_models import InputRow
from .plan_models import (EvaluationSnapshot, EvaluationResult, EvaluationTime,
                          SourceEvaluationSnapshot, CertificatePreviewResult)
from .rules import evaluate
from .certificate import construct_hour_certificate, CertificateError

router = APIRouter()


class PreviewSnapshot(EvaluationSnapshot):
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
    time: EvaluationTime | None = None


class CertificatePreview(InputRow):
    snapshot: SourcePreviewSnapshot


@router.post('/plans/certificate/preview', response_model=CertificatePreviewResult)
def preview_certificate(payload: CertificatePreview, request: Request, principal=Depends(require_editor)):
    source = payload.snapshot
    frozen_time = source.time or EvaluationTime.at(request.app.state.clock())
    try:
        plan = construct_hour_certificate(source.inputs, source.plan.segments, source.plan.initial_states,
                                          window_start=frozen_time.window_start)
    except CertificateError as error:
        return CertificatePreviewResult(plan=None, evaluation=None, issues=error.issues)
    snapshot = frozen_snapshot(source, request, plan=plan)
    return CertificatePreviewResult(plan=plan, evaluation=evaluate(snapshot))
