"""Bounded scheduling neighborhoods with independent whole-plan acceptance.

Generation may use cumulative output projections. Publication always uses the
complete rules and the unchanged adopted baseline, with exact hour witnesses.
Native libraries are deliberately absent from this controller.
"""
from collections import defaultdict
from datetime import timedelta
from fractions import Fraction
from itertools import permutations
import hashlib
import json
import time
from .certificate import CertificateError
from .input_lookup import InputIndex
from .plan_operations import OperationContext
from .manual_operations import bounded_certificate, _authorize_change
from .output_rules import output_evaluation
from .plan_models import Plan, ProductionSegment, RuleIssue
from .plans import protect, PlanProblem, effect_keys
from .production_time import production_date
from .rules import evaluate
from .solver_models import Candidate, FrozenSolveInput, SolveResult, StageBound


def objective(evaluation):
    """All fifteen on-time gaps precede all fifteen final gaps."""
    ontime, final = [0] * 15, [0] * 15
    for row in evaluation.weekly_results:
        number = (row.week - evaluation.time.window_start).days // 7
        if 0 <= number < 15:
            ontime[number] += row.demand - row.on_time
            final[number] += row.gap
    return tuple(Fraction(value) for value in [*ontime, *final, evaluation.switch_count,
        evaluation.stability.weighted_cost, evaluation.stability.order_change_count])


def patch_operation(before, after):
    """A replayable delta retains independent edits made after computation."""
    result = {'kind': 'patch_plan'}
    for field, identity, removed in [('segments', lambda r: r.id, 'remove_segments'),
            ('switches', lambda r: r.id, 'remove_switches'),
            ('ordinary_idle', lambda r: (r.date, r.line_code), 'remove_idle')]:
        old, new = {identity(r): r for r in getattr(before, field)}, {identity(r): r for r in getattr(after, field)}
        result[field] = [new[key].model_dump(mode='json') for key in new if old.get(key) != new[key]]
        result[removed] = [(old[key].model_dump(mode='json') if field == 'ordinary_idle' else key) for key in old if key not in new]
    result['initial_states'] = [r.model_dump(mode='json') for r in after.initial_states if r not in before.initial_states]
    if hasattr(after, 'current_day_witnesses'):
        result['current_day_witnesses'] = [r.model_dump(mode='json') for r in after.current_day_witnesses if r not in before.current_day_witnesses]
    return result


def _certificate(plan, context, scope):
    # Execution owns current-day basis validation and the residual-hour witness.
    try:
        from .execution_rules import execution_certificate
    except ImportError:
        return bounded_certificate(plan, context, scope)
    return execution_certificate(plan, context, scope)


class _Finished(Exception):
    pass


