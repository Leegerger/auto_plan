"""Explicit operator-run Oracle migrations. Startup never changes the schema."""
import argparse
from datetime import datetime, timezone
import re
from uuid import uuid4
from pathlib import Path
import oracledb
from .oracle_access import OracleAccessStore
from .settings import Settings


def statements(document: str):
    pending = []
    plsql = False
    for line in document.splitlines():
        if not line.strip() or line.lstrip().startswith('--'):
            continue
        if line.upper().startswith('CREATE OR REPLACE TRIGGER'):
            plsql = True
        if plsql and line.strip() == '/':
            yield '\n'.join(pending)
            pending = []
            plsql = False
        else:
            pending.append(line)
            if not plsql and line.rstrip().endswith(';'):
                yield '\n'.join(pending).rstrip().removesuffix(';')
                pending = []
    if pending:
        raise ValueError('Migration has an unterminated statement')



def _identity(connection, expected_user):
    with connection.cursor() as cursor:
        cursor.execute("SELECT SYS_CONTEXT('USERENV','SESSION_USER') FROM DUAL")
        if cursor.fetchone()[0].upper()!=expected_user.upper():
            raise ValueError('Connected schema differs from expected account')


def _event(cursor, action, through, metadata):
    cursor.execute("INSERT INTO AP_SCHEMA_EVENT (EVENT_ID,ACTION,THROUGH_VERSION,RECOVERY_POINT,OPERATOR_ID,RELEASE_ID,SOURCE_REVISION,RECORDED_AT) VALUES (:id,:action,:version,:recovery_point,:operator_id,:release_id,:source_revision,:now)",
        id=str(uuid4()),action=action,version=through,now=datetime.now(timezone.utc),**metadata)


def operate(pool, migration, expected_user, *, metadata, bootstrap=False, baseline_through=None):
    """Explicit operator action. A failed DDL sequence is never automatically retried."""
    from .schema import manifest, MIGRATIONS
    if any(not isinstance(value,str) or not value.strip() or len(value)>256 for value in metadata.values()):
        raise ValueError('Operator/release/source/recovery-point records are required')
    with pool.acquire() as identity_connection:
        _identity(identity_connection,expected_user)
    if baseline_through is not None:
        # Maintenance mode prevents concurrent writers during this inspection.
        # Release each connection before taking another, even with pool max=1.
        if baseline_through>=2:
            from .oracle_inputs import OracleInputStore
            OracleInputStore(pool).check_consistency()
        if baseline_through>=3:
            from .oracle_plans import OraclePlanStore
            plans=OraclePlanStore(pool);plans.check_consistency()
        if baseline_through>=4:
            from .oracle_demands import OracleDemandStore
            OracleDemandStore(plans).check_consistency()
        if baseline_through>=5:
            from .oracle_jobs import OracleJobStore
            OracleJobStore(pool).check_consistency()
    with pool.acquire() as connection:
        connection.call_timeout=15000
        _identity(connection,expected_user)
        with connection.cursor() as cursor:
            if bootstrap:
                for statement in statements((MIGRATIONS/'000_schema_ledger.sql').read_text()): cursor.execute(statement)
                _event(cursor,'bootstrap',None,metadata);connection.commit();return
            cursor.execute('SELECT VERSION_NO,FILENAME,SHA256,STATUS FROM AP_SCHEMA_MIGRATION ORDER BY VERSION_NO')
            recorded={int(row[0]):row for row in cursor}
            available=manifest()
            if baseline_through is not None:
                if recorded or baseline_through not in {v for v,_,_ in available}:
                    raise ValueError('Baseline requires an empty ledger and known complete prefix')
                # Baseline is an explicit inspected installation, never inferred from one table.
                for version,filename,sha in available:
                    if version>baseline_through: break
                    document=(MIGRATIONS/filename).read_text()
                    for kind,name in re.findall(r'CREATE (?:OR REPLACE )?(TABLE|TRIGGER|INDEX) (AP_[A-Z0-9_]+)',document):
                        cursor.execute('SELECT STATUS FROM USER_OBJECTS WHERE OBJECT_NAME=:name AND OBJECT_TYPE=:kind',name=name,kind=kind)
                        row=cursor.fetchone()
                        if row is None or row[0]!='VALID': raise ValueError('Baseline object absent or invalid')
                        if kind=='TRIGGER':
                            cursor.execute('SELECT STATUS FROM USER_TRIGGERS WHERE TRIGGER_NAME=:name',name=name)
                            enabled=cursor.fetchone()
                            if enabled is None or enabled[0]!='ENABLED': raise ValueError('Baseline immutable trigger disabled')
                for version,filename,sha in available:
                    if version>baseline_through: break
                    _insert(cursor,version,filename,sha,'BASELINED',metadata)
                _event(cursor,'inspected_baseline',baseline_through,metadata);connection.commit();return
            if migration is None: raise ValueError('A migration file is required')
            selected=[entry for entry in available if entry[1]==migration.name and (MIGRATIONS/entry[1]).read_bytes()==migration.read_bytes()]
            if len(selected)!=1: raise ValueError('Only a release migration with the exact recorded checksum may run')
            version,filename,sha=selected[0]
            if version in recorded:
                row=recorded[version]
                if tuple(row[1:3])==(filename,sha) and row[3] in {'APPLIED','BASELINED'}: return
                raise ValueError('Partial migration or checksum mismatch requires operator recovery')
            if set(recorded)!=set(range(1,version)) or any(row[3] not in {'APPLIED','BASELINED'} for row in recorded.values()):
                raise ValueError('Migrations must apply in order after successful predecessors')
            for earlier,oldname,oldsha in available:
                if earlier<version and tuple(recorded[earlier][1:3])!=(oldname,oldsha): raise ValueError('Predecessor checksum mismatch')
            _insert(cursor,version,filename,sha,'APPLYING',metadata);connection.commit()
            completed=0
            try:
                for statement in statements(migration.read_text()):
                    cursor.execute(statement);completed+=1
                    cursor.execute('UPDATE AP_SCHEMA_MIGRATION SET COMPLETED_STATEMENTS=:count WHERE VERSION_NO=:version',count=completed,version=version)
                    connection.commit()
                cursor.execute("UPDATE AP_SCHEMA_MIGRATION SET STATUS='APPLIED',COMPLETED_AT=:now WHERE VERSION_NO=:version",now=datetime.now(timezone.utc),version=version)
                _event(cursor,'apply',version,metadata);connection.commit()
            except Exception as error:
                # DDL commits. Preserve the completed statement count and sanitized code.
                code=getattr(error.args[0],'full_code','operator_error') if error.args else 'operator_error'
                cursor.execute("UPDATE AP_SCHEMA_MIGRATION SET STATUS='FAILED',ERROR_CODE=:code,COMPLETED_STATEMENTS=:count WHERE VERSION_NO=:version",code=code[:32],count=completed,version=version)
                connection.commit();raise


