"""Versioned rolling witnesses with declared public preparation provenance.

Asset generation uses only the standard library. V1 is read-only; live operation
bindings and source history belong to separate preparation evidence.
"""
import argparse
from copy import deepcopy
from datetime import date, timedelta
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
from uuid import UUID, uuid4, uuid5

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

ACTOR = 'ACCEPTANCE0'
REASON = 'Synthetic rolling-v2 coverage and cutoff correction; no planned quantity is an execution fact.'
V1_MANIFEST_SHA256 = '986bffaad672f68add9b40ad76a15972b8354d5a9d0d9cf66593de0980ae94db'


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def load(root, pointer):
    raw = gzip.decompress((root / pointer['path']).read_bytes())
    if hashlib.sha256(raw).hexdigest() != pointer['sha256']:
        raise ValueError('Frozen predecessor asset hash mismatch')
    return json.loads(raw)


def preparation_operations(source_adoption, source):
    """One atomic public draft command with explicit synthetic observations."""
    start = date.fromisoformat(source['time']['window_start'])
    scope = {'start_date': start.isoformat(), 'end_date': (start + timedelta(days=104)).isoformat(),
             'line_codes': [row['code'] for row in source['inputs']['masters']['lines']],
             'model_codes': [row['code'] for row in source['inputs']['masters']['models']]}
    action = source['execution']['audit'][0]['action_id'].removesuffix('-execution')
    return [
        {'kind': 'record_execution', 'action_id': action+'-execution', 'rows': [],
         'coverage': deepcopy(source['execution']['coverage']), 'cutoff': source['execution']['cutoff'], 'reason': REASON},
        {'kind': 'weekly_reset', 'action_id': action+'-reset', 'source_adoption': source_adoption,
         'snapshot': deepcopy(source['reset']), 'scope': scope, 'choices': []},
        {'kind': 'patch_plan', 'action_id': action+'-observation',
         'initial_states': deepcopy(source['plan']['initial_states'])},
    ]


def write_assets(v1, output):
    """Freeze all three independent rolling cases and declared audit history."""
    v1, output = Path(v1), Path(output)
    assert v1.resolve()!=output.resolve() and v1.resolve() not in output.resolve().parents
    manifest_raw = (v1 / 'manifest.json').read_bytes()
    assert hashlib.sha256(manifest_raw).hexdigest()==V1_MANIFEST_SHA256, 'Unreviewed predecessor manifest'
    original = json.loads(manifest_raw)
    output.mkdir(parents=True, exist_ok=False)

    def freeze(value):
        raw = canonical(value)
        sha = hashlib.sha256(raw).hexdigest()
        path = sha + '.json.gz'
        (output / path).write_bytes(gzip.compress(raw, mtime=0))
        return {'path': path, 'sha256': sha, 'canonical_bytes': len(raw)}

    manifest = {key: deepcopy(value) for key, value in original.items() if key != 'scenarios'}
    manifest.update(fixture_version=2, family='rolling', prepared_at='2026-10-16', scenarios=[],
                    predecessor_manifest_sha256=V1_MANIFEST_SHA256)
    for count in (20,10,40):
        predecessor=next(row for row in original['scenarios'] if row['id']==f'r{count}-rolling')
        prior=next(row for row in original['scenarios'] if row['id']==f'r{count}-loose')
        ident=f'r{count}-rolling-v2'
        prior_source=load(v1,prior['source'])
        source,reference=(load(v1,predecessor[key]) for key in ('source','reference'))
        models=[row['code'] for row in source['inputs']['masters']['models']]
        start=source['time']['window_start'];cutoff=source['execution']['cutoff']
        reset={'batch_id':'synthetic-'+ident+'-zero-wip','production_week':start,'cutoff':cutoff,
               'models':[{'model_code':model,'wip':0,'net_balance':0} for model in models]}
        audit={'action_id':ident+'-execution','actor':ACTOR,'recorded_at':source['time']['as_of'],'reason':REASON,
               'changes':[{'family':'coverage','before':deepcopy(prior_source['execution']['coverage']),
                           'after':deepcopy(source['execution']['coverage'])},
                          {'family':'cutoff','before':prior_source['execution']['cutoff'],'after':cutoff}]}
        for snapshot in (source,reference):
            snapshot['inputs']['business']['demands'].sort(key=lambda row:(row['model_code'],row['week']))
            snapshot['execution']['audit']=[deepcopy(audit)]
            snapshot['reset']=deepcopy(reset)
        demand_file={'attribution_date':start,'rows':[{'model_code':model,'week':'2027-01-25','delta':700} for model in models]}
        preparation={'actor':ACTOR,'initial_clock':prior_source['time']['as_of'],'rolling_clock':source['time']['as_of'],
            'clock_transition':'Restart the controlled disposable test adapter against the same namespace, then log in again.',
            'execution_action_id':ident+'-execution','reset_action_id':ident+'-reset','observation_action_id':ident+'-observation',
            'execution_audit':audit,'reset_snapshot':reset,
            'entering_recipe_observation':{'kind':'explicit synthetic boundary observation','as_of':source['time']['as_of'],
                                          'initial_states':deepcopy(source['plan']['initial_states'])},
            'history':{'prior_scenario':prior['id'],'prior_source_sha256':prior['source']['sha256'],
                'initial_demand_attribution':'2026-10-12','initial_demand_rows':600,'new_demand_attribution':start,
                'new_demand_rows':40,'expired_planned_segments':280,'expired_planned_quantity':28000,
                'execution_facts':[],'adoption_after_rolling':'preserve original prior-week adoption'},
            'prepared_source':{'segments':3920,'switches':len(source['plan']['switches']),'expected':392000,'on_time':392000,'gap':28000}}
        scenario=deepcopy(predecessor)
        scenario.update({'id':ident,'source':freeze(source),'reference':freeze(reference),'action':freeze(load(v1,predecessor['action'])),
            'import':freeze(load(v1,prior['import'])),'prior_source':freeze(prior_source),'demand_file':freeze(demand_file),
            'preparation':freeze(preparation),'predecessor':{'scenario':predecessor['id'],'manifest_sha256':V1_MANIFEST_SHA256,
                **{name+'_sha256':predecessor[name]['sha256'] for name in ('source','reference','action','import')}}})
        scenario['notes'] += [
            'V2 declares the genuine public coverage/cutoff audit before preparation; V1 remains immutable.',
            'Materialized demands use public model/week ordering; both immutable demand files retain their original row order.',
            'Known entering Recipes are explicit synthetic observations, never inferred from expired planned work.',
            'The import asset is the original prior-week first import, not a fresh substitute for the rolled source.']
        manifest['scenarios'].append(scenario)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


