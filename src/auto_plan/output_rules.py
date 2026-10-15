"""Deterministic output sources and FIFO demand attribution over frozen inputs."""
from collections import defaultdict, Counter
from datetime import date, timedelta
from fractions import Fraction
from decimal import Decimal, localcontext, ROUND_HALF_UP
from bisect import bisect_right
from .input_lookup import InputIndex
from .production_time import production_date, production_cutoff
from .plan_models import (RuleIssue, ReconciledCell, OutputSource, OutputPortion,
                          OutputAttribution, WeeklyResult, OutputTotals, ConfirmationUsage, BatchUse)


def ceil(value):
    return -(-value.numerator // value.denominator)


def rational(value):
    value = Fraction(value)
    return str(value.numerator) if value.denominator == 1 else f'{value.numerator}/{value.denominator}'


def rate_display(covered, demand):
    if not demand:
        return '不适用'
    with localcontext() as context:
        context.prec = max(len(str(covered)), len(str(demand))) + 10
        value = Decimal(covered) * 100 / Decimal(demand)
        return str(value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)) + '%'


def _reconcile(snapshot, index):
    issues = []
    cells = []
    plan = defaultdict(int)
    for segment in snapshot.plan.segments:
        plan[(segment.date, segment.line_code, segment.model_code)] += segment.quantity
    facts = {(row.date, row.line_code, row.model_code): row.quantity for row in snapshot.execution.facts}
    from .execution_rules import executed_by_segment
    executed=executed_by_segment(snapshot.plan,snapshot.execution)
    unexecuted=defaultdict(int)
    for segment in snapshot.plan.segments:
        unexecuted[segment.date,segment.line_code,segment.model_code]+=max(0,segment.quantity-executed.get(segment.id,0))
    today = production_date(snapshot.time.as_of)
    cutoff = snapshot.execution.cutoff
    if cutoff is not None and cutoff > snapshot.time.as_of:
        issues.append(RuleIssue(code='execution_cutoff', reason='执行事实截止时点晚于冻结计算时点'))
    valid_cutoff = cutoff is not None and cutoff <= snapshot.time.as_of
    fact_keys = [(r.date, r.line_code, r.model_code) for r in snapshot.execution.facts]
    for key, count in Counter(fact_keys).items():
        if key[1] not in index.lines or key[2] not in index.models:
            issues.append(RuleIssue(code='unknown_master', reason='执行事实引用不存在的线体或机种', date=key[0], line_code=key[1], model_code=key[2]))
        if count > 1:
            issues.append(RuleIssue(code='execution_duplicate', reason='累计事实组合重复，不能重复相加', date=key[0], line_code=key[1], model_code=key[2]))
    for label, rows, key_fields in [('demand', snapshot.inputs.business.demands, ('model_code','week')),
                                  ('confirmation', snapshot.inputs.business.confirmations, ('model_code','week','kind','batch_id'))]:
        seen = set()
        for row in rows:
            key = tuple(getattr(row, field) for field in key_fields)
            if key in seen:
                issues.append(RuleIssue(code=label+'_duplicate', reason='需求或确认批次识别组合重复', model_code=row.model_code, demand_week=row.week))
            seen.add(key)
            if row.model_code not in index.models:
                issues.append(RuleIssue(code='unknown_master', reason='需求或确认引用不存在的机种', model_code=row.model_code, demand_week=row.week))
            if label == 'demand' and not snapshot.time.window_start <= row.week <= snapshot.time.window_end:
                issues.append(RuleIssue(code='demand_outside_window', severity='warning', reason='窗口外完整需求保留在历史，本轮不重复占用产出', model_code=row.model_code, demand_week=row.week))
    for key in sorted(set(plan) | set(facts)):
        day, line, model = key
        if not snapshot.time.window_start <= day <= snapshot.time.window_end:
            continue  # Earlier input is represented only by the starting WIP.
        planned = plan[key]
        covered_zero = any(c.start_date <= day <= c.end_date and line in c.line_codes and model in c.model_codes and c.blanks_are_zero for c in snapshot.execution.coverage)
        known = day > today or valid_cutoff and day <= production_date(cutoff) and (key in facts or covered_zero)
        actual = facts.get(key, 0) if known else None
        if key in facts and (day > today or not valid_cutoff or day > production_date(cutoff)):
            issues.append(RuleIssue(code='execution_cutoff', reason='累计执行事实不在已确认截止时点内', date=day, line_code=line, model_code=model))
            actual = facts[key]  # The fact is retained, while the result is blocked.
        if not known:
            issues.append(RuleIssue(code='execution_unknown', reason='执行事实或覆盖内空白为零尚未确认，不能把未知当零', severity='incomplete', date=day, line_code=line, model_code=model))
        remaining = unexecuted[key] if known and day >= today else 0
        expired = unexecuted[key] if known and day < today else 0
        cells.append(ReconciledCell(date=day, line_code=line, model_code=model,
            quantity=(actual or 0) + remaining, planned=planned, actual=actual,
            remaining=remaining, expired=expired, known=known))
    return issues, cells


