from uuid import UUID
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import JSONResponse
from .app import require_editor, require_viewer
from .input_models import InputRow
from .plans import PlanService, PlanCommand, SubmitPlan, PlanProblem, PlanBusy, PlanUnknown

router=APIRouter()


def service(request):
    if request.app.state.plan_store is None:
        from .oracle_plans import OraclePlanStore
        request.app.state.plan_store=OraclePlanStore(request.app.state.access_store.pool)
    return PlanService(request.app.state.plan_store)


def invoke(action):
    try: return action()
    except PlanProblem as error:
        return JSONResponse({'detail':error.detail,**error.data},status_code=error.status)
    except PlanBusy:
        return JSONResponse({'detail':'系统正忙，其他用户正在保存或采用，请稍后重试','state':'busy'},status_code=503)
    except PlanUnknown:
        return JSONResponse({'detail':'保存结果未知，请保持只读并核实前次操作','state':'unknown'},status_code=503)
    except KeyError:
        raise HTTPException(404,'版本不存在') from None


@router.get('/plans/current')
def current(request:Request,principal=Depends(require_viewer)):
    return invoke(lambda:service(request).current(principal.user_id,request.app.state.clock()))


@router.get('/plans/revisions/{revision}')
def revision(revision:int,request:Request,principal=Depends(require_viewer)):
    return invoke(lambda:service(request).revision(revision,principal.user_id))


@router.get('/plans/adoptions/{adoption}')
def adoption(adoption:int,request:Request,principal=Depends(require_viewer)):
    return invoke(lambda:service(request).adoption(adoption,principal.user_id))


@router.post('/plans/preview')
def preview(payload:PlanCommand,request:Request,principal=Depends(require_editor)):
    # PlanService already serializes its complete typed result in JSON mode.
    # Render it once rather than recursively converting the same large ledger.
    return invoke(lambda:JSONResponse(service(request).preview(payload,principal.user_id,request.app.state.clock())))


@router.post('/plans/save')
def save(payload:SubmitPlan,request:Request,principal=Depends(require_editor)):
    return invoke(lambda:service(request).submit(payload,principal.user_id,request.app.state.clock(),'save'))


@router.post('/plans/adopt')
def adopt(payload:SubmitPlan,request:Request,principal=Depends(require_editor)):
    return invoke(lambda:service(request).submit(payload,principal.user_id,request.app.state.clock(),'adopt'))


class Verification(InputRow):
    operation_id:UUID


@router.post('/plans/verify')
def verify(payload:Verification,request:Request,principal=Depends(require_editor)):
    return invoke(lambda:service(request).verify(payload.operation_id,principal.user_id,request.app.state.clock()))
