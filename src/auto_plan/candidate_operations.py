"""Server-owned candidate references replay through the shared plan transaction."""
from typing import Literal
from uuid import UUID
from pydantic import Field
from .input_models import InputRow
from .plan_operations import PatchPlan, patch, register_operation, ReplayResult
from .manual_operations import _authorize_change


class ApplyCandidate(InputRow):
    kind: Literal['apply_candidate']
    job_id: UUID
    candidate_id: UUID
    payload_sha: str = Field(pattern=r'^[a-f0-9]{64}$')
    selection: list[str] | None = Field(default=None,max_length=10000)


def validate_change(before,after,request,context):
    from .plans import protect
    scope=request.scope
    protect(before,after,context.snapshot)
    _authorize_change(before,after,scope,context)
    prior={r.id:r for r in before.segments};current={r.id:r for r in after.segments}
    if request.mode=='new_week' and any(current.get(identity)!=row for identity,row in prior.items()):
        raise ValueError('新增周计算必须保留原有安排')
    if before.initial_states!=after.initial_states:
        raise ValueError('候选不能改写起始 Recipe')
    old={r.id:r for r in before.switches};new={r.id:r for r in after.switches}
    for identity in old.keys()|new.keys():
        if old.get(identity)==new.get(identity): continue
        for row in (old.get(identity),new.get(identity)):
            if row and (request.mode=='new_week' and identity in old or row.line_code not in scope.line_codes or any(not scope.start_date<=a.date<=scope.end_date for a in row.allocations)):
                raise ValueError('候选修改范围外切机，请扩大调整范围')
    for field in ['ordinary_idle','current_day_witnesses']:
        old={(r.date,r.line_code):r for r in getattr(before,field)}
        new={(r.date,r.line_code):r for r in getattr(after,field)}
        for key in old.keys()|new.keys():
            if old.get(key)!=new.get(key) and (key[1] not in scope.line_codes or not scope.start_date<=key[0]<=scope.end_date):
                raise ValueError('候选修改范围外工时依据')


def selected_patch(operation,selection):
    if not selection or len(selection)!=len(set(selection)):
        raise ValueError('请选择不重复的候选变更')
    available={}
    for field,rows in operation.model_dump(mode='json').items():
        if field in {'kind','current_day_witnesses'}: continue
        for row in rows:
            if field in {'segments','switches'}: identity=row['id']
            elif field in {'remove_segments','remove_switches'}: identity=row
            elif field=='initial_states': identity=row['line_code']
            else: identity=row['date']+':'+row['line_code']
            available[field+':'+identity]=(field,row)
    if set(selection)-available.keys(): raise ValueError('所选变更不属于该候选')
    output={'kind':'patch_plan'}
    for identity in selection:
        field,row=available[identity]
        output.setdefault(field,[]).append(row)
    return PatchPlan.model_validate(output)


def apply_candidate(plan,command,context):
    from .plans import PlanProblem
    candidate=context.history.get_candidate(str(command.job_id),str(command.candidate_id),command.payload_sha,context.time.as_of)
    if candidate.source_revision!=context.source_revision:
        raise PlanProblem(409,'候选须使用原始共享修订重检，不能更换基准跳过冲突',base_revision=candidate.source_revision)
    if candidate.input_version!=context.inputs.input_version:
        raise PlanProblem(409,'候选输入已变化，仅可查看或按原始输入重跑',input_version=context.inputs.input_version)
    operation=PatchPlan.model_validate(candidate.operation)
    if command.selection is not None: operation=selected_patch(operation,command.selection)
    proposed=patch(plan,operation,context)
    if command.selection is not None:
        from .execution_rules import execution_certificate
        proposed=execution_certificate(proposed,context,candidate.request.scope)
    validate_change(plan,proposed,candidate.request,context)
    return ReplayResult(proposed,lineage={'candidate_application':{
        'job_id':str(command.job_id),'candidate_id':str(command.candidate_id),'payload_sha':command.payload_sha,
        'source_revision':candidate.source_revision,'input_version':candidate.input_version,'selection':command.selection,
        'applied_patch':operation.model_dump(mode='json')}})


register_operation('apply_candidate',ApplyCandidate,apply_candidate)