def _output_from_cells(snapshot, cells, index):
    issues = []
    combined = defaultdict(list)
    for cell in cells:
        if cell.quantity:
            combined[(cell.date, cell.model_code)].append(cell)
    sources = []

    def non_holiday(day):
        while index.holidays.get(day, False):
            day += timedelta(days=1)
        return day

    holiday_ordinals = sorted(day.toordinal() for day, holiday in index.holidays.items() if holiday)

    def kth_day(day, n):
        if not n:
            return non_holiday(day)
        origin = day.toordinal()
        target = origin + n
        while True:
            if target > date.max.toordinal():
                raise OverflowError
            skipped = bisect_right(holiday_ordinals, target) - bisect_right(holiday_ordinals, origin)
            corrected = origin + n + skipped
            if corrected == target:
                return date.fromordinal(target)
            target = corrected

    parameters_by_month={}
    exact_parameters={}
    for (day, model), lines in sorted(combined.items()):
        month=day.strftime('%Y-%m')
        key=month,model
        if key not in parameters_by_month:
            parameters_by_month[key]=index.critical_parameters(month,model)
        parameters=parameters_by_month[key]
        if any(p['state'] == 'missing' for p in parameters.values()):
            for name, parameter in parameters.items():
                if parameter['state'] == 'missing' and not any(i.code == 'missing_'+name and i.date == day and i.model_code == model for i in issues):
                    issues.append(RuleIssue(code='missing_'+name, reason='投入来源缺少关键良率或 Cycle Time', severity='incomplete', date=day, model_code=model, line_code=lines[0].line_code))
            continue
        quantity = sum(row.quantity for row in lines)
        line_yield = parameters['line_yield']['percent']
        model_yield = parameters['model_yield']['percent']
        if key not in exact_parameters:
            ct=Fraction(parameters['cycle_time']['days'])
            n,f=ct.numerator//ct.denominator,ct%1
            exact_parameters[key]=(Fraction(line_yield)*Fraction(model_yield)/10000,ct,((n,1-f),(n+1,f)))
        factor,ct,splits=exact_parameters[key]
        gross=quantity*factor
        portions = []
        try:
            for offset, share in splits:
                if not share:
                    continue
                value = gross * share
                portions.append(OutputPortion(date=kth_day(day, offset), fraction=rational(share),
                                              before_rounding=rational(value), quantity=ceil(value)))
        except OverflowError:
            issues.append(RuleIssue(code='output_date_range', reason='CT产出日期超出可表示日历范围，尚未完成计算', severity='incomplete', date=day, model_code=model))
            continue
        sources.append(OutputSource(id=f'input:{model}:{day}', kind='input', model_code=model,
            input_date=day, quantity=quantity, lines=lines, yield_month=month,
            line_yield=line_yield, model_yield=model_yield, cycle_time=rational(ct), portions=portions))
    if snapshot.reset:
        reset = snapshot.reset
        if reset.production_week != snapshot.time.window_start or reset.cutoff != production_cutoff(reset.production_week):
            issues.append(RuleIssue(code='reset_context', reason='起始在制品归属周或截止时点与冻结窗口不一致'))
        seen = set()
        for row in reset.models:
            if row.model_code in seen:
                issues.append(RuleIssue(code='reset_duplicate', reason='在制品机种重复', model_code=row.model_code))
                continue
            seen.add(row.model_code)
            if not row.wip:
                continue
            month = reset.production_week.strftime('%Y-%m')
            line_yield = index.line_yields.get(month)
            model_yield = index.model_yields.get((month, row.model_code))
            if not line_yield or not model_yield:
                issues.append(RuleIssue(code='wip_yield_missing', reason='起始在制品缺少重置月份双良率', severity='incomplete', model_code=row.model_code))
                continue
            value = row.wip * Fraction(line_yield.percent) * Fraction(model_yield.percent) / 10000
            sources.append(OutputSource(id=f'wip:{row.model_code}:{reset.batch_id}', kind='wip',
                model_code=row.model_code, input_date=reset.production_week, quantity=row.wip,
                yield_month=month, line_yield=line_yield.percent, model_yield=model_yield.percent, cycle_time=None,
                portions=[OutputPortion(date=non_holiday(reset.production_week), fraction='1', before_rounding=rational(value), quantity=ceil(value))]))
    sources.sort(key=lambda source: (min(p.date for p in source.portions), source.kind != 'wip', source.input_date, source.id))
    attributions = []
    excluded_demands = [row for row in snapshot.inputs.business.demands if not snapshot.time.window_start <= row.week <= snapshot.time.window_end]
    demands = {(row.model_code, row.week): row.quantity for row in snapshot.inputs.business.demands
               if snapshot.time.window_start <= row.week <= snapshot.time.window_end}
    needs = dict(demands)
    demand_weeks = defaultdict(list)
    for model, week in demands:
        demand_weeks[model].append(week)
    for weeks in demand_weeks.values():
        weeks.sort()
    next_week = defaultdict(int)
    events = sorted([(portion.date, source.input_date, source.id, i, source, portion)
                     for source in sources for i, portion in enumerate(source.portions)], key=lambda e: (e[0], e[4].kind != 'wip', e[1], e[2], e[3]))
    for day, input_day, source_id, part_index, source, portion in events:
        available = portion.quantity
        model = source.model_code
        weeks = demand_weeks[model]
        cursor = next_week[model]
        while available and cursor < len(weeks):
            week = weeks[cursor]
            if not needs[(model, week)]:
                cursor += 1
                continue
            take = min(available, needs[(model, week)])
            on_time = take if day < week + timedelta(days=7) else 0
            attributions.append(OutputAttribution(source_id=source.id, model_code=model,
                demand_week=week, input_date=source.input_date, date=day, quantity=take,
                material_satisfied=take, on_time=on_time, late=take-on_time))
            available -= take
            needs[(model, week)] -= take
        next_week[model] = cursor
        source.portions[part_index] = portion.model_copy(update={'unallocated': available})
    allocated_by_source = Counter()
    for row in attributions:
        allocated_by_source[row.source_id] += row.quantity
    # Match each processed portion without merging identical dates.
    for position, source in enumerate(sources):
        total = sum(p.quantity for p in source.portions)
        used = allocated_by_source[source.id]
        sources[position] = source.model_copy(update={'allocated_output': used, 'unallocated_output': total-used})
    groups = defaultdict(list)
    for i, row in enumerate(attributions):
        groups[(row.source_id, row.demand_week)].append(i)
    by_source = {source.id: source for source in sources}
    apportioned = {}
    ideal = {}
    denominators = {}
    source_week_output = defaultdict(dict)
    for (source_id, week), indices in groups.items():
        source_week_output[source_id][week] = sum(attributions[i].quantity for i in indices)
    for source in sources:
        weekly_output = source_week_output[source.id]
        denominator = sum(weekly_output.values())
        if not denominator:
            continue
        shares = {week: Fraction(source.quantity * quantity, denominator) if source.kind == 'input' else Fraction(0)
                  for week, quantity in weekly_output.items()}
        floors = {week: share.numerator // share.denominator for week, share in shares.items()}
        left = source.quantity - sum(floors.values()) if source.kind == 'input' else 0
        for week in sorted(shares, key=lambda week: (-(shares[week] % 1), week))[:left]:
            floors[week] += 1
        for week in weekly_output:
            apportioned[(source.id, week)] = floors[week]
            ideal[(source.id, week)] = rational(shares[week])
            denominators[(source.id, week)] = denominator
    batches = snapshot.inputs.business.confirmations
    batch_keys = [(b.model_code, b.week, b.kind, b.batch_id) for b in batches]
    used = {key: 0 for key in batch_keys}
    bans = {(b.model_code, b.week, b.kind) for b in batches if b.status == 'forbidden'}
    allowed_batches = defaultdict(list)
    related_batches = defaultdict(set)
    for batch in batches:
        related_batches[(batch.model_code, batch.week)].add(batch.batch_id)
        if batch.status == 'allowed':
            allowed_batches[(batch.model_code, batch.week, batch.kind)].append(batch)
    for queue in allowed_batches.values():
        queue.sort(key=lambda batch: (batch.date, batch.batch_id))

    def consume(model, week, kind, day, quantity):
        matched = []
        remaining = quantity
        for batch in allowed_batches[(model, week, kind)]:
            if not (batch.date <= day if kind == 'material' else batch.date >= day):
                continue
            key = (model, week, kind, batch.batch_id)
            take = min(remaining, max(batch.quantity-used[key], 0))
            if take:
                used[key] += take
                matched.append(BatchUse(batch_id=batch.batch_id, quantity=take))
                remaining -= take
        return quantity-remaining, matched

    for (source_id, week), indices in sorted(groups.items(), key=lambda pair: (by_source[pair[0][0]].input_date, pair[0][1], pair[0][0])):
        source = by_source[source_id]
        week_output = sum(attributions[i].quantity for i in indices)
        input_share = apportioned[(source_id, week)]
        required = source.kind == 'input' and source.input_date < week
        forbidden = required and (source.model_code, week, 'material') in bans
        if forbidden:
            allowed, matches, satisfied = 0, [], 0
        elif required and input_share:
            allowed, matches = consume(source.model_code, week, 'material', source.input_date, input_share)
            satisfied = week_output * allowed // input_share
        else:
            allowed, matches, satisfied = input_share, [], week_output
        for i in sorted(indices, key=lambda i: (attributions[i].date, i)):
            row = attributions[i]
            take = min(row.quantity, satisfied)
            satisfied -= take
            attributions[i] = row.model_copy(update={
                'source_week_output': week_output, 'apportionment_denominator': denominators[(source_id, week)],
                'apportioned_input': input_share, 'input_share_exact': ideal[(source_id, week)],
                'material_required': required, 'material_input_used': allowed,
                'material_input_pending': input_share-allowed if required else 0,
                'material_satisfied': take, 'pending_material': row.quantity-take,
                'forbidden_material': forbidden, 'material_batches': matches, 'on_time': 0, 'late': 0})
    # Shipping uses only material-satisfied portions; pending material never reserves quota.
    for i in sorted(range(len(attributions)), key=lambda i: (attributions[i].date, by_source[attributions[i].source_id].kind != 'wip', by_source[attributions[i].source_id].input_date, attributions[i].source_id, i)):
        row = attributions[i]
        late = row.date >= row.demand_week + timedelta(days=7)
        forbidden = late and (row.model_code, row.demand_week, 'shipping') in bans
        if not late:
            valid, matches = row.material_satisfied, []
        elif forbidden:
            valid, matches = 0, []
        else:
            valid, matches = consume(row.model_code, row.demand_week, 'shipping', row.date, row.material_satisfied)
        pending_shipping = row.material_satisfied-valid if late else 0
        attributions[i] = row.model_copy(update={'forbidden_shipping': forbidden, 'pending_shipping': pending_shipping,
            'on_time': 0 if late else valid, 'late': valid if late else 0, 'shipping_batches': matches})
        if row.pending_material or pending_shipping:
            issues.append(RuleIssue(code='confirmation_forbidden' if row.forbidden_material or forbidden else 'confirmation_pending',
                reason='跨周安排存在整周禁令' if row.forbidden_material or forbidden else '跨周安排有必要确认未满足',
                date=row.date, input_date=row.input_date, model_code=row.model_code, demand_week=row.demand_week, source_id=row.source_id,
                line_codes=[cell.line_code for cell in by_source[row.source_id].lines],
                related_batch_ids=sorted(related_batches[(row.model_code, row.demand_week)])))
    usages = [ConfirmationUsage(model_code=b.model_code, week=b.week, kind=b.kind,
                batch_id=b.batch_id, status=b.status, date=b.date, quota=b.quantity,
                used=used[(b.model_code, b.week, b.kind, b.batch_id)],
                remaining=max(b.quantity-used[(b.model_code, b.week, b.kind, b.batch_id)], 0) if b.status == 'allowed' else 0,
                banned=(b.model_code, b.week, b.kind) in bans) for b in batches]
    weekly = []
    weekly_totals = defaultdict(Counter)
    for row in attributions:
        totals = weekly_totals[(row.model_code, row.demand_week)]
        totals['attributed'] += row.quantity
        totals['on_time'] += row.on_time
        totals['late'] += row.late
        totals['pending_material'] += row.pending_material
        totals['pending_shipping'] += row.pending_shipping
    incomplete_models = {issue.model_code for issue in issues if issue.severity == 'incomplete'}
    for (model, week), demand in sorted(demands.items(), key=lambda item: (item[0][1], item[0][0])):
        totals = weekly_totals[(model, week)]
        on_time, late = totals['on_time'], totals['late']
        week_complete = None not in incomplete_models and model not in incomplete_models
        weekly.append(WeeklyResult(computation_complete=week_complete, model_code=model, week=week, demand=demand,
            attributed=totals['attributed'], on_time=on_time, late=late,
            pending_material=totals['pending_material'], pending_shipping=totals['pending_shipping'],
            pending=totals['pending_material']+totals['pending_shipping'], gap=demand-on_time-late,
            on_time_rate=rational(Fraction(on_time, demand)) if demand and week_complete else None,
            on_time_rate_display=rate_display(on_time, demand) if week_complete else '计算未完成'))
    demand_total = sum(demands.values())
    complete = not any(i.severity == 'incomplete' for i in issues)
    total = OutputTotals(computation_complete=complete, expected=sum(p.quantity for s in sources for p in s.portions),
        outside_window=sum(p.quantity for s in sources for p in s.portions if p.date > snapshot.time.window_end),
        unallocated=sum(s.unallocated_output for s in sources), on_time=sum(w.on_time for w in weekly),
        late=sum(w.late for w in weekly), pending=sum(w.pending for w in weekly), gap=sum(w.gap for w in weekly),
        on_time_rate=rational(Fraction(sum(w.on_time for w in weekly), demand_total)) if demand_total and complete else None,
        on_time_rate_display=rate_display(sum(w.on_time for w in weekly), demand_total) if complete else '计算未完成')
    return {'issues': issues, 'reconciled_cells': cells, 'output_sources': sources,
            'excluded_demands': excluded_demands, 'attributions': attributions, 'weekly_results': weekly, 'confirmation_usage': usages, 'output_totals': total}


def output_evaluation(snapshot, index=None, *, output_cache=None):
    index = index if index is not None else InputIndex(snapshot.inputs)
    issues, cells = _reconcile(snapshot, index)
    output = (output_cache.evaluate(snapshot, index, cells, _output_from_cells)
              if output_cache is not None and not issues
              else _output_from_cells(snapshot, cells, index))
    output['issues'] = issues + output['issues']
    return output
