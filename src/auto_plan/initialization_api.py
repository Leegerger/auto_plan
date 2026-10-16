from uuid import UUID
from dataclasses import dataclass
from datetime import datetime
from fastapi import APIRouter, Depends, Request
from .app import require_editor, require_viewer
from .shared_plan_api import service, invoke
from .initialization import InitialImport, prepare_initial
from .inputs import InputConflict
from .plans import PlanProblem
from .input_models import InputRow, InputSnapshot
router=APIRouter()


@dataclass(frozen=True)
class _InitialSource:
    """Request-owned values, with no unit/cursor retained after capture."""
    inputs: InputSnapshot
    revision: int
    adoption: int
    as_of: datetime

    def prepare(self,payload):
        head={'revision':self.revision,'latest_adoption':self.adoption}
        try: return prepare_initial(self.inputs,head,payload,self.as_of)
        except InputConflict: raise PlanProblem(409,'输入资料已变化，请重新预览首次接入') from None


def _initial_source(request,unit):
    return _InitialSource(unit.inputs,unit.revision,unit.adoption,request.app.state.clock())


def prepare(request,payload,actor,source=None):
    if source is not None: return source.prepare(payload)
    return service(request).store.read(actor,lambda unit:_initial_source(request,unit).prepare(payload))


@router.post('/initialization/preview')
def preview(payload:InitialImport,request:Request,principal=Depends(require_editor)):
    return invoke(lambda:prepare(request,payload,principal.user_id))


def demand_store(request):
    if request.app.state.demand_store is None:
        from .oracle_demands import OracleDemandStore
        request.app.state.demand_store=OracleDemandStore(service(request).store)
    return request.app.state.demand_store


class InitialSubmission(InitialImport):
    operation_id: UUID


@router.post('/initialization/apply')
def initialize(payload:InitialSubmission,request:Request,principal=Depends(require_editor)):
    from .demands import DemandRow, file_record
    from .input_models import InputSnapshot
    from .plan_models import Plan
    from .plans import PlanningContext
    from .production_time import BEIJING
    plans=service(request); actor=principal.user_id; now=request.app.state.clock()
    def action(unit):
        try: prepared=prepare_initial(unit.inputs,{'revision':unit.revision,'latest_adoption':unit.adoption},payload,now)
        except InputConflict: raise PlanProblem(409,'输入资料已变化，请重新预览首次接入') from None
        if not prepared['valid']: raise PlanProblem(422,'整批未提交，请修正首次接入资料',errors=prepared['errors'])
        rows=[DemandRow(model_code=r['model_code'],week=r['week'],delta=r['quantity'],source_row=i) for i,r in enumerate(prepared['inputs']['business']['demands'],2)]
        record=file_record(rows,now.astimezone(BEIJING).date(),payload.operation_id,actor,now,1)
        lineage=prepared['lineage']|{'demand_initial_file':record['file_id']}
        result=plans.store.initialize_in_transaction(unit.cursor,actor,now,
            InputSnapshot.model_validate(prepared['inputs']),PlanningContext.model_validate(prepared['context']),
            Plan.model_validate(prepared['plan']),lineage,payload.operation_id)
        demand_store(request).insert_file(unit.cursor,record,result['input_version'])
        return result
    return invoke(lambda:plans.store.submit(payload,actor,now,'initialize',action))


@router.get('/demands/files')
def demand_files(request:Request,principal=Depends(require_viewer)):
    from .demands import DemandService
    return invoke(lambda:DemandService(demand_store(request)).files(principal.user_id))


from .demands import DemandCommand, SubmitDemand


@router.post('/demands/preview')
def demand_preview(payload:DemandCommand,request:Request,principal=Depends(require_editor)):
    from .demands import DemandService
    return invoke(lambda:DemandService(demand_store(request)).preview(payload,principal.user_id,request.app.state.clock()))


@router.post('/demands/apply')
def demand_apply(payload:SubmitDemand,request:Request,principal=Depends(require_editor)):
    from .demands import DemandService
    return invoke(lambda:DemandService(demand_store(request)).apply(payload,principal.user_id,request.app.state.clock()))


class DemandVerification(InputRow):
    operation_id: UUID


@router.post('/demands/verify')
def demand_verify(payload:DemandVerification,request:Request,principal=Depends(require_editor)):
    return invoke(lambda:demand_store(request).verify(str(payload.operation_id),principal.user_id,request.app.state.clock()))


from fastapi import HTTPException
from fastapi.responses import Response, JSONResponse
from pydantic import Field
from .input_models import Pieces