def _insert(cursor,version,filename,sha,status,metadata):
    cursor.execute('INSERT INTO AP_SCHEMA_MIGRATION (VERSION_NO,FILENAME,SHA256,STATUS,RECOVERY_POINT,OPERATOR_ID,RELEASE_ID,SOURCE_REVISION,STARTED_AT) VALUES (:version,:filename,:sha,:status,:recovery_point,:operator_id,:release_id,:source_revision,:now)',
        version=version,filename=filename,sha=sha,status=status,now=datetime.now(timezone.utc),**metadata)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('migration',type=Path,nargs='?')
    parser.add_argument('--expected-user',required=True)
    action=parser.add_mutually_exclusive_group()
    action.add_argument('--bootstrap-ledger',action='store_true')
    action.add_argument('--baseline-through',type=int)
    parser.add_argument('--recovery-point',required=True)
    parser.add_argument('--operator',required=True)
    parser.add_argument('--release',required=True)
    parser.add_argument('--source-revision',required=True)
    args=parser.parse_args()
    store=None
    try:
        settings=Settings()
        if settings.oracle_user.upper()!=args.expected_user.upper(): raise ValueError('Configured account differs from expected account')
        store=OracleAccessStore.connect(settings)
        operate(store.pool,args.migration,args.expected_user,metadata=dict(recovery_point=args.recovery_point,operator_id=args.operator,release_id=args.release,source_revision=args.source_revision),bootstrap=args.bootstrap_ledger,baseline_through=args.baseline_through)
    except Exception:
        raise SystemExit('Migration refused or failed. Inspect the sanitized ledger and operator recovery record; no automatic retry.') from None
    finally:
        if store: store.close()
    print('Operator migration action completed')


if __name__=='__main__': main()
