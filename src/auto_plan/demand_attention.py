"""Demand handling derived from the authoritative output attribution ledger."""
from datetime import timedelta
from .plan_models import ConfirmationUsage, DemandAttention, MissingDemandInput, WeeklyResult
from .production_time import production_cutoff, production_date


PARAMETERS = {
    'line_yield': ('line_yields', '缺少机种月度线体良率'),
    'model_yield': ('model_yields', '缺少机种月度机种良率'),
    'cycle_time': ('cycle_times', '缺少机种 Cycle Time'),
}


def missing_inputs(snapshot, weekly, issues, index):
    missing = {}

    def add(kind, month):
        category, reason = PARAMETERS[kind]
        month = None if kind == 'cycle_time' else month
        missing[category, month] = MissingDemandInput(category=category, model_code=weekly.model_code,
                                                     month=month, reason=reason)

    for issue in issues:
        if issue.severity != 'incomplete' or issue.model_code != weekly.model_code:
            continue
        kind = issue.code.removeprefix('missing_')
        if kind in PARAMETERS:
            add(kind, issue.yield_month or (issue.date.strftime('%Y-%m') if issue.date else None))
    # Unscheduled demand has no output source from which the output evaluator
    # could discover missing parameters. Check prospective input months without
    # changing the completeness of already evaluated output or writing inputs.
    if weekly.demand > weekly.attributed:
        start = max(snapshot.time.window_start, production_date(snapshot.time.as_of))
        end = max(start, min(snapshot.time.window_end, weekly.week + timedelta(days=6)))
        months = {(start + timedelta(days=n)).strftime('%Y-%m') for n in range((end-start).days+1)}
        for month in sorted(months):
            for kind, value in index.critical_parameters(month, weekly.model_code).items():
                if value['state'] == 'missing':
                    add(kind, month)
    return list(missing.values())


def demand_attention(snapshot, output, issues, index):
    rows = []
    bans = set()
    for value in output['confirmation_usage']:
        usage = ConfirmationUsage.model_validate(value)
        if usage.banned:
            bans.add((usage.model_code, usage.week, usage.kind))
    for value in output['weekly_results']:
        weekly = WeeklyResult.model_validate(value)
        missing = missing_inputs(snapshot, weekly, issues, index)
        complete = weekly.computation_complete and not missing
        target = max(weekly.demand - weekly.attributed, 0) if complete else None
        reasons = []
        if target:
            reasons.append('unarranged')
        if weekly.pending_material:
            reasons.append('pending_material')
        if weekly.pending_shipping:
            reasons.append('pending_shipping')
        for kind in ('material', 'shipping'):
            if (weekly.model_code, weekly.week, kind) in bans:
                reasons.append('forbidden_' + kind)
        if weekly.late:
            reasons.append('confirmed_late')
        if not complete:
            reasons.append('incomplete')
        if reasons:
            rows.append(DemandAttention(**weekly.model_dump(), deadline=production_cutoff(weekly.week+timedelta(days=7)), unarranged_output=target,
                                        planning_complete=complete, reasons=reasons, missing_inputs=missing))
    return rows