from scripts.acceptance.local_v2 import OutcomeUnknown, request_json


def prepare_public(call,fixtures,config,output,*,phase):
    """Prepare through authenticated public calls, across an explicit restart.

    Initial phase stops after verifying the prior-week adoption and history.
    Rolling phase requires the same target at its declared new clock. Neither
    phase changes a clock, writes SQL or infers execution from planned work.
    """
    from scripts.acceptance.assertions import assert_frozen_snapshot
    from scripts.acceptance.compare import numeric
    from auto_plan.plan_models import Plan,ExecutionContext
    fixtures,output=Path(fixtures),Path(output)
    raw=(fixtures/'manifest.json').read_bytes();manifest=json.loads(raw)
    assert manifest.get('family')=='rolling' and manifest.get('fixture_version')==2
    assert manifest['predecessor_manifest_sha256']==V1_MANIFEST_SHA256
    scenario=next(row for row in manifest['scenarios'] if row['id']==config['scenario'])
    source,prior,imported,demand,preparation=(load(fixtures,scenario[name]) for name in ('source','prior_source','import','demand_file','preparation'))
    target=config['target']
    assert target['disposable'] is True and target['production'] is False and target['kind'] in ('adapter','PLAN_TEST')
    if target['kind']=='PLAN_TEST': assert re.fullmatch(r'AP_TEST_[A-F0-9]{6}',target['namespace'])
    identity={'scenario':scenario['id'],'target':target,'manifest_sha256':hashlib.sha256(raw).hexdigest(),
              'source_sha256':scenario['source']['sha256'],'predecessor':scenario['predecessor']}

    def retain(name,value): (output/name).write_text(json.dumps(value,indent=2)+'\n')
    if phase=='initial':
        output.mkdir(parents=True,exist_ok=False)
        report=identity|{'kind':'public API preparation; not timed acceptance','public_api_only':True,'status':'in_progress','operations':[]}
        retain('initial-config.json',config)
    elif phase=='rolling':
        report=json.loads((output/'report.json').read_text())
        assert all(report[key]==value for key,value in identity.items()), 'Preparation target or frozen identity changed'
        assert report['status']=='awaiting_clock_transition', 'Reconcile existing operation evidence before any restart'
        assert json.loads((output/'initial-config.json').read_text())==config
    else: raise ValueError('Unknown preparation phase')

    def persist(): retain('report.json',report)
    def stage(name): report['phase']=name;persist()
    def commit(path,verification,payload,kind):
        key=str(uuid4());command=payload|{'operation_id':key}
        operation={'kind':kind,'operation_id':key,'request':command,
                   'request_sha256':hashlib.sha256(canonical(command)).hexdigest(),'status':'prepared','unknown_outcome_reconciled':False}
        report['operations'].append(operation);persist()
        try: result=call(path,command)
        except OutcomeUnknown:
            operation['status']='unknown';persist()
            result=call(verification,{'operation_id':key})
            operation['unknown_outcome_reconciled']=True
        operation['result']=result;persist()
        assert result['status']=='COMMITTED', 'Preserve and reconcile the original UUID before a new write'
        operation['verification']=call(verification,{'operation_id':key})
        assert operation['verification']==result
        operation['status']='COMMITTED';persist()
        return result
    def state():
        head=call('/plans/current');context=head['context'];baseline=None
        if head['latest_adoption']:
            adopted=call('/plans/adoptions/'+str(head['latest_adoption']))
            baseline={'version':head['latest_adoption'],'plan':adopted['snapshot']['plan'],'coverage':adopted['coverage']}
        return {'head':head,'snapshot':{'time':head['time'],'inputs':call('/inputs?version='+str(head['input_version'])),
            'plan':head['plan'],'execution':context['execution'],'reset':context['reset'],'locks':context['locks'],
            'baseline':baseline,'preferences':context['preferences']}}
    def tokens(head):
        return {'source_revision':head['revision'],'input_version':head['input_version'],'latest_adoption':head['latest_adoption']}
    def assert_file(record,rows,operation_id,version,attribution):
        file_id=uuid5(UUID(operation_id),'demand-file')
        assert record['file_id']==str(file_id) and record['version']==version and record['attribution_date']==attribution
        expected=[row|{'week_label':None,'source_row':i+2,'row_id':str(uuid5(file_id,str(i))),'ordinal':i} for i,row in enumerate(rows)]
        assert record['rows']==expected

    persist()
    try:
        assert call('/auth/me')['user_id']==preparation['actor'], 'Declared synthetic audit actor differs before any business write'
        if phase=='initial':
            stage('empty_prior_week_target');initial=state();retain('initial-state.json',initial)
            head=initial['head']
            assert head['revision']==head['latest_adoption']==0 and head['time']==prior['time']
            assert call('/demands/files')['files']==[]
            imported.update(base_revision=head['revision'],input_version=head['input_version'],latest_adoption=head['latest_adoption'])
            preview=call('/initialization/preview',imported);retain('initial-preview.json',preview)
            assert preview['valid'] and preview['evaluation']['executable']
            stage('initialize_prior_week');commit('/initialization/apply','/plans/verify',imported,'initialize_prior_week')
            head=call('/plans/current')
            command={'base_revision':head['revision'],'input_version':head['input_version'],'latest_adoption':head['latest_adoption'],
                     'operations':[{'kind':'replace_plan','plan':prior['plan']}]}
            preview=call('/plans/preview',command);retain('prior-adoption-preview.json',preview)
            assert preview['evaluation']['executable'] and not preview['adoption_blocked']
            stage('adopt_prior_week');adopted=commit('/plans/adopt','/plans/verify',command|{'pending':False},'adopt_prior_week')
            prepared=state();assert_frozen_snapshot(prepared['snapshot'],prior)
            original=call('/plans/adoptions/'+str(adopted['latest_adoption']));retain('prior-adoption.json',original)
            assert original['coverage']==prior['baseline']['coverage']
            files=call('/demands/files');retain('initial-demand-files.json',files)
            assert len(files['files'])==1
            rows=[{'model_code':row['model_code'],'week':row['week'],'delta':row['quantity']} for row in imported['demands']]
            assert_file(files['files'][0],rows,report['operations'][0]['operation_id'],1,prior['time']['window_start'])
            retain('prior-state.json',prepared)
            report.update(status='awaiting_clock_transition',phase='prior_week_complete',actual_tokens=tokens(prepared['head']),
                          clock_transition=preparation['clock_transition'])
            persist();return report

        stage('verify_same_target_after_clock_transition');before=state();retain('rolling-before.json',before)
        head=before['head'];original=json.loads((output/'prior-adoption.json').read_text())
        initial_files=json.loads((output/'initial-demand-files.json').read_text())
        assert tokens(head)==report['actual_tokens'] and head['time']==source['time']
        assert numeric(head['plan'])==numeric(Plan.model_validate(prior['plan']).model_dump(mode='json'))
        assert call('/plans/adoptions/'+str(head['latest_adoption']))==original
        assert call('/demands/files')==initial_files
        demand['input_version']=head['input_version']
        preview=call('/demands/preview',demand);retain('new-week-demand-preview.json',preview)
        assert preview['valid'] and preview['demands']==source['inputs']['business']['demands']
        stage('append_new_week_demands');commit('/demands/apply','/demands/verify',demand,'append_new_week_demands')
        head=call('/plans/current')
        command={'base_revision':head['revision'],'input_version':head['input_version'],'latest_adoption':head['latest_adoption'],
                 'operations':preparation_operations(head['latest_adoption'],source)}
        preview=call('/plans/preview',command);retain('rolling-preview.json',preview)
        assert all(preview['evaluation'][name] for name in ('capacity_valid','computation_complete','executable')) and not preview['adoption_blocked']
        assert_frozen_snapshot(preview['snapshot'],source)
        stage('save_rolling_source');saved=commit('/plans/save','/plans/verify',command|{'pending':False},'save_rolling_source')
        record=call('/plans/revisions/'+str(saved['revision']));retain('rolling-revision.json',record)
        assert_frozen_snapshot(record['snapshot'],source)
        actual=state();retain('state.json',actual)
        assert_frozen_snapshot(actual['snapshot'],source)
        assert actual['head']['latest_adoption']==before['head']['latest_adoption']
        assert actual['head']['context']['execution']==ExecutionContext.model_validate(source['execution']).model_dump(mode='json')
        assert actual['head']['context']['reset']==source['reset']
        assert call('/plans/adoptions/'+str(head['latest_adoption']))==original
        files=call('/demands/files');retain('demand-files.json',files)
        assert len(files['files'])==2 and files['files'][0]==initial_files['files'][0]
        # File versions are per attribution date. This is the first October19
        # file, while the original October12 version1 remains independently live.
        assert_file(files['files'][1],demand['rows'],report['operations'][2]['operation_id'],1,demand['attribution_date'])
        assert all(row['effective'] for row in files['files'])
        reset=actual['head']['lineage']['resets'][source['time']['window_start']]
        assert reset['source_adoption']==head['latest_adoption'] and reset['snapshot']==source['reset']
        expected_history=[row|{'destination':'由本周最终净余额与在制品接续，不复制投入'} for row in prior['plan']['segments'] if row['date']<source['time']['window_start']]
        assert numeric(reset['historical_inputs'])==numeric(expected_history)
        assert len(expected_history)==280 and sum(row['quantity'] for row in expected_history)==28000
        prepared=deepcopy(config);prepared['fixtures']=str(fixtures.resolve());prepared['bindings']=tokens(actual['head'])
        retain('run-config.json',prepared);retain('head.json',actual['head'])
        report.update(status='pass',phase='complete',source_assertion='pass',actual_tokens=prepared['bindings'])
        persist();return report
    except Exception as error:
        report.update(status='fail',failure_type=type(error).__name__);persist()
        raise