class _Search:
    def __init__(self, frozen, progress, should_stop):
        self.frozen = frozen.model_copy(deep=True)
        self.snapshot, self.request = self.frozen.snapshot, self.frozen.request
        self.index = InputIndex(self.snapshot.inputs)
        self.scope = self.request.scope
        self.context = OperationContext(self.snapshot, self.frozen.source_revision, action_id='solver')
        self.start = time.monotonic()
        self.deadline = self.start + self.request.time_limit_seconds
        self.progress, self.should_stop = progress or (lambda value: None), should_stop or (lambda: False)
        self.stopped = False
        self.bounds, self.issues, self.history = [], [], []
        self.best, self.best_evaluation, self.best_score = None, None, None
        self.today = production_date(self.snapshot.time.as_of)
        self.locks = {(r.date, r.line_code, r.model_code): r.quantity for r in self.snapshot.locks}
        try:
            from .execution_rules import executed_by_segment
            self.executed = executed_by_segment(self.snapshot.plan, self.snapshot.execution)
        except ImportError:
            facts = {(r.date, r.line_code, r.model_code): r.quantity for r in self.snapshot.execution.facts}
            self.executed = {}
            for row in sorted(self.snapshot.plan.segments, key=lambda r: (r.date, r.line_code, r.order, r.id)):
                key = (row.date, row.line_code, row.model_code)
                self.executed[row.id] = min(row.quantity, facts.get(key, 0))
                facts[key] = max(0, facts.get(key, 0) - row.quantity)

    def check(self):
        if self.should_stop():
            self.stopped = True
            raise _Finished
        if time.monotonic() >= self.deadline:
            raise _Finished

    def stage(self, name, detail, status='heuristic', score=None, lower=None, neighborhood='authorized scope'):
        bound = StageBound(stage=name, neighborhood=neighborhood, status=status,
            objective=[str(v) for v in score] if score is not None else None,
            lower_bound=[str(v) for v in lower] if lower is not None else None, detail=detail)
        self.bounds.append(bound)
        self.progress({'kind': 'stage', **bound.model_dump(mode='json')})

    def mutable(self, row):
        return self.scope.contains(row) and row.date >= self.today and (row.date, row.line_code, row.model_code) not in self.locks

    def capacity_allowed(self, day, line):
        if day > self.today:
            return True
        if day < self.today:
            return False
        try:
            from .execution_rules import confirmed_day_capacity
        except ImportError:
            return False
        return confirmed_day_capacity(self.snapshot, line) is not None

    def witness(self, plan, *, fixed_switches=False):
        self.check()
        if fixed_switches:
            from .solver_witness import fixed_switch_certificate
            result = fixed_switch_certificate(plan, self.context, self.scope, self.request.mode == 'new_week')
        else:
            result = _certificate(plan, self.context, self.scope)
            if self.request.mode == 'new_week':
                from .solver_witness import preserve_existing
                result = preserve_existing(result, self.context, self.scope)
        self.authorize(result)
        return result

    def authorize(self, plan):
        protect(self.snapshot.plan, plan, self.snapshot)
        _authorize_change(self.snapshot.plan, plan, self.scope, self.context)
        old_rows = {r.id: r for r in self.snapshot.plan.segments}
        new_rows = {r.id: r for r in plan.segments}
        for identity, row in old_rows.items():
            if (not self.scope.contains(row) or self.request.mode == 'new_week') and new_rows.get(identity) != row:
                raise ValueError('范围外或新增周原有安排必须保持不变，请扩大授权范围')
        old_switches, new_switches = {s.id: s for s in self.snapshot.plan.switches}, {s.id: s for s in plan.switches}
        for identity in old_switches.keys() | new_switches.keys():
            before, after = old_switches.get(identity), new_switches.get(identity)
            if before == after:
                continue
            for switch in (before, after):
                if switch and (self.request.mode == 'new_week' and before is not None or switch.line_code not in self.scope.line_codes or any(not self.scope.start_date <= a.date <= self.scope.end_date for a in switch.allocations)):
                    raise ValueError('调整需要增加或修改范围外切机，请扩大范围')
        if plan.initial_states != self.snapshot.plan.initial_states:
            raise ValueError('计算不能改写冻结的线体起始 Recipe')

    def project(self, plan, model):
        """Exact per-model output ledger for generation, never publication."""
        business = self.snapshot.inputs.business.model_copy(update={
            'demands': [r for r in self.snapshot.inputs.business.demands if r.model_code == model],
            'confirmations': [r for r in self.snapshot.inputs.business.confirmations if r.model_code == model]})
        snapshot = self.snapshot.model_copy(update={
            'plan': plan.model_copy(update={'segments': [r for r in plan.segments if r.model_code == model]}),
            'inputs': self.snapshot.inputs.model_copy(update={'business': business}),
            'execution': self.snapshot.execution.model_copy(update={'facts': [r for r in self.snapshot.execution.facts if r.model_code == model]}),
            'baseline': None})
        return output_evaluation(snapshot, self.index)

    def output_score(self, projected):
        values = [0] * 30
        for row in projected['weekly_results']:
            number = (row.week - self.snapshot.time.window_start).days // 7
            if 0 <= number < 15:
                values[number] += row.demand - row.on_time
                values[15 + number] += row.gap
        return tuple(values)

    def accept(self, plan, stage, *, prepare=False):
        self.check()
        if not prepare and not self.eligible(plan):
            return False
        try:
            self.authorize(plan)
        except (ValueError, PlanProblem):
            return False
        result = evaluate(self.snapshot.model_copy(update={'plan': plan}))
        # Pending preplans are separate from executable recommendations. Bans,
        # incomplete calculations and capacity/protection errors never qualify.
        if not result.computation_complete or not result.capacity_valid or any(i.severity == 'error' and i.code != 'confirmation_pending' for i in result.issues):
            self.issues.extend(i for i in result.issues if i.severity != 'warning' and i not in self.issues)
            return False
        score = objective(result)
        if not prepare and self.best_score is not None and score >= self.best_score:
            return False
        self.best, self.best_evaluation, self.best_score = plan, result, score
        self.history.append((plan, result, score, stage, list(self.bounds)))
        candidate = self.candidate(self.history[-1])
        self.progress({'kind': 'candidate', 'candidate': candidate.model_dump(mode='json')})
        return True

    def candidate(self, entry):
        plan, result, score, stage, bounds = entry
        identity = hashlib.sha256(json.dumps(plan.model_dump(mode='json'), sort_keys=True).encode()).hexdigest()
        return Candidate(id=identity, source_revision=self.frozen.source_revision, input_version=self.frozen.input_version,
            latest_adoption=self.frozen.latest_adoption, snapshot=self.snapshot, request=self.request, plan=plan,
            operation=patch_operation(self.snapshot.plan, plan), effects=[list(r) for r in effect_keys(self.snapshot.plan, plan, self.snapshot)],
            objective=[str(v) for v in score], evaluation=result, stage_bounds=bounds or [StageBound(stage=stage,
                neighborhood='authorized scope', status='validated', detail='完整规则已校验，没有全局最优证明')],
            eligibility='executable' if result.executable else 'pending_confirmation', tradeoff=stage)

    def cleanup(self, plan):
        if self.request.mode == 'new_week':
            return plan
        working = plan.model_copy(deep=True)
        for model in sorted({r.model_code for r in working.segments}):
            self.check()
            projection = self.project(working, model)
            target = self.output_score(projection)
            for source in reversed(projection['output_sources']):
                if source.kind != 'input' or not source.unallocated_output and source.allocated_output:
                    continue
                rows = [r for r in working.segments if r.model_code == model and r.date == source.input_date and self.mutable(r)]
                if not rows:
                    issue = RuleIssue(code='protected_surplus', severity='warning', model_code=model, date=source.input_date,
                        reason='未覆盖需求的富余属于事实、锁定或范围外安排，保留原投入')
                    if issue not in self.issues:
                        self.issues.append(issue)
                for row in reversed(rows):
                    self.check()
                    floor = self.executed.get(row.id, 0)
                    low, high, chosen = floor, row.quantity, working
                    while low < high:
                        amount = (low + high) // 2
                        trial = working.model_copy(update={'segments': [r.model_copy(update={'quantity': amount}) if r.id == row.id else r for r in working.segments if r.id != row.id or amount]})
                        if self.output_score(self.project(trial, model)) == target:
                            high, chosen = amount, trial
                        else:
                            low = amount + 1
                    if low != row.quantity:
                        working = working.model_copy(update={'segments': [r.model_copy(update={'quantity': low}) if r.id == row.id else r for r in working.segments if r.id != row.id or low]})
        if working == plan:
            return plan
        try:
            return self.witness(working)
        except (ValueError, CertificateError, PlanProblem) as error:
            self.scope_issue(error)
            return plan

    def scope_issue(self, error):
        reason = str(error)
        if '范围' in reason:
            issue = RuleIssue(code='scope_expansion_required', reason=reason, severity='warning')
            if issue not in self.issues:
                self.issues.append(issue)

    def add(self, plan, model, day, line, amount):
        recipe = self.index.models[model].recipe_code
        rows = sorted([r for r in plan.segments if (r.date, r.line_code) == (day, line)], key=lambda r: (r.order, r.id))
        matching = [r for r in rows if r.model_code == model and self.mutable(r) and not self.executed.get(r.id)]
        if matching and self.request.mode != 'new_week':
            selected = matching[-1]
            return plan.model_copy(update={'segments': [r.model_copy(update={'quantity': r.quantity + amount}) if r.id == selected.id else r for r in plan.segments]})
        identity = 'solve-' + hashlib.sha256(f'{model}:{day}:{line}'.encode()).hexdigest()[:40]
        # Reuse an inserted row across cumulative demands.
        inserted = next((r for r in rows if r.id == identity), None)
        if inserted:
            return plan.model_copy(update={'segments': [r.model_copy(update={'quantity': r.quantity + amount}) if r.id == identity else r for r in plan.segments]})
        order = max([r.order for r in rows] + [-1]) + 2
        added = ProductionSegment(id=identity, date=day, line_code=line, order=order, model_code=model, quantity=amount, hours='0')
        return plan.model_copy(update={'segments': [*plan.segments, added]})

    def capacity_trial(self, plan, model, day, line, amount, fast):
        trial = self.add(plan, model, day, line, amount)
        if not fast or day <= self.today:
            return self.witness(trial)
        # Generation-only day bound. A whole exact certificate and complete
        # evaluator remain mandatory before any batched proposal is accepted.
        daily = sorted([r for r in trial.segments if r.date == day and r.line_code == line and r.quantity], key=lambda r: (r.order, r.id))
        earlier = [r for r in trial.segments if r.line_code == line and r.date < day and r.quantity]
        state = self.index.models[max(earlier, key=lambda r: (r.date, r.order, r.id)).model_code].recipe_code if earlier else next((r.recipe_code for r in plan.initial_states if r.line_code == line), None)
        switch_hours = Fraction(0)
        quantities, fixed_hours = defaultdict(int), defaultdict(Fraction)
        old = {r.id for r in self.snapshot.plan.segments}
        for row in daily:
            recipe = self.index.models[row.model_code].recipe_code
            switch_hours += Fraction(self.index.switch(state, recipe)['hours'])
            state = recipe
            quantities[recipe] += row.quantity
            if not self.scope.contains(row) or self.request.mode == 'new_week' and row.id in old:
                fixed_hours[recipe] += Fraction(row.hours)
        lower = Fraction(0)
        strict = False
        for recipe, quantity in quantities.items():
            capacity = self.index.capacity(day.strftime('%Y-%m'), recipe, line)
            if capacity['state'] != 'known':
                raise ValueError('正投入需要已知允许产能')
            bound = Fraction(24 * (quantity - 1), capacity['pieces'])
            lower += max(bound, fixed_hours[recipe])
            strict |= bound >= fixed_hours[recipe]
        spare = Fraction(self.index.day(day, line)['available_hours']) - switch_hours - lower
        if spare < 0 or strict and spare <= 0:
            raise ValueError('生成阶段日容量不足')
        return trial

    def construct(self, plan, *, on_time_only=False):
        """Allocate cumulative unmet output, using exact ledgers and capacity."""
        working = plan
        demands = sorted([r for r in self.snapshot.inputs.business.demands if r.quantity and self.snapshot.time.window_start <= r.week <= self.snapshot.time.window_end], key=lambda r: (r.week, r.model_code))
        model_scores = {model: self.output_score(self.project(working, model)) for model in {r.model_code for r in demands}}
        fast = len(demands) * (self.scope.end_date - self.scope.start_date).days * len(self.scope.line_codes) > 500
        current_week = None
        for demand in demands:
            self.check()
            if fast and current_week is not None and demand.week != current_week:
                try:
                    working = self.witness(working)
                    self.accept(working, 'cumulative_week:' + str(current_week))
                except (ValueError, CertificateError, PlanProblem) as error:
                    self.scope_issue(error)
            current_week = demand.week
            model = demand.model_code
            priority = (demand.week - self.snapshot.time.window_start).days // 7 + (0 if on_time_only else 15)
            if model not in self.index.models or not self.index.models[model].active:
                continue
            if self.scope.model_codes and model not in self.scope.model_codes:
                continue
            recipe = self.index.models[model].recipe_code
            if not self.index.recipes[recipe].active:
                continue
            slots = []
            day = max(self.scope.start_date, self.today)
            while day <= self.scope.end_date:
                if on_time_only and day >= demand.week + timedelta(days=7):
                    break
                if day < demand.week and not any(batch.model_code == model and batch.week == demand.week and batch.kind == 'material'
                        and batch.status == 'allowed' and batch.quantity and batch.date <= day for batch in self.snapshot.inputs.business.confirmations):
                    day += timedelta(days=1)
                    continue
                for line in self.scope.line_codes:
                    if line not in self.index.lines or not self.index.lines[line].active or not self.capacity_allowed(day, line) or (day, line, model) in self.locks:
                        continue
                    capacity = self.index.capacity(day.strftime('%Y-%m'), recipe, line)
                    if capacity['state'] != 'known' or not self.index.day(day, line)['available_hours']:
                        continue
                    parameters = self.index.critical_parameters(day.strftime('%Y-%m'), model)
                    if any(p['state'] == 'missing' for p in parameters.values()):
                        issue = RuleIssue(code='missing_solver_parameter', reason='需求机种缺少关键良率或 Cycle Time，不能把未知当作零产出', severity='incomplete', date=day, line_code=line, model_code=model)
                        if issue not in self.issues:
                            self.issues.append(issue)
                        continue
                    slots.append((day, line, capacity['pieces']))
                day += timedelta(days=1)
            for day, line, maximum in slots:
                self.check()
                previous = model_scores[model]
                if not previous[priority]:
                    break
                # Capacity bisection is independent of any demand estimate. A
                # valid partial witness remains useful when all demand cannot fit.
                low, high, witness = 0, maximum, None
                while low < high:
                    amount = (low + high + 1) // 2
                    try:
                        trial = self.capacity_trial(working, model, day, line, amount, fast)
                    except (ValueError, CertificateError, PlanProblem) as error:
                        self.scope_issue(error)
                        high = amount - 1
                    else:
                        low, witness = amount, trial
                if not low:
                    continue
                maximum_plan = witness if witness is not None else self.capacity_trial(working, model, day, line, low, fast)
                projected = self.project(maximum_plan, model)
                if any(i.severity == 'error' for i in projected['issues']):
                    # General bans and batch quotas can make the largest input
                    # invalid while a smaller partial arrangement is executable.
                    high = low
                    low, maximum_plan = 0, None
                    while low < high:
                        amount = (low + high + 1) // 2
                        trial = self.add(working, model, day, line, amount)
                        ledger = self.project(trial, model)
                        if any(i.severity == 'error' for i in ledger['issues']):
                            high = amount - 1
                        else:
                            low, maximum_plan = amount, trial
                    if not low:
                        continue
                    maximum_plan = self.capacity_trial(working, model, day, line, low, fast)
                    projected = self.project(maximum_plan, model)
                target = self.output_score(projected)
                if target[:priority + 1] >= previous[:priority + 1]:
                    continue
                if target >= previous:
                    continue
                # Keep the least input achieving this exact coverage, preserving
                # necessary CT/yield rounding tails rather than nominal ceilings.
                high, low_bound = low, 1
                while low_bound < high:
                    amount = (low_bound + high) // 2
                    trial = self.add(working, model, day, line, amount)
                    ledger = self.project(trial, model)
                    if self.output_score(ledger)[:priority + 1] <= target[:priority + 1] and not any(i.severity == 'error' for i in ledger['issues']):
                        high = amount
                    else:
                        low_bound = amount + 1
                working = self.capacity_trial(working, model, day, line, low_bound, fast)
                model_scores[model] = self.output_score(self.project(working, model))
        return self.witness(working) if fast else working

    def eligible(self, plan):
        """Reject removable input used solely to buy a lower switch count."""
        original = {r.id for r in self.snapshot.plan.segments}
        for model in {r.model_code for r in plan.segments}:
            projection = self.project(plan, model)
            score = self.output_score(projection)
            surplus_dates = {s.input_date for s in projection['output_sources']
                if s.kind == 'input' and (s.unallocated_output or not s.allocated_output)}
            for row in plan.segments:
                if row.model_code != model or row.date not in surplus_dates or not self.mutable(row) or row.quantity <= self.executed.get(row.id, 0) or self.request.mode == 'new_week' and row.id in original:
                    continue
                quantity = row.quantity - 1
                trial = plan.model_copy(update={'segments': [r.model_copy(update={'quantity': quantity}) if r.id == row.id else r for r in plan.segments if r.id != row.id or quantity]})
                if self.output_score(self.project(trial, model)) == score:
                    return False
        return True

    def run(self):
        months = set()
        day = self.scope.start_date
        while day <= self.scope.end_date:
            months.add(day.strftime('%Y-%m'))
            day += timedelta(days=1)
        for demand in self.snapshot.inputs.business.demands:
            if not demand.quantity or not self.snapshot.time.window_start <= demand.week <= self.snapshot.time.window_end:
                continue
            if self.scope.model_codes and demand.model_code not in self.scope.model_codes:
                continue
            for month in sorted(months):
                missing = [name for name, value in self.index.critical_parameters(month, demand.model_code).items() if value['state'] == 'missing']
                if missing:
                    self.issues.append(RuleIssue(code='missing_solver_parameter', severity='incomplete', model_code=demand.model_code,
                        demand_week=demand.week, reason=month + ' 缺少关键资料 ' + ', '.join(missing)))
        if any(i.severity == 'incomplete' for i in self.issues):
            self.stage('context_check', '关键资料不完整，不能把未知当零产出推荐', status='incomplete')
            return
        protected = defaultdict(int)
        for lock in self.snapshot.locks:
            if lock.date > self.today and self.snapshot.time.window_start <= lock.date <= self.snapshot.time.window_end and lock.model_code in self.index.models:
                protected[lock.date, lock.line_code, self.index.models[lock.model_code].recipe_code] += lock.quantity
        for (day, line, recipe), quantity in protected.items():
            capacity = self.index.capacity(day.strftime('%Y-%m'), recipe, line)
            if capacity['state'] == 'missing':
                continue
            hours = self.index.day(day, line)['available_hours']
            maximum = Fraction(capacity['pieces'] * hours, 24) if capacity['state'] == 'known' else Fraction(0)
            upper = -(-maximum.numerator // maximum.denominator)
            if quantity > upper:
                reason = f'{day} {line} {recipe} 锁定投入{quantity}超过全部日工时也只能投入{upper}的上限'
                self.issues.append(RuleIssue(code='protected_capacity_conflict', reason=reason, date=day, line_code=line, recipe_code=recipe))
                self.stage('protected_capacity', reason, status='proven_protected_conflict', neighborhood=f'{day}/{line}/{recipe} aggregate locks and available day hours')
        if any(i.code == 'protected_capacity_conflict' for i in self.issues):
            return
        plan = self.snapshot.plan
        prepared = self.cleanup(plan)
        self.stage('eligible_start', '范围内无需求富余经精确产出检查移除；最近采用版仍是原稳定性基线')
        self.accept(prepared, 'eligible_start', prepare=True)
        self.order(prepared, 'order_before_quantity')
        prepared = self.best or prepared
        built = self.construct(prepared, on_time_only=True)
        built = self.construct(built)
        self.stage('cumulative_constructor', '累计需求、实际月产能与完整确认投影构造，可保留有缺口的可执行方案')
        self.accept(built, 'cumulative_constructor')
        if self.request.mode != 'new_week':
            # Structural reconstruction is a separate authorized proposal. It
            # may move/delete free input while keeping locked/factual rows.
            skeleton = prepared.model_copy(update={'segments': [r.model_copy(update={'quantity': self.executed[r.id]})
                if self.mutable(r) and self.executed.get(r.id) else r for r in prepared.segments
                if not self.mutable(r) or self.executed.get(r.id)]})
            try:
                skeleton = self.witness(skeleton)
                rebuilt = self.construct(skeleton, on_time_only=True)
                rebuilt = self.construct(rebuilt)
                self.stage('structural_constructor', '自由日期、线体与零激活数量构造；保留锁定、事实和范围外事件')
                self.accept(rebuilt, 'structural_constructor')
            except (ValueError, CertificateError, PlanProblem) as error:
                self.scope_issue(error)
        self.order(self.best or built, 'order_after_quantity')
        self.local_neighborhood(self.best or built)
        recipes = sorted({self.index.models[r.model_code].recipe_code for r in self.snapshot.inputs.business.demands if r.model_code in self.index.models})
        for recipe in recipes:
            self.check()
            self.local_neighborhood(self.best or built, recipe=recipe)
        self.order(self.best or built, 'order_after_recipe_blocks')

    def local_neighborhood(self, plan, recipe=None):
        from .solver_neighborhood import enumerate_local
        from .solver_process import select
        stage = 'recipe_block:' + recipe if recipe is not None else 'local_' + self.request.strategy
        result, detail = enumerate_local(self, plan, recipe=recipe)
        neighborhood = ('Recipe quantities with all other quantities/order/global switches fixed' if recipe is not None else 'finite free date/line/integer-quantity/daily-order domain')
        if result is None or not result:
            self.stage(stage, detail, status='bounded_search_skipped', neighborhood=neighborhood)
            if recipe is not None and self.request.mode != 'new_week':
                self.quantity_repair(plan, recipe)
            return
        table, scores = result
        if not table:
            self.stage(stage, detail + '; no executable plan in this finite neighborhood', status='no_local_witness', neighborhood=neighborhood)
            return
        self.check()
        route = self.request.strategy if self.request.strategy != 'hybrid' else 'cp_sat'
        response = select(route, [[str(v) for v in score] for score in scores],
            min(1 if recipe else 5, max(0.1, self.deadline - time.monotonic())), self.request.native_threads, self.should_stop)
        self.check()
        chosen = response.get('selected')
        exact = min(range(len(scores)), key=lambda n: scores[n])
        # Numerical solver answers never outrank exact rational comparison.
        # Verification of this finite table is independent of its native model.
        proven = isinstance(chosen, int) and 0 <= chosen < len(scores) and scores[chosen] == scores[exact]
        if not proven:
            chosen = exact
        self.stage(stage, detail + '; native ' + route + ': ' + response.get('status', 'failed')
            + '; exact finite-table check; no global scheduling proof', status='optimal_in_finite_domain',
            score=scores[chosen], lower=scores[chosen], neighborhood=neighborhood)
        self.accept(table[chosen], stage)

    def quantity_repair(self, plan, recipe):
        from .solver_quantity import repair
        route = self.request.strategy if self.request.strategy != 'hybrid' else 'cp_sat'
        try:
            proposed, response = repair(self, plan, recipe, route,
                min(1, max(0.1, self.deadline - time.monotonic())))
            self.check()
            self.stage('native_recipe:' + recipe,
                'native ' + route + ': ' + response.get('status', 'failed') + '; cumulative-output relaxation; general quotas/FIFO/full33 independently checked',
                status='restricted_relaxation', neighborhood='other Recipe quantities and all global switches fixed')
            if proposed is not None:
                self.accept(proposed, 'native_recipe:' + recipe)
        except (ValueError, CertificateError, PlanProblem) as error:
            self.scope_issue(error)

    def order(self, plan, name):
        from .solver_order import order_proposal
        proposed, exhaustive = order_proposal(self, plan)
        self.stage(name, '固定数量的 Recipe 状态路径；容量及边界仍需完整精确工时检查',
            neighborhood='finite daily order graph' if exhaustive else 'daily order subset')
        if proposed == plan:
            return
        try:
            self.accept(self.witness(proposed), name)
        except (ValueError, CertificateError, PlanProblem) as error:
            self.scope_issue(error)

    def result(self):
        history = sorted(self.history, key=lambda entry: entry[2])
        selected = []
        for entry in history:
            candidate = self.candidate(entry)
            if any(candidate.plan == prior.plan or candidate.objective == prior.objective for prior in selected):
                continue
            score = entry[2]
            if any(all(Fraction(a) <= b for a, b in zip(prior.objective, score)) for prior in selected):
                continue
            selected.append(candidate)
            if len(selected) == self.request.max_candidates:
                break
        status = 'stopped' if self.stopped else 'time_limit' if time.monotonic() >= self.deadline else 'feasible' if selected else 'incomplete' if any(i.severity == 'incomplete' for i in self.issues) else 'scope_conflict' if any(i.code in ('scope_expansion_required', 'protected_capacity_conflict') for i in self.issues) else 'no_candidate'
        return SolveResult(status=status, candidates=selected, stage_bounds=self.bounds, issues=self.issues, elapsed_seconds=time.monotonic() - self.start)


def solve(frozen: FrozenSolveInput, progress=None, should_stop=None) -> SolveResult:
    search = _Search(frozen, progress, should_stop)
    try:
        search.run()
    except _Finished:
        pass
    return search.result()
