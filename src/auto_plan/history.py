"""Immutable selected records and the shared-draft recovery boundary."""
from datetime import datetime, timedelta

RECOVERY_DAYS = 30


def recovery_available(record, as_of):
    return record.get('kind') == 'adopt' or (
        bool(record.get('recorded_at')) and
        datetime.fromisoformat(record['recorded_at']) >= as_of - timedelta(days=RECOVERY_DAYS))


def resolve_history_reference(unit, source, as_of):
    from .plans import PlanProblem
    try:
        record = unit.get_adoption(source.id) if source.kind == 'adoption' else unit.get_revision(source.id)
    except KeyError:
        raise PlanProblem(404, '历史版本不存在') from None
    if source.kind != 'adoption' and not recovery_available(record, as_of):
        raise PlanProblem(410, '共享修订已超过30天恢复期限，请选择长期保留的采用版')
    return record
