"""Derived cache admission must validate original types before serializers."""
from copy import deepcopy
import pytest

from auto_plan.output_reuse import OutputReuse, _part, _thaw
from auto_plan.output_rules import _output_from_cells
from auto_plan.plan_models import BatchUse, EvaluationSnapshot
from auto_plan.rules import evaluate
from tests.test_output_reuse import reuse_source


def part():
    result=evaluate(EvaluationSnapshot.model_validate(reuse_source()))
    return {name:deepcopy(getattr(result,name)) for name in ('output_sources','attributions','weekly_results')}


@pytest.mark.parametrize('field',['source','allocated','portion','line','attribution','batch','weekly'])
def test_original_boolean_quantities_are_rejected_before_json_coercion(field):
    value=part()
    if field in ('source','allocated'):
        name='quantity' if field=='source' else 'allocated_output'
        value['output_sources'][0]=value['output_sources'][0].model_copy(update={name:True})
    elif field in ('portion','line'):
        children=getattr(value['output_sources'][0],'portions' if field=='portion' else 'lines')
        children[0]=children[0].model_copy(update={'quantity':False})
    elif field=='attribution':
        value['attributions'][0]=value['attributions'][0].model_copy(update={'quantity':True})
    elif field=='batch':
        value['attributions'][0].material_batches.append(BatchUse(batch_id='B',quantity=1).model_copy(update={'quantity':False}))
    else:
        value['weekly_results'][0]=value['weekly_results'][0].model_copy(update={'gap':True})
    cache=OutputReuse()
    assert cache._put((_output_from_cells,b'B'*32),value) is None
    assert cache.stats()['entries']==cache.stats()['bytes']==cache.stats()['nodes']==0


def test_original_values_are_fully_normalized_and_source_is_never_mutated():
    value=part()
    value['output_sources'][0]=value['output_sources'][0].model_copy(update={'model_code':' M ','quantity':'00100'})
    original=deepcopy(value)
    cache=OutputReuse()
    frozen=cache._put((_output_from_cells,b'N'*32),value)
    assert frozen is not None
    checked=_thaw(frozen)
    assert checked['output_sources'][0]['model_code']=='M'
    assert checked['output_sources'][0]['quantity']==100
    assert type(checked['output_sources'][0]['quantity']) is int
    assert value==original
    assert _part.validate_python(checked)['output_sources'][0].quantity==100


def test_normalization_keeps_unicode_dates_booleans_and_large_integer_values():
    from datetime import date
    value=part()
    number=10**1000+123
    identity='产出-🙂-\\'
    value['output_sources'][0]=value['output_sources'][0].model_copy(update={
        'id':identity,'input_date':date(2026,10,15),'quantity':number})
    frozen=OutputReuse()._put((_output_from_cells,b'U'*32),value)
    assert frozen is not None
    checked=_thaw(frozen)
    source=checked['output_sources'][0]
    assert source['id']==identity and source['input_date']=='2026-10-15'
    assert type(source['quantity']) is int and source['quantity']==number
    assert type(checked['weekly_results'][0]['computation_complete']) is bool


@pytest.mark.parametrize('collection,field,value', [
    ('output_sources','quantity',1.5),('output_sources','input_date','2026-99-99'),
    ('weekly_results','model_code',''),('attributions','material_batches',[{'batch_id':'B','quantity':-1}]),
])
def test_other_invalid_original_nested_fields_still_refuse_admission(collection,field,value):
    original=part()
    original[collection][0]=original[collection][0].model_copy(update={field:value})
    cache=OutputReuse()
    assert cache._put((_output_from_cells,b'I'*32),original) is None
    assert cache.stats()['entries']==cache.stats()['bytes']==cache.stats()['nodes']==0


def test_json_number_limit_and_oversized_admission_still_fall_back_completely():
    value=part()
    value['output_sources'][0]=value['output_sources'][0].model_copy(update={'quantity':10**5000})
    cache=OutputReuse()
    assert cache._put((_output_from_cells,b'L'*32),value) is None
    assert cache.stats()['entries']==0
    small=OutputReuse(max_entry_bytes=1)
    assert small._put((_output_from_cells,b'S'*32),part()) is None
    assert small.stats()['entries']==0
