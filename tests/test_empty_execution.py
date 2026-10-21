"""Empty execution evidence keeps complete assignment without allocation work."""
from auto_plan.execution_rules import executed_by_segment
from auto_plan.plan_models import ExecutionContext, Plan, ProductionSegment


def test_empty_evidence_preserves_frozen_cells_order_and_fresh_ownership():
    plan = Plan(segments=[
        ProductionSegment(id='b', date='2026-10-15', line_code='L', model_code='M', order=1, quantity=5, hours='1'),
        ProductionSegment(id='a', date='2026-10-15', line_code='L', model_code='M', order=0, quantity=0, hours='0'),
    ])
    execution = ExecutionContext(association_cells=[
        {'date':'2026-10-15','line_code':'L','model_code':'M','quantity':100},
    ])
    before = plan.model_dump(mode='json'), execution.model_dump(mode='json')
    first = executed_by_segment(plan, execution)
    second = executed_by_segment(plan, execution)
    assert list(first.items()) == [('a', 0), ('b', 0)]
    assert first is not second
    first['a'] = 999
    first['unrelated'] = 1
    assert list(second.items()) == [('a', 0), ('b', 0)]
    assert (plan.model_dump(mode='json'), execution.model_dump(mode='json')) == before
    assert executed_by_segment(Plan(), execution) == {}


def test_nonempty_execution_keeps_cumulative_splits_and_explicit_association_semantics():
    plan = Plan(segments=[
        ProductionSegment(id='b', date='2026-10-15', line_code='L', model_code='M', order=1, quantity=4, hours='1'),
        ProductionSegment(id='a', date='2026-10-15', line_code='L', model_code='M', order=0, quantity=5, hours='1'),
    ])
    cell = {'date':'2026-10-15','line_code':'L','model_code':'M'}
    examples = [
        ({'facts':[cell | {'quantity':0}]}, [('a',0),('b',0)]),
        ({'facts':[cell | {'quantity':6}]}, [('a',5),('b',1)]),
        ({'facts':[cell | {'quantity':15}]}, [('a',5),('b',4)]),
        ({'facts':[cell | {'quantity':6}], 'associations':[{'segment_id':'b','quantity':3}]}, [('b',3),('a',3)]),
        ({'associations':[{'segment_id':'a','quantity':0}]}, [('a',0),('b',0)]),
        ({'associations':[{'segment_id':'missing','quantity':9}]}, [('a',0),('b',0)]),
        ({'facts':[cell | {'quantity':6}], 'associations':[{'segment_id':'a','quantity':2}], 'association_cells':[cell | {'quantity':6}]}, [('a',2),('b',0)]),
        ({'facts':[cell | {'quantity':6},cell | {'quantity':2}]}, [('a',2),('b',0)]),
    ]
    for values, expected in examples:
        execution = ExecutionContext.model_validate(values)
        before = execution.model_dump(mode='json')
        assert list(executed_by_segment(plan, execution).items()) == expected
        assert execution.model_dump(mode='json') == before
