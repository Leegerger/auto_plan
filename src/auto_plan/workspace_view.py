"""Complete matrix reads from the same frozen snapshot as its evaluation."""
from collections import defaultdict
from datetime import timedelta
from fractions import Fraction
from .exact import hours
from .input_lookup import InputIndex
from .execution_rules import confirmed_day_capacity
from .production_time import production_date, production_cutoff
from .recipe_states import analyze_recipe_states


def _hours(value):
    return hours(value).model_dump(mode='json')


def _job_details(record):
    return record.unfinished_switch.model_dump(mode='json') | {'record_id': record.id,
        'effective_at':record.effective_at.isoformat(),'source':record.source,'actor':record.actor,'recorded_at':record.recorded_at.isoformat()}


def workspace_view(snapshot, evaluation):
    index = InputIndex(snapshot.inputs)
    plan = snapshot.plan
    lines = sorted({row.code for row in snapshot.inputs.masters.lines if row.active}
                   | {row.line_code for row in plan.segments}
                   | {row.line_code for row in plan.switches}
                   | {row.line_code for row in plan.initial_states})
    sparse = {(row['date'], row['line_code']): row for row in evaluation['days']}
    locks = defaultdict(list)
    for lock in snapshot.locks:
        locks[lock.date, lock.line_code].append({'model_code': lock.model_code, 'quantity': lock.quantity})
    issues = defaultdict(list)
    for issue in evaluation['issues']:
        if issue.get('date') and issue.get('line_code'):
            issues[issue['date'], issue['line_code']].append(issue['code'])
    zero = _hours(0)
    days = []
    weeks = []
    from .line_states import calculation_anchors, active_state_reviews, active_unfinished_jobs, unfinished_job_history
    unfinished = active_unfinished_jobs(snapshot)
    history = unfinished_job_history(snapshot)
    uncertain={review.line_code for review in active_state_reviews(snapshot)}
    analysis = analyze_recipe_states(plan, index, include_transitions=True, confirmed_states=calculation_anchors(snapshot),window_start=snapshot.time.window_start,as_of=snapshot.time.as_of)
    today = production_date(snapshot.time.as_of)
    for line in lines:
        confirmation = confirmed_day_capacity(snapshot, line)
        observations = [row for row in snapshot.execution.day_capacities if row.line_code == line and row.date == today]
        observed = max(observations, key=lambda row: row.confirmed_at) if observations else None
        current = {'observed_at': observed.confirmed_at.isoformat(), 'recipe_code': observed.current_recipe,
                   'valid': observed == confirmation,'state':'unfinished_switch' if observed.residual_record_id else 'retained_recipe',
                   'unfinished_switch':None} if observed else None
        if observed is not None and observed.residual_record_id is not None and line in unfinished:
            record=unfinished[line]
            current=current|{'unfinished_switch':record.unfinished_switch.model_dump(mode='json'),'record_id':record.id,
                'effective_at':record.effective_at.isoformat(),'source':record.source,'actor':record.actor,'recorded_at':record.recorded_at.isoformat()}
        records=[record for record in snapshot.line_states.records if record.line_code==line and record.effective_at<=snapshot.time.as_of]
        latest=max(records,key=lambda record:(record.effective_at,record.recorded_at)) if records else None
        if latest is not None and (confirmation is None or latest.effective_at>=confirmation.confirmed_at):
            from .line_states import record_is_current
            current={'observed_at':latest.effective_at.isoformat(),'effective_at':latest.effective_at.isoformat(),
                     'recipe_code':latest.recipe_code,'source':latest.source,'actor':latest.actor,
                     'recorded_at':latest.recorded_at.isoformat(),'record_id':latest.id,'valid':record_is_current(snapshot,latest),
                     'state':latest.state,'unfinished_switch':latest.unfinished_switch.model_dump(mode='json') if latest.unfinished_switch else None}
        if current is not None and line in uncertain:
            current=current|{'valid':False}
        if current is not None and current.get('state')=='unfinished_switch' and line not in unfinished:
            current=current|{'valid':False}
        weeks.extend(_line_weeks(snapshot, index, evaluation, line, analysis, current, history))
        for offset in range(105):
            day = snapshot.time.window_start + timedelta(days=offset)
            row = sparse.get((day.isoformat(), line))
            calendar = index.day(day, line)
            available = confirmation.remaining_hours if confirmation and day == today else calendar['available_hours']
            occupied = row['occupied_hours'] if row else zero
            days.append({'date': day.isoformat(), 'line_code': line,
                         'calendar_hours': _hours(calendar['available_hours']), 'available_hours': _hours(available),
                         'remaining_hours': _hours(max(0, Fraction(available) - Fraction(occupied['exact']))),
                         'production_hours': row['production_hours'] if row else zero,
                         'switch_hours': row['switch_hours'] if row else zero, 'occupied_hours': occupied,
                         'holiday': calendar['output_holiday'], 'opening_exception': index.openings.get((day, line), False),
                         'maintenance_hours': index.maintenance.get((day, line), 0),
                         'experiment_hours': index.experiments.get((day, line), 0),
                         'basis': 'confirmed_remaining_day' if confirmation and day == today else 'calendar',
                         'locks': locks[day, line], 'issues': sorted(set(issues[day.isoformat(), line])),
                         'unfinished_switch': _job_details(unfinished[line]) if line in unfinished else None,
                         'residual_switches': [{'id':switch.id,'job_id':switch.id,'residual_record_id':switch.residual_record_id,
                             'from_recipe':switch.from_recipe,'to_recipe':switch.to_recipe,
                             'allocated_hours':_hours(sum((Fraction(a.hours) for a in switch.allocations if a.date==day),Fraction(0)))}
                             for switch in plan.switches if switch.line_code==line and switch.residual_record_id is not None and any(a.date==day for a in switch.allocations)]})
    months = sorted({row['date'][:7] for row in days})
    capacities = [{'month': month, 'line_code': line, 'recipe_code': recipe,
                   'state': capacity['state'], 'full_day_capacity': capacity['pieces']}
                  for month in months for line in lines
                  for recipe, master in sorted(index.recipes.items()) if master.active
                  for capacity in [index.capacity(month, recipe, line)]]
    from .switch_continuity import continuity_details
    return {'days': days, 'line_weeks': weeks, 'capacities': capacities,'switch_continuity':continuity_details(snapshot)}


