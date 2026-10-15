"""Disposable PLAN_TEST AP_* business tables, never production configuration.

Access/grants/sessions keep their canonical tables; only the explicit business
identifier allowlist is rewritten. Every object created belongs to one random
validated AP_TEST_* namespace. Canonical immutable history is never reset.
"""
from contextlib import contextmanager
from pathlib import Path
import re
import oracledb
from auto_plan.migrate import statements

BUSINESS_IDENTIFIERS=(
    'AP_DEMAND_FILE','AP_DEMAND_KEY','AP_DEMAND_FILE_IMMUTABLE',
    'AP_INPUT_HEAD','AP_INPUT_REVISION','AP_INPUT_REVISION_IMMUTABLE',
    'AP_PLAN_HEAD','AP_PLAN_CONTEXT','AP_PLAN_SNAPSHOT','AP_PLAN_REVISION',
    'AP_PLAN_EFFECT','AP_PLAN_ADOPTION','AP_ADOPTED_CELL','AP_PLAN_KEY','AP_PLAN_AUDIT',
    'AP_PLAN_CONTEXT_IMMUTABLE','AP_PLAN_SNAPSHOT_IMMUTABLE','AP_PLAN_REVISION_IMMUTABLE',
    'AP_PLAN_EFFECT_IMMUTABLE','AP_PLAN_ADOPTION_IMMUTABLE','AP_ADOPTED_CELL_IMMUTABLE','AP_PLAN_AUDIT_IMMUTABLE')
TABLES=[name for name in BUSINESS_IDENTIFIERS if not name.endswith('_IMMUTABLE')]


class NamespacePool:
    def __init__(self,pool,prefix):
        if not re.fullmatch(r'AP_TEST_[A-F0-9]{6}',prefix): raise ValueError('Only short random AP_TEST_* prefixes are allowed')
        self.pool=pool; self.prefix=prefix
        self.names={name:prefix+'_'+name.removeprefix('AP_') for name in BUSINESS_IDENTIFIERS}
        self.pattern=re.compile(r'\b('+'|'.join(sorted(BUSINESS_IDENTIFIERS,key=len,reverse=True))+r')\b',re.I)
    def rewrite(self,statement): return self.pattern.sub(lambda m:self.names[m[0].upper()],statement)
    @contextmanager
    def acquire(self):
        with self.pool.acquire() as connection: yield NamespaceConnection(connection,self)


class NamespaceConnection:
    def __init__(self,connection,namespace): self.connection=connection; self.namespace=namespace
    def __getattr__(self,name): return getattr(self.connection,name)
    @property
    def call_timeout(self): return self.connection.call_timeout
    @call_timeout.setter
    def call_timeout(self,value): self.connection.call_timeout=value
    @contextmanager
    def cursor(self):
        with self.connection.cursor() as cursor: yield NamespaceCursor(cursor,self.namespace)


class NamespaceCursor:
    def __init__(self,cursor,namespace): self.cursor=cursor; self.namespace=namespace
    def __getattr__(self,name): return getattr(self.cursor,name)
    def __iter__(self): return iter(self.cursor)
    def execute(self,statement,*args,**kwargs): return self.cursor.execute(self.namespace.rewrite(statement),*args,**kwargs)
    def executemany(self,statement,*args,**kwargs): return self.cursor.executemany(self.namespace.rewrite(statement),*args,**kwargs)


@contextmanager
def disposable_business_namespace(pool,prefix):
    namespace=NamespacePool(pool,prefix)
    root=Path(__file__).resolve().parents[1]
    with pool.acquire() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT SYS_CONTEXT('USERENV','SESSION_USER') FROM DUAL")
            if cursor.fetchone()[0].upper()!='PLAN_TEST': raise ValueError('Only PLAN_TEST may create disposable AP_* test objects')
    try:
        with namespace.acquire() as connection:
            with connection.cursor() as cursor:
                for filename in ('002_inputs.sql','003_plans.sql','004_demands.sql'):
                    for statement in statements((root/'migrations'/filename).read_text()): cursor.execute(statement)
            connection.commit()
        yield namespace
    finally:
        with pool.acquire() as connection:
            with connection.cursor() as cursor:
                for name in reversed(TABLES):
                    try: cursor.execute('DROP TABLE '+namespace.names[name]+' CASCADE CONSTRAINTS PURGE')
                    except oracledb.DatabaseError as error:
                        if error.args[0].code!=942: raise
            connection.commit()
