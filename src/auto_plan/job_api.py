"""Public durable calculation API, sharing the application's Oracle pool."""
from uuid import UUID
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from .app import current_principal, require_editor, require_viewer
from .jobs import JobService, SubmitJob, JobKey, JobUnknown
from .shared_plan_api import service as plan_service, invoke

router = APIRouter()


def store(request):
    app = request.app
    if getattr(app.state, 'job_store', None) is None:
        from .oracle_jobs import OracleJobStore
        from .job_process import worker_allocation
        app.state.job_store = OracleJobStore(app.state.access_store.pool,
            queue_capacity=app.state.settings.job_queue_capacity,
            worker_limit=worker_allocation(app.state.settings.job_workers))
        # Authorization and queued/running cancellation commit together.
        app.state.access_store.job_revocation = app.state.job_store.revoke_in_transaction
    return app.state.job_store


def configure_jobs(app):
    """Construct lifecycle components; caller owns readiness and startup order."""
    from .job_process import JobController,worker_allocation
    from .oracle_jobs import OracleJobStore
    if app.state.job_store is None:
        app.state.job_store=OracleJobStore(app.state.access_store.pool,
            queue_capacity=app.state.settings.job_queue_capacity,
            worker_limit=worker_allocation(app.state.settings.job_workers))
    app.state.access_store.job_revocation=app.state.job_store.revoke_in_transaction
    if app.state.job_controller is None:
        app.state.job_controller=JobController(app.state.job_store,
            workers=app.state.settings.job_workers,clock=app.state.clock)
    return app.state.job_controller


def service(request):
    return JobService(store(request), plan_service(request))


def call(action):
    try:
        return invoke(action)
    except JobUnknown:
        return JSONResponse({'detail': '任务操作结果未知，请核实原提交编号', 'state': 'unknown'}, status_code=503)


def admitting(request):
    controller = getattr(request.app.state, 'job_controller', None)
    if controller is not None and not controller.accepting:
        from .plans import PlanProblem
        raise PlanProblem(503, '计算服务正在停止接收任务', state='closed')


@router.post('/jobs')
def submit(payload: SubmitJob, request: Request, principal=Depends(require_editor)):
    def action():
        admitting(request)
        return service(request).submit(payload, principal.user_id, request.app.state.clock())
    return call(action)


@router.post('/jobs/verify')
def verify(payload: JobKey, request: Request, principal=Depends(require_editor)):
    return call(lambda: service(request).verify(payload.submission_id, principal.user_id, request.app.state.clock()))


@router.get('/jobs')
def jobs(request: Request, principal=Depends(require_viewer)):
    return call(lambda: service(request).list(principal.user_id, request.app.state.clock()))


@router.get('/jobs/{job_id}')
def get(job_id: UUID, request: Request, principal=Depends(require_viewer)):
    return call(lambda: service(request).get(str(job_id), principal.user_id, request.app.state.clock()))


@router.post('/jobs/{job_id}/stop')
def stop(job_id: UUID, request: Request, principal=Depends(current_principal)):
    def action():
        result=service(request).stop(str(job_id), principal.user_id, request.app.state.clock())
        controller=getattr(request.app.state,'job_controller',None)
        if controller and result.get('stop_requested_at'):
            from datetime import datetime
            controller.signal_stop(str(job_id),datetime.fromisoformat(result['stop_requested_at']))
        return result
    return call(action)


@router.post('/jobs/{job_id}/rerun')
def rerun(job_id: UUID, payload: JobKey, request: Request, principal=Depends(require_editor)):
    def action():
        admitting(request)
        return service(request).rerun(str(job_id), payload, principal.user_id, request.app.state.clock())
    return call(action)


@router.get('/jobs/{job_id}/candidates/{candidate_id}')
def candidate(job_id: UUID, candidate_id: UUID, request: Request, principal=Depends(require_viewer)):
    return call(lambda: service(request).candidate(str(job_id), str(candidate_id), principal.user_id, request.app.state.clock()))