def prepare_external(config,fixtures,output,credentials,phase):
    import httpx
    from urllib.parse import urlsplit
    origin=urlsplit(config['origin'])
    assert origin.scheme=='https' and origin.netloc and not origin.username and not origin.password and not origin.path and not origin.query and not origin.fragment
    assert config['base_path'].startswith('/') and not config['base_path'].endswith('/')
    prefix=config['origin']+config['base_path']+'/api'
    verify=config.get('tls_ca_file') or not config.get('allow_test_certificate',False)
    with httpx.Client(verify=verify,timeout=240,headers={'Origin':config['origin'],'X-Auto-Plan-Request':'1'}) as client:
        account=credentials['accounts'][0]
        response=client.post(prefix+'/auth/login',json={'username':account['username'],'password':account['password']})
        assert response.status_code==200,'Public login failed'
        account['password']=''
        return prepare_public(lambda path,payload=None:request_json(client,prefix,path,payload),fixtures,config,output,phase=phase)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    freeze=commands.add_parser('freeze')
    freeze.add_argument('--v1',type=Path,default=ROOT/'docs/acceptance/frozen')
    freeze.add_argument('--output',required=True,type=Path)
    prepare=commands.add_parser('prepare')
    prepare.add_argument('--phase',choices=('initial','rolling'),required=True)
    prepare.add_argument('--config',type=Path,required=True)
    prepare.add_argument('--fixtures',type=Path,required=True)
    prepare.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    try:
        if args.command=='freeze': write_assets(args.v1,args.output)
        else: prepare_external(json.loads(args.config.read_text()),args.fixtures,args.output,json.load(sys.stdin),args.phase)
    except Exception as error:
        print(type(error).__name__,file=sys.stderr)
        raise SystemExit(1) from None