def _line_weeks(snapshot, index, evaluation, line, analysis, current, history):
    events = analysis.transitions.get(line, [])
    switches = {row.id: row for row in snapshot.plan.switches}
    segments = {row.id: row for row in snapshot.plan.segments}
    state = analysis.initial_states.get(line)
    pending = analysis.initial_pending.get(line)
    jobs = {}
    known = {identity:record for identity,(record,_) in history.items() if record.line_code==line}
    for identity,record in known.items():
        jobs[identity] = {'start':production_date(record.unfinished_switch.started_at),'end':history[identity][1] or snapshot.time.window_end}
    for switch in switches.values():
        if switch.line_code != line:
            continue
        start = jobs.get(switch.id,{}).get('start',min(row.date for row in switch.allocations))
        record = known.get(switch.id)
        complete = record is None or sum((Fraction(row.hours) for row in switch.allocations if row.date>=production_date(snapshot.time.as_of)),Fraction(0)) == Fraction(record.unfinished_switch.remaining_hours)
        if record is None or history[switch.id][1] is None:
            jobs[switch.id] = {'start':start,'end':max(row.date for row in switch.allocations) if complete else snapshot.time.window_end}
    evaluated = {row['id']:row for row in evaluation['switches']}
    uncertain = False
    invalid_switches = {issue['switch_id'] for issue in evaluation['issues']
                        if issue.get('switch_id') and issue['severity'] == 'error'}
    position = 0
    rows = []
    for number in range(15):
        week = snapshot.time.window_start + timedelta(days=number * 7)
        end = week + timedelta(days=7)
        while position < len(events) and events[position].date < week:
            event = events[position]
            uncertain |= event.kind == 'switch' and event.identity in invalid_switches
            state, pending = (None if uncertain else event.ending_recipe), event.pending_switch
            position += 1
        first = next((event for event in events[position:] if event.date < end and event.kind == 'production'
                      and (segments[event.identity].quantity or Fraction(segments[event.identity].hours))), None)
        link = None
        actual = known.get(pending)
        if first:
            segment = segments[first.identity]
            recipe = index.models[segment.model_code].recipe_code
            before = [event for event in events[position:] if (event.date, event.order) < (first.date, first.order)]
            origin_job=actual
            origin=actual.unfinished_switch.from_recipe if actual is not None else state
            completed=[]
            for event in before:
                if event.kind=='confirmation':
                    origin_job=known.get(event.pending_switch)
                    origin=origin_job.unfinished_switch.from_recipe if origin_job is not None else event.ending_recipe
                    completed=[]
                elif event.kind=='switch' and event.pending_switch is None and event.identity not in completed:
                    completed.append(event.identity)
            duration = index.switch(origin, recipe)
            duration_state = duration['state']
            required = Fraction(duration['hours'])
            if origin_job is not None:
                required=Fraction(origin_job.unfinished_switch.remaining_hours)
                duration_state='known'
            if completed:
                durations = [index.switch(switches[identity].from_recipe, switches[identity].to_recipe) for identity in completed]
                required = sum((Fraction(evaluated[identity]['total_hours']['exact']) if identity in evaluated else Fraction(value['hours']) for identity,value in zip(completed,durations)), Fraction(0))
                duration_state = 'default_missing' if any(value['state'] == 'default_missing' for value in durations) else 'known'
            relevant = sorted({issue['code'] for issue in evaluation['issues'] if issue.get('segment_id') == first.identity
                               or issue.get('switch_id') in completed})
            ready = first.entering_recipe == recipe and first.pending_switch is None
            link = {'segment_id': segment.id, 'date': segment.date.isoformat(), 'order': segment.order,
                    'recipe_code': recipe, 'from_recipe': origin, 'duration_state': duration_state,
                    'required_hours': _hours(required), 'planned_switch_ids': completed,
                    'arranged': origin is not None and ready and not any(code in relevant for code in
                        ('recipe_sequence', 'unfinished_switch', 'switch_total', 'switch_sequence', 'duplicate_order')),
                    'issues': relevant}
        pending_switch = switches.get(pending)
        pending_actual = actual is not None and pending == actual.unfinished_switch.job_id
        inferred_start = pending_actual and actual.effective_at>production_cutoff(week) and actual.unfinished_switch.started_at<=production_cutoff(week)
        rows.append({'week': week.isoformat(), 'line_code': line, 'recipe_code': state,
                     'state': 'unfinished_switch' if pending_actual else 'planned_switch' if pending_switch else 'retained_recipe' if state else 'unknown',
                     'source': ('confirmed_job_start' if inferred_start else 'confirmed_state' if any(record.line_code==line and record.effective_at<=production_cutoff(week) for record in snapshot.line_states.records) else 'window_plan_start') if number == 0 else 'plan_projection',
                     'planned_switch': {'id': pending_switch.id, 'from_recipe': pending_switch.from_recipe,
                                        'to_recipe': pending_switch.to_recipe} if pending_switch else None,
                     'current_confirmation': current, 'first_production': link,
                     'unfinished_switch':_job_details(actual)|{'remaining_at_week_start_known':actual.effective_at<=production_cutoff(week)} if pending_actual else None,
                     'new_switch_count':sum(week<=job['start']<end for job in jobs.values()),
                     'carried_switch_count':sum(job['start']<week<=job['end'] for job in jobs.values()),
                     'switch_hours':_hours(sum((Fraction(row.hours) for switch in switches.values() if switch.line_code==line for row in switch.allocations if week<=row.date<end),Fraction(0)))})
    return rows