@router.get('/initialization/template.xlsx')
def initial_template(principal=Depends(require_viewer)):
    from .initialization_tables import template_bytes
    return Response(template_bytes(),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="auto-plan-initialization.xlsx"'})


def adapted_initial(request,payload,errors,actor,source=None):
    from pydantic import ValidationError
    try: typed=InitialImport.model_validate(payload)
    except ValidationError:
        return {'valid':False,'errors':errors+[{'sheet':'workbook','row':1,'field':'file','reason':'工作簿资料格式无效'}],'payload':payload}
    result=prepare(request,typed,actor,source)
    result['errors']=errors+result['errors'];result['valid']=result['valid'] and not result['errors']
    result['payload']=payload
    return result


async def upload_bytes(request):
    from .input_tables import MAX_UPLOAD
    content=bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content)>MAX_UPLOAD: raise HTTPException(413,'工作簿最大10MB')
    return bytes(content)


@router.post('/initialization/excel/preview')
async def initial_excel(request:Request,base_revision:int,input_version:int,latest_adoption:int,principal=Depends(require_editor)):
    from .initialization_tables import excel_bundle
    from fastapi.encoders import jsonable_encoder
    from starlette.concurrency import run_in_threadpool
    content=await upload_bytes(request)
    def action():
        # The authorized store read pins R/A/V atomically and closes before
        # workbook decoding. Later writers cannot change this owned preview.
        source=service(request).store.read(principal.user_id,lambda unit:_initial_source(request,unit))
        payload,errors=excel_bundle(content,{'base_revision':base_revision,'input_version':input_version,'latest_adoption':latest_adoption})
        result=adapted_initial(request,payload,errors,principal.user_id,source)
        # Validate original Excel types before normalizing the echoed payload.
        # Prepared plan/evaluation values are already in JSON mode.
        result['payload']=jsonable_encoder(result['payload'])
        return JSONResponse(result)
    return await run_in_threadpool(lambda:invoke(action))


class InitialPaste(InputRow):
    base_revision: Pieces
    input_version: Pieces
    latest_adoption: Pieces
    tables: dict[str,str]


@router.post('/initialization/paste/preview')
def initial_paste(payload:InitialPaste,request:Request,principal=Depends(require_editor)):
    from .initialization_tables import pasted_bundle
    tokens=payload.model_dump(exclude={'tables'})
    bundle,errors=pasted_bundle(payload.tables,tokens)
    return invoke(lambda:adapted_initial(request,bundle,errors,principal.user_id))


@router.get('/demands/template.xlsx')
def demands_template(principal=Depends(require_viewer)):
    from .initialization_tables import template_bytes
    return Response(template_bytes(True),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="auto-plan-demand-changes.xlsx"'})


class DemandPaste(InputRow):
    input_version: Pieces
    attribution_date: str | None = None
    text: str = Field(max_length=10*1024*1024)


def adapted_demand(request,tables,errors,tokens,actor):
    from .demands import DemandService
    from pydantic import ValidationError
    payload=tokens|{'rows':tables.get('demand_changes',[])}
    try: typed=DemandCommand.model_validate(payload)
    except ValidationError:
        return {'valid':False,'errors':errors+[{'sheet':'demand_changes','row':1,'field':'attribution_date','reason':'变更归属日期或输入版本格式无效'}],'payload':payload}
    result=DemandService(demand_store(request)).preview(typed,actor,request.app.state.clock())
    result['errors']=errors+result['errors'];result['valid']=not result['errors'];result['payload']=payload
    return result


@router.post('/demands/paste/preview')
def demands_paste(payload:DemandPaste,request:Request,principal=Depends(require_editor)):
    from .input_tables import paste_rows
    from .initialization_tables import DEMAND_HEADERS
    tables,errors=paste_rows('demand_changes',payload.text,DEMAND_HEADERS)
    return invoke(lambda:adapted_demand(request,tables,errors,payload.model_dump(exclude={'text'}),principal.user_id))


@router.post('/demands/excel/preview')
async def demands_excel(request:Request,input_version:int,attribution_date:str|None=None,principal=Depends(require_editor)):
    from .input_tables import excel_rows
    from .initialization_tables import DEMAND_HEADERS
    from starlette.concurrency import run_in_threadpool
    content=await upload_bytes(request)
    def action():
        tables,errors=excel_rows(content,DEMAND_HEADERS)
        return adapted_demand(request,tables,errors,{'input_version':input_version,'attribution_date':attribution_date},principal.user_id)
    return await run_in_threadpool(lambda:invoke(action))
