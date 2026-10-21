from uuid import UUID
from fastapi import APIRouter, Depends, Request, HTTPException, Query
from fastapi.responses import JSONResponse, Response
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
    # The service has already converted its complete result to JSON values.
    return invoke(lambda:JSONResponse(service(request).current(principal.user_id,request.app.state.clock())))


@router.get('/plans/compare')
def compare(request:Request,base_revision:int=Query(ge=0),input_version:int=Query(ge=0),principal=Depends(require_viewer)):
    return invoke(lambda:service(request).compare(base_revision,input_version,principal.user_id))


@router.get('/plans/revisions/{revision}')
def revision(revision:int,request:Request,principal=Depends(require_viewer)):
    return invoke(lambda:JSONResponse(service(request).revision(revision,principal.user_id)))


@router.get('/plans/history')
def history(request:Request,before:int|None=Query(default=None,ge=1),limit:int=Query(default=50,ge=1,le=100),principal=Depends(require_viewer)):
    return invoke(lambda:service(request).history(principal.user_id,request.app.state.clock(),before,limit))


@router.get('/plans/adoptions/{adoption}')
def adoption(adoption:int,request:Request,principal=Depends(require_viewer)):
    return invoke(lambda:JSONResponse(service(request).adoption(adoption,principal.user_id)))


def export_record(record, kind, identity):
    from .history_export import workbook_bytes
    return Response(workbook_bytes(record), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition':f'attachment; filename="auto-plan-{kind}-{identity}.xlsx"'})


@router.get('/plans/revisions/{revision}/export.xlsx')
def export_revision(revision:int,request:Request,principal=Depends(require_viewer)):
    return invoke(lambda:export_record(service(request).revision(revision,principal.user_id),'revision',revision))


@router.get('/plans/adoptions/{adoption}/export.xlsx')
def export_adoption(adoption:int,request:Request,principal=Depends(require_viewer)):
    return invoke(lambda:export_record(service(request).adoption(adoption,principal.user_id),'adoption',adoption))


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


from datetime import date, timedelta
from .input_models import Code
from .manual_operations import EditingScope, AppendFill, append_fill
from .plan_models import EvaluationSnapshot
from .plan_operations import OperationContext
from .input_lookup import InputIndex


class FillOptions(PlanCommand):
    model_code: Code
    start_date: date
    end_date: date
    line_code: Code | None = None
    scope: EditingScope


@router.post('/plans/fill-options')
def fill_options(payload:FillOptions,request:Request,principal=Depends(require_editor)):
    planner=service(request)
    def action(unit):
        prepared=planner._prepare(unit,PlanCommand.model_validate(payload.model_dump(include=set(PlanCommand.model_fields))),request.app.state.clock())
        snapshot=EvaluationSnapshot.model_validate(prepared['snapshot'])
        index=InputIndex(snapshot.inputs)
        if payload.model_code not in index.models or not index.models[payload.model_code].active:
            raise PlanProblem(422,'请选择有效机种')
        if not snapshot.time.window_start<=payload.start_date<=payload.end_date<=snapshot.time.window_end:
            raise PlanProblem(422,'追加日期必须位于当周至第十五周')
        dates=[payload.start_date+timedelta(days=n) for n in range((payload.end_date-payload.start_date).days+1)]
        recipe=index.models[payload.model_code].recipe_code
        maxima={line:sum(capacity['pieces'] for day in dates if (capacity:=index.capacity(day.strftime('%Y-%m'),recipe,line))['state']=='known')
            for line,value in index.lines.items() if value.active}
        lines=[{'code':line,'name':index.lines[line].name} for line,maximum in maxima.items() if maximum>0]
        days=[]
        if payload.line_code:
            if payload.line_code not in {row['code'] for row in lines}:
                raise PlanProblem(422,'所选线体在范围内没有已知且允许的正产能')
            command=AppendFill(kind='append_fill',scope=payload.scope,model_code=payload.model_code,quantity=maxima[payload.line_code],
                line_code=payload.line_code,start_date=payload.start_date,end_date=payload.end_date)
            try: result=append_fill(snapshot.plan,command,OperationContext(snapshot,action_id='capacity-probe'))
            except ValueError as error: raise PlanProblem(422,str(error)) from None
            previous={r.id for r in snapshot.plan.segments}
            placed={r.date:r.quantity for r in result.plan.segments if r.id not in previous}
            days=[{'date':day.isoformat(),'line_code':payload.line_code,'quantity':placed.get(day,0)} for day in dates]
        return {'lines':lines,'days':days,'notice':'可追加数量仅表示指定范围的精确容量；执行前须完整预览需求、保护与确认状态'}
    return invoke(lambda:planner.store.read(principal.user_id,action))
