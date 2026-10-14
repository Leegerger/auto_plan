"""Archive recorded runs and independently recheck each retained candidate."""
from pathlib import Path
import gzip
import hashlib
import json
import statistics
import sys

from decomposition_experiment import validate
from surplus_audit import trim

source=Path(sys.argv[1]);root=Path(__file__).parent
groups={'initial':'decomposition-runs','quantity_first':'decomposition-runs-v2',
    'switches_first_full':'decomposition-repeats-full','switches_first_local':'decomposition-repeats-local',
    'cancellation':'decomposition-cancel'}
bundle={};summaries=[];checks=[]
for version,directory in groups.items():
    files={}
    for path in sorted((source/directory).iterdir()):
        if path.suffix not in ('.json','.log'):continue
        files[path.name]=json.loads(path.read_text()) if path.suffix=='.json' else path.read_text()
    bundle[version]=files
    for name,value in files.items():
        if isinstance(value,dict) and 'history' in value:
            saved=None
            for h in value['history']:
                if h['accepted']:
                    assert saved is None or h['vector']<saved,(version,name,h['stage'])
                    saved=h['vector']
            assert saved==value['vector']
            summaries.append({'version':version,'name':name,
                **{key:value[key] for key in ('variant','local','seconds','first_candidate_saved_seconds','vector','reference_vector','reference_quality_met','peak_rss_kib','input_sha256')},
                'within_compute_target':value['seconds']<=value['budget_seconds'],
                'first_reference_quality_checked_seconds':next((h['seconds'] for h in value['history'] if h['accepted'] and h['vector']<=value['reference_vector']),None)})
        if isinstance(value,dict) and 'candidate' in value and 'input' in value:
            cells={tuple(c) for c in value['mutable_cells']}
            result=validate(value['input'],value['candidate'],cells)
            assert result==value['vector'] and not any(result[:30]),(version,name)
            active={i for i,r in enumerate(value['candidate']['plan']) if (r['date'],r['line']) in cells}
            cleaned,audit=trim(value['candidate'],value['candidate'],active)
            unchanged=cleaned==[r['quantity'] for r in value['candidate']['plan']]
            checks.append({'version':version,'name':name,'vector_verified':True,
                'no_positive_segment_surplus_found':unchanged,
                'zero_segment_review_required':audit['zero_segment_review_required']})
            assert unchanged,(version,name)
aggregate=[]
for version in ('switches_first_full','switches_first_local'):
    rows=[r for r in summaries if r['version']==version]
    aggregate.append({'version':version,'runs':len(rows),'quality_passes':sum(r['reference_quality_met'] for r in rows),
        'within_compute_target':sum(r['within_compute_target'] for r in rows),
        'median_seconds':statistics.median(r['seconds'] for r in rows),
        'min_seconds':min(r['seconds'] for r in rows),'max_seconds':max(r['seconds'] for r in rows),
        'min_first_quality_seconds':min(r['first_reference_quality_checked_seconds'] for r in rows),
        'max_first_quality_seconds':max(r['first_reference_quality_checked_seconds'] for r in rows),
        'min_change_cost':min(r['vector'][-2] for r in rows),'max_change_cost':max(r['vector'][-2] for r in rows),
        'peak_rss_kib':max(r['peak_rss_kib'] for r in rows)})
payload=gzip.compress(json.dumps(bundle,separators=(',',':')).encode(),mtime=0)
(root/'decomposition-results.json.gz').write_bytes(payload)
summary={'runs':summaries,'repeated_groups':aggregate,'candidate_rechecks':checks,
    'archive_sha256':hashlib.sha256(payload).hexdigest()}
(root/'decomposition-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps({'repeated_groups':aggregate,'retained_candidates_rechecked':len(checks),'zero_segment_review_count':sum(len(x['zero_segment_review_required']) for x in checks)},indent=2))
