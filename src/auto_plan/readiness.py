"""One startup decision and one absolute process shutdown deadline."""
from dataclasses import dataclass
import logging
import time

log = logging.getLogger('auto_plan.runtime')
REASONS = {
    'starting': '应用正在启动，请稍后重试',
    'configuration': '运行配置无效，请联系维护者检查配置',
    'database': '数据库连接不可用，请联系维护者检查连接',
    'schema': '数据库迁移版本或校验和不兼容，请联系维护者检查迁移记录',
    'session_recovery': '旧会话失效处理失败，请联系维护者检查数据库',
    'input_integrity': '输入版本或上下文一致性检查失败，请联系维护者检查恢复点',
    'plan_integrity': '共享计划、采用版或幂等记录一致性检查失败，请联系维护者检查恢复点',
    'demand_integrity': '需求历史或幂等记录一致性检查失败，请联系维护者检查恢复点',
    'job_integrity': '计算任务或候选记录一致性检查失败，请联系维护者检查恢复点',
    'job_recovery': '计算任务中断恢复失败，请联系维护者检查数据库',
    'stopping': '应用正在停止，请稍后重新连接',
}


@dataclass
class Readiness:
    ready: bool = False
    reason_code: str = 'starting'
    deadline: float | None = None

    def public(self):
        return {'status': 'ready' if self.ready else 'unavailable', 'writes_enabled': self.ready,
                'reason_code': None if self.ready else self.reason_code,
                'detail': '服务可用' if self.ready else REASONS[self.reason_code]}

    def check(self, code, action):
        self.reason_code = code
        action()

    def begin_shutdown(self, controller=None):
        if self.deadline is None:
            self.deadline = time.monotonic() + 30
        self.ready = False
        self.reason_code = 'stopping'
        if controller is not None:
            controller.close_admission()
            if hasattr(controller,'begin_shutdown'): controller.begin_shutdown(self.deadline)
        return self.deadline


def initialize(app, *, owned):
    state=app.state
    readiness=state.readiness
    try:
        if owned:
            from .oracle_access import OracleAccessStore
            readiness.check('database', lambda: setattr(state, 'access_store', OracleAccessStore.connect(state.settings)))
        if state.authenticator is None:
            from .company_auth import CompanyAuthenticator, TestAuthenticator
            readiness.check('configuration', lambda: setattr(state,'authenticator',TestAuthenticator(state.settings) if state.settings.test_auth_enabled else CompanyAuthenticator(state.settings)))
        if owned:
            from .schema import verify_schema
            from .oracle_inputs import OracleInputStore
            from .oracle_plans import OraclePlanStore
            from .oracle_demands import OracleDemandStore
            from .job_api import configure_jobs
            readiness.check('schema', lambda: verify_schema(state.access_store.pool))
            state.input_store=OracleInputStore(state.access_store.pool)
            state.plan_store=OraclePlanStore(state.access_store.pool)
            state.demand_store=OracleDemandStore(state.plan_store)
            configure_jobs(app)
        # Invalidate sessions even if a later integrity check refuses writes.
        readiness.check('session_recovery', state.access_store.startup)
        state.access_ready=True
        for code,store in [('input_integrity',state.input_store),('plan_integrity',state.plan_store),('demand_integrity',state.demand_store),('job_integrity',state.job_controller)]:
            if store is not None and hasattr(store,'check_consistency'):
                readiness.check(code, store.check_consistency)
        if state.job_controller is not None:
            readiness.check('job_recovery',state.job_controller.recover_startup)
            state.job_controller.start()
        readiness.ready=True
        log.info('startup ready')
    except Exception:
        # Exception strings, validation inputs and DB messages can contain secrets.
        log.error('startup refused reason_code=%s', readiness.reason_code)
        if state.job_controller is not None:
            state.job_controller.close_admission()
