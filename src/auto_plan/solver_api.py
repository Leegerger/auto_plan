"""Read-only candidate preview; durable submission belongs to the jobs module."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from .app import require_editor
from .input_models import InputRow, Pieces
from .plans import PlanProblem
from .shared_plan_api import service, invoke
from .solver_models import FrozenSolveInput, SolveRequest
from .solver import solve

router = APIRouter()


class CandidatePreview(InputRow):
    base_revision: Pieces
    input_version: Pieces
    latest_adoption: Pieces
    request: SolveRequest


def freeze(planner, payload, actor, now):
    def read(unit):
        if (payload.base_revision, payload.input_version, payload.latest_adoption) != (unit.revision, unit.inputs.input_version, unit.adoption):
            raise PlanProblem(409, '共享草稿、完整输入或采用版已变化，请刷新后提交')
        return FrozenSolveInput(snapshot=planner._snapshot(unit, unit.plan, now), source_revision=unit.revision,
            input_version=unit.inputs.input_version, latest_adoption=unit.adoption, request=payload.request)
    return planner.store.read(actor, read)


@router.post('/plans/candidates/preview')
def preview(payload: CandidatePreview, request: Request, principal=Depends(require_editor)):
    def action():
        frozen = freeze(service(request), payload, principal.user_id, request.app.state.clock())
        return JSONResponse(solve(frozen).model_dump(mode='json'))
    return invoke(action)
