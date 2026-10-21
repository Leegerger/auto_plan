"""Selected immutable reads preserve authoritative bytes and full history."""
import hashlib
import json
import zlib
import pytest


def test_planning_read_owns_selected_fields_and_full_read_keeps_history():
    from auto_plan.oracle_plans import decode, decode_planning, document, encode
    record = {
        'snapshot': {
            'plan': {'segments': [{'id': 'S', 'quantity': 19}]},
            'time': {'window_start': '2026-10-12'},
            'inputs': {'input_version': 7, 'business': {'demands': [{'quantity': 19}]}},
        },
        'coverage': [{'blanks_are_zero': True}],
        'lineage': {'source_adoption': 3},
        'unallocated': [{'id': 'remaining', 'quantity': 4}],
        'operation_issues': [{'id': 'pending', 'severity': 'warning'}],
        'kind': 'adopt', 'pending': False,
        'evaluation': {'executable': True, 'arbitrary_historical_field': ['kept']},
        'future_full_record_field': {'untouched': [1, 2, 3]},
    }
    blob = encode(record)
    sha = hashlib.sha256(document(record)).hexdigest()
    selected = decode_planning(blob, sha)
    assert selected['snapshot'] == {key: record['snapshot'][key] for key in ('plan', 'time')}
    assert selected['lineage'] == record['lineage']
    assert selected['coverage'] == record['coverage']
    assert selected['unallocated'] == record['unallocated']
    assert selected['operation_issues'] == record['operation_issues']
    assert 'evaluation' not in selected and 'future_full_record_field' not in selected
    selected['snapshot']['plan']['segments'][0]['quantity'] = 999
    assert decode_planning(blob, sha)['snapshot']['plan']['segments'][0]['quantity'] == 19
    assert decode(blob, sha) == record


@pytest.mark.parametrize('value', [1.2345678901234567, -0.0, float('inf'), float('nan'), 10**2000])
def test_planning_read_preserves_legacy_numeric_serialization(value):
    from auto_plan.oracle_plans import decode, decode_planning, encode
    blob = encode({'snapshot': {'plan': {'value': value}}, 'evaluation': {'legacy': True}})
    expected = decode(blob)['snapshot']['plan']
    actual = decode_planning(blob)['snapshot']['plan']
    assert json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)


@pytest.mark.parametrize('raw', [b'{"snapshot":', b'{"snapshot":{"plan":{"value":"\\ud800"}}}', b'{"context":{"locks":[]}}'])
def test_planning_read_keeps_legacy_parser_fallback_and_errors(raw):
    from auto_plan.access import StoreUnavailable
    from auto_plan.oracle_plans import decode, decode_planning
    blob = zlib.compress(raw)
    try:
        expected = decode(blob)
    except StoreUnavailable:
        with pytest.raises(StoreUnavailable):
            decode_planning(blob)
    else:
        assert decode_planning(blob) == expected


def test_planning_read_verifies_ignored_historical_content_and_lob_bytes():
    from io import BytesIO
    from auto_plan.access import StoreUnavailable
    from auto_plan.oracle_plans import decode_planning, document, encode
    record = {'snapshot': {'plan': {}}, 'evaluation': {'legacy': 1}}
    sha = hashlib.sha256(document(record)).hexdigest()
    assert decode_planning(BytesIO(encode(record)), sha)['snapshot']['plan'] == {}
    changed = record | {'evaluation': {'legacy': 2}}
    with pytest.raises(StoreUnavailable):
        decode_planning(encode(changed), sha)
    with pytest.raises(StoreUnavailable):
        decode_planning(b'invalid compressed blob', sha)
